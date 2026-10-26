"""Offline tests for packaging/build.sh (stdlib unittest only).

Each test copies build.sh into a scratch repository with its own runtime.toml
and vergil.toml, and puts a fake ``curl`` first on PATH. The fake records the
URL it was asked for and serves a small locally built PBS-shaped archive, so
nothing here touches the network. build.sh needs ``uv`` (to parse TOML) and
``sha256sum``, both present in the dev container. The matrix tests run the
installed ``vrg-package matrix`` (vergil-tooling, in the dev container) on the
fixture vergil.toml; a missing ``vrg-package`` fails them, never skips them.
"""

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_BUILD = _REPO / "packaging" / "build.sh"
_TAG = "20261013"
_TAG15 = "20261021"
_RELEASE = "1.0.5"
_PBS = "https://github.com/astral-sh/python-build-standalone/releases/download"

_FAKE_CURL = """#!/usr/bin/env bash
# Fake curl: record the URL; serve $FAKE_ARCHIVE to -o, or fail like curl -f.
out="" url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    --retry) shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
echo "$url" >>"$FAKE_CURL_LOG"
[ -n "${FAKE_ARCHIVE:-}" ] || { echo "fake curl: no archive" >&2; exit 22; }
cp "$FAKE_ARCHIVE" "$out"
"""


def _archive(series: str) -> bytes:
    """A PBS-shaped install_only_stripped archive: a python/ root with bin and lib."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, mode, data in [
            ("python", 0o755, None),
            ("python/bin", 0o755, None),
            (f"python/bin/python{series}", 0o755, b"#!/bin/sh\n"),
            ("python/lib", 0o755, None),
            (f"python/lib/python{series}", 0o755, None),
            (f"python/lib/python{series}/os.py", 0o644, b""),
        ]:
            info = tarfile.TarInfo(name)
            info.mode = mode
            if data is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _series(series: str | None = "3.14", cpython: str = "3.14.8", tag: str = _TAG, x86: str = "0" * 64) -> str:
    lines = ["[[series]]\n"]
    if series is not None:
        lines.append(f'series = "{series}"\n')
    lines += [
        f'cpython = "{cpython}"\n',
        f'pbs_tag = "{tag}"\n',
        "\n[series.sha256]\n",
        f'x86_64 = "{x86}"\n',
        f'aarch64 = "{"1" * 64}"\n',
    ]
    return "".join(lines)


def _package(
    name: str = "vergil-python3.14",
    upstream: str = f"3.14.8+{_TAG}",
    interpreter: str = "/opt/vergil/python/3.14/bin/python3.14",
    *,
    version_key: str = "upstream-version",
) -> str:
    return (
        "[[package]]\n"
        'builder = "staged"\n'
        'vendor = "vergil"\n'
        f'name = "{name}"\n'
        f'{version_key} = "{upstream}"\n'
        'summary = "test"\n'
        f"smoke = \"{interpreter} -c 'import ssl'\"\n"
        "\n[package.staged]\n"
        'build-command = "packaging/build.sh"\n'
    )


# The committed vergil.toml's tables before its first [[package]], so the
# fixture vergil.toml is a complete config that vrg-package matrix accepts.
_COMMITTED = (_REPO / "vergil.toml").read_text()
_PROJECT = _COMMITTED[: _COMMITTED.index("[[package]]")]
_SERIES15 = _series("3.15", "3.15.1", _TAG15)
_PACKAGE15 = _package("vergil-python3.15", f"3.15.1+{_TAG15}", "/opt/vergil/python/3.15/bin/python3.15")


def _two(series14: str | None = None, package14: str | None = None) -> tuple[str, str]:
    """A two-series runtime.toml and vergil.toml: 3.14 (overridable) and 3.15."""
    return (
        (series14 if series14 is not None else _series()) + "\n" + _SERIES15,
        _PROJECT
        + (package14 if package14 is not None else _package())
        + "\n"
        + _PACKAGE15,
    )


def _matrix(vergil: str) -> dict:
    """``vrg-package matrix`` for *vergil* (a vergil.toml), run in a scratch directory."""
    exe = shutil.which("vrg-package")
    if exe is None:
        raise AssertionError("vrg-package is not on PATH (it ships with vergil-tooling in the dev container)")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "vergil.toml").write_text(vergil)
        result = subprocess.run([exe, "matrix"], cwd=tmp, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"vrg-package matrix failed: {result.stderr}")
    return json.loads(result.stdout)


class BuildShTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.repo = self.tmp / "repo"
        (self.repo / "packaging").mkdir(parents=True)
        shutil.copy2(_BUILD, self.repo / "packaging" / "build.sh")
        self.staging = self.tmp / "staging"
        self.staging.mkdir()
        bindir = self.tmp / "bin"
        bindir.mkdir()
        curl = bindir / "curl"
        curl.write_text(_FAKE_CURL)
        curl.chmod(curl.stat().st_mode | stat.S_IXUSR)
        self.curl_log = self.tmp / "curl.log"
        self.archives: dict[str, Path] = {}
        self.sha: dict[str, str] = {}
        for series in ("3.14", "3.15"):
            path = self.tmp / f"archive-{series}.tar.gz"
            path.write_bytes(_archive(series))
            self.archives[series] = path
            self.sha[series] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.env = {
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "VRG_STAGING_ROOT": str(self.staging),
            "VRG_TARGET_ARCH": "amd64",
            "FAKE_CURL_LOG": str(self.curl_log),
        }

    def _build(
        self,
        runtime: str,
        vergil: str,
        *,
        name: str = "vergil-python3.14",
        version: str | None = None,
        serve: str | None = "3.14",
        unset: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess:
        (self.repo / "runtime.toml").write_text(runtime)
        (self.repo / "vergil.toml").write_text(vergil)
        env = dict(self.env)
        env["VRG_PACKAGE_NAME"] = name
        env["VRG_PACKAGE_VERSION"] = version if version is not None else f"3.14.8+{_TAG}.{_RELEASE}"
        if serve is not None:
            env["FAKE_ARCHIVE"] = str(self.archives[serve])
        for key in unset:
            env.pop(key)
        return subprocess.run(
            ["bash", str(self.repo / "packaging" / "build.sh")],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def _curl_calls(self) -> list[str]:
        return self.curl_log.read_text().splitlines() if self.curl_log.exists() else []

    def _assert_rejected(self, result: subprocess.CompletedProcess, message: str) -> None:
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertEqual(self._curl_calls(), [], "build.sh fetched before its config guard")
        self.assertEqual(list(self.staging.iterdir()), [])

    def _assert_staged(self, result: subprocess.CompletedProcess, series: str, url: str) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self._curl_calls(), [url])
        root = self.staging / "opt" / "vergil" / "python"
        self.assertEqual(sorted(p.name for p in root.iterdir()), [series])
        self.assertTrue(os.access(root / series / "bin" / f"python{series}", os.X_OK))
        marker = root / series / "lib" / f"python{series}" / "EXTERNALLY-MANAGED"
        self.assertIn("[externally-managed]", marker.read_text())

    # -- selecting and staging a series ------------------------------------

    def test_stages_requested_series(self):
        result = self._build(*_two(_series(x86=self.sha["3.14"])))
        self._assert_staged(
            result,
            "3.14",
            f"{_PBS}/{_TAG}/cpython-3.14.8+{_TAG}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
        )

    def test_stages_the_other_series_from_the_same_files(self):
        runtime, vergil = _two()
        runtime = runtime.replace(_SERIES15, _series("3.15", "3.15.1", _TAG15, x86=self.sha["3.15"]))
        result = self._build(
            runtime, vergil, name="vergil-python3.15", version=f"3.15.1+{_TAG15}.{_RELEASE}", serve="3.15"
        )
        self._assert_staged(
            result,
            "3.15",
            f"{_PBS}/{_TAG15}/cpython-3.15.1+{_TAG15}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
        )

    def test_unknown_package_name_fails(self):
        self._assert_rejected(
            self._build(*_two(), name="vergil-python3.13", version=f"3.13.12+{_TAG}.{_RELEASE}"),
            "runtime.toml has no [[series]] '3.13'",
        )

    def test_package_name_and_version_must_be_set(self):
        for key in ("VRG_PACKAGE_NAME", "VRG_PACKAGE_VERSION"):
            with self.subTest(key=key):
                self._assert_rejected(self._build(*_two(), unset=(key,)), f"{key} must be set")

    def test_version_must_carry_inputs(self):
        for version in [
            f"3.14.9+20261101.{_RELEASE}",  # different inputs
            f"3.14.8+{_TAG}",  # inputs without the repo version
            f"3.14.8+{_TAG}.1.0",  # not an X.Y.Z repo version
            f"3.14.8+{_TAG}.{_RELEASE}x",
        ]:
            with self.subTest(version=version):
                self._assert_rejected(
                    self._build(*_two(), version=version), f"is not '3.14.8+{_TAG}.<X.Y.Z>'"
                )

    # -- the two files must agree one-to-one ---------------------------------

    def test_every_series_has_one_package(self):
        runtime, vergil = _two()
        cases = {
            "series without a package": (runtime, vergil.replace(_PACKAGE15, ""), "series without a package ['3.15']"),
            "package without a series": (
                runtime.replace(_SERIES15, ""),
                vergil,
                "packages without a series ['vergil-python3.15']",
            ),
            "duplicate package": (runtime, vergil + "\n" + _PACKAGE15, "lists package 'vergil-python3.15' twice"),
            "duplicate series": (runtime + "\n" + _SERIES15, vergil, "lists series '3.15' twice"),
        }
        for label, (rt, vg, message) in cases.items():
            with self.subTest(label):
                # Building the agreeing series still fails: the files as a whole disagree.
                self._assert_rejected(self._build(rt, vg), message)

    def test_removed_series_dropped_from_build(self):
        # Remove 3.14 from both files: its build is unknown, 3.15 still builds,
        # and the tooling resolves no vergil-python3.14 cell.
        runtime = _series("3.15", "3.15.1", _TAG15, x86=self.sha["3.15"])
        vergil = _PROJECT + _PACKAGE15
        self._assert_rejected(self._build(runtime, vergil), "runtime.toml has no [[series]] '3.14'")
        result = self._build(
            runtime, vergil, name="vergil-python3.15", version=f"3.15.1+{_TAG15}.{_RELEASE}", serve="3.15"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        cells = _matrix(vergil)["build"]
        self.assertEqual({c["package"] for c in cells}, {"vergil-python3.15"})
        self.assertFalse([c for c in cells if "3.14" in c["id"]])

    def test_matrix_builds_every_listed_series(self):
        cells = _matrix(_two()[1])["build"]
        self.assertEqual(
            sorted((c["package"], c["arch"]) for c in cells),
            [(f"vergil-python{s}", a) for s in ("3.14", "3.15") for a in ("amd64", "arm64")],
        )

    # -- per-entry guards ------------------------------------------------------

    def test_build_rejects_series_mismatch(self):
        self._assert_rejected(
            self._build(*_two(_series(cpython="3.15.0"), _package(upstream=f"3.15.0+{_TAG}"))),
            "is not a patch of series '3.14'",
        )

    def test_build_rejects_series_prefix_lookalike(self):
        # 3.1 is not the series of 3.14.8, even though "3.14.8" starts with "3.1".
        self._assert_rejected(
            self._build(*_two(_series(series="3.1"), _package(name="vergil-python3.1")), name="vergil-python3.1"),
            "is not a patch of series '3.1'",
        )

    def test_build_rejects_malformed_series(self):
        for series in ["3", "3.14.8", "latest"]:
            with self.subTest(series=series):
                self._assert_rejected(
                    self._build(*_two(_series(series=series))), "is not an x.y CPython series"
                )

    def test_build_rejects_missing_series(self):
        self._assert_rejected(self._build(*_two(_series(series=None))), "series must be a non-empty string")

    def test_build_rejects_old_single_runtime_layout(self):
        old = f'series = "3.14"\ncpython = "3.14.8"\npbs_tag = "{_TAG}"\n'
        self._assert_rejected(self._build(old, _two()[1]), "runtime.toml must hold one or more [[series]] entries")

    def test_build_rejects_name_not_naming_the_series(self):
        self._assert_rejected(
            self._build(*_two(package14=_package(name="vergil-python3.14.8"))),
            "packages without a series ['vergil-python3.14.8']",
        )

    def test_build_rejects_upstream_version_mismatch(self):
        self._assert_rejected(
            self._build(*_two(package14=_package(upstream=f"3.14.7+{_TAG}"))),
            f"upstream-version '3.14.7+{_TAG}' != '3.14.8+{_TAG}'",
        )

    def test_build_rejects_full_version_override(self):
        self._assert_rejected(
            self._build(*_two(package14=_package(version_key="version"))),
            "sets version; a runtime package sets upstream-version only",
        )

    def test_build_rejects_smoke_not_running_the_series_interpreter(self):
        for interpreter in ["/opt/vergil/python/3.14.8/bin/python3.14", "/opt/vergil/python/3.14/bin/python3.14t"]:
            with self.subTest(interpreter=interpreter):
                self._assert_rejected(
                    self._build(*_two(package14=_package(interpreter=interpreter))),
                    "smoke does not run /opt/vergil/python/3.14/bin/python3.14",
                )

    def test_build_rejects_unknown_arch(self):
        self.env["VRG_TARGET_ARCH"] = "riscv64"
        self._assert_rejected(self._build(*_two()), "unsupported VRG_TARGET_ARCH=riscv64")

    def test_build_rejects_checksum_mismatch(self):
        result = self._build(*_two(_series(x86="0" * 64)))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.staging / "opt").exists())

    # -- the committed files ---------------------------------------------------

    def test_repository_pins_pass_the_config_guards(self):
        # The committed runtime.toml and vergil.toml must agree for every listed
        # series: build.sh gets as far as fetching each archive (which the fake
        # curl then refuses).
        runtime = (_REPO / "runtime.toml").read_text()
        vergil = (_REPO / "vergil.toml").read_text()
        release = (_REPO / "VERSION").read_text().strip()
        entries = tomllib.loads(runtime)["series"]
        self.assertEqual([e["series"] for e in entries], ["3.14", "3.15"])
        for e in entries:
            with self.subTest(series=e["series"]):
                self.curl_log.unlink(missing_ok=True)
                inputs = f"{e['cpython']}+{e['pbs_tag']}"
                result = self._build(
                    runtime, vergil, name=f"vergil-python{e['series']}", version=f"{inputs}.{release}", serve=None
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("fake curl: no archive", result.stderr)
                self.assertEqual(
                    self._curl_calls(),
                    [f"{_PBS}/{e['pbs_tag']}/cpython-{inputs}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"],
                )

    def test_repository_matrix_builds_both_series(self):
        cells = _matrix((_REPO / "vergil.toml").read_text())["build"]
        self.assertEqual(len(cells), 4)
        self.assertEqual({c["package"] for c in cells}, {"vergil-python3.14", "vergil-python3.15"})


if __name__ == "__main__":
    unittest.main()
