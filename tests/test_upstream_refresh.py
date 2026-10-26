"""Offline unit tests for scripts/dev/upstream_refresh.py (stdlib unittest only).

Every network lookup is injected through ``main(fetch=...)`` or a patched
``urllib.request.urlopen``; nothing here touches the network.
"""

import io
import os
import sys
import tempfile
import tomllib
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts" / "dev"))
import upstream_refresh as ur  # noqa: E402

_TAG = "20261101"
_OLD14 = "20261013"
_OLD15 = "20261021"
_X86 = "x86_64-unknown-linux-gnu"
_ARM = "aarch64-unknown-linux-gnu"
_STRIPPED = "install_only_stripped.tar.gz"
_H = {c: c * 64 for c in "0123456789abcdef"}


def _line(sha: str, version: str, triple: str, tag: str = _TAG, kind: str = _STRIPPED) -> str:
    return f"{sha}  cpython-{version}+{tag}-{triple}-{kind}"


def _both(version: str, x86: str, arm: str, tag: str = _TAG) -> list[str]:
    return [_line(x86, version, _X86, tag), _line(arm, version, _ARM, tag)]


# The latest PBS ships 3.14.9 and 3.15.2 (plus noise that must be ignored).
_SUMS = "\n".join(
    [
        *_both("3.14.9", _H["a"], _H["b"]),
        *_both("3.14.7", _H["1"], _H["2"]),
        *_both("3.15.2", _H["c"], _H["d"]),
        _line(_H["e"], "3.16.0a1", _X86),
        _line(_H["e"], "3.16.0rc1", _ARM),
        _line(_H["f"], "3.13.12", _X86),
        _line(_H["9"], "3.14.9", _X86, kind="install_only.tar.gz"),
        _line(_H["8"], "3.14.9", f"{_X86}-freethreaded"),
        _line(_H["7"], "3.14.9", "x86_64_v3-unknown-linux-gnu"),
        _line(_H["6"], "3.14.9", "x86_64-apple-darwin"),
        _line(_H["5"], "3.14.9", _X86, tag="20250101"),
        "",
    ]
)

_ENTRIES = [
    {"series": "3.14", "cpython": "3.14.8", "pbs_tag": _OLD14, "sha256": {"x86_64": _H["3"], "aarch64": _H["4"]}},
    {"series": "3.15", "cpython": "3.15.1", "pbs_tag": _OLD15, "sha256": {"x86_64": _H["5"], "aarch64": _H["6"]}},
]


def _package(series: str, cpython: str, tag: str) -> str:
    return (
        "[[package]]\n"
        'builder = "staged"\n'
        'vendor = "vergil"\n'
        f'name = "vergil-python{series}"\n'
        f'upstream-version = "{cpython}+{tag}"\n'
        f'summary = "Pinned CPython {cpython} (python-build-standalone {tag}) for vergil products"\n'
        f"smoke = \"/opt/vergil/python/{series}/bin/python{series} -c 'import ssl'\"\n"
        "\n"
        "[package.staged]\n"
        'build-command = "packaging/build.sh"\n'
    )


_HEAD = '[project]\nrepository-type = "infrastructure"\n\n[dependencies]\nvergil = "v2.1"\n\n'
_TAIL = '\n[upstream-refresh]\nhook = "scripts/dev/upstream-refresh.sh"\n'
_VERGIL = _HEAD + _package("3.14", "3.14.8", _OLD14) + "\n" + _package("3.15", "3.15.1", _OLD15) + _TAIL


def _fetcher(tag: str = _TAG, sums: str = _SUMS):
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        if url == ur.LATEST_URL:
            return f"https://github.com/astral-sh/python-build-standalone/releases/tag/{tag}"
        if url == ur.sums_url(tag):
            return sums
        raise AssertionError(f"unexpected URL {url}")

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


class ParseSumsTest(unittest.TestCase):
    def test_stable_stripped_linux_gnu_assets_of_the_tag(self):
        sums = ur.parse_sums(_SUMS, _TAG)
        self.assertEqual(sums["3.14.9"], {"x86_64": _H["a"], "aarch64": _H["b"]})
        self.assertEqual(sums["3.15.2"], {"x86_64": _H["c"], "aarch64": _H["d"]})
        self.assertNotIn("3.16.0a1", sums)
        self.assertNotIn("3.16.0rc1", sums)

    def test_keeps_partial_versions_so_a_gap_can_fail_loud(self):
        self.assertEqual(ur.parse_sums(_SUMS, _TAG)["3.13.12"], {"x86_64": _H["f"]})

    def test_conflicting_checksums_fail(self):
        with self.assertRaises(SystemExit):
            ur.parse_sums(_SUMS + "\n" + _line(_H["0"], "3.14.9", _X86), _TAG)

    def test_no_assets_for_tag_fails(self):
        with self.assertRaises(SystemExit):
            ur.parse_sums(_SUMS, "20991231")


class CheckSeriesTest(unittest.TestCase):
    def test_patch_within_series_passes(self):
        ur.check_series("3.14", "3.14.8")
        ur.check_series("3.9", "3.9.20")

    def test_series_mismatch_fails(self):
        for series, cpython in [("3.14", "3.15.0"), ("3.1", "3.14.8"), ("3.14", "3.14"), ("3.14", "3.14.8a1")]:
            with self.subTest(series=series, cpython=cpython), self.assertRaises(SystemExit):
                ur.check_series(series, cpython)

    def test_malformed_or_missing_series_fails(self):
        for series in [None, "", "3", "3.14.8", "three.fourteen", 3.14]:
            with self.subTest(series=series), self.assertRaises(SystemExit):
                ur.check_series(series, "3.14.8")


class LoadSeriesTest(unittest.TestCase):
    def test_round_trips_through_render(self):
        text = ur.render_runtime(_ENTRIES)
        self.assertTrue(text.startswith(ur.RUNTIME_HEADER))
        self.assertEqual(ur.load_series(text), _ENTRIES)
        self.assertEqual(ur.render_runtime(ur.load_series(text)), text)

    def test_committed_runtime_is_in_rendered_form(self):
        # The hook rewrites runtime.toml whole, so the committed file must be
        # exactly what it renders: a refresh then diffs only the pins.
        text = (_REPO / "runtime.toml").read_text()
        self.assertEqual(ur.render_runtime(ur.load_series(text)), text)

    def test_committed_files_agree(self):
        entries = ur.load_series((_REPO / "runtime.toml").read_text())
        ur.check_packages(ur.load_packages((_REPO / "vergil.toml").read_text()), entries)

    def test_rejects_bad_layouts(self):
        good = ur.render_runtime(_ENTRIES)
        bad = {
            "old single-series layout": 'series = "3.14"\ncpython = "3.14.8"\n',
            "no entries": "# nothing\n",
            "duplicate series": good + "\n" + good[good.index("[[series]]"):],
            "missing arch": good.replace(f'aarch64 = "{_H["4"]}"\n', ""),
            "bad checksum": good.replace(_H["3"], "nothex"),
            "bad tag": good.replace(f'pbs_tag = "{_OLD14}"', 'pbs_tag = "latest"'),
            "patch outside series": good.replace('cpython = "3.14.8"', 'cpython = "3.15.0"'),
            "missing series": good.replace('series = "3.14"\n', "", 1),
            "unknown key": good.replace('cpython = "3.14.8"\n', 'cpython = "3.14.8"\nextra = "x"\n'),
            "not toml": "[[series]\n",
        }
        for label, text in bad.items():
            with self.subTest(label), self.assertRaises(SystemExit):
                ur.load_series(text)


class CheckPackagesTest(unittest.TestCase):
    def test_agreeing_files_pass(self):
        ur.check_packages(ur.load_packages(_VERGIL), _ENTRIES)

    def test_mismatches_fail(self):
        cases = {
            "upstream-version": _VERGIL.replace(f'"3.14.8+{_OLD14}"', f'"3.14.7+{_OLD14}"'),
            "summary": _VERGIL.replace(f"CPython 3.14.8 (python-build-standalone {_OLD14})", "CPython 3.14.8"),
            "smoke": _VERGIL.replace("/opt/vergil/python/3.14/bin/python3.14 ", "/opt/vergil/python/3.14/bin/python3.14t "),
            "name": _VERGIL.replace('name = "vergil-python3.14"', 'name = "vergil-python3.14.8"'),
            "full version": _VERGIL.replace(f'upstream-version = "3.14.8+{_OLD14}"', f'version = "3.14.8+{_OLD14}"'),
            "duplicate": _VERGIL + "\n" + _package("3.15", "3.15.1", _OLD15),
        }
        for label, text in cases.items():
            with self.subTest(label), self.assertRaises(SystemExit):
                ur.check_packages(ur.load_packages(text), _ENTRIES)


class RewritePackagesTest(unittest.TestCase):
    def test_rewrites_only_the_changed_series_lines(self):
        new = [dict(_ENTRIES[0], cpython="3.14.9", pbs_tag=_TAG), _ENTRIES[1]]
        out = ur.rewrite_packages(_VERGIL, new)
        self.assertEqual(
            out, _VERGIL.replace(f"3.14.8+{_OLD14}", f"3.14.9+{_TAG}").replace(
                f"CPython 3.14.8 (python-build-standalone {_OLD14})", f"CPython 3.14.9 (python-build-standalone {_TAG})"
            )
        )

    def test_values_outside_package_entries_untouched(self):
        text = _VERGIL + f'\n[other]\nnote = "3.14.8+{_OLD14}"\n'
        out = ur.rewrite_packages(text, [dict(_ENTRIES[0], cpython="3.14.9", pbs_tag=_TAG), _ENTRIES[1]])
        self.assertIn(f'note = "3.14.8+{_OLD14}"', out)

    def test_package_without_series_fails(self):
        text = _VERGIL + "\n" + _package("3.13", "3.13.12", _OLD14)
        with self.assertRaises(SystemExit):
            ur.rewrite_packages(text, _ENTRIES)

    def test_summary_without_pin_fails(self):
        text = _VERGIL.replace(f"CPython 3.15.1 (python-build-standalone {_OLD15})", "CPython")
        with self.assertRaises(SystemExit):
            ur.rewrite_packages(text, _ENTRIES)

    def test_no_package_entries_fails(self):
        with self.assertRaises(SystemExit):
            ur.rewrite_packages(_HEAD, _ENTRIES)


class RefreshSeriesTest(unittest.TestCase):
    def setUp(self):
        self.sums = ur.parse_sums(_SUMS, _TAG)

    def test_newest_patch_within_series(self):
        new, note = ur.refresh_series(_ENTRIES[0], _TAG, self.sums)
        self.assertEqual(new, {"series": "3.14", "cpython": "3.14.9", "pbs_tag": _TAG,
                               "sha256": {"x86_64": _H["a"], "aarch64": _H["b"]}})
        self.assertEqual(note, f"vergil-python3.14: 3.14.8+{_OLD14} -> 3.14.9+{_TAG}")

    def test_numeric_not_lexical_ordering(self):
        sums = ur.parse_sums("\n".join([*_both("3.14.9", _H["a"], _H["b"]), *_both("3.14.10", _H["c"], _H["d"])]), _TAG)
        new, _ = ur.refresh_series(_ENTRIES[0], _TAG, sums)
        self.assertEqual(new["cpython"], "3.14.10")

    def test_refuses_to_downgrade_the_patch(self):
        sums = ur.parse_sums("\n".join(_both("3.14.7", _H["a"], _H["b"])), _TAG)
        with self.assertRaises(SystemExit):
            ur.refresh_series(_ENTRIES[0], _TAG, sums)

    def test_current_pin_is_unchanged(self):
        sums = ur.parse_sums("\n".join(_both("3.14.8", _H["3"], _H["4"], tag=_OLD14)), _OLD14)
        self.assertEqual(ur.refresh_series(_ENTRIES[0], _OLD14, sums), (_ENTRIES[0], None))

    def test_changed_checksum_for_the_same_pin_fails(self):
        sums = ur.parse_sums("\n".join(_both("3.14.8", _H["a"], _H["4"], tag=_OLD14)), _OLD14)
        with self.assertRaises(SystemExit):
            ur.refresh_series(_ENTRIES[0], _OLD14, sums)

    def test_newer_minor(self):
        self.assertEqual(ur.newer_minor(_ENTRIES, ["3.14.9", "3.15.2", "3.16.1", "3.16.0"]), "3.16.1")
        self.assertIsNone(ur.newer_minor(_ENTRIES, ["3.14.9", "3.15.2"]))
        self.assertIsNone(ur.newer_minor(_ENTRIES[:1] + [dict(_ENTRIES[1], series="3.10", cpython="3.10.1")],
                                         ["3.9.9"]))


class MainTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runtime = ur.render_runtime(_ENTRIES)
        (self.root / "runtime.toml").write_text(self.runtime)
        (self.root / "vergil.toml").write_text(_VERGIL)
        (self.root / "VERSION").write_text("1.0.5\n")
        self.report = self.root / "report.txt"
        env = mock.patch.dict(os.environ, {"VRG_UPSTREAM_REPORT": str(self.report)})
        env.start()
        self.addCleanup(env.stop)

    def _files(self):
        return (self.root / "runtime.toml").read_text(), (self.root / "vergil.toml").read_text()

    def _notes(self):
        return self.report.read_text().splitlines() if self.report.exists() else []

    def _assert_fails_writing_nothing(self, fetch):
        before = self._files()
        with self.assertRaises(SystemExit) as cm:
            ur.main(root=self.root, fetch=fetch)
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(self._files(), before)
        self.assertEqual(self._notes(), [])
        return cm.exception

    def test_refreshes_each_series_independently(self):
        self.assertEqual(ur.main(root=self.root, fetch=_fetcher()), 0)
        runtime, vergil = self._files()
        self.assertEqual(
            ur.load_series(runtime),
            [
                {"series": "3.14", "cpython": "3.14.9", "pbs_tag": _TAG, "sha256": {"x86_64": _H["a"], "aarch64": _H["b"]}},
                {"series": "3.15", "cpython": "3.15.2", "pbs_tag": _TAG, "sha256": {"x86_64": _H["c"], "aarch64": _H["d"]}},
            ],
        )
        packages = {p["name"]: p for p in tomllib.loads(vergil)["package"]}
        self.assertEqual(packages["vergil-python3.14"]["upstream-version"], f"3.14.9+{_TAG}")
        self.assertEqual(packages["vergil-python3.15"]["upstream-version"], f"3.15.2+{_TAG}")
        self.assertIn(f"CPython 3.15.2 (python-build-standalone {_TAG})", packages["vergil-python3.15"]["summary"])
        self.assertEqual(
            self._notes(),
            [f"vergil-python3.14: 3.14.8+{_OLD14} -> 3.14.9+{_TAG}", f"vergil-python3.15: 3.15.1+{_OLD15} -> 3.15.2+{_TAG}"],
        )

    def test_series_on_different_pbs_builds_refresh_separately(self):
        # Only 3.14 moves; 3.15 is already current at the latest tag.
        entries = [_ENTRIES[0], dict(_ENTRIES[1], cpython="3.15.2", pbs_tag=_TAG,
                                     sha256={"x86_64": _H["c"], "aarch64": _H["d"]})]
        (self.root / "runtime.toml").write_text(ur.render_runtime(entries))
        (self.root / "vergil.toml").write_text(_HEAD + _package("3.14", "3.14.8", _OLD14) + "\n"
                                               + _package("3.15", "3.15.2", _TAG) + _TAIL)
        self.assertEqual(ur.main(root=self.root, fetch=_fetcher()), 0)
        self.assertEqual(self._notes(), [f"vergil-python3.14: 3.14.8+{_OLD14} -> 3.14.9+{_TAG}"])
        self.assertEqual(ur.load_series(self._files()[0])[1], entries[1])

    def test_series_no_longer_built_keeps_pin_with_note(self):
        sums = "\n".join(_both("3.15.2", _H["c"], _H["d"]))
        self.assertEqual(ur.main(root=self.root, fetch=_fetcher(sums=sums)), 0)
        runtime, vergil = self._files()
        entries = ur.load_series(runtime)
        self.assertEqual(entries[0], _ENTRIES[0])
        self.assertEqual(entries[1]["cpython"], "3.15.2")
        self.assertIn(f'upstream-version = "3.14.8+{_OLD14}"', vergil)
        self.assertIn(
            f"vergil-python3.14: no longer built upstream (latest PBS {_TAG} ships no 3.14 patch); "
            f"kept at 3.14.8+{_OLD14}",
            self._notes(),
        )

    def test_never_searches_older_pbs_releases(self):
        fetch = _fetcher(sums="\n".join(_both("3.15.2", _H["c"], _H["d"])))
        ur.main(root=self.root, fetch=fetch)
        self.assertEqual(fetch.calls, [ur.LATEST_URL, ur.sums_url(_TAG)])

    def test_one_series_failure_writes_nothing(self):
        # 3.14 would refresh cleanly, but 3.15's aarch64 asset is missing: neither file changes.
        sums = "\n".join([*_both("3.14.9", _H["a"], _H["b"]), _line(_H["c"], "3.15.2", _X86)])
        exc = self._assert_fails_writing_nothing(_fetcher(sums=sums))
        self.assertIn("3.15.2", str(exc.code))
        self.assertIn("aarch64", str(exc.code))

    def test_one_series_unreadable_checksum_writes_nothing(self):
        # A truncated checksum is not a readable asset line, so 3.15.2 lacks aarch64.
        sums = "\n".join([*_both("3.14.9", _H["a"], _H["b"]), _line(_H["c"], "3.15.2", _X86),
                          _line("d" * 63, "3.15.2", _ARM)])
        self._assert_fails_writing_nothing(_fetcher(sums=sums))

    def test_package_series_mismatch_fails(self):
        (self.root / "vergil.toml").write_text(_VERGIL + "\n" + _package("3.13", "3.13.12", _OLD14))
        fetch = _fetcher()
        exc = self._assert_fails_writing_nothing(fetch)
        self.assertIn("vergil-python3.13", str(exc.code))
        self.assertEqual(fetch.calls, [])

    def test_series_without_package_fails(self):
        (self.root / "vergil.toml").write_text(_HEAD + _package("3.14", "3.14.8", _OLD14) + _TAIL)
        fetch = _fetcher()
        self._assert_fails_writing_nothing(fetch)
        self.assertEqual(fetch.calls, [])

    def test_never_changes_repo_version(self):
        ur.main(root=self.root, fetch=_fetcher())
        self.assertEqual((self.root / "VERSION").read_text(), "1.0.5\n")
        for package in tomllib.loads(self._files()[1])["package"]:
            self.assertNotIn("1.0.5", package["upstream-version"])
            self.assertNotIn("version", package)

    def test_already_current_is_a_no_op(self):
        sums = "\n".join([*_both("3.14.8", _H["3"], _H["4"], tag=_OLD15), *_both("3.15.1", _H["5"], _H["6"], tag=_OLD15)])
        entries = [dict(_ENTRIES[0], pbs_tag=_OLD15), _ENTRIES[1]]
        (self.root / "runtime.toml").write_text(ur.render_runtime(entries))
        (self.root / "vergil.toml").write_text(_VERGIL.replace(_OLD14, _OLD15))
        before = self._files()
        self.assertEqual(ur.main(root=self.root, fetch=_fetcher(tag=_OLD15, sums=sums)), 0)
        self.assertEqual(self._files(), before)
        self.assertEqual(self._notes(), [])

    def test_newer_minor_is_noted_never_added(self):
        sums = _SUMS + "\n" + "\n".join(_both("3.16.0", _H["e"], _H["f"]))
        self.assertEqual(ur.main(root=self.root, fetch=_fetcher(sums=sums)), 0)
        self.assertEqual([e["series"] for e in ur.load_series(self._files()[0])], ["3.14", "3.15"])
        self.assertNotIn("3.16", self._files()[1])
        self.assertIn(
            f"held back: PBS {_TAG} ships CPython 3.16.0, newer than every listed series; "
            "a new series is added to runtime.toml and vergil.toml by hand",
            self._notes(),
        )

    def test_bad_runtime_fails_without_fetching_or_writing(self):
        for runtime in [self.runtime.replace('series = "3.14"', 'series = "3.15"'),
                        self.runtime.replace('series = "3.14"\n', ""),
                        self.runtime.replace(f'pbs_tag = "{_OLD14}"', 'pbs_tag = "latest"')]:
            (self.root / "runtime.toml").write_text(runtime)
            fetch = _fetcher()
            with self.subTest(runtime=runtime):
                self._assert_fails_writing_nothing(fetch)
                self.assertEqual(fetch.calls, [])

    def test_unexpected_latest_tag_fails(self):
        self._assert_fails_writing_nothing(lambda url: "https://github.com/astral-sh/python-build-standalone/releases")

    def test_older_latest_tag_fails(self):
        self._assert_fails_writing_nothing(_fetcher(tag="20261015", sums=_SUMS))

    def test_fetch_error_propagates_loud(self):
        def fetch(url):
            raise SystemExit("upstream-refresh: boom")
        self._assert_fails_writing_nothing(fetch)

    def test_report_env_unset_fails(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self._assert_fails_writing_nothing(_fetcher())


class FetchTest(unittest.TestCase):
    def test_network_error_fails_loud(self):
        err = urllib.error.URLError("no route to host")
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(SystemExit) as cm:
                ur.fetch(ur.LATEST_URL)
        self.assertIn("no route to host", str(cm.exception.code))

    def test_http_error_fails_loud(self):
        err = urllib.error.HTTPError(ur.LATEST_URL, 503, "Service Unavailable", None, io.BytesIO(b""))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(SystemExit):
                ur.fetch(ur.sums_url(_TAG))

    def test_latest_returns_redirect_target(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.geturl.return_value = "https://github.com/x/releases/tag/20261101"
        with mock.patch("urllib.request.urlopen", return_value=resp):
            self.assertEqual(ur.fetch(ur.LATEST_URL), "https://github.com/x/releases/tag/20261101")

    def test_body_is_decoded(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = b"abc\n"
        with mock.patch("urllib.request.urlopen", return_value=resp):
            self.assertEqual(ur.fetch(ur.sums_url(_TAG)), "abc\n")


if __name__ == "__main__":
    unittest.main()
