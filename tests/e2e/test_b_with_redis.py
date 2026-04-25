"""E2E Test B — app + Redis.

Verifies:
  - From inside `dev`, `redis-cli -h redis ping` returns PONG (inter-service
    networking over the dedicated compose network).
  - Redis is NOT reachable on the host (sidecars default to internal-only;
    no `ports:` entry is generated unless the user explicitly opts in).
"""

from __future__ import annotations

import socket
import subprocess
import time

import pytest

from tests.e2e.conftest import run_carthage


pytestmark = pytest.mark.e2e


@pytest.mark.project_fixture("py-with-redis")
def test_inter_service_and_host_not_exposed(
    base_image, project_dir, compose_project_name, docker_cleanup
):
    r = run_carthage("up", cwd=project_dir)
    assert r.returncode == 0, f"carthage up failed: {r.stderr}"

    compose_file = project_dir / ".carthage" / "docker-compose.yaml"

    def in_dev(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", "compose",
             "-f", str(compose_file), "-p", compose_project_name,
             "exec", "-T", "dev", *argv],
            capture_output=True, text=True,
        )

    # Inter-service: redis-cli -h redis ping returns PONG.
    deadline = time.time() + 15
    last = ""
    while time.time() < deadline:
        r = in_dev("redis-cli", "-h", "redis", "ping")
        last = (r.stdout + r.stderr).strip()
        if r.returncode == 0 and "PONG" in r.stdout:
            break
        time.sleep(0.5)
    else:
        pytest.fail(f"inter-service redis ping failed: {last}")

    # Host-side: redis 6379 should NOT be bound. Probe a TCP connect —
    # if something answers on 127.0.0.1:6379 we've leaked the port.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        result = s.connect_ex(("127.0.0.1", 6379))
    finally:
        s.close()
    assert result != 0, (
        "redis is reachable on host port 6379 — it should not be "
        "(compose has no `ports:` block for the redis service)"
    )
