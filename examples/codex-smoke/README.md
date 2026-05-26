# Codex Smoke Project

This is a minimal Carthage project for manually exercising the Codex profile.
It uses `carthage-base-personal:v2`, so `carthage fortify` must build the
personal image before `carthage up`.

From this directory:

```bash
./scripts/run.sh
```

To stop the container afterward:

```bash
./scripts/down.sh
```

Equivalent manual commands:

```bash
export CARTHAGE_PERSONAL_CONFIG=/home/jtao/code/carthage/examples/personal-config.codex.toml

uv --project /home/jtao/code/carthage run carthage fortify
uv --project /home/jtao/code/carthage run carthage up --force-rebuild
uv --project /home/jtao/code/carthage run carthage attach
```

Inside tmux, the session should be `carthage-session` with a `codex` window:

```bash
tmux display-message -p '#S'
tmux list-windows
which codex
codex --version
ls ~/.codex/auth.json
```

If Codex is not logged in on the host yet, run `codex --login` on the host
first. Keep `HOME` unchanged when running this example so `${HOME}/.codex`
mounts your real Codex auth directory into the container.
