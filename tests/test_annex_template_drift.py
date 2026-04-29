"""Unit tests for `annex_template_is_outdated`.

Used by `up`/`status`/`survey` to nudge users to re-annex when the templates
have likely changed underneath their project. Patch-only drift returns
False by policy — patch releases don't ship template changes.
"""

from unittest.mock import patch

from carthage import annex_template_is_outdated


def _at_version(v: str):
    """Pin __version__ for the duration of a test."""
    return patch("carthage.__version__", v)


def test_none_means_pre_1_0_treated_as_outdated():
    with _at_version("1.1.0"):
        assert annex_template_is_outdated(None) is True


def test_equal_versions_not_outdated():
    with _at_version("1.1.0"):
        assert annex_template_is_outdated("1.1.0") is False


def test_patch_drift_not_outdated():
    # Policy: patch releases don't ship template changes, so 1.1.0 -> 1.1.5
    # is fine; we don't nudge the user.
    with _at_version("1.1.5"):
        assert annex_template_is_outdated("1.1.0") is False


def test_minor_drift_outdated():
    with _at_version("1.2.0"):
        assert annex_template_is_outdated("1.1.0") is True


def test_major_drift_outdated():
    with _at_version("2.0.0"):
        assert annex_template_is_outdated("1.5.0") is True


def test_newer_than_cli_not_outdated():
    # A project annexed under a newer CLI than is currently installed isn't
    # "outdated"; if anything the *CLI* is behind. Either way: no nudge.
    with _at_version("1.0.0"):
        assert annex_template_is_outdated("1.1.0") is False


def test_unparseable_version_not_outdated():
    # Don't false-positive on garbage. Better to silently skip the check.
    with _at_version("1.1.0"):
        assert annex_template_is_outdated("not-a-version") is False
