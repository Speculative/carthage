"""Carthage — sandboxed dev-environment management.

Three artifacts share one semver: the CLI, the personal Claude skills
(`/carthage-annex` today, `/carthage-migrate` reserved), and the
`carthage-base` Docker image. The skills ship inside the CLI wheel via
`tool.hatch.build.targets.wheel.force-include`, so installing CLI vN.M
means skill vN.M is on disk after `carthage fortify` — no drift possible.

Compatibility policy: projects pin to a base-image *major* (e.g. `:v1`), and
the CLI reads config schemas one major back. Major bumps require explicit
per-project migration; old projects never auto-upgrade.
"""

__version__ = "1.1.1"

# The schema version the CLI currently *writes*. Config files with this
# `version` are read natively; older schemas in the backward-compat window are
# read with a warning; anything older requires /carthage-migrate.
CURRENT_CONFIG_SCHEMA = "1"

# Oldest config schema this CLI can still read. Typically CURRENT - 1 (one
# major back). Equal to CURRENT means no backward compatibility window yet,
# which is correct for v1.0.
MIN_READABLE_CONFIG_SCHEMA = "1"

# The base image major this CLI expects. When the CLI bumps its major, this
# likely bumps too — but not always; a CLI major bump may just be a config
# schema change.
EXPECTED_BASE_IMAGE_TAG = "v1"

BASE_IMAGE_REPO = "ghcr.io/speculative/carthage-base"


def annex_template_is_outdated(annexed_with_cli: str | None) -> bool:
    """True iff a project's `annexed_with_cli` is older than this CLI by
    minor-or-major, meaning the annex skill's templates may have changed
    underneath it. Patch-only drift returns False — we don't ship template
    changes in patch releases by policy (CONTRIBUTING "Versioning policy").

    Used by `up`/`status`/`survey` to nudge the user toward
    `/carthage-annex --upgrade`. None means a pre-field config (very old);
    treat as outdated.
    """
    if annexed_with_cli is None:
        return True
    try:
        cur = tuple(int(p) for p in __version__.split(".")[:2])
        old = tuple(int(p) for p in annexed_with_cli.split(".")[:2])
    except ValueError:
        return False
    return old < cur
