import json

from carthage.runtime import (
    RuntimeEnv,
    RuntimeMount,
    RuntimeOverlay,
    filter_disabled_overlay,
    merge_overlays,
    render_compose_overlay,
)


def test_merge_overlays_preserves_order():
    first = RuntimeOverlay(
        mounts=(RuntimeMount("notes", "/host/notes", "/notes", "ro"),),
        environment=(RuntimeEnv("editor", "EDITOR", "vim"),),
    )
    second = RuntimeOverlay(
        mounts=(RuntimeMount("scratch", "/tmp/scratch", "/scratch", "rw"),),
        environment=(RuntimeEnv("pager", "PAGER", "less"),),
    )

    merged = merge_overlays(first, second)

    assert [mount.id for mount in merged.mounts] == ["notes", "scratch"]
    assert [item.id for item in merged.environment] == ["editor", "pager"]


def test_filter_disabled_overlay_removes_matching_ids():
    overlay = RuntimeOverlay(
        mounts=(
            RuntimeMount("notes", "/host/notes", "/notes", "ro"),
            RuntimeMount("scratch", "/tmp/scratch", "/scratch", "rw"),
        ),
        environment=(
            RuntimeEnv("editor", "EDITOR", "vim"),
            RuntimeEnv("pager", "PAGER", "less"),
        ),
    )

    filtered = filter_disabled_overlay(overlay, {"notes", "pager"})

    assert filtered.mounts == (RuntimeMount("scratch", "/tmp/scratch", "/scratch", "rw"),)
    assert filtered.environment == (RuntimeEnv("editor", "EDITOR", "vim"),)


def test_render_compose_overlay_empty_returns_none():
    assert render_compose_overlay("dev", RuntimeOverlay()) is None


def test_render_compose_overlay_quotes_values():
    overlay = RuntimeOverlay(
        mounts=(RuntimeMount("notes", "/host path/notes", "/home/carthage/.notes", "ro"),),
        environment=(RuntimeEnv("greeting", "GREETING", 'hello "world"'),),
    )

    rendered = render_compose_overlay("dev", overlay)

    assert json.loads(rendered) == {
        "services": {
            "dev": {
                "environment": {
                    "GREETING": 'hello "world"',
                },
                "volumes": [
                    {
                        "type": "bind",
                        "source": "/host path/notes",
                        "target": "/home/carthage/.notes",
                        "read_only": True,
                    }
                ],
            }
        }
    }
