# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/)
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.3] - 2026-10-23

### Chores

- repo hook
- add the report-only dependabot.yml (#33)

### Features

- move the runtime series to CPython 3.15 (#35)

## [1.0.2] - 2026-10-21

### CI

- run the update-deps hook tests via scripts/bin/validate-custom (#19)

### Chores

- repo hook
- turn off Claude Code co-author attribution (#24)

### Documentation

- describe the dependency-update hook and stop duplicating the runtime pin (#20)
- list scripts/bin/validate-custom in the repository layout (#22)

### Features

- publish one runtime package per Python series (#27)

## [1.0.1] - 2026-10-16

### Documentation

- update release status and document the release flow (#10)

### Features

- add the dependency-update hook for the PBS runtime pin (#12)

## [1.0.0] - 2026-10-07

### Bug fixes

- use secrets: inherit so package-sign receives the environment secrets (#4) (#5)

### Chores

- step 3 - vergil.toml
- step 4 - config files
- step 6 - CI/CD workflows

### Features

- vergil-python runtime package (pinned PBS CPython 3.14.8) (#3)
