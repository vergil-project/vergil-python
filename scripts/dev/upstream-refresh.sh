#!/usr/bin/env bash
# vergil-python's upstream-refresh hook ([upstream-refresh].hook in vergil.toml;
# epic vergil-project/.github#472, spec §5.4).
#
# vrg-upstream-refresh runs it from the repo root with VRG_UPSTREAM_REPORT set.
# It refreshes every listed series in runtime.toml within itself, rewriting
# runtime.toml and the matching vergil.toml [[package]] entries together, adds
# a leading-edge series once the version window has it and PBS ships it
# (VRG_VERSION_WINDOW, when set, names a local window.json), and writes notes
# for the refresh PR body. It edits files only; the driver commits.
# Any failure exits non-zero before either file is written.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
: "${VRG_UPSTREAM_REPORT:?upstream-refresh.sh: VRG_UPSTREAM_REPORT must be set (vrg-upstream-refresh sets it)}"
# uv supplies the interpreter, as in packaging/build.sh.
exec uv run --no-project --quiet python3 "${ROOT}/scripts/dev/upstream_refresh.py"
