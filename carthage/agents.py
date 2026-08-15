"""Agent profile definitions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
    install_user: str = "root"
    root_install_commands: tuple[str, ...] = ()
    update_command: str | None = None
    auth_mounts: tuple[RuntimeMount, ...] = ()
    launch_environment: tuple[RuntimeEnv, ...] = ()
    auth_check_path: str | None = None
    auth_login_hint: str | None = None
    managed_skill_dir: str | None = None
    state_home: str | None = None

    def runtime_overlay(
        self,
        *,
        include_mounts: bool = True,
        launch: bool = False,
        state_dir: Path | None = None,
    ) -> RuntimeOverlay:
        environment: tuple[RuntimeEnv, ...] = ()
        if launch:
            environment = (
                RuntimeEnv("agent-kind", "CARTHAGE_AGENT_KIND", self.kind),
                RuntimeEnv("agent-binary", "CARTHAGE_AGENT_BINARY", self.binary),
                RuntimeEnv("agent-command", "CARTHAGE_AGENT_COMMAND", self.launch_command),
                RuntimeEnv("agent-window", "CARTHAGE_AGENT_WINDOW", self.tmux_window),
                RuntimeEnv("tmux-session", "CARTHAGE_TMUX_SESSION", DEFAULT_TMUX_SESSION),
                RuntimeEnv("agent-update-command", "CARTHAGE_AGENT_UPDATE_COMMAND", self.update_command or ""),
                *self.launch_environment,
            )
        mounts = self.auth_mounts if include_mounts else ()
        if include_mounts and state_dir is not None and self.state_home is not None:
            mounts = (
                RuntimeMount(
                    f"agent-{self.kind}-state",
                    str(state_dir),
                    self.state_home,
                    "rw",
                ),
                *mounts,
            )
        return RuntimeOverlay(mounts=mounts, environment=environment)


CLAUDE_PROFILE = AgentProfile(
    kind="claude",
    binary="claude",
    install_commands=(
        "curl -fsSL https://claude.ai/install.sh | bash",
        "claude --version",
    ),
    launch_command="claude --dangerously-skip-permissions",
    tmux_window="claude",
    install_user="carthage",
    auth_mounts=(
        RuntimeMount(
            "agent-claude-credentials",
            "${HOME}/.claude/.credentials.json",
            "/home/carthage/.claude/.credentials.json",
            "rw",
        ),
        RuntimeMount(
            "agent-claude-json",
            "${HOME}/.claude.json",
            "/home/carthage/.claude.json",
            "rw",
        ),
    ),
    state_home="/home/carthage/.claude",
    launch_environment=(
        RuntimeEnv("agent-claude-no-flicker", "CLAUDE_CODE_NO_FLICKER", "1"),
    ),
    auth_check_path="~/.claude/.credentials.json",
    auth_login_hint="Run `claude` on the host once to authenticate.",
    managed_skill_dir="~/.claude/skills",
)

CODEX_PROFILE = AgentProfile(
    kind="codex",
    binary="codex",
    root_install_commands=(
        "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
        "apt-get install -y --no-install-recommends nodejs",
        "rm -rf /var/lib/apt/lists/*",
        "node --version",
        "npm --version",
    ),
    install_commands=(
        "npm config set prefix /home/carthage/.local",
        "npm install -g @openai/codex",
        "codex --version",
    ),
    install_user="carthage",
    update_command="codex update",
    launch_command="codex --dangerously-bypass-approvals-and-sandbox",
    tmux_window="codex",
    auth_mounts=(
        RuntimeMount(
            "agent-codex-auth",
            "${HOME}/.codex/auth.json",
            "/home/carthage/.codex/auth.json",
            "rw",
        ),
    ),
    state_home="/home/carthage/.codex",
    auth_check_path="~/.codex/auth.json",
    auth_login_hint="Run `codex --login` on the host first.",
    managed_skill_dir="~/.codex/skills",
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
