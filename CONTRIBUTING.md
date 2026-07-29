# Contributing to diffmeter

Thanks for considering a contribution. This is a small, young project, so
there's plenty of room for real improvements — and plenty of ways a
drive-by PR can accidentally make things worse. This doc is here to make
both easier to tell apart.

## Setup

```
git clone https://github.com/claytonlin1110/diffmeter.git
cd diffmeter
python -m venv .venv
.venv/Scripts/activate   # .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"
```

## Running tests

```
pytest
```

Every change should come with tests. If you're fixing a bug, add a test
that fails without your fix (see `test_scorer.py` for how the scorer's
tests are structured — they assert exact line-classification counts, not
just a final score, so failures are easy to diagnose).

If you're touching `scorer.py`'s line-classification logic, run it against
a few real snippets by hand first — see the verification approach in the
git history of `src/diffmeter/scorer.py` (an ancestor-walk bug in comment
detection for Rust/Java was caught exactly this way, since the smallest
AST node at a point can be an anonymous token nested inside the comment
node, not the comment node itself).

## Adding language support

Language detection and parser access both go through
`tree-sitter-language-pack`; `src/diffmeter/languages.py` is the only file
that talks to it directly. If a language parses but comments aren't being
classified as trivial, it's almost always because the comment's leaf node
in that grammar isn't itself named `*comment*` — check the ancestor chain
(see `_classify_lines` in `scorer.py`) rather than adding a per-language
special case.

## Reporting bugs / requesting features

Use the issue templates. For scoring bugs, include the minimal before/after
snippet that reproduces the misclassification — that turns directly into a
test case.

## Pull requests

- Keep PRs focused. A PR that fixes one thing is easy to review and merge;
  a PR that fixes one thing and also reformats three unrelated files is not.
- Update the README if you change user-facing behavior (CLI flags, output
  format, supported languages).
- `pytest` should pass locally before you open the PR — CI runs it too, but
  catching it locally is faster for everyone.

## Objective merge criteria

A PR merges or closes based on fixed, automated checks — not a maintainer's
read of it. Every check below runs in `.github/workflows/ci.yml` /
`pr-policy.yml` against the PR's own code, not a prior release:

- **Tests pass** across the full OS/Python matrix (`test` job).
- **Coverage doesn't drop below the floor** set in `pyproject.toml`'s
  `[tool.coverage.report] fail_under` (currently 92%, a few points under the
  94% baseline it was set from, as headroom for cross-OS variance rather
  than a target to coast at).
- **diffmeter's own `--min-score` gate**, run against the PR's diff using
  diffmeter built from that PR's own code (`diffmeter-self-score` job,
  threshold 30 — the same number the README's own CI-gate example uses).
- The composite Action (`test-action` job) still actually runs, not just
  parses as valid YAML.

Once every job above succeeds, `pr-policy.yml` enables GitHub's native
auto-merge on the PR — it doesn't merge immediately, it just tells GitHub to
merge once required status checks are green, which is what actually
performs the merge.

The reject side is symmetric and immediate, not a grace period: if
`required-checks` concludes with a failure, `close-on-failure`
(`ci.yml`) closes the PR and comments why, right then — no chance to push a
follow-up fix to the same PR, since there's no maintainer discretion to
appeal to on either side of this policy. Open a new PR once it's fixed.
`close-stale-prs` (`pr-policy.yml`, daily) is the backstop for what
`close-on-failure` can't catch: a check that never reaches a conclusive
result (stuck, cancelled, infra flake) closes after 7 days stale + 3 days
grace instead of sitting open indefinitely.

**A known limitation, not a design choice being hidden:** GitHub gives a PR
opened from a fork a read-only `GITHUB_TOKEN` by design, so neither
`enable-auto-merge` nor `close-on-failure` can act on a fork PR — only a
maintainer running `gh pr merge --auto` / `gh pr close` by hand (or a future
bot with its own PAT) can; a failing fork PR falls through to the 10-day
stale close instead. This isn't a workaround-able gap: letting an untrusted
PR grant itself merge (or immunity from closing) is exactly the
vulnerability class GitHub's token scoping exists to prevent, so this
project isn't going to try to route around it.

## Roadmap / known gaps

Noted here so effort isn't duplicated:

- **Full structural (AST tree) diffing.** The scorer line-diffs old vs.
  new content (`difflib`) and classifies each changed line independently;
  `_find_moved_lines_global` in `scorer.py` layers on exact-content move
  detection (reordering, and now cross-file moves too -- see below) to
  approximate this, but that's a content-matching heuristic, not a real
  tree-diff. It won't catch a moved block whose formatting also changed
  (e.g. reflowing a call across lines). A real tree-diff (matching AST
  nodes across the edit, e.g. GumTree-style) would handle that, at the
  cost of meaningfully more complexity. Worth doing once the simpler
  approach's limits are actually felt in practice, not before.
- ~~Cross-file move detection~~ — done: scoring now happens in two phases
  (`_prepare_file` per file, independent and parallelizable, then one
  `_finalize_diff` pass across every file's results), so a function
  extracted to a new file is recognized as moved on both sides instead of
  scoring as 100% new + 100% deleted. `score_file` on a single file still
  can't detect this (nothing else to pool against by definition); use
  `score_diff`/`score_pull_request` for multi-file diffs. Real trade-off
  that came with widening the matching pool: two *unrelated* files that
  happen to share an identical long-enough line, one adding it and the
  other removing it, now read as a move -- documented in the README's
  limitations section, not silently swept under the rug.
- ~~Per-language weighting~~ — done: see `--weight` / the `.diffmeter.toml`
  `[weights]` table (README has usage). It's actually per-*path-pattern*
  weighting rather than per-language, which turned out more flexible (lets
  you weight a specific directory, not just a whole language) — if a
  genuinely per-language axis turns out to be needed on top of that, open
  an issue with the concrete use case.
