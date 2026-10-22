# vergil-python

CPython runtime (python-build-standalone) packaged as one `.deb`/`.rpm` per
CPython series (`vergil-python3.15`, ...) for vergil products.

## Table of Contents

- [Status](#status)
- [Overview](#overview)
- [The current pin](#the-current-pin)
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
(PBS) build of CPython as an OS package, for Ubuntu 24.04 and 26.04 (`.deb`) and
RHEL 9 and 10 (`.rpm`), on amd64 and arm64.

- The package name carries the CPython series (`vergil-python<X.Y>`) and the
  version carries the patch and the PBS build (`<X.Y.Z>+<PBS tag>`). A new patch
  or PBS rebuild is an in-place upgrade; a new series is a new package installed
  alongside.
- The interpreter installs to `/opt/vergil/python/<X.Y>/` and is never put on
  `PATH`, so it cannot shadow the system `python3`.
- A PEP 668 `EXTERNALLY-MANAGED` marker stops `pip install` into the shared
  runtime. Products build their own venv on top of it.

See [docs/runtime-package-pattern.md](docs/runtime-package-pattern.md) for the
design, how to change the pin, and the release rule.

## The current pin

The pin lives in two files, and they are the only source of truth:

- [`runtime.toml`](runtime.toml): the CPython series (`series`), the patch
  built for it (`cpython`), the PBS release (`pbs_tag`) and the SHA-256 of each
  architecture's archive.
- [`vergil.toml`](vergil.toml) `[package]`: the package `name`
  (`vergil-python<series>`), `version` (`<cpython>+<pbs_tag>`), `summary`, and
  the `smoke` test, which names the interpreter path
  `/opt/vergil/python/<series>/bin/python<series>`.

They are not repeated here because they change often. The dependency-update
stage of every `vrg-release` runs this repository's hook, which moves both files
together to the newest PBS build and CPython patch within the series. Moving to
a new series is a reviewed PR made by hand. See
[Changing the pin](docs/runtime-package-pattern.md#changing-the-pin).

## Install

Products that need the runtime depend on it, so it normally arrives as a
dependency. To install it directly, enable the vergil package repository (the
`vergil-archive-keyring` package from `vergil-project/packages`), then install
the package named by `[package].name` in `vergil.toml`, for example:

```bash
sudo apt install vergil-python3.15   # Ubuntu
sudo dnf install vergil-python3.15   # RHEL
```

## Repository layout

| Path | Purpose |
|---|---|
| `runtime.toml` | The pin: CPython series and patch, PBS tag, SHA-256 per architecture. |
| `vergil.toml` | `[package]`: name, version, summary and smoke test for the `staged` builder. |
| `packaging/build.sh` | Fetches, verifies and unpacks the pinned archive into the staging root and writes the PEP 668 marker. |
| `scripts/dev/update-deps.sh` | The dependency-update hook (`[dependency-update].hook` in `vergil.toml`) that `vrg-release` runs to move the pin. |
| `scripts/dev/update_deps.py` | The hook's logic: finds the newest PBS build and rewrites `runtime.toml` and `vergil.toml` `[package]` together. |
| `tests/test_update_deps.py` | Offline unit tests for `update_deps.py`. |
| `tests/check-update-deps.sh` | Runs those tests in the dev container. |
| `tests/test_build.py` | Offline tests for `packaging/build.sh`: the series consistency checks and the install root. |
| `tests/check-build.sh` | Runs those tests in the dev container. |
| `scripts/bin/validate-custom` | Repo-specific checks that `vrg-validate` runs as its `custom` stage; runs `tests/check-update-deps.sh` and `tests/check-build.sh`. |
| `docs/runtime-package-pattern.md` | Why the runtime is packaged this way, and how to change it. |

## License

MIT — see [LICENSE](LICENSE).
