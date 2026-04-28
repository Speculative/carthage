"""Read and validate a project's `.carthage/config.toml`.

Schema shape (v1):

    [carthage]
    version          = "1"      # config schema version
    base_image_tag   = "v1"     # which carthage-base MAJOR the project targets
    annexed_with_cli = "1.0.1"  # informational; what CLI did the annex
    service_name     = "dev"
    project_slug     = "my-project"

`base_image` is *derived*, not stored — the tag + repo fully determines it.
This avoids one class of drift (tag in config disagrees with `FROM` in
Dockerfile). The CLI synthesizes `ghcr.io/speculative/carthage-base:<tag>`.

The CLI accepts config schemas one major back: a `v2` CLI reads `version=1`
configs (with a soft note pointing at `/carthage-migrate`). Anything older
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
    CURRENT_CONFIG_SCHEMA,
    MIN_READABLE_CONFIG_SCHEMA,
)


class ConfigError(RuntimeError):
    """`.carthage/config.toml` is missing, malformed, or an unsupported schema."""


class SchemaTooOldError(ConfigError):
    """The config schema is older than this CLI can still read. Point user at
    `/carthage-migrate`."""


@dataclass(frozen=True)
class CarthageConfig:
    project_root: Path
    version: str                  # schema version
    base_image_tag: str           # e.g. "v1"
    annexed_with_cli: str | None  # informational; None for pre-1.0 configs
    service_name: str
    project_slug: str
    schema_is_outdated: bool      # True if schema < CURRENT but within compat window
    base_image_override: str | None  # escape hatch for tests / forks; bypasses derivation

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
        "Run `/carthage-annex` in Claude Code to set up this project first."
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

    required = ("version", "service_name", "project_slug")
    missing = [k for k in required if k not in table]
    if missing:
        raise ConfigError(
            f"{config_path}: missing required keys in [carthage]: "
            + ", ".join(missing)
        )

    schema_version = str(table["version"])

    # --- Schema compat check ---
    # Lexicographic compare on single-digit schema ints works for the
    # foreseeable future (we're at "1"; bumping to "2" stays fine). If we
    # ever hit "10" we'll revisit.
    if schema_version < MIN_READABLE_CONFIG_SCHEMA:
        raise SchemaTooOldError(
            f"{config_path}: schema version '{schema_version}' is older than this "
            f"CLI can read (min: '{MIN_READABLE_CONFIG_SCHEMA}', current: "
            f"'{CURRENT_CONFIG_SCHEMA}'). Run `/carthage-migrate` in this project "
            "from Claude Code to upgrade the config."
        )
    schema_is_outdated = schema_version < CURRENT_CONFIG_SCHEMA
    if schema_version > CURRENT_CONFIG_SCHEMA:
        raise ConfigError(
            f"{config_path}: schema version '{schema_version}' is newer than this "
            f"CLI knows about (current: '{CURRENT_CONFIG_SCHEMA}'). "
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

    annexed_with_cli = table.get("annexed_with_cli")
    annexed_with_cli = str(annexed_with_cli) if annexed_with_cli is not None else None

    override = table.get("base_image")
    base_image_override = str(override) if override else None

    return CarthageConfig(
        project_root=root,
        version=schema_version,
        base_image_tag=base_image_tag,
        annexed_with_cli=annexed_with_cli,
        service_name=str(table["service_name"]),
        project_slug=slug,
        schema_is_outdated=schema_is_outdated,
        base_image_override=base_image_override,
    )
