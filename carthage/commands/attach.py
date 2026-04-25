"""`carthage attach` — drop into the project's tmux Claude session."""

from __future__ import annotations

import subprocess
import sys

import click
from rich.console import Console

from carthage import compose
from carthage.config import ConfigError, load_config

console = Console()


@click.command()
@click.option(
    "--session",
    default="claude",
    help="Name of the tmux session to attach to (default: claude).",
)
def attach(session: str) -> None:
    """Attach to the running container's tmux session."""
    try:
        cfg = load_config()
    except ConfigError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(2)

    # If the session doesn't exist yet (e.g. entrypoint's attempt failed),
    # start one. Using `new-session -A` attaches if it exists, otherwise creates.
    rc = compose.exec_interactive(
        cfg,
        cfg.service_name,
        ["tmux", "new-session", "-A", "-s", session],
    )
    # tmux exits 0 on normal detach; other codes pass through to the user.
    sys.exit(rc)
