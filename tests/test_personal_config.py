from carthage.personal_config import (
    CURRENT_PERSONAL_CONFIG_SCHEMA,
    PersonalConfig,
    load_personal_config,
)


def test_missing_personal_config_returns_defaults(tmp_path):
    path = tmp_path / "config.toml"

    result = load_personal_config(path)

    assert result.config == PersonalConfig(version=CURRENT_PERSONAL_CONFIG_SCHEMA)
    assert result.exists is False
    assert result.warnings == ()
    assert result.path == path


def test_valid_personal_config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[carthage]\npersonal_config_version = "1"\n')

    result = load_personal_config(path)

    assert result.config == PersonalConfig(version="1")
    assert result.exists is True
    assert result.warnings == ()


def test_malformed_toml_warns_and_uses_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[carthage\n")

    result = load_personal_config(path)

    assert result.config == PersonalConfig()
    assert result.exists is True
    assert len(result.warnings) == 1
    assert "malformed TOML" in result.warnings[0]
    assert "ignoring personal config" in result.warnings[0]


def test_missing_carthage_table_warns_and_uses_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[other]\npersonal_config_version = "1"\n')

    result = load_personal_config(path)

    assert result.config == PersonalConfig()
    assert result.exists is True
    assert result.warnings == (
        f"{path}: missing [carthage] table; using personal defaults",
    )


def test_missing_version_warns_and_defaults_to_current(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[carthage]\n")

    result = load_personal_config(path)

    assert result.config == PersonalConfig(version=CURRENT_PERSONAL_CONFIG_SCHEMA)
    assert result.exists is True
    assert len(result.warnings) == 1
    assert "missing [carthage].personal_config_version" in result.warnings[0]


def test_newer_version_warns_and_uses_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[carthage]\npersonal_config_version = "999"\n')

    result = load_personal_config(path)

    assert result.config == PersonalConfig()
    assert result.exists is True
    assert result.warnings == (
        f"{path}: personal_config_version '999' is newer than this CLI supports; "
        "ignoring personal config",
    )
