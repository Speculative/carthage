"""Helpers for reading the personal skills installed by Carthage.

Carthage manages one personal skill:
  - `carthage-annex` — annex a project into Carthage.

Both are installed by `carthage fortify` from package data bundled into the
CLI wheel. The canonical copy lives under `~/.carthage/skills/`; agent
discovery paths link to that single copy. Each SKILL.md's frontmatter carries
a `version` field equal to the CLI version it shipped with — `survey` and
`--version` flag drift if someone hand-edits a SKILL.md.
"""

from __future__ import annotations

import re
from pathlib import Path

# The skills we expect `carthage fortify` to have installed.
MANAGED_SKILLS: tuple[str, ...] = ("carthage-annex",)

CARTHAGE_HOME = Path.home() / ".carthage"
SKILLS_DIR = CARTHAGE_HOME / "skills"


def skill_path(name: str) -> Path:
    return SKILLS_DIR / name / "SKILL.md"


def agent_skill_dir(raw_dir: str) -> Path:
    return Path(raw_dir).expanduser()


def agent_skill_path(raw_dir: str, name: str) -> Path:
    return agent_skill_dir(raw_dir) / name


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


def find_drifted_skills(cli_version: str) -> list[tuple[str, str | None]]:
    """Return `(name, on_disk_version)` for installed skills whose version
    doesn't match `cli_version`. Skills that aren't installed at all are
    *not* drift — they require `carthage fortify` to install in the first
    place, but that's a separate concern. The preamble warning is for users
    who have a working install that's gone stale, not for first-time setup.

    A skill present without a parseable `version:` field counts as drift
    (`on_disk_version=None`) — the file exists but we can't tell what it is,
    which usually means a hand-edit broke the frontmatter.
    """
    drifted: list[tuple[str, str | None]] = []
    for name in MANAGED_SKILLS:
        if not skill_path(name).is_file():
            continue
        ver = read_skill_version(name)
        if ver != cli_version:
            drifted.append((name, ver))
    return drifted


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
