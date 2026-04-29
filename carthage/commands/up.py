"""`carthage up` — bring the project container online, rebuilding if stale.

Includes a host-port collision precheck — multiple Carthage projects can run
simultaneously, but two can't claim the same host port. On conflict we fail
loudly and name the offending project (read from `carthage.managed` labels
on running containers); `--no-host-ports` strips all host bindings for this
run, and `--port HOST:CONTAINER` adds a one-off override.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import click
from rich.console import Console

from carthage import __version__, annex_template_is_outdated, compose, image, ports
from carthage.config import CarthageConfig, ConfigError, load_config

console = Console()


@click.command()
@click.option(
    "--force-rebuild",
    is_flag=True,
    help="Skip the staleness check and rebuild unconditionally.",
)
@click.option(
    "--no-host-ports",
    is_flag=True,
    help="Strip all host-side port bindings for this run.",
)
@click.option(
    "--port",
    "port_overrides",
    multiple=True,
    metavar="HOST:CONTAINER",
    help=(
        "One-off port binding to add (may be repeated). Example: --port 3000:3000. "
        "Explicit only — we never silently remap."
    ),
)
def up(force_rebuild: bool, no_host_ports: bool, port_overrides: tuple[str, ...]) -> None:
    """Start the project container (rebuild first if out of date)."""
    try:
        cfg = load_config()
    except ConfigError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(2)

    if cfg.schema_is_outdated:
        console.print(
            f"[yellow]note:[/yellow] config schema '{cfg.version}' is older than "
            "current. Still readable, but consider running `/carthage-migrate` "
            "from Claude Code in this project."
        )

    if annex_template_is_outdated(cfg.annexed_with_cli):
        console.print(
            f"[yellow]note:[/yellow] this project was annexed under CLI "
            f"{cfg.annexed_with_cli or 'pre-1.0'}; current CLI is {__version__}. "
            "Run /carthage-annex --upgrade from Claude Code to pick up template "
            "improvements (mounts, labels, etc.)."
        )

    # Validate port-override syntax early (fail before doing any work).
    overrides = _parse_overrides(port_overrides)

    # --- Refresh base image so digest drift is visible to the hash check ---
    # `compute_expected_hash` reads the base image's digest from the local
    # cache; without this pull, an upstream `:vN` rebuild looks like a no-op
    # to the staleness check and the user runs an old base indefinitely.
    ok, detail = image.pull_base_image(cfg.base_image)
    if not ok:
        console.print(
            f"[yellow]note:[/yellow] could not refresh {cfg.base_image} ({detail}); "
            "using local cache. Run `carthage build --pull` once you're online."
        )

    # --- Build (conditionally) ---
    expected_hash = image.compute_expected_hash(cfg)
    have_image = image.local_image_exists(cfg.project_image_repo, expected_hash)

    if force_rebuild or not have_image:
        reason = "forced" if force_rebuild else "image missing or stale"
        console.print(f"[cyan]rebuilding[/cyan] ({reason})…")
        try:
            compose.run(cfg, ["build", cfg.service_name])
        except subprocess.CalledProcessError as exc:
            console.print(f"[red]build failed[/red] (exit {exc.returncode})")
            sys.exit(exc.returncode)
        expected_hash = image.compute_expected_hash(cfg)
        ok, detail = image.tag_built_service_image(cfg, expected_hash)
        if not ok:
            console.print(
                f"[yellow]warning:[/yellow] could not tag image as "
                f"{cfg.project_image_repo}:{expected_hash} ({detail}); "
                "the staleness check will trigger another rebuild next run."
            )
        image.write_last_build_hash(cfg, expected_hash)
    else:
        console.print(f"[green]image current[/green] (hash={expected_hash})")

    # --- Port-collision precheck ---
    # We run this AFTER build, because compose config needs the image to
    # have been resolvable. (It doesn't strictly need it to exist yet, but
    # putting the check here keeps the "cheap checks first, expensive work
    # later" flow intact for most reruns.)
    compose_args, cleanup = _build_compose_args(cfg, no_host_ports, overrides)
    try:
        _check_port_collisions(cfg, compose_args)
    except subprocess.CalledProcessError:
        cleanup()
        sys.exit(1)

    # --- Host-side state dir (bind-mounted to /commandhistory) ---------------
    # Must exist before `compose up` so docker doesn't auto-create the mount
    # path as a directory it owns (with root perms on Linux).
    cfg.host_state_dir.mkdir(parents=True, exist_ok=True)

    # --- Start ---
    env_overrides = _resolve_runtime_env(cfg)
    console.print(f"[cyan]starting[/cyan] service '{cfg.service_name}'…")
    try:
        compose.run(cfg, compose_args + ["up", "-d"], env_overrides=env_overrides)
    except subprocess.CalledProcessError as exc:
        cleanup()
        console.print(f"[red]compose up failed[/red] (exit {exc.returncode})")
        sys.exit(exc.returncode)
    cleanup()

    console.print(f"[green]ready.[/green] attach with: [bold]carthage attach[/bold]")


# --- Helpers --------------------------------------------------------------


def _parse_overrides(raw: tuple[str, ...]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for entry in raw:
        try:
            host, container = entry.split(":", 1)
            out.append((int(host), int(container)))
        except ValueError:
            console.print(
                f"[red]error:[/red] --port {entry!r} must be HOST:CONTAINER with integers."
            )
            sys.exit(2)
    return out


def _resolve_runtime_env(cfg: CarthageConfig) -> dict[str, str]:
    """Compose env overrides we resolve per-invocation (not stored in the
    compose file itself): memory limit, cpu count, base-image version.

    Mem/cpu fall back to unlimited (`0`) if we can't detect — Docker treats
    that as "no limit". On macOS, /proc/meminfo doesn't exist.

    `CARTHAGE_BASE_IMAGE_VERSION` is the OCI `image.version` label read off
    the locally-cached base image and stamped onto the running container as
    a label (see compose template). Empty when the base image pre-dates
    v1.1.0 (when we started setting the label) — `status` then shows
    "unknown" rather than a misleading version string.
    """
    overrides: dict[str, str] = {}
    mem = compose.host_mem_limit_bytes()
    if mem:
        overrides["CARTHAGE_MEM_LIMIT"] = str(mem)
    cpus = os.cpu_count() or 0
    if cpus > 1:
        overrides["CARTHAGE_CPUS"] = str(max(1, cpus - 1))
    overrides["CARTHAGE_BASE_IMAGE_VERSION"] = image.get_base_image_version(cfg.base_image) or ""
    return overrides


def _build_compose_args(
    cfg: CarthageConfig,
    no_host_ports: bool,
    overrides: list[tuple[int, int]],
) -> tuple[list[str], callable]:
    """Return (extra compose args, cleanup fn).

    Without port mutations, this is simply `([], lambda: None)`. With
    mutations, we generate an override compose file via `tempfile` and point
    compose at it with `-f`. The cleanup fn deletes that temp file.
    """
    if not no_host_ports and not overrides:
        return [], lambda: None

    # Build the override YAML. Compose's override-file semantics are that list
    # fields (`ports`) merge positionally — which isn't what we want. Using
    # `!reset` on ports (compose v2.24+) is the clean answer; we combine
    # `ports: !reset []` with explicit new entries.
    parts: list[str] = ["services:", f"  {cfg.service_name}:"]
    if no_host_ports:
        parts.append("    ports: !reset []")
    if overrides:
        if not no_host_ports:
            # When adding without resetting, just append — compose's default merge
            # behavior will concatenate. We accept that this may produce duplicates
            # if the user picks a port that's already in the compose file; the
            # collision check below will catch that case.
            parts.append("    ports:")
        else:
            # After a !reset, `ports:` needs to be re-specified fresh.
            parts[-1] = "    ports: !override"
        for host, container in overrides:
            parts.append(f"      - \"{host}:{container}\"")

    override_content = "\n".join(parts) + "\n"
    tmp_dir = tempfile.mkdtemp(prefix="carthage-compose-override-")
    override_path = Path(tmp_dir) / "override.yaml"
    override_path.write_text(override_content)

    extra_args = ["-f", str(cfg.compose_file), "-f", str(override_path)]

    def cleanup() -> None:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Note: extra_args intentionally *replaces* the default -f args that
    # compose.py injects — we rebuild the full `-f` sequence here. This
    # matters because compose.run() always inserts its own `-f` too. To
    # avoid duplication, we return extra args that we'll prepend, and
    # compose.run sees them and skips its own default. See compose.run.
    return ["--extra-f-sequence"] + extra_args, cleanup


def _check_port_collisions(cfg: CarthageConfig, compose_args: list[str]) -> None:
    """Run the collision precheck. Exits non-zero via CalledProcessError
    (caught by the caller) on conflict."""
    bindings = ports.extract_host_ports(cfg)
    if not bindings:
        return
    conflicts = ports.find_conflicts(bindings)
    if not conflicts:
        return
    console.print("[red]port collision detected:[/red]")
    for c in conflicts:
        console.print(
            f"  - host :{c.binding.published} (for service '{c.binding.service}') "
            f"is already in use by {c.owner}"
        )
    console.print(
        "\nSuggestions:\n"
        "  - stop the offending project: `cd <that project> && carthage down`\n"
        "  - run this project without host ports: `carthage up --no-host-ports`\n"
        "  - use a different host port: `carthage up --port <HOST>:<CONTAINER>`"
    )
    raise subprocess.CalledProcessError(1, "port-collision")


