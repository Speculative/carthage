#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
example_dir="$(cd -- "${script_dir}/.." && pwd)"
repo_dir="$(cd -- "${example_dir}/../.." && pwd)"

export CARTHAGE_PERSONAL_CONFIG="${repo_dir}/examples/personal-config.codex.toml"

cd "${example_dir}"

uv --project "${repo_dir}" run carthage fortify
uv --project "${repo_dir}" run carthage up --force-rebuild
uv --project "${repo_dir}" run carthage attach

