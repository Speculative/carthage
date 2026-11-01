#!/usr/bin/env bash
# carthage-entrypoint — PID 1 inside every Carthage container.
#
# Runs as the `carthage` user, mapped to the unprivileged host account under
# rootless Docker. Starts a detached tmux server with the configured agent so
# `carthage attach` always has something to attach to. Finally execs the
# container CMD (default: `sleep infinity`).
#
# Note: there is no network-lockdown step here. Carthage does not isolate the
# container from the host LAN; see README "Security model" for the rationale.
set -euo pipefail

log() { printf '[carthage-entrypoint] %s\n' "$*" >&2; }

tmux_session="${CARTHAGE_TMUX_SESSION:-carthage-session}"
# CARTHAGE_TMUX_* is the generic foreground-app interface. Fall back to the
# older agent names so existing images/configs keep working unchanged.
agent_window="${CARTHAGE_TMUX_WINDOW:-${CARTHAGE_AGENT_WINDOW:-shell}}"
agent_command="${CARTHAGE_TMUX_COMMAND:-${CARTHAGE_AGENT_COMMAND:-bash -l}}"
agent_update_command="${CARTHAGE_AGENT_UPDATE_COMMAND:-}"
tmux_agent_command="bash -lc '${agent_command}; exec bash -l'"

if [ "${CARTHAGE_AGENT_AUTO_UPDATE:-0}" = "1" ] && [ -n "${agent_update_command}" ]; then
    log "updating agent with: ${agent_update_command}"
    if ${agent_update_command}; then
        log "agent update complete"
    else
        log "agent update failed; continuing"
    fi
fi

# Initialize before opening an Ostraka TUI so it can own the supervisor without
# stopping at its first-project prompt. The web process also handles restarts.
if [ -n "${CARTHAGE_WEB_HOST:-}" ] && [ ! -d .ostraka ]; then
    ostraka init
fi

# --- tmux session for the configured agent ---------------------------------
# Agent auto-approval flags are appropriate here because the sandbox *is* the
# permission boundary — prompting inside adds no safety.
if ! tmux has-session -t "${tmux_session}" 2>/dev/null; then
    # `exec` so the shell tmux spawns replaces itself with the agent in place.
    # Without `exec`, the pane's process group leader is bash and tmux's
    # auto-rename reports the window as "bash" instead of the agent binary.
    tmux new-session -d -s "${tmux_session}" -n "${agent_window}" \
        "${tmux_agent_command}"
    log "started tmux session '${tmux_session}' with window '${agent_window}'"
elif ! tmux list-windows -t "${tmux_session}" -F '#{window_name}' \
        | grep -Fx -- "${agent_window}" >/dev/null; then
    tmux new-window -d -t "${tmux_session}:" -n "${agent_window}" \
        "${tmux_agent_command}"
    log "added tmux window '${agent_window}' to session '${tmux_session}'"
else
    log "tmux session '${tmux_session}' already has window '${agent_window}'"
fi

if tmux list-windows -t "${tmux_session}" -F '#{window_name}' \
        | grep -Fx -- "${agent_window}" >/dev/null; then
    tmux select-window -t "${tmux_session}:${agent_window}"
fi

# Remote ingress shares this container's loopback, so localhost-only dev servers
# work too. A tmux window keeps logs visible and retries failed child processes.
if [ -n "${CARTHAGE_WEB_HOST:-}" ] && ! tmux list-windows -t "${tmux_session}" -F '#{window_name}' | grep -Fx web-access >/dev/null; then
    tmux new-window -d -t "${tmux_session}:" -n web-access \
        'bash -c "while true; do carthage-web; sleep 3; done"'
fi

# --- Exec the CMD ----------------------------------------------------------
if [ "$#" -eq 0 ]; then
    set -- sleep infinity
fi

log "exec'ing: $*"
exec "$@"
