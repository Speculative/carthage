from carthage.personal_config import (
    CURRENT_PERSONAL_CONFIG_SCHEMA,
    PersonalConfig,
    PersonalEnvironment,
    PersonalImage,
    PersonalMount,
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
    path.write_text(
        """
[carthage]
personal_config_version = "1"

[[mounts]]
id = "notes"
source = "~/notes"
target = "/home/carthage/.notes"
mode = "ro"

[[environment]]
id = "editor"
name = "EDITOR"
value = "vim"

[image]
apt_packages = ["fzf", "shellcheck"]
"""
    )

    result = load_personal_config(path)

    assert result.config == PersonalConfig(
        version="1",
        mounts=(
            PersonalMount(
                id="notes",
                source=str((tmp_path.home() / "notes")),
                target="/home/carthage/.notes",
                mode="ro",
            ),
        ),
        environment=(PersonalEnvironment(id="editor", name="EDITOR", value="vim"),),
        image=PersonalImage(apt_packages=("fzf", "shellcheck")),
    )
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


def test_invalid_mount_and_environment_entries_are_skipped(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[carthage]
personal_config_version = "1"

[[mounts]]
id = "relative-source"
source = "notes"
target = "/notes"
mode = "ro"

[[mounts]]
id = "bad-mode"
source = "~/notes"
target = "/notes"
mode = "maybe"

[[environment]]
id = "bad-env"
name = "1INVALID"
value = "x"
"""
    )

    result = load_personal_config(path)

    assert result.config == PersonalConfig(version="1")
    assert len(result.warnings) == 3
    assert "source must be absolute" in result.warnings[0]
    assert "mode must be 'ro' or 'rw'" in result.warnings[1]
    assert "not a valid environment variable" in result.warnings[2]


def test_invalid_image_packages_are_skipped(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[carthage]
personal_config_version = "1"

[image]
apt_packages = ["ok-package", "bad/package", "ok-package"]
"""
    )

    result = load_personal_config(path)

    assert result.config.image == PersonalImage(apt_packages=("ok-package",))
    assert len(result.warnings) == 2
    assert "not a valid apt package name" in result.warnings[0]
    assert "duplicated" in result.warnings[1]
