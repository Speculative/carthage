#!/usr/bin/env bash
# carthage-entrypoint — PID 1 inside every Carthage container.
#
# Runs as the `carthage` user (the image sets `USER carthage` so we never need
# root at runtime). Starts a detached tmux server with a `claude` session so
# `carthage attach` always has something to attach to. Finally execs the
# container CMD (default: `sleep infinity`).
#
# Note: there is no network-lockdown step here. Carthage does not isolate the
# container from the host LAN; see README "Security model" for the rationale.
set -euo pipefail

log() { printf '[carthage-entrypoint] %s\n' "$*" >&2; }

# Ensure ~/.claude exists. On typical invocations the host bind-mount provides
# this; if the image is run without the mount, we still want Claude Code to
# have a writable config dir.
if [ ! -d "${HOME:-/home/carthage}/.claude" ]; then
    mkdir -p "${HOME:-/home/carthage}/.claude"
fi

# --- tmux session for Claude Code ------------------------------------------
# `claude --dangerously-skip-permissions` is appropriate here because the
# sandbox *is* the permission boundary — prompting inside adds no safety.
if ! tmux has-session -t claude 2>/dev/null; then
    # `exec` so the shell tmux spawns replaces itself with claude in place.
    # Without `exec`, the pane's process group leader is bash and tmux's
    # auto-rename reports the window as "bash" instead of "claude".
    tmux new-session -d -s claude \
        'exec claude --dangerously-skip-permissions'
    log "started tmux session 'claude'"
else
    log "tmux session 'claude' already exists"
fi

# --- Exec the CMD ----------------------------------------------------------
if [ "$#" -eq 0 ]; then
    set -- sleep infinity
fi

log "exec'ing: $*"
exec "$@"
