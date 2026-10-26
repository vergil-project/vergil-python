"""vergil-python's upstream-refresh logic (epic vergil-project/.github#472, spec §5.3-§5.4).

Run by scripts/dev/upstream-refresh.sh, the ``[upstream-refresh].hook`` that
``vrg-upstream-refresh`` runs. The repo builds one runtime package per listed
CPython series (``vergil-python<x.y>``): ``runtime.toml`` holds one
``[[series]]`` entry per series, and ``vergil.toml`` one ``[[package]]`` entry
per series. The hook:

1. checks that the two files agree (every series has exactly one package named
   ``vergil-python<series>``, whose ``upstream-version`` is
   ``<cpython>+<pbs_tag>``, whose ``summary`` names that pin and whose
   ``smoke`` runs the series interpreter), before any network access;
2. resolves the newest python-build-standalone (PBS) release through the
   unmetered ``releases/latest`` redirect and reads its ``SHA256SUMS``;
3. refreshes **each series within itself**: the newest CPython patch of the
   series that release ships as ``install_only_stripped`` for both
   architectures becomes the series' ``cpython``, ``pbs_tag`` and checksums,
   and its package's ``upstream-version`` and ``summary``;
4. leaves a series that release no longer ships at its pin, with a "no longer
   built upstream" note. It never searches older PBS releases (that needs the
   rate-limited GitHub API), and that is not a failure;
5. writes one note per change, and a held-back note for a stable CPython minor
   newer than every listed series, to ``$VRG_UPSTREAM_REPORT``. It never adds
   or removes a series.

Stdlib only. The rewrite is all-or-nothing: every failure (network, parse, a
missing architecture or checksum, a series/package mismatch, an unexpected file
layout) exits non-zero before either file is written. The hook edits files
only; the driver commits. It never touches the repo's ``VERSION``: the full
package version (``<upstream-version>.<VERSION>``) is joined by vergil-tooling.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, NoReturn

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
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_SERIES_KEYS = {"series", "cpython", "pbs_tag", "sha256"}
# The pin as the [[package]] summary names it; rewritten in place.
_SUMMARY_PIN_RE = r"CPython \d+\.\d+\.\d+ \(python-build-standalone \d{8}\)"

RUNTIME_HEADER = """\
# The python-build-standalone (PBS) runtimes packaged by this repo: one package
# per listed CPython series, vergil-python<series> at /opt/vergil/python/<series>/
# (epic vergil-project/.github#472, spec §5.3). Each [[series]] entry pins one
# series: `series` is the x.y; `cpython` is the patch built for it and
# `pbs_tag` the PBS build, and both float within the series. Different series
# may sit on different PBS builds.
#
# Source of every value but `series`: the PBS release's SHA256SUMS asset,
#   https://github.com/astral-sh/python-build-standalone/releases/download/<pbs_tag>/SHA256SUMS
# for the asset
#   cpython-<cpython>+<pbs_tag>-<triple>-install_only_stripped.tar.gz
#
# Every series has exactly one [[package]] entry in vergil.toml, named
# vergil-python<series>, with upstream-version = "<cpython>+<pbs_tag>".
# packaging/build.sh fails the build if the two files disagree. The
# upstream-refresh hook (scripts/dev/upstream-refresh.sh) rewrites this file
# whole, so comments go here, never between entries. See
# docs/runtime-package-pattern.md.
"""

Fetch = Callable[[str], str]
Entry = dict[str, Any]


def _die(msg: str) -> NoReturn:
    raise SystemExit(f"upstream-refresh: {msg}")


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
    """The packaged interpreter path for *series*, which a package's ``smoke`` must run."""
    return f"/opt/vergil/python/{series}/bin/python{series}"


def package_name(series: str) -> str:
    return f"vergil-python{series}"


def pin(entry: Entry) -> str:
    """A series' upstream inputs, ``<cpython>+<pbs_tag>`` (its package's ``upstream-version``)."""
    return f"{entry['cpython']}+{entry['pbs_tag']}"


def summary_pin(entry: Entry) -> str:
    return f"CPython {entry['cpython']} (python-build-standalone {entry['pbs_tag']})"


def load_series(text: str) -> list[Entry]:
    """The validated ``[[series]]`` entries of runtime.toml, in file order."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        _die(f"runtime.toml is not valid TOML: {exc}")
    extra = sorted(set(data) - {"series"})
    if extra:
        _die(f"runtime.toml has unexpected top-level keys {extra} (expected only [[series]] entries)")
    raw = data.get("series")
    if not isinstance(raw, list) or not raw or not all(isinstance(e, dict) for e in raw):
        _die("runtime.toml must hold one or more [[series]] entries")
    entries: list[Entry] = []
    seen: set[str] = set()
    for item in raw:
        if set(item) != _SERIES_KEYS:
            _die(f"runtime.toml [[series]] entry {item.get('series')!r} has keys {sorted(item)}, "
                 f"expected {sorted(_SERIES_KEYS)}")
        series, cpython, tag, sha = item["series"], item["cpython"], item["pbs_tag"], item["sha256"]
        check_series(series, cpython)
        if series in seen:
            _die(f"runtime.toml lists series {series} twice")
        seen.add(series)
        if not isinstance(tag, str) or not _TAG_RE.fullmatch(tag):
            _die(f"runtime.toml series {series} pbs_tag {tag!r} is not a YYYYMMDD PBS release tag")
        if not isinstance(sha, dict) or set(sha) != set(_ARCHES):
            _die(f"runtime.toml series {series} sha256 must have exactly {sorted(_ARCHES)}")
        for arch in _ARCHES:
            if not isinstance(sha[arch], str) or not _SHA_RE.fullmatch(sha[arch]):
                _die(f"runtime.toml series {series} sha256.{arch} is not a SHA-256 hex digest")
        entries.append({"series": series, "cpython": cpython, "pbs_tag": tag, "sha256": dict(sha)})
    return entries


def render_runtime(entries: list[Entry]) -> str:
    """runtime.toml for *entries*: the fixed header, then one ``[[series]]`` block each."""
    blocks = []
    for e in entries:
        blocks.append(
            "[[series]]\n"
            f'series = "{e["series"]}"\n'
            f'cpython = "{e["cpython"]}"\n'
            f'pbs_tag = "{e["pbs_tag"]}"\n'
            "\n"
            "[series.sha256]\n"
            + "".join(f'{arch} = "{e["sha256"][arch]}"\n' for arch in _ARCHES)
        )
    out = RUNTIME_HEADER + "\n" + "\n".join(blocks)
    if load_series(out) != entries:
        _die("rendered runtime.toml does not parse back to the new pins")
    return out


def load_packages(text: str) -> list[dict[str, Any]]:
    """vergil.toml's package entries (a single ``[package]`` counts as one)."""
    try:
        raw = tomllib.loads(text).get("package")
    except tomllib.TOMLDecodeError as exc:
        _die(f"vergil.toml is not valid TOML: {exc}")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw or not all(isinstance(p, dict) for p in raw):
        _die("vergil.toml has no [[package]] entries")
    return raw


def check_packages(packages: list[dict[str, Any]], entries: list[Entry]) -> None:
    """Fail unless *packages* and *entries* agree one-to-one, as packaging/build.sh requires."""
    names = [p.get("name") for p in packages]
    for name in names:
        if names.count(name) > 1:
            _die(f"vergil.toml lists package {name!r} twice")
    by_name = {package_name(e["series"]): e for e in entries}
    unpackaged = sorted(set(by_name) - set(names), key=str)
    unlisted = sorted(set(names) - set(by_name), key=str)
    if unpackaged or unlisted:
        _die(
            "runtime.toml [[series]] and vergil.toml [[package]] disagree: "
            f"series without a package {unpackaged}, packages without a series {unlisted}"
        )
    for p in packages:
        name, entry = p["name"], by_name[p["name"]]
        if "version" in p:
            _die(f"vergil.toml package {name} sets version; it must set upstream-version only")
        if p.get("upstream-version") != pin(entry):
            _die(f"vergil.toml package {name} upstream-version {p.get('upstream-version')!r} "
                 f"!= {pin(entry)!r} (runtime.toml)")
        summary = p.get("summary")
        if not isinstance(summary, str) or summary_pin(entry) not in summary:
            _die(f"vergil.toml package {name} summary does not name {summary_pin(entry)}")
        smoke = p.get("smoke")
        interpreter = smoke_interpreter(entry["series"])
        if not isinstance(smoke, str) or f"{interpreter} " not in f"{smoke} ":
            _die(f"vergil.toml package {name} smoke does not run {interpreter}")


def _sub_once(pattern: str, repl: Callable[[re.Match[str]], str], text: str, what: str) -> str:
    out, count = re.subn(pattern, repl, text, flags=re.M)
    if count != 1:
        _die(f"expected exactly one {what}, found {count}")
    return out


def rewrite_packages(text: str, entries: list[Entry]) -> str:
    """Rewrite each ``vergil-python<series>`` package's ``upstream-version`` and ``summary``.

    Only those two lines of each ``[[package]]`` entry change; everything else,
    including ``name`` and ``smoke`` (which name the series and never change
    within it), is kept byte for byte. The result must agree with *entries*
    exactly (:func:`check_packages`), or nothing is returned.
    """
    by_name = {package_name(e["series"]): e for e in entries}
    lines = text.splitlines(keepends=True)
    headers = [i for i, line in enumerate(lines) if line.lstrip().startswith("[")]
    out: list[str] = []
    pos = 0
    found = 0
    for idx, start in enumerate(headers):
        if lines[start].strip() != "[[package]]":
            continue
        found += 1
        end = headers[idx + 1] if idx + 1 < len(headers) else len(lines)
        block = "".join(lines[start + 1 : end])
        name = tomllib.loads(block).get("name")
        entry = by_name.get(name) if isinstance(name, str) else None
        if entry is None:
            _die(f"vergil.toml [[package]] {name!r} names no runtime.toml series")
        what = f"vergil.toml package {name}"
        block = _sub_once(r'^(upstream-version = ")[^"]*"$', lambda m, e=entry: f'{m[1]}{pin(e)}"',
                          block, f"upstream-version in {what}")
        block = _sub_once(rf'^(summary = ".*?){_SUMMARY_PIN_RE}', lambda m, e=entry: m[1] + summary_pin(e),
                          block, f"summary naming a CPython pin in {what}")
        out.extend(lines[pos : start + 1])
        out.append(block)
        pos = end
    if not found:
        _die("vergil.toml has no [[package]] entries")
    out.extend(lines[pos:])
    result = "".join(out)
    check_packages(load_packages(result), entries)
    return result


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


def no_longer_built_note(entry: Entry, tag: str) -> str:
    series = entry["series"]
    return (
        f"{package_name(series)}: no longer built upstream (latest PBS {tag} ships no {series} patch); "
        f"kept at {pin(entry)}"
    )


def refresh_series(entry: Entry, tag: str, sums: dict[str, dict[str, str]]) -> tuple[Entry, str | None]:
    """Refresh one series within itself against PBS *tag*; return (new entry, note or None)."""
    series, current = entry["series"], entry["cpython"]
    same = [v for v in sums if _minor(v) == series]
    if not same:
        return entry, no_longer_built_note(entry, tag)
    target = max(same, key=_key)
    if _key(target) < _key(current):
        _die(f"PBS {tag} ships CPython {target}, older than the pinned {current} of series {series}")
    if set(sums[target]) != set(_ARCHES):
        missing = sorted(set(_ARCHES) - set(sums[target]))
        _die(f"PBS {tag} ships CPython {target} without an install_only_stripped asset for {missing}")
    new = {"series": series, "cpython": target, "pbs_tag": tag, "sha256": dict(sums[target])}
    if (target, tag) == (current, entry["pbs_tag"]):
        if new["sha256"] != entry["sha256"]:
            _die(f"PBS {tag} lists different checksums for the pinned CPython {current} than runtime.toml")
        return entry, None
    return new, f"{package_name(series)}: {pin(entry)} -> {pin(new)}"


def newer_minor(entries: Iterable[Entry], versions: Iterable[str]) -> str | None:
    """The newest stable CPython in *versions* whose minor is newer than every listed series."""
    newest = max((_key(e["series"]) for e in entries))
    newer = [v for v in versions if _key(v)[:2] > newest]
    return max(newer, key=_key) if newer else None


def refresh(root: Path, fetch: Fetch = fetch) -> list[str]:
    """Refresh every listed series; rewrite runtime.toml and vergil.toml together; return the notes."""
    runtime_path, vergil_path = root / "runtime.toml", root / "vergil.toml"
    runtime_text = runtime_path.read_text(encoding="utf-8")
    vergil_text = vergil_path.read_text(encoding="utf-8")
    entries = load_series(runtime_text)
    check_packages(load_packages(vergil_text), entries)

    final = fetch(LATEST_URL)
    tag = final.rstrip("/").rsplit("/", 1)[-1]
    if not _TAG_RE.fullmatch(tag):
        _die(f"unexpected latest PBS release tag {tag!r} (from {final})")
    for e in entries:
        if tag < e["pbs_tag"]:
            _die(f"the latest PBS release {tag} is older than series {e['series']}'s pinned {e['pbs_tag']}")

    sums = parse_sums(fetch(sums_url(tag)), tag)
    new_entries: list[Entry] = []
    notes: list[str] = []
    for e in entries:
        new, note = refresh_series(e, tag, sums)
        new_entries.append(new)
        if note is not None:
            notes.append(note)
    minor = newer_minor(entries, sums)
    if minor is not None:
        notes.append(f"held back: PBS {tag} ships CPython {minor}, newer than every listed series; "
                     "a new series is added to runtime.toml and vergil.toml by hand")

    if new_entries != entries:
        # Render both files before writing either, so a failure never leaves a partial rewrite.
        new_runtime = render_runtime(new_entries)
        new_vergil = rewrite_packages(vergil_text, new_entries)
        runtime_path.write_text(new_runtime, encoding="utf-8")
        vergil_path.write_text(new_vergil, encoding="utf-8")
    return notes


def main(*, root: Path, fetch: Fetch = fetch) -> int:
    report_path = os.environ.get("VRG_UPSTREAM_REPORT")
    if not report_path:
        _die("VRG_UPSTREAM_REPORT is not set (vrg-upstream-refresh sets it)")
    notes = refresh(root, fetch)
    if notes:
        with open(report_path, "a", encoding="utf-8") as fh:
            fh.write("".join(f"{note}\n" for note in notes))
    for note in notes:
        print(f"upstream-refresh: {note}")
    if not notes:
        print("upstream-refresh: every series is current")
    return 0


if __name__ == "__main__":
    sys.exit(main(root=Path(__file__).resolve().parents[2]))
