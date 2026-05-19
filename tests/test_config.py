from pathlib import Path

import pytest

from carthage.config import ConfigError, load_config


def _write_project_config(root: Path, extra: str = "") -> None:
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


def test_project_personal_disable_defaults_to_empty(tmp_path):
    _write_project_config(tmp_path)

    cfg = load_config(tmp_path)

    assert cfg.personal_disabled == ()


def test_project_personal_disable_is_loaded(tmp_path):
    _write_project_config(
        tmp_path,
        """
[personal]
disable = ["notes", "editor"]
""",
    )

    cfg = load_config(tmp_path)

    assert cfg.personal_disabled == ("notes", "editor")


def test_project_personal_disable_must_be_strings(tmp_path):
    _write_project_config(
        tmp_path,
        """
[personal]
disable = ["notes", 7]
""",
    )

    with pytest.raises(ConfigError, match=r"\[personal\]\.disable"):
        load_config(tmp_path)
