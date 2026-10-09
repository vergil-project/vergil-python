# The runtime-package pattern

This repository packages one pinned CPython build as an OS package
(`vergil-python<X.Y.Z>`, `.deb` and `.rpm`) that vergil products depend on. This
document explains why the runtime is packaged this way, how to change the pin,
and when the same pattern applies to other languages.

Background: epic `vergil-project/.github#356`, spec §4.1, §6.2 and decision D7.

## Table of Contents

- [Why the runtime is its own package](#why-the-runtime-is-its-own-package)
- [Side by side, one package per patch](#side-by-side-one-package-per-patch)
- [Exact CPython pin, floating PBS rebuilds](#exact-cpython-pin-floating-pbs-rebuilds)
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
be the same bytes the product was tested against.

The operating system's `python3` cannot be that interpreter. Its version
differs between Ubuntu 24.04, Ubuntu 26.04, RHEL 9 and RHEL 10, and the
distribution can change it underneath us. Bundling an interpreter into every
product package would duplicate the unpacked PBS tree (roughly 90 to 110 MB)
in every product, and make a security fix in the interpreter's bundled
libraries (OpenSSL, SQLite, zlib, ...) a rebuild of every product.

Packaging the runtime once, as its own package, gives one interpreter per
CPython patch, installed by the package manager as a normal dependency. Products
declare `Depends: vergil-python3.14.N (>= <PBS build>)` (deb) or
`Requires: vergil-python3.14.N >= <PBS build>` (rpm).

## Side by side, one package per patch

The CPython patch is part of the **package name**, not just its version:
`vergil-python3.14.8` installs to `/opt/vergil/python/3.14.8/`. A different
patch is a different package, installed alongside at its own path. Two products
pinned to different patches can coexist on one host, and moving one product to a
new patch never disturbs another.

Directory ownership follows spec §6.3: the package owns every directory strictly
below `/opt/vergil`, never `/opt/vergil` itself. dpkg and rpm reference-count
shared directories such as `/opt/vergil/python`, so removing the last runtime
leaves nothing behind.

## Exact CPython pin, floating PBS rebuilds

The interpreter comes from
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
(PBS): relocatable CPython builds that need only glibc
2.17 or newer, which every supported target exceeds.

- **The CPython patch is exact.** A product is tested against, and pins,
  one patch (for example `3.14.8`). The tested interpreter is the deployed
  interpreter.
- **The PBS build floats.** PBS republishes the same CPython patch whenever a
  bundled library (OpenSSL, SQLite, ...) gets a fix. That rebuild is the same
  package name with a higher **version** (`3.14.8+<newer PBS tag>`), so it is an
  in-place upgrade. Products depend on `>=` the PBS build they were tested with,
  so they pick up library security fixes without a product rebuild.

## Never on PATH, never pip-installable

- **Nothing goes in `/usr/bin`.** The runtime is reachable only at
  `/opt/vergil/python/<X.Y.Z>/bin/python<X.Y>`. It can never shadow the
  system `python3` that the OS and Ansible rely on.
- **The PEP 668 `EXTERNALLY-MANAGED` marker** is written into
  `lib/python<X.Y>/`. `pip install` into the shared runtime is refused; products
  install into their own venv instead.

## Provenance and the build

`runtime.toml` is the pin:

| Key | Meaning |
|---|---|
| `cpython` | The exact CPython patch, e.g. `3.14.8`. |
| `pbs_tag` | The PBS release tag (`YYYYMMDD`) the archive comes from. |
| `sha256.x86_64` / `sha256.aarch64` | SHA-256 of each architecture's `install_only_stripped` archive, copied from that release's `SHA256SUMS` asset. |

The package is built by the vergil-tooling `staged` builder (spec §6.6), which
runs `packaging/build.sh` in each build cell with `VRG_STAGING_ROOT` and
`VRG_TARGET_ARCH` set. The script:

1. checks that `vergil.toml` `[package].name` is `vergil-python<cpython>` and
   `[package].version` is `<cpython>+<pbs_tag>`, so the two files cannot drift;
2. maps `VRG_TARGET_ARCH` (`amd64` or `arm64`) to the PBS triple
   (`x86_64-unknown-linux-gnu` or `aarch64-unknown-linux-gnu`);
3. downloads
   `cpython-<cpython>+<pbs_tag>-<triple>-install_only_stripped.tar.gz` from the
   PBS release and fails unless its SHA-256 matches `runtime.toml`;
4. unpacks the archive's `python/` root into
   `$VRG_STAGING_ROOT/opt/vergil/python/<cpython>/`;
5. writes the `EXTERNALLY-MANAGED` marker.

The builder then applies the glibc floor guard to every ELF file in the tree and
hands it to nFPM. In PR CI, `package / evidence` installs each package on all
eight targets and runs the `smoke` command from `vergil.toml`, which imports
`ssl`, `sqlite3`, `ctypes` and `zlib` with the packaged interpreter.

## Changing the pin

Every pin change is one reviewed PR. Pick the newest PBS release that ships the
wanted patch as `install_only_stripped` for both `x86_64-unknown-linux-gnu` and
`aarch64-unknown-linux-gnu`, and take both checksums from its `SHA256SUMS`.
Never type a checksum by hand from anywhere else.

**A PBS rebuild of the same patch** (an in-place upgrade):

- `runtime.toml`: `pbs_tag` and both `sha256` values.
- `vergil.toml` `[package]`: `version` (`<cpython>+<new tag>`) and `summary`.

**A new CPython patch** (a new package, installed alongside the old one):

- `runtime.toml`: `cpython`, `pbs_tag` and both `sha256` values.
- `vergil.toml` `[package]`: `name`, `version`, `summary`, and the interpreter
  path in `smoke`.

Products move to a new patch on their own schedule, by changing
`[package.python].runtime` in their own `vergil.toml`. The previous patch's
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
- If a packaging change other than the pin must ship (for example a fix to
  `packaging/build.sh` that changes the installed tree), it still needs a new
  package version. Take the next PBS rebuild of the same patch, or move to a new
  patch, in the same release.

## Which languages the pattern applies to

The pattern fits any language whose programs run on a **versioned interpreter or
virtual machine** that must match what the product was tested with:

- **Ruby**: a pinned Ruby build per patch, with gems installed into a
  per-product bundle.
- **Perl**: a pinned Perl per version, with modules installed into a
  per-product `local::lib`.
- **The JVM**: a pinned JDK or JRE per version, with the product's jars on top.

It does **not** apply to languages that compile to native binaries (Go, Rust,
C, C++). Their products carry no runtime to share: the binary is the package,
built by the `staged` builder (for example `cmake --install` into the staging
root) and checked by the same glibc floor guard.
