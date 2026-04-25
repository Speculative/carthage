"""`carthage down` — stop the project container without destroying volumes."""

from __future__ import annotations

import subprocess
import sys

import click
from rich.console import Console

from carthage import compose
from carthage.config import ConfigError, load_config

console = Console()


@click.command()
def down() -> None:
    """Stop the project container. Volumes are preserved."""
    try:
        cfg = load_config()
    except ConfigError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(2)

    try:
        compose.run(cfg, ["down"])
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)

    console.print("[green]stopped.[/green]")
