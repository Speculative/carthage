"""`carthage agent` — inspect and update the personal default agent."""

from __future__ import annotations

import re
import sys

import click
from rich.console import Console
from rich.table import Table

from carthage.agents import get_agent_profile, supported_agent_kinds
from carthage.personal_config import default_personal_config_path, load_personal_config

console = Console()


@click.group(name="agent")
def agent_group() -> None:
    """Manage personal agent selection."""


@agent_group.command(name="list")
def list_agents() -> None:
    """List supported agents and the personal default."""
    personal = load_personal_config()
    configured = personal.config.agents

    table = Table(title="Carthage agents")
    table.add_column("agent", style="cyan")
    table.add_column("installed")
    table.add_column("default")
    table.add_column("launch command")

    for kind in supported_agent_kinds():
        profile = get_agent_profile(kind)
        table.add_row(
            kind,
            "yes" if kind in configured.installed else "no",
            "yes" if kind == configured.default else "",
            profile.launch_command if profile is not None else "",
        )
    console.print(table)
    if personal.warnings:
        for warning in personal.warnings:
            console.print(f"[yellow]note:[/yellow] personal config: {warning}")


@agent_group.command(name="use")
@click.argument("kind")
def use_agent(kind: str) -> None:
    """Set the personal default agent."""
    if kind not in supported_agent_kinds():
        console.print(
            f"[red]error:[/red] unsupported agent {kind!r}. "
            f"Supported: {', '.join(supported_agent_kinds())}"
        )
        sys.exit(2)

    personal = load_personal_config()
    installed = list(personal.config.agents.installed)
    if kind not in installed:
        installed.insert(0, kind)

    path = personal.path
    try:
        text = path.read_text() if path.exists() else ""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_set_agents_table(text, installed=installed, default=kind))
    except OSError as exc:
        console.print(f"[red]error:[/red] failed to update {path}: {exc}")
        sys.exit(1)

    console.print(f"[green]default agent set to {kind}[/green] in {path}")


def _set_agents_table(text: str, *, installed: list[str], default: str) -> str:
    block = _agents_block(installed=installed, default=default)
    if not text.strip():
        return (
            "[carthage]\n"
            "personal_config_version = \"1\"\n"
            "\n"
            f"{block}"
        )

    match = re.search(r"(?ms)^\[agents\]\n.*?(?=^\[|\Z)", text)
    if match:
        updated = text[: match.start()] + block.rstrip("\n") + "\n" + text[match.end():]
    else:
        suffix = "" if text.endswith("\n") else "\n"
        updated = text + suffix + "\n" + block
    return updated if updated.endswith("\n") else updated + "\n"


def _agents_block(*, installed: list[str], default: str) -> str:
    installed_repr = ", ".join(f'"{kind}"' for kind in installed)
    return (
        "[agents]\n"
        f"installed = [{installed_repr}]\n"
        f"default = \"{default}\"\n"
    )


agent = agent_group
