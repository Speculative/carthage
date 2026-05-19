"""Pure runtime overlay objects shared by personal config and agent profiles."""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeMount:
    id: str
    source: str
    target: str
    mode: str

    @property
    def read_only(self) -> bool:
        return self.mode == "ro"


@dataclass(frozen=True)
class RuntimeEnv:
    id: str
    name: str
    value: str


@dataclass(frozen=True)
class RuntimeOverlay:
    mounts: tuple[RuntimeMount, ...] = ()
    environment: tuple[RuntimeEnv, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.mounts and not self.environment


def merge_overlays(*overlays: RuntimeOverlay) -> RuntimeOverlay:
    mounts: list[RuntimeMount] = []
    environment: list[RuntimeEnv] = []
    for overlay in overlays:
        mounts.extend(overlay.mounts)
        environment.extend(overlay.environment)
    return RuntimeOverlay(mounts=tuple(mounts), environment=tuple(environment))


def filter_disabled_overlay(
    overlay: RuntimeOverlay,
    disabled_ids: Collection[str],
) -> RuntimeOverlay:
    disabled = set(disabled_ids)
    return RuntimeOverlay(
        mounts=tuple(mount for mount in overlay.mounts if mount.id not in disabled),
        environment=tuple(item for item in overlay.environment if item.id not in disabled),
    )


def render_compose_overlay(service_name: str, overlay: RuntimeOverlay) -> str | None:
    if overlay.is_empty:
        return None

    parts: list[str] = ["services:", f"  {service_name}:"]
    if overlay.environment:
        parts.append("    environment:")
        for item in overlay.environment:
            parts.append(f"      {item.name}: {_yaml_scalar(item.value)}")
    if overlay.mounts:
        parts.append("    volumes:")
        for mount in overlay.mounts:
            parts.extend([
                "      - type: bind",
                f"        source: {_yaml_scalar(mount.source)}",
                f"        target: {_yaml_scalar(mount.target)}",
                f"        read_only: {'true' if mount.read_only else 'false'}",
            ])
    return "\n".join(parts) + "\n"


def render_compose_overlay_service_parts(overlay: RuntimeOverlay) -> list[str]:
    """Render only the service body lines for callers composing a larger
    temporary override file, such as `carthage up --port`."""
    rendered = render_compose_overlay("dev", overlay)
    if rendered is None:
        return []
    lines = rendered.splitlines()
    return lines[2:]


def _yaml_scalar(value: str) -> str:
    return json.dumps(value)
