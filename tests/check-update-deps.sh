#!/usr/bin/env bash
# Offline unit tests for the dependency-update hook (scripts/dev/update_deps.py).
# Run in the dev container: vrg-container-run -- bash tests/check-update-deps.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
uv run --no-project --quiet python3 -m unittest discover -s "${ROOT}/tests" -p 'test_update_deps.py' -v
