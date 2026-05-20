"""User-level Carthage config.

Personal config is intentionally softer than project config: a malformed
`~/.carthage/config.toml` should not break every Carthage project on the
machine. Callers get defaults plus warnings they can surface to the user.
"""

from __future__ import annotations

import sys
import re
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - fallback for 3.10
    import tomli as tomllib

from carthage.agents import DEFAULT_AGENT_KIND, supported_agent_kinds
from carthage.runtime import RuntimeEnv, RuntimeMount, RuntimeOverlay


CURRENT_PERSONAL_CONFIG_SCHEMA = "1"
MIN_READABLE_PERSONAL_CONFIG_SCHEMA = "1"
RESERVED_ENVIRONMENT_NAMES = frozenset({
    "CARTHAGE",
    "CARTHAGE_PROJECT",
    "CARTHAGE_AGENT_BINARY",
    "CARTHAGE_AGENT_COMMAND",
    "CARTHAGE_AGENT_KIND",
    "CARTHAGE_AGENT_WINDOW",
    "CARTHAGE_TMUX_SESSION",
})
_APT_PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+.-]*$")


PersonalMount = RuntimeMount
PersonalEnvironment = RuntimeEnv


@dataclass(frozen=True)
class PersonalImage:
    apt_packages: tuple[str, ...] = ()


@dataclass(frozen=True)
class PersonalAgents:
    installed: tuple[str, ...] = (DEFAULT_AGENT_KIND,)
    default: str = DEFAULT_AGENT_KIND


@dataclass(frozen=True)
class PersonalConfig:
    version: str = CURRENT_PERSONAL_CONFIG_SCHEMA
    mounts: tuple[PersonalMount, ...] = ()
    environment: tuple[PersonalEnvironment, ...] = ()
    image: PersonalImage = field(default_factory=PersonalImage)
    agents: PersonalAgents = field(default_factory=PersonalAgents)

    @property
    def runtime_overlay(self) -> RuntimeOverlay:
        return RuntimeOverlay(mounts=self.mounts, environment=self.environment)


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

    mounts, mount_warnings = _parse_mounts(data.get("mounts"), config_path)
    environment, env_warnings = _parse_environment(data.get("environment"), config_path)
    image_config, image_warnings = _parse_image(data.get("image"), config_path)
    agents_config, agent_warnings = _parse_agents(
        data.get("agents"),
        data.get("agent"),
        config_path,
    )
    warnings.extend(mount_warnings)
    warnings.extend(env_warnings)
    warnings.extend(image_warnings)
    warnings.extend(agent_warnings)

    return PersonalConfigResult(
        PersonalConfig(
            version=version,
            mounts=mounts,
            environment=environment,
            image=image_config,
            agents=agents_config,
        ),
        tuple(warnings),
        config_path,
        True,
    )


def describe_personal_config(result: PersonalConfigResult) -> str:
    if not result.exists:
        return "none"
    counts = []
    if result.config.mounts:
        counts.append(f"{len(result.config.mounts)} mount(s)")
    if result.config.environment:
        counts.append(f"{len(result.config.environment)} env var(s)")
    if result.config.image.apt_packages:
        counts.append(f"{len(result.config.image.apt_packages)} apt package(s)")
    if result.config.agents.default != DEFAULT_AGENT_KIND or result.config.agents.installed != (
        DEFAULT_AGENT_KIND,
    ):
        counts.append(
            "agents: "
            f"{result.config.agents.default} default, "
            f"{len(result.config.agents.installed)} installed"
        )
    suffix = f" ({', '.join(counts)})" if counts else ""
    base = f"{result.path} schema '{result.config.version}'{suffix}"
    if result.warnings:
        return base + "; " + "; ".join(result.warnings)
    return base


def _parse_mounts(raw: object, config_path: Path) -> tuple[tuple[PersonalMount, ...], list[str]]:
    if raw is None:
        return (), []
    if not isinstance(raw, list):
        return (), [f"{config_path}: [mounts] must be an array of tables; skipping mounts"]

    mounts: list[PersonalMount] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, start=1):
        label = f"{config_path}: mounts[{index}]"
        if not isinstance(item, dict):
            warnings.append(f"{label} must be a table; skipping")
            continue
        item_id = _string_field(item, "id")
        source = _string_field(item, "source")
        target = _string_field(item, "target")
        mode = _string_field(item, "mode")
        missing = [
            name for name, value in (
                ("id", item_id),
                ("source", source),
                ("target", target),
                ("mode", mode),
            )
            if value is None
        ]
        if missing:
            warnings.append(f"{label} missing required field(s): {', '.join(missing)}; skipping")
            continue
        assert item_id is not None and source is not None and target is not None and mode is not None
        if item_id in seen:
            warnings.append(f"{label} id {item_id!r} is duplicated; skipping")
            continue
        if mode not in {"ro", "rw"}:
            warnings.append(f"{label} mode must be 'ro' or 'rw'; skipping")
            continue
        expanded_source = str(Path(source).expanduser())
        if not Path(expanded_source).is_absolute():
            warnings.append(f"{label} source must be absolute or start with '~'; skipping")
            continue
        if not target.startswith("/"):
            warnings.append(f"{label} target must be an absolute container path; skipping")
            continue
        seen.add(item_id)
        mounts.append(PersonalMount(item_id, expanded_source, target, mode))
    return tuple(mounts), warnings


def _parse_environment(
    raw: object,
    config_path: Path,
) -> tuple[tuple[PersonalEnvironment, ...], list[str]]:
    if raw is None:
        return (), []
    if not isinstance(raw, list):
        return (), [
            f"{config_path}: [environment] must be an array of tables; skipping environment"
        ]

    environment: list[PersonalEnvironment] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for index, item in enumerate(raw, start=1):
        label = f"{config_path}: environment[{index}]"
        if not isinstance(item, dict):
            warnings.append(f"{label} must be a table; skipping")
            continue
        item_id = _string_field(item, "id")
        name = _string_field(item, "name")
        value = _string_field(item, "value")
        missing = [
            field for field, field_value in (
                ("id", item_id),
                ("name", name),
                ("value", value),
            )
            if field_value is None
        ]
        if missing:
            warnings.append(f"{label} missing required field(s): {', '.join(missing)}; skipping")
            continue
        assert item_id is not None and name is not None and value is not None
        if item_id in seen_ids:
            warnings.append(f"{label} id {item_id!r} is duplicated; skipping")
            continue
        if name in seen_names:
            warnings.append(f"{label} name {name!r} is duplicated; skipping")
            continue
        if name in RESERVED_ENVIRONMENT_NAMES:
            warnings.append(f"{label} name {name!r} is reserved by Carthage; skipping")
            continue
        if not name or not all(c.isalnum() or c == "_" for c in name) or name[0].isdigit():
            warnings.append(f"{label} name {name!r} is not a valid environment variable; skipping")
            continue
        seen_ids.add(item_id)
        seen_names.add(name)
        environment.append(PersonalEnvironment(item_id, name, value))
    return tuple(environment), warnings


def _string_field(table: dict, key: str) -> str | None:
    value = table.get(key)
    if not isinstance(value, str) or value == "":
        return None
    return value


def _parse_image(raw: object, config_path: Path) -> tuple[PersonalImage, list[str]]:
    if raw is None:
        return PersonalImage(), []
    if not isinstance(raw, dict):
        return PersonalImage(), [f"{config_path}: [image] must be a table; skipping image config"]

    warnings: list[str] = []
    apt_raw = raw.get("apt_packages", [])
    if not isinstance(apt_raw, list) or not all(isinstance(item, str) for item in apt_raw):
        return PersonalImage(), [
            f"{config_path}: [image].apt_packages must be a list of strings; skipping"
        ]

    packages: list[str] = []
    seen: set[str] = set()
    for package in apt_raw:
        if not _APT_PACKAGE_RE.match(package):
            warnings.append(
                f"{config_path}: [image].apt_packages entry {package!r} is not "
                "a valid apt package name; skipping"
            )
            continue
        if package in seen:
            warnings.append(
                f"{config_path}: [image].apt_packages entry {package!r} is duplicated; skipping"
            )
            continue
        seen.add(package)
        packages.append(package)

    return PersonalImage(apt_packages=tuple(packages)), warnings


def _parse_agents(
    raw_agents: object,
    raw_agent: object,
    config_path: Path,
) -> tuple[PersonalAgents, list[str]]:
    """Parse agent selection.

    Preferred shape:

        [agents]
        installed = ["claude"]
        default = "claude"

    The issue #4 sketch used `[agent].kind`; accept it as a compatibility
    alias while converging on `[agents]` for multi-agent installs.
    """
    supported = set(supported_agent_kinds())
    warnings: list[str] = []

    if raw_agents is not None:
        if not isinstance(raw_agents, dict):
            return PersonalAgents(), [f"{config_path}: [agents] must be a table; using claude"]
        installed_raw = raw_agents.get("installed", [DEFAULT_AGENT_KIND])
        default_raw = raw_agents.get("default", DEFAULT_AGENT_KIND)
    elif raw_agent is not None:
        if not isinstance(raw_agent, dict):
            return PersonalAgents(), [f"{config_path}: [agent] must be a table; using claude"]
        kind_raw = raw_agent.get("kind", DEFAULT_AGENT_KIND)
        installed_raw = [kind_raw]
        default_raw = kind_raw
    else:
        return PersonalAgents(), []

    if not isinstance(installed_raw, list) or not all(
        isinstance(item, str) for item in installed_raw
    ):
        warnings.append(f"{config_path}: [agents].installed must be a list of strings; using claude")
        installed = [DEFAULT_AGENT_KIND]
    else:
        installed = []
        seen: set[str] = set()
        for kind in installed_raw:
            if kind in seen:
                warnings.append(f"{config_path}: agent {kind!r} is duplicated; skipping")
                continue
            if kind not in supported:
                warnings.append(f"{config_path}: agent {kind!r} is not supported; skipping")
                continue
            seen.add(kind)
            installed.append(kind)
        if not installed:
            warnings.append(f"{config_path}: no supported agents configured; using claude")
            installed = [DEFAULT_AGENT_KIND]

    if not isinstance(default_raw, str) or default_raw == "":
        warnings.append(f"{config_path}: [agents].default must be a string; using {installed[0]!r}")
        default = installed[0]
    elif default_raw not in supported:
        warnings.append(f"{config_path}: default agent {default_raw!r} is not supported; using claude")
        default = DEFAULT_AGENT_KIND
        if default not in installed:
            installed.insert(0, default)
    elif default_raw not in installed:
        warnings.append(
            f"{config_path}: default agent {default_raw!r} is not in installed agents; adding it"
        )
        default = default_raw
        installed.insert(0, default)
    else:
        default = default_raw

    return PersonalAgents(installed=tuple(installed), default=default), warnings
