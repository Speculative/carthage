"""Carthage — sandboxed dev-environment management.

Three artifacts share one semver: the CLI, the personal Carthage skills
(`/carthage-annex` today), and the `carthage-base` Docker image. The
skills ship inside the CLI wheel via
`tool.hatch.build.targets.wheel.force-include`, so installing CLI vN.M
means skill vN.M is on disk after `carthage fortify` — no drift possible.

Compatibility policy: projects pin to a base-image *major* (e.g. `:v2`).
TOML schemas and annex templates have separate integer versions because they
move independently. Old projects never auto-upgrade.
"""

__version__ = "2.2.0"

# The project `.carthage/config.toml` schema version the CLI currently writes.
CURRENT_PROJECT_SCHEMA_VERSION = 1

# Oldest project config schema this CLI can still read.
MIN_READABLE_PROJECT_SCHEMA_VERSION = 1

# The annex template bundle version the CLI currently writes. This is bumped
# only when generated committed project files should change.
CURRENT_TEMPLATE_VERSION = 2

# The base image major this CLI expects. When the CLI bumps its major, this
# likely bumps too — but not always; a CLI major bump may just be a config
# schema change.
EXPECTED_BASE_IMAGE_TAG = "v2"

BASE_IMAGE_REPO = "ghcr.io/speculative/carthage-base"
PERSONAL_BASE_IMAGE_REPO = "carthage-base-personal"


def annex_template_is_outdated(template_version: int | None) -> bool:
    """True iff a project's template bundle is older than this CLI's bundle.

    Projects without `template_version` are legacy `annexed_with_cli`
    projects from before Carthage tracked template versions explicitly, so
    they should be nudged through `/carthage-annex --upgrade`.
    """
    if template_version is None:
        return True
    return template_version < CURRENT_TEMPLATE_VERSION
