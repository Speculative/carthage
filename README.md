# Carthage

A personal sandboxed dev-environment system for running coding agents on projects with confidence, anywhere.

## What is Carthage?

Carthage wraps each project in an isolated Docker container that has the tools your coding agent needs, shares agent auth with the host, scopes filesystem access tightly, and persists sessions via tmux. One command takes you from "cloned repo" to "developing inside an isolated container with an agent running."

It is three cooperating pieces: a published base image (`ghcr.io/speculative/carthage-base`) with the common tooling plus a local personal layer (`carthage-base-personal`) built by `carthage fortify`; a host-side Python CLI (`carthage up`, `carthage attach`, etc.); and personal Carthage skills (`/carthage-annex`) installed into `~/.carthage/skills/` and linked into configured agents by `carthage fortify`.

The design goal is that `--dangerously-skip-permissions` is reasonable inside Carthage because the sandbox itself supplies meaningful boundaries: non-root user with a narrow capability set, no `sudo`, no Docker socket, `no-new-privileges`, tight mount scoping, ephemeral lifecycle. Prompt fatigue goes away; trust is anchored at what the container *can't* do.

## Installation

**CLI (one-time, per development machine):**

```bash
uv tool install git+https://github.com/speculative/carthage.git
carthage --version
carthage fortify        # one-time: checks deps, installs personal skills
```

`carthage fortify` is also how you pick up skill updates and rebuild your
local personal base image after a CLI or `~/.carthage/config.toml` change —
re-run it after `uv tool upgrade carthage-cli`. Skill updates are **not**
automatic.

## Bringing a project in

From a configured agent, run `/carthage-annex` in the project root. Answer the questions about runtimes, services, published ports, and whether you need native debugging tools. The skill generates `.carthage/` config and project-local sandbox-awareness skills under agent discovery paths such as `.claude/skills/carthage-env/` and `.agents/skills/carthage-env/`. Commit those files. Then on the host:

```bash
carthage up       # builds (first time) and starts the container
carthage attach   # drops you into a tmux session with the configured agent running
```

## Daily commands

| Command | What it does |
|---|---|
| `carthage fortify` | One-time host setup. Checks deps, installs personal skills, builds `carthage-base-personal`. Also run after upgrading the CLI or changing personal image config. |
| `carthage up` | Start the container. Rebuilds first if the image is stale. Refuses to start on host-port collisions with other Carthage projects. |
| `carthage up --no-host-ports` | Start the container with all host bindings stripped (internal-only). |
| `carthage up --port H:C` | Start with an extra one-off host binding. Explicit, repeatable. |
| `carthage attach` | Attach to the running container's tmux session. Detach with `Ctrl-b d`. |
| `carthage down` | Stop the container. Volumes and images are kept. |
| `carthage destroy` | Stop and remove containers, volumes, and project-local images. Confirms first. |
| `carthage build` | Force a rebuild. Pass `--no-cache` to skip the layer cache. |
| `carthage status` | Quick runtime state for the current project. |
| `carthage status --all` | Table of every running Carthage container across projects (reads Docker labels; no registry file). |
| `carthage survey` | Deep diagnostic: deps, skill version alignment, config schema, Dockerfile `FROM`. `--deep` currently probes Claude auth. |
| `carthage --version` | CLI version + installed skill versions + expected base image. |

## Security model

### What *is* enforced

- **Non-root user (`carthage`) matching host UID/GID.** File writes to bind-mounted `/workspace` get sane ownership on the host.
- **Narrow Linux capabilities.** `cap_drop: [ALL]` then `cap_add: [CHOWN, SETUID, SETGID, DAC_OVERRIDE]`. No `SYS_ADMIN`, no `NET_ADMIN`, no `SYS_PTRACE`. Projects that need `SYS_PTRACE` (gdb/strace/perf) opt in during annex; the annex skill asks.
- **`no-new-privileges`.** Setuid binaries can't gain privilege. No `sudo` is installed.
- **No Docker socket.** `/var/run/docker.sock` is never mounted. The container can't start sibling containers or escape via the daemon.
- **Tight mount scoping.** Only `/workspace` (rw), configured agent auth directories (rw), a read-only `~/.gitconfig`, and `~/.carthage/state/<slug>/` (rw — per-project shell history etc., scoped by slug). Nothing else from the host is visible unless personal config or the project annex explicitly adds it.
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
2. The `carthage-base` Docker image, published as `:v1`, `:v1.2`, `:latest` on GHCR. `carthage fortify` layers local personal config on top as `carthage-base-personal:v1`; projects normally inherit from that local image.
3. The personal Carthage skills installed by `carthage fortify` (`carthage-annex`).

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
2. **Skills** — `carthage fortify`. Re-installs `~/.carthage/skills/carthage-annex` from the new wheel and refreshes agent discovery links. Required after every CLI upgrade; not automatic.
3. **Per-project templates** — in each annexed project, run `/carthage-annex --upgrade` from an agent session. The skill reads its bundled `CHANGELOG.md`, surfaces every entry newer than the project's `annexed_with_cli`, then proposes file-by-file diffs against your committed `.carthage/`. This is how an existing project picks up template changes (new mounts, new env vars, new hardening defaults). Without this step, your committed compose file stays frozen at whatever template was current when you originally annexed.
4. **Base image, within a major** — `carthage build --pull` from the project root. The `:v1` tag on GHCR moves forward as we publish minor/patch base-image releases (e.g., new tooling, default config files); `--pull` refreshes that published base, rebuilds the local personal base when the project uses it, then rebuilds the project image. Without `--pull` you stay on whatever base digest is cached locally.
5. **Base image, across majors** — edit the `FROM` line in `.carthage/Dockerfile` (the source of truth — `base_image_tag` in `.carthage/config.toml` is informational and should be updated as a courtesy), then `carthage build`. Projects normally move from `carthage-base-personal:v1` to the next personal major after `carthage fortify` supports it. Major bumps may require code or config changes; the `carthage-base` release notes will say.

`carthage survey` surfaces version-alignment drift across all of these axes (CLI, skills, project schema, Dockerfile `FROM`).

## Personal config

Carthage also looks for user-level config at `~/.carthage/config.toml`. This is
reserved for preferences that apply across all of your Carthage projects.

Minimal file:

```toml
[carthage]
personal_config_version = "1"
```

Personal mounts and environment variables use stable IDs so individual projects
can opt out:

```toml
[carthage]
personal_config_version = "1"

[[mounts]]
id = "notes"
source = "~/notes"
target = "/home/carthage/.notes"
mode = "ro"  # "ro" or "rw"

[[environment]]
id = "editor"
name = "EDITOR"
value = "vim"

[image]
apt_packages = ["fzf", "shellcheck"]
```

In a project-local `.carthage/config.toml`:

```toml
[personal]
disable = ["notes"]
```

`carthage fortify` builds a local `carthage-base-personal:v1` image from the
published Carthage base and installs `[image].apt_packages` into that personal
layer. Newly annexed projects inherit from this local personal image. Re-run
`carthage fortify` after changing `[image]`.

Malformed or unsupported personal config is reported as a warning and ignored.
Project-local `.carthage/config.toml` remains the source of truth for each
project. Be careful with personal mounts: they apply to every Carthage project
unless that project opts out, including projects whose code you do not fully
trust.

## Troubleshooting

`carthage survey` first — it catches the common issues (Docker not running, GHCR unreachable, agent auth missing, skill version drift). `carthage survey --deep` currently probes Claude auth inside a test container.

- **"port collision" on `carthage up`** — another Carthage project has the host port. `carthage status --all` tells you who. Options: stop that project, use `carthage up --no-host-ports` for internal-only access, or pick a different host port with `--port H:C`.
- **The agent says "not authenticated" inside the container** — run the agent's login flow on the host once. The configured agent auth mount shares auth with the sandbox.
- **"image current" but code changes aren't showing up** — the source tree is a bind mount, so edits are live. If you changed `.carthage/Dockerfile` and expected a rebuild, check that the hash in `.carthage/last-build-hash` changed; if not, `carthage build --no-cache`.
- **Container can't reach `postgres` / `redis`** — they're on the same compose network. Reference them by service name (`redis-cli -h redis ping`), not `localhost` or the host IP.
- **Installed skill version doesn't match CLI** — `carthage fortify` re-installs. Required after `uv tool upgrade`.

## The name

Carthage was Rome's great rival — an independent, walled city. Roman propaganda demanded *Carthago delenda est*: "Carthage must be destroyed." The name here is deliberately the opposite. Carthage containers *should* be destroyed — casually, frequently, per project, on a whim. The walls are the point. Reach for `carthage destroy` without fear.
