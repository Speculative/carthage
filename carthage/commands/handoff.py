"""Move a stopped Carthage workspace over SSH with rsync.

The MVP intentionally treats the transfer as an explicit user-mediated
ownership change.  It stops the source before copying, refuses destination
overwrites/deletions unless ``--force`` is supplied, and leaves the source
stopped when a later step fails.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass

import click
from rich.console import Console

from carthage import __version__, compose
from carthage.config import ConfigError, load_config
from carthage.personal_config import load_personal_config
from carthage.remote import (
    RemoteError,
    RsyncSummary,
    ensure_compatible_cli,
    find_remote,
    local_paths,
    local_project_running,
    prepare_local_state,
    prepare_remote_dirs,
    remote_down,
    remote_attach_command,
    remote_paths,
    remote_preflight,
    remote_project_exists,
    remote_project_running,
    remote_state_exists,
    remote_terminfo_available,
    remote_up,
    run_rsync,
    summarize,
)


console = Console()


@dataclass(frozen=True)
class TransferDirection:
    name: str
    source_remote: bool


HANDOFF = TransferDirection("handoff", False)
RECLAIM = TransferDirection("reclaim", True)


def _load_target(remote_name: str):
    try:
        cfg = load_config()
    except ConfigError as exc:
        raise RemoteError(str(exc)) from exc
    personal = load_personal_config()
    for warning in personal.warnings:
        console.print(f"[yellow]note:[/yellow] personal config: {warning}")
    try:
        target = find_remote(personal.config, remote_name)
    except RemoteError:
        raise
    return cfg, target


def _check_destination_running(cfg, target, direction: TransferDirection) -> None:
    if direction is HANDOFF:
        if remote_project_running(target, cfg):
            raise RemoteError(
                f"remote project '{cfg.project_slug}' has a running Carthage container; "
                "run `carthage down` on the remote before handoff"
            )
    elif local_project_running(cfg):
        raise RemoteError(
            f"local project '{cfg.project_slug}' has a running Carthage container; "
            "run `carthage down` locally before reclaim"
        )


def _source_and_destination(cfg, target, direction: TransferDirection):
    local = local_paths(cfg)
    remote = remote_paths(cfg, target)
    if direction is HANDOFF:
        return local, remote
    return remote, local


def _preview_transfer(
    cfg,
    target,
    direction: TransferDirection,
) -> RsyncSummary:
    source, destination = _source_and_destination(cfg, target, direction)
    destination_project_exists = (
        remote_project_exists(target, cfg)
        if direction is HANDOFF
        else True
    )
    destination_state_exists = (
        remote_state_exists(target, cfg)
        if direction is HANDOFF
        else cfg.host_state_dir.is_dir()
    )

    project_summary = RsyncSummary()
    if destination_project_exists:
        project_summary = run_rsync(
            source.project,
            destination.project,
            target,
            dry_run=True,
            source_remote=direction.source_remote,
        )
    state_summary = RsyncSummary()
    if destination_state_exists:
        state_summary = run_rsync(
            source.state,
            destination.state,
            target,
            dry_run=True,
            source_remote=direction.source_remote,
        )
    return project_summary.merge(state_summary)


def _print_preview(summary: RsyncSummary, direction: TransferDirection) -> None:
    console.print(f"[cyan]{direction.name} transfer plan:[/cyan]")
    console.print(summarize(summary))
    if summary.destructive:
        console.print(
            "[red]destination files would be overwritten or deleted.[/red] "
            "Re-run with --force to permit those changes."
        )


def _sync(
    cfg,
    target,
    direction: TransferDirection,
) -> None:
    source, destination = _source_and_destination(cfg, target, direction)
    if direction is HANDOFF:
        prepare_remote_dirs(target, cfg)
        prepare_local_state(cfg)
    else:
        cfg.host_state_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[cyan]syncing[/cyan] project tree…")
    run_rsync(
        source.project,
        destination.project,
        target,
        dry_run=False,
        source_remote=direction.source_remote,
    )
    console.print(f"[cyan]syncing[/cyan] project state…")
    run_rsync(
        source.state,
        destination.state,
        target,
        dry_run=False,
        source_remote=direction.source_remote,
    )


def _quiesce_source(cfg, target, direction: TransferDirection) -> None:
    console.print(f"[cyan]stopping[/cyan] {direction.name} source…")
    try:
        if direction is HANDOFF:
            compose.run(cfg, ["down"])
        else:
            remote_down(target, cfg)
    except subprocess.CalledProcessError as exc:
        raise RemoteError(f"could not stop source environment (exit {exc.returncode})") from exc


def _start_destination(cfg, target, direction: TransferDirection) -> None:
    if direction is HANDOFF:
        remote_up(target, cfg)
    else:
        try:
            subprocess.run(
                [sys.executable, "-m", "carthage", "up"],
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RemoteError(f"local destination startup failed (exit {exc.returncode})") from exc


def transfer(
    remote_name: str,
    *,
    direction: TransferDirection,
    dry_run: bool,
    no_up: bool,
    force: bool,
) -> None:
    try:
        cfg, target = _load_target(remote_name)
        if direction is RECLAIM and not remote_project_exists(target, cfg):
            raise RemoteError(
                f"remote project does not exist at {remote_paths(cfg, target).project}; "
                "run `carthage handoff` first"
            )
        if direction is RECLAIM and not remote_state_exists(target, cfg):
            raise RemoteError(
                f"remote project state does not exist at {remote_paths(cfg, target).state}; "
                "refusing to reclaim without the transferred Carthage state"
            )
        if direction is HANDOFF and remote_state_exists(target, cfg) is False:
            console.print("[yellow]note:[/yellow] remote project state directory will be initialized")

        remote_preflight(cfg, target)
        ensure_compatible_cli(target, __version__)
        if direction is HANDOFF:
            term = os.environ.get("TERM")
            if term and not remote_terminfo_available(target, term):
                console.print(
                    f"[yellow]warning:[/yellow] remote host {target.name!r} has no "
                    f"terminfo source for TERM={term!r}. `carthage attach` may "
                    "fail there; install the entry under "
                    "`~/.carthage/terminfo/` and run `carthage fortify` remotely."
                )
        _check_destination_running(cfg, target, direction)

        summary = _preview_transfer(cfg, target, direction)
        _print_preview(summary, direction)
        if dry_run:
            return
        if summary.destructive and not force:
            raise click.exceptions.Exit(2)

        _quiesce_source(cfg, target, direction)
        _sync(cfg, target, direction)
        if no_up:
            console.print(
                f"[green]{direction.name} complete.[/green] Both environments are stopped; "
                "start the destination with `carthage up`."
            )
            return

        console.print("[cyan]starting destination…[/cyan]")
        _start_destination(cfg, target, direction)
        console.print(f"[green]{direction.name} complete.[/green]")
        if direction is HANDOFF:
            paths = remote_paths(cfg, target)
            console.print(f"Attach remotely with: [bold]{remote_attach_command(target, cfg)}[/bold]")
        else:
            console.print("Attach locally with: [bold]carthage attach[/bold]")
    except click.exceptions.Exit:
        raise
    except (ConfigError, RemoteError, OSError, subprocess.CalledProcessError) as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise click.exceptions.Exit(1) from exc


@click.command()
@click.argument("remote_name")
@click.option("--dry-run", is_flag=True, help="Show the transfer plan without stopping or copying anything.")
@click.option("--no-up", is_flag=True, help="Transfer the workspace but leave the destination stopped.")
@click.option("--force", is_flag=True, help="Permit destination files to be overwritten or deleted.")
def handoff(remote_name: str, dry_run: bool, no_up: bool, force: bool) -> None:
    """Stop locally, copy the workspace to REMOTE, and start it there."""
    transfer(remote_name, direction=HANDOFF, dry_run=dry_run, no_up=no_up, force=force)


@click.command()
@click.argument("remote_name")
@click.option("--dry-run", is_flag=True, help="Show the transfer plan without stopping or copying anything.")
@click.option("--no-up", is_flag=True, help="Transfer the workspace but leave the destination stopped.")
@click.option("--force", is_flag=True, help="Permit destination files to be overwritten or deleted.")
def reclaim(remote_name: str, dry_run: bool, no_up: bool, force: bool) -> None:
    """Stop REMOTE, copy its workspace back locally, and start it here."""
    transfer(remote_name, direction=RECLAIM, dry_run=dry_run, no_up=no_up, force=force)
