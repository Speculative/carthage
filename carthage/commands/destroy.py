"""`carthage destroy` — nuke the project's Carthage state (containers, images, volumes)."""

from __future__ import annotations

import subprocess
import sys

import click
from rich.console import Console

from carthage import compose
from carthage.config import ConfigError, load_config

console = Console()


@click.command()
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
def destroy(yes: bool) -> None:
    """Stop and remove containers, volumes, and project-local images."""
    try:
        cfg = load_config()
    except ConfigError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(2)

    if not yes:
        click.confirm(
            f"This will remove containers, volumes, and the "
            f"{cfg.project_image_repo}:* image(s) for project '{cfg.project_slug}'. "
            "Continue?",
            abort=True,
        )

    try:
        compose.run(cfg, ["down", "-v", "--rmi", "local"])
    except subprocess.CalledProcessError as exc:
        console.print(
            f"[yellow]compose down returned {exc.returncode}[/yellow]; "
            "continuing with image cleanup."
        )

    # Also sweep any lingering carthage-<slug>:* images (the compose `--rmi
    # local` only removes images compose built itself; our manually-tagged
    # hash copies survive that and accumulate otherwise).
    try:
        result = subprocess.run(
            [
                "docker",
                "images",
                "--format",
                "{{.Repository}}:{{.Tag}}",
                cfg.project_image_repo,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        tags = [t for t in result.stdout.strip().splitlines() if t]
        if tags:
            subprocess.run(["docker", "rmi", "-f", *tags], check=False)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Remove the hash file so the next `up` rebuilds from clean.
    try:
        cfg.last_build_hash_file.unlink()
    except FileNotFoundError:
        pass

    console.print("[green]destroyed.[/green]")
