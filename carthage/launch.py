"""Resolve the application that owns the main Carthage tmux window."""

from __future__ import annotations

from dataclasses import dataclass

from carthage.agents import AgentProfile, require_agent_profile
from carthage.personal_config import PersonalConfig


@dataclass(frozen=True)
class LaunchSpec:
    provider: AgentProfile
    command: str
    window: str
    configured: bool = False


def resolve_launch(
    config: PersonalConfig,
    agent_override: str | None = None,
) -> LaunchSpec:
    """Resolve the tmux app while retaining an agent provider for Ostraka."""

    provider = require_agent_profile(agent_override or config.agents.default)
    if config.launch.command is not None:
        return LaunchSpec(
            provider=provider,
            command=config.launch.command,
            window=config.launch.window or "app",
            configured=True,
        )
    return LaunchSpec(
        provider=provider,
        command=provider.launch_command,
        window=provider.tmux_window,
    )
