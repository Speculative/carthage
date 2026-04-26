# Carthage

A personal sandboxed dev-environment system for running Claude Code on projects with confidence, anywhere.

## What is Carthage?

Carthage wraps each project in an isolated Docker container that has the tools Claude Code needs, shares your Claude auth with the host, scopes filesystem access tightly, and persists sessions via tmux. One command takes you from "cloned repo" to "developing inside an isolated container with Claude Code running."

It is three cooperating pieces: a published base image (`ghcr.io/speculative/carthage-base`) with the common tooling; a host-side Python CLI (`carthage up`, `carthage attach`, etc.); and personal Claude Code skills (`/carthage-annex`, and reserved for future `/carthage-migrate`) installed into `~/.claude/skills/` by `carthage fortify`.

The design goal is that `--dangerously-skip-permissions` is reasonable inside Carthage because the sandbox itself supplies meaningful boundaries: non-root user with a narrow capability set, no `sudo`, no Docker socket, `no-new-privileges`, tight mount scoping, ephemeral lifecycle. Prompt fatigue goes away; trust is anchored at what the container *can't* do.

## Installation

**CLI (one-time, per development machine):**

```bash
uv tool install git+https://github.com/speculative/carthage.git
carthage --version
carthage fortify        # one-time: checks deps, installs ~/.claude/skills/carthage-annex
```

`carthage fortify` is also how you pick up skill updates after a CLI upgrade — re-run it after `uv tool upgrade carthage-cli`. Skill updates are **not** automatic.

## Bringing a project in

In Claude Code, from the project root, run `/carthage-annex`. Answer the questions about runtimes, services, published ports, and whether you need native debugging tools. The skill generates `.carthage/` config and a project-local sandbox-awareness skill under `.claude/skills/carthage-env/`. Commit those files. Then on the host:

```bash
carthage up       # builds (first time) and starts the container
carthage attach   # drops you into a tmux session with Claude Code running
```

## Daily commands

| Command | What it does |
|---|---|
| `carthage fortify` | One-time host setup. Checks deps, installs personal skills. Also run after upgrading the CLI. |
| `carthage up` | Start the container. Rebuilds first if the image is stale. Refuses to start on host-port collisions with other Carthage projects. |
| `carthage up --no-host-ports` | Start the container with all host bindings stripped (internal-only). |
| `carthage up --port H:C` | Start with an extra one-off host binding. Explicit, repeatable. |
| `carthage attach` | Attach to the running container's tmux `claude` session. Detach with `Ctrl-b d`. |
| `carthage down` | Stop the container. Volumes and images are kept. |
| `carthage destroy` | Stop and remove containers, volumes, and project-local images. Confirms first. |
| `carthage build` | Force a rebuild. Pass `--no-cache` to skip the layer cache. |
| `carthage status` | Quick runtime state for the current project. |
| `carthage status --all` | Table of every running Carthage container across projects (reads Docker labels; no registry file). |
| `carthage survey` | Deep diagnostic: deps, skill version alignment, config schema, Dockerfile `FROM`. `--deep` spins up a test container and probes Claude auth. |
| `carthage --version` | CLI version + installed skill versions + expected base image. |

## Security model

### What *is* enforced

- **Non-root user (`carthage`) matching host UID/GID.** File writes to bind-mounted `/workspace` get sane ownership on the host.
- **Narrow Linux capabilities.** `cap_drop: [ALL]` then `cap_add: [CHOWN, SETUID, SETGID, DAC_OVERRIDE]`. No `SYS_ADMIN`, no `NET_ADMIN`, no `SYS_PTRACE`. Projects that need `SYS_PTRACE` (gdb/strace/perf) opt in during annex; the annex skill asks.
- **`no-new-privileges`.** Setuid binaries can't gain privilege. No `sudo` is installed.
- **No Docker socket.** `/var/run/docker.sock` is never mounted. The container can't start sibling containers or escape via the daemon.
- **Tight mount scoping.** Only `/workspace` (rw), `~/.claude` (rw — Claude Code writes session state there), and a read-only `~/.gitconfig`. Nothing else from the host is visible.
- **`pids_limit: 1000`.** Catches fork bombs; generous for parallel builds and test runners.
- **Memory and CPU limits.** The CLI resolves `~75%` of host RAM and `hostcpus - 1` at `carthage up` time. Prevents runaway processes (large builds, local LLMs) from OOMing the host. Set to `0` (no limit) if detection fails (e.g., on macOS the mem check reads `/proc/meminfo`).
- **Default Docker seccomp stays on.** No `seccomp=unconfined`. No `privileged: true`. Ever.
- **Ephemeral.** `carthage destroy` wipes the project's containers, volumes, and local images cleanly. No snowflake state to manage.

### What is **not** enforced

- **Host-LAN isolation.** Carthage does *not* firewall off RFC1918 addresses. If the host can reach `192.168.1.1`, the container can too. Earlier iterations shipped an iptables-based lockdown; it was dropped because it added `NET_ADMIN` capability in exchange for defense-in-depth that doesn't pull its weight for this threat model (the actually-load-bearing properties are non-root + cap drops + no-sudo + no-socket + scoped mounts + ephemeral lifecycle). If a specific project needs LAN isolation, add `NET_ADMIN` and `iptables` rules in its `.carthage/Dockerfile` / compose file.
- **Read-only rootfs.** Too much friction with dev tooling (compilers, installers, pip/npm caches). Not enabled in v1; may revisit.
- **User namespace remapping.** Conflicts with the host-UID-matching design.
- **Ports by default.** Sidecars (Postgres, Redis, etc.) are internal-only — the `dev` container reaches them via Docker DNS (`redis:6379`, etc.), and nothing is bound on the host. Host-side ports for local browser testing are explicit opt-in during `/carthage-annex`.

## Versioning and upgrading

Carthage ships three artifacts that share a single semver:

1. The `carthage` CLI (versioned in `pyproject.toml`).
2. The `carthage-base` Docker image, published as `:v1`, `:v1.2`, `:latest` on GHCR. Projects pin to a major (`v1`).
3. The personal Claude skills installed by `carthage fortify` (`carthage-annex` in v1; `carthage-migrate` reserved).

Compatibility commitments:

- The CLI reads `.carthage/config.toml` schemas one major back.
- The `carthage-base` image maintains compatibility within a major — `:v1.5` won't break projects pinning `:v1`.
- Major bumps require explicit per-project migration. Old projects opt in to new features; they never auto-upgrade.

The `.carthage/config.toml` schema (v1) looks like:

```toml
[carthage]
version          = "1"         # config schema version
base_image_tag   = "v1"        # carthage-base major this project targets
annexed_with_cli = "1.0.1"     # informational; what CLI did the annex
service_name     = "dev"
project_slug     = "my-project"
```

### Upgrade flow

There are several upgrade paths because the three artifacts (CLI, base image, in-project templates) are consumed independently. A typical full upgrade does steps 1–4; step 5 is only needed across major versions.

1. **CLI** — `uv tool upgrade carthage-cli`. Installs the new CLI binary on the host.
2. **Skills** — `carthage fortify`. Re-installs `~/.claude/skills/carthage-annex` from the new wheel. Required after every CLI upgrade; not automatic.
3. **Per-project templates** — in each annexed project, run `/carthage-annex --upgrade` from a Claude Code session. The skill reads its bundled `CHANGELOG.md`, surfaces every entry newer than the project's `annexed_with_cli`, then proposes file-by-file diffs against your committed `.carthage/`. This is how an existing project picks up template changes (new mounts, new env vars, new hardening defaults). Without this step, your committed compose file stays frozen at whatever template was current when you originally annexed.
4. **Base image, within a major** — `carthage build --pull` from the project root. The `:v1` tag on GHCR moves forward as we publish minor/patch base-image releases (e.g., new tooling, default config files); `--pull` is what forces Docker to fetch the moved tag instead of using its cached digest. Without `--pull` you stay on whatever `:v1` resolved to last time.
5. **Base image, across majors** — edit the `FROM` line in `.carthage/Dockerfile` (the source of truth — `base_image_tag` in `.carthage/config.toml` is informational and should be updated as a courtesy), then `carthage build`. Major bumps may require code or config changes; the `carthage-base` release notes will say. (`/carthage-migrate` will eventually orchestrate this; reserved in v1.)

`carthage survey` surfaces version-alignment drift across all of these axes (CLI, skills, project schema, Dockerfile `FROM`).

## Troubleshooting

`carthage survey` first — it catches the common issues (Docker not running, GHCR unreachable, `~/.claude` missing, skill version drift). `carthage survey --deep` additionally probes Claude auth inside a test container.

- **"port collision" on `carthage up`** — another Carthage project has the host port. `carthage status --all` tells you who. Options: stop that project, use `carthage up --no-host-ports` for internal-only access, or pick a different host port with `--port H:C`.
- **Claude Code says "not authenticated" inside the container** — run `claude` on the host once. The `~/.claude` mount shares auth with the sandbox.
- **"image current" but code changes aren't showing up** — the source tree is a bind mount, so edits are live. If you changed `.carthage/Dockerfile` and expected a rebuild, check that the hash in `.carthage/last-build-hash` changed; if not, `carthage build --no-cache`.
- **Container can't reach `postgres` / `redis`** — they're on the same compose network. Reference them by service name (`redis-cli -h redis ping`), not `localhost` or the host IP.
- **Installed skill version doesn't match CLI** — `carthage fortify` re-installs. Required after `uv tool upgrade`.

## The name

Carthage was Rome's great rival — an independent, walled city. Roman propaganda demanded *Carthago delenda est*: "Carthage must be destroyed." The name here is deliberately the opposite. Carthage containers *should* be destroyed — casually, frequently, per project, on a whim. The walls are the point. Reach for `carthage destroy` without fear.
