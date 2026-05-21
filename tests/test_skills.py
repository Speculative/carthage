from pathlib import Path

from carthage.agents import CODEX_PROFILE
from carthage.commands import fortify
from carthage.skills import agent_skill_path


def test_agent_skill_path_expands_user_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    path = agent_skill_path("~/.codex/skills", "carthage-annex")

    assert path == tmp_path / ".codex" / "skills" / "carthage-annex"


def test_link_skill_for_agent_replaces_existing_copy(monkeypatch, tmp_path):
    canonical = tmp_path / ".carthage" / "skills"
    source = canonical / "carthage-annex"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: carthage-annex\n---\n")
    monkeypatch.setattr(fortify, "SKILLS_DIR", canonical)
    monkeypatch.setenv("HOME", str(tmp_path))

    old_copy = tmp_path / ".codex" / "skills" / "carthage-annex"
    old_copy.mkdir(parents=True)
    (old_copy / "SKILL.md").write_text("old\n")

    ok, detail = fortify._link_skill_for_agent(CODEX_PROFILE, "carthage-annex")

    assert ok is True, detail
    assert old_copy.is_symlink()
    assert old_copy.resolve() == source
