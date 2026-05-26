"""`carthage fortify` — one-time host setup.

Does dependency checks, then installs personal Carthage skills into
`~/.carthage/skills/` at the version matching the installed CLI and links
configured agents to that canonical copy. Idempotent — re-run after
`uv tool upgrade carthage-cli` to pull in matching skill updates.

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

from carthage import BASE_IMAGE_REPO, EXPECTED_BASE_IMAGE_TAG, __version__
from carthage import image as base_image
from carthage.agents import (
    AgentConfigError,
    AgentProfile,
    agent_profiles_for,
    require_agent_profile,
    supported_agent_kinds,
)
from carthage.commands.agent import _set_agents_table
from carthage.personal_config import describe_personal_config, load_personal_config
from carthage.personal_image import (
    build_personal_image,
    personal_image_is_current,
    personal_image_ref,
)
from carthage.skills import (
    MANAGED_SKILLS,
    SKILLS_DIR,
    agent_skill_path,
    read_skill_version,
)

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
            "before using Carthage with Claude."
        )
    return True, str(p)


def _check_codex_dir() -> tuple[bool, str]:
    p = Path.home() / ".codex"
    if not p.is_dir():
        return False, f"{p} does not exist. Run `codex --login` on the host first."
    return True, str(p)


def _check_agent_auth(profile: AgentProfile) -> tuple[bool, str]:
    if profile.kind == "claude":
        return _check_claude_dir()
    if profile.kind == "codex":
        return _check_codex_dir()
    return True, "no host auth check defined"


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
    """Install (or refresh) the named skill at ~/.carthage/skills/<name>/.

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


def _link_skill_for_agent(profile: AgentProfile, name: str) -> tuple[bool, str]:
    """Expose a canonical Carthage skill through an agent discovery path."""
    if profile.managed_skill_dir is None:
        return True, "agent has no managed skill directory"

    source = SKILLS_DIR / name
    target = agent_skill_path(profile.managed_skill_dir, name)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() and target.resolve() == source.resolve():
            return True, f"linked {target}"
        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        target.symlink_to(source, target_is_directory=True)
    except OSError as exc:
        return False, f"failed to link {target} -> {source}: {exc}"
    return True, f"linked {target}"


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

    personal = load_personal_config()
    rows.append(("personal config", True, describe_personal_config(personal)))
    try:
        require_agent_profile(personal.config.agents.default)
        agent_config_ok = True
    except AgentConfigError:
        kinds = supported_agent_kinds()
        console.print(
            "\n[yellow]No default agent configured.[/yellow] "
            "Which agent would you like to use?\n"
            + "\n".join(f"  {i + 1}. {k}" for i, k in enumerate(kinds))
        )
        choice = click.prompt(
            "Agent",
            type=click.Choice(list(kinds), case_sensitive=False),
            default=kinds[0],
        )
        path = personal.path
        try:
            text = path.read_text() if path.exists() else ""
            path.parent.mkdir(parents=True, exist_ok=True)
            installed = list(personal.config.agents.installed)
            if choice not in installed:
                installed.insert(0, choice)
            path.write_text(_set_agents_table(text, installed=installed, default=choice))
        except OSError as exc:
            rows.append(("default agent", False, f"failed to write config: {exc}"))
            agent_config_ok = False
        else:
            personal = load_personal_config()
            rows.append(("default agent", True, f"configured: {choice}"))
            agent_config_ok = True

    if agent_config_ok:
        for profile in agent_profiles_for(personal.config.agents.installed):
            aok, adetail = _check_agent_auth(profile)
            rows.append((f"{profile.kind} auth", aok, adetail))

    if ok and agent_config_ok:
        source_image = f"{BASE_IMAGE_REPO}:{EXPECTED_BASE_IMAGE_TAG}"
        target_image = personal_image_ref(EXPECTED_BASE_IMAGE_TAG)
        with console.status(f"pulling {source_image}…"):
            pull_ok, pull_detail = base_image.pull_base_image(source_image)
        if not pull_ok:
            rows.append(("base image refresh", False, pull_detail))
        else:
            rows.append(("base image refresh", True, pull_detail))
            current, current_detail = personal_image_is_current(
                image_ref=target_image,
                base_image=source_image,
                config=personal.config,
            )
            if current:
                rows.append(("personal image", True, current_detail))
            else:
                with console.status("building personal image…"):
                    build_ok, build_detail = build_personal_image(
                        base_image=source_image,
                        target_image=target_image,
                        config=personal.config,
                    )
                rows.append(("personal image", build_ok, build_detail))

    # Proceed with skill installs even if deps failed — users should see the
    # full state in one shot, not dep errors only.
    skill_agents = tuple(
        profile
        for profile in agent_profiles_for(personal.config.agents.installed)
        if profile.managed_skill_dir is not None
    )
    if skill_agents:
        for name in MANAGED_SKILLS:
            sok, sdetail = _install_skill(name, force=force)
            rows.append((f"skill: {name}", sok, sdetail))
            if sok:
                for profile in skill_agents:
                    lok, ldetail = _link_skill_for_agent(profile, name)
                    rows.append((f"skill link: {profile.kind}/{name}", lok, ldetail))

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
