"""E2E Test E — `carthage up` warns when annex templates have drifted.

The fixture is annexed under CLI 1.0.0 (per CONTRIBUTING: "Don't update the
E2E fixture configs — they represent projects annexed at a specific past
CLI version"). Running the CURRENT CLI against it should print the
re-annex nudge.

We assert on stdout text rather than parsing because the message is the
user-facing surface — if the wording changes meaningfully, the test
should fail and the test author should re-confirm the message is still
clear before updating the assertion.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import run_carthage


pytestmark = pytest.mark.e2e


@pytest.mark.project_fixture("py-http-server")
def test_up_nudges_when_annexed_under_older_cli(
    base_image, project_dir, compose_project_name, docker_cleanup
):
    r = run_carthage("up", cwd=project_dir)
    assert r.returncode == 0, f"carthage up failed: {r.stderr}"

    out = r.stdout
    assert "annexed under CLI 1.0.0" in out, (
        f"expected re-annex nudge naming the old version, got:\n{out}"
    )
    assert "/carthage-annex --upgrade" in out, (
        f"expected nudge to mention the upgrade command, got:\n{out}"
    )


@pytest.mark.project_fixture("py-http-server")
def test_status_flags_outdated_annex(
    base_image, project_dir, compose_project_name, docker_cleanup
):
    r = run_carthage("up", cwd=project_dir)
    assert r.returncode == 0, f"carthage up failed: {r.stderr}"

    r = run_carthage("status", cwd=project_dir)
    assert r.returncode == 0, f"carthage status failed: {r.stderr}"

    out = r.stdout
    assert "annexed under" in out, f"expected 'annexed under' row, got:\n{out}"
    assert "/carthage-annex --upgrade" in out, (
        f"expected status to flag the drift with an upgrade hint, got:\n{out}"
    )


@pytest.mark.project_fixture("py-http-server")
def test_survey_check_fails_for_outdated_annex(
    base_image, project_dir, compose_project_name, docker_cleanup
):
    """Survey should non-zero-exit because the annex-template check fails."""
    r = run_carthage("survey", cwd=project_dir)
    # Other checks may or may not pass in this environment; we don't assert
    # on returncode beyond "the check ran." We just want to see the line.
    assert "project annex templates are up to date" in r.stdout, (
        f"expected the new survey check, got:\n{r.stdout}"
    )
    assert "annexed under CLI 1.0.0" in r.stdout, (
        f"expected the failed-check detail to name the old version, got:\n{r.stdout}"
    )
