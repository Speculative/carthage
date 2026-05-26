# Carthage Examples

These projects are small, checked-in sandboxes for manual testing and documentation.
They are meant to be run with the local checkout of the Carthage CLI:

```bash
uv --project /home/jtao/code/carthage run carthage <command>
```

For personal config experiments, prefer `CARTHAGE_PERSONAL_CONFIG` instead of
changing your real `~/.carthage/config.toml`. Do not override `HOME` for these
manual examples; auth mounts such as `${HOME}/.codex` should still point at your
real host account.

