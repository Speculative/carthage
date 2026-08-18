"""User-level Carthage config.

Personal config is intentionally softer than project config: a malformed
`~/.carthage/config.toml` should not break every Carthage project on the
machine. Callers get defaults plus warnings they can surface to the user.
Remote targets are also personal because their SSH aliases and destination
paths are host-specific.
"""

from __future__ import annotations

import sys
import re
import os
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - fallback for 3.10
    import tomli as tomllib

from carthage.agents import supported_agent_kinds
from carthage.runtime import RuntimeEnv, RuntimeMount, RuntimeOverlay


CURRENT_PERSONAL_SCHEMA_VERSION = 1
MIN_READABLE_PERSONAL_SCHEMA_VERSION = 1
PERSONAL_CONFIG_ENV = "CARTHAGE_PERSONAL_CONFIG"
RESERVED_ENVIRONMENT_NAMES = frozenset({
    "CARTHAGE",
    "CARTHAGE_PROJECT",
    "CARTHAGE_AGENT_BINARY",
    "CARTHAGE_AGENT_COMMAND",
    "CARTHAGE_AGENT_KIND",
    "CARTHAGE_AGENT_WINDOW",
    "CARTHAGE_TMUX_SESSION",
    "CARTHAGE_TMUX_COMMAND",
    "CARTHAGE_TMUX_WINDOW",
})
_APT_PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+.-]*$")
_FILE_MODE_RE = re.compile(r"^0?[0-7]{3}$")


PersonalMount = RuntimeMount
PersonalEnvironment = RuntimeEnv


@dataclass(frozen=True)
class PersonalImage:
    apt_packages: tuple[str, ...] = ()
    terminfo_files: tuple[str, ...] = ()
    files: tuple["PersonalImageFile", ...] = ()


@dataclass(frozen=True)
class PersonalImageFile:
    source: str
    target: str
    mode: str = "0644"


@dataclass(frozen=True)
class PersonalAgents:
    installed: tuple[str, ...] = ()
    default: str | None = None


@dataclass(frozen=True)
class PersonalLaunch:
    """The application started in the project's main tmux window."""

    command: str | None = None
    window: str | None = None


@dataclass(frozen=True)
class RemoteTarget:
    """An SSH host and destination root for workspace handoff."""

    name: str
    ssh: str
    projects_dir: str


@dataclass(frozen=True)
class PersonalConfig:
    schema_version: int = CURRENT_PERSONAL_SCHEMA_VERSION
    mounts: tuple[PersonalMount, ...] = ()
    environment: tuple[PersonalEnvironment, ...] = ()
    image: PersonalImage = field(default_factory=PersonalImage)
    agents: PersonalAgents = field(default_factory=PersonalAgents)
    launch: PersonalLaunch = field(default_factory=PersonalLaunch)
    remotes: tuple[RemoteTarget, ...] = ()

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
    configured = os.environ.get(PERSONAL_CONFIG_ENV)
    if configured:
        return Path(configured).expanduser()
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

    raw_version = table.get("schema_version", table.get("personal_config_version"))
    if raw_version is None:
        warnings.append(
            f"{config_path}: missing [carthage].schema_version; "
            f"defaulting to {CURRENT_PERSONAL_SCHEMA_VERSION}"
        )
        schema_version = CURRENT_PERSONAL_SCHEMA_VERSION
    else:
        schema_version = _coerce_version_int(raw_version)
        if schema_version is None:
            return PersonalConfigResult(
                default,
                (
                    f"{config_path}: [carthage].schema_version must be an integer; "
                    "ignoring personal config",
                ),
                config_path,
                True,
            )

    if schema_version < MIN_READABLE_PERSONAL_SCHEMA_VERSION:
        return PersonalConfigResult(
            default,
            (
                f"{config_path}: schema_version {schema_version} is older "
                f"than this CLI can read; ignoring personal config",
            ),
            config_path,
            True,
        )
    if schema_version > CURRENT_PERSONAL_SCHEMA_VERSION:
        return PersonalConfigResult(
            default,
            (
                f"{config_path}: schema_version {schema_version} is newer "
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
    launch_config, launch_warnings = _parse_launch(data.get("launch"), config_path)
    remotes_config, remote_warnings = _parse_remotes(data.get("remotes"), config_path)
    warnings.extend(mount_warnings)
    warnings.extend(env_warnings)
    warnings.extend(image_warnings)
    warnings.extend(agent_warnings)
    warnings.extend(launch_warnings)
    warnings.extend(remote_warnings)

    return PersonalConfigResult(
        PersonalConfig(
            schema_version=schema_version,
            mounts=mounts,
            environment=environment,
            image=image_config,
            agents=agents_config,
            launch=launch_config,
            remotes=remotes_config,
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
    if result.config.image.terminfo_files:
        counts.append(f"{len(result.config.image.terminfo_files)} terminfo file(s)")
    if result.config.image.files:
        counts.append(f"{len(result.config.image.files)} image file(s)")
    if result.config.agents.default is not None or result.config.agents.installed:
        counts.append(
            "agents: "
            f"{result.config.agents.default or 'none'} default, "
            f"{len(result.config.agents.installed)} installed"
        )
    if result.config.launch.command is not None:
        counts.append(f"launch: {result.config.launch.window or 'default'}")
    if result.config.remotes:
        counts.append(f"{len(result.config.remotes)} remote(s)")
    suffix = f" ({', '.join(counts)})" if counts else ""
    base = f"{result.path} schema {result.config.schema_version}{suffix}"
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
            name for name, value in (("source", source), ("target", target), ("mode", mode))
            if value is None
        ]
        if missing:
            warnings.append(f"{label} missing required field(s): {', '.join(missing)}; skipping")
            continue
        assert source is not None and target is not None and mode is not None
        if item_id is not None and item_id in seen:
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
        if item_id is not None:
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


def _coerce_version_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


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
    terminfo_raw = raw.get("terminfo_files", [])
    if not isinstance(terminfo_raw, list) or not all(isinstance(item, str) for item in terminfo_raw):
        return PersonalImage(), [
            f"{config_path}: [image].terminfo_files must be a list of strings; skipping"
        ]
    files_raw = raw.get("files", [])
    if not isinstance(files_raw, list):
        return PersonalImage(), [
            f"{config_path}: [image].files must be an array of tables; skipping image config"
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

    terminfo_files: list[str] = []
    seen_files: set[str] = set()
    for source in terminfo_raw:
        expanded_source = str(Path(source).expanduser())
        if not Path(expanded_source).is_absolute():
            warnings.append(
                f"{config_path}: [image].terminfo_files entry {source!r} must be absolute "
                "or start with '~'; skipping"
            )
            continue
        if expanded_source in seen_files:
            warnings.append(
                f"{config_path}: [image].terminfo_files entry {source!r} is duplicated; skipping"
            )
            continue
        seen_files.add(expanded_source)
        terminfo_files.append(expanded_source)

    image_files: list[PersonalImageFile] = []
    seen_targets: set[str] = set()
    for index, item in enumerate(files_raw, start=1):
        label = f"{config_path}: [image].files[{index}]"
        if not isinstance(item, dict):
            warnings.append(f"{label} must be a table; skipping")
            continue
        source = _string_field(item, "source")
        target = _string_field(item, "target")
        mode = item.get("mode", "0644")
        if source is None or target is None:
            missing = [
                name for name, value in (("source", source), ("target", target))
                if value is None
            ]
            warnings.append(f"{label} missing required field(s): {', '.join(missing)}; skipping")
            continue
        if not isinstance(mode, str) or not _FILE_MODE_RE.match(mode):
            warnings.append(f"{label}.mode must be an octal file mode such as '0755'; skipping")
            continue
        expanded_source = str(Path(source).expanduser())
        if not Path(expanded_source).is_absolute():
            warnings.append(
                f"{label}.source must be absolute or start with '~'; skipping"
            )
            continue
        if not target.startswith("/") or target == "/":
            warnings.append(f"{label}.target must be an absolute non-root path; skipping")
            continue
        if ".." in Path(target).parts:
            warnings.append(f"{label}.target must not contain '..'; skipping")
            continue
        if target in seen_targets:
            warnings.append(f"{label}.target {target!r} is duplicated; skipping")
            continue
        seen_targets.add(target)
        image_files.append(PersonalImageFile(expanded_source, target, mode))

    return PersonalImage(
        apt_packages=tuple(packages),
        terminfo_files=tuple(terminfo_files),
        files=tuple(image_files),
    ), warnings


def _parse_launch(raw: object, config_path: Path) -> tuple[PersonalLaunch, list[str]]:
    if raw is None:
        return PersonalLaunch(), []
    if not isinstance(raw, dict):
        return PersonalLaunch(), [
            f"{config_path}: [launch] must be a table; skipping launch config"
        ]

    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        return PersonalLaunch(), [
            f"{config_path}: [launch].command must be a non-empty string; skipping launch config"
        ]
    command = command.strip()

    window = raw.get("window")
    if window is None:
        window = command.split()[0]
    elif not isinstance(window, str) or not window.strip():
        return PersonalLaunch(), [
            f"{config_path}: [launch].window must be a non-empty string; skipping launch config"
        ]
    else:
        window = window.strip()

    return PersonalLaunch(command=command, window=window), []


def _parse_remotes(
    raw: object,
    config_path: Path,
) -> tuple[tuple[RemoteTarget, ...], list[str]]:
    if raw is None:
        return (), []
    if not isinstance(raw, dict):
        return (), [f"{config_path}: [remotes] must be a table; skipping remotes"]

    remotes: list[RemoteTarget] = []
    warnings: list[str] = []
    for name, item in raw.items():
        label = f"{config_path}: [remotes.{name}]"
        if not isinstance(name, str) or not name.strip():
            warnings.append(f"{label} must have a non-empty name; skipping")
            continue
        if not isinstance(item, dict):
            warnings.append(f"{label} must be a table; skipping")
            continue
        ssh = _string_field(item, "ssh")
        projects_dir = _string_field(item, "projects_dir")
        missing = [
            field for field, value in (("ssh", ssh), ("projects_dir", projects_dir))
            if value is None
        ]
        if missing:
            warnings.append(f"{label} missing required field(s): {', '.join(missing)}; skipping")
            continue
        assert ssh is not None and projects_dir is not None
        if (
            ssh.startswith("-")
            or any(char.isspace() or ord(char) == 0 for char in ssh)
            or not all(char.isalnum() or char in "._@:-[]" for char in ssh)
        ):
            warnings.append(
                f"{label}.ssh must be an SSH alias or user@host using safe host characters; skipping"
            )
            continue
        if not Path(projects_dir).is_absolute() or "\x00" in projects_dir:
            warnings.append(f"{label}.projects_dir must be an absolute path; skipping")
            continue
        remotes.append(RemoteTarget(name.strip(), ssh, projects_dir))
    return tuple(remotes), warnings


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
            return PersonalAgents(), [f"{config_path}: [agents] must be a table; ignoring agents"]
        installed_raw = raw_agents.get("installed", [])
        default_raw = raw_agents.get("default")
    elif raw_agent is not None:
        if not isinstance(raw_agent, dict):
            return PersonalAgents(), [f"{config_path}: [agent] must be a table; ignoring agent"]
        kind_raw = raw_agent.get("kind")
        installed_raw = [kind_raw]
        default_raw = kind_raw
    else:
        return PersonalAgents(), []

    if not isinstance(installed_raw, list) or not all(
        isinstance(item, str) for item in installed_raw
    ):
        warnings.append(f"{config_path}: [agents].installed must be a list of strings; ignoring it")
        installed = []
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
            warnings.append(f"{config_path}: no supported agents configured")

    if default_raw is None:
        default = None
        warnings.append(f"{config_path}: no default agent configured; run `carthage agent use`")
    elif not isinstance(default_raw, str) or default_raw == "":
        warnings.append(f"{config_path}: [agents].default must be a non-empty string")
        default = None
    elif default_raw not in supported:
        warnings.append(f"{config_path}: default agent {default_raw!r} is not supported")
        default = None
    elif default_raw not in installed:
        warnings.append(
            f"{config_path}: default agent {default_raw!r} is not in installed agents; adding it"
        )
        default = default_raw
        installed.insert(0, default)
    else:
        default = default_raw

    return PersonalAgents(installed=tuple(installed), default=default), warnings
