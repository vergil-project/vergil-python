# The runtime-package pattern

This repository packages a CPython runtime as an OS package, **one package per
CPython series** (`vergil-python<X.Y>`, `.deb` and `.rpm`), that vergil products
depend on. This document explains why the runtime is packaged this way, how to
change the pin, and when the same pattern applies to other languages.

Background: epic `vergil-project/.github#356`, spec §4.1, §6.2 and decision D7.
The per-series model comes from epic `vergil-project/.github#407` (the language
version lifecycle), spec §7.

## Table of Contents

- [Why the runtime is its own package](#why-the-runtime-is-its-own-package)
- [One package per series, side by side](#one-package-per-series-side-by-side)
- [The patch and the PBS build float within the series](#the-patch-and-the-pbs-build-float-within-the-series)
- [Never on PATH, never pip-installable](#never-on-path-never-pip-installable)
- [Provenance and the build](#provenance-and-the-build)
- [Changing the pin](#changing-the-pin)
- [Releasing](#releasing)
- [Never re-release an unchanged name and version](#never-re-release-an-unchanged-name-and-version)
- [Which languages the pattern applies to](#which-languages-the-pattern-applies-to)

## Why the runtime is its own package

A Python product is shipped as a virtual environment at
`/opt/vergil/<name>/venv/`. A venv is not self-contained: it points at the
interpreter it was created with, and it cannot be moved. So every product needs
a known interpreter at a fixed path on every target, and that interpreter must
be the series the product was tested against.

The operating system's `python3` cannot be that interpreter. Its version
differs between Ubuntu 24.04, Ubuntu 26.04, RHEL 9 and RHEL 10, and the
distribution can change it underneath us. Bundling an interpreter into every
product package would duplicate the unpacked PBS tree (roughly 90 to 110 MB)
in every product, and make a security fix in the interpreter's bundled
libraries (OpenSSL, SQLite, zlib, ...) a rebuild of every product.

Packaging the runtime once, as its own package, gives one interpreter per
CPython series, installed by the package manager as a normal dependency.
Products declare `Depends: vergil-python3.14 (>= <tested version>)` (deb) or
`Requires: vergil-python3.14 >= <tested version>` (rpm).

## One package per series, side by side

The CPython **series** (`X.Y`) is part of the package name; the patch is not:
`vergil-python3.14` installs to `/opt/vergil/python/3.14/`. A different series
is a different package, installed alongside at its own path, so
`vergil-python3.14` and `vergil-python3.15` coexist on one host. A product
moves to a new series on its own schedule, and that never disturbs another
product.

Each package is built from `runtime.toml`'s `series`, which follows the newest
version in the vergil-containers version window. Moving `series` to a new `X.Y`
is one reviewed PR, made after the containers add that version. The packages
already published for earlier series stay in the apt and dnf index, so they
remain installable alongside the new one until their series is dropped.

Directory ownership follows spec §6.3: the package owns every directory strictly
below `/opt/vergil`, never `/opt/vergil` itself. dpkg and rpm reference-count
shared directories such as `/opt/vergil/python`, so removing the last runtime
leaves nothing behind.

## The patch and the PBS build float within the series

The interpreter comes from
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
(PBS): relocatable CPython builds that need only glibc
2.17 or newer, which every supported target exceeds.

- **The CPython patch floats within the series.** A new patch (`3.14.8` to
  `3.14.9`) is the same package with a higher **version**
  (`3.14.9+<PBS tag>`), installed in place at the same path. CPython keeps the
  ABI stable within a series, so a product's venv keeps working across the
  upgrade.
- **The PBS build floats too.** PBS republishes the same CPython patch whenever
  a bundled library (OpenSSL, SQLite, ...) gets a fix. That rebuild is again
  the same package with a higher version (`3.14.8+<newer PBS tag>`).
- Products depend on `>=` the version they were tested with, so they pick up
  patch and library security fixes without a product rebuild.

dpkg and rpm compare the version's numeric parts numerically, so `3.14.10+<tag>`
sorts after `3.14.9+<tag>`.

## Never on PATH, never pip-installable

- **Nothing goes in `/usr/bin`.** The runtime is reachable only at
  `/opt/vergil/python/<X.Y>/bin/python<X.Y>`. It can never shadow the
  system `python3` that the OS and Ansible rely on.
- **The PEP 668 `EXTERNALLY-MANAGED` marker** is written into
  `lib/python<X.Y>/`. `pip install` into the shared runtime is refused; products
  install into their own venv instead.

## Provenance and the build

`runtime.toml` is the pin:

| Key | Meaning |
|---|---|
| `series` | The CPython series this repository builds, e.g. `3.14`. It names the package and its install root. |
| `cpython` | The CPython patch built for the series, e.g. `3.14.8`. It must be a patch of `series`. |
| `pbs_tag` | The PBS release tag (`YYYYMMDD`) the archive comes from. |
| `sha256.x86_64` / `sha256.aarch64` | SHA-256 of each architecture's `install_only_stripped` archive, copied from that release's `SHA256SUMS` asset. |

The package is built by the vergil-tooling `staged` builder (spec §6.6), which
runs `packaging/build.sh` in each build cell with `VRG_STAGING_ROOT` and
`VRG_TARGET_ARCH` set. The script:

1. checks that `series` is an `X.Y` and that `cpython` is an `X.Y.Z` patch of
   it;
2. checks that `vergil.toml` `[package].name` is `vergil-python<series>`,
   `[package].version` is `<cpython>+<pbs_tag>`, and `[package].smoke` runs
   `/opt/vergil/python/<series>/bin/python<series>`, so the two files cannot
   drift;
3. maps `VRG_TARGET_ARCH` (`amd64` or `arm64`) to the PBS triple
   (`x86_64-unknown-linux-gnu` or `aarch64-unknown-linux-gnu`);
4. downloads
   `cpython-<cpython>+<pbs_tag>-<triple>-install_only_stripped.tar.gz` from the
   PBS release and fails unless its SHA-256 matches `runtime.toml`;
5. unpacks the archive's `python/` root into
   `$VRG_STAGING_ROOT/opt/vergil/python/<series>/`;
6. writes the `EXTERNALLY-MANAGED` marker.

The builder then applies the glibc floor guard to every ELF file in the tree and
hands it to nFPM. In PR CI, `package / evidence` installs each package on all
eight targets and runs the `smoke` command from `vergil.toml`, which imports
`ssl`, `sqlite3`, `ctypes` and `zlib` with the packaged interpreter.

The offline tests for these checks and for the install root are
`tests/test_build.py`, run by `tests/check-build.sh`. They serve a small fake
archive through a stand-in `curl`, so they never touch the network.

## Changing the pin

Every pin change is one reviewed PR. Normally the PR is opened for you.

### The dependency-update hook

`vergil.toml` names `scripts/dev/update-deps.sh` as this repository's
`[dependency-update].hook`. Every `vrg-release` runs it in its dependency-update
stage, after the release is published, and the change it makes lands on
`develop` as a dependency-update PR for review. The hook runs
`scripts/dev/update_deps.py`, which:

1. takes the newest python-build-standalone release (the target of
   `releases/latest`);
2. reads that release's `SHA256SUMS` and takes the newest CPython patch
   **within `series`** that it ships as `install_only_stripped` for both
   `x86_64-unknown-linux-gnu` and `aarch64-unknown-linux-gnu`;
3. rewrites `runtime.toml` (`cpython`, `pbs_tag` and both `sha256` values) and
   `vergil.toml` `[package]` (`version` and `summary`) together, so
   `packaging/build.sh`'s consistency check holds. It never changes `series`,
   the package `name` or the `smoke` path, which all name the series; it only
   checks that they agree with it. When the pin is already current it changes
   nothing;
4. reports a newer stable CPython minor, if the release ships one, in the
   release's dependency report as held back. It never moves the series.

It fails, before writing either file, if `series` is missing or `cpython` is
not a patch of it, if the package `name` or `smoke` path does not name the
series, if the newest PBS release is older than the pinned tag or ships only an
older patch than the pinned one (a downgrade), if `SHA256SUMS` lists two
different checksums for the same archive, if the chosen patch is missing an
archive for either architecture, or if either file does not have the layout it
expects. Its offline unit tests are `tests/test_update_deps.py`, run by
`tests/check-update-deps.sh`.

### By hand

Change the pin by hand when the hook cannot, and always for a new series. Pick
the newest PBS release that ships the wanted patch as `install_only_stripped`
for both `x86_64-unknown-linux-gnu` and `aarch64-unknown-linux-gnu`, and take
both checksums from its `SHA256SUMS`. Never type a checksum by hand from
anywhere else.

**A new patch or PBS rebuild within the series** (an in-place upgrade of the
same package):

- `runtime.toml`: `cpython` (for a new patch), `pbs_tag` and both `sha256`
  values.
- `vergil.toml` `[package]`: `version` (`<cpython>+<pbs_tag>`) and `summary`.

**A new series** (a new package, installed alongside the old one). Make this
change only after vergil-containers has added the version to its window:

- `runtime.toml`: `series`, `cpython`, `pbs_tag` and both `sha256` values.
- `vergil.toml` `[package]`: `name` (`vergil-python<series>`), `version`,
  `summary`, and the interpreter path in `smoke`
  (`/opt/vergil/python/<series>/bin/python<series>`).

The hook only reports a new minor, so a new series is always moved by hand.
Products move to a new series on their own schedule. The previous series'
package stays in the index while a retained product still depends on it.

## Releasing

A release runs `.github/workflows/cd.yml` on `main`, which calls the
vergil-actions `cd-release.yml` reusable workflow:

- **Signing happens in the `package-signing` environment.** Its
  deployment-branch policy admits only `main` (spec §7.4), and it holds
  `PACKAGE_SIGNING_KEY` and `PACKAGE_SIGNING_PASSPHRASE`. `cd-release`'s
  `package-sign` job signs each `.rpm` and attests the provenance of every
  package there before the release attaches them. The `.deb` files are
  covered by the signed apt index.
- **`cd.yml` must pass `secrets: inherit`**, with its
  `# nosemgrep: yaml.github-actions.security.secrets-inherit.secrets-inherit`
  comment. Environment secrets reach a job in a cross-repo reusable workflow
  only through `inherit`. With an explicit `secrets:` map, `package-sign` sees
  an empty `PACKAGE_SIGNING_KEY` and fails.
- **The index is rebuilt after the release.** Once a new release is created,
  `cd-release` dispatches `package-released` to `vergil-project/packages`. Its
  `publish-index` workflow rebuilds, re-signs and deploys the apt/dnf index.
  A missed dispatch is picked up by that workflow's weekly reconcile.

## Never re-release an unchanged name and version

Every release of this repository builds and publishes the package named by
`vergil.toml`. The package version is the explicit `[package].version`, not the
repository `VERSION`, so **a release that does not change the pin re-publishes
the same `name`/`version` with different bytes** (the packages are rebuilt,
and nothing guarantees the rebuilt bytes are identical).

The index treats two artifacts with the same format, name, version-release and
architecture but different bytes as a hard error, naming both releases (spec
§7.2). It never picks one. So:

- **Only release this repository when the pin changes.** Documentation or
  workflow changes merge to `develop` and ship with the next pin change.
- **The next pin usually arrives right after a release.** The release's
  dependency-update stage opens a PR that moves the pin, so the next pin
  normally lands on `develop` as that PR, and `vrg-release` refuses to start
  while a dependency-update PR is still open (unless given
  `--allow-pending-deps`). Merge it before the next release.
  If the hook found the pin already current, there is no new pin, and the
  next release has to wait for one.
- If a packaging change other than the pin must ship (for example a fix to
  `packaging/build.sh` that changes the installed tree), it still needs a new
  package version. Take the next PBS rebuild or CPython patch of the series in
  the same release.

## Which languages the pattern applies to

The pattern fits any language whose programs run on a **versioned interpreter or
virtual machine** that must match what the product was tested with:

- **Ruby**: a Ruby build per series, with gems installed into a per-product
  bundle.
- **Perl**: a Perl per version, with modules installed into a per-product
  `local::lib`.
- **The JVM**: a JDK or JRE per major version, with the product's jars on top.

It does **not** apply to languages that compile to native binaries (Go, Rust,
C, C++). Their products carry no runtime to share: the binary is the package,
built by the `staged` builder (for example `cmake --install` into the staging
root) and checked by the same glibc floor guard.
