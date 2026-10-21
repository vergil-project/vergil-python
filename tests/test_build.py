"""Offline tests for packaging/build.sh (stdlib unittest only).

Each test copies build.sh into a scratch repository with its own runtime.toml
and vergil.toml, and puts a fake ``curl`` first on PATH. The fake records the
URL it was asked for and serves a small locally built PBS-shaped archive, so
nothing here touches the network. build.sh needs ``uv`` (to parse TOML) and
``sha256sum``, both present in the dev container.
"""

import hashlib
import io
import os
import re
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
_X86_ARCHIVE = f"cpython-3.14.8+{_TAG}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"

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


def _runtime(series: str = "3.14", cpython: str = "3.14.8", x86: str = "0" * 64) -> str:
    lines = [f'series = "{series}"\n'] if series is not None else []
    return "".join(lines) + (
        f'cpython = "{cpython}"\n'
        f'pbs_tag = "{_TAG}"\n'
        "\n"
        "[sha256]\n"
        f'x86_64 = "{x86}"\n'
        f'aarch64 = "{"1" * 64}"\n'
    )


def _vergil(
    name: str = "vergil-python3.14",
    version: str = f"3.14.8+{_TAG}",
    interpreter: str = "/opt/vergil/python/3.14/bin/python3.14",
) -> str:
    return (
        "[package]\n"
        'builder = "staged"\n'
        f'name = "{name}"\n'
        f'version = "{version}"\n'
        'summary = "test"\n'
        f"smoke = \"{interpreter} -c 'import ssl'\"\n"
    )


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
        self.archive = self.tmp / _X86_ARCHIVE
        self.archive.write_bytes(_archive("3.14"))
        self.archive_sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.env = {
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "VRG_STAGING_ROOT": str(self.staging),
            "VRG_TARGET_ARCH": "amd64",
            "FAKE_CURL_LOG": str(self.curl_log),
        }

    def _build(self, runtime: str, vergil: str, *, serve: bool = True) -> subprocess.CompletedProcess:
        (self.repo / "runtime.toml").write_text(runtime)
        (self.repo / "vergil.toml").write_text(vergil)
        env = dict(self.env)
        if serve:
            env["FAKE_ARCHIVE"] = str(self.archive)
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

    def test_build_rejects_series_mismatch(self):
        self._assert_rejected(
            self._build(_runtime(series="3.14", cpython="3.15.0"), _vergil(version=f"3.15.0+{_TAG}")),
            "is not a patch of series '3.14'",
        )

    def test_build_rejects_series_prefix_lookalike(self):
        # 3.1 is not the series of 3.14.8, even though "3.14.8" starts with "3.1".
        self._assert_rejected(
            self._build(_runtime(series="3.1"), _vergil(name="vergil-python3.1")),
            "is not a patch of series '3.1'",
        )

    def test_build_rejects_malformed_series(self):
        for series in ["3", "3.14.8", "latest"]:
            with self.subTest(series=series):
                self._assert_rejected(self._build(_runtime(series=series), _vergil()), "is not an x.y CPython series")

    def test_build_rejects_missing_series(self):
        self._assert_rejected(self._build(_runtime(series=None), _vergil()), "series")

    def test_build_rejects_name_not_naming_the_series(self):
        self._assert_rejected(
            self._build(_runtime(), _vergil(name="vergil-python3.14.8")),
            "!= 'vergil-python3.14'",
        )

    def test_build_rejects_version_mismatch(self):
        self._assert_rejected(
            self._build(_runtime(), _vergil(version=f"3.14.7+{_TAG}")),
            f"!= '3.14.8+{_TAG}'",
        )

    def test_build_rejects_smoke_not_running_the_series_interpreter(self):
        for interpreter in ["/opt/vergil/python/3.14.8/bin/python3.14", "/opt/vergil/python/3.14/bin/python3.14t"]:
            with self.subTest(interpreter=interpreter):
                self._assert_rejected(
                    self._build(_runtime(), _vergil(interpreter=interpreter)),
                    "smoke does not run /opt/vergil/python/3.14/bin/python3.14",
                )

    def test_build_stages_the_runtime_under_the_series_root(self):
        result = self._build(_runtime(x86=self.archive_sha), _vergil())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self._curl_calls(),
            [f"https://github.com/astral-sh/python-build-standalone/releases/download/{_TAG}/{_X86_ARCHIVE}"],
        )
        root = self.staging / "opt" / "vergil" / "python"
        self.assertEqual(sorted(p.name for p in root.iterdir()), ["3.14"])
        self.assertTrue(os.access(root / "3.14" / "bin" / "python3.14", os.X_OK))
        marker = root / "3.14" / "lib" / "python3.14" / "EXTERNALLY-MANAGED"
        self.assertIn("[externally-managed]", marker.read_text())

    def test_build_rejects_checksum_mismatch(self):
        result = self._build(_runtime(x86="0" * 64), _vergil())
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.staging / "opt").exists())

    def test_repository_pin_passes_the_config_guards(self):
        # The committed runtime.toml and vergil.toml must agree: build.sh gets as
        # far as fetching the archive (which the fake curl then refuses).
        result = self._build(
            (_REPO / "runtime.toml").read_text(), (_REPO / "vergil.toml").read_text(), serve=False
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fake curl: no archive", result.stderr)
        [url] = self._curl_calls()
        series = re.escape(tomllib.loads((_REPO / "runtime.toml").read_text())["series"])
        self.assertRegex(url, rf"/cpython-{series}\.\d+\+\d{{8}}-x86_64-unknown-linux-gnu-install_only_stripped\.tar\.gz$")


if __name__ == "__main__":
    unittest.main()
