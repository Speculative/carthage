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

### 3. Investigate the project, then confirm a proposed annex spec

Don't make the user enumerate their own dependencies — most users haven't thought through "do I need Postgres" abstractly, but the answer is usually visible in the repo. Investigate first; ask only what the code can't tell you.

#### 3a. Investigate

**Read the project broadly enough to make an informed proposal about every aspect of the dev environment that affects the container build or runtime.** This is open-ended on purpose: anything that would make the difference between "the user runs `carthage attach` and can immediately work" and "the user hits a wall and has to re-annex" is in scope. Think about the whole development flow — building, running, testing, debugging, deploying, talking to external services — and ask: what does each step need from the container?

For each potential factor, decide whether to include it (with a one-line justification you can show the user), exclude it, or flag as uncertain.

A non-exhaustive **example** list of factors that often come up — useful as a starting checklist, but the project may need things outside this list and may not need things on it:

- **Sidecar services** (Postgres, MySQL, Redis, RabbitMQ, Elasticsearch, MinIO, …): look at `docker-compose.yml` if one exists, ORM config (`alembic.ini`, `prisma/schema.prisma`, `config/database.yml`, `settings.py`), env templates (`.env.example`), and connection-string usage in code.
- **Native debugging** (`SYS_PTRACE` cap_add): C/C++/Rust-with-unsafe projects, or anything where strace/gdb/lldb shows up in the README, scripts, or test setup.
- **System packages** beyond the base image: native compilers (`build-essential`, `pkg-config`), library headers (`libpq-dev`, `libssl-dev`, `libffi-dev`), media tooling (`ffmpeg`, `imagemagick`, `tesseract`), browser automation (`chromium`, `playwright` deps), CLI tools the project shells out to (`awscli`, `gh`, `kubectl`, `terraform`).
- **Runtime versions** the base image doesn't ship: a `.python-version`, `.nvmrc`, `.tool-versions`, `rust-toolchain.toml`, or a `requires-python`/`engines` constraint that excludes the base default. **The base image ships Python 3.12 and Node 20.x.** `uv` is also present and fetches alternate Pythons on demand, so a `requires-python` mismatch is usually a soft constraint, not a hard requirement to install pyenv.
- **Build/test toolchain quirks**: `pre-commit` hooks that need specific binaries, GPU/CUDA requirements, anything pinned in CI that's invisible from the manifest alone.
- **Extra host paths the project reads or writes outside its own directory.** Many dev workflows reach across the filesystem — a sibling repo the user is co-developing, a checked-out fork mounted as a build dependency, a shared dataset/asset directory, or per-machine credentials. The `.carthage/` mount only covers the project root; anything outside it is invisible from the container unless explicitly mounted. Look for hard-coded absolute paths in code, config, and scripts (e.g. `/cpython/build-opt/python`, `~/data/...`); references to "the X repo" / "checkout Y next to this one" in the README; and CLI tools or build steps that assume a sibling working tree.

Don't stop at this list. **Inventory the test suite (`tests/`, `test/`, `e2e/`, `pytest`/`jest`/`vitest` config) and the CI pipeline (`.github/workflows/`, `.gitlab-ci.yml`, `Makefile`, top-level `scripts/`, `bin/`)** — these are where build-time deps that don't appear in the runtime manifest hide. Examples: `playwright install-deps chromium` for headless e2e, `tesseract-ocr` for OCR fixtures, CUDA toolkit for GPU tests. The CI config tells you what an outside machine has to install to make the project work; that's a strong proxy for what the dev container needs. Also read the README, `CONTRIBUTING.md`, and any `HANDOFF.md` / design notes for things that don't fit anywhere else.

#### 3b. Ask about workflow-shaped intent: ports and extra host mounts

Two dimensions can't be derived from the code alone — they depend on how the user develops, not what the code says. Ask both, and frame each so the user can answer with concrete paths/numbers, not abstract yes/no.

**Ports.** Whether the user wants to expose ports to the host browser is a developer-workflow preference, not a code property:

> **"Any ports you want *published to the host* for local browser testing?"** — e.g., `3000`, `8000`, `5173`. Default: **none**.

Tradeoff to surface:
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

**Extra host mounts.** The project root is mounted at `/workspace` automatically. But many workflows need *additional* host paths visible inside the container — a sibling repo, a vendored fork, a shared dataset, an assets directory. If you found hard-coded absolute paths or "see also: ../foo" references during 3a, propose them and ask. If you didn't, still ask — users often forget about a `~/data/` directory or a sibling checkout until they hit the wall:

> **"Any host paths outside this project the dev container will need to reach?"** Frame it concretely:
> - **Code outside the project** you want to read or edit alongside this one (a sibling repo, a checked-out fork or library you're co-developing).
> - **Data directories** the project reads from or writes to (datasets, fixtures, large media, persistent caches).
> - **Anywhere code in this project reaches outside its own directory** (hard-coded paths in scripts, env vars pointing at host paths, "extract this tarball to `/srv/...`" in setup docs).
>
> Default: **none.** Each extra mount should be specified as `<host path>:<container path>` plus `:ro` for read-only or `:rw` for read-write. Read-only is safer when you don't intend to write — e.g. mounting a sibling repo just to grep through it.

If you proposed any from your investigation, list them in the spec table. If the user volunteers more, add them.

#### 3c. Present the proposed spec and confirm

Show the user a single consolidated proposal — not one question at a time. Format it so they can scan and correct in one round:

```
Based on the project, here's what I'm proposing for the dev container:

  Sidecars:        postgres:16  (seen in docker-compose.yml + alembic.ini)
                   redis:7      (used by tasks.py worker queue)
  System packages: libpq-dev, build-essential  (psycopg compiles from source)
                   awscli       (scripts/deploy.sh shells out to it)
  Native debugging: no          (pure Python, no gdb/strace usage)
  Runtime:         Python 3.12  (matches base image; pyenv not needed)
  Published ports: 8000 (FastAPI) — note: uvicorn already binds 0.0.0.0
                   5173 (Vite)  — needs `--host` added to `npm run dev`
  Extra host mounts: ~/data/corpus:/data/corpus:ro
                                  (scripts/eval.py reads /data/corpus/*.jsonl)
                   (need to ask: anything else outside the project?)

Anything wrong, missing, or that you want to drop?
```

For uncertain items, mark them explicitly (`?` or "uncertain — flag for review") rather than silently picking. Accept free-form corrections; iterate one more round if the user adds something substantial.

Don't ask permission for *every* item individually — that's the trap the old elicitation flow fell into. The user is correcting a proposal, not authoring one from scratch.

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
  - Mounts: `..:/workspace`, `${HOME}/.claude:/home/carthage/.claude`, `${HOME}/.claude.json:/home/carthage/.claude.json` (rw — Claude Code login state lives here, sibling of `.claude/`), `${HOME}/.gitconfig:/home/carthage/.gitconfig:ro`, `${HOME}/.carthage/state/<slug>:/commandhistory:rw` (per-project shell-history dir; CLI creates the host side on `up`). Append any extra host mounts the user confirmed in step 3b (preserve their `:ro`/`:rw` mode and the exact host path — don't rewrite to relative paths).
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
- **Don't add project-specific runtimes, sidecars, system packages, or ports that weren't in the spec the user confirmed in step 3c.** If you find new evidence mid-generation that something else is needed, loop back and re-confirm — don't silently expand the scope.
