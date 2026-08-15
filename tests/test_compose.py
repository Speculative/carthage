from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from carthage import compose


def test_exec_interactive_forwards_host_terminal_capabilities(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-ghostty")
    monkeypatch.setenv("COLORTERM", "truecolor")
    cfg = SimpleNamespace(
        compose_file=Path(".carthage/docker-compose.yaml"),
        compose_project_name="carthage-demo",
    )

    with patch("carthage.compose.subprocess.call", return_value=0) as call:
        result = compose.exec_interactive(cfg, "dev", ["tmux", "attach-session"])

    assert result == 0
    command = call.call_args.args[0]
    assert command == [
        "docker", "compose", "-f", ".carthage/docker-compose.yaml", "-p", "carthage-demo",
        "exec", "--env", "TERM=xterm-ghostty", "--env", "COLORTERM=truecolor",
        "dev", "tmux", "attach-session",
    ]
