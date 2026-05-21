# Contributing

Notes for working on Carthage itself. (User-facing docs live in [README.md](README.md).)

## How the three artifacts fit together

Carthage ships three things from this one repo:

1. **The CLI** — `carthage-cli` Python package, versioned in [pyproject.toml](pyproject.toml).
2. **The Claude skills** under [skill/](skill/) — bundled *into the CLI wheel* via [`tool.hatch.build.targets.wheel.force-include`](pyproject.toml).
3. **The `carthage-base` Docker image** — built from [base/](base/), published to GHCR by [.github/workflows/publish-base.yml](.github/workflows/publish-base.yml) on every `v*.*.*` git tag push. Users normally build a local `carthage-base-personal:<major>` layer on top with `carthage fortify`.

The CLI and the skills share one version because they're physically the same wheel. The base image is published from the same commit but is consumed independently. New projects normally inherit from `carthage-base-personal:v1`; existing older projects may still inherit directly from `ghcr.io/speculative/carthage-base:v1`.

## The trap: skill-template changes ARE CLI changes

Because [skill/](skill/) is `force-include`d into the wheel, **anything you edit under `skill/` is part of the CLI release** — not just code in `carthage/`. This is easy to forget and we've already been bitten by it.

Real example (v1.0.1): a one-line fix to [skill/carthage-annex/templates/docker-compose.yaml.j2](skill/carthage-annex/templates/docker-compose.yaml.j2) added the `~/.claude.json` bind mount. Without bumping the CLI version, `uv tool upgrade carthage-cli && carthage fortify` was a no-op for users — the new template never reached their host.

**Rule of thumb:** if your diff touches *anything* outside `base/`, it needs a CLI version bump. Touching `base/` requires a tag push (which `base/` ships through). Most non-trivial changes touch both.

## Releasing a new version

1. **Bump the version in all three places** (they must agree):
   - [pyproject.toml](pyproject.toml) `version`
   - [carthage/__init__.py](carthage/__init__.py) `__version__`
   - [skill/carthage-annex/SKILL.md](skill/carthage-annex/SKILL.md) frontmatter `version`

2. **If generated project files changed, bump `CURRENT_TEMPLATE_VERSION`** in [carthage/__init__.py](carthage/__init__.py) and update the `template_version` defaults in [skill/carthage-annex/templates/carthage-config.toml.j2](skill/carthage-annex/templates/carthage-config.toml.j2) and [skill/carthage-annex/templates/carthage-env-SKILL.md.j2](skill/carthage-annex/templates/carthage-env-SKILL.md.j2). Template versions are flat integers and only change when annex-generated committed files should change.

3. **Add a changelog entry for any template change** in [skill/carthage-annex/CHANGELOG.md](skill/carthage-annex/CHANGELOG.md). `/carthage-annex --upgrade` reads this file and surfaces entries newer than the project's `template_version`, so the entry is the *only* place an existing-project user sees the rationale for a template diff. Include a "Migration for existing projects" subsection with the manual edits if `--upgrade` isn't run. A template change without a changelog entry is invisible to existing-project users and counts as a regression.

4. **Don't update the doc-example versions** in [carthage/config.py](carthage/config.py) and [README.md](README.md) every release — they're illustrative. Update only when they'd actively mislead a reader.

5. **Don't update the E2E fixture configs** ([tests/e2e/fixtures/*/.carthage/config.toml](tests/e2e/fixtures/)) unless the test scenario intentionally needs the new generated surface. They represent projects annexed at a specific past template version.

6. **Commit, tag, push:**
   ```sh
   git commit -am "release: vX.Y.Z"
   git tag vX.Y.Z
   git push origin main
   git push origin vX.Y.Z
   ```
   The tag push triggers [.github/workflows/publish-base.yml](.github/workflows/publish-base.yml), which builds and publishes three GHCR tags from this commit: `:vX.Y.Z`, `:vX` (clobbered), `:latest` (clobbered).

7. **CLI distribution:** users install from `git+https://github.com/speculative/carthage.git`, so they pick up `main` on `uv tool upgrade`. There is no PyPI publish step (yet). If we add one, document it here.

## What changes go where

| If you're changing… | You need to… |
|---|---|
| Anything in [base/](base/) (Dockerfile, tmux.conf, entrypoint.sh, SANDBOX.md) | Push a `v*.*.*` tag — CI publishes the new base image. Users pick it up with `carthage build --pull`; projects using `carthage-base-personal` rebuild that local layer first. |
| Anything in [skill/](skill/) (templates, SKILL.md instructions) | Bump CLI version. Users pick it up with `uv tool upgrade carthage-cli && carthage fortify`. **Existing already-annexed projects do NOT auto-update** — their `.carthage/` files were generated from the *old* template and are committed in their repo. They need a re-annex (`/carthage-annex --upgrade`) or a manual edit. |
| Anything in [carthage/](carthage/) (CLI code) | Bump CLI version. `uv tool upgrade carthage-cli`. |
| Anything in [tests/](tests/) | Nothing user-visible. No version bump needed. |

## Compatibility policy

- Projects pin to a base-image *major* (e.g. `:v1`). Minor/patch bumps within a major flow out automatically on `carthage build --pull`.
- The CLI reads config schemas one major back (see [carthage/config.py](carthage/config.py)). Major bumps require explicit per-project migration.
- Old projects never auto-upgrade. The user is always in control.

## Versioning policy

The CLI package, bundled personal skills, and base-image release use standard
semver:

- **Major (`X.0.0`)** — breaking changes: a config schema bump, a base-image change that requires per-project migration, or a CLI flag/command removed/renamed in a way an existing project relies on.
- **Minor (`x.Y.0`)** — new features, additive: new CLI commands or flags, new tools or env defaults in the base image, new mounts in the compose template, new annex template fields. Anything users would notice as "I can do something I couldn't before."
- **Patch (`x.y.Z`)** — bug fixes only: behavior was supposed to work and didn't; it now does. No new capability.

When in doubt, prefer minor over patch — a too-low bump under-communicates change to users; the cost of a too-high bump is essentially zero.

Other version surfaces are intentionally separate:

- **Project config schema** — `schema_version` in `.carthage/config.toml`,
  represented as a flat integer. Bump only when the project TOML schema
  changes incompatibly.
- **Personal config schema** — `schema_version` in
  `~/.carthage/config.toml`, represented as a flat integer. Bump only when
  the personal TOML schema changes incompatibly.
- **Annex template bundle** — `template_version` in
  `.carthage/config.toml`, represented as a flat integer. Bump whenever
  generated committed project files should change: `.carthage/Dockerfile`,
  `.carthage/docker-compose.yaml`, `.carthage/config.toml`,
  `.dockerignore`, `.gitignore` managed entries, or project-local
  `carthage-env` skills.
- **Personal image freshness** — the
  `org.carthage.personal-image-hash` Docker label is an opaque content hash,
  not a user-facing version.

## Testing

E2E tests under [tests/e2e/](tests/e2e/) build images and spin up containers — they're slow and require Docker. They use committed fixture compose files (not the live template), so a template change won't break them — but it also means a template change with no fixture update can ship with broken-but-untested behavior. **When you change the compose template, mirror the change into the fixture compose files.**

Run E2E tests explicitly (uses the project's `.venv` with dev deps installed):
```sh
uv sync --all-extras    # one-time: pytest + project deps in .venv
uv run pytest -m e2e
```
