"""Entry point for the `carthage` CLI."""

from __future__ import annotations

import click
from rich.console import Console

from carthage import (
    BASE_IMAGE_REPO,
    EXPECTED_BASE_IMAGE_TAG,
    __version__,
)
from carthage.commands import (
    attach,
    build,
    destroy,
    down,
    fortify,
    status,
    survey,
    up,
)
from carthage.skills import find_drifted_skills, installed_skills

# Subcommands that already report skill drift themselves, or whose job IS to
# fix it. Suppress the preamble warning on these to avoid double-printing
# (`survey`) or self-defeating nags (`fortify`).
_SKIP_DRIFT_PREAMBLE: frozenset[str] = frozenset({"fortify", "survey"})

_stderr = Console(stderr=True)


def _print_version(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    click.echo(f"carthage {__version__}")
    skills = installed_skills()
    if skills:
        parts = ", ".join(f"{name}@{ver or 'unknown'}" for name, ver in skills)
        click.echo(f"  installed skills: {parts}")
    else:
        click.echo("  installed skills: (none — run `carthage fortify`)")
    click.echo(f"  expected base image: {BASE_IMAGE_REPO}:{EXPECTED_BASE_IMAGE_TAG}")
    ctx.exit()


def _warn_if_skills_drifted(invoked: str | None) -> None:
    if invoked in _SKIP_DRIFT_PREAMBLE:
        return
    drifted = find_drifted_skills(__version__)
    if not drifted:
        return
    parts = ", ".join(f"{name}@{ver or 'unknown'}" for name, ver in drifted)
    _stderr.print(
        f"[yellow]note:[/yellow] skill drift detected ({parts}; CLI is "
        f"{__version__}). Run `carthage fortify` to refresh."
    )


@click.group(help="Manage Carthage sandboxed dev-environment containers.")
@click.option(
    "--version",
    is_flag=True,
    callback=_print_version,
    expose_value=False,
    is_eager=True,
    help="Show version information and exit.",
)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Root command group."""
    _warn_if_skills_drifted(ctx.invoked_subcommand)


cli.add_command(fortify.fortify)
cli.add_command(up.up)
cli.add_command(down.down)
cli.add_command(build.build)
cli.add_command(attach.attach)
cli.add_command(status.status)
cli.add_command(survey.survey)
cli.add_command(destroy.destroy)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
