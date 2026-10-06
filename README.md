# vergil-python

Pinned CPython runtime (python-build-standalone) packaged as
`vergil-python3.14.N` `.deb`/`.rpm` for vergil products.

## Table of Contents

- [Status](#status)
- [Overview](#overview)
- [The current pin](#the-current-pin)
- [Install](#install)
- [Repository layout](#repository-layout)
- [License](#license)

## Status

Early development (epic `vergil-project/.github#356`).

## Overview

This repository republishes one verified
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
(PBS) build of CPython as an OS package, for Ubuntu 24.04 and 26.04 (`.deb`) and
RHEL 9 and 10 (`.rpm`), on amd64 and arm64.

- The package name carries the CPython patch (`vergil-python3.14.8`) and the
  version carries the PBS build (`3.14.8+20261003`). A PBS rebuild is an in-place
  upgrade; a new CPython patch is a new package installed alongside.
- The interpreter installs to `/opt/vergil/python/<X.Y.Z>/` and is never put on
  `PATH`, so it cannot shadow the system `python3`.
- A PEP 668 `EXTERNALLY-MANAGED` marker stops `pip install` into the shared
  runtime. Products build their own venv on top of it.

See [docs/runtime-package-pattern.md](docs/runtime-package-pattern.md) for the
design, how to change the pin, and the release rule.

## The current pin

| Item | Value |
|---|---|
| CPython | 3.14.8 |
| PBS release | [`20261003`](https://github.com/astral-sh/python-build-standalone/releases/tag/20261003) |
| Package | `vergil-python3.14.8`, version `3.14.8+20261003` |
| Interpreter | `/opt/vergil/python/3.14.8/bin/python3.14` |

The authoritative values, including the per-architecture SHA-256 checksums, are
in [`runtime.toml`](runtime.toml).

## Install

Products that need the runtime depend on it, so it normally arrives as a
dependency. To install it directly, enable the vergil package repository (the
`vergil-archive-keyring` package from `vergil-project/packages`), then:

```bash
sudo apt install vergil-python3.14.8   # Ubuntu
sudo dnf install vergil-python3.14.8   # RHEL
```

## Repository layout

| Path | Purpose |
|---|---|
| `runtime.toml` | The pin: CPython patch, PBS tag, SHA-256 per architecture. |
| `vergil.toml` | `[package]`: name, version, summary and smoke test for the `staged` builder. |
| `packaging/build.sh` | Fetches, verifies and unpacks the pinned archive into the staging root and writes the PEP 668 marker. |
| `docs/runtime-package-pattern.md` | Why the runtime is packaged this way, and how to change it. |

## License

MIT — see [LICENSE](LICENSE).
