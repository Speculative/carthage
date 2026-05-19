"""`carthage build` — force a rebuild of the project's image."""

from __future__ import annotations

import subprocess
import sys

import click
from rich.console import Console

from carthage import compose, image
from carthage.config import ConfigError, load_config
from carthage.personal_config import load_personal_config
from carthage.personal_image import build_personal_image, personal_image_ref

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

    from_ref = _dockerfile_from_ref(cfg)
    pulls_local_personal_image = (
        pull and from_ref is not None and from_ref == personal_image_ref(cfg.base_image_tag)
    )
    if pulls_local_personal_image:
        console.print(f"[cyan]refreshing[/cyan] {cfg.base_image}…")
        ok, detail = image.pull_base_image(cfg.base_image)
        if not ok:
            console.print(f"[red]base image refresh failed:[/red] {detail}")
            sys.exit(1)
        personal = load_personal_config()
        for warning in personal.warnings:
            console.print(f"[yellow]note:[/yellow] personal config: {warning}")
        console.print(f"[cyan]rebuilding[/cyan] {from_ref}…")
        ok, detail = build_personal_image(
            base_image=cfg.base_image,
            target_image=from_ref,
            config=personal.config,
        )
        if not ok:
            console.print(f"[red]personal image build failed:[/red] {detail}")
            sys.exit(1)

    args = ["build"]
    if no_cache:
        args.append("--no-cache")
    if pull and not pulls_local_personal_image:
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

    ok, detail = image.tag_built_service_image(cfg, new_hash)
    if ok:
        console.print(f"[green]tagged[/green] {detail}")
    else:
        console.print(
            f"[yellow]warning:[/yellow] could not tag image as "
            f"{cfg.project_image_repo}:{new_hash} ({detail}); "
            "the staleness check will rebuild next time."
        )

    console.print(f"[green]build complete.[/green] hash={new_hash}")


def _dockerfile_from_ref(cfg) -> str | None:
    try:
        return image.parse_base_image(cfg.dockerfile.read_text())
    except OSError:
        return None
