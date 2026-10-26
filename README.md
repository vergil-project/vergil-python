# vergil-python

CPython runtime (python-build-standalone) packaged as one `.deb`/`.rpm` per
listed CPython series (`vergil-python3.14`, `vergil-python3.15`) for vergil
products.

## Table of Contents

- [Status](#status)
- [Overview](#overview)
- [The current pins](#the-current-pins)
- [Install](#install)
- [Repository layout](#repository-layout)
- [License](#license)

## Status

Released. The first package shipped in
[v1.0.0](https://github.com/vergil-project/vergil-python/releases/tag/v1.0.0),
and every release is served, signed, from the vergil package repository at
<https://vergil-project.github.io/packages>. Background: epic
`vergil-project/.github#356`.

## Overview

This repository republishes one verified
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
(PBS) build of CPython per listed series as an OS package, for Ubuntu 24.04 and
26.04 (`.deb`) and RHEL 9 and 10 (`.rpm`), on amd64 and arm64. Every release
builds and publishes every listed series.

- The package name carries the CPython series (`vergil-python<X.Y>`). The
  version carries the patch and the PBS build, then this repository's release
  (`<X.Y.Z>+<PBS tag>.<release>`, for example `3.15.1+20261021.1.0.5`). A new
  patch, PBS rebuild or release is an in-place upgrade; a new series is a new
  package installed alongside.
- The interpreter installs to `/opt/vergil/python/<X.Y>/` and is never put on
  `PATH`, so it cannot shadow the system `python3`.
- A PEP 668 `EXTERNALLY-MANAGED` marker stops `pip install` into the shared
  runtime. Products build their own venv on top of it.

See [docs/runtime-package-pattern.md](docs/runtime-package-pattern.md) for the
design, how the pins change, and the release rule.

## The current pins

The pins live in two files, and they are the only source of truth:

- [`runtime.toml`](runtime.toml): one `[[series]]` entry per listed series,
  with the CPython series (`series`), the patch built for it (`cpython`), the
  PBS release (`pbs_tag`) and the SHA-256 of each architecture's archive.
- [`vergil.toml`](vergil.toml): one `[[package]]` entry per series, with the
  package `name` (`vergil-python<series>`), `upstream-version`
  (`<cpython>+<pbs_tag>`), `summary`, and the `smoke` test, which names the
  interpreter path `/opt/vergil/python/<series>/bin/python<series>`.

They are not repeated here because they change often. This repository's
upstream-refresh hook, run by `vrg-upstream-refresh`, moves both files together
to the newest PBS build and CPython patch within each series, in one reviewed
PR. See [Changing the pins](docs/runtime-package-pattern.md#changing-the-pins).

## Install

Products that need the runtime depend on it, so it normally arrives as a
dependency. To install it directly, enable the vergil package repository (the
`vergil-archive-keyring` package from `vergil-project/packages`), then install
a package named by a `[[package]]` `name` in `vergil.toml`, for example:

```bash
sudo apt install vergil-python3.15   # Ubuntu
sudo dnf install vergil-python3.15   # RHEL
```

## Repository layout

| Path | Purpose |
|---|---|
| `runtime.toml` | The pins: one `[[series]]` per listed series, with its CPython patch, PBS tag and SHA-256 per architecture. |
| `vergil.toml` | One `[[package]]` per series: name, upstream-version, summary and smoke test for the `staged` builder. |
| `packaging/build.sh` | Selects the series named by `VRG_PACKAGE_NAME`, then fetches, verifies and unpacks its pinned archive into the staging root and writes the PEP 668 marker. |
| `scripts/dev/upstream-refresh.sh` | The upstream-refresh hook (`[upstream-refresh].hook` in `vergil.toml`) that `vrg-upstream-refresh` runs to move the pins. |
| `scripts/dev/upstream_refresh.py` | The hook's logic: refreshes each series from the newest PBS build and rewrites `runtime.toml` and `vergil.toml` together. |
| `tests/test_upstream_refresh.py` | Offline unit tests for `upstream_refresh.py`. |
| `tests/check-upstream-refresh.sh` | Runs those tests in the dev container. |
| `tests/test_build.py` | Offline tests for `packaging/build.sh`: series selection, the consistency checks, the install root and the build matrix. |
| `tests/check-build.sh` | Runs those tests in the dev container. |
| `scripts/bin/validate-custom` | Repo-specific checks that `vrg-validate` runs as its `custom` stage; runs `tests/check-upstream-refresh.sh` and `tests/check-build.sh`. |
| `docs/runtime-package-pattern.md` | Why the runtime is packaged this way, and how to change it. |

## License

MIT — see [LICENSE](LICENSE).
