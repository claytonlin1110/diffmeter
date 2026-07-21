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

## Roadmap / known gaps

Noted here so effort isn't duplicated:

- **Full structural (AST tree) diffing.** The scorer line-diffs old vs.
  new content (`difflib`) and classifies each changed line independently;
  `_find_moved_lines` in `scorer.py` layers on exact-content move
  detection to catch pure reordering, but that's a content-matching
  heuristic, not a real tree-diff. It won't catch a moved block whose
  formatting also changed. A real tree-diff (matching AST nodes across the
  edit, e.g. GumTree-style) would handle that, at the cost of meaningfully
  more complexity. Worth doing once the simpler approach's limits are
  actually felt in practice, not before.
- **Cross-file move detection.** `_find_moved_lines` only matches lines
  within the *same* file's added/removed sets, so extracting a function to
  a new file scores as 100% new on both sides instead of being recognized
  as a move. Fixing this means moving the matching pass up from per-file
  `score_file` to the multi-file level (`score_diff`/`score_pull_request`)
  so it can pool candidate lines across all changed files at once --
  doable without a full tree-diff, but touches the scoring pipeline's
  architecture (all three per-file call sites) more than a purely additive
  change, so it deserves its own careful pass rather than being bolted on.
- ~~Per-language weighting~~ — done: see `--weight` / the `.diffmeter.toml`
  `[weights]` table (README has usage). It's actually per-*path-pattern*
  weighting rather than per-language, which turned out more flexible (lets
  you weight a specific directory, not just a whole language) — if a
  genuinely per-language axis turns out to be needed on top of that, open
  an issue with the concrete use case.
