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
        content = override_path.read_text()
    finally:
        cleanup()

    assert args[:2] == ["--extra-f-sequence", "-f"]
    assert "    environment:\n      EDITOR: \"vim\"" in content
    assert "CARTHAGE: \"nope\"" not in content
    assert 'source: "/tmp/notes"' in content
    assert 'target: "/home/carthage/.notes"' in content
    assert "read_only: true" in content
    assert 'source: "/tmp/scratch"' in content
    assert "read_only: false" in content


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
        content = Path(args[-1]).read_text()
    finally:
        cleanup()

    assert "EDITOR" not in content
    assert "/tmp/notes" not in content
    assert "/tmp/scratch" in content
