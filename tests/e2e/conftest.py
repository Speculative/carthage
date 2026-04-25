"""Shared fixtures for E2E tests.

These tests build a local `carthage-base:e2e` image once per test session
(expensive: ~2 min on a cold cache), then copy fixture projects into tempdirs
and run real `carthage up` / `carthage attach` flows against them.

Gated behind `pytest -m e2e` so casual `pytest` doesn't attempt docker.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = REPO_ROOT / "base"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Tag we use for the locally-built base image during E2E. Pinning a distinct
# tag means we don't clobber any manually-pulled ghcr.io/speculative/carthage-base.
E2E_BASE_TAG = "carthage-base:e2e"


def _docker_available() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True)
        return r.returncode == 0
    except FileNotFoundError:
        return False


@pytest.fixture(scope="session", autouse=True)
def _require_docker() -> None:
    if not _docker_available():
        pytest.skip("docker daemon not reachable; skipping e2e tests")


@pytest.fixture(scope="session")
def base_image() -> str:
    """Build `carthage-base:e2e` once per session and return the tag."""
    cmd = [
        "docker", "build",
        "-t", E2E_BASE_TAG,
        "--build-arg", f"HOST_UID={os.getuid()}",
        "--build-arg", f"HOST_GID={os.getgid()}",
        str(BASE_DIR),
    ]
    print(f"\n[e2e] building {E2E_BASE_TAG}…")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        pytest.fail(f"failed to build base image: exit {r.returncode}")
    return E2E_BASE_TAG


@pytest.fixture
def project_dir(tmp_path: Path, request) -> Path:
    """Copy a fixture project into a tempdir and yield the path.

    The test parameterizes via `pytest.mark.project_fixture('<name>')`.
    """
    marker = request.node.get_closest_marker("project_fixture")
    if marker is None:
        pytest.fail("test is missing @pytest.mark.project_fixture(<name>)")
    name = marker.args[0]
    src = FIXTURES_DIR / name
    if not src.is_dir():
        pytest.fail(f"fixture project {src} does not exist")

    # Unique dest so multiple tests can run in parallel without name clashes.
    dest = tmp_path / f"{name}-{uuid.uuid4().hex[:8]}"
    shutil.copytree(src, dest)
    return dest


@pytest.fixture
def compose_project_name(project_dir: Path) -> str:
    """The compose project name carthage would use (must match compose.py)."""
    # Read slug from the fixture's config.toml.
    import tomllib
    cfg = tomllib.loads((project_dir / ".carthage" / "config.toml").read_text())
    return f"carthage-{cfg['carthage']['project_slug']}"


@pytest.fixture
def docker_cleanup(compose_project_name: str, project_dir: Path):
    """Yield, then tear down any compose resources and images the test created."""
    yield
    compose_file = project_dir / ".carthage" / "docker-compose.yaml"
    if compose_file.exists():
        subprocess.run(
            [
                "docker", "compose",
                "-f", str(compose_file),
                "-p", compose_project_name,
                "down", "-v", "--rmi", "local",
            ],
            capture_output=True,
        )
    # Belt-and-braces: also remove any carthage-<slug>:* images
    import tomllib
    try:
        cfg = tomllib.loads((project_dir / ".carthage" / "config.toml").read_text())
        slug = cfg["carthage"]["project_slug"]
        r = subprocess.run(
            ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}", f"carthage-{slug}"],
            capture_output=True, text=True,
        )
        tags = [t for t in r.stdout.splitlines() if t]
        if tags:
            subprocess.run(["docker", "rmi", "-f", *tags], capture_output=True)
    except (FileNotFoundError, KeyError):
        pass


def run_carthage(*args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Invoke the `carthage` CLI via `python -m carthage` from the source tree.

    We use the in-tree module rather than `shutil.which('carthage')` because E2E
    runs out of the dev checkout, where the package may not be `uv tool install`-ed.
    """
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    # Make sure PYTHONPATH lets us import the CLI package from source.
    existing = full_env.get("PYTHONPATH", "")
    full_env["PYTHONPATH"] = f"{REPO_ROOT}{os.pathsep}{existing}" if existing else str(REPO_ROOT)
    return subprocess.run(
        ["python3", "-m", "carthage", *args],
        cwd=str(cwd),
        env=full_env,
        capture_output=True,
        text=True,
    )
