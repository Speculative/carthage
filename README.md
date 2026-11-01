# Carthage

A personal sandboxed dev-environment system for running coding agents on projects with confidence, anywhere.

## What is Carthage?

Carthage wraps each project in an isolated Docker container that has the tools your coding agent needs, shares agent auth with the host, scopes filesystem access tightly, and persists sessions via tmux. One command takes you from "cloned repo" to "developing inside an isolated container with an agent running."

It is three cooperating pieces: a published base image (`ghcr.io/speculative/carthage-base`) with the common tooling plus a local personal layer (`carthage-base-personal`) built by `carthage fortify`; a host-side Python CLI (`carthage up`, `carthage attach`, etc.); and personal Carthage skills (`/carthage-annex`) installed into `~/.carthage/skills/` and linked into configured agents by `carthage fortify`.

The main tmux window can launch a personal application instead of an agent
directly. Agent CLIs remain installed and available to that application.

The design goal is that `--dangerously-skip-permissions` is reasonable inside Carthage because the sandbox itself supplies meaningful boundaries: a scoped container user with a narrow capability set, no `sudo`, no Docker socket, `no-new-privileges`, tight mount scoping, ephemeral lifecycle. Prompt fatigue goes away; trust is anchored at what the container *can't* do.

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
| `carthage fortify` | One-time host setup. Prompts for agent selection if none is configured, checks deps, installs personal skills, builds `carthage-base-personal`. Also run after upgrading the CLI or changing personal image config. Pass `--rebuild-personal` to force a no-cache personal image rebuild, e.g. to bake in agent CLI updates. |
| `carthage up` | Start the container. Rebuilds first if the image is stale. Refuses to start on host-port collisions with other Carthage projects. |
| `carthage up --no-host-ports` | Start the container with all host bindings stripped (internal-only). |
| `carthage up --port H:C` | Start with an extra one-off host binding. Explicit, repeatable. |
| `carthage attach` | Attach to the running container's tmux session. Detach with `Ctrl-b d`. |
| `carthage agent list` | Show supported agents, which are installed in your personal image, and the default agent profile. |
| `carthage agent use KIND` | Set the personal default agent profile, adding it to the installed-agent list if needed. Re-run `carthage fortify` afterward if the personal image does not already include it. |
| `carthage down` | Stop the container. Volumes and images are kept. |
| `carthage destroy` | Stop and remove containers, volumes, and project-local images. Confirms first. |
| `carthage build` | Force a rebuild. Pass `--no-cache` to skip the layer cache. |
| `carthage status` | Quick runtime state for the current project. |
| `carthage status --all` | Table of every running Carthage container across projects (reads Docker labels; no registry file). |
| `carthage survey` | Diagnostic: deps, skill version alignment, config schema, Dockerfile `FROM`, and configured agent credentials. |
| `carthage handoff REMOTE` | Stop locally, mirror the project and project state over SSH/rsync, and start it on a configured remote. |
| `carthage reclaim REMOTE` | Stop the remote, mirror its project and project state back locally, and start it here. |
| `carthage --version` | CLI version + installed skill versions + expected base image. |

## Security model

### What *is* enforced

- **Container user mapped to the host user.** With rootful Docker, `carthage`
  matches your host UID/GID. With rootless Docker, namespace UID/GID 0 maps to
  your unprivileged host account. The personal image uses that mapping so
  project files, agent credentials, and explicitly mounted directories stay
  writable with your normal host ownership. Namespace root is not host root.
  Claude receives `IS_SANDBOX=1` in the rootless personal image so unattended
  launches continue to work.
- **Host ports bind to localhost.** `carthage up` applies this to every service,
  including existing project configurations and `--port` overrides.
- **Narrow Linux capabilities.** `cap_drop: [ALL]` then `cap_add: [CHOWN, SETUID, SETGID, DAC_OVERRIDE]`. No `SYS_ADMIN`, no `NET_ADMIN`, no `SYS_PTRACE`. Projects that need `SYS_PTRACE` (gdb/strace/perf) opt in during annex; the annex skill asks.
- **`no-new-privileges`.** Setuid binaries can't gain privilege. No `sudo` is installed.
- **No Docker socket.** `/var/run/docker.sock` is never mounted. The container can't start sibling containers or escape via the daemon.
- **Tight mount scoping.** Only `/workspace` (rw), the agent credential files needed for the selected CLIs, a read-only `~/.gitconfig`, and `~/.carthage/state/<slug>/` (rw — per-project shell and agent-session state). Nothing else from the host is visible unless personal config or the project annex explicitly adds it.
- **`pids_limit: 1000`.** Catches fork bombs; generous for parallel builds and test runners.
- **Memory and CPU limits.** The CLI resolves `~75%` of host RAM and `hostcpus - 1` at `carthage up` time. Prevents runaway processes (large builds, local LLMs) from OOMing the host. Set to `0` (no limit) if detection fails (e.g., on macOS the mem check reads `/proc/meminfo`).
- **Default Docker seccomp stays on.** No `seccomp=unconfined`. No `privileged: true`. Ever.
- **Ephemeral.** `carthage destroy` wipes the project's containers, volumes, and local images cleanly. No snowflake state to manage.

### What is **not** enforced

- **Host-LAN isolation.** Carthage does *not* firewall off RFC1918 addresses. If the host can reach `192.168.1.1`, the container can too. Earlier iterations shipped an iptables-based lockdown; it was dropped because it added `NET_ADMIN` capability in exchange for defense-in-depth that doesn't pull its weight for this threat model (the boundaries include user identity + cap drops + no-sudo + no-socket + scoped mounts + ephemeral lifecycle). If a specific project needs LAN isolation, add `NET_ADMIN` and `iptables` rules in its `.carthage/Dockerfile` / compose file.
- **Read-only rootfs.** Too much friction with dev tooling (compilers, installers, pip/npm caches). Not enabled in v2; may revisit.
- **Rootful daemon user namespace remapping.** Not configured automatically. Rootless Docker is supported with the identity mapping described above.
- **Ports by default.** Sidecars (Postgres, Redis, etc.) are internal-only — the `dev` container reaches them via Docker DNS (`redis:6379`, etc.), and nothing is bound on the host. Host-side ports for local browser testing are explicit opt-in during `/carthage-annex`.

## Versioning and upgrading

Carthage ships three artifacts that share a single semver:

1. The `carthage` CLI (versioned in `pyproject.toml`).
2. The `carthage-base` Docker image, published as `:v2`, `:v2.0`, `:latest` on GHCR. `carthage fortify` layers local personal config on top as `carthage-base-personal:v2`; projects normally inherit from that local image.
3. The personal Carthage skills installed by `carthage fortify` (`carthage-annex`).

Compatibility commitments:

- The CLI reads `.carthage/config.toml` schemas one major back.
- The `carthage-base` image maintains compatibility within a major — `:v2.5` won't break projects pinning `:v2`.
- Major bumps require explicit per-project migration. Old projects opt in to new features; they never auto-upgrade.

The `.carthage/config.toml` schema (v1) looks like:

```toml
[carthage]
schema_version   = 1       # project config TOML schema version
template_version = 1       # annex template bundle version
base_image_tag   = "v2"    # carthage-base major this project targets
service_name     = "dev"
project_slug     = "my-project"
```

### Upgrade flow

There are several upgrade paths because the three artifacts (CLI, base image, in-project templates) are consumed independently. A typical full upgrade does steps 1–4; step 5 is only needed across major versions.

1. **CLI** — `uv tool upgrade carthage-cli`. Installs the new CLI binary on the host.
2. **Skills** — `carthage fortify`. Re-installs `~/.carthage/skills/carthage-annex` from the new wheel and refreshes agent discovery links. Required after every CLI upgrade; not automatic.
3. **Per-project templates** — in each annexed project, run `/carthage-annex --upgrade` from an agent session. The skill reads its bundled `CHANGELOG.md`, surfaces every entry newer than the project's `template_version`, then proposes file-by-file diffs against your committed `.carthage/`. This is how an existing project picks up template changes (new mounts, new env vars, new hardening defaults). Without this step, your committed compose file stays frozen at whatever template was current when you originally annexed.
4. **Base image, within a major** — `carthage build --pull` from the project root. The `:v2` tag on GHCR moves forward as we publish minor/patch base-image releases (e.g., new tooling, default config files); `--pull` refreshes that published base, rebuilds the local personal base when the project uses it, then rebuilds the project image. Without `--pull` you stay on whatever base digest is cached locally.
5. **Base image, across majors** — edit the `FROM` line in `.carthage/Dockerfile` (the source of truth — `base_image_tag` in `.carthage/config.toml` is informational and should be updated as a courtesy), then `carthage build`. Projects normally move from `carthage-base-personal:v2` to the next personal major after `carthage fortify` supports it. Major bumps may require code or config changes; the `carthage-base` release notes will say.

`carthage survey` surfaces version-alignment drift across all of these axes (CLI, skills, project schema, Dockerfile `FROM`).

## Personal config

Carthage also looks for user-level config at `~/.carthage/config.toml`. This is
reserved for preferences that apply across all of your Carthage projects.

Minimal file:

```toml
[carthage]
schema_version = 1
```

Personal mounts and environment variables accept an optional `id` field. Set one
if you want a project to be able to opt out of a specific entry via
`[personal].disable`:

```toml
[carthage]
schema_version = 1

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
# Source files produced by `infocmp -x <TERM>`; compiled into the personal image.
terminfo_files = ["~/.carthage/terminfo/xterm-ghostty"]

[[image.files]]
source = "~/go/bin/ostraka"
target = "/usr/local/bin/ostraka"
mode = "0755"

[agents]
installed = ["claude", "codex"]
default = "claude"

[launch]
command = "ostraka tui"

[remotes.cloud]
ssh = "cloud"                         # SSH alias or user@host
projects_dir = "/home/user/carthage"  # absolute path on the remote
```

In a project-local `.carthage/config.toml`:

```toml
[personal]
disable = ["notes"]
```

`carthage fortify` builds a local `carthage-base-personal:v2` image from the
published Carthage base, installs `[image].apt_packages`, compiles
`[image].terminfo_files`, and installs the configured `[agents].installed` agent
CLIs into that personal layer. It copies each `[[image.files]]` entry to its
declared target and mode. `[launch].command` controls the foreground
application in the main tmux window; it does not change which agent CLIs are
installed or which agent credentials are mounted. Newly annexed projects
inherit from this local personal image. Re-run `carthage fortify` after changing
`[image]`, `[launch]`, or adding a new installed agent. Generate a Ghostty source file with
`mkdir -p ~/.carthage/terminfo && infocmp -x xterm-ghostty > ~/.carthage/terminfo/xterm-ghostty`.
The personal image maps its `carthage` user to the host account: matching
UID/GID with rootful Docker, or namespace UID/GID 0 with rootless Docker.
Bind-mounted source, credentials, and agent state remain writable with normal
host ownership. `carthage up` rebuilds the personal image when this mapping
changes.

When `[launch]` is absent, the default agent profile controls the main tmux
window. With `[launch]` configured, switching between already installed agent
profiles remains quick:

```bash
carthage agent use codex
carthage up
carthage attach
```

Malformed or unsupported personal config is reported as a warning and ignored.
Project-local `.carthage/config.toml` remains the source of truth for each
project. Be careful with personal mounts: they apply to every Carthage project
unless that project opts out, including projects whose code you do not fully
trust.

### Remote handoff

Configure an SSH alias (including keys, ports, and jump hosts) in
`~/.ssh/config`, then add a `[remotes.<name>]` table as shown above. The
remote must have OpenSSH, rsync, Docker with Compose v2, a compatible Carthage
major version, and its own completed `carthage fortify`/agent authentication.
If your local terminal reports `TERM=xterm-ghostty`, each remote also needs that
terminfo source in its host-local personal configuration. Handoff warns when the
remote cannot find it, but does not modify the remote host.

`carthage handoff cloud` stops the local Compose project, mirrors the complete
project tree—including ignored, untracked, and Git metadata—and mirrors
`~/.carthage/state/<slug>/` to the remote before starting the remote project.
Images and global agent credentials stay on each host; the destination builds
its own images. `carthage reclaim cloud` performs the reverse transfer.

The MVP is intentionally explicit and single-user: it does not enforce
ownership manifests or provide rollback. Before either command, rsync performs
an itemized dry run. If any destination file would be overwritten or deleted,
the command prints representative paths and exits; repeat with `--force` to
authorize the destructive changes. Use `--dry-run` to inspect the plan and
`--no-up` to transfer while leaving the destination stopped. Do not restart or
edit the inactive copy after a successful handoff.

## Troubleshooting

`carthage survey` first — it catches the common issues (Docker not running, GHCR unreachable, configured agent credential files missing, skill version drift).

- **"port collision" on `carthage up`** — another Carthage project has the host port. `carthage status --all` tells you who. Options: stop that project, use `carthage up --no-host-ports` for internal-only access, or pick a different host port with `--port H:C`.
- **The agent says "not authenticated" inside the container** — run the agent's login flow on the host once. The configured agent auth mount shares auth with the sandbox.
- **"image current" but code changes aren't showing up** — the source tree is a bind mount, so edits are live. If you changed `.carthage/Dockerfile` and expected a rebuild, check that the hash in `.carthage/last-build-hash` changed; if not, `carthage build --no-cache`.
- **Container can't reach `postgres` / `redis`** — they're on the same compose network. Reference them by service name (`redis-cli -h redis ping`), not `localhost` or the host IP.
- **Installed skill version doesn't match CLI** — `carthage fortify` re-installs. Required after `uv tool upgrade`.

## The name

Carthage was Rome's great rival — an independent, walled city. Roman propaganda demanded *Carthago delenda est*: "Carthage must be destroyed." The name here is deliberately the opposite. Carthage containers *should* be destroyed — casually, frequently, per project, on a whim. The walls are the point. Reach for `carthage destroy` without fear.

## Remote Ostraka web access through Lyoko

Remote access is opt-in per host. With `[web]` configured, the personal-image
build installs `carthage-web` and its startup entrypoint,
including on older v2 base images. The personal image must also install an
Ostraka binary with `ostraka web` support (for example through `[image].files`).
Rebuild the personal and project images after enabling web access.

Store the existing frps token in a host file readable by your container UID
(`chmod 600 ~/.carthage/frp-token`), then add this once to your personal config (`~/.carthage/config.toml`):

```toml
[web]
server_addr = "tunnel.speculative.tech"
domain = "agents.speculative.tech"
token_file = "~/.carthage/frp-token"
supervise = false
```

This host-wide setting enables web access automatically for every project,
including newly annexed projects. `carthage up` starts the router, Ostraka web
listener in the `web-access` tmux window. The tunnel runs in a separate
`carthage-frpc` service using `fatedier/frpc:v0.71.1`, with a read-only filesystem,
no Linux capabilities, and no project or agent credential mounts. Only that
service receives the tunnel token. It reaches the development router through a
shared Unix socket; no host port is required. Use `docker logs` for tunnel logs.
`carthage down` removes both containers. With
`supervise = false`, the Ostraka TUI owns the supervisor. Set it to `true` for a
web-only session, and use a shell or agent as your personal launch command so
there is only one supervisor. Web startup initializes a missing project
Ostraka store automatically.

Visit `https://<slug>.agents.speculative.tech` for Ostraka, or
`https://<port>-<slug>.agents.speculative.tech` for an HTTP dev server, including
WebSockets. Dev servers can bind to localhost; the router translates Host to
localhost to support their default host allowlists. Port 8090 is reserved for
Ostraka; the router uses a private Unix socket. Unknown hosts and privileged ports are
rejected. The router publishes no Docker ports.
Use `[personal].disable = ["web"]` in a project to opt out.

Lyoko must deploy its `web/deploy-agents.yaml` playbook and configure wildcard
DNS for `*.agents.speculative.tech`. Its existing GitHub forward-auth middleware
protects the portal and every container route; its `speculative.tech` cookie
already covers these names. Dev-port hostnames use a numeric port followed by
`-<slug>`, sharing the same wildcard certificate as the portal. The Lyoko
gateway translates them into frps's per-container wildcard routes, so new
projects and ports need no Lyoko configuration. Project slugs beginning with
an integer followed by `-` are reserved for port routing.
The portal lists online Carthage containers, their Ostraka workspaces, and
automatically discovered listening TCP ports. It refreshes every 15 seconds.
Port links support HTTP and WebSocket services; UDP is not listed.
Stopping a project with `carthage down` also removes its tunnel. If the dev
process crashes independently, a remaining tunnel returns an upstream error.
Lyoko strips its `tf_sess_*` and `tf_state_*` cookies after forward authentication,
and blocks applications from setting those reserved cookies. Application cookies
continue to pass through.

### Rootless Docker on Linux

Install rootless Docker using the [Docker setup instructions](https://docs.docker.com/engine/security/rootless/),
then select its context with `docker context use rootless`. `carthage up` detects
the daemon, rebuilds the personal image when its identity mapping differs, and
starts projects normally. Images and named volumes belong to the selected
Docker daemon; existing bind-mounted source and agent state stay in place.
Use `systemctl --user enable --now docker` to start the user service on login.
For startup before login, enable lingering with `sudo loginctl enable-linger "$USER"`.
After migration, retire the unused rootful daemon and Docker group access if
other workflows do not need them. Rootless Docker still shares the host kernel;
it does not prevent an agent from changing files or credentials explicitly
mounted into its container.

Implementation follows the [remote access design in Notion](https://app.notion.com/p/407be87663c88125a455cfe23c1e0a66).
