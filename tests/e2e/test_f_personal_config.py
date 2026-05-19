"""E2E Test F — personal config and personal image behavior."""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from tests.e2e.conftest import run_carthage


pytestmark = pytest.mark.e2e


def _prepare_home(path: Path) -> Path:
    home = path / "home"
    home.mkdir()
    (home / ".claude").mkdir()
    (home / ".claude.json").write_text("{}\n")
    (home / ".gitconfig").write_text("[user]\n\tname = E2E\n\temail = e2e@example.invalid\n")
    return home


def _personal_repo() -> str:
    return f"carthage-base-personal-e2e-{uuid.uuid4().hex[:8]}"


def _env(home: Path, personal_repo: str) -> dict[str, str]:
    return {
        "HOME": str(home),
        "CARTHAGE_PERSONAL_IMAGE_REPO": personal_repo,
    }


def _use_personal_image(project_dir: Path, personal_repo: str) -> str:
    image_ref = f"{personal_repo}:v1"
    dockerfile = project_dir / ".carthage" / "Dockerfile"
    text = dockerfile.read_text()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("FROM "):
            lines[index] = f"FROM {image_ref}"
            break
    else:
        lines.insert(0, f"FROM {image_ref}")
    dockerfile.write_text("\n".join(lines) + "\n")
    return image_ref


def _dev_container_id(compose_project_name: str) -> str:
    ps = subprocess.run(
        [
            "docker", "ps", "-a",
            "--filter", f"label=carthage.project={compose_project_name.removeprefix('carthage-')}",
            "--filter", "label=carthage.role=dev",
            "--format", "{{.ID}}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    container_id = ps.stdout.strip().splitlines()[0]
    assert container_id
    return container_id


def _docker_exec(container_id: str, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", container_id, *argv],
        capture_output=True,
        text=True,
    )


def _cleanup_personal_repo(repo: str) -> None:
    r = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}", repo],
        capture_output=True,
        text=True,
    )
    tags = [line for line in r.stdout.splitlines() if line.strip()]
    if tags:
        subprocess.run(["docker", "rmi", "-f", *tags], capture_output=True)


@pytest.fixture
def personal_e2e(tmp_path: Path):
    home = _prepare_home(tmp_path)
    repo = _personal_repo()
    yield home, repo
    _cleanup_personal_repo(repo)
    shutil.rmtree(home, ignore_errors=True)


@pytest.mark.project_fixture("py-http-server")
def test_up_builds_isolated_personal_image_without_personal_config(
    base_image, project_dir, compose_project_name, docker_cleanup, personal_e2e
):
    home, repo = personal_e2e
    image_ref = _use_personal_image(project_dir, repo)

    r = run_carthage("up", cwd=project_dir, env=_env(home, repo))
    assert r.returncode == 0, f"carthage up failed:\nstdout={r.stdout}\nstderr={r.stderr}"

    inspect = subprocess.run(
        ["docker", "image", "inspect", image_ref],
        capture_output=True,
        text=True,
    )
    assert inspect.returncode == 0, f"personal image {image_ref} was not built"
    assert _dev_container_id(compose_project_name)


@pytest.mark.project_fixture("py-http-server")
def test_personal_mount_and_environment_land_in_container(
    base_image, project_dir, compose_project_name, docker_cleanup, personal_e2e
):
    home, repo = personal_e2e
    _use_personal_image(project_dir, repo)
    notes = home / "notes"
    notes.mkdir()
    (notes / "hello.txt").write_text("hello from personal mount\n")
    carthage_home = home / ".carthage"
    carthage_home.mkdir()
    (carthage_home / "config.toml").write_text(
        """
[carthage]
personal_config_version = "1"

[[mounts]]
id = "notes"
source = "~/notes"
target = "/home/carthage/.notes"
mode = "ro"

[[environment]]
id = "editor"
name = "CARTHAGE_E2E_PERSONAL_ENV"
value = "from-personal-config"
"""
    )

    r = run_carthage("up", cwd=project_dir, env=_env(home, repo))
    assert r.returncode == 0, f"carthage up failed:\nstdout={r.stdout}\nstderr={r.stderr}"
    container_id = _dev_container_id(compose_project_name)

    r = _docker_exec(container_id, "sh", "-c", "printf '%s' \"$CARTHAGE_E2E_PERSONAL_ENV\"")
    assert r.returncode == 0
    assert r.stdout == "from-personal-config"
    r = _docker_exec(container_id, "cat", "/home/carthage/.notes/hello.txt")
    assert r.returncode == 0
    assert r.stdout == "hello from personal mount\n"


@pytest.mark.project_fixture("py-http-server")
def test_project_opt_out_disables_personal_items(
    base_image, project_dir, compose_project_name, docker_cleanup, personal_e2e
):
    home, repo = personal_e2e
    _use_personal_image(project_dir, repo)
    notes = home / "notes"
    notes.mkdir()
    (notes / "hello.txt").write_text("hello\n")
    carthage_home = home / ".carthage"
    carthage_home.mkdir()
    (carthage_home / "config.toml").write_text(
        """
[carthage]
personal_config_version = "1"

[[mounts]]
id = "notes"
source = "~/notes"
target = "/home/carthage/.notes"
mode = "ro"

[[environment]]
id = "editor"
name = "CARTHAGE_E2E_PERSONAL_ENV"
value = "from-personal-config"
"""
    )
    project_config = project_dir / ".carthage" / "config.toml"
    project_config.write_text(project_config.read_text() + '\n[personal]\ndisable = ["notes", "editor"]\n')

    r = run_carthage("up", cwd=project_dir, env=_env(home, repo))
    assert r.returncode == 0, f"carthage up failed:\nstdout={r.stdout}\nstderr={r.stderr}"
    container_id = _dev_container_id(compose_project_name)

    r = _docker_exec(container_id, "sh", "-c", "test -z \"${CARTHAGE_E2E_PERSONAL_ENV:-}\"")
    assert r.returncode == 0, "personal env var should have been opted out"
    r = _docker_exec(container_id, "test", "!", "-e", "/home/carthage/.notes/hello.txt")
    assert r.returncode == 0, "notes mount should have been opted out"


@pytest.mark.project_fixture("py-http-server")
def test_personal_apt_packages_are_available_in_project_container(
    base_image, project_dir, compose_project_name, docker_cleanup, personal_e2e
):
    home, repo = personal_e2e
    _use_personal_image(project_dir, repo)
    carthage_home = home / ".carthage"
    carthage_home.mkdir()
    (carthage_home / "config.toml").write_text(
        """
[carthage]
personal_config_version = "1"

[image]
apt_packages = ["shellcheck"]
"""
    )

    r = run_carthage("up", cwd=project_dir, env=_env(home, repo))
    assert r.returncode == 0, f"carthage up failed:\nstdout={r.stdout}\nstderr={r.stderr}"
    container_id = _dev_container_id(compose_project_name)

    r = _docker_exec(container_id, "which", "shellcheck")
    assert r.returncode == 0, f"shellcheck missing: stdout={r.stdout} stderr={r.stderr}"
