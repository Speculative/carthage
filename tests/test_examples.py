from pathlib import Path

from carthage.config import load_config


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_codex_smoke_example_is_a_valid_project():
    root = REPO_ROOT / "examples" / "codex-smoke"

    cfg = load_config(root)

    assert cfg.project_slug == "example-codex-smoke"
    assert cfg.service_name == "dev"
    assert cfg.dockerfile.read_text().startswith("FROM carthage-base-personal:v1\n")
    assert (root / ".agents" / "skills" / "carthage-env" / "SKILL.md").is_file()
    assert (root / "scripts" / "run.sh").is_file()
    assert (root / "scripts" / "down.sh").is_file()
