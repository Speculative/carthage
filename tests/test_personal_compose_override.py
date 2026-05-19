import json
from pathlib import Path

from carthage.commands.up import _build_compose_args
from carthage.config import load_config
from carthage.personal_config import load_personal_config


def _write_project(root: Path, extra: str = "") -> None:
    carthage_dir = root / ".carthage"
    carthage_dir.mkdir()
    (carthage_dir / "config.toml").write_text(
        f"""
[carthage]
version = "1"
base_image_tag = "v1"
annexed_with_cli = "1.2.0"
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
personal_config_version = "1"

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
    assert service["environment"] == {"EDITOR": "vim"}
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
    assert "environment" not in service
    assert service["volumes"] == [
        {
            "type": "bind",
            "source": "/tmp/scratch",
            "target": "/scratch",
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
            "EDITOR": "vim"
        }
        assert "ports: !reset []" in Path(override_files[1]).read_text()
    finally:
        cleanup()
