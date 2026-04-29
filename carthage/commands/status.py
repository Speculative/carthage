"""`carthage status` — quick state of the current project.
`carthage status --all` — table of every running Carthage container across
projects.

The `--all` view reads `carthage.managed=true` / `carthage.project=<slug>`
labels from `docker ps` rather than maintaining a host-side registry file.
That keeps state in one place (Docker), avoids stale registry entries when
containers crash or get manually removed, and means there's nothing to
clean up beyond destroying the containers themselves.
"""

from __future__ import annotations

import json
import subprocess
import sys

import click
from rich.console import Console
from rich.table import Table

from carthage import __version__, annex_template_is_outdated, compose, image
from carthage.config import ConfigError, load_config

console = Console()


CARTHAGE_LABEL = "carthage.managed=true"


def _status_all() -> None:
    """Query docker for every container with `carthage.managed=true`."""
    r = subprocess.run(
        [
            "docker", "ps", "-a",
            "--filter", f"label={CARTHAGE_LABEL}",
            "--format", "{{json .}}",
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        console.print(f"[red]docker ps failed:[/red] {r.stderr.strip()}")
        sys.exit(1)

    lines = [l for l in r.stdout.splitlines() if l.strip()]
    if not lines:
        console.print("[dim]no Carthage containers found.[/dim]")
        return

    table = Table(title="Carthage containers")
    table.add_column("project", style="cyan")
    table.add_column("role")
    table.add_column("state")
    table.add_column("uptime")
    table.add_column("host ports")
    table.add_column("base img")  # OCI version label recorded at `up` time

    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        labels = _parse_label_string(row.get("Labels", ""))
        project = labels.get("carthage.project", "?")
        role = labels.get("carthage.role", "?")
        state = row.get("State", "?")
        status_str = row.get("Status", "")
        ports = row.get("Ports", "") or "-"
        # Trim bind addresses for display: "0.0.0.0:3000->3000/tcp" → "3000→3000/tcp"
        ports = _format_ports(ports)
        # Only `dev` containers carry the base-image version label; for
        # sidecars (postgres, redis, …) leave the cell blank rather than
        # printing a confusing "?".
        base_version = labels.get("carthage.base-image-version") if role == "dev" else None
        if base_version:
            base_cell = f"v{base_version}"
        elif role == "dev":
            base_cell = "[dim]unknown[/dim]"  # pre-v1.1.0 container
        else:
            base_cell = ""
        table.add_row(project, role, state, status_str, ports, base_cell)

    console.print(table)


def _parse_label_string(s: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in s.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _format_ports(s: str) -> str:
    if not s or s == "-":
        return "-"
    parts = []
    for p in s.split(", "):
        # e.g. "0.0.0.0:3000->3000/tcp" or "3000/tcp" (internal only)
        p = p.replace("0.0.0.0:", "").replace("[::]:", "")
        parts.append(p.replace("->", "→"))
    return ", ".join(parts)


def _status_current() -> None:
    try:
        cfg = load_config()
    except ConfigError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(2)

    table = Table(title=f"carthage status — {cfg.project_slug}", show_header=False)
    table.add_column("key", style="cyan")
    table.add_column("value")

    # Compose state
    try:
        ps = compose.run(cfg, ["ps", "--format", "json"], capture=True)
        lines = [l for l in ps.stdout.splitlines() if l.strip()]
        if lines:
            running = total = 0
            for line in lines:
                try:
                    row = json.loads(line)
                    total += 1
                    if row.get("State") == "running":
                        running += 1
                except json.JSONDecodeError:
                    pass
            table.add_row("services", f"{running}/{total} running")
        else:
            table.add_row("services", "[yellow]no containers[/yellow] (run `carthage up`)")
    except subprocess.CalledProcessError:
        table.add_row("services", "[red]compose ps failed[/red]")

    # tmux session (reflects whether entrypoint successfully spun it up)
    probe = compose.run(
        cfg,
        ["exec", "-T", cfg.service_name, "tmux", "has-session", "-t", "claude"],
        capture=True, check=False,
    )
    if probe.returncode == 0:
        table.add_row("tmux 'claude'", "[green]alive[/green]")
    else:
        table.add_row("tmux 'claude'", "[dim]absent or container stopped[/dim]")

    # Image freshness
    try:
        expected = image.compute_expected_hash(cfg)
        last = image.read_last_build_hash(cfg)
        have = image.local_image_exists(cfg.project_image_repo, expected)
        if have and last == expected:
            table.add_row("image", f"[green]current[/green] (hash={expected})")
        elif have:
            table.add_row(
                "image",
                f"[yellow]tag exists but last-build-hash differs[/yellow] "
                f"(expected={expected}, last={last})",
            )
        else:
            table.add_row(
                "image",
                f"[yellow]rebuild needed[/yellow] (expected hash={expected})",
            )
    except OSError as exc:
        table.add_row("image", f"[red]hash failed: {exc}[/red]")

    table.add_row("base image", cfg.base_image)

    # Running container's recorded base-image version vs what's now locally
    # cached. Three states:
    #   - both populated and equal → green "current"
    #   - both populated but different → yellow "stale, run `carthage up`"
    #   - container running but no recorded version → "unknown (pre-v1.1.0)"
    #   - no container running → omit the row
    running_version = image.read_running_dev_container_base_version(cfg.compose_project_name)
    if running_version is not None:
        latest_version = image.get_base_image_version(cfg.base_image)
        if not running_version:
            row = "[dim]unknown[/dim] (container annexed before v1.1.0)"
        elif latest_version and running_version == latest_version:
            row = f"[green]current[/green] (v{running_version})"
        elif latest_version:
            row = (
                f"[yellow]stale[/yellow] (running v{running_version}, "
                f"latest v{latest_version} — `carthage up` to refresh)"
            )
        else:
            row = f"v{running_version} (couldn't read latest from {cfg.base_image})"
        table.add_row("running base", row)

    table.add_row("config schema", cfg.version + (
        " [yellow](outdated — run /carthage-migrate)[/yellow]" if cfg.schema_is_outdated else ""
    ))

    annex_label = cfg.annexed_with_cli or "pre-1.0"
    if annex_template_is_outdated(cfg.annexed_with_cli):
        annex_label += (
            f" [yellow](CLI is {__version__} — run /carthage-annex --upgrade)"
            "[/yellow]"
        )
    table.add_row("annexed under", annex_label)

    table.add_row("project root", str(cfg.project_root))

    console.print(table)


@click.command()
@click.option("--all", "all_projects", is_flag=True,
              help="Show every running Carthage container across projects.")
def status(all_projects: bool) -> None:
    """Show runtime state. `--all` for a cross-project table."""
    if all_projects:
        _status_all()
    else:
        _status_current()
