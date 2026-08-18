"""`carthage attach` — drop into the project's tmux session."""

from __future__ import annotations

import subprocess
import sys
import shlex

import click
from rich.console import Console

from carthage import compose
from carthage.agents import DEFAULT_TMUX_SESSION, AgentConfigError
from carthage.config import ConfigError, load_config
from carthage.launch import resolve_launch
from carthage.personal_config import load_personal_config

console = Console()


@click.command()
@click.option(
    "--session",
    default=None,
    help="Name of the tmux session to attach to (default: Carthage's main session).",
)
def attach(session: str | None) -> None:
    """Attach to the running container's tmux session."""
    try:
        cfg = load_config()
    except ConfigError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(2)

    personal = load_personal_config()
    try:
        launch = resolve_launch(personal.config)
    except AgentConfigError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(2)
    if session is None:
        session = DEFAULT_TMUX_SESSION

    _ensure_agent_window(cfg, session=session, window=launch.window, command=launch.command)

    # If the session doesn't exist yet (e.g. entrypoint's attempt failed),
    # start one. Using `new-session -A` attaches if it exists, otherwise creates.
    rc = compose.exec_interactive(
        cfg,
        cfg.service_name,
        ["tmux", "new-session", "-A", "-s", session],
    )
    # tmux exits 0 on normal detach; other codes pass through to the user.
    sys.exit(rc)


def _ensure_agent_window(cfg, *, session: str, window: str, command: str) -> None:
    """Best-effort creation of the configured agent window.

    This lets a running container pick up a personal-agent change without
    replacing the single tmux session users attach to.
    """
    script = f"""
session={shlex.quote(session)}
window={shlex.quote(window)}
command={shlex.quote(command)}
tmux_command="bash -lc '$command; exec bash -l'"
if ! tmux has-session -t "$session" 2>/dev/null; then
    tmux new-session -d -s "$session" -n "$window" "$tmux_command"
elif ! tmux list-windows -t "$session" -F '#{{window_name}}' | grep -Fx -- "$window" >/dev/null; then
    tmux new-window -d -t "$session:" -n "$window" "$tmux_command"
fi
tmux select-window -t "$session:$window"
"""
    compose.run(
        cfg,
        ["exec", "-T", cfg.service_name, "sh", "-lc", script],
        capture=True,
        check=False,
    )
