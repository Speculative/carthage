from click.testing import CliRunner

from carthage.commands.agent import _set_agents_table, agent


def test_set_agents_table_creates_config():
    text = _set_agents_table("", installed=["claude"], default="claude")

    assert '[carthage]\npersonal_config_version = "1"' in text
    assert '[agents]\ninstalled = ["claude"]\ndefault = "claude"' in text


def test_set_agents_table_replaces_existing_agents_table():
    text = _set_agents_table(
        """
[carthage]
personal_config_version = "1"

[agents]
installed = ["old"]
default = "old"

[image]
apt_packages = ["fzf"]
""",
        installed=["claude"],
        default="claude",
    )

    assert 'installed = ["old"]' not in text
    assert '[agents]\ninstalled = ["claude"]\ndefault = "claude"' in text
    assert '[image]\napt_packages = ["fzf"]' in text


def test_agent_use_writes_personal_config(tmp_path):
    runner = CliRunner()
    home = tmp_path / "home"
    home.mkdir()

    result = runner.invoke(agent, ["use", "claude"], env={"HOME": str(home)})

    assert result.exit_code == 0
    assert (home / ".carthage" / "config.toml").read_text() == (
        "[carthage]\n"
        "personal_config_version = \"1\"\n"
        "\n"
        "[agents]\n"
        "installed = [\"claude\"]\n"
        "default = \"claude\"\n"
    )


def test_agent_use_rejects_unknown_agent(tmp_path):
    runner = CliRunner()
    home = tmp_path / "home"
    home.mkdir()

    result = runner.invoke(agent, ["use", "codex"], env={"HOME": str(home)})

    assert result.exit_code == 2
    assert "unsupported agent 'codex'" in result.output
