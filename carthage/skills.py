"""Helpers for reading the personal skills installed at `~/.claude/skills/`.

Carthage manages two personal skills:
  - `carthage-annex`   — annex a project into Carthage. Implemented.
  - `carthage-migrate` — schema migrations between major versions.
                         Reserved; not implemented in v1.

Both are installed by `carthage fortify` from package data bundled into the
CLI wheel. Each SKILL.md's frontmatter carries a `version` field equal to
the CLI version it shipped with — `survey` and `--version` flag drift if
someone hand-edits a SKILL.md.
"""

from __future__ import annotations

import re
from pathlib import Path

# The skills we expect `carthage fortify` to have installed. A future skill
# (e.g. carthage-migrate) is added here when it lands.
MANAGED_SKILLS: tuple[str, ...] = ("carthage-annex",)

SKILLS_DIR = Path.home() / ".claude" / "skills"


def skill_path(name: str) -> Path:
    return SKILLS_DIR / name / "SKILL.md"


def read_skill_version(name: str) -> str | None:
    """Extract the `version:` field from the SKILL.md frontmatter, or None
    if the skill isn't installed or its frontmatter lacks a version."""
    path = skill_path(name)
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except OSError:
        return None

    # Frontmatter is between leading `---` and the next `---` on its own line.
    if not text.startswith("---"):
        return None
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        return None
    frontmatter = parts[0]
    m = re.search(r"^version:\s*['\"]?([^'\"\n]+)['\"]?\s*$", frontmatter, re.MULTILINE)
    return m.group(1).strip() if m else None


def installed_skills() -> list[tuple[str, str | None]]:
    """Return `(name, version)` for each managed skill currently on disk.

    Skills not installed are omitted. Use this for `carthage --version` and
    the `carthage survey` version-alignment check.
    """
    out: list[tuple[str, str | None]] = []
    for name in MANAGED_SKILLS:
        if skill_path(name).is_file():
            out.append((name, read_skill_version(name)))
    return out
