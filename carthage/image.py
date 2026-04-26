"""Image staleness check.

A project's Dockerfile inherits from `carthage-base:vN`. We tag each locally-
built project image as `carthage-<slug>:<hash>`, where the hash covers:
  - the content of `.carthage/Dockerfile`
  - the content of any local files COPY'd in by the Dockerfile
  - the digest of the pinned `carthage-base` tag

`carthage up` computes the expected hash, checks if `docker image inspect`
finds it, and rebuilds if not. This is deliberately conservative — we'd rather
spend 5 seconds hashing than ship a stale image.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from carthage.config import CarthageConfig


# Minimal Dockerfile parsing — enough to identify local files referenced in
# COPY/ADD lines. We intentionally do NOT try to fully parse Dockerfile syntax
# (heredocs, --chmod flags, etc.); if you need something we don't handle, you
# can force a rebuild with `carthage build --no-cache`.
_COPY_RE = re.compile(r"^\s*(?:COPY|ADD)\s+(.+)$", re.IGNORECASE)
# Flags like --from=..., --chown=..., --chmod=..., --link
_FLAG_RE = re.compile(r"^--[a-zA-Z][\w-]*(?:=\S*)?$")


@dataclass(frozen=True)
class ImageHashInputs:
    dockerfile_bytes: bytes
    copied_files: dict[str, bytes]  # project-relative path -> contents
    base_image_digest: str

    def hash(self) -> str:
        h = hashlib.sha256()
        h.update(b"dockerfile\0")
        h.update(self.dockerfile_bytes)
        h.update(b"\0base\0")
        h.update(self.base_image_digest.encode())
        for path in sorted(self.copied_files):
            h.update(b"\0copy\0")
            h.update(path.encode())
            h.update(b"\0")
            h.update(self.copied_files[path])
        return h.hexdigest()[:16]


def parse_copied_sources(dockerfile_text: str) -> list[str]:
    """Return the list of source paths referenced by COPY/ADD directives.

    We skip:
      - COPY --from=<stage> ... (those reference prior build stages, not local files)
      - http(s):// sources in ADD (those don't live in the build context)
      - the final token, which is the destination, not a source
    """
    sources: list[str] = []
    # Collapse line continuations (\\ at EOL) so multi-line COPY works.
    joined = re.sub(r"\\\n", " ", dockerfile_text)
    for line in joined.splitlines():
        m = _COPY_RE.match(line)
        if not m:
            continue
        tokens = shlex.split(m.group(1), posix=True)
        # Strip leading flags. If we hit --from=, skip the entire directive —
        # it's referencing a build stage, not the local context.
        has_from = False
        filtered: list[str] = []
        for tok in tokens:
            if _FLAG_RE.match(tok):
                if tok.startswith("--from="):
                    has_from = True
                continue
            filtered.append(tok)
        if has_from or len(filtered) < 2:
            continue
        # Last token is the destination; everything before is a source.
        for src in filtered[:-1]:
            if src.startswith(("http://", "https://")):
                continue
            sources.append(src)
    return sources


def _read_file_or_empty(path: Path) -> bytes:
    """Read a file if it exists. Return empty bytes if not — absent files still
    contribute to the hash via their path, which is enough to detect renames."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""
    except OSError:
        # Directories, device files, permission errors — treat as "content unknown"
        # but still record their path. A glob source (e.g. `COPY . /workspace`)
        # will land here; we'll catch changes to `.carthage/Dockerfile` via the
        # dockerfile bytes themselves, which is the common case.
        return b""


def compute_expected_hash(cfg: CarthageConfig) -> str:
    """Compute the hash we'd expect `carthage-<slug>:<hash>` to carry."""
    dockerfile_bytes = cfg.dockerfile.read_bytes()
    dockerfile_text = dockerfile_bytes.decode("utf-8", errors="replace")

    copied: dict[str, bytes] = {}
    for src in parse_copied_sources(dockerfile_text):
        # Build context is the project root (relative to .carthage/docker-compose.yaml,
        # build.context is `..`). So COPY paths are relative to project_root.
        src_path = (cfg.project_root / src).resolve()
        try:
            src_path.relative_to(cfg.project_root.resolve())
        except ValueError:
            # Outside the project root — skip (Docker would reject anyway).
            continue
        # Glob patterns and directories: fall back to path-only tracking.
        if any(ch in src for ch in "*?[]") or src_path.is_dir():
            copied[src] = b""
            continue
        copied[src] = _read_file_or_empty(src_path)

    base_digest = get_base_image_digest(cfg.base_image)

    return ImageHashInputs(
        dockerfile_bytes=dockerfile_bytes,
        copied_files=copied,
        base_image_digest=base_digest,
    ).hash()


def pull_base_image(image_ref: str) -> tuple[bool, str]:
    """Best-effort `docker pull` for the base image.

    Returns (ok, detail). ok=False on network/registry errors — callers should
    soft-fail (fall through to the local cache) rather than abort, so users
    without network can still bring containers up. The pull is not just a
    convenience: `compute_expected_hash` mixes the base image's repo digest
    into the hash, and reads it from the *local* cache. Without this pull,
    drift in the upstream `:vN` tag is invisible to the staleness check.
    """
    try:
        r = subprocess.run(
            ["docker", "pull", "--quiet", image_ref],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return False, "docker not on PATH"
    if r.returncode != 0:
        tail = (r.stderr.strip() or r.stdout.strip()).splitlines()
        return False, tail[-1] if tail else f"exit {r.returncode}"
    return True, image_ref


def get_base_image_digest(image_ref: str) -> str:
    """Return a stable identifier for the base image.

    Preference order:
      1. RepoDigests (e.g. `ghcr.io/…@sha256:…`) — survives retagging.
      2. Image ID (`sha256:…`) — changes on rebuild but stable across inspects.
      3. The image reference string itself — fallback if the image isn't pulled
         yet. The first `carthage up` will pull, and the *next* hash check will
         have real data.
    """
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image_ref],
            capture_output=True,
            check=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return f"unpulled:{image_ref}"

    try:
        inspected = json.loads(result.stdout)
        if inspected:
            repo_digests = inspected[0].get("RepoDigests") or []
            if repo_digests:
                return repo_digests[0]
            image_id = inspected[0].get("Id")
            if image_id:
                return image_id
    except (json.JSONDecodeError, KeyError, IndexError):
        pass
    return f"unpulled:{image_ref}"


def local_image_exists(repo: str, tag: str) -> bool:
    try:
        subprocess.run(
            ["docker", "image", "inspect", f"{repo}:{tag}"],
            capture_output=True,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def read_last_build_hash(cfg: CarthageConfig) -> str | None:
    try:
        return cfg.last_build_hash_file.read_text().strip() or None
    except FileNotFoundError:
        return None


def write_last_build_hash(cfg: CarthageConfig, h: str) -> None:
    cfg.last_build_hash_file.write_text(h + "\n")
