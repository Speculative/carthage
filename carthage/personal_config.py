"""User-level Carthage config.

Personal config is intentionally softer than project config: a malformed
`~/.carthage/config.toml` should not break every Carthage project on the
machine. Callers get defaults plus warnings they can surface to the user.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - fallback for 3.10
    import tomli as tomllib


CURRENT_PERSONAL_CONFIG_SCHEMA = "1"
MIN_READABLE_PERSONAL_CONFIG_SCHEMA = "1"


@dataclass(frozen=True)
class PersonalConfig:
    version: str = CURRENT_PERSONAL_CONFIG_SCHEMA


@dataclass(frozen=True)
class PersonalConfigResult:
    config: PersonalConfig
    warnings: tuple[str, ...]
    path: Path
    exists: bool


def default_personal_config_path() -> Path:
    return Path.home() / ".carthage" / "config.toml"


def load_personal_config(path: Path | None = None) -> PersonalConfigResult:
    config_path = path or default_personal_config_path()
    default = PersonalConfig()

    try:
        with config_path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return PersonalConfigResult(default, (), config_path, False)
    except tomllib.TOMLDecodeError as exc:
        return PersonalConfigResult(
            default,
            (f"{config_path}: malformed TOML; ignoring personal config ({exc})",),
            config_path,
            True,
        )
    except OSError as exc:
        return PersonalConfigResult(
            default,
            (f"{config_path}: could not read personal config; ignoring it ({exc})",),
            config_path,
            True,
        )

    warnings: list[str] = []
    table = data.get("carthage")
    if not isinstance(table, dict):
        return PersonalConfigResult(
            default,
            (f"{config_path}: missing [carthage] table; using personal defaults",),
            config_path,
            True,
        )

    raw_version = table.get("personal_config_version")
    if raw_version is None:
        warnings.append(
            f"{config_path}: missing [carthage].personal_config_version; "
            f"defaulting to '{CURRENT_PERSONAL_CONFIG_SCHEMA}'"
        )
        version = CURRENT_PERSONAL_CONFIG_SCHEMA
    else:
        version = str(raw_version)

    if version < MIN_READABLE_PERSONAL_CONFIG_SCHEMA:
        return PersonalConfigResult(
            default,
            (
                f"{config_path}: personal_config_version '{version}' is older "
                f"than this CLI can read; ignoring personal config",
            ),
            config_path,
            True,
        )
    if version > CURRENT_PERSONAL_CONFIG_SCHEMA:
        return PersonalConfigResult(
            default,
            (
                f"{config_path}: personal_config_version '{version}' is newer "
                f"than this CLI supports; ignoring personal config",
            ),
            config_path,
            True,
        )

    return PersonalConfigResult(
        PersonalConfig(version=version),
        tuple(warnings),
        config_path,
        True,
    )


def describe_personal_config(result: PersonalConfigResult) -> str:
    if not result.exists:
        return "none"
    base = f"{result.path} schema '{result.config.version}'"
    if result.warnings:
        return base + "; " + "; ".join(result.warnings)
    return base
