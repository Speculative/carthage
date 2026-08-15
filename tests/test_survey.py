from carthage.commands.survey import check_agent_auth_dirs
from carthage.personal_config import PersonalAgents, PersonalConfig, PersonalConfigResult


def _personal_result(tmp_path, *agents: str) -> PersonalConfigResult:
    return PersonalConfigResult(
        config=PersonalConfig(
            agents=PersonalAgents(installed=agents, default=agents[0] if agents else None),
        ),
        warnings=(),
        path=tmp_path / "config.toml",
        exists=True,
    )


def test_agent_auth_dirs_use_profile_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "auth.json").write_text("{}")

    results = check_agent_auth_dirs(_personal_result(tmp_path, "codex"))

    assert len(results) == 1
    assert results[0].name == "codex auth"
    assert results[0].ok is True
    assert results[0].detail == str(tmp_path / ".codex" / "auth.json")


def test_agent_auth_dirs_report_profile_login_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    results = check_agent_auth_dirs(_personal_result(tmp_path, "codex"))

    assert len(results) == 1
    assert results[0].name == "codex auth"
    assert results[0].ok is False
    assert "Run `codex --login` on the host first." in results[0].detail
