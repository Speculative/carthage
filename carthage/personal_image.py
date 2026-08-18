"""Build the local personal base image used by project images."""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import tempfile
import os
from pathlib import Path

from carthage import EXPECTED_BASE_IMAGE_TAG, PERSONAL_BASE_IMAGE_REPO, __version__
from carthage.agents import agent_profiles_for
from carthage import image
from carthage.personal_config import PersonalConfig, PersonalImageFile


PERSONAL_IMAGE_HASH_LABEL = "org.carthage.personal-image-hash"


class PersonalImageRequiredError(RuntimeError):
    """Project Dockerfile does not inherit from the required personal image."""


def personal_image_ref(tag: str = EXPECTED_BASE_IMAGE_TAG) -> str:
    # Test hook: e2e tests isolate personal images from the user's real
    # carthage-base-personal tag and clean up the generated repo afterward.
    repo = os.environ.get("CARTHAGE_PERSONAL_IMAGE_REPO", PERSONAL_BASE_IMAGE_REPO)
    return f"{repo}:{tag}"


def require_project_personal_image(dockerfile: Path, tag: str) -> str:
    expected = personal_image_ref(tag)
    try:
        from_ref = image.parse_base_image(dockerfile.read_text())
    except OSError as exc:
        raise PersonalImageRequiredError(f"could not read {dockerfile}: {exc}") from exc
    if from_ref is None:
        raise PersonalImageRequiredError(f"{dockerfile} has no FROM line")
    if from_ref != expected:
        raise PersonalImageRequiredError(
            f"{dockerfile} uses {from_ref!r}, but Carthage 2.0 projects must "
            f"use {expected!r}. Run /carthage-annex --upgrade or change the "
            f"Dockerfile to `FROM {expected}`."
        )
    return from_ref


def personal_image_hash(*, base_image: str, config: PersonalConfig) -> str:
    payload = {
        "schema": 1,
        "cli_version": __version__,
        # Bind-mounted agent state and credentials are owned by the host user.
        # Include the numeric identity so a personal image built on one host
        # is not incorrectly reused on another host with different IDs.
        "host_uid": os.getuid(),
        "host_gid": os.getgid(),
        "base_image": base_image,
        "agents": [
            {
                "kind": profile.kind,
                "root_install_commands": profile.root_install_commands,
                "install_commands": profile.install_commands,
                "install_user": profile.install_user,
            }
            for profile in agent_profiles_for(config.agents.installed)
        ],
        "apt_packages": config.image.apt_packages,
        "terminfo_files": _terminfo_file_fingerprints(config.image.terminfo_files),
        "files": _image_file_fingerprints(config.image.files),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _terminfo_file_fingerprints(files: tuple[str, ...]) -> list[dict[str, str]]:
    """Fingerprint sources so changing a local entry invalidates the image."""
    fingerprints = []
    for source in files:
        path = Path(source)
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            # Let build_personal_image report the actionable error, while still
            # invalidating an image made from a source that has since vanished.
            fingerprints.append({"path": source, "error": str(exc)})
        else:
            fingerprints.append({"path": source, "sha256": digest})
    return fingerprints


def _image_file_fingerprints(files: tuple[PersonalImageFile, ...]) -> list[dict[str, str]]:
    """Fingerprint copied personal files and their image destinations."""
    fingerprints = []
    for item in files:
        path = Path(item.source)
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            fingerprints.append({
                "source": item.source,
                "target": item.target,
                "mode": item.mode,
                "error": str(exc),
            })
        else:
            fingerprints.append({
                "source": item.source,
                "target": item.target,
                "mode": item.mode,
                "sha256": digest,
            })
    return fingerprints


def read_personal_image_hash(image_ref: str) -> str | None:
    try:
        result = subprocess.run(
            [
                "docker", "image", "inspect", image_ref,
                "--format", f'{{{{ index .Config.Labels "{PERSONAL_IMAGE_HASH_LABEL}" }}}}',
            ],
            capture_output=True,
            check=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    out = result.stdout.strip()
    if not out or out == "<no value>":
        return None
    return out


def render_personal_image_dockerfile(base_image: str, config: PersonalConfig) -> str:
    expected_hash = personal_image_hash(base_image=base_image, config=config)
    lines = [
        "# Generated by carthage fortify. Do not edit.",
        f"FROM {base_image}",
        f'LABEL {PERSONAL_IMAGE_HASH_LABEL}="{expected_hash}"',
        "",
        "ARG HOST_UID=1000",
        "ARG HOST_GID=1000",
        "",
        "USER root",
        "RUN set -eux; \\",
        "    target_user=\"$(getent passwd \"$HOST_UID\" | cut -d: -f1 || true)\"; \\",
        "    if [ -n \"$target_user\" ] && [ \"$target_user\" != carthage ]; then \\",
        "      echo \"HOST_UID=$HOST_UID is already assigned to $target_user\" >&2; \\",
        "      exit 1; \\",
        "    fi; \\",
        "    target_group=\"$(getent group \"$HOST_GID\" | cut -d: -f1 || true)\"; \\",
        "    if [ -n \"$target_group\" ] && [ \"$target_group\" != carthage ]; then \\",
        "      echo \"HOST_GID=$HOST_GID is already assigned to $target_group\" >&2; \\",
        "      exit 1; \\",
        "    fi; \\",
        "    current_gid=\"$(id -g carthage)\"; \\",
        "    if [ \"$current_gid\" != \"$HOST_GID\" ]; then groupmod --gid \"$HOST_GID\" carthage; fi; \\",
        "    current_uid=\"$(id -u carthage)\"; \\",
        "    if [ \"$current_uid\" != \"$HOST_UID\" ] || [ \"$current_gid\" != \"$HOST_GID\" ]; then \\",
        "      usermod --uid \"$HOST_UID\" --gid \"$HOST_GID\" carthage; \\",
        "    fi; \\",
        "    chown -R carthage:carthage /home/carthage /commandhistory",
    ]
    image_packages = list(config.image.apt_packages)
    if config.image.terminfo_files and "ncurses-bin" not in image_packages:
        image_packages.append("ncurses-bin")
    if image_packages:
        lines.extend([
            "",
            "RUN apt-get update \\",
            " && apt-get install -y --no-install-recommends \\",
        ])
        for package in image_packages:
            lines.append(f"      {package} \\")
        lines.append(" && rm -rf /var/lib/apt/lists/*")

    if config.image.terminfo_files:
        lines.extend([
            "",
            "# Personal terminal descriptions, compiled into the standard terminfo database.",
            "COPY terminfo/ /tmp/carthage-terminfo/",
            "RUN for source in /tmp/carthage-terminfo/*; do tic -x -o /usr/share/terminfo \"$source\"; done \\",
            " && rm -rf /tmp/carthage-terminfo",
        ])

    for index, item in enumerate(config.image.files):
        lines.extend([
            "",
            f"# Personal file: {item.source} -> {item.target}",
            f"COPY [\"files/{index}\", {json.dumps(item.target)}]",
            f"RUN chmod {item.mode} {shlex.quote(item.target)}",
        ])

    for profile in agent_profiles_for(config.agents.installed):
        if not profile.root_install_commands and not profile.install_commands:
            continue
        lines.extend([
            "",
            f"# Agent: {profile.kind}",
        ])
        for command in profile.root_install_commands:
            lines.append(f"RUN {command}")
        if profile.install_user != "root":
            lines.append(f"USER {profile.install_user}")
        for command in profile.install_commands:
            lines.append(f"RUN {command}")
        if profile.install_user != "root":
            lines.append("USER root")

    lines.extend([
        "",
        "USER carthage",
        "WORKDIR /workspace",
        "",
    ])
    return "\n".join(lines)


def personal_image_is_current(
    *,
    image_ref: str,
    base_image: str,
    config: PersonalConfig,
) -> tuple[bool, str]:
    expected = personal_image_hash(base_image=base_image, config=config)
    actual = read_personal_image_hash(image_ref)
    if actual is None:
        return False, f"missing or unlabeled (expected hash={expected})"
    if actual != expected:
        return False, f"stale (expected hash={expected}, found {actual})"
    return True, f"current (hash={expected})"


def build_personal_image(
    *,
    base_image: str,
    target_image: str,
    config: PersonalConfig,
    no_cache: bool = False,
) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="carthage-personal-image-") as tmp:
        context = Path(tmp)
        try:
            _copy_terminfo_files(context, config.image.terminfo_files)
            _copy_image_files(context, config.image.files)
        except OSError as exc:
            return False, f"could not stage personal image file: {exc}"
        dockerfile = Path(tmp) / "Dockerfile"
        dockerfile.write_text(render_personal_image_dockerfile(base_image, config))
        args = [
            "docker", "build", "-t", target_image, "-f", str(dockerfile),
            "--build-arg", f"HOST_UID={os.getuid()}",
            "--build-arg", f"HOST_GID={os.getgid()}",
        ]
        if no_cache:
            args.append("--no-cache")
        args.append(tmp)
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        tail = (result.stderr.strip() or result.stdout.strip()).splitlines()
        return False, tail[-1] if tail else f"docker build exited {result.returncode}"
    return True, target_image


def _copy_terminfo_files(context: Path, files: tuple[str, ...]) -> None:
    if not files:
        return
    destination = context / "terminfo"
    destination.mkdir()
    for index, source in enumerate(files):
        path = Path(source)
        if not path.is_file():
            raise OSError(f"{path} is not a readable regular file")
        (destination / str(index)).write_bytes(path.read_bytes())


def _copy_image_files(context: Path, files: tuple[PersonalImageFile, ...]) -> None:
    if not files:
        return
    destination = context / "files"
    destination.mkdir()
    for index, item in enumerate(files):
        path = Path(item.source)
        if not path.is_file():
            raise OSError(f"{path} is not a readable regular file")
        (destination / str(index)).write_bytes(path.read_bytes())
