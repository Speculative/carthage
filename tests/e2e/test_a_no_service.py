"""E2E Test A — no-service Python web server.

Verifies:
  - `carthage up` lands in a running container with the source mount and
    tmux 'claude' session in place.
  - Host-side `curl http://localhost:18000` reaches the container's server
    via the published port.
  - Hardening spot-check: no sudo, NoNewPrivs=1, no docker socket, narrow caps.

We do NOT test host-LAN isolation; Carthage doesn't claim that property.
"""

from __future__ import annotations

import subprocess
import time

import pytest

from tests.e2e.conftest import run_carthage


pytestmark = pytest.mark.e2e


@pytest.mark.project_fixture("py-http-server")
def test_up_attach_and_host_reachable(
    base_image, project_dir, compose_project_name, docker_cleanup
):
    r = run_carthage("up", cwd=project_dir)
    assert r.returncode == 0, f"carthage up failed:\nstdout={r.stdout}\nstderr={r.stderr}"

    ps = subprocess.run(
        ["docker", "compose",
         "-f", str(project_dir / ".carthage" / "docker-compose.yaml"),
         "-p", compose_project_name,
         "ps", "-q", "dev"],
        capture_output=True, text=True, check=True,
    )
    container_id = ps.stdout.strip()
    assert container_id, "dev container not running after `carthage up`"

    r = subprocess.run(
        ["docker", "exec", container_id, "ls", "/workspace/app.py"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"source mount missing: {r.stderr}"

    # tmux session exists (entrypoint created it).
    r = subprocess.run(
        ["docker", "exec", container_id, "tmux", "has-session", "-t", "claude"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"tmux session 'claude' missing: {r.stderr}"

    # Start the python server inside the container in the background.
    subprocess.run(
        ["docker", "exec", "-d", container_id,
         "bash", "-c",
         "cd /workspace && python3 app.py > /tmp/app.log 2>&1"],
        check=True,
    )

    # Host-side curl should succeed.
    deadline = time.time() + 20
    last_err = None
    while time.time() < deadline:
        curl = subprocess.run(
            ["curl", "-sS", "--fail", "http://127.0.0.1:18000/"],
            capture_output=True, text=True,
        )
        if curl.returncode == 0:
            break
        last_err = curl.stderr
        time.sleep(0.5)
    else:
        pytest.fail(f"host curl to published port failed: {last_err}")


@pytest.mark.project_fixture("py-http-server")
def test_sandbox_invariants(base_image, project_dir, compose_project_name, docker_cleanup):
    """Hardening spot-check for the things SPEC_CHANGES still enforces:
      - no passwordless sudo (and sudo typically not installed at all)
      - NoNewPrivs = 1
      - /var/run/docker.sock not mounted
      - narrow cap set: NET_ADMIN and SYS_ADMIN absent; basics present
    """
    r = run_carthage("up", cwd=project_dir)
    assert r.returncode == 0, f"carthage up failed: {r.stderr}"

    compose_file = project_dir / ".carthage" / "docker-compose.yaml"

    def in_container(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", "compose",
             "-f", str(compose_file), "-p", compose_project_name,
             "exec", "-T", "dev", *argv],
            capture_output=True, text=True,
        )

    # sudo: absent or not passwordless.
    r = in_container("sh", "-c", "command -v sudo || echo absent")
    if "absent" not in r.stdout:
        r = in_container("sudo", "-n", "true")
        assert r.returncode != 0, "passwordless sudo is available — should not be"

    # NoNewPrivs = 1
    r = in_container("sh", "-c", "grep '^NoNewPrivs' /proc/self/status")
    assert r.returncode == 0 and "1" in r.stdout, f"NoNewPrivs: {r.stdout!r}"

    # Docker socket not mounted
    r = in_container("ls", "/var/run/docker.sock")
    assert r.returncode != 0, "/var/run/docker.sock is mounted — it must not be"

    # Cap set: NET_ADMIN and SYS_ADMIN both absent from the bounding set,
    # CHOWN present. We check the `bounding set` line specifically — capsh's
    # `iab` line lists denied caps with a `!` prefix and would false-positive
    # any naive substring check.
    r = in_container("capsh", "--print")
    bounding_line = next(
        (line for line in r.stdout.splitlines() if "bounding set" in line.lower()),
        "",
    ).lower()
    assert bounding_line, f"no 'bounding set' line in capsh output: {r.stdout}"
    assert "cap_sys_admin" not in bounding_line, f"SYS_ADMIN present: {bounding_line}"
    assert "cap_net_admin" not in bounding_line, (
        f"NET_ADMIN present — SPEC_CHANGES explicitly drops it. {bounding_line}"
    )
    assert "cap_chown" in bounding_line, f"CHOWN missing: {bounding_line}"
