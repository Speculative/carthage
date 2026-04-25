"""`carthage build` — force a rebuild of the project's image."""

from __future__ import annotations

import subprocess
import sys

import click
from rich.console import Console

from carthage import compose, image
from carthage.config import ConfigError, load_config

console = Console()


@click.command()
@click.option("--no-cache", is_flag=True, help="Build without using the layer cache.")
@click.option("--pull", is_flag=True, help="Always pull a newer version of the base image.")
def build(no_cache: bool, pull: bool) -> None:
    """Build (or rebuild) the project's Carthage image."""
    try:
        cfg = load_config()
    except ConfigError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(2)

    args = ["build"]
    if no_cache:
        args.append("--no-cache")
    if pull:
        args.append("--pull")
    args.append(cfg.service_name)

    console.print(f"[cyan]building[/cyan] {cfg.project_image_repo}…")
    try:
        compose.run(cfg, args)
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]build failed[/red] (exit {exc.returncode})")
        sys.exit(exc.returncode)

    # Recompute the hash post-build, now that the base image is definitely
    # pulled (so RepoDigests will be populated).
    new_hash = image.compute_expected_hash(cfg)
    image.write_last_build_hash(cfg, new_hash)

    # Also tag the freshly-built service image as carthage-<slug>:<hash> so
    # the staleness check can find it by name next time.
    service_image = f"carthage-{cfg.project_slug}:latest"
    tagged = f"{cfg.project_image_repo}:{new_hash}"
    # Compose names the image after the project + service by default; find it
    # via `docker compose images --quiet`.
    try:
        result = compose.run(
            cfg,
            ["images", "-q", cfg.service_name],
            capture=True,
        )
        image_id = result.stdout.strip().splitlines()[0] if result.stdout.strip() else None
        if image_id:
            subprocess.run(
                ["docker", "tag", image_id, tagged],
                check=True,
            )
            console.print(f"[green]tagged[/green] {tagged}")
    except (subprocess.CalledProcessError, IndexError):
        # Non-fatal: the hash file is the authority, the tag is a convenience.
        console.print(
            f"[yellow]warning:[/yellow] could not tag image as {tagged}; "
            "staleness check will rebuild next time."
        )

    console.print(f"[green]build complete.[/green] hash={new_hash}")
    _ = service_image  # reserved for future: tag by :latest for discoverability
