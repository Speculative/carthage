# Codex Smoke Project

This project exists to verify that Carthage can install, auth-mount, and launch
Codex inside the sandbox.

Useful checks:

- `python3 app.py` should print a short message.
- `which codex` should find the Codex CLI.
- `ls ~/.codex/auth.json` should show the host auth file mounted in.

