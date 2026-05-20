"""Agent profile definitions.

Profiles describe the agent-specific pieces Carthage needs to launch a CLI
agent inside the sandbox. The first implementation keeps Claude as the only
supported profile so existing projects keep their behavior while the hardcoded
paths move behind a registry.
"""

from __future__ import annotations

from dataclasses import dataclass

from carthage.runtime import RuntimeEnv, RuntimeMount, RuntimeOverlay


DEFAULT_AGENT_KIND = "claude"
DEFAULT_TMUX_SESSION = "carthage-session"


@dataclass(frozen=True)
class AgentProfile:
    kind: str
    binary: str
    launch_command: str
    tmux_window: str
    auth_mounts: tuple[RuntimeMount, ...] = ()
    managed_skill_dir: str | None = None

    @property
    def runtime_overlay(self) -> RuntimeOverlay:
        return RuntimeOverlay(
            environment=(
                RuntimeEnv("agent-kind", "CARTHAGE_AGENT_KIND", self.kind),
                RuntimeEnv("agent-binary", "CARTHAGE_AGENT_BINARY", self.binary),
                RuntimeEnv("agent-command", "CARTHAGE_AGENT_COMMAND", self.launch_command),
                RuntimeEnv("agent-window", "CARTHAGE_AGENT_WINDOW", self.tmux_window),
                RuntimeEnv("tmux-session", "CARTHAGE_TMUX_SESSION", DEFAULT_TMUX_SESSION),
            )
        )


CLAUDE_PROFILE = AgentProfile(
    kind="claude",
    binary="claude",
    launch_command="claude --dangerously-skip-permissions",
    tmux_window="claude",
    auth_mounts=(
        RuntimeMount("agent-claude-dir", "${HOME}/.claude", "/home/carthage/.claude", "rw"),
        RuntimeMount(
            "agent-claude-json",
            "${HOME}/.claude.json",
            "/home/carthage/.claude.json",
            "rw",
        ),
    ),
    managed_skill_dir="~/.claude/skills",
)


AGENT_PROFILES: dict[str, AgentProfile] = {
    CLAUDE_PROFILE.kind: CLAUDE_PROFILE,
}


def supported_agent_kinds() -> tuple[str, ...]:
    return tuple(sorted(AGENT_PROFILES))


def get_agent_profile(kind: str) -> AgentProfile | None:
    return AGENT_PROFILES.get(kind)


def default_agent_profile() -> AgentProfile:
    return AGENT_PROFILES[DEFAULT_AGENT_KIND]
