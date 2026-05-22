from pathlib import Path

import pytest

from unittest.mock import patch

from carthage.config import ConfigError, SchemaTooOldError, load_config


def _write_project_config(root: Path, extra: str = "") -> None:
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


def test_project_personal_disable_defaults_to_empty(tmp_path):
    _write_project_config(tmp_path)

    cfg = load_config(tmp_path)

    assert cfg.personal_disabled == ()
    assert cfg.schema_version == 1
    assert cfg.template_version == 1
    assert cfg.legacy_annexed_with_cli is None


def test_legacy_project_version_fields_are_loaded(tmp_path):
    carthage_dir = tmp_path / ".carthage"
    carthage_dir.mkdir()
    (carthage_dir / "config.toml").write_text(
        """
[carthage]
version = "1"
base_image_tag = "v2"
annexed_with_cli = "1.2.0"
service_name = "dev"
project_slug = "demo"
"""
    )

    cfg = load_config(tmp_path)

    assert cfg.schema_version == 1
    assert cfg.template_version is None
    assert cfg.legacy_annexed_with_cli == "1.2.0"


def test_numeric_string_project_versions_are_accepted(tmp_path):
    carthage_dir = tmp_path / ".carthage"
    carthage_dir.mkdir()
    (carthage_dir / "config.toml").write_text(
        """
[carthage]
schema_version = "1"
template_version = "1"
base_image_tag = "v2"
service_name = "dev"
project_slug = "demo"
"""
    )

    cfg = load_config(tmp_path)

    assert cfg.schema_version == 1
    assert cfg.template_version == 1


def test_project_schema_version_is_required(tmp_path):
    carthage_dir = tmp_path / ".carthage"
    carthage_dir.mkdir()
    (carthage_dir / "config.toml").write_text(
        """
[carthage]
template_version = 1
base_image_tag = "v2"
service_name = "dev"
project_slug = "demo"
"""
    )

    with pytest.raises(ConfigError, match=r"schema_version.*required"):
        load_config(tmp_path)


def test_project_schema_version_must_be_integer(tmp_path):
    carthage_dir = tmp_path / ".carthage"
    carthage_dir.mkdir()
    (carthage_dir / "config.toml").write_text(
        """
[carthage]
schema_version = "v1"
template_version = 1
base_image_tag = "v2"
service_name = "dev"
project_slug = "demo"
"""
    )

    with pytest.raises(ConfigError, match=r"schema_version.*integer"):
        load_config(tmp_path)


def test_project_template_version_must_be_integer(tmp_path):
    carthage_dir = tmp_path / ".carthage"
    carthage_dir.mkdir()
    (carthage_dir / "config.toml").write_text(
        """
[carthage]
schema_version = 1
template_version = "v1"
base_image_tag = "v2"
service_name = "dev"
project_slug = "demo"
"""
    )

    with pytest.raises(ConfigError, match=r"template_version.*integer"):
        load_config(tmp_path)


def test_newer_project_schema_requires_newer_cli(tmp_path):
    carthage_dir = tmp_path / ".carthage"
    carthage_dir.mkdir()
    (carthage_dir / "config.toml").write_text(
        """
[carthage]
schema_version = 2
template_version = 1
base_image_tag = "v2"
service_name = "dev"
project_slug = "demo"
"""
    )

    with pytest.raises(ConfigError, match=r"newer than this CLI"):
        load_config(tmp_path)


def test_older_unreadable_project_schema_is_rejected(tmp_path):
    carthage_dir = tmp_path / ".carthage"
    carthage_dir.mkdir()
    (carthage_dir / "config.toml").write_text(
        """
[carthage]
schema_version = 1
template_version = 1
base_image_tag = "v2"
service_name = "dev"
project_slug = "demo"
"""
    )

    with patch("carthage.config.MIN_READABLE_PROJECT_SCHEMA_VERSION", 2):
        with pytest.raises(SchemaTooOldError, match=r"older than this CLI"):
            load_config(tmp_path)


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
