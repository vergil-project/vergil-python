"""Offline unit tests for scripts/dev/update_deps.py (stdlib unittest only).

Every network lookup is injected through ``main(fetch=...)`` or a patched
``urllib.request.urlopen``; nothing here touches the network.
"""

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "dev"))
import update_deps as ud  # noqa: E402

_TAG = "20261101"
_OLD_TAG = "20261003"
_X86 = "x86_64-unknown-linux-gnu"
_ARM = "aarch64-unknown-linux-gnu"
_STRIPPED = "install_only_stripped.tar.gz"


def _line(sha: str, version: str, triple: str, tag: str = _TAG, kind: str = _STRIPPED) -> str:
    return f"{sha}  cpython-{version}+{tag}-{triple}-{kind}"


_SUMS = "\n".join(
    [
        _line("a" * 64, "3.14.9", _X86),
        _line("b" * 64, "3.14.9", _ARM),
        _line("1" * 64, "3.14.7", _X86),
        _line("2" * 64, "3.14.7", _ARM),
        _line("c" * 64, "3.15.1", _X86),
        _line("d" * 64, "3.15.1", _ARM),
        _line("e" * 64, "3.16.0a1", _X86),
        _line("e" * 64, "3.16.0rc1", _ARM),
        _line("f" * 64, "3.13.12", _X86),
        _line("9" * 64, "3.14.9", _X86, kind="install_only.tar.gz"),
        _line("8" * 64, "3.14.9", f"{_X86}-freethreaded", kind=_STRIPPED),
        _line("7" * 64, "3.14.9", "x86_64_v3-unknown-linux-gnu"),
        _line("6" * 64, "3.14.9", "x86_64-apple-darwin"),
        _line("5" * 64, "3.14.9", _X86, tag="20250101"),
        "",
    ]
)

_RUNTIME = (
    "# header comment\n"
    "# cpython = \"0.0.0\" in a comment must survive\n"
    "\n"
    'series = "3.14"\n'
    'cpython = "3.14.8"\n'
    f'pbs_tag = "{_OLD_TAG}"\n'
    "\n"
    "[sha256]\n"
    'x86_64 = "old1"\n'
    'aarch64 = "old2"\n'
)

_VERGIL = (
    "[project]\n"
    'repository-type = "infrastructure"\n'
    "\n"
    "[dependencies]\n"
    'vergil = "v2.1"\n'
    "\n"
    "[package]\n"
    'builder = "staged"\n'
    'vendor = "vergil"\n'
    'name = "vergil-python3.14"\n'
    f'version = "3.14.8+{_OLD_TAG}"\n'
    f'summary = "Pinned CPython 3.14.8 (python-build-standalone {_OLD_TAG}) for vergil products"\n'
    "smoke = \"/opt/vergil/python/3.14/bin/python3.14 -c 'import ssl'\"\n"
    "\n"
    "[package.staged]\n"
    'build-command = "packaging/build.sh"\n'
)


def _fetcher(tag: str = _TAG, sums: str = _SUMS):
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        if url == ud.LATEST_URL:
            return f"https://github.com/astral-sh/python-build-standalone/releases/tag/{tag}"
        if url == ud.sums_url(tag):
            return sums
        raise AssertionError(f"unexpected URL {url}")

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


class ParseSumsTest(unittest.TestCase):
    def test_stable_stripped_linux_gnu_assets_of_the_tag(self):
        sums = ud.parse_sums(_SUMS, _TAG)
        self.assertEqual(sums["3.14.9"], {"x86_64": "a" * 64, "aarch64": "b" * 64})
        self.assertEqual(sums["3.15.1"], {"x86_64": "c" * 64, "aarch64": "d" * 64})
        self.assertNotIn("3.16.0a1", sums)
        self.assertNotIn("3.16.0rc1", sums)

    def test_keeps_partial_versions_so_a_gap_can_fail_loud(self):
        self.assertEqual(ud.parse_sums(_SUMS, _TAG)["3.13.12"], {"x86_64": "f" * 64})

    def test_conflicting_checksums_fail(self):
        text = _SUMS + "\n" + _line("0" * 64, "3.14.9", _X86)
        with self.assertRaises(SystemExit):
            ud.parse_sums(text, _TAG)

    def test_no_assets_for_tag_fails(self):
        with self.assertRaises(SystemExit):
            ud.parse_sums(_SUMS, "20991231")


class CheckSeriesTest(unittest.TestCase):
    def test_patch_within_series_passes(self):
        ud.check_series("3.14", "3.14.8")
        ud.check_series("3.9", "3.9.20")

    def test_series_mismatch_fails(self):
        for series, cpython in [("3.14", "3.15.0"), ("3.1", "3.14.8"), ("3.14", "3.14"), ("3.14", "3.14.8a1")]:
            with self.subTest(series=series, cpython=cpython), self.assertRaises(SystemExit):
                ud.check_series(series, cpython)

    def test_malformed_or_missing_series_fails(self):
        for series in [None, "", "3", "3.14.8", "three.fourteen", 3.14]:
            with self.subTest(series=series), self.assertRaises(SystemExit):
                ud.check_series(series, "3.14.8")


class PickTest(unittest.TestCase):
    def test_newest_patch_within_series_and_newer_minor(self):
        self.assertEqual(
            ud.pick("3.14", "3.14.8", ["3.14.7", "3.14.9", "3.15.1", "3.13.12"]), ("3.14.9", "3.15.1")
        )

    def test_no_newer_minor(self):
        self.assertEqual(ud.pick("3.14", "3.14.9", ["3.14.9", "3.13.12"]), ("3.14.9", None))

    def test_numeric_not_lexical_ordering(self):
        self.assertEqual(ud.pick("3.14", "3.14.9", ["3.14.9", "3.14.10"]), ("3.14.10", None))
        self.assertEqual(ud.pick("3.9", "3.9.1", ["3.9.1", "3.10.0"]), ("3.9.1", "3.10.0"))

    def test_series_missing_fails(self):
        with self.assertRaises(SystemExit):
            ud.pick("3.14", "3.14.8", ["3.15.1"])

    def test_refuses_to_downgrade_the_patch(self):
        with self.assertRaises(SystemExit):
            ud.pick("3.14", "3.14.8", ["3.14.7"])


class RenderRuntimeTest(unittest.TestCase):
    def test_rewrites_values_and_keeps_comments(self):
        out = ud.render_runtime(_RUNTIME, "3.14.9", _TAG, {"x86_64": "aaa", "aarch64": "bbb"})
        self.assertTrue(out.startswith("# header comment\n# cpython = \"0.0.0\" in a comment must survive\n"))
        self.assertIn('\ncpython = "3.14.9"\n', out)
        self.assertIn(f'\npbs_tag = "{_TAG}"\n', out)
        self.assertIn('\nx86_64 = "aaa"\n', out)
        self.assertIn('\naarch64 = "bbb"\n', out)

    def test_missing_key_fails(self):
        with self.assertRaises(SystemExit):
            ud.render_runtime(_RUNTIME.replace('aarch64 = "old2"\n', ""), "3.14.9", _TAG,
                              {"x86_64": "aaa", "aarch64": "bbb"})

    def test_incomplete_sums_fail(self):
        with self.assertRaises(SystemExit):
            ud.render_runtime(_RUNTIME, "3.14.9", _TAG, {"x86_64": "aaa"})

    def test_keeps_series(self):
        out = ud.render_runtime(_RUNTIME, "3.14.9", _TAG, {"x86_64": "aaa", "aarch64": "bbb"})
        self.assertIn('\nseries = "3.14"\n', out)

    def test_patch_outside_series_fails(self):
        with self.assertRaises(SystemExit):
            ud.render_runtime(_RUNTIME, "3.15.0", _TAG, {"x86_64": "aaa", "aarch64": "bbb"})

    def test_missing_series_fails(self):
        with self.assertRaises(SystemExit):
            ud.render_runtime(_RUNTIME.replace('series = "3.14"\n', ""), "3.14.9", _TAG,
                              {"x86_64": "aaa", "aarch64": "bbb"})


class RewritePackageTest(unittest.TestCase):
    def test_new_patch_rewrites_version_and_summary_only(self):
        out = ud.rewrite_package(_VERGIL, "3.14", "3.14.8", _OLD_TAG, "3.14.9", _TAG)
        self.assertIn('name = "vergil-python3.14"\n', out)
        self.assertIn(f'version = "3.14.9+{_TAG}"', out)
        self.assertIn(f'summary = "Pinned CPython 3.14.9 (python-build-standalone {_TAG}) for vergil products"', out)
        self.assertIn("/opt/vergil/python/3.14/bin/python3.14 -c", out)
        self.assertIn('vergil = "v2.1"', out)
        self.assertIn('build-command = "packaging/build.sh"', out)
        self.assertNotIn("3.14.8", out)
        self.assertNotIn(_OLD_TAG, out)
        self.assertEqual(out, _VERGIL.replace("3.14.8", "3.14.9").replace(_OLD_TAG, _TAG))

    def test_pbs_rebuild_keeps_name(self):
        out = ud.rewrite_package(_VERGIL, "3.14", "3.14.8", _OLD_TAG, "3.14.8", _TAG)
        self.assertIn('name = "vergil-python3.14"\n', out)
        self.assertIn(f'version = "3.14.8+{_TAG}"', out)

    def test_package_table_first_in_file(self):
        text = _VERGIL[_VERGIL.index("[package]\n"):]
        out = ud.rewrite_package(text, "3.14", "3.14.8", _OLD_TAG, "3.14.9", _TAG)
        self.assertTrue(out.startswith("[package]\n"))
        self.assertIn(f'version = "3.14.9+{_TAG}"', out)

    def test_values_outside_package_table_untouched(self):
        text = _VERGIL + '\n[other]\nnote = "3.14.8 and 20261003"\n'
        out = ud.rewrite_package(text, "3.14", "3.14.8", _OLD_TAG, "3.14.9", _TAG)
        self.assertIn('note = "3.14.8 and 20261003"', out)

    def test_name_not_naming_the_series_fails(self):
        for name in ["vergil-python3.14.8", "vergil-python3.15", "vergil-python"]:
            bad = _VERGIL.replace('name = "vergil-python3.14"', f'name = "{name}"')
            with self.subTest(name=name), self.assertRaises(SystemExit):
                ud.rewrite_package(bad, "3.14", "3.14.8", _OLD_TAG, "3.14.9", _TAG)

    def test_smoke_not_running_the_series_interpreter_fails(self):
        for path in ["/opt/vergil/python/3.14.8/bin/python3.14", "/opt/vergil/python/3.14/bin/python3.15",
                     "/opt/vergil/python/3.14/bin/python3.14t"]:
            bad = _VERGIL.replace("/opt/vergil/python/3.14/bin/python3.14", path)
            with self.subTest(path=path), self.assertRaises(SystemExit):
                ud.rewrite_package(bad, "3.14", "3.14.8", _OLD_TAG, "3.14.9", _TAG)

    def test_inconsistent_version_fails(self):
        bad = _VERGIL.replace(f'version = "3.14.8+{_OLD_TAG}"', f'version = "3.14.7+{_OLD_TAG}"')
        with self.assertRaises(SystemExit):
            ud.rewrite_package(bad, "3.14", "3.14.8", _OLD_TAG, "3.14.9", _TAG)

    def test_new_patch_outside_series_fails(self):
        with self.assertRaises(SystemExit):
            ud.rewrite_package(_VERGIL, "3.14", "3.14.8", _OLD_TAG, "3.15.0", _TAG)

    def test_missing_package_table_fails(self):
        with self.assertRaises(SystemExit):
            ud.rewrite_package("[project]\n", "3.14", "3.14.8", _OLD_TAG, "3.14.9", _TAG)


class MainTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "runtime.toml").write_text(_RUNTIME)
        (self.root / "vergil.toml").write_text(_VERGIL)
        self.report = self.root / "report.jsonl"
        self.report.write_text("")
        env = mock.patch.dict(os.environ, {"VRG_DEPS_REPORT": str(self.report)})
        env.start()
        self.addCleanup(env.stop)

    def _files(self):
        return (self.root / "runtime.toml").read_text(), (self.root / "vergil.toml").read_text()

    def test_bumps_patch_rewrites_both_files_and_reports_minor(self):
        self.assertEqual(ud.main(root=self.root, fetch=_fetcher()), 0)
        runtime, vergil = self._files()
        self.assertIn('cpython = "3.14.9"', runtime)
        self.assertIn(f'pbs_tag = "{_TAG}"', runtime)
        self.assertIn(f'x86_64 = "{"a" * 64}"', runtime)
        self.assertIn(f'aarch64 = "{"b" * 64}"', runtime)
        self.assertIn('name = "vergil-python3.14"\n', vergil)
        self.assertIn('series = "3.14"', runtime)
        self.assertIn(f'version = "3.14.9+{_TAG}"', vergil)
        lines = [json.loads(x) for x in self.report.read_text().splitlines()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["source"], "vergil-python")
        self.assertEqual(lines[0]["name"], "cpython")
        self.assertEqual(lines[0]["current"], "3.14.9")
        self.assertEqual(lines[0]["latest"], "3.15.1")
        self.assertEqual(set(lines[0]), {"source", "name", "current", "latest", "note"})
        self.assertTrue(all(isinstance(v, str) for v in lines[0].values()))

    def test_update_hook_stays_within_series(self):
        # PBS ships both 3.14.8 and 3.15.0: the hook stays on 3.14.x and only reports 3.15.
        sums = "\n".join([_line("a" * 64, "3.14.8", _X86), _line("b" * 64, "3.14.8", _ARM),
                          _line("c" * 64, "3.15.0", _X86), _line("d" * 64, "3.15.0", _ARM)])
        self.assertEqual(ud.main(root=self.root, fetch=_fetcher(sums=sums)), 0)
        runtime, vergil = self._files()
        self.assertIn('series = "3.14"', runtime)
        self.assertIn('cpython = "3.14.8"', runtime)
        self.assertIn(f'x86_64 = "{"a" * 64}"', runtime)
        self.assertNotIn("3.15", runtime)
        self.assertIn('name = "vergil-python3.14"\n', vergil)
        self.assertIn(f'version = "3.14.8+{_TAG}"', vergil)
        self.assertNotIn("3.15", vergil)
        [entry] = [json.loads(x) for x in self.report.read_text().splitlines()]
        self.assertEqual((entry["current"], entry["latest"]), ("3.14.8", "3.15.0"))

    def test_series_mismatch_fails_without_fetching_or_writing(self):
        for runtime in [_RUNTIME.replace('series = "3.14"', 'series = "3.15"'),
                        _RUNTIME.replace('series = "3.14"\n', ""),
                        _RUNTIME.replace(f'pbs_tag = "{_OLD_TAG}"', 'pbs_tag = "latest"')]:
            (self.root / "runtime.toml").write_text(runtime)
            fetch = _fetcher()
            with self.subTest(runtime=runtime), self.assertRaises(SystemExit):
                ud.main(root=self.root, fetch=fetch)
            self.assertEqual(fetch.calls, [])
            self.assertEqual(self._files(), (runtime, _VERGIL))

    def test_package_name_mismatch_fails_without_writing(self):
        bad = _VERGIL.replace('name = "vergil-python3.14"', 'name = "vergil-python3.14.8"')
        (self.root / "vergil.toml").write_text(bad)
        with self.assertRaises(SystemExit):
            ud.main(root=self.root, fetch=_fetcher())
        self.assertEqual(self._files(), (_RUNTIME, bad))
        self.assertEqual(self.report.read_text(), "")

    def test_already_current_is_a_no_op(self):
        sums = "\n".join([_line("a" * 64, "3.14.8", _X86, tag=_OLD_TAG),
                          _line("b" * 64, "3.14.8", _ARM, tag=_OLD_TAG)])
        before = self._files()
        self.assertEqual(ud.main(root=self.root, fetch=_fetcher(tag=_OLD_TAG, sums=sums)), 0)
        self.assertEqual(self._files(), before)
        self.assertEqual(self.report.read_text(), "")

    def test_missing_arch_for_target_fails_without_writing(self):
        sums = "\n".join([_line("a" * 64, "3.14.9", _X86), _line("c" * 64, "3.14.7", _X86),
                          _line("d" * 64, "3.14.7", _ARM)])
        before = self._files()
        with self.assertRaises(SystemExit):
            ud.main(root=self.root, fetch=_fetcher(sums=sums))
        self.assertEqual(self._files(), before)
        self.assertEqual(self.report.read_text(), "")

    def test_unexpected_latest_tag_fails(self):
        def fetch(url):
            return "https://github.com/astral-sh/python-build-standalone/releases"
        with self.assertRaises(SystemExit):
            ud.main(root=self.root, fetch=fetch)

    def test_older_latest_tag_fails(self):
        with self.assertRaises(SystemExit):
            ud.main(root=self.root, fetch=_fetcher(tag="20250101", sums=_SUMS))

    def test_fetch_error_propagates_loud(self):
        def fetch(url):
            raise SystemExit("update-deps: boom")
        with self.assertRaises(SystemExit):
            ud.main(root=self.root, fetch=fetch)
        self.assertEqual(self._files(), (_RUNTIME, _VERGIL))

    def test_report_env_unset_fails(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                ud.main(root=self.root, fetch=_fetcher())


class FetchTest(unittest.TestCase):
    def test_network_error_fails_loud(self):
        err = urllib.error.URLError("no route to host")
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(SystemExit) as cm:
                ud.fetch(ud.LATEST_URL)
        self.assertIn("no route to host", str(cm.exception.code))

    def test_http_error_fails_loud(self):
        err = urllib.error.HTTPError(ud.LATEST_URL, 503, "Service Unavailable", None, io.BytesIO(b""))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(SystemExit):
                ud.fetch(ud.sums_url(_TAG))

    def test_latest_returns_redirect_target(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.geturl.return_value = "https://github.com/x/releases/tag/20261101"
        with mock.patch("urllib.request.urlopen", return_value=resp):
            self.assertEqual(ud.fetch(ud.LATEST_URL), "https://github.com/x/releases/tag/20261101")

    def test_body_is_decoded(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = b"abc\n"
        with mock.patch("urllib.request.urlopen", return_value=resp):
            self.assertEqual(ud.fetch(ud.sums_url(_TAG)), "abc\n")


if __name__ == "__main__":
    unittest.main()
