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

    service: dict[str, object] = {}
    if overlay.environment:
        service["environment"] = {item.name: item.value for item in overlay.environment}
    if overlay.mounts:
        service["volumes"] = [
            {
                "type": "bind",
                "source": mount.source,
                "target": mount.target,
                "read_only": mount.read_only,
            }
            for mount in overlay.mounts
        ]
    return json.dumps({"services": {service_name: service}}, indent=2) + "\n"
