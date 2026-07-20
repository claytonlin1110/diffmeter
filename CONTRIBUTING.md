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
  `_find_moved_lines` in `scorer.py` layers on same-file, exact-content
  move detection to catch pure reordering, but that's a content-matching
  heuristic, not a real tree-diff. It won't catch a moved block whose
  formatting also changed, or a move across two files. A real tree-diff
  (matching AST nodes across the edit, e.g. GumTree-style) would handle
  both, at the cost of meaningfully more complexity. Worth doing once the
  simpler approach's limits are actually felt in practice, not before.
- **Per-language weighting.** Right now every substantive line counts
  equally regardless of language. Some scoring use cases may want to
  weight, e.g., generated files or config-language changes differently
  from application code. No design for this yet — open an issue if you
  have a concrete use case before sending a PR.
