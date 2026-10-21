"""vergil-python's dependency-update logic (epic vergil-project/.github#381, spec §6.3).

Run by scripts/dev/update-deps.sh, the ``[dependency-update].hook`` that
``vrg-update-deps`` runs in the dev container. The package is one runtime per
CPython series (``vergil-python<x.y>``; epic vergil-project/.github#407, spec
§7), and the CPython patch floats within that series. The hook:

1. resolves the newest python-build-standalone (PBS) release;
2. reads that release's ``SHA256SUMS`` and takes the newest CPython patch
   **within** ``runtime.toml``'s ``series`` that it ships as
   ``install_only_stripped`` for both architectures;
3. rewrites ``runtime.toml`` (``cpython``, ``pbs_tag``, per-arch ``sha256``) and
   ``vergil.toml [package]`` (``version``, ``summary``) together, so
   packaging/build.sh's consistency guard holds. The package ``name`` and the
   ``smoke`` interpreter path name the series, so they never change here; the
   hook only checks that they name the pinned series;
4. appends a held-back report line to ``$VRG_DEPS_REPORT`` when PBS ships a
   newer stable CPython minor. A new series is reported, never bumped: moving
   ``series`` is a reviewed PR, made after the containers add the version.

Stdlib only. Every failure (network, parse, a missing architecture or checksum,
an unexpected file layout, a series mismatch) exits non-zero before either file
is written, so a run never leaves a partial rewrite. The hook edits files only;
the driver commits.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import NoReturn

_PBS = "astral-sh/python-build-standalone"
LATEST_URL = f"https://github.com/{_PBS}/releases/latest"
_ARCHES = {"x86_64": "x86_64-unknown-linux-gnu", "aarch64": "aarch64-unknown-linux-gnu"}
_TRIPLES = {triple: arch for arch, triple in _ARCHES.items()}
# Stable releases only: a pre-release (3.16.0a1, 3.16.0rc1) does not match.
_ASSET_RE = re.compile(
    r"^([0-9a-f]{64})\s+\*?cpython-(\d+\.\d+\.\d+)\+(\d{8})-(\S+)-install_only_stripped\.tar\.gz$"
)
_TAG_RE = re.compile(r"\d{8}")
_SERIES_RE = re.compile(r"\d+\.\d+")

Fetch = Callable[[str], str]


def _die(msg: str) -> NoReturn:
    raise SystemExit(f"update-deps: {msg}")


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _minor(version: str) -> str:
    return ".".join(version.split(".")[:2])


def sums_url(tag: str) -> str:
    return f"https://github.com/{_PBS}/releases/download/{tag}/SHA256SUMS"


def fetch(url: str) -> str:
    """GET *url*. For the ``releases/latest`` URL, return the redirect target."""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - fixed https URLs
            if url == LATEST_URL:
                return resp.geturl()
            return resp.read().decode("utf-8")
    except OSError as exc:  # URLError, HTTPError and timeouts are all OSError
        _die(f"fetching {url} failed: {exc}")


def check_series(series: object, cpython: object) -> None:
    """Fail unless *series* is an ``x.y`` and *cpython* is an ``x.y.z`` patch within it."""
    if not isinstance(series, str) or not _SERIES_RE.fullmatch(series):
        _die(f"runtime.toml series {series!r} is not an x.y CPython series")
    if not isinstance(cpython, str) or not re.fullmatch(rf"{re.escape(series)}\.\d+", cpython):
        _die(f"runtime.toml cpython {cpython!r} is not a patch of series {series}")


def smoke_interpreter(series: str) -> str:
    """The packaged interpreter path for *series*, which ``[package].smoke`` must run."""
    return f"/opt/vergil/python/{series}/bin/python{series}"


def parse_sums(text: str, tag: str) -> dict[str, dict[str, str]]:
    """Map each stable CPython version in *tag*'s SHA256SUMS to ``{arch: sha256}``.

    Only linux-gnu ``install_only_stripped`` assets for the two packaged
    architectures count. A version shipped for only one architecture is kept
    (partial) so the caller can fail loud on it rather than silently skip it.
    """
    by_version: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        m = _ASSET_RE.match(line.strip())
        if m is None or m[3] != tag or m[4] not in _TRIPLES:
            continue
        sha, version, arch = m[1], m[2], _TRIPLES[m[4]]
        seen = by_version.setdefault(version, {})
        if arch in seen and seen[arch] != sha:
            _die(f"SHA256SUMS for PBS {tag} lists two checksums for CPython {version} {arch}")
        seen[arch] = sha
    if not by_version:
        _die(f"SHA256SUMS for PBS {tag} lists no install_only_stripped linux-gnu CPython assets")
    return by_version


def pick(series: str, current: str, versions: Iterable[str]) -> tuple[str, str | None]:
    """Return (newest patch within *series*, newest newer stable minor or None).

    Only *series* is ever a bump target. A newer minor is returned separately,
    for the held-back report, and is never chosen.
    """
    versions = list(versions)
    same = [v for v in versions if _minor(v) == series]
    if not same:
        _die(f"the newest PBS release ships no CPython {series}.x")
    target = max(same, key=_key)
    if _key(target) < _key(current):
        _die(f"the newest PBS release ships CPython {target}, older than the pinned {current}")
    newer = [v for v in versions if _key(v)[:2] > _key(series)]
    return target, (max(newer, key=_key) if newer else None)


def _sub_once(pattern: str, repl: Callable[[re.Match[str]], str], text: str, what: str) -> str:
    out, count = re.subn(pattern, repl, text, flags=re.M)
    if count != 1:
        _die(f"expected exactly one {what}, found {count}")
    return out


def render_runtime(text: str, cpython: str, tag: str, sums: dict[str, str]) -> str:
    """Rewrite runtime.toml's values in place, keeping its comments and layout.

    ``series`` is never rewritten, and the new ``cpython`` must lie within it.
    """
    series = tomllib.loads(text).get("series")
    check_series(series, cpython)
    if set(sums) != set(_ARCHES):
        _die(f"CPython {cpython} in PBS {tag} has checksums for {sorted(sums)}, need {sorted(_ARCHES)}")
    out = text
    for key, value in (("cpython", cpython), ("pbs_tag", tag), *((arch, sums[arch]) for arch in _ARCHES)):
        out = _sub_once(rf'^({key} = ")[^"]*"$', lambda m, v=value: f'{m[1]}{v}"', out, f"runtime.toml {key}")
    data = tomllib.loads(out)
    got = (data.get("series"), data.get("cpython"), data.get("pbs_tag"), data.get("sha256"))
    if got != (series, cpython, tag, sums):
        _die("rewritten runtime.toml does not parse back to the new pin")
    return out


def rewrite_package(
    text: str, series: str, old_cpython: str, old_tag: str, new_cpython: str, new_tag: str
) -> str:
    """Rewrite vergil.toml's ``[package]`` version and summary for a new pin.

    Only the ``[package]`` table is touched. Its ``name`` must be
    ``vergil-python<series>`` and its ``smoke`` must run the series
    interpreter; neither changes within a series, so both are checked, never
    rewritten. ``version`` and ``summary`` must name the old pin exactly as
    packaging/build.sh expects. Any mismatch fails.
    """
    check_series(series, new_cpython)
    lines = text.splitlines(keepends=True)
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "[package]")
    except StopIteration:
        _die("vergil.toml has no [package] table")
    package = tomllib.loads(text)["package"]
    if package.get("name") != f"vergil-python{series}":
        _die(f"vergil.toml [package].name {package.get('name')!r} is not 'vergil-python{series}'")
    smoke = package.get("smoke")
    if not isinstance(smoke, str) or f"{smoke_interpreter(series)} " not in f"{smoke} ":
        _die(f"vergil.toml [package].smoke does not run {smoke_interpreter(series)}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    block = "".join(lines[start + 1 : end])
    old_ver = re.escape(old_cpython)
    # (pattern, replacement for the whole match, what) -- group 1 is the kept line prefix.
    edits = [
        (rf'^(version = "){old_ver}\+{re.escape(old_tag)}"$', f'{new_cpython}+{new_tag}"', "[package].version"),
        (
            rf'^(summary = ".*)CPython {old_ver} \(python-build-standalone {re.escape(old_tag)}\)',
            f"CPython {new_cpython} (python-build-standalone {new_tag})",
            "[package].summary naming the old pin",
        ),
    ]
    for pattern, tail, what in edits:
        block = _sub_once(pattern, lambda m, t=tail: m[1] + t, block, what)
    out = "".join(lines[: start + 1]) + block + "".join(lines[end:])
    package = tomllib.loads(out).get("package", {})
    if (package.get("name"), package.get("version")) != (f"vergil-python{series}", f"{new_cpython}+{new_tag}"):
        _die("rewritten vergil.toml [package] does not parse back to the new pin")
    return out


def main(*, root: Path, fetch: Fetch = fetch) -> int:
    report_path = os.environ.get("VRG_DEPS_REPORT")
    if not report_path:
        _die("VRG_DEPS_REPORT is not set (vrg-update-deps sets it)")

    runtime_text = (root / "runtime.toml").read_text(encoding="utf-8")
    vergil_text = (root / "vergil.toml").read_text(encoding="utf-8")
    runtime = tomllib.loads(runtime_text)
    series, current, old_tag = runtime.get("series"), runtime.get("cpython"), runtime.get("pbs_tag")
    check_series(series, current)
    if not isinstance(old_tag, str) or not _TAG_RE.fullmatch(old_tag):
        _die(f"runtime.toml pbs_tag {old_tag!r} is not a YYYYMMDD PBS release tag")

    final = fetch(LATEST_URL)
    tag = final.rstrip("/").rsplit("/", 1)[-1]
    if not _TAG_RE.fullmatch(tag):
        _die(f"unexpected latest PBS release tag {tag!r} (from {final})")
    if tag < old_tag:
        _die(f"the latest PBS release {tag} is older than the pinned {old_tag}")

    sums = parse_sums(fetch(sums_url(tag)), tag)
    target, minor = pick(series, current, sums)
    if set(sums[target]) != set(_ARCHES):
        missing = sorted(set(_ARCHES) - set(sums[target]))
        _die(f"PBS {tag} ships CPython {target} without an install_only_stripped asset for {missing}")

    if (target, tag) == (current, old_tag):
        print(f"update-deps: runtime pin {current}+{old_tag} is current")
    else:
        # Render both files before writing either, so a failure never leaves a partial rewrite.
        new_runtime = render_runtime(runtime_text, target, tag, sums[target])
        new_vergil = rewrite_package(vergil_text, series, current, old_tag, target, tag)
        (root / "runtime.toml").write_text(new_runtime, encoding="utf-8")
        (root / "vergil.toml").write_text(new_vergil, encoding="utf-8")
        print(f"update-deps: runtime pin {current}+{old_tag} -> {target}+{tag}")

    if minor is not None:
        entry = {
            "source": "vergil-python",
            "name": "cpython",
            "current": target,
            "latest": minor,
            "note": f"PBS {tag} ships CPython {minor}; a new series is a new runtime package, moved by hand",
        }
        with open(report_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        print(f"update-deps: held back: CPython {minor} is newer than the {series} series")
    return 0


if __name__ == "__main__":
    sys.exit(main(root=Path(__file__).resolve().parents[2]))
