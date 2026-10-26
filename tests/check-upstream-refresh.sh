#!/usr/bin/env bash
# Offline unit tests for the upstream-refresh hook (scripts/dev/upstream_refresh.py).
# Run in the dev container: vrg-container-run -- bash tests/check-upstream-refresh.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
uv run --no-project --quiet python3 -m unittest discover -s "${ROOT}/tests" -p 'test_upstream_refresh.py' -v
