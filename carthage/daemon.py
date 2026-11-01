"""Identity mapping for rootful and rootless Docker contexts."""
from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache


@lru_cache(maxsize=1)
def docker_security_options() -> tuple[str, ...]:
    try:
        result = subprocess.run(
            ['docker', 'info', '--format', '{{json .SecurityOptions}}'],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return ()
        return tuple(json.loads(result.stdout) or ())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ()


def runtime_ids() -> tuple[int, int]:
    # In rootless Docker, namespace UID/GID 0 maps to the invoking host user.
    # Keeping the named carthage user at that ID preserves ordinary bind mounts
    # without broadening permissions or changing ownership of host files.
    if any('rootless' in item for item in docker_security_options()):
        return 0, 0
    return os.getuid(), os.getgid()
