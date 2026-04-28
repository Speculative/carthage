# Contributing

Notes for working on Carthage itself. (User-facing docs live in [README.md](README.md).)

## How the three artifacts fit together

Carthage ships three things from this one repo:

1. **The CLI** — `carthage-cli` Python package, versioned in [pyproject.toml](pyproject.toml).
2. **The Claude skills** under [skill/](skill/) — bundled *into the CLI wheel* via [`tool.hatch.build.targets.wheel.force-include`](pyproject.toml).
3. **The `carthage-base` Docker image** — built from [base/](base/), published to GHCR by [.github/workflows/publish-base.yml](.github/workflows/publish-base.yml) on every `v*.*.*` git tag push.

The CLI and the skills share one version because they're physically the same wheel. The base image is published from the same commit but is consumed independently (projects pin `:v1` in their `.carthage/Dockerfile`).

## The trap: skill-template changes ARE CLI changes

Because [skill/](skill/) is `force-include`d into the wheel, **anything you edit under `skill/` is part of the CLI release** — not just code in `carthage/`. This is easy to forget and we've already been bitten by it.

Real example (v1.0.1): a one-line fix to [skill/carthage-annex/templates/docker-compose.yaml.j2](skill/carthage-annex/templates/docker-compose.yaml.j2) added the `~/.claude.json` bind mount. Without bumping the CLI version, `uv tool upgrade carthage-cli && carthage fortify` was a no-op for users — the new template never reached their host.

**Rule of thumb:** if your diff touches *anything* outside `base/`, it needs a CLI version bump. Touching `base/` requires a tag push (which `base/` ships through). Most non-trivial changes touch both.

## Releasing a new version

1. **Bump the version in all three places** (they must agree):
   - [pyproject.toml](pyproject.toml) `version`
   - [carthage/__init__.py](carthage/__init__.py) `__version__`
   - [skill/carthage-annex/SKILL.md](skill/carthage-annex/SKILL.md) frontmatter `version`

2. **Update the `annexed_with_cli` default** in [skill/carthage-annex/templates/carthage-config.toml.j2](skill/carthage-annex/templates/carthage-config.toml.j2) and [skill/carthage-annex/templates/carthage-env-SKILL.md.j2](skill/carthage-annex/templates/carthage-env-SKILL.md.j2) — both default to a version string when not overridden. Keep them in sync with the CLI version.

3. **Add a changelog entry for any template change** in [skill/carthage-annex/CHANGELOG.md](skill/carthage-annex/CHANGELOG.md). `/carthage-annex --upgrade` reads this file and surfaces entries newer than the project's `annexed_with_cli`, so the entry is the *only* place an existing-project user sees the rationale for a template diff. Include a "Migration for existing projects" subsection with the manual edits if `--upgrade` isn't run. A template change without a changelog entry is invisible to existing-project users and counts as a regression.

4. **Don't update the doc-example versions** in [carthage/config.py](carthage/config.py) and [README.md](README.md) every release — they're illustrative. Update only when they'd actively mislead a reader.

5. **Don't update the E2E fixture configs** ([tests/e2e/fixtures/*/.carthage/config.toml](tests/e2e/fixtures/)) — they represent projects annexed at a specific past CLI version. The `annexed_with_cli` field is informational and not runtime-checked.

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
| Anything in [base/](base/) (Dockerfile, tmux.conf, entrypoint.sh, SANDBOX.md) | Push a `v*.*.*` tag — CI publishes the new base image. Users pick it up with `carthage build --pull`. |
| Anything in [skill/](skill/) (templates, SKILL.md instructions) | Bump CLI version. Users pick it up with `uv tool upgrade carthage-cli && carthage fortify`. **Existing already-annexed projects do NOT auto-update** — their `.carthage/` files were generated from the *old* template and are committed in their repo. They need a re-annex (`/carthage-annex --upgrade`) or a manual edit. |
| Anything in [carthage/](carthage/) (CLI code) | Bump CLI version. `uv tool upgrade carthage-cli`. |
| Anything in [tests/](tests/) | Nothing user-visible. No version bump needed. |

## Compatibility policy

- Projects pin to a base-image *major* (e.g. `:v1`). Minor/patch bumps within a major flow out automatically on `carthage build --pull`.
- The CLI reads config schemas one major back (see [carthage/config.py](carthage/config.py)). Major bumps require explicit per-project migration.
- Old projects never auto-upgrade. The user is always in control.

## Versioning policy

Standard semver, applied to the unified CLI/skills/base-image version:

- **Major (`X.0.0`)** — breaking changes: a config schema bump, a base-image change that requires per-project migration, or a CLI flag/command removed/renamed in a way an existing project relies on.
- **Minor (`x.Y.0`)** — new features, additive: new CLI commands or flags, new tools or env defaults in the base image, new mounts in the compose template, new annex template fields. Anything users would notice as "I can do something I couldn't before."
- **Patch (`x.y.Z`)** — bug fixes only: behavior was supposed to work and didn't; it now does. No new capability.

When in doubt, prefer minor over patch — a too-low bump under-communicates change to users; the cost of a too-high bump is essentially zero.

## Testing

E2E tests under [tests/e2e/](tests/e2e/) build images and spin up containers — they're slow and require Docker. They use committed fixture compose files (not the live template), so a template change won't break them — but it also means a template change with no fixture update can ship with broken-but-untested behavior. **When you change the compose template, mirror the change into the fixture compose files.**

Run E2E tests explicitly (uses the project's `.venv` with dev deps installed):
```sh
uv sync --all-extras    # one-time: pytest + project deps in .venv
uv run pytest -m e2e
```
