#!/usr/bin/env bash
# Fetch, verify and lay out one pinned python-build-standalone (PBS) runtime
# into the staging root (spec §6.2, §6.6).
#
# Run by the `staged` builder as each [[package]]'s [package.staged].build-command,
# from the repo root, with:
#   VRG_STAGING_ROOT     an empty directory standing in for /
#   VRG_TARGET_ARCH      amd64 | arm64
#   VRG_PACKAGE_NAME     the package being built, vergil-python<series>
#   VRG_PACKAGE_VERSION  its full version, <cpython>+<pbs_tag>.<X.Y.Z>
#
# The repo builds one runtime per listed CPython series (epic
# vergil-project/.github#472, spec §5.3): runtime.toml has one [[series]] entry
# and vergil.toml one [[package]] entry per series. VRG_PACKAGE_NAME selects
# the series; the result is $VRG_STAGING_ROOT/opt/vergil/python/<series>/
# holding the PBS install_only_stripped tree plus a PEP 668 EXTERNALLY-MANAGED
# marker. Any mismatch (a series without a package or the reverse, config
# drift, a cpython outside its series, a version not carrying the inputs,
# unknown arch, checksum, archive layout) is fatal.
set -euo pipefail

cd "$(dirname "$0")/.."

die() {
  echo "build.sh: $*" >&2
  exit 1
}

: "${VRG_STAGING_ROOT:?build.sh: VRG_STAGING_ROOT must be set}"
: "${VRG_TARGET_ARCH:?build.sh: VRG_TARGET_ARCH must be set}"
: "${VRG_PACKAGE_NAME:?build.sh: VRG_PACKAGE_NAME must be set}"
: "${VRG_PACKAGE_VERSION:?build.sh: VRG_PACKAGE_VERSION must be set}"
[ -d "$VRG_STAGING_ROOT" ] || die "VRG_STAGING_ROOT=$VRG_STAGING_ROOT is not a directory"

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

# select_series NAME ARCH_KEY : check that runtime.toml's [[series]] and
# vergil.toml's [[package]] entries agree one-to-one (each series x.y has
# exactly one package vergil-python<x.y>, and every cpython is a patch of its
# series), then print the selected entry's series, cpython, pbs_tag,
# upstream-version, smoke and ARCH_KEY sha256, one per line. Python's tomllib
# does the parsing; uv supplies the interpreter (raw build images ship no
# Python of their own).
select_series() {
  uv run --no-project --quiet python3 -c '
import re, sys, tomllib

name, arch = sys.argv[1], sys.argv[2]

def die(msg):
    sys.exit(f"build.sh: {msg}")

def load(path):
    with open(path, "rb") as fh:
        return tomllib.load(fh)

def text(table, key, where):
    value = table.get(key)
    if not isinstance(value, str) or not value:
        die(f"{where} {key} must be a non-empty string")
    return value

entries = load("runtime.toml").get("series")
if not isinstance(entries, list) or not entries or not all(isinstance(e, dict) for e in entries):
    die("runtime.toml must hold one or more [[series]] entries")
packages = load("vergil.toml").get("package")
if isinstance(packages, dict):
    packages = [packages]
if not isinstance(packages, list) or not packages or not all(isinstance(p, dict) for p in packages):
    die("vergil.toml has no [[package]] entries")

by_series = {}
for entry in entries:
    series = text(entry, "series", "runtime.toml [[series]]")
    cpython = text(entry, "cpython", f"runtime.toml series {series!r}")
    tag = text(entry, "pbs_tag", f"runtime.toml series {series!r}")
    if not re.fullmatch(r"[0-9]+\.[0-9]+", series):
        die(f"runtime.toml series {series!r} is not an x.y CPython series")
    if not re.fullmatch(re.escape(series) + r"\.[0-9]+", cpython):
        die(f"runtime.toml cpython {cpython!r} is not a patch of series {series!r}")
    if not re.fullmatch(r"[0-9]{8}", tag):
        die(f"runtime.toml series {series!r} pbs_tag {tag!r} is not a YYYYMMDD PBS release tag")
    if series in by_series:
        die(f"runtime.toml lists series {series!r} twice")
    by_series[series] = entry

by_name = {}
for package in packages:
    pname = text(package, "name", "vergil.toml [[package]]")
    if pname in by_name:
        die(f"vergil.toml lists package {pname!r} twice")
    by_name[pname] = package

expected = {f"vergil-python{series}": series for series in by_series}
unpackaged = sorted(expected[n] for n in set(expected) - set(by_name))
unlisted = sorted(set(by_name) - set(expected))
if unpackaged or unlisted:
    die(
        "runtime.toml [[series]] and vergil.toml [[package]] disagree: "
        f"series without a package {unpackaged}, packages without a series {unlisted}"
    )

if name not in by_name:
    wanted = name.removeprefix("vergil-python")
    listed = ", ".join(sorted(by_series))
    die(
        f"VRG_PACKAGE_NAME {name!r} is not a listed series: runtime.toml has no [[series]] "
        f"{wanted!r} (listed: {listed})"
    )
series = expected[name]
entry, package = by_series[series], by_name[name]
if "version" in package:
    die(f"vergil.toml [[package]] {name!r} sets version; a runtime package sets upstream-version only")
sha = entry.get("sha256")
if not isinstance(sha, dict):
    die(f"runtime.toml series {series!r} has no [series.sha256] table")
print(series)
print(entry["cpython"])
print(entry["pbs_tag"])
print(text(package, "upstream-version", f"vergil.toml [[package]] {name!r}"))
print(text(package, "smoke", f"vergil.toml [[package]] {name!r}"))
print(text(sha, arch, f"runtime.toml series {series!r} sha256"))
' "$@"
}

selected=$(select_series "$VRG_PACKAGE_NAME" "$key")
mapfile -t fields <<<"$selected"
[ "${#fields[@]}" -eq 6 ] || die "internal: expected 6 fields for $VRG_PACKAGE_NAME, got ${#fields[@]}"
series=${fields[0]}
cpython=${fields[1]}
tag=${fields[2]}
upstream=${fields[3]}
smoke=${fields[4]}
sum=${fields[5]}
inputs="${cpython}+${tag}"

# runtime.toml is the pin; the [[package]] entry and the version the builder
# computed from it must carry the same inputs.
interpreter_path="/opt/vergil/python/${series}/bin/python${series}"
[ "$VRG_PACKAGE_NAME" = "vergil-python${series}" ] \
  || die "VRG_PACKAGE_NAME '$VRG_PACKAGE_NAME' != 'vergil-python${series}' (runtime.toml series)"
[ "$upstream" = "$inputs" ] \
  || die "vergil.toml [[package]] '$VRG_PACKAGE_NAME' upstream-version '$upstream' != '$inputs' (runtime.toml)"
release=${VRG_PACKAGE_VERSION#"${inputs}."}
[[ "$VRG_PACKAGE_VERSION" == "${inputs}."* && "$release" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
  || die "VRG_PACKAGE_VERSION '$VRG_PACKAGE_VERSION' is not '${inputs}.<X.Y.Z>' (runtime.toml inputs plus the repo version)"
[[ "$smoke " == *"${interpreter_path} "* ]] \
  || die "vergil.toml [[package]] '$VRG_PACKAGE_NAME' smoke does not run ${interpreter_path} (runtime.toml series)"

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

dest="$VRG_STAGING_ROOT/opt/vergil/python/${series}"
mkdir -p "$dest"
tar -xzf "$work/$archive" -C "$dest" --strip-components=1 --no-same-owner

interpreter="$dest/bin/python${series}"
stdlib="$dest/lib/python${series}"
[ -x "$interpreter" ] || die "expected interpreter $interpreter is missing"
[ -d "$stdlib" ] || die "expected stdlib directory $stdlib is missing"

# PEP 668: nobody may pip install into the shared runtime.
printf '%s\n' \
  '[externally-managed]' \
  'Error=This interpreter is the shared vergil runtime. Install into a product venv, never into the runtime.' \
  >"$stdlib/EXTERNALLY-MANAGED"

echo "build.sh: staged CPython ${cpython} (series ${series}, PBS ${tag}, ${triple}) as ${VRG_PACKAGE_NAME} ${VRG_PACKAGE_VERSION} at ${dest#"$VRG_STAGING_ROOT"}"
