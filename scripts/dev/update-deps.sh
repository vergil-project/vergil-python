#!/usr/bin/env bash
# vergil-python's dependency-update hook ([dependency-update].hook in
# vergil.toml; epic vergil-project/.github#381, spec §6.2-§6.3).
#
# vrg-update-deps runs it in the dev container from the repo root with
# VRG_DEPS_REPORT set. It bumps the PBS runtime pin in runtime.toml and
# vergil.toml [package] together, and reports a newer CPython minor. It edits
# files only; the driver commits. Any failure exits non-zero.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
: "${VRG_DEPS_REPORT:?update-deps.sh: VRG_DEPS_REPORT must be set (vrg-update-deps sets it)}"
# uv supplies the interpreter, as in packaging/build.sh.
exec uv run --no-project --quiet python3 "${ROOT}/scripts/dev/update_deps.py"
