# Changelog — carthage-annex skill

This changelog covers the *templates* this skill generates: `.carthage/Dockerfile`, `.carthage/docker-compose.yaml`, `.carthage/config.toml`, and the in-project `carthage-env` skill. CLI behavior changes that don't affect generated files are out of scope here — see the project root for those.

`/carthage-annex --upgrade` reads this file and surfaces entries newer than the target project's `annexed_with_cli`, so existing projects get the rationale and migration steps before any diff is applied.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions match the CLI/skill semver.

## [Unreleased]

### Added
- **Per-project shell-history bind mount** — `${HOME}/.carthage/state/<slug>:/commandhistory:rw` is now part of the generated compose file. The base image points `HISTFILE` at `/commandhistory/.bash_history` and runs `history -a` after every prompt, so bash history persists across `carthage down`/`up` cycles instead of dying with the container's writable layer. Slug-keyed: two projects with the same slug share history (same caveat as the compose project name).

### Migration for existing projects
`/carthage-annex --upgrade` will offer the diff. To apply by hand, add to `.carthage/docker-compose.yaml` under the `dev` service:

```yaml
    volumes:
      - ${HOME}/.carthage/state/<your project slug>:/commandhistory:rw
```

(Substitute your `project_slug` from `.carthage/config.toml`.) The CLI creates `~/.carthage/state/<slug>/` on `carthage up`, but if you `up` against an old CLI that doesn't, `mkdir -p` it on the host yourself before the next start so docker doesn't auto-create it as a root-owned directory.

### Base image
A new release of `ghcr.io/speculative/carthage-base` ships the `HISTFILE`/`PROMPT_COMMAND` bashrc additions and the `/commandhistory` mount point. Picked up via `carthage build --pull`.

## [1.0.1] — 2026-04-25

### Added
- **`~/.claude.json` bind mount** in the `dev` service. Without this mount, Claude Code starts logged-out inside the container even though `~/.claude/.credentials.json` is present — `.claude.json` is a sibling of `.claude/` (not inside it) and holds the OAuth account + login state. Fixes the most common "first `carthage up`" surprise.
- **`CARTHAGE_PROJECT` environment variable** in the `dev` service. The base-image tmux config reads this to render the project slug in the status bar so the sandbox is visually distinguishable from a host shell.

### Migration for existing projects
`/carthage-annex --upgrade` will offer the diff. To apply by hand, add to `.carthage/docker-compose.yaml` under the `dev` service:

```yaml
    volumes:
      - ${HOME}/.claude.json:/home/carthage/.claude.json:rw

    environment:
      CARTHAGE_PROJECT: "<your project slug>"
```

The mount must be `rw` — Claude Code rewrites the file on every session. The slug should match `project_slug` in `.carthage/config.toml`.

### Base image
v1.0.1 of `ghcr.io/speculative/carthage-base` ships a default tmux config (prefix `C-a`, vim-style pane nav, project-aware status bar). Picked up via `carthage build --pull`. No template change required — the base image is consumed by `FROM` in the project's Dockerfile.

## [1.0.0] — 2026-04-24

Initial release. Establishes the `.carthage/` layout, the `dev` service with hardening (`cap_drop: [ALL]` + a small `cap_add` set, `no-new-privileges`, `pids_limit`), the `${HOME}/.claude` mount, and optional sidecar services (Postgres, MySQL, Redis).
