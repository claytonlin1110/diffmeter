# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] - 2026-07-20

### Added

- `diffmeter score --pr owner/repo#123` (or a full PR URL): scores a GitHub
  pull request directly via the GitHub API, with no local clone required.
  Honors `GITHUB_TOKEN`/`GH_TOKEN` to avoid the low unauthenticated rate
  limit. Also available as a library function, `score_pull_request`.
- A composite GitHub Action (`action.yml`) so other repositories can add
  diffmeter as a CI check in one step: `uses: claytonlin1110/diffmeter@v0.2.0`.

### Fixed

- `previous_filename` handling for the PR-scoring code path: GitHub's API
  returns this key as present-but-`null` for non-renamed files, not
  omitted, so `dict.get(key, default)` was silently passing `None` through
  instead of falling back — meaning ordinary (non-renamed) modified files
  in a PR would have crashed. Caught before release by checking the real
  API response shape, not just mocked tests.

## [0.1.0] - 2026-07-20

Initial release.

### Added

- `diffmeter score`: scores a diff (uncommitted changes by default, or any
  two revisions via `--base`/`--head`) by classifying each changed line as
  substantive or trivial (comment/blank), using tree-sitter ASTs.
- `--json` output and `--min-score` CI-gate flag.
- Python library API: `diffmeter.score_diff`, `diffmeter.score_file`.
- Support for ~300 languages via `tree-sitter-language-pack`, with a
  best-effort comment-prefix heuristic fallback for unrecognized file types.
- Binary file detection (excluded from scoring).
