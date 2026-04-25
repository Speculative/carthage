"""Thin wrapper around `docker compose` that always targets the project's
`.carthage/docker-compose.yaml`.

Rationale: users shouldn't need to remember `-f .carthage/docker-compose.yaml`
every time. The CLI injects it, and also sets a project name so containers
and networks get a predictable prefix we can show in `carthage status`.

`carthage up` sometimes needs to splice in an override compose file (e.g. to
strip `ports:` entries for `--no-host-ports`). It does that by passing an
argv that starts with the sentinel `--extra-f-sequence`, followed by a
replacement `-f ... -f ...` block. When we see that sentinel we skip our
default `-f` injection. Keeps the normal-path code short.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

from carthage.config import CarthageConfig


_EXTRA_F_SEQUENCE_SENTINEL = "--extra-f-sequence"


def _docker_compose_base(cfg: CarthageConfig, args: Sequence[str]) -> tuple[list[str], list[str]]:
    """Return (base cmd, remaining args) — splitting off any `--extra-f-sequence`."""
    if args and args[0] == _EXTRA_F_SEQUENCE_SENTINEL:
        # Caller is providing its own `-f` block. Use it in place of ours.
        # args looks like: ["--extra-f-sequence", "-f", ".carthage/docker-compose.yaml", "-f", "<override>.yaml", "up", ...]
        # Find where the `-f` block ends (first non -f / non path arg).
        i = 1
        f_block: list[str] = []
        while i < len(args) and args[i] == "-f":
            f_block.append(args[i])
            if i + 1 < len(args):
                f_block.append(args[i + 1])
                i += 2
            else:
                break
        remaining = list(args[i:])
        return (
            ["docker", "compose", *f_block, "-p", cfg.compose_project_name],
            remaining,
        )
    return (
        ["docker", "compose", "-f", str(cfg.compose_file), "-p", cfg.compose_project_name],
        list(args),
    )


def compose_env(cfg: CarthageConfig) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("HOST_UID", str(os.getuid()))
    env.setdefault("HOST_GID", str(os.getgid()))
    env.setdefault("HOST_HOME", str(Path.home()))
    return env


def run(
    cfg: CarthageConfig,
    args: Sequence[str],
    *,
    check: bool = True,
    capture: bool = False,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    base, remaining = _docker_compose_base(cfg, args)
    cmd = base + remaining
    env = compose_env(cfg)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        cmd,
        check=check,
        env=env,
        capture_output=capture,
        text=capture,
    )


def exec_interactive(
    cfg: CarthageConfig,
    service: str,
    command: Sequence[str],
    *,
    env_overrides: dict[str, str] | None = None,
) -> int:
    """`docker compose exec -it <service> <command>`. Hands the terminal to
    the container (tmux wants a real tty)."""
    base, _ = _docker_compose_base(cfg, [])
    cmd = base + ["exec", service, *command]
    env = compose_env(cfg)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.call(cmd, env=env)


def host_mem_limit_bytes() -> int | None:
    """~75% of host RAM. Linux-only (reads /proc/meminfo); macOS returns None,
    and the compose template treats the absent env var as "no limit" (`0`)."""
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return int(kb * 1024 * 0.75)
    except (OSError, ValueError):
        return None
    return None
