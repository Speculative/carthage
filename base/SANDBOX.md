# You are inside a Carthage sandbox container

The environment variable `$CARTHAGE` is set to `1`. This document describes
the rules of the sandbox so you don't waste effort working around them.

## What *is* isolated

- You are running as a non-root user (`carthage`).
- Most Linux capabilities are dropped (only `CHOWN`, `SETUID`, `SETGID`,
  `DAC_OVERRIDE` are retained; no `SYS_ADMIN`, no `NET_ADMIN`, no `SYS_PTRACE`
  by default).
- `no-new-privileges` is set — setuid binaries can't gain privilege.
- There is no `sudo`. System packages go in `.carthage/Dockerfile`, not the
  running container.
- `/var/run/docker.sock` is **not** mounted — you cannot start sibling
  containers or escape via the Docker daemon.
- `pids_limit` and `mem_limit` / `cpus` caps are set on the container.
- File mounts are scoped: `/workspace` (the project source tree),
  `/home/carthage/.claude` (auth + session state), a read-only
  `/home/carthage/.gitconfig`, and `/commandhistory` (per-project
  shell-state dir from `~/.carthage/state/<slug>/`). Nothing else from
  the host is visible.
- The container is ephemeral — `carthage destroy` wipes it cleanly.

## What is **not** isolated

- **The host LAN.** Carthage does *not* firewall off RFC1918 ranges.
  If the host can reach `192.168.1.1`, this container can too. Treat the
  container as "has the same network reach as the host user" and scope
  your actions accordingly. If you need LAN isolation for a specific
  project, add `iptables` rules in that project's `.carthage/Dockerfile`
  at runtime (requires granting `NET_ADMIN` explicitly in the compose file).

## No passwordless sudo

- There is no `sudo`. This is deliberate.
- System packages belong in `.carthage/Dockerfile`, not in runtime apt-get.
- If you need a new system package (build tools, a database client, etc.):
  1. Edit `.carthage/Dockerfile` to add it to the appropriate `RUN apt-get`
     or equivalent layer.
  2. Tell the user: "I've added X to `.carthage/Dockerfile`. Please run
     `carthage build` on the host so the change takes effect."
- Language-level package managers (`uv`, `pip`, `npm`, `cargo`, `go get`,
  `gem`, etc.) run fine as the `carthage` user. Use those for 90% of what
  you need.

## Git

- Local git operations work normally: commit, revert, rebase, reset,
  cherry-pick, stash, branch manipulation, etc. The container has
  read/write access to `/workspace/.git` and uses the host's
  `~/.gitconfig` (read-only) for `user.name` / `user.email`.
- `git push` happens on the host, not here. The container does not have
  the host's SSH keys or HTTPS credential helpers. When you have changes
  ready to publish, tell the user: "ready to push from the host."
- If the host has commit signing configured, signed commits from inside
  the container may fail (the container does not have the host's GPG/SSH
  signing keys). If you hit this, ask the user to commit on the host
  instead.

## Permissions prompt

- `--dangerously-skip-permissions` is the right flag for this environment.
  The sandbox itself is the permission boundary; prompting inside an isolated
  container adds friction without adding safety.

## Missing services

- If you need a service that isn't running (a database, cache, message
  broker, etc.), check `.carthage/docker-compose.yaml` first. If it's not
  there, don't try to `apt install` and run it in this container. Instead,
  tell the user: "I need service X. Please run `/carthage-annex` again to
  add it, or add it to `.carthage/docker-compose.yaml` manually."

## What's mounted where

- `/workspace` — the project's source tree (read/write). This is your
  working directory.
- `/home/carthage/.claude` — shared with the host, so your session state
  and login persist across container rebuilds.
- `/home/carthage/.gitconfig` — read-only, shared with the host.
- `/commandhistory` — per-project state dir (read/write). Currently holds
  `.bash_history`; lives at `~/.carthage/state/<slug>/` on the host so
  shell history survives `carthage down`/`up` cycles.
- `/etc/carthage/SANDBOX.md` — this document. Lives in the image, not a
  mount. Upgraded only when the base image version bumps.
