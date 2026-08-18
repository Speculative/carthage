from carthage.commands.survey import check_agent_auth_dirs, check_personal_mount_sources
from carthage.personal_config import (
    PersonalAgents,
    PersonalConfig,
    PersonalConfigResult,
    PersonalMount,
)


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


def test_personal_mount_sources_are_checked(tmp_path):
    source = tmp_path / "notes"
    source.mkdir()
    result = PersonalConfigResult(
        config=PersonalConfig(
            mounts=(PersonalMount("notes", str(source), "/notes", "ro"),),
        ),
        warnings=(),
        path=tmp_path / "config.toml",
        exists=True,
    )

    checks = check_personal_mount_sources(result)

    assert checks[0].name == "personal mount notes"
    assert checks[0].ok is True


def test_personal_mount_sources_report_missing_path(tmp_path):
    result = PersonalConfigResult(
        config=PersonalConfig(
            mounts=(PersonalMount("notes", str(tmp_path / "missing"), "/notes", "ro"),),
        ),
        warnings=(),
        path=tmp_path / "config.toml",
        exists=True,
    )

    checks = check_personal_mount_sources(result)

    assert checks[0].ok is False
