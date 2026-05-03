---
name: carthage-annex
version: "1.1.1"
description: Use when the user wants to bring a project into the Carthage sandboxed dev-environment workflow. Trigger phrases include "annex this project into carthage", "carthage-annex", "set up carthage for this repo", "make this project carthage-compatible", "bring this repo into carthage", and "/carthage-annex". Generates `.carthage/` config for a new or existing project so the user can run `carthage up && carthage attach` to get a sandboxed Claude Code session.
---

# /carthage-annex — bring a project into Carthage

You're helping the user set up Carthage for a project. The end state is:

- `.carthage/config.toml`, `.carthage/Dockerfile`, `.carthage/docker-compose.yaml` committed in the repo.
- `.claude/skills/carthage-env/SKILL.md` committed in the repo (so the in-container Claude session knows it's in a sandbox).
- `.dockerignore` at the project root (only if one doesn't already exist).
- `.carthage/last-build-hash` entry appended to `.gitignore`.

After this, the user runs `carthage up && carthage attach` on the host to start developing.

## Your job, step by step

### 1. Check invocation mode

If the user passed `--upgrade` (or asked to re-run annex against a project that already has `.carthage/` populated):

1. **Read [CHANGELOG.md](CHANGELOG.md)** (lives next to this SKILL.md, shipped in the wheel). Read the project's `.carthage/config.toml` to get its `annexed_with_cli` value.
2. **Surface every changelog entry strictly newer than `annexed_with_cli`** to the user, in chronological order. For each entry, print the version, the date, the user-facing summary, and any "Migration for existing projects" notes. This is the *narrative* that a raw diff can't convey.
3. **Then** propose targeted edits: diff the project's current `.carthage/` files against what the templates would produce now, and ask file-by-file whether to apply. Preserve the user's customizations — never blanket-overwrite. If a section has been hand-edited and the template also changed it, flag the conflict and let the user pick.
4. After the user accepts the edits, update `annexed_with_cli` in `.carthage/config.toml` to the current CLI version so the *next* `--upgrade` only shows entries newer than this run.

Notes:
- If `annexed_with_cli` is missing or unparseable, treat it as "show every changelog entry."
- Full structured `--upgrade` (auto-merge with conflict resolution per file section) is reserved for a future release. The current implementation is "narrate from the changelog, then diff each file."

Otherwise, proceed with a fresh annex (steps 2 onward).

### 2. Detect project type

Look for the usual suspects at the project root:

- `pyproject.toml` → Python (check for `.python-version`, `requires-python`)
- `package.json` → Node (check for `.nvmrc`, `"engines"` field)
- `Cargo.toml` → Rust
- `go.mod` → Go
- `Gemfile` → Ruby
- `requirements.txt` → Python (older style)

If multiple are present or the detection is ambiguous, **ask the user** which runtime(s) should be installed in the container. Don't guess — getting this wrong means a broken build.

### 3. Ask about services, ports, and native debugging

Use the available elicitation tool. These are the questions:

- **"Does this project need a database? If so, which?"** — offer Postgres, MySQL, Redis, None, Other.

- **"Any ports you want *published to the host* for local browser testing?"** — e.g., `3000`, `8000`, `5173`. Default: **none**. Be explicit about the tradeoff:
  - Published ports let you open `localhost:3000` in your browser on the host.
  - They also are the only way another Carthage project could collide with yours — `carthage up` will refuse to start if another project already owns a port you've asked for. (When that happens, the CLI suggests a copy-pasteable `carthage up --port HOST:CONTAINER` retry.)
  - Inter-service networking works regardless (the `dev` container reaches `redis`, `postgres`, etc. by service name over the compose network).

  **Bind-interface gotcha — important.** Most dev servers (Vite, Next, Django runserver, Rails, `python -m http.server`) default to binding `127.0.0.1` *inside the container*, which is a different loopback than the host's. Docker's `-p` only forwards traffic from the container's external interface, so a server bound to container-side 127.0.0.1 is unreachable from the host even when the port is "published." Symptom: `curl localhost:3000` from the host returns connection refused while the server is clearly running inside the container.

  Whenever the user opts into published ports, **figure out for each port what process will serve it and how that process gets told to bind on `0.0.0.0`** (all interfaces). Don't assume — different tools have different switches:
  - Vite: `vite --host` (shorthand for `--host 0.0.0.0`), or `server.host: '0.0.0.0'` in `vite.config.{ts,js}`.
  - Next.js: `next dev -H 0.0.0.0` (or `--hostname 0.0.0.0`).
  - Django: `python manage.py runserver 0.0.0.0:8000`.
  - Rails: `bin/rails server -b 0.0.0.0`.
  - Python http.server: `python -m http.server --bind 0.0.0.0`.
  - Express / FastAPI / Uvicorn / Flask: usually default to `0.0.0.0` already, but verify the project's start script doesn't pin `127.0.0.1`/`localhost`.

  Look at the project's start script (`package.json` `scripts.dev`, `Procfile`, `Makefile`, `pyproject.toml` scripts, README) and propose the concrete edit needed — a new flag, a config-file change, or a code change. If there's no obvious start script, surface this as a known-issue note in the next-steps printout (step 8) so the user knows to check when they boot the server.

- **"Do you anticipate using a native debugger (gdb, strace, lldb)?"** — default: no. If yes, we'll add `SYS_PTRACE` to the container's cap_add. This is looser hardening, so only opt in if you actually need it. Heuristic: if the project is C / C++ / Rust with unsafe, or if the user mentions "strace" / "perf" / "gdb" / kernel-level debugging, lean toward asking.

- **"Anything else that should be pre-installed in the dev image?"** — free-text (e.g. "gdb", "awscli", "the AWS CLI with our org's config").

### 4. Check for existing files

Before writing anything, check for:

- `.carthage/config.toml`, `.carthage/Dockerfile`, `.carthage/docker-compose.yaml` — if any exist, show the user a diff of what would change and ask before overwriting.
- Root `Dockerfile` or `docker-compose.yaml` — if they exist for the project's own purposes (e.g., production deploy), note them. Carthage files live under `.carthage/*` and won't conflict.

### 5. Generate from templates

Templates live alongside this skill at `templates/`. Render them with the values you've collected:

- **`.carthage/config.toml`** — from `carthage-config.toml.j2`. Fields:
  - `version = "1"` (schema version)
  - `base_image_tag = "v1"` (the `carthage-base` major to target)
  - `annexed_with_cli = "1.0.1"` (informational; whatever CLI version did the annex)
  - `service_name = "dev"`
  - `project_slug` — derive from project directory name, lowercase, replace non-alnum with `-`.

- **`.carthage/Dockerfile`** — from `Dockerfile.j2`. `FROM ghcr.io/speculative/carthage-base:v1`. Add `RUN` layers for detected runtimes. For Python, prefer installing pyenv only if the project pins a non-system version (`.python-version` file or `requires-python = ">=3.X"` constraint); otherwise the base image's Python 3.12 is fine. For Node, install nvm only if `.nvmrc` pins a version different from the base image's LTS.

- **`.carthage/docker-compose.yaml`** — from `docker-compose.yaml.j2`. One service named `dev` with:
  - `build.context: ..` (project root)
  - `build.dockerfile: .carthage/Dockerfile`
  - `build.args: { HOST_UID: ${HOST_UID}, HOST_GID: ${HOST_GID} }`
  - `cap_drop: [ALL]`, `cap_add: [CHOWN, SETUID, SETGID, DAC_OVERRIDE]`. Add `SYS_PTRACE` iff the user opted in during step 3.
  - `security_opt: [no-new-privileges:true]`
  - `pids_limit: 1000`
  - `mem_limit: ${CARTHAGE_MEM_LIMIT:-0}`, `cpus: ${CARTHAGE_CPUS:-0}` — both resolved by the CLI at `up` time; 0 means unlimited.
  - `environment: { CARTHAGE: "1", CARTHAGE_PROJECT: "<slug>" }` — `CARTHAGE_PROJECT` is read by the in-container tmux status bar.
  - Mounts: `..:/workspace`, `${HOME}/.claude:/home/carthage/.claude`, `${HOME}/.claude.json:/home/carthage/.claude.json` (rw — Claude Code login state lives here, sibling of `.claude/`), `${HOME}/.gitconfig:/home/carthage/.gitconfig:ro`, `${HOME}/.carthage/state/<slug>:/commandhistory:rw` (per-project shell-history dir; CLI creates the host side on `up`)
  - `working_dir: /workspace`, `init: true`, `tty: true`, `stdin_open: true`
  - Labels: `carthage.managed=true`, `carthage.project=<slug>`, `carthage.role=dev`. Sidecars get `carthage.role=postgres` / `redis` / etc.
  - **No** `ports:` block unless the user opted into published ports in step 3.
  - Sibling services (`postgres`, `redis`, etc.) go in the same file on the same dedicated `carthage-net` network (reachable by service name from `dev`). These also get labels and **no** published ports.

- **`.claude/skills/carthage-env/SKILL.md`** — from `carthage-env-SKILL.md.j2`. Pointer skill the in-container Claude instance discovers.

- **`.dockerignore`** at the project root — from `dockerignore.j2`, only if one doesn't already exist.

- **`.gitignore`** — append a `.carthage/last-build-hash` entry if not already present.

### 6. Verify the `carthage` CLI is installed on the host

Run `command -v carthage`. If missing, print:

```
uv tool install git+https://github.com/speculative/carthage.git
carthage fortify     # one-time host setup; installs this skill into ~/.claude/skills/
```

Do NOT install it yourself.

### 7. Run `carthage survey`

If the CLI is installed, run `carthage survey` (not `--deep` — that spins up a test container which is too slow for the annex flow). Report any failed checks.

### 8. Print next steps

End with:

> Next:
> 1. Review the generated files under `.carthage/` and `.claude/skills/carthage-env/`.
> 2. Commit them: `git add .carthage .claude/skills/carthage-env .dockerignore .gitignore && git commit -m "carthage: initial setup"`.
> 3. Run `carthage up` to build and start the container.
> 4. Run `carthage attach` to drop into the tmux session with Claude Code running.

## What you do NOT do

- **Don't commit anything.** The user runs `git commit` when they're ready.
- **Don't start the container.** That's the user's next manual step.
- **Don't install `carthage-base`** or prebuild. The first `carthage up` pulls and builds.
- **Don't install the `carthage` CLI.** Print instructions if it's missing.
- **Don't add project-specific runtimes or ports the user didn't confirm.** If detection was ambiguous, you asked in step 3 — trust that answer over your guess.
