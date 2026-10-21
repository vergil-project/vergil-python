#!/usr/bin/env bash
# Offline tests for the package build script (packaging/build.sh): the series
# consistency guards and the /opt/vergil/python/<series> install root.
# Run in the dev container: vrg-container-run -- bash tests/check-build.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
uv run --no-project --quiet python3 -m unittest discover -s "${ROOT}/tests" -p 'test_build.py' -v
