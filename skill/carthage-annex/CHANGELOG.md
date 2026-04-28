# Changelog — carthage-annex skill

This changelog covers the *templates* this skill generates: `.carthage/Dockerfile`, `.carthage/docker-compose.yaml`, `.carthage/config.toml`, and the in-project `carthage-env` skill. CLI behavior changes that don't affect generated files are out of scope here — see the project root for those.

`/carthage-annex --upgrade` reads this file and surfaces entries newer than the target project's `annexed_with_cli`, so existing projects get the rationale and migration steps before any diff is applied.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions match the CLI/skill semver.

## [1.1.0] — 2026-04-28

### Added
- **Per-project shell-history bind mount** — `${HOME}/.carthage/state/<slug>:/commandhistory:rw` is now part of the generated compose file. The base image points `HISTFILE` at `/commandhistory/.bash_history` and runs `history -a` after every prompt, so bash history persists across `carthage down`/`up` cycles instead of dying with the container's writable layer. Slug-keyed: two projects with the same slug share history (same caveat as the compose project name).
- **`carthage.base-image-version` label** on the dev container. The CLI reads the OCI `org.opencontainers.image.version` label off the locally-cached base image at `up` time and stamps it onto the container so `carthage status` / `carthage survey` can flag a running container as stale when its base image is bumped underneath it.

### Migration for existing projects
Two edits to `.carthage/docker-compose.yaml`:

1. Add this line to the `dev` service's `volumes:` block (substituting `project_slug` from `.carthage/config.toml`):

   ```yaml
         - ${HOME}/.carthage/state/<project slug>:/commandhistory:rw
   ```

2. Add this line to the `dev` service's `labels:` block:

   ```yaml
         carthage.base-image-version: "${CARTHAGE_BASE_IMAGE_VERSION:-}"
   ```

   The CLI populates `CARTHAGE_BASE_IMAGE_VERSION` at `up` time. Without the label, `carthage status` shows the running container's version as "unknown" until the next `up` after this edit.

The CLI creates `~/.carthage/state/<slug>/` on `carthage up`. If the user is running an older CLI that doesn't, `mkdir -p` the dir on the host before the next `up`, otherwise docker auto-creates it as a root-owned directory the carthage user can't write to.

## [1.0.1] — 2026-04-25

### Added
- **`~/.claude.json` bind mount** in the `dev` service. Without this mount, Claude Code starts logged-out inside the container even though `~/.claude/.credentials.json` is present — `.claude.json` is a sibling of `.claude/` (not inside it) and holds the OAuth account + login state. Fixes the most common "first `carthage up`" surprise.
- **`CARTHAGE_PROJECT` environment variable** in the `dev` service. The base-image tmux config reads this to render the project slug in the status bar so the sandbox is visually distinguishable from a host shell.

### Migration for existing projects
Add to `.carthage/docker-compose.yaml` under the `dev` service:

```yaml
    volumes:
      - ${HOME}/.claude.json:/home/carthage/.claude.json:rw

    environment:
      CARTHAGE_PROJECT: "<project slug>"
```

The mount must be `rw` — Claude Code rewrites the file on every session. Substitute `project_slug` from `.carthage/config.toml`.

## [1.0.0] — 2026-04-24

Initial release. Establishes the `.carthage/` layout, the `dev` service with hardening (`cap_drop: [ALL]` + a small `cap_add` set, `no-new-privileges`, `pids_limit`), the `${HOME}/.claude` mount, and optional sidecar services (Postgres, MySQL, Redis).
