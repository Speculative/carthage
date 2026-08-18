from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from carthage.commands.handoff import handoff
from carthage.personal_config import RemoteTarget
from carthage.remote import (
    RsyncSummary,
    parse_rsync_output,
    remote_carthage_command,
    remote_paths,
    resolve_remote_path,
    remote_shell_path,
    remote_terminfo_available,
    rsync_command,
)


def _cfg(tmp_path):
    return SimpleNamespace(
        project_root=tmp_path / "project",
        project_slug="demo",
        host_state_dir=tmp_path / "state" / "demo",
        base_image="ghcr.io/speculative/carthage-base:v2",
    )


def test_remote_paths_use_slug_and_state_home():
    cfg = SimpleNamespace(project_slug="demo")
    target = RemoteTarget("cloud", "cloud", "/srv/projects")

    assert remote_paths(cfg, target).project == "/srv/projects/demo"
    assert remote_paths(cfg, target).state == "~/.carthage/state/demo"


def test_remote_shell_path_expands_home_without_literal_tilde():
    assert remote_shell_path("~/.carthage/state/demo") == "$HOME/.carthage/state/demo"


def test_remote_terminfo_available_ignores_dumb(monkeypatch):
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("dumb terminals should not probe SSH")

    monkeypatch.setattr("carthage.remote.run_ssh", fake_run)
    assert remote_terminfo_available(RemoteTarget("cloud", "cloud", "/srv/projects"), "dumb")
    assert not called


def test_remote_terminfo_available_probes_host(monkeypatch):
    calls = []

    class Result:
        returncode = 1

    def fake_run(target, script, **kwargs):
        calls.append((target, script, kwargs))
        return Result()

    monkeypatch.setattr("carthage.remote.run_ssh", fake_run)
    assert not remote_terminfo_available(
        RemoteTarget("cloud", "cloud", "/srv/projects"), "xterm-ghostty"
    )
    assert "terminfo/" in calls[0][1]
    assert "xterm-ghostty" in calls[0][1]
    assert "infocmp -x xterm-ghostty" in calls[0][1]


def test_remote_carthage_command_falls_back_to_uv_tool_path():
    command = remote_carthage_command("survey", "--base-image", "ghcr.io/example:v2")

    assert 'command -v carthage' in command
    assert '$HOME/.local/bin/carthage' in command
    assert '"$CARTHAGE_BIN" survey --base-image ghcr.io/example:v2' in command


def test_resolve_remote_home_path_uses_remote_home(monkeypatch):
    target = RemoteTarget("cloud", "cloud", "/srv/projects")
    monkeypatch.setattr("carthage.remote.remote_home", lambda _target: "/home/jtao")

    assert resolve_remote_path("~/.carthage/state/demo", target) == "/home/jtao/.carthage/state/demo"


def test_rsync_command_carries_exclusions_and_direction():
    target = RemoteTarget("cloud", "cloud", "/srv/projects")

    to_remote = rsync_command("/work/demo", "/srv/projects/demo", target, dry_run=True)
    from_remote = rsync_command(
        "~/.carthage/state/demo",
        "/work/state/demo",
        target,
        dry_run=False,
        source_remote=True,
    )

    assert to_remote[-2:] == ["/work/demo/", "cloud:/srv/projects/demo/"]
    assert from_remote[-2:] == ["cloud:~/.carthage/state/demo/", "/work/state/demo/"]
    assert "--dry-run" in to_remote
    assert "--exclude=/.carthage/last-build-hash" in to_remote
    assert "--exclude=/.git/**/*.lock" in to_remote
    assert "--delete-excluded" in to_remote


def test_rsync_output_marks_replacements_and_deletes_destructive():
    summary = parse_rsync_output(
        ">f+++++++++ new.txt\n"
        ">f.st...... changed.txt\n"
        "cd+++++++++ new-dir\n"
        "*deleting old.txt\n"
    )

    assert [change.path for change in summary.destructive] == ["changed.txt", "old.txt"]
    assert [change.path for change in summary.additions] == ["new.txt", "new-dir"]


def test_handoff_requires_force_for_destructive_plan(tmp_path):
    cfg = _cfg(tmp_path)
    target = RemoteTarget("cloud", "cloud", "/srv/projects")
    runner = CliRunner()
    destructive = parse_rsync_output(">f.st...... changed.txt\n")

    with patch("carthage.commands.handoff._load_target", return_value=(cfg, target)), \
         patch("carthage.commands.handoff.remote_preflight"), \
         patch("carthage.commands.handoff.ensure_compatible_cli"), \
         patch("carthage.commands.handoff._check_destination_running"), \
         patch("carthage.commands.handoff._preview_transfer", return_value=destructive), \
         patch("carthage.commands.handoff._quiesce_source") as quiesce:
        result = runner.invoke(handoff, ["cloud"])

    assert result.exit_code == 2
    assert "--force" in result.output
    quiesce.assert_not_called()


def test_handoff_dry_run_reports_destructive_plan_without_stopping(tmp_path):
    cfg = _cfg(tmp_path)
    target = RemoteTarget("cloud", "cloud", "/srv/projects")
    runner = CliRunner()
    destructive = parse_rsync_output(">f.st...... changed.txt\n")

    with patch("carthage.commands.handoff._load_target", return_value=(cfg, target)), \
         patch("carthage.commands.handoff.remote_preflight"), \
         patch("carthage.commands.handoff.ensure_compatible_cli"), \
         patch("carthage.commands.handoff._check_destination_running"), \
         patch("carthage.commands.handoff._preview_transfer", return_value=destructive), \
         patch("carthage.commands.handoff._quiesce_source") as quiesce:
        result = runner.invoke(handoff, ["cloud", "--dry-run"])

    assert result.exit_code == 0
    assert "overwrite/delete" in result.output
    quiesce.assert_not_called()


def test_handoff_warns_when_remote_lacks_terminal_source(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    target = RemoteTarget("cloud", "cloud", "/srv/projects")
    runner = CliRunner()
    monkeypatch.setenv("TERM", "xterm-ghostty")

    with patch("carthage.commands.handoff._load_target", return_value=(cfg, target)), \
         patch("carthage.commands.handoff.remote_preflight"), \
         patch("carthage.commands.handoff.ensure_compatible_cli"), \
         patch("carthage.commands.handoff.remote_terminfo_available", return_value=False), \
         patch("carthage.commands.handoff._check_destination_running"), \
         patch("carthage.commands.handoff._preview_transfer", return_value=RsyncSummary()):
        result = runner.invoke(handoff, ["cloud", "--dry-run"])

    assert result.exit_code == 0
    assert "terminfo source" in result.output
