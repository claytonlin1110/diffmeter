# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.9.0] - 2026-07-28

### Added

- Objective, automated merge/close criteria for PRs to this repo (see
  CONTRIBUTING.md's new "Objective merge criteria" section for the full
  policy). Previously every PR was merged on a maintainer's read of it, like
  any ordinary OSS project; now merging/closing is driven entirely by fixed
  checks:
  - A coverage floor (`[tool.coverage.report] fail_under = 92` in
    `pyproject.toml`, a few points under the measured 94% baseline as
    cross-OS headroom, not a target to coast at), enforced via `pytest --cov`
    in the existing `test` matrix job.
  - `diffmeter score --min-score 30` run against each PR's own diff, using
    diffmeter built from that PR's own code -- a new `diffmeter-self-score`
    CI job, dogfooding the exact tool this repo ships to gate its own PRs.
  - A new `required-checks` aggregator job so branch protection only needs
    to require one named check instead of every individual matrix cell.
  - A new `pr-policy.yml` workflow: `enable-auto-merge` turns on GitHub's
    native auto-merge for a PR as soon as it's opened (the actual merge
    still waits for every required check to go green -- this only sets the
    flag, it never runs PR code with elevated privileges); `close-stale-prs`
    closes a PR automatically 10 days after it opens if it never passed
    (7 days to go stale, 3 more grace).

### Notes

- Fork PRs get a read-only `GITHUB_TOKEN` from GitHub by design, so
  `enable-auto-merge` can't set the auto-merge flag on them -- only a
  maintainer running `gh pr merge --auto` by hand, or a future bot with its
  own PAT, can. Documented as a known limitation in CONTRIBUTING.md, not
  something worth routing around: doing so would mean letting an untrusted
  PR grant itself merge rights, which is exactly the vulnerability class
  GitHub's token scoping exists to prevent.
- This only ships the mechanism as code (workflow files, coverage config,
  policy docs). Actually requiring `required-checks` via branch protection
  and enabling repo-level auto-merge are live GitHub settings changes, not
  something in this diff -- those need a deliberate decision, not a side
  effect of a code change, especially since they'd also end this project's
  established direct-push-to-main workflow for anything going through a PR.

## [0.8.1] - 2026-07-28

### Fixed

- `--min-score` treated a diff with nothing scoreable -- `overall_score is
  None`, meaning either an empty diff or every changed file excluded via
  `--ignore`/`--weight` -- the same as a failing score, exiting 1 with
  "overall score None is below --min-score X". This directly undermined
  `--ignore`'s whole purpose as a CI gate: a PR that only touched an
  excluded path (a lockfile, vendored code) would fail the gate for being
  "too trivial" instead of correctly having nothing to penalize. A plain
  `diffmeter score --min-score 50` against zero uncommitted changes failed
  the exact same way. `overall_score is None` now skips the gate (exit 0)
  instead of failing it.
- Caught by CI itself, not a local test: the 0.7.1 release added a
  `test-action` step asserting that excluding a trivial file via `ignore`
  lets a `min-score` gate pass, and that step failed on push -- the gate
  was failing on the *ignored* file's missing score, not on any real
  triviality. Two regression tests now cover it directly (an empty diff,
  and a diff where the only changed file is ignored) rather than relying
  on CI to catch a repeat.

## [0.8.0] - 2026-07-28

### Added

- `score_diff()` now accepts the same `matcher`/`weight_matchers` params
  `score_pull_request()` already had, built via `diffmeter.build_matcher` /
  `diffmeter.build_weight_matchers`. Previously the only public multi-file
  entry point without ignore/weight support was `score_diff` itself --
  the CLI's local-scoring path and `score_pull_request` both had it (via
  private scorer internals the CLI and github_pr.py each called directly),
  but a library caller going through the documented top-level `score_diff`
  API had no way to reach either feature short of doing the same thing.

### Notes

- Unlike the CLI's local-scoring path and `score_pull_request` -- both of
  which check a path against `matcher` *before* reading that file's
  content, so an ignored file's bytes are never fetched -- `score_diff`
  callers have necessarily already read `base_content`/`head_content` to
  build each pair before calling it. Passing `matcher` here still skips the
  parsing/diffing work for a matched file, just not the read that already
  happened to hand its bytes to this function; documented on `score_diff`
  itself rather than left to be discovered as a surprise.

## [0.7.1] - 2026-07-28

### Fixed

- The composite GitHub Action (`action.yml`) had no way to pass `--ignore`
  or `--weight` through to `diffmeter score` -- both shipped as CLI/library
  features back in 0.4.0 and 0.6.0 and are documented in the README, but the
  Action itself was never updated to expose them, so anyone using the
  documented CI integration path had no way to reach either feature short of
  dropping the Action for a raw `pip install` + CLI invocation. Added
  `ignore`, `weight`, and `jobs` inputs; `ignore`/`weight` take one
  gitignore-style pattern (or `PATTERN=NUMBER` pair, for weight) per line,
  same as `.diffmeter.toml`'s list/table syntax.
- The `test-action` CI job (added in 0.6.1 to actually exercise `action.yml`,
  not just validate its YAML) now covers `ignore` too: a trivial
  comment-only commit to a file matched by an `ignore` pattern must pass a
  `min-score` gate that a matching *unignored* file already failed a step
  earlier in the same job -- so the assertion is that exclusion, not
  coincidence, is what made it pass.

### Notes

- The new `ignore`/`weight` inputs are newline-separated lists read into the
  composite Action's script via `env:` variables and a `while read` loop,
  not interpolated directly into the shell script the way the pre-existing
  `path`/`base`/`head`/`min-score` inputs are -- a pattern containing shell
  metacharacters would otherwise be run as script rather than treated as
  data. Worth revisiting whether the older inputs should move to the same
  approach, but that's a pre-existing pattern this change didn't introduce,
  so left alone for now rather than expanding this PR's scope.

## [0.7.0] - 2026-07-21

### Added

- Cross-file move detection: extracting a function to a new file (or
  moving code between any two files in the same diff) is now recognized
  as **moved** rather than scored as 100% new content in one file and
  100% deleted in the other. Previously move detection only matched
  within a single file. Verified end-to-end via the CLI on a real
  extract-to-new-file scenario before writing any tests.

### Changed

- Scoring now runs in two phases internally: `_prepare_file` classifies
  one file's changed lines (language detection, line-diffing, AST
  classification) independently of every other file -- still the
  parallelizable, CPU-heavy part -- and a single `_finalize_diff` pass
  then pools every file's candidate lines and matches moves across the
  *whole* diff at once. `score_diff` and `score_pull_request` both use
  this now. `score_file` (scoring one file alone) is unchanged in
  behavior and still same-file-only, since there's nothing else to pool
  against when only one file is in play -- confirmed by the full existing
  test suite passing unmodified against the new implementation.

### Notes

- Real trade-off that comes with widening the matching pool: two
  *unrelated* files that happen to share an identical line at least 8
  characters long -- one adding it, the other removing it -- now read as
  a move. Documented in the README's limitations section and pinned down
  with a dedicated test, not discovered later as a surprise bug report.
- Caught (before it shipped) that an *existing* PR-scoring test fixture
  reused `return 1` as filler content across three different fake files;
  once matching went global, that coincidental repetition tripped the
  exact trade-off above and failed the test. Fixed the fixture to use
  distinct content per file -- the fixture was the problem, not the new
  matching logic.
- Two of my own hand-computed expected values in new tests were wrong on
  the first pass (once for a partially-new file miscounted as fully
  moved, once for not knowing `moved` counts both sides of a match, so a
  single swapped line is `moved == 2` not `1`) -- verified actual output
  before fixing the assertions, same as every other arithmetic slip this
  project's tests have caught.

## [0.6.1] - 2026-07-21

### Fixed

- The composite GitHub Action (`action.yml`, shipped in 0.2.0 and
  documented in the README as the primary CI integration path) had never
  actually been run in a workflow -- only validated as syntactically
  correct YAML. Added a `test-action` CI job that exercises it for real
  via `uses: ./` against a synthetic trivial-then-substantive commit
  pair, asserting the `min-score` gate actually fails on the trivial one
  and passes on the substantive one.
- Caught a real bug in the test itself before it ever reached CI, via a
  local dry run in a throwaway clone: the first version used a `#`-prefixed
  line in README.md as the "trivial" comment-only change, but `#` is a
  Markdown *heading*, not a comment -- tree-sitter correctly scored it
  100% substantive, which would have made the test assert the opposite of
  what it meant to check. Fixed by using a throwaway `.py` file instead,
  where `#` really is a comment.

## [0.6.0] - 2026-07-21

### Added

- Per-path weighting: `--weight PATTERN=NUMBER` (repeatable, gitignore-style
  pattern; later ones win on a collision) or a `[weights]` table in
  `.diffmeter.toml`, e.g. down-weighting docs or test fixtures relative to
  application code instead of excluding them outright. New `weight` field
  on `FileScore` (default 1.0, shown in `--json` output and, when any file
  has a non-default weight, as a WEIGHT column in the table view).
  `DiffScore.overall_score` is now a weighted average across files;
  `changed_total`/`changed_trivial` stay unweighted raw counts for
  transparency, and a file's own `score` is unaffected by its weight --
  weight only controls how much that file's result counts toward the
  aggregate. Closes the "per-language weighting" item that had been on the
  roadmap since 0.1.0 (turned out more useful scoped to path patterns than
  strictly to language, since it lets you weight a specific directory).
- `score_file()`, `score_pull_request()`, `DiffmeterConfig` all gained a
  `weight`/`weights` parameter; new `diffmeter.config` functions
  `build_weight_matchers`, `resolve_weight`, `parse_weight_flag`, all
  re-exported from the top-level package.

### Notes

- CLI weight overrides always win over `.diffmeter.toml` on a pattern
  collision, by construction (config patterns are matched first, CLI
  patterns appended after) -- not by a dict merge, which would not
  actually guarantee that (Python's `{**a, **b}` keeps a colliding key's
  *position* from `a`, which could let an unrelated later pattern in `a`
  still win). Covered by a dedicated test.
- Cross-file move detection (extracting a function to a new file currently
  scores as 100% new on both sides rather than being recognized as a
  move) is still open -- see CONTRIBUTING.md's roadmap. It's a real gap,
  but fixing it means moving move-matching from the per-file level up to
  the whole-diff level, which is more architecture change than fits
  alongside this release.

## [0.5.1] - 2026-07-21

### Fixed

- `--pr` mode now retries transient GitHub failures with backoff instead
  of failing the whole scan on one blip: connection errors, 5xx server
  errors, and GitHub's *secondary* rate limit (a 403 carrying a
  `Retry-After` header, which is honored exactly, capped at 60s so a large
  value can't stall a job for an hour). Motivated by watching this
  project's own CI hit a transient 429 downloading a GitHub Action a few
  runs ago -- transient failures against GitHub's infrastructure are a
  real, observed thing, not a hypothetical.
- Deliberately does *not* retry a 404 (won't fix itself), a 401 (auth
  failure), or a 403 with no `Retry-After` -- that last one is GitHub's
  *primary* rate limit, meaning the quota is genuinely exhausted, and
  retrying immediately would just fail again; the existing GITHUB_TOKEN
  hint is the real fix for that case.

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
