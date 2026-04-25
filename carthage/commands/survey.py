"""`carthage survey` — deep diagnostic, including version-alignment checks.

Split from `status`: status is for "is the current project running?"
(fast, runs all the time), survey is for "is my install healthy?" (slow,
runs occasionally — pulls images, may spin up test containers).

Survey reports; it never fixes. The user runs `carthage fortify` if skills
drift, `/carthage-migrate` if config schemas drift, edits the Dockerfile
themselves if the base image major moved.

Checks performed:
  - docker CLI + daemon reachable
  - `docker compose` v2 plugin works
  - host ~/.claude exists and is readable
  - host UID/GID sensible (not root)
  - installed skill versions match the CLI version
  - (if inside a project) base image pullable
  - (if inside a project) config schema is readable by this CLI
  - (if inside a project) base image major in Dockerfile still supported
  - (if --deep) spins up a test container, probes Claude auth
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import click
from rich.console import Console

from carthage import (
    BASE_IMAGE_REPO,
    CURRENT_CONFIG_SCHEMA,
    EXPECTED_BASE_IMAGE_TAG,
    __version__,
)
from carthage.config import ConfigError, load_config
from carthage.skills import MANAGED_SKILLS, read_skill_version

console = Console()


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


def _check(name: str) -> callable:
    def wrap(fn):
        def inner(*a, **kw) -> CheckResult:
            try:
                ok, detail = fn(*a, **kw)
            except Exception as exc:  # noqa: BLE001
                return CheckResult(name, False, f"exception: {exc}")
            return CheckResult(name, ok, detail)
        return inner
    return wrap


@_check("docker CLI on PATH")
def check_docker_present() -> tuple[bool, str]:
    path = shutil.which("docker")
    return (path is not None), path or "not found"


@_check("docker daemon reachable")
def check_docker_daemon() -> tuple[bool, str]:
    r = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if r.returncode != 0:
        tail = r.stderr.strip().splitlines()
        return False, tail[-1] if tail else "docker info failed"
    return True, "ok"


@_check("docker compose v2 plugin")
def check_compose_v2() -> tuple[bool, str]:
    r = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
    if r.returncode != 0:
        return False, "docker compose v2 plugin not available"
    return True, r.stdout.strip().splitlines()[0] if r.stdout.strip() else "ok"


@_check("host ~/.claude exists and is readable")
def check_claude_dir() -> tuple[bool, str]:
    p = Path.home() / ".claude"
    if not p.exists():
        return False, f"{p} does not exist. Run `claude` on the host once to authenticate."
    if not os.access(p, os.R_OK):
        return False, f"{p} exists but is not readable"
    return True, str(p)


@_check("host UID/GID are sensible")
def check_uid_gid() -> tuple[bool, str]:
    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        return False, "you are running as root (UID 0). Carthage expects a regular user."
    return True, f"uid={uid} gid={gid}"


@_check("installed skills match CLI version")
def check_skill_versions() -> tuple[bool, str]:
    mismatches: list[str] = []
    missing: list[str] = []
    matched: list[str] = []
    for name in MANAGED_SKILLS:
        ver = read_skill_version(name)
        if ver is None:
            # Could be missing *or* present without a version field.
            from carthage.skills import skill_path
            if skill_path(name).is_file():
                mismatches.append(f"{name}@unknown")
            else:
                missing.append(name)
        elif ver != __version__:
            mismatches.append(f"{name}@{ver}")
        else:
            matched.append(f"{name}@{ver}")
    if missing or mismatches:
        parts = []
        if matched:
            parts.append("matched: " + ", ".join(matched))
        if mismatches:
            parts.append(f"drift: {', '.join(mismatches)} (CLI is {__version__})")
        if missing:
            parts.append("missing: " + ", ".join(missing))
        return False, "; ".join(parts) + " — run `carthage fortify`"
    return True, ", ".join(matched) if matched else "no managed skills — run `carthage fortify`"


@_check("base image pullable")
def check_base_image_pullable(image_ref: str) -> tuple[bool, str]:
    r = subprocess.run(
        ["docker", "pull", image_ref], capture_output=True, text=True,
    )
    if r.returncode != 0:
        inspect = subprocess.run(
            ["docker", "image", "inspect", image_ref],
            capture_output=True, text=True,
        )
        if inspect.returncode == 0:
            return True, f"pull failed but local cache has {image_ref}"
        tail = r.stderr.strip().splitlines()
        return False, tail[-1] if tail else "pull failed and no local cache"
    return True, image_ref


@_check("project config schema readable")
def check_config_schema(cfg) -> tuple[bool, str]:
    if cfg.schema_is_outdated:
        return True, (
            f"schema '{cfg.version}' is older than current '{CURRENT_CONFIG_SCHEMA}' "
            "but still readable. Run `/carthage-migrate` to upgrade."
        )
    return True, f"schema '{cfg.version}' (current)"


@_check("project Dockerfile FROM tag supported")
def check_dockerfile_base(cfg) -> tuple[bool, str]:
    """Read the `FROM` line and see if it matches cfg.base_image_tag."""
    try:
        dockerfile = cfg.dockerfile.read_text()
    except FileNotFoundError:
        return False, f"{cfg.dockerfile} is missing"
    m = re.search(r"^\s*FROM\s+(\S+)", dockerfile, re.MULTILINE)
    if not m:
        return False, "no FROM line found"
    from_ref = m.group(1)
    # Accept either the exact configured ref or any tag on the same repo
    # whose major matches base_image_tag.
    if from_ref == cfg.base_image:
        return True, from_ref
    if from_ref.startswith(BASE_IMAGE_REPO + ":"):
        tag = from_ref.split(":", 1)[1]
        major = re.match(r"^v\d+", tag)
        if major and major.group(0) == cfg.base_image_tag:
            return True, f"{from_ref} (major matches {cfg.base_image_tag})"
        return False, (
            f"Dockerfile FROM {from_ref!r} disagrees with config "
            f"base_image_tag={cfg.base_image_tag!r}"
        )
    # Custom / fork ref — we can't verify, but flag softly.
    return True, f"{from_ref} (non-standard; skipped check)"


@_check("claude CLI auth (deep)")
def check_claude_auth_deep(image_ref: str) -> tuple[bool, str]:
    home = Path.home()
    r = subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{home}/.claude:/home/carthage/.claude",
            "-e", "CARTHAGE=1",
            image_ref,
            "timeout", "30", "claude", "--print", "reply with exactly: OK",
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        tail = (r.stderr.strip() or r.stdout.strip()).splitlines()
        return False, tail[-1][:300] if tail else f"exit {r.returncode}"
    if not r.stdout.strip():
        return False, "claude returned empty — likely not authenticated"
    return True, f"claude responded ({len(r.stdout.strip())} chars)"


def _default_base_image() -> str:
    return f"{BASE_IMAGE_REPO}:{EXPECTED_BASE_IMAGE_TAG}"


@click.command()
@click.option("--deep", is_flag=True, help="Pull the base image and probe Claude auth.")
@click.option("--base-image", default=None, help="Override the base image ref.")
def survey(deep: bool, base_image: str | None) -> None:
    """Deep diagnostic: tool versions, skill alignment, (optional) live probes."""
    # Try to pick up project config if we're inside one.
    cfg = None
    try:
        cfg = load_config()
    except ConfigError:
        pass

    if base_image is None:
        base_image = cfg.base_image if cfg else _default_base_image()

    results: list[CheckResult] = [
        check_docker_present(),
        check_docker_daemon(),
    ]
    docker_up = results[0].ok and results[1].ok
    if docker_up:
        results.append(check_compose_v2())
    results.append(check_claude_dir())
    results.append(check_uid_gid())
    results.append(check_skill_versions())

    if cfg is not None:
        results.append(check_config_schema(cfg))
        results.append(check_dockerfile_base(cfg))

    if docker_up:
        results.append(check_base_image_pullable(base_image))
        if deep and results[-1].ok:
            results.append(check_claude_auth_deep(base_image))
        elif deep:
            console.print(
                "[yellow]skipping deep claude-auth check — base image not available.[/yellow]"
            )

    any_failed = False
    for r in results:
        icon = "[green]✓[/green]" if r.ok else "[red]✗[/red]"
        console.print(f"{icon} {r.name}: {r.detail}")
        if not r.ok:
            any_failed = True

    console.print(f"\n[dim]CLI version: {__version__}[/dim]")
    if any_failed:
        console.print("[red]one or more checks failed.[/red]")
        sys.exit(1)
    console.print("[green]all checks passed.[/green]")
