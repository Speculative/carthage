"""Opt-in remote web ingress through a personal frp server."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from carthage.runtime import RuntimeEnv, RuntimeMount, RuntimeOverlay

_DNS = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


@dataclass(frozen=True)
class WebAccess:
    server_addr: str
    domain: str
    token_file: str
    supervise: bool = False

    def validate(self, slug: str) -> None:
        if not _DNS.fullmatch(slug) or re.match(r"^[0-9]+-", slug):
            raise ValueError(f"web access requires a DNS-compatible project slug: {slug!r}")
        token = Path(self.token_file)
        if not token.is_file():
            raise ValueError(f"web access token file does not exist: {token}")

    def tunnel_dir(self, slug: str) -> Path:
        self.validate(slug)
        return Path.home() / ".carthage" / "tunnels" / slug

    def runtime_overlay(self, slug: str) -> RuntimeOverlay:
        directory = self.tunnel_dir(slug)
        return RuntimeOverlay(
            mounts=(RuntimeMount("web", str(directory / "socket"), "/run/carthage-web", "rw"),),
            environment=tuple(RuntimeEnv("web", name, value) for name, value in (
                ("CARTHAGE_WEB_HOST", f"{slug}.{self.domain}"),
                ("CARTHAGE_WEB_SUPERVISE", "1" if self.supervise else "0"),
            )),
        )

    def frpc_config(self, slug: str) -> dict:
        self.validate(slug)
        host = f"{slug}.{self.domain}"
        return {
            "serverAddr": self.server_addr, "serverPort": 7000,
            "loginFailExit": False,
            "auth": {"method": "token", "tokenSource": {"type": "file", "file": {"path": "/run/carthage-frp-token"}}},
            "transport": {"tls": {"enable": True}},
            "proxies": [{"name": slug, "type": "http", "plugin": {"type": "unix_domain_socket", "unixPath": "/run/carthage-web/router.sock"}, "customDomains": [host, "*." + host]}],
        }

    def prepare(self, slug: str) -> None:
        directory = self.tunnel_dir(slug)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        socket_dir = directory / "socket"
        socket_dir.mkdir(exist_ok=True, mode=0o700)
        socket_dir.chmod(0o700)
        config = directory / "frpc.json"
        # Atomic replacement prevents frpc from seeing a partially written file.
        fd, temporary = tempfile.mkstemp(dir=directory, prefix="frpc-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self.frpc_config(slug), stream)
            os.replace(temporary, config)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def sidecar(self, slug: str, service: str) -> dict:
        from carthage.daemon import runtime_ids

        directory = self.tunnel_dir(slug)
        uid, gid = runtime_ids()
        config_hash = hashlib.sha256(json.dumps(self.frpc_config(slug), sort_keys=True).encode()).hexdigest()
        token_stat = Path(self.token_file).stat()
        return {"carthage-frpc": {
            "image": "fatedier/frpc:v0.71.1",
            "command": ["-c", "/etc/frp/frpc.json"],
            "user": f"{uid}:{gid}",
            "depends_on": [service],
            "restart": "unless-stopped",
            # Only outbound Internet and the shared Unix socket are needed.
            # Avoid creating a Compose default network solely for this sidecar.
            "network_mode": "bridge",
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "labels": {"carthage.managed": "true", "carthage.project": slug, "carthage.role": "frpc", "carthage.tunnel-config": config_hash, "carthage.tunnel-token": f"{token_stat.st_ino}:{token_stat.st_mtime_ns}"},
            "volumes": [
                {"type": "bind", "source": str(directory / "frpc.json"), "target": "/etc/frp/frpc.json", "read_only": True},
                {"type": "bind", "source": self.token_file, "target": "/run/carthage-frp-token", "read_only": True},
                {"type": "bind", "source": str(directory / "socket"), "target": "/run/carthage-web", "read_only": True},
            ],
        }}


def parse_web(raw: object, path: Path) -> tuple[WebAccess | None, list[str]]:
    if raw is None:
        return None, []
    message = f"{path}: [web] requires server_addr, domain, an absolute token_file, and optional boolean supervise; disabling web access"
    if not isinstance(raw, dict):
        return None, [message]
    server, domain, token = (raw.get(k) for k in ("server_addr", "domain", "token_file"))
    supervise = raw.get("supervise", False)
    if not all(isinstance(v, str) and v for v in (server, domain, token)) or not isinstance(supervise, bool):
        return None, [message]
    if not all(_DNS.fullmatch(label) for label in domain.split(".")) or len(domain) > 189:
        return None, [message]
    if not all(_DNS.fullmatch(label) for label in server.split(".")):
        return None, [message]
    token = str(Path(token).expanduser())
    if not Path(token).is_absolute():
        return None, [message]
    return WebAccess(server, domain, token, supervise), []


def web_build_sources() -> dict[str, bytes]:
    """Runtime sources bundled with the CLI, also usable from a source checkout."""
    from importlib.resources import files

    packaged = files('carthage') / '_web'
    if packaged.is_dir():
        return {name: (packaged / name).read_bytes()
                for name in ('main.go', 'ports.go', 'go.mod', 'entrypoint.sh')}
    base = Path(__file__).resolve().parent.parent / 'base'
    return {
        'main.go': (base / 'web/main.go').read_bytes(),
        'ports.go': (base / 'web/ports.go').read_bytes(),
        'go.mod': (base / 'web/go.mod').read_bytes(),
        'entrypoint.sh': (base / 'entrypoint.sh').read_bytes(),
    }
