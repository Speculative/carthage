from carthage.personal_config import (
    CURRENT_PERSONAL_CONFIG_SCHEMA,
    PersonalAgents,
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


def test_personal_config_path_can_come_from_env(tmp_path, monkeypatch):
    path = tmp_path / "custom-personal.toml"
    path.write_text(
        """
[carthage]
personal_config_version = "1"

[agents]
installed = ["codex"]
default = "codex"
"""
    )
    monkeypatch.setenv("CARTHAGE_PERSONAL_CONFIG", str(path))

    result = load_personal_config()

    assert result.path == path
    assert result.config.agents == PersonalAgents(installed=("codex",), default="codex")


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

[agents]
installed = ["claude"]
default = "claude"
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
        agents=PersonalAgents(installed=("claude",), default="claude"),
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


def test_legacy_agent_kind_is_accepted(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[carthage]
personal_config_version = "1"

[agent]
kind = "claude"
"""
    )

    result = load_personal_config(path)

    assert result.config.agents == PersonalAgents(installed=("claude",), default="claude")
    assert result.warnings == ()


def test_unknown_agents_are_skipped(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[carthage]
personal_config_version = "1"

[agents]
installed = ["gemini", "claude", "claude"]
default = "gemini"
"""
    )

    result = load_personal_config(path)

    assert result.config.agents == PersonalAgents(installed=("claude",), default=None)
    assert len(result.warnings) == 3
    assert "agent 'gemini' is not supported" in result.warnings[0]
    assert "agent 'claude' is duplicated" in result.warnings[1]
    assert "default agent 'gemini' is not supported" in result.warnings[2]


def test_agents_without_default_do_not_pick_one_implicitly(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[carthage]
personal_config_version = "1"

[agents]
installed = ["codex"]
"""
    )

    result = load_personal_config(path)

    assert result.config.agents == PersonalAgents(installed=("codex",), default=None)
    assert len(result.warnings) == 1
    assert "no default agent configured" in result.warnings[0]


def test_codex_agent_config_is_accepted(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[carthage]
personal_config_version = "1"

[agents]
installed = ["claude", "codex"]
default = "codex"
"""
    )

    result = load_personal_config(path)

    assert result.config.agents == PersonalAgents(
        installed=("claude", "codex"),
        default="codex",
    )
    assert result.warnings == ()
