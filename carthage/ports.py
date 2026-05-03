"""Host port collision detection for `carthage up`.

Flow:
  1. `docker compose config --format json` emits the resolved compose, with
     ports: entries expanded into `{"published": ..., "target": ...}` dicts.
  2. For each host-side `published` port, we check if another Carthage
     container (label `carthage.managed=true`) already owns it. If yes, we
     name the offending project. If not, we attempt a TCP bind; if *that*
     fails, we report "in use by a non-Carthage process."
  3. On any collision we fail loudly and do NOT auto-reassign — silent
     remapping breaks user expectations and makes "where did my port go"
     debugging harder than just refusing to start.
"""

from __future__ import annotations

import json
import socket
import subprocess
from dataclasses import dataclass

from carthage.config import CarthageConfig


@dataclass
class HostPortBinding:
    service: str
    published: int
    target: int
    protocol: str  # "tcp" / "udp"
    host_ip: str = ""  # "" means bind on all interfaces (0.0.0.0); "127.0.0.1" means localhost-only


def extract_host_ports(
    cfg: CarthageConfig,
    extra_compose_files: list[str] | None = None,
) -> list[HostPortBinding]:
    """Return the resolved host-side port bindings from the project's compose.

    `extra_compose_files`, when provided, replaces the default `-f` sequence
    so callers can include override files (e.g. the temp override that
    `carthage up --port` generates). Without it, only `.carthage/docker-compose.yaml`
    is read — which means the precheck won't see user-supplied overrides.

    Services with no `ports:` block (or only container-internal ports) contribute nothing.
    """
    if extra_compose_files:
        f_args: list[str] = []
        for path in extra_compose_files:
            f_args.extend(["-f", path])
    else:
        f_args = ["-f", str(cfg.compose_file)]
    r = subprocess.run(
        [
            "docker", "compose",
            *f_args,
            "-p", cfg.compose_project_name,
            "config", "--format", "json",
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        # If the compose file is broken, `carthage up` will fail with a clearer
        # error than ours. Return empty so we don't double-report.
        return []
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return []

    out: list[HostPortBinding] = []
    for service_name, service in (data.get("services") or {}).items():
        for p in service.get("ports") or []:
            # Ports can be shorthand strings ("3000:3000") or dicts. `config
            # --format json` normalizes to dicts, but handle both.
            if isinstance(p, str):
                # Shorthand: "3000:3000", "127.0.0.1:3000:3000", "3000:3000/tcp",
                # or "3000/tcp" (internal only). When 3 colon-separated parts,
                # the leading one is host_ip.
                if ":" not in p:
                    continue  # internal-only, no host binding
                parts = p.split(":")
                if len(parts) == 3:
                    host_ip, host_part, container_part = parts
                else:
                    host_ip = ""
                    host_part, container_part = parts
                protocol = "tcp"
                if "/" in container_part:
                    container_part, protocol = container_part.split("/", 1)
                out.append(HostPortBinding(
                    service=service_name,
                    published=int(host_part),
                    target=int(container_part),
                    protocol=protocol,
                    host_ip=host_ip,
                ))
            elif isinstance(p, dict):
                if p.get("published") is None:
                    continue
                out.append(HostPortBinding(
                    service=service_name,
                    published=int(p["published"]),
                    target=int(p.get("target", p["published"])),
                    protocol=p.get("protocol", "tcp"),
                    host_ip=p.get("host_ip", "") or "",
                ))
    return out


def carthage_owner_of_port(port: int) -> str | None:
    """Return the carthage.project label of the container already bound to
    `port` on the host, if any. None if no Carthage container owns it."""
    r = subprocess.run(
        [
            "docker", "ps",
            "--filter", "label=carthage.managed=true",
            "--format", "{{json .}}",
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ports_str = row.get("Ports", "") or ""
        # Docker formats this as e.g. "0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp"
        for chunk in ports_str.split(", "):
            chunk = chunk.strip()
            if f":{port}->" in chunk:
                labels = _parse_labels(row.get("Labels", ""))
                return labels.get("carthage.project", row.get("Names", "?"))
    return None


def _parse_labels(s: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in s.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def port_is_free(port: int, protocol: str = "tcp") -> bool:
    """Attempt to bind `port` on 127.0.0.1; return True if it succeeds.

    This is a best-effort check — racy with anything else starting right now,
    but catches the common case where another local process already owns
    the port. We test 127.0.0.1 rather than 0.0.0.0 because Docker's port
    publishing binds to 0.0.0.0 and would have already taken the port if
    a collision existed; if 127.0.0.1 is free, 0.0.0.0 likely is too.
    """
    sock_type = socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
    s = socket.socket(socket.AF_INET, sock_type)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


@dataclass
class PortConflict:
    binding: HostPortBinding
    owner: str   # human-readable description of what has the port


def find_conflicts(bindings: list[HostPortBinding]) -> list[PortConflict]:
    conflicts: list[PortConflict] = []
    for b in bindings:
        owner = carthage_owner_of_port(b.published)
        if owner:
            conflicts.append(PortConflict(b, f"Carthage project '{owner}'"))
            continue
        if not port_is_free(b.published, b.protocol):
            conflicts.append(PortConflict(b, "a non-Carthage process on this host"))
    return conflicts


def find_free_host_port(start: int, protocol: str = "tcp", limit: int = 100) -> int | None:
    """Probe upward from `start` for a host port not owned by another Carthage
    container and not bound by any local process. Returns None if nothing free
    is found within `limit` ports."""
    for candidate in range(start, start + limit):
        if candidate > 65535:
            return None
        if carthage_owner_of_port(candidate):
            continue
        if port_is_free(candidate, protocol):
            return candidate
    return None
