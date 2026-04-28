"""E2E test — verify the CLI tools we ship in carthage-base actually work.

Runs `docker run` directly against the locally-built `carthage-base:e2e`
image (no compose, no project fixture) and asserts:

  - bat / delta / fzf / vim / git / ripgrep / tmux / less are on PATH
  - bat is wired up under the `bat` name (not just batcat)
  - system gitconfig sets delta as core.pager
  - fzf's bash key-bindings load and install Ctrl-R history search
  - PAGER=less is set in the image env

We piggyback on the session-scoped `base_image` fixture in conftest.py,
which builds the image once per pytest run.
"""

from __future__ import annotations

import subprocess

import pytest


pytestmark = pytest.mark.e2e


def _run(base_image: str, *argv: str) -> subprocess.CompletedProcess:
    """Run a one-shot command in the base image and return the result."""
    return subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", argv[0], base_image, *argv[1:]],
        capture_output=True, text=True,
    )


@pytest.mark.parametrize("tool", ["bat", "delta", "fzf", "vim", "git", "rg", "tmux", "less"])
def test_tool_on_path(base_image: str, tool: str) -> None:
    r = _run(base_image, "which", tool)
    assert r.returncode == 0 and r.stdout.strip(), (
        f"{tool} not on PATH: rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}"
    )


def test_bat_runs_under_bat_name(base_image: str) -> None:
    """The `bat` symlink should resolve and produce versioned output."""
    r = _run(base_image, "bat", "--version")
    assert r.returncode == 0, f"bat --version failed: {r.stderr}"
    assert r.stdout.lower().startswith("bat"), f"unexpected version output: {r.stdout!r}"


def test_delta_runs(base_image: str) -> None:
    r = _run(base_image, "delta", "--version")
    assert r.returncode == 0, f"delta --version failed: {r.stderr}"
    assert "delta" in r.stdout.lower(), f"unexpected version output: {r.stdout!r}"


def test_system_gitconfig_pager_is_delta(base_image: str) -> None:
    r = _run(base_image, "git", "config", "--system", "--get", "core.pager")
    assert r.returncode == 0, f"git config --system core.pager failed: {r.stderr}"
    assert r.stdout.strip() == "delta", f"core.pager: {r.stdout!r}"


@pytest.mark.parametrize("var,expected", [
    ("PAGER", "less"),
    ("EDITOR", "vim"),
    ("VISUAL", "vim"),
    ("CLAUDE_CODE_NO_FLICKER", "1"),
])
def test_env_var(base_image: str, var: str, expected: str) -> None:
    r = _run(base_image, "sh", "-c", f"echo ${var}")
    assert r.returncode == 0
    assert r.stdout.strip() == expected, f"{var}: {r.stdout!r}"


def test_fzf_keybindings_load_in_interactive_bash(base_image: str) -> None:
    """An interactive bash should auto-load fzf bindings via /etc/bash.bashrc.

    `bind -p` doesn't work without a real TTY, so we instead verify that the
    keybindings script sourced cleanly: it defines a `__fzf_history__`
    function and binds it to Ctrl-R via `bind -m emacs-standard ...`. The
    function's presence after `bash -i` means our /etc/bash.bashrc snippet
    fired and the script ran without errors.
    """
    r = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "bash", base_image,
         "-i", "-c", "type __fzf_history__"],
        capture_output=True, text=True,
    )
    # `bash -i` warns "cannot set terminal process group" on a non-tty; ignore stderr.
    assert r.returncode == 0, (
        f"__fzf_history__ not defined after `bash -i`: "
        f"rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert "function" in r.stdout, f"unexpected `type` output: {r.stdout!r}"


def test_fzf_keybindings_file_present(base_image: str) -> None:
    """Sanity check: the dpkg path-include kept the keybindings script."""
    r = _run(base_image, "test", "-f", "/usr/share/doc/fzf/examples/key-bindings.bash")
    assert r.returncode == 0, "fzf key-bindings.bash missing — dpkg path-include not applied?"
