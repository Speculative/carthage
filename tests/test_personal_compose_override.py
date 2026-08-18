import json
from pathlib import Path

from carthage.commands.up import _build_compose_args, _resolve_runtime_env
from carthage.config import load_config
from carthage.personal_config import load_personal_config


def _write_project(root: Path, extra: str = "") -> None:
    carthage_dir = root / ".carthage"
    carthage_dir.mkdir()
    (carthage_dir / "config.toml").write_text(
        f"""
[carthage]
schema_version = 1
template_version = 1
base_image_tag = "v2"
service_name = "dev"
project_slug = "demo"
{extra}
"""
    )
    (carthage_dir / "docker-compose.yaml").write_text("services:\n  dev:\n    image: demo\n")


def _write_personal(path: Path) -> None:
    path.write_text(
        """
[carthage]
schema_version = 1

[[mounts]]
id = "notes"
source = "/tmp/notes"
target = "/home/carthage/.notes"
mode = "ro"

[[mounts]]
id = "scratch"
source = "/tmp/scratch"
target = "/scratch"
mode = "rw"

[[environment]]
id = "editor"
name = "EDITOR"
value = "vim"

[[environment]]
id = "reserved"
name = "CARTHAGE"
value = "nope"

[agents]
installed = ["claude"]
default = "claude"
"""
    )


def test_personal_compose_override_adds_mounts_and_environment(tmp_path):
    _write_project(tmp_path)
    personal_path = tmp_path / "personal.toml"
    _write_personal(personal_path)
    cfg = load_config(tmp_path)
    personal = load_personal_config(personal_path)
    assert len(personal.warnings) == 1
    assert "reserved by Carthage" in personal.warnings[0]

    args, cleanup = _build_compose_args(cfg, False, [], personal)
    try:
        override_path = Path(args[-1])
        content = json.loads(override_path.read_text())
    finally:
        cleanup()

    assert args[:2] == ["--extra-f-sequence", "-f"]
    service = content["services"]["dev"]
    assert service["environment"] == {
        "CARTHAGE_AGENT_BINARY": "claude",
        "CARTHAGE_AGENT_COMMAND": "claude --dangerously-skip-permissions",
        "CARTHAGE_AGENT_KIND": "claude",
        "CARTHAGE_AGENT_UPDATE_COMMAND": "",
        "CARTHAGE_AGENT_WINDOW": "claude",
        "CARTHAGE_TMUX_SESSION": "carthage-session",
        "CLAUDE_CODE_NO_FLICKER": "1",
        "EDITOR": "vim",
    }
    assert service["volumes"] == [
        {
            "type": "bind",
            "source": "/tmp/notes",
            "target": "/home/carthage/.notes",
            "read_only": True,
        },
        {
            "type": "bind",
            "source": "/tmp/scratch",
            "target": "/scratch",
            "read_only": False,
        },
        {
            "type": "bind",
            "source": str(cfg.agent_state_dir("claude")),
            "target": "/home/carthage/.claude",
            "read_only": False,
        },
        {
            "type": "bind",
            "source": "${HOME}/.claude/.credentials.json",
            "target": "/home/carthage/.claude/.credentials.json",
            "read_only": False,
        },
        {
            "type": "bind",
            "source": "${HOME}/.claude.json",
            "target": "/home/carthage/.claude.json",
            "read_only": False,
        },
    ]


def test_project_can_disable_personal_items_by_id(tmp_path):
    _write_project(
        tmp_path,
        """
[personal]
disable = ["notes", "editor"]
""",
    )
    personal_path = tmp_path / "personal.toml"
    _write_personal(personal_path)
    cfg = load_config(tmp_path)
    personal = load_personal_config(personal_path)

    args, cleanup = _build_compose_args(cfg, False, [], personal)
    try:
        content = json.loads(Path(args[-1]).read_text())
    finally:
        cleanup()

    service = content["services"]["dev"]
    assert service["environment"] == {
        "CARTHAGE_AGENT_BINARY": "claude",
        "CARTHAGE_AGENT_COMMAND": "claude --dangerously-skip-permissions",
        "CARTHAGE_AGENT_KIND": "claude",
        "CARTHAGE_AGENT_UPDATE_COMMAND": "",
        "CARTHAGE_AGENT_WINDOW": "claude",
        "CARTHAGE_TMUX_SESSION": "carthage-session",
        "CLAUDE_CODE_NO_FLICKER": "1",
    }
    assert service["volumes"] == [
        {
            "type": "bind",
            "source": "/tmp/scratch",
            "target": "/scratch",
            "read_only": False,
        },
        {
            "type": "bind",
            "source": str(cfg.agent_state_dir("claude")),
            "target": "/home/carthage/.claude",
            "read_only": False,
        },
        {
            "type": "bind",
            "source": "${HOME}/.claude/.credentials.json",
            "target": "/home/carthage/.claude/.credentials.json",
            "read_only": False,
        },
        {
            "type": "bind",
            "source": "${HOME}/.claude.json",
            "target": "/home/carthage/.claude.json",
            "read_only": False,
        },
    ]


def test_personal_and_port_overrides_are_separate_files(tmp_path):
    _write_project(tmp_path)
    personal_path = tmp_path / "personal.toml"
    _write_personal(personal_path)
    cfg = load_config(tmp_path)
    personal = load_personal_config(personal_path)

    args, cleanup = _build_compose_args(cfg, True, [], personal)
    try:
        assert args[0] == "--extra-f-sequence"
        override_files = args[4::2]
        assert [Path(path).name for path in override_files] == ["runtime.json", "ports.yaml"]
        assert json.loads(Path(override_files[0]).read_text())["services"]["dev"]["environment"] == {
            "CARTHAGE_AGENT_BINARY": "claude",
            "CARTHAGE_AGENT_COMMAND": "claude --dangerously-skip-permissions",
            "CARTHAGE_AGENT_KIND": "claude",
            "CARTHAGE_AGENT_UPDATE_COMMAND": "",
            "CARTHAGE_AGENT_WINDOW": "claude",
            "CARTHAGE_TMUX_SESSION": "carthage-session",
            "CLAUDE_CODE_NO_FLICKER": "1",
            "EDITOR": "vim",
        }
        assert "ports: !reset []" in Path(override_files[1]).read_text()
    finally:
        cleanup()


def test_installed_codex_agent_adds_auth_mount_and_launch_env(tmp_path):
    _write_project(tmp_path)
    personal_path = tmp_path / "personal.toml"
    personal_path.write_text(
        """
[carthage]
schema_version = 1

[agents]
installed = ["claude", "codex"]
default = "codex"
"""
    )
    cfg = load_config(tmp_path)
    personal = load_personal_config(personal_path)

    args, cleanup = _build_compose_args(cfg, False, [], personal)
    try:
        content = json.loads(Path(args[-1]).read_text())
    finally:
        cleanup()

    service = content["services"]["dev"]
    assert service["environment"] == {
        "CARTHAGE_AGENT_BINARY": "codex",
        "CARTHAGE_AGENT_COMMAND": "codex --dangerously-bypass-approvals-and-sandbox",
        "CARTHAGE_AGENT_KIND": "codex",
        "CARTHAGE_AGENT_UPDATE_COMMAND": "codex update",
        "CARTHAGE_AGENT_WINDOW": "codex",
        "CARTHAGE_TMUX_SESSION": "carthage-session",
    }
    assert "CLAUDE_CODE_NO_FLICKER" not in service["environment"]
    assert {
        "type": "bind",
        "source": str(cfg.agent_state_dir("claude")),
        "target": "/home/carthage/.claude",
        "read_only": False,
    } in service["volumes"]
    assert {
        "type": "bind",
        "source": "${HOME}/.claude/.credentials.json",
        "target": "/home/carthage/.claude/.credentials.json",
        "read_only": False,
    } in service["volumes"]
    assert {
        "type": "bind",
        "source": "${HOME}/.claude.json",
        "target": "/home/carthage/.claude.json",
        "read_only": False,
    } in service["volumes"]
    assert {
        "type": "bind",
        "source": str(cfg.agent_state_dir("codex")),
        "target": "/home/carthage/.codex",
        "read_only": False,
    } in service["volumes"]
    assert {
        "type": "bind",
        "source": "${HOME}/.codex/auth.json",
        "target": "/home/carthage/.codex/auth.json",
        "read_only": False,
    } in service["volumes"]


def test_configured_launch_app_preserves_agent_provider_env(tmp_path):
    _write_project(tmp_path)
    personal_path = tmp_path / "personal.toml"
    personal_path.write_text(
        """
[carthage]
schema_version = 1

[agents]
installed = ["claude", "codex"]
default = "codex"

[launch]
command = "ostraka tui"
window = "ostraka"
"""
    )
    cfg = load_config(tmp_path)
    personal = load_personal_config(personal_path)

    args, cleanup = _build_compose_args(cfg, False, [], personal)
    try:
        content = json.loads(Path(args[-1]).read_text())
    finally:
        cleanup()

    environment = content["services"]["dev"]["environment"]
    assert environment["CARTHAGE_AGENT_BINARY"] == "codex"
    assert environment["CARTHAGE_AGENT_COMMAND"] == "codex --dangerously-bypass-approvals-and-sandbox"
    assert environment["CARTHAGE_TMUX_COMMAND"] == "ostraka tui"
    assert environment["CARTHAGE_TMUX_WINDOW"] == "ostraka"


def test_codex_only_config_does_not_mount_claude_auth(tmp_path):
    _write_project(tmp_path)
    personal_path = tmp_path / "personal.toml"
    personal_path.write_text(
        """
[carthage]
schema_version = 1

[agents]
installed = ["codex"]
default = "codex"
"""
    )
    cfg = load_config(tmp_path)
    personal = load_personal_config(personal_path)

    args, cleanup = _build_compose_args(cfg, False, [], personal)
    try:
        content = json.loads(Path(args[-1]).read_text())
    finally:
        cleanup()

    service = content["services"]["dev"]
    assert service["volumes"] == [
        {
            "type": "bind",
            "source": str(cfg.agent_state_dir("codex")),
            "target": "/home/carthage/.codex",
            "read_only": False,
        },
        {
            "type": "bind",
            "source": "${HOME}/.codex/auth.json",
            "target": "/home/carthage/.codex/auth.json",
            "read_only": False,
        },
    ]


def test_agent_auto_update_env_tracks_offline_flag(tmp_path):
    _write_project(tmp_path)
    cfg = load_config(tmp_path)

    online = _resolve_runtime_env(cfg, offline=False)
    offline = _resolve_runtime_env(cfg, offline=True)

    assert online["CARTHAGE_AGENT_AUTO_UPDATE"] == "1"
    assert offline["CARTHAGE_AGENT_AUTO_UPDATE"] == "0"
