"""E2E Test C — bash history persists across `carthage down` / `up`.

The base image points HISTFILE at /commandhistory/.bash_history, and the
CLI bind-mounts ~/.carthage/state/<slug>/ over /commandhistory at `up`
time. So any line we append in one container instance must show up in
the *next* container instance's history file.

Verifies:
  - `carthage up` lands in a container with /commandhistory writable as
    the carthage user.
  - Writing to .bash_history in container A is visible after a full
    down/up cycle in container B.
  - The host-side file lives where we documented (~/.carthage/state/<slug>/).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.e2e.conftest import run_carthage


pytestmark = pytest.mark.e2e


@pytest.mark.project_fixture("py-http-server")
def test_bash_history_persists(
    base_image, project_dir, compose_project_name, docker_cleanup
):
    # First up: write a sentinel to the in-container .bash_history.
    r = run_carthage("up", cwd=project_dir)
    assert r.returncode == 0, f"first `carthage up` failed: {r.stderr}"

    compose_file = project_dir / ".carthage" / "docker-compose.yaml"

    def in_container(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", "compose",
             "-f", str(compose_file), "-p", compose_project_name,
             "exec", "-T", "dev", *argv],
            capture_output=True, text=True,
        )

    sentinel = "carthage-history-sentinel-CXuZ2v"
    r = in_container(
        "bash", "-c",
        f"echo {sentinel} >> /commandhistory/.bash_history",
    )
    assert r.returncode == 0, f"writing sentinel failed: {r.stderr}"

    # Confirm it's actually on the host side immediately (the bind mount
    # should make it visible without the container being torn down).
    host_file = Path.home() / ".carthage" / "state" / "e2e-py-http-server" / ".bash_history"
    assert host_file.exists(), f"host history file not created at {host_file}"
    assert sentinel in host_file.read_text(), (
        f"sentinel not in host file after in-container append: {host_file.read_text()!r}"
    )

    # Tear down the container fully (down -v removes anonymous volumes too).
    r = subprocess.run(
        ["docker", "compose",
         "-f", str(compose_file), "-p", compose_project_name,
         "down"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"`compose down` failed: {r.stderr}"

    # Bring it back up.
    r = run_carthage("up", cwd=project_dir)
    assert r.returncode == 0, f"second `carthage up` failed: {r.stderr}"

    # The new container should see the sentinel in its history file.
    r = in_container("cat", "/commandhistory/.bash_history")
    assert r.returncode == 0, f"could not read history in fresh container: {r.stderr}"
    assert sentinel in r.stdout, (
        f"sentinel did not survive down/up — history is: {r.stdout!r}"
    )


@pytest.mark.project_fixture("py-http-server")
def test_commandhistory_writable_as_carthage_user(
    base_image, project_dir, compose_project_name, docker_cleanup
):
    """If docker auto-creates the mount path because the host dir is missing,
    it ends up root-owned and the carthage user can't write to it. The CLI
    is supposed to mkdir -p the host dir before up; this test fails loudly
    if that gets dropped."""
    r = run_carthage("up", cwd=project_dir)
    assert r.returncode == 0, f"`carthage up` failed: {r.stderr}"

    compose_file = project_dir / ".carthage" / "docker-compose.yaml"
    r = subprocess.run(
        ["docker", "compose",
         "-f", str(compose_file), "-p", compose_project_name,
         "exec", "-T", "dev",
         "bash", "-c", "touch /commandhistory/.bash_history && stat -c '%U' /commandhistory"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"touch in /commandhistory failed: {r.stderr}"
    assert r.stdout.strip() == "carthage", (
        f"/commandhistory not owned by carthage user: {r.stdout!r}"
    )
