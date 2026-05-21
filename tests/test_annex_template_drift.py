"""Unit tests for `annex_template_is_outdated`.

Used by `up`/`status`/`survey` to nudge users to re-annex when the templates
have changed underneath their project.
"""

from unittest.mock import patch

from carthage import annex_template_is_outdated
from carthage.commands.survey import check_annex_template_current


class _Cfg:
    def __init__(self, template_version):
        self.template_version = template_version


def _at_template_version(v: int):
    """Pin CURRENT_TEMPLATE_VERSION for the duration of a test."""
    return patch("carthage.CURRENT_TEMPLATE_VERSION", v)


def _at_survey_template_version(v: int):
    return patch("carthage.commands.survey.CURRENT_TEMPLATE_VERSION", v)


def test_none_means_legacy_template_marker_is_outdated():
    with _at_template_version(1):
        assert annex_template_is_outdated(None) is True


def test_equal_versions_not_outdated():
    with _at_template_version(1):
        assert annex_template_is_outdated(1) is False


def test_older_template_is_outdated():
    with _at_template_version(3):
        assert annex_template_is_outdated(2) is True


def test_newer_than_cli_template_not_outdated():
    with _at_template_version(1):
        assert annex_template_is_outdated(2) is False


def test_survey_template_check_passes_for_current_template():
    with _at_template_version(1):
        result = check_annex_template_current(_Cfg(1))

    assert result.ok is True
    assert "matches current" in result.detail


def test_survey_template_check_fails_for_legacy_template():
    result = check_annex_template_current(_Cfg(None))

    assert result.ok is False
    assert "no `template_version` field" in result.detail


def test_survey_template_check_fails_for_stale_template():
    with _at_template_version(3), _at_survey_template_version(3):
        result = check_annex_template_current(_Cfg(2))

    assert result.ok is False
    assert "template 2, current is 3" in result.detail
