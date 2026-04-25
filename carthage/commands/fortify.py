"""`carthage fortify` — one-time host setup.

Does dependency checks, then installs the personal Claude skills into
`~/.claude/skills/` at the version matching the installed CLI. Idempotent —
re-run after `uv tool upgrade carthage-cli` to pull in matching skill updates.

Skill files are bundled into the CLI wheel via hatch `force-include` (see
pyproject.toml). At runtime we read them out of `importlib.resources` and
copy to disk. This guarantees `skill_version == cli_version` by construction:
the wheel *is* the shared versioned artifact. No network, no git required.
"""

from __future__ import annotations

import importlib.resources as ir
import shutil
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console

from carthage import __version__
from carthage.skills import MANAGED_SKILLS, SKILLS_DIR, read_skill_version

console = Console()


# --- Dep checks (each returns (ok, detail)) -------------------------------


def _check_docker_daemon() -> tuple[bool, str]:
    if not shutil.which("docker"):
        return False, "docker CLI not found on PATH"
    r = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if r.returncode != 0:
        tail = r.stderr.strip().splitlines()
        return False, tail[-1] if tail else "docker daemon not reachable"
    return True, "docker daemon reachable"


def _check_compose_v2() -> tuple[bool, str]:
    r = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
    if r.returncode != 0:
        return False, (
            "docker compose v2 plugin not available. Install the compose v2 "
            "plugin — on Docker Desktop it's bundled; on Linux, install "
            "`docker-compose-plugin` from your package manager."
        )
    first = r.stdout.strip().splitlines()[:1]
    return True, first[0] if first else "ok"


def _check_claude_dir() -> tuple[bool, str]:
    p = Path.home() / ".claude"
    if not p.is_dir():
        return False, (
            f"{p} does not exist. Run `claude` on the host once to authenticate "
            "to Claude Code before using Carthage."
        )
    return True, str(p)


# --- Skill installation ---------------------------------------------------


def _packaged_skills_root():
    """Return an importlib.resources Traversable for the bundled skills
    directory (`carthage/_skills/` inside the installed wheel)."""
    return ir.files("carthage") / "_skills"


def _copy_traversable(src, dest: Path) -> None:
    """Recursively copy an importlib.resources Traversable to a filesystem path.

    Works whether the resource lives on disk (editable / source checkout) or
    inside a zip-like wheel — we only use the Traversable API, not real paths.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        dest_child = dest / child.name
        if child.is_dir():
            _copy_traversable(child, dest_child)
        else:
            dest_child.write_bytes(child.read_bytes())


def _copy_skill_from_package(name: str, target: Path) -> None:
    """Copy the packaged skill `name` to `target`, replacing any existing
    content at that path. Callers decide whether overwrite is appropriate."""
    src = _packaged_skills_root() / name
    if not src.is_dir():
        raise FileNotFoundError(
            f"packaged skill {name!r} not found in CLI wheel. This is a build "
            "bug — did hatch force-include miss `skill/`?"
        )
    if target.exists():
        shutil.rmtree(target)
    _copy_traversable(src, target)


def _install_skill(name: str, *, force: bool = False) -> tuple[bool, str]:
    """Install (or refresh) the named skill at ~/.claude/skills/<name>/.

    Idempotent: if the installed SKILL.md already reports the CLI's version
    and the directory exists, this is a no-op unless `force=True`.
    """
    target = SKILLS_DIR / name
    current_version = read_skill_version(name)

    if not force and current_version == __version__ and target.is_dir():
        return True, f"already at v{__version__}"

    try:
        _copy_skill_from_package(name, target)
    except FileNotFoundError as exc:
        return False, str(exc)
    except OSError as exc:
        return False, f"failed to write {target}: {exc}"

    new_version = read_skill_version(name)
    if new_version != __version__:
        # The copy succeeded, but the SKILL.md frontmatter doesn't carry the
        # version we expected. Still functional; flag so the user notices.
        return True, (
            f"installed, but SKILL.md reports v{new_version or 'unknown'} "
            f"(CLI is v{__version__})"
        )
    return True, f"installed v{__version__}"


# --- CLI ------------------------------------------------------------------


@click.command()
@click.option(
    "--force",
    is_flag=True,
    help="Reinstall skills even if the installed version already matches.",
)
def fortify(force: bool) -> None:
    """One-time host setup: verify deps, install personal Carthage skills."""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[str, bool, str]] = []

    ok, detail = _check_docker_daemon()
    rows.append(("docker daemon", ok, detail))

    if ok:
        ok2, detail2 = _check_compose_v2()
        rows.append(("docker compose v2", ok2, detail2))

    ok3, detail3 = _check_claude_dir()
    rows.append(("~/.claude directory", ok3, detail3))

    # Proceed with skill installs even if deps failed — users should see the
    # full state in one shot, not dep errors only.
    for name in MANAGED_SKILLS:
        sok, sdetail = _install_skill(name, force=force)
        rows.append((f"skill: {name}", sok, sdetail))

    any_failed = False
    for label, row_ok, detail in rows:
        icon = "[green]✓[/green]" if row_ok else "[red]✗[/red]"
        console.print(f"{icon} {label}: {detail}")
        if not row_ok:
            any_failed = True

    if any_failed:
        console.print("\n[red]fortify incomplete.[/red] Fix the failed checks and re-run.")
        sys.exit(1)
    console.print(f"\n[green]fortify complete.[/green] CLI v{__version__}.")
