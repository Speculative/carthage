"""SSH/rsync primitives for moving a Carthage workspace.

The first remote workflow is deliberately a stopped-source mirror.  This
module keeps subprocess construction and rsync-plan interpretation separate
from the Click commands so the safety gate can be tested without SSH or
Docker.
"""

from __future__ import annotations

import re
import shutil
import shlex
import subprocess
from dataclasses import dataclass

from carthage.config import CarthageConfig
from carthage.personal_config import RemoteTarget


class RemoteError(RuntimeError):
    """The remote target or transfer operation is not usable."""


RSYNC_EXCLUDES = (
    "/.carthage/last-build-hash",
    "/.git/*.lock",
    "/.git/**/*.lock",
)


@dataclass(frozen=True)
class RsyncChange:
    code: str
    path: str
    raw: str

    @property
    def destructive(self) -> bool:
        if self.raw.startswith("*deleting"):
            return True
        # Content/symlink/hard-link replacements overwrite destination data;
        # directory metadata updates and newly-created entries do not.
        if not self.code or self.code[0] not in "> <c":
            return False
        code = self.code.replace(" ", "")
        if len(code) < 2 or code[1] not in "fLh":
            return False
        return "+" not in code


@dataclass(frozen=True)
class RsyncSummary:
    changes: tuple[RsyncChange, ...] = ()

    @property
    def destructive(self) -> tuple[RsyncChange, ...]:
        return tuple(change for change in self.changes if change.destructive)

    @property
    def additions(self) -> tuple[RsyncChange, ...]:
        return tuple(change for change in self.changes if not change.destructive)

    @property
    def is_empty(self) -> bool:
        return not self.changes

    def merge(self, other: "RsyncSummary") -> "RsyncSummary":
        return RsyncSummary(self.changes + other.changes)


@dataclass(frozen=True)
class TransferPaths:
    project: str
    state: str


def find_remote(config, name: str) -> RemoteTarget:
    for target in config.remotes:
        if target.name == name:
            return target
    available = ", ".join(remote.name for remote in config.remotes) or "none"
    raise RemoteError(f"unknown remote {name!r}; configured remotes: {available}")


def remote_paths(cfg: CarthageConfig, target: RemoteTarget) -> TransferPaths:
    projects_root = target.projects_dir.rstrip("/") or "/"
    project = f"{projects_root}/{cfg.project_slug}" if projects_root != "/" else f"/{cfg.project_slug}"
    return TransferPaths(project=project, state=f"~/.carthage/state/{cfg.project_slug}")


def local_paths(cfg: CarthageConfig) -> TransferPaths:
    return TransferPaths(
        project=str(cfg.project_root),
        state=str(cfg.host_state_dir),
    )


def _require_local_tools() -> None:
    missing = [name for name in ("ssh", "rsync") if shutil.which(name) is None]
    if missing:
        raise RemoteError(
            "required local tool(s) missing: " + ", ".join(missing) + ". "
            "Install OpenSSH and rsync before using handoff/reclaim."
        )


def run_ssh(
    target: RemoteTarget,
    script: str,
    *,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    _require_local_tools()
    result = subprocess.run(
        ["ssh", target.ssh, script],
        check=False,
        capture_output=capture,
        text=True,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "remote command failed").strip()
        raise RemoteError(f"SSH command failed on {target.name}: {detail}")
    return result


def remote_shell_path(path: str) -> str:
    if path.startswith("~/"):
        # Keep HOME outside the quoted portion so the remote shell expands it;
        # quoting the entire path would create a literal directory named '~'.
        return "$HOME/" + shlex.quote(path[2:])
    return shlex.quote(path)


def remote_carthage_command(*args: str) -> str:
    """Invoke Carthage from PATH or the standard uv tool location."""
    rendered_args = " ".join(shlex.quote(arg) for arg in args)
    return (
        'CARTHAGE_BIN="$(command -v carthage || true)"; '
        'if test -z "$CARTHAGE_BIN"; then CARTHAGE_BIN="$HOME/.local/bin/carthage"; fi; '
        'test -x "$CARTHAGE_BIN"; '
        f'"$CARTHAGE_BIN" {rendered_args}'
    )


def remote_preflight(cfg: CarthageConfig, target: RemoteTarget) -> None:
    """Check the remote host without touching the project tree."""
    script = (
        "set -eu; "
        "command -v rsync >/dev/null; "
        "command -v docker >/dev/null; "
        "docker info >/dev/null; "
        "docker compose version >/dev/null; "
        f"test -d {remote_shell_path(target.projects_dir)}; "
        f"test -w {remote_shell_path(target.projects_dir)}; "
        f"{remote_carthage_command('--version')}; "
        f"{remote_carthage_command('survey', '--base-image', cfg.base_image)}"
    )
    result = run_ssh(target, script, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "remote preflight failed").strip()
        raise RemoteError(
            f"remote preflight failed for {target.name}: {detail}. "
            "Check SSH, Docker/Compose, Carthage (including ~/.local/bin), rsync, "
            "and `carthage survey` on the server."
        )


def remote_terminfo_available(target: RemoteTarget, term: str | None) -> bool:
    """Return whether the remote can provide a terminfo source for ``term``.

    Personal image configuration is intentionally host-local. The common
    Carthage convention is ``~/.carthage/terminfo/<TERM>``; an existing host
    database entry is also useful because it can be exported with
    ``infocmp``. This is advisory only: a prebuilt personal image may already
    contain the entry even when neither source is present now.
    """
    if not term or term in {"dumb", "unknown"}:
        return True
    quoted = shlex.quote(term)
    script = (
        "if test -r \"$HOME/.carthage/terminfo/\""
        f"{quoted}; then exit 0; fi; "
        f"command -v infocmp >/dev/null 2>&1 && infocmp -x {quoted} >/dev/null 2>&1"
    )
    result = run_ssh(target, script, check=False)
    return result.returncode == 0


def remote_cli_version(target: RemoteTarget) -> str:
    result = run_ssh(target, remote_carthage_command("--version"), check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "could not read remote Carthage version").strip()
        raise RemoteError(detail)
    return (result.stdout or "").strip().splitlines()[0] if result.stdout else ""


def ensure_compatible_cli(target: RemoteTarget, local_version: str) -> None:
    remote_version = remote_cli_version(target)
    local_match = re.search(r"(\d+)", local_version)
    remote_match = re.search(r"(\d+)", remote_version)
    if not local_match or not remote_match or local_match.group(1) != remote_match.group(1):
        raise RemoteError(
            f"incompatible Carthage versions: local {local_version!r}, "
            f"remote {remote_version!r}; install the same major version on both hosts"
        )


def remote_project_exists(target: RemoteTarget, cfg: CarthageConfig) -> bool:
    paths = remote_paths(cfg, target)
    result = run_ssh(target, f"test -d {remote_shell_path(paths.project)}", check=False)
    return result.returncode == 0


def remote_state_exists(target: RemoteTarget, cfg: CarthageConfig) -> bool:
    paths = remote_paths(cfg, target)
    result = run_ssh(target, f"test -d {remote_shell_path(paths.state)}", check=False)
    return result.returncode == 0


def remote_home(target: RemoteTarget) -> str:
    result = run_ssh(target, 'printf "%s" "$HOME"', check=False)
    if result.returncode != 0 or not (result.stdout or "").strip():
        detail = (result.stderr or "could not determine remote HOME").strip()
        raise RemoteError(f"could not determine remote home on {target.name}: {detail}")
    return (result.stdout or "").strip()


def resolve_remote_path(path: str, target: RemoteTarget) -> str:
    if path.startswith("~/"):
        return remote_home(target) + path[1:]
    return path


def remote_project_running(target: RemoteTarget, cfg: CarthageConfig) -> bool:
    # Labels are available even when the destination path is incomplete and
    # prevent an unrelated same-slug container from becoming a second writer.
    script = (
        "docker ps --filter label=carthage.managed=true "
        f"--filter label=carthage.project={shlex.quote(cfg.project_slug)} "
        "--format '{{.ID}}'"
    )
    result = run_ssh(target, script, check=False)
    return bool((result.stdout or "").strip())


def local_project_running(cfg: CarthageConfig) -> bool:
    result = subprocess.run(
        [
            "docker", "ps",
            "--filter", "label=carthage.managed=true",
            "--filter", f"label=carthage.project={cfg.project_slug}",
            "--format", "{{.ID}}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RemoteError(
            (result.stderr or "docker ps failed").strip()
        )
    return bool((result.stdout or "").strip())


def prepare_remote_dirs(target: RemoteTarget, cfg: CarthageConfig) -> None:
    paths = remote_paths(cfg, target)
    run_ssh(
        target,
        "mkdir -p "
        f"{remote_shell_path(paths.project)} "
        f"{remote_shell_path(paths.state)}",
    )


def prepare_local_state(cfg: CarthageConfig) -> None:
    cfg.host_state_dir.mkdir(parents=True, exist_ok=True)


def rsync_command(
    source: str,
    destination: str,
    target: RemoteTarget | None,
    *,
    dry_run: bool,
    source_remote: bool = False,
) -> list[str]:
    options = [
        "rsync",
        "--archive",
        "--no-owner",
        "--no-group",
        "--no-devices",
        "--no-specials",
        "--delete",
        "--delete-excluded",
        "--protect-args",
        "--itemize-changes",
        "--out-format=%i %n%L",
    ]
    for pattern in RSYNC_EXCLUDES:
        options.append(f"--exclude={pattern}")
    if dry_run:
        options.append("--dry-run")

    source_arg = source.rstrip("/") + "/"
    destination_arg = destination.rstrip("/") + "/"
    if target is not None and source_remote:
        source_arg = f"{target.ssh}:{source_arg}"
    elif target is not None:
        destination_arg = f"{target.ssh}:{destination_arg}"
    return [*options, source_arg, destination_arg]


def parse_rsync_output(output: str) -> RsyncSummary:
    changes: list[RsyncChange] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        if line.startswith("*deleting "):
            changes.append(RsyncChange("*deleting", line[len("*deleting "):], line))
            continue
        if " " not in line:
            continue
        code, path = line.split(" ", 1)
        changes.append(RsyncChange(code, path, line))
    return RsyncSummary(tuple(changes))


def run_rsync(
    source: str,
    destination: str,
    target: RemoteTarget | None,
    *,
    dry_run: bool,
    source_remote: bool = False,
) -> RsyncSummary:
    _require_local_tools()
    if target is not None:
        if source_remote:
            source = resolve_remote_path(source, target)
        else:
            destination = resolve_remote_path(destination, target)
    command = rsync_command(
        source,
        destination,
        target,
        dry_run=dry_run,
        source_remote=source_remote,
    )
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "rsync failed").strip()
        raise RemoteError(f"rsync failed: {detail}")
    return parse_rsync_output(result.stdout or "")


def shell_cd_command(path: str, command: str) -> str:
    return f"cd {remote_shell_path(path)} && {command}"


def remote_down(target: RemoteTarget, cfg: CarthageConfig) -> None:
    paths = remote_paths(cfg, target)
    run_ssh(target, shell_cd_command(paths.project, remote_carthage_command("down")))


def remote_up(target: RemoteTarget, cfg: CarthageConfig) -> None:
    paths = remote_paths(cfg, target)
    run_ssh(target, shell_cd_command(paths.project, remote_carthage_command("up")), capture=False)


def remote_attach_command(target: RemoteTarget, cfg: CarthageConfig) -> str:
    paths = remote_paths(cfg, target)
    return (
        f"ssh -t {shlex.quote(target.ssh)} "
        f"{shlex.quote(shell_cd_command(paths.project, remote_carthage_command('attach')))}"
    )


def summarize(summary: RsyncSummary, *, limit: int = 12) -> str:
    if summary.is_empty:
        return "no file changes"
    destructive = summary.destructive
    additions = summary.additions
    lines = [
        f"{len(summary.changes)} change(s): {len(additions)} add/update, "
        f"{len(destructive)} overwrite/delete",
    ]
    for change in summary.changes[:limit]:
        marker = "!" if change.destructive else "+"
        lines.append(f"  {marker} {change.path}")
    if len(summary.changes) > limit:
        lines.append(f"  … {len(summary.changes) - limit} more")
    return "\n".join(lines)
