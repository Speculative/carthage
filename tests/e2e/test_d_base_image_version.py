"""E2E Test D — base-image OCI version flows from image to running container.

Verifies the runtime version-tracking story end-to-end:
  - The CLI reads `org.opencontainers.image.version` off the locally-cached
    base image at `up` time.
  - It propagates that into the dev container as a `carthage.base-image-version`
    label.
  - `image.read_running_dev_container_base_version()` reads it back.
  - `carthage status` flags a stale running container after the local base
    image is re-tagged with a newer OCI version.

We don't try to exercise `carthage survey` here — the version-currency check
is the same code path as `status`, just rendered as a row in a different
table. status coverage is sufficient.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.e2e.conftest import E2E_BASE_OCI_VERSION, E2E_BASE_TAG, run_carthage


pytestmark = pytest.mark.e2e


@pytest.mark.project_fixture("py-http-server")
def test_base_image_version_label_flows_to_container(
    base_image, project_dir, compose_project_name, docker_cleanup
):
    r = run_carthage("up", cwd=project_dir)
    assert r.returncode == 0, f"carthage up failed: {r.stderr}"

    # Direct docker query — confirm the label landed on the running container.
    # `docker ps --format` returns labels as a comma-joined string, not a map,
    # so we have to find the container ID first and then `docker inspect` it.
    ps = subprocess.run(
        ["docker", "ps", "-a",
         "--filter", f"label=carthage.project={compose_project_name.removeprefix('carthage-')}",
         "--filter", "label=carthage.role=dev",
         "--format", "{{.ID}}"],
        capture_output=True, text=True, check=True,
    )
    container_id = ps.stdout.strip().splitlines()[0]
    assert container_id, "no dev container found after `carthage up`"
    inspect = subprocess.run(
        ["docker", "inspect", container_id,
         "--format", '{{ index .Config.Labels "carthage.base-image-version" }}'],
        capture_output=True, text=True, check=True,
    )
    label = inspect.stdout.strip()
    assert label == E2E_BASE_OCI_VERSION, (
        f"expected base-image-version label {E2E_BASE_OCI_VERSION!r} on running "
        f"container, got {label!r}"
    )


@pytest.mark.project_fixture("py-http-server")
def test_status_flags_stale_running_container(
    base_image, project_dir, compose_project_name, docker_cleanup
):
    """Up the container, then re-tag the base image with a newer OCI version
    label and confirm `carthage status` reports the running container as
    stale (it still records the OLD version even though the local base
    image has been bumped)."""
    r = run_carthage("up", cwd=project_dir)
    assert r.returncode == 0, f"carthage up failed: {r.stderr}"

    # Build a transient image that re-tags carthage-base:e2e with a *newer*
    # OCI version label. We do this with a one-line Dockerfile so we don't
    # invalidate the session-scoped base_image fixture's tag for other tests.
    bumped_dockerfile = (
        f"FROM {E2E_BASE_TAG}\n"
        f"LABEL org.opencontainers.image.version=99.99.99-bumped\n"
    )
    p = project_dir / "_e2e_bumped_base"
    p.mkdir()
    (p / "Dockerfile").write_text(bumped_dockerfile)
    bumped_tag = E2E_BASE_TAG  # clobber the session tag — we'll restore in finalize
    try:
        r = subprocess.run(
            ["docker", "build", "-t", bumped_tag, str(p)],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"failed to build bumped base: {r.stderr}"

        # Now `carthage status` should report stale.
        r = run_carthage("status", cwd=project_dir)
        assert r.returncode == 0, f"carthage status failed: {r.stderr}"
        assert "stale" in r.stdout.lower(), (
            f"expected status to flag stale running container, got:\n{r.stdout}"
        )
        assert "99.99.99-bumped" in r.stdout, (
            f"expected status to mention the new version, got:\n{r.stdout}"
        )
        assert E2E_BASE_OCI_VERSION in r.stdout, (
            f"expected status to mention the running version "
            f"{E2E_BASE_OCI_VERSION}, got:\n{r.stdout}"
        )
    finally:
        # Restore the session-scoped base image tag so subsequent tests
        # don't see the bumped version. We rebuild from the original
        # base/Dockerfile with the original OCI label.
        from tests.e2e.conftest import BASE_DIR
        import os
        subprocess.run(
            ["docker", "build",
             "-t", E2E_BASE_TAG,
             "--label", f"org.opencontainers.image.version={E2E_BASE_OCI_VERSION}",
             "--build-arg", f"HOST_UID={os.getuid()}",
             "--build-arg", f"HOST_GID={os.getgid()}",
             str(BASE_DIR)],
            capture_output=True, text=True,
        )
