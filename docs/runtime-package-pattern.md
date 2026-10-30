# The runtime-package pattern

This repository packages CPython runtimes as OS packages, **one package per
listed CPython series** (`vergil-python<X.Y>`, `.deb` and `.rpm`), that vergil
products depend on. This document explains why the runtime is packaged this
way, how the pins change, and when the same pattern applies to other languages.

Background: epic `vergil-project/.github#356`, spec §4.1, §6.2 and decision D7.
The per-series model comes from epic `vergil-project/.github#407` (the language
version lifecycle), spec §7. Building several series at once, the upstream
refresh and the release-suffixed version come from epic
`vergil-project/.github#472` (multi-series runtime), spec §5.3 to §5.5.

## Table of Contents

- [Why the runtime is its own package](#why-the-runtime-is-its-own-package)
- [One package per series, side by side](#one-package-per-series-side-by-side)
- [The patch and the PBS build float within each series](#the-patch-and-the-pbs-build-float-within-each-series)
- [The package version](#the-package-version)
- [Never on PATH, never pip-installable](#never-on-path-never-pip-installable)
- [Provenance and the build](#provenance-and-the-build)
- [Changing the pins](#changing-the-pins)
- [Adding and removing a series](#adding-and-removing-a-series)
- [Releasing](#releasing)
- [Every release publishes every series](#every-release-publishes-every-series)
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
Products declare `Depends: vergil-python3.15 (>= <tested version>)` (deb) or
`Requires: vergil-python3.15 >= <tested version>` (rpm).

## One package per series, side by side

The CPython **series** (`X.Y`) is part of the package name; the patch is not:
`vergil-python3.15` installs to `/opt/vergil/python/3.15/`. A different series
is a different package, installed alongside at its own path, so
`vergil-python3.14` and `vergil-python3.15` coexist on one host, and moving one
product to a new series never disturbs another.

This repository builds **every listed series**. The list is `runtime.toml`'s
`[[series]]` entries, currently 3.14 and 3.15, each with exactly one matching
`[[package]]` entry in `vergil.toml`. Every series is refreshed within itself
(see [Changing the pins](#changing-the-pins)), and every release publishes
every series (see
[Every release publishes every series](#every-release-publishes-every-series)).

Which series a product builds against depends on whether it pins one:

- **An unpinned product derives its runtime.** At each build, the vergil-tooling
  `python` builder picks the newest version in the vergil-containers version
  window whose `vergil-python` runtime is published (vergil-tooling#3455). So
  an unpinned product moves to a new series at its next build after that
  series is published, with no change of its own.
- **Only a product pinned to an exact patch chooses its own schedule.** A
  `[package.python].runtime` pin is an exact `x.y.z`, and the build checks that
  the installed runtime serves exactly that patch (vergil-tooling#3455).
  `vrg-update-deps` bumps the pin to newer patches within its series, but never
  moves it to another series (vergil-tooling#3456). Moving a pinned product to
  a new series is a change to its pin.

### Publishing a series feeds the version window

A product derives a series only once the window records that series' runtime
as published. The window's `runtime_published` flag for a version is set by
the vergil-containers lifecycle job: when a new `vergil-python<X.Y>` package
is published, the job opens a `runtime` PR in vergil-containers that sets the
flag (for example `vergil-project/vergil-containers#666`). Until that PR
merges, unpinned products keep building against the previous published series.

Directory ownership follows spec §6.3: each package owns every directory
strictly below `/opt/vergil`, never `/opt/vergil` itself. dpkg and rpm
reference-count shared directories such as `/opt/vergil/python`, so removing the
last runtime leaves nothing behind.

## The patch and the PBS build float within each series

The interpreter comes from
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
(PBS): relocatable CPython builds that need only glibc
2.17 or newer, which every supported target exceeds.

- **The CPython patch floats within its series.** A new patch (`3.15.1` to
  `3.15.2`) is the same package with a higher **version**, installed in place
  at the same path. CPython keeps the ABI stable within a series, so a
  product's venv keeps working across the upgrade.
- **The PBS build floats too.** PBS republishes the same CPython patch whenever
  a bundled library (OpenSSL, SQLite, ...) gets a fix. That rebuild is again
  the same package with a higher version.
- Each series floats on its own: different series may sit on different PBS
  builds.
- Products depend on `>=` the version they were tested with, so they pick up
  patch and library security fixes without a product rebuild.

## The package version

A runtime package's version carries its **inputs** (the CPython patch and the
PBS build) first, then the vergil-python **release** that built it:

```text
<cpython>+<pbs_tag>.<X.Y.Z>        for example  3.15.1+20261021.1.0.5
```

`vergil.toml` holds only the inputs, in each `[[package]]` entry's
`upstream-version` (`"3.15.1+20261021"`), which is all the upstream-refresh hook
ever writes. vergil-tooling joins it with the repository `VERSION` to form the
full version, in one place (`naming.package_version`), for the build, the
`check-unpublished` identities and the release's index wait. The hook never
touches `VERSION`.

The order is chosen so that dpkg and rpm both sort it correctly: a newer CPython
patch sorts higher, a newer PBS build of the same patch sorts higher, and at the
same inputs a newer vergil-python release sorts higher. A release-suffixed
version also sorts above the older `<cpython>+<pbs_tag>` versions already in
the index, so hosts upgrade. `apt policy` and `dnf info` show the installed
CPython and PBS build directly. dpkg and rpm compare the numeric parts
numerically, so `3.15.10+<tag>` sorts after `3.15.9+<tag>`.

## Never on PATH, never pip-installable

- **Nothing goes in `/usr/bin`.** The runtime is reachable only at
  `/opt/vergil/python/<X.Y>/bin/python<X.Y>`. It can never shadow the
  system `python3` that the OS and Ansible rely on.
- **The PEP 668 `EXTERNALLY-MANAGED` marker** is written into
  `lib/python<X.Y>/`. `pip install` into the shared runtime is refused; products
  install into their own venv instead.

## Provenance and the build

`runtime.toml` holds the pins, one `[[series]]` entry per listed series:

| Key | Meaning |
|---|---|
| `series` | The CPython series, e.g. `3.15`. It names the package and its install root. |
| `cpython` | The CPython patch built for the series, e.g. `3.15.1`. It must be a patch of `series`. |
| `pbs_tag` | The PBS release tag (`YYYYMMDD`) the archive comes from. |
| `sha256.x86_64` / `sha256.aarch64` | SHA-256 of each architecture's `install_only_stripped` archive, copied from that release's `SHA256SUMS` asset. |

Each series has exactly one `[[package]]` entry in `vergil.toml`, with `name`
`vergil-python<series>`, `upstream-version` `<cpython>+<pbs_tag>`, a `summary`
naming the pin, and a `smoke` command running
`/opt/vergil/python/<series>/bin/python<series>`.

Each package is built by the vergil-tooling `staged` builder (spec §6.6), which
runs `packaging/build.sh` in each of that package's build cells with
`VRG_STAGING_ROOT`, `VRG_TARGET_ARCH`, `VRG_PACKAGE_NAME` and
`VRG_PACKAGE_VERSION` set. The script:

1. checks that the two files agree one-to-one: every series is an `X.Y` with
   `cpython` an `X.Y.Z` patch of it, and every series has exactly one package
   named `vergil-python<series>`, and every package exactly one series. A
   series in one file but not the other fails every build;
2. selects the series named by `VRG_PACKAGE_NAME`, and fails if no listed
   series has that name;
3. checks that the selected package's `upstream-version` is
   `<cpython>+<pbs_tag>`, that `VRG_PACKAGE_VERSION` is
   `<cpython>+<pbs_tag>.<X.Y.Z>`, and that its `smoke` runs the series
   interpreter, so the two files and the computed version cannot drift;
4. maps `VRG_TARGET_ARCH` (`amd64` or `arm64`) to the PBS triple
   (`x86_64-unknown-linux-gnu` or `aarch64-unknown-linux-gnu`);
5. downloads
   `cpython-<cpython>+<pbs_tag>-<triple>-install_only_stripped.tar.gz` from the
   PBS release and fails unless its SHA-256 matches `runtime.toml`;
6. unpacks the archive's `python/` root into
   `$VRG_STAGING_ROOT/opt/vergil/python/<series>/`;
7. writes the `EXTERNALLY-MANAGED` marker.

The builder then applies the glibc floor guard to every ELF file in the tree and
hands it to nFPM. In PR CI, `package / evidence` builds every listed series and
installs each package on all eight targets, running its `smoke` command, which
imports `ssl`, `sqlite3`, `ctypes` and `zlib` with the packaged interpreter.

The offline tests for these checks, the install root and the build matrix are
`tests/test_build.py`, run by `tests/check-build.sh`. They serve a small fake
archive through a stand-in `curl`, so they never touch the network.

## Changing the pins

Every pin change is one reviewed PR. Normally the PR is opened for you.

### The upstream-refresh hook

`vergil.toml` names `scripts/dev/upstream-refresh.sh` as this repository's
`[upstream-refresh].hook`. `vrg-upstream-refresh` runs it on a clean checkout of
`develop` and keeps exactly one `upstream-refresh` PR in step with what it
changed; a human reviews and merges it. It is separate from the dependency
update: `vrg-release`'s `update-deps` stage never touches the series, and this
repository has no `[dependency-update].hook`. The hook runs
`scripts/dev/upstream_refresh.py`, which:

1. checks that `runtime.toml` and `vergil.toml` agree, as `packaging/build.sh`
   does, before any network access;
2. takes the newest python-build-standalone release (the target of
   `releases/latest`) and reads its `SHA256SUMS`;
3. for **each listed series**, takes the newest CPython patch **within that
   series** that the release ships as `install_only_stripped` for both
   `x86_64-unknown-linux-gnu` and `aarch64-unknown-linux-gnu`, and rewrites
   that series' `cpython`, `pbs_tag` and both `sha256` values in
   `runtime.toml`, and its package's `upstream-version` and `summary` in
   `vergil.toml`. It never changes `series`, a package `name` or a `smoke`
   path, which all name the series. A series already current changes nothing;
4. leaves a series the release no longer ships at its pin, with the note
   `vergil-python<X.Y>: no longer built upstream (latest PBS <tag> ships no
   <X.Y> patch); kept at <cpython>+<pbs_tag>`. It never searches older PBS
   releases (that needs the rate-limited GitHub API). This usually means the
   series is past end of life; it is not a failure and does not hold back the
   other series;
5. adds a **leading-edge** series from the version window (see
   [Adding and removing a series](#adding-and-removing-a-series)). It never
   back-fills an older series and never removes one;
6. writes a note for each series it moved or added, and for each window
   version it held back. Those notes become the refresh PR's `## Notes`.

The rewrite is all-or-nothing. The hook fails, before writing either file, if
the two files disagree, if the version window cannot be read or is not schema
1, if the newest PBS release is older than a series'
pinned tag, if it ships only an older patch of a series than the pinned one (a
downgrade), if `SHA256SUMS` lists two different checksums for one archive or a
different checksum for an unchanged pin, if a series' chosen patch is missing an
archive (or a readable checksum) for either architecture, or if either file
does not have the layout it expects. One series' failure stops the whole run, so
there is never a partial rewrite across series. The hook rewrites
`runtime.toml` whole, header included: it writes the `RUNTIME_HEADER` constant
from `scripts/dev/upstream_refresh.py`, not the file's existing header. Change
a header comment in `RUNTIME_HEADER` and in `runtime.toml` together; a comment
changed only in `runtime.toml`, or put between entries, is lost on the next
refresh. Its offline unit tests
are `tests/test_upstream_refresh.py`, run by `tests/check-upstream-refresh.sh`.

### By hand

Change a pin by hand when the hook cannot. Pick the newest PBS release that
ships the wanted patch as `install_only_stripped` for both
`x86_64-unknown-linux-gnu` and `aarch64-unknown-linux-gnu`, and take both
checksums from its `SHA256SUMS`. Never type a checksum by hand from anywhere
else. For a new patch or PBS rebuild of a series (an in-place upgrade of the
same package), change:

- that series' `runtime.toml` entry: `cpython` (for a new patch), `pbs_tag` and
  both `sha256` values;
- its `vergil.toml` `[[package]]`: `upstream-version` (`<cpython>+<pbs_tag>`)
  and `summary`.

## Adding and removing a series

- **Add (automatic).** A series is added by the upstream-refresh PR. The hook
  reads the version window: the `in_policy` versions of the `python` family in
  vergil-containers' `docker/versions/window.json`, at the ref in this
  repository's `vergil.toml` `[dependencies].vergil`. The `VRG_VERSION_WINDOW`
  environment variable, when set, names a local `window.json` to read instead.
  A window version is added only when it is **newer than the newest listed
  series** and the same PBS release the refresh uses ships it as
  `install_only_stripped` for both architectures. It is added at its newest
  patch, to both files together: a `[[series]]` entry in `runtime.toml`, and a
  `[[package]]` entry after the last one in `vergil.toml`, worded like the
  others. The note is `added series <X.Y> (CPython <cpython>, PBS <tag>)`.
  A window version that the release does not ship (or ships for only one
  architecture) is never added half-built. It gets the note
  `held back: series <X.Y> is in the window but PBS <tag> does not ship it`,
  and any newer window version waits for it, so it never becomes a back-fill.
  Window versions older than the newest listed series are never added, and a
  stable CPython minor that PBS ships but the window lacks is not added either.
  What puts a version in the window is vergil-containers adding it and
  releasing. Once the new package is published and the window records it (see
  [Publishing a series feeds the version window](#publishing-a-series-feeds-the-version-window)),
  unpinned products move to it at their next build.
- **Add (by hand).** Still possible: add the series to both files in one
  reviewed PR, a `[[series]]` entry in `runtime.toml` and a `[[package]]` entry
  in `vergil.toml` with `name` `vergil-python<series>`, `upstream-version`,
  `summary`, the series' interpreter path in `smoke`, and `[package.staged]
  build-command = "packaging/build.sh"`. Add a series only once
  vergil-containers has the version in its window. `packaging/build.sh` refuses
  an entry in one file without the other.
- **Remove.** Delete the series' `[[series]]` entry and its `[[package]]` entry
  in one reviewed PR. The series is then no longer built. Its packages already
  published stay in the index until retention ages them out, or for as long as
  a retained consumer depends on them.

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

## Every release publishes every series

Every release of this repository builds and publishes **every listed series**;
there is no "unchanged series" to skip. Because each package version ends in the
repository release (see [The package version](#the-package-version)), a release
always publishes new identities, even for a series whose inputs did not move.
So a release that changes only this repository's own code (for example a
`packaging/build.sh` fix) is safe: it never re-publishes an existing
`name`/`version` with different bytes, which the index treats as a hard error
(spec §7.2).

A merged refresh PR is published by the next normal `vrg-release`, which is
always a human action.

## Which languages the pattern applies to

The pattern fits any language whose programs run on a **versioned interpreter or
virtual machine** that must match what the product was tested with:

- **Perl**: a Perl per version, with modules installed into a per-product
  `local::lib`.
- **The JVM**: a JDK or JRE per major version, with the product's jars on top.

It does **not** apply to languages that compile to native binaries (Go, Rust,
C, C++). Their products carry no runtime to share: the binary is the package,
built by the `staged` builder (for example `cmake --install` into the staging
root) and checked by the same glibc floor guard.
