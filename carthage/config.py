"""Read and validate a project's `.carthage/config.toml`.

Schema shape (v1):

    [carthage]
    schema_version   = 1        # config TOML schema version
    template_version = 1        # annex template bundle version
    base_image_tag   = "v1"     # which carthage-base MAJOR the project targets
    service_name     = "dev"
    project_slug     = "my-project"

Optional personal-config opt-outs:

    [personal]
    disable = ["my-personal-mount"]

`base_image` is *derived*, not stored — the tag + repo fully determines it.
This avoids one class of drift (tag in config disagrees with `FROM` in
Dockerfile). The CLI synthesizes `ghcr.io/speculative/carthage-base:<tag>`.

The CLI accepts config schemas in its compatibility window. Anything older
than that is rejected; anything newer means the CLI is out of date.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover — fallback for 3.10
    import tomli as tomllib

from carthage import (
    BASE_IMAGE_REPO,
    CURRENT_PROJECT_SCHEMA_VERSION,
    MIN_READABLE_PROJECT_SCHEMA_VERSION,
)


class ConfigError(RuntimeError):
    """`.carthage/config.toml` is missing, malformed, or an unsupported schema."""


class SchemaTooOldError(ConfigError):
    """The config schema is older than this CLI can still read."""


@dataclass(frozen=True)
class CarthageConfig:
    project_root: Path
    schema_version: int           # schema version
    template_version: int | None  # None for legacy annexed_with_cli projects
    legacy_annexed_with_cli: str | None
    base_image_tag: str           # e.g. "v1"
    service_name: str
    project_slug: str
    schema_is_outdated: bool      # True if schema < CURRENT but within compat window
    base_image_override: str | None  # escape hatch for tests / forks; bypasses derivation
    personal_disabled: tuple[str, ...]  # stable personal-config IDs disabled for this project

    @property
    def carthage_dir(self) -> Path:
        return self.project_root / ".carthage"

    @property
    def dockerfile(self) -> Path:
        return self.carthage_dir / "Dockerfile"

    @property
    def compose_file(self) -> Path:
        return self.carthage_dir / "docker-compose.yaml"

    @property
    def last_build_hash_file(self) -> Path:
        return self.carthage_dir / "last-build-hash"

    @property
    def base_image(self) -> str:
        """The full image reference. Derived from `base_image_tag` unless
        `base_image_override` is set in the config (tests use this)."""
        if self.base_image_override:
            return self.base_image_override
        return f"{BASE_IMAGE_REPO}:{self.base_image_tag}"

    @property
    def project_image_repo(self) -> str:
        """Local image name we tag per-hash builds into."""
        return f"carthage-{self.project_slug}"

    @property
    def compose_project_name(self) -> str:
        """Compose project name — used to namespace containers and networks."""
        return f"carthage-{self.project_slug}"

    @property
    def host_state_dir(self) -> Path:
        """Per-project host-side state dir, bind-mounted into the container at
        /commandhistory. Currently holds .bash_history; future state files
        (zsh history, fzf history, etc.) live alongside it. Slug-keyed, so
        two projects with the same slug share state — same caveat as the
        compose project name."""
        return Path.home() / ".carthage" / "state" / self.project_slug


def find_project_root(start: Path | None = None) -> Path:
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".carthage" / "config.toml").is_file():
            return candidate
    raise ConfigError(
        "No `.carthage/config.toml` found in this directory or any parent. "
        "Run `/carthage-annex` from a configured agent to set up this project first."
    )


def load_config(project_root: Path | None = None) -> CarthageConfig:
    root = project_root if project_root is not None else find_project_root()
    config_path = root / ".carthage" / "config.toml"
    try:
        with config_path.open("rb") as f:
            data = tomllib.load(f)
    except OSError as exc:
        raise ConfigError(f"Could not read {config_path}: {exc}") from exc

    table = data.get("carthage")
    if not isinstance(table, dict):
        raise ConfigError(
            f"{config_path}: missing `[carthage]` table at the top level."
        )

    required = ("service_name", "project_slug")
    missing = [k for k in required if k not in table]
    if missing:
        raise ConfigError(
            f"{config_path}: missing required keys in [carthage]: "
            + ", ".join(missing)
        )

    schema_version = _parse_required_version_int(
        table.get("schema_version", table.get("version")),
        field=f"{config_path}: [carthage].schema_version",
    )

    # --- Schema compat check ---
    if schema_version < MIN_READABLE_PROJECT_SCHEMA_VERSION:
        raise SchemaTooOldError(
            f"{config_path}: schema version '{schema_version}' is older than this "
            f"CLI can read (min: '{MIN_READABLE_PROJECT_SCHEMA_VERSION}', "
            f"current: '{CURRENT_PROJECT_SCHEMA_VERSION}'). Upgrade the project "
            "config before using this CLI."
        )
    schema_is_outdated = schema_version < CURRENT_PROJECT_SCHEMA_VERSION
    if schema_version > CURRENT_PROJECT_SCHEMA_VERSION:
        raise ConfigError(
            f"{config_path}: schema version '{schema_version}' is newer than this "
            f"CLI knows about (current: '{CURRENT_PROJECT_SCHEMA_VERSION}'). "
            "Upgrade your carthage CLI: `uv tool upgrade carthage-cli`."
        )

    # Slug validation — used in image tags and compose project names.
    slug = str(table["project_slug"])
    if not slug or not all(c.isalnum() or c in "._-" for c in slug) or not slug[0].isalnum():
        raise ConfigError(
            f"{config_path}: project_slug {slug!r} is invalid. "
            "Use lowercase letters, digits, '.', '_', or '-' (starting with alnum)."
        )

    # base_image_tag: accept v1 schema files that pre-date this field and fall
    # back to the schema default. In practice the annex skill always writes it.
    base_image_tag = str(table.get("base_image_tag", "v1"))

    template_version = _parse_optional_version_int(
        table.get("template_version"),
        field=f"{config_path}: [carthage].template_version",
    )
    legacy_annexed_with_cli = table.get("annexed_with_cli")
    legacy_annexed_with_cli = (
        str(legacy_annexed_with_cli) if legacy_annexed_with_cli is not None else None
    )

    override = table.get("base_image")
    base_image_override = str(override) if override else None

    personal_table = data.get("personal", {})
    if personal_table is None:
        personal_table = {}
    if not isinstance(personal_table, dict):
        raise ConfigError(f"{config_path}: [personal] must be a table if present.")
    disabled_raw = personal_table.get("disable", [])
    if not isinstance(disabled_raw, list) or not all(isinstance(item, str) for item in disabled_raw):
        raise ConfigError(f"{config_path}: [personal].disable must be a list of strings.")
    personal_disabled = tuple(disabled_raw)

    return CarthageConfig(
        project_root=root,
        schema_version=schema_version,
        template_version=template_version,
        legacy_annexed_with_cli=legacy_annexed_with_cli,
        base_image_tag=base_image_tag,
        service_name=str(table["service_name"]),
        project_slug=slug,
        schema_is_outdated=schema_is_outdated,
        base_image_override=base_image_override,
        personal_disabled=personal_disabled,
    )


def _parse_required_version_int(
    value: object,
    *,
    field: str,
) -> int:
    if value is None:
        raise ConfigError(f"{field} is required")
    parsed = _coerce_version_int(value)
    if parsed is None:
        raise ConfigError(f"{field} must be an integer")
    return parsed


def _parse_optional_version_int(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    parsed = _coerce_version_int(value)
    if parsed is None:
        raise ConfigError(f"{field} must be an integer")
    return parsed


def _coerce_version_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None
