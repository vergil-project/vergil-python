# vergil-python

The repo builds one runtime package per listed series. The pins
(`runtime.toml` `[[series]]` and `vergil.toml` `[[package]]`) are moved by the
upstream-refresh hook `scripts/dev/upstream-refresh.sh`, which
`vrg-upstream-refresh` runs.

- Every `runtime.toml` series must have exactly one matching `vergil.toml`
  `[[package]]` entry; `packaging/build.sh` fails the build if they disagree.
- The hook rewrites `runtime.toml` whole, header included: it writes
  `RUNTIME_HEADER` from `scripts/dev/upstream_refresh.py`. Change header comments
  there as well; comments between entries are lost.
- The full pattern: `docs/runtime-package-pattern.md`.

<!-- vergil:template:claude-md:begin -->
## Memory

Never write, edit or delete memory files without explicit human approval.
Propose the entry and where it belongs first, then wait for the decision.

## Worktrees

- Sessions always start at the project root, never inside `.worktrees/`.
- One worktree per issue, `.worktrees/issue-<N>-<slug>/` on `feature/<N>-<slug>`, or per batch,
  `.worktrees/issue-<N>-batch-<slug>/` on `feature/<N>-batch-<slug>` (`<N>` its first issue). Remove it once its branch lands.
- The main worktree is read-only, bar ignored `build/` scratch; every edit goes through a worktree.
- Read, Edit and Write use the worktree's absolute path. Bash commands `cd`
  into the worktree first or use absolute paths.
- The full convention: <https://github.com/vergil-project/vergil-tooling/blob/develop/docs/specs/worktree-convention.md>.
- Brief each parallel sub-agent with its [Agent prompt contract](https://github.com/vergil-project/vergil-tooling/blob/develop/docs/specs/worktree-convention.md#agent-prompt-contract).

## Git and GitHub

Use `vrg-git` and `vrg-gh` only; the hook guard blocks raw `git` and `gh`. If a
command is not available through the wrappers, explain why and ask the human to
run it with `! <command>` in the prompt.

## Validation

- Every linter, formatter and test runs through `vrg-container-run`, never directly on the host.
- Running one tool ad hoc while working is fine: one test file, or `vrg-container-run -- vrg-validate --markdown --fix <file>`, which lints markdown as the gate does (a bare `markdownlint` does not).
- Work is finished only when a full `vrg-container-run -- vrg-validate` passes; it is the gate before handoff.

## PR handoff

- After the merge review, record the PR with `vrg-pr-workflow report-ready
  --issue <N> --title --summary --notes`. It pushes the branch itself. Leave
  `--linkage` at its default (`Ref`).
- Agents submit and finalize their own PRs into `develop` through the tools,
  never with `--release`: `vrg-submit-pr` runs the gates, opens the PR with
  auto-merge on and finalizes it; after a fix, rerun `vrg-finalize-pr`.
- Releases and promotion to `main` are human actions.
- From report-ready until the agent's own submit the branch is frozen: no new commits and no
  push that advances it; more work is a new follow-up issue. Re-running report-ready to fix the
  prose is allowed. Submitting with `vrg-submit-pr` lifts it, as does `vrg-pr-workflow unfreeze`.
- The report-ready title, summary and notes are public (the relay ref is
  world-readable): never put secrets in them.

## Local settings

`.claude/settings.local.json` must stay absent or exactly `{}`; the agent tools
refuse otherwise. See the [ownership model](https://github.com/vergil-project/vergil-tooling/blob/develop/docs/site/docs/guides/claude-config-ownership.md).

## Engineering principles

- Correctness over cost over speed. Never ship known-broken code with a follow-up fix; if something is wrong, fix it now.
- No silent failures: no swallowed exceptions, no `except: return ""`, no fallbacks that hide errors. A failure hidden in code becomes a hallucination at the AI layer.
- Prefer a tool's machine-readable output (`--json`, exact `--format` strings, NUL-separated `-z` records, HTTP status and exit codes) over parsing text meant for a person; where there is none, pin the locale, anchor the match and fail loudly on anything unexpected. This applies to new and touched code, not as a sweep.
  A switch is rarely just adding `--json`: one tool can report errors in several shapes, values can change case or format between interfaces, exit codes can carry meaning, and a stricter parse can change what callers rely on.
  Test each switch with the input the old parse misread, and check that ordinary input behaves as before.

## paad

paad usage policy and deviations:
<https://github.com/vergil-project/vergil-claude-plugin/blob/develop/docs/paad-policy.md>.
<!-- vergil:template:claude-md:end -->
