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
        _check_port_collisions(
            cfg,
            compose_args,
            invocation_flags=_reconstruct_flags(force_rebuild, no_host_ports, overrides),
            user_overrides=overrides,
        )
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

    `--port HOST:CONTAINER` semantics: replace any existing entry that targets
    the same container port. `--port 5174:5173` on a project that publishes
    `5173:5173` yields a single `5174:5173` binding, not both. This requires
    rewriting the full ports list under `!override` because compose's default
    list-merge would otherwise produce duplicates.
    """
    if not no_host_ports and not overrides:
        return [], lambda: None

    parts: list[str] = ["services:", f"  {cfg.service_name}:"]
    if no_host_ports:
        # User asked for no host ports at all; ignore --port too — explicit
        # mutual exclusion would surprise users who combined them. Reset wins.
        parts.append("    ports: !reset []")
    elif overrides:
        # Resolve original bindings for this service, replace any whose
        # container port matches a --port override, then append leftover
        # overrides as additions. Use !override so compose treats this as a
        # full replacement of the ports list.
        original = [b for b in ports.extract_host_ports(cfg) if b.service == cfg.service_name]
        override_by_container = {container: host for host, container in overrides}

        merged: list[str] = []
        replaced_containers: set[int] = set()
        for b in original:
            if b.target in override_by_container:
                new_host = override_by_container[b.target]
                replaced_containers.add(b.target)
                published = new_host
            else:
                published = b.published
            entry = f"{b.host_ip}:{published}:{b.target}" if b.host_ip else f"{published}:{b.target}"
            if b.protocol and b.protocol != "tcp":
                entry = f"{entry}/{b.protocol}"
            merged.append(entry)
        for host, container in overrides:
            if container not in replaced_containers:
                merged.append(f"{host}:{container}")

        parts.append("    ports: !override")
        for entry in merged:
            parts.append(f"      - \"{entry}\"")

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


def _extract_compose_files(compose_args: list[str]) -> list[str] | None:
    """If compose_args carries an `--extra-f-sequence` block, return the list
    of compose file paths in it. Otherwise None (caller should use defaults)."""
    if not compose_args or compose_args[0] != "--extra-f-sequence":
        return None
    files: list[str] = []
    i = 1
    while i < len(compose_args) and compose_args[i] == "-f":
        if i + 1 >= len(compose_args):
            break
        files.append(compose_args[i + 1])
        i += 2
    return files or None


def _reconstruct_flags(
    force_rebuild: bool,
    no_host_ports: bool,
    overrides: list[tuple[int, int]],
) -> list[str]:
    """Render the user's invocation flags back to a list of CLI tokens, so we
    can echo them in error suggestions verbatim."""
    flags: list[str] = []
    if force_rebuild:
        flags.append("--force-rebuild")
    if no_host_ports:
        flags.append("--no-host-ports")
    for host, container in overrides:
        flags.extend(["--port", f"{host}:{container}"])
    return flags


def _check_port_collisions(
    cfg: CarthageConfig,
    compose_args: list[str],
    invocation_flags: list[str],
    user_overrides: list[tuple[int, int]],
) -> None:
    """Run the collision precheck. Exits non-zero via CalledProcessError
    (caught by the caller) on conflict.

    Reads the same `-f` sequence we'll hand to `compose up`, so user-supplied
    overrides (`--port`, `--no-host-ports`) are honored — without this, the
    precheck would flag collisions on bindings the user already remapped.
    """
    extra_files = _extract_compose_files(compose_args)
    bindings = ports.extract_host_ports(cfg, extra_compose_files=extra_files)
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

    # Build a copy-pasteable retry command: drop any user --port override that
    # targets a container port we're about to remap (otherwise the suggested
    # command would carry the conflicting binding alongside the new one).
    remapped_container_ports = {c.binding.target for c in conflicts}
    carried_flags = list(_reconstruct_flags_excluding(
        invocation_flags, drop_port_for_container=remapped_container_ports,
    ))
    suggested_overrides: list[str] = []
    unresolved: list[int] = []
    for c in conflicts:
        free = ports.find_free_host_port(c.binding.published + 1, c.binding.protocol)
        if free is None:
            unresolved.append(c.binding.published)
            continue
        suggested_overrides.extend(["--port", f"{free}:{c.binding.target}"])

    if unresolved:
        console.print(
            f"\n[red]could not find a free host port near {', '.join(str(p) for p in unresolved)}[/red] "
            "(scanned 100 ports upward). Free a port and retry."
        )
        raise subprocess.CalledProcessError(1, "port-collision")

    retry_cmd = " ".join(["carthage", "up", *carried_flags, *suggested_overrides])
    console.print(
        "\nTo retry on different host ports, run:\n"
        f"  [bold]{retry_cmd}[/bold]\n"
        "[yellow]warning:[/yellow] some apps don't tolerate host-port remapping — "
        "OAuth callbacks, webhooks, CORS allowlists, and hardcoded HMR client ports "
        "may need updating to match the new host port."
    )
    raise subprocess.CalledProcessError(1, "port-collision")


def _reconstruct_flags_excluding(
    flags: list[str],
    drop_port_for_container: set[int],
) -> list[str]:
    """Return `flags` with any `--port HOST:CONTAINER` pair removed when
    CONTAINER is in `drop_port_for_container`. Used to avoid carrying a stale
    user override into the suggested retry command when we're remapping that
    container port to a new host port."""
    out: list[str] = []
    i = 0
    while i < len(flags):
        if flags[i] == "--port" and i + 1 < len(flags):
            value = flags[i + 1]
            try:
                _, container = value.split(":", 1)
                if int(container) in drop_port_for_container:
                    i += 2
                    continue
            except ValueError:
                pass
            out.extend([flags[i], flags[i + 1]])
            i += 2
            continue
        out.append(flags[i])
        i += 1
    return out


