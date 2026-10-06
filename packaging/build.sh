#!/usr/bin/env bash
# Fetch, verify and lay out the pinned python-build-standalone (PBS) runtime
# into the staging root (spec §6.2, §6.6).
#
# Run by the `staged` builder as [package.staged].build-command, from the repo
# root, with:
#   VRG_STAGING_ROOT  an empty directory standing in for /
#   VRG_TARGET_ARCH   amd64 | arm64
#
# The result is $VRG_STAGING_ROOT/opt/vergil/python/<cpython>/ holding the PBS
# install_only_stripped tree plus a PEP 668 EXTERNALLY-MANAGED marker. Any
# mismatch (config drift, unknown arch, checksum, archive layout) is fatal.
set -euo pipefail

cd "$(dirname "$0")/.."

die() {
  echo "build.sh: $*" >&2
  exit 1
}

: "${VRG_STAGING_ROOT:?build.sh: VRG_STAGING_ROOT must be set}"
: "${VRG_TARGET_ARCH:?build.sh: VRG_TARGET_ARCH must be set}"
[ -d "$VRG_STAGING_ROOT" ] || die "VRG_STAGING_ROOT=$VRG_STAGING_ROOT is not a directory"

# read_toml FILE KEY... : print the string at the nested KEY path of a TOML
# file. Python's tomllib does the parsing; uv supplies the interpreter (raw
# build images ship no Python of their own).
read_toml() {
  uv run --no-project --quiet python3 -c '
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    value = tomllib.load(fh)
for key in sys.argv[2:]:
    value = value[key]
where = ".".join(sys.argv[2:])
if not isinstance(value, str) or not value:
    sys.exit(f"{sys.argv[1]}: {where} must be a non-empty string")
print(value)
' "$@"
}

cpython=$(read_toml runtime.toml cpython)
tag=$(read_toml runtime.toml pbs_tag)
name=$(read_toml vergil.toml package name)
version=$(read_toml vergil.toml package version)

# runtime.toml is the pin; vergil.toml must name the same runtime.
[ "$name" = "vergil-python${cpython}" ] \
  || die "vergil.toml [package].name '$name' != 'vergil-python${cpython}' (runtime.toml)"
[ "$version" = "${cpython}+${tag}" ] \
  || die "vergil.toml [package].version '$version' != '${cpython}+${tag}' (runtime.toml)"

case "$VRG_TARGET_ARCH" in
  amd64)
    triple=x86_64-unknown-linux-gnu
    key=x86_64
    ;;
  arm64)
    triple=aarch64-unknown-linux-gnu
    key=aarch64
    ;;
  *) die "unsupported VRG_TARGET_ARCH=$VRG_TARGET_ARCH (expected amd64 or arm64)" ;;
esac
sum=$(read_toml runtime.toml sha256 "$key")

# Download outside the staging root so only the runtime tree is packaged.
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
archive="cpython-${cpython}+${tag}-${triple}-install_only_stripped.tar.gz"
url="https://github.com/astral-sh/python-build-standalone/releases/download/${tag}/${archive}"
echo "build.sh: fetching $url"
curl -fsSL --retry 3 -o "$work/$archive" "$url"
(cd "$work" && echo "${sum}  ${archive}" | sha256sum -c -)

# The PBS archive root is python/; nothing else may sit at the top level.
roots=$(tar -tzf "$work/$archive" | cut -d/ -f1 | sort -u)
[ "$roots" = "python" ] || die "unexpected archive top level in $archive: $roots"

dest="$VRG_STAGING_ROOT/opt/vergil/python/${cpython}"
mkdir -p "$dest"
tar -xzf "$work/$archive" -C "$dest" --strip-components=1 --no-same-owner

minor="${cpython%.*}"
interpreter="$dest/bin/python${minor}"
stdlib="$dest/lib/python${minor}"
[ -x "$interpreter" ] || die "expected interpreter $interpreter is missing"
[ -d "$stdlib" ] || die "expected stdlib directory $stdlib is missing"

# PEP 668: nobody may pip install into the shared runtime.
printf '%s\n' \
  '[externally-managed]' \
  'Error=This interpreter is the shared vergil runtime. Install into a product venv, never into the runtime.' \
  >"$stdlib/EXTERNALLY-MANAGED"

echo "build.sh: staged CPython ${cpython} (PBS ${tag}, ${triple}) at ${dest#"$VRG_STAGING_ROOT"}"
