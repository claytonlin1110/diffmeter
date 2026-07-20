# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
