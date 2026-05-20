"""Agent profile definitions."""

from __future__ import annotations

from dataclasses import dataclass

from carthage.runtime import RuntimeEnv, RuntimeMount, RuntimeOverlay


DEFAULT_TMUX_SESSION = "carthage-session"


class AgentConfigError(ValueError):
    """Personal agent config is missing or unusable."""


@dataclass(frozen=True)
class AgentProfile:
    kind: str
    binary: str
    install_commands: tuple[str, ...]
    launch_command: str
    tmux_window: str
    auth_mounts: tuple[RuntimeMount, ...] = ()
    managed_skill_dir: str | None = None

    def runtime_overlay(
        self,
        *,
        include_mounts: bool = True,
        launch: bool = False,
    ) -> RuntimeOverlay:
        environment: tuple[RuntimeEnv, ...] = ()
        if launch:
            environment = (
                RuntimeEnv("agent-kind", "CARTHAGE_AGENT_KIND", self.kind),
                RuntimeEnv("agent-binary", "CARTHAGE_AGENT_BINARY", self.binary),
                RuntimeEnv("agent-command", "CARTHAGE_AGENT_COMMAND", self.launch_command),
                RuntimeEnv("agent-window", "CARTHAGE_AGENT_WINDOW", self.tmux_window),
                RuntimeEnv("tmux-session", "CARTHAGE_TMUX_SESSION", DEFAULT_TMUX_SESSION),
            )
        return RuntimeOverlay(
            mounts=self.auth_mounts if include_mounts else (),
            environment=environment,
        )


CLAUDE_PROFILE = AgentProfile(
    kind="claude",
    binary="claude",
    install_commands=(),
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

CODEX_PROFILE = AgentProfile(
    kind="codex",
    binary="codex",
    install_commands=(
        "npm install -g @openai/codex",
        "codex --version",
    ),
    launch_command="codex --full-auto",
    tmux_window="codex",
    auth_mounts=(
        RuntimeMount("agent-codex-dir", "${HOME}/.codex", "/home/carthage/.codex", "rw"),
    ),
)


AGENT_PROFILES: dict[str, AgentProfile] = {
    CLAUDE_PROFILE.kind: CLAUDE_PROFILE,
    CODEX_PROFILE.kind: CODEX_PROFILE,
}


def supported_agent_kinds() -> tuple[str, ...]:
    return tuple(sorted(AGENT_PROFILES))


def get_agent_profile(kind: str) -> AgentProfile | None:
    return AGENT_PROFILES.get(kind)


def agent_profiles_for(kinds: tuple[str, ...]) -> tuple[AgentProfile, ...]:
    return tuple(profile for kind in kinds if (profile := get_agent_profile(kind)) is not None)


def require_agent_profile(kind: str | None) -> AgentProfile:
    if kind is None:
        raise AgentConfigError(
            "no default agent configured. Run `carthage agent use codex` "
            "or `carthage agent use claude` first."
        )
    profile = get_agent_profile(kind)
    if profile is None:
        raise AgentConfigError(
            f"default agent {kind!r} is not supported by this CLI. "
            "Run `carthage agent list` to see supported agents."
        )
    return profile
