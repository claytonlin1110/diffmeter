# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.5.0] - 2026-07-21

### Added

- Concurrent scoring: files are scored in parallel via a thread pool by
  default (`--jobs 8`, `-j`; `--jobs 1` disables it). Biggest win in `--pr`
  mode, where the cost is almost entirely network round-trips to GitHub --
  measured 25s -> 7s on a real 10-file PR (`pallets/click#3704`), with
  byte-identical output between sequential and concurrent runs. Local
  scoring is parallelized too (mostly `git show` subprocess overhead).
- `score_diff()` and `score_pull_request()` both take an optional
  `max_workers` parameter for library callers.

### Fixed

- Real thread-safety hazard, fixed *before* it could bite anyone: the
  tree-sitter `Parser` cache in `languages.py` was a single process-wide
  `@lru_cache`, meaning every thread would share and call `.parse()` on
  the *same* Parser object -- tree-sitter Parsers aren't documented as
  safe for concurrent use like that, which would have meant intermittent
  corruption or crashes once concurrency landed. Switched to a
  thread-local cache (confirmed the underlying library hands back a fresh
  Parser object per call, so this is both correct and still avoids
  reconstructing one per file). Locked in with a test that forces two
  distinct OS threads and asserts they get different Parser instances,
  rather than relying on timing to expose a race.

## [0.4.0] - 2026-07-20

### Added

- Ignore patterns: exclude paths from scoring entirely via `--ignore
  PATTERN` (repeatable, gitignore-style) or a `.diffmeter.toml` file
  (`ignore = [...]`) in the repo root, auto-loaded for local scoring.
  `--pr` mode has no local checkout to read a config file from, so it only
  honors `--ignore` passed explicitly. Excluded files still appear in
  output (`ignored: true`, `score: null`) instead of silently vanishing,
  and their blob content isn't even fetched in `--pr` mode.
- New `diffmeter.config` module: `load_config`, `build_matcher`,
  `is_ignored`, `DiffmeterConfig`, `ConfigError` -- all re-exported from
  the top-level package for library use.
- New dependencies: `pathspec` (gitignore-style matching) and `tomli` on
  Python < 3.11 (stdlib `tomllib` covers 3.11+).

## [0.3.0] - 2026-07-20

### Added

- Move detection: a line that looks substantive on its own is now checked
  against the rest of the same file's diff for an exact
  (whitespace-normalized) content match on the other side. A matched line
  is scored as moved rather than newly written, so a pure reorder of code
  now scores near 0 instead of the 100 it would have gotten from
  classifying each line independently. New `moved` field on `FileScore`/
  `DiffScore` and in the `--json` output; surfaced as a note in the table
  output too.
- Matching is restricted to lines of at least 8 characters (stripped) to
  avoid false-positive matches on short, common lines like `}` or
  `else:` — see `_MIN_MOVE_MATCH_CHARS` in `scorer.py`. This is a
  documented, deliberate trade-off (false negatives on short moved lines
  are preferred over false positives on coincidental short matches).

### Notes

- This is content-matching within a single file, not a full AST tree-diff:
  it doesn't catch cross-file moves or a moved block whose formatting also
  changed. A real tree-diff remains on the roadmap (see CONTRIBUTING.md).

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
