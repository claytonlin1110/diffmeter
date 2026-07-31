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

Passing every job above is **necessary but not sufficient** for auto-merge.
Objective checks can confirm a change is substantive and tested; they can't
confirm it's the *right* change, so `pr-policy.yml` also requires every
changed file to match the auto-merge allowlist:

- `tests/**`
- `*.md` (README, CONTRIBUTING, CHANGELOG)
- `.github/ISSUE_TEMPLATE/**`, `.github/PULL_REQUEST_TEMPLATE.md`

A PR touching anything else — `src/diffmeter/**` (the actual library/CLI),
`action.yml` (the external-facing composite Action contract),
`.github/workflows/**` (letting a PR modify its own gate and then have that
gate auto-merge it would be a real supply-chain hole), or `pyproject.toml`
(dependencies/packaging) — always needs a maintainer to click merge by
hand, no matter how cleanly its checks pass. `enable-auto-merge` comments
on an ineligible PR explaining this, once, rather than silently doing
nothing.

Eligibility is re-checked on every push, and a PR can flip from eligible to
not: it might start out touching only `tests/` (auto-merge armed on that
push), then a later commit adds a change under `src/diffmeter/`. Nothing
about GitHub's native auto-merge revokes itself when that happens, so
`enable-auto-merge` explicitly calls `gh pr merge --disable-auto` the
moment a PR is found ineligible -- without it, a PR could still merge
itself on a stale armed flag despite now touching code outside the
allowlist, which would have quietly defeated the entire point of
path-scoping.

Once every job above succeeds *and* the path check passes, `pr-policy.yml`
enables GitHub's native auto-merge on the PR — it doesn't merge
immediately, it just tells GitHub to merge once required status checks are
green, which is what actually performs the merge. This step runs as
`PR_AUTOMERGE_TOKEN`, a fine-grained PAT scoped to pull-requests
read/write on this repo only, not the workflow's default `GITHUB_TOKEN` —
confirmed the hard way, not assumed: `GITHUB_TOKEN` cannot call the
`enablePullRequestAutoMerge` GraphQL mutation at all, even with
`pull-requests: write` granted in the workflow and the repo's "Allow
GitHub Actions to create and approve pull requests" setting turned on.
GitHub restricts that specific mutation to a real user-associated token
regardless of what a workflow-default token is granted, so a PAT is the
only way to make this step genuinely unattended.

The reject side has no equivalent carve-out and applies uniformly regardless
of what a PR touches: if `required-checks` concludes with a failure,
`close-on-failure` (`ci.yml`) closes the PR and comments why, right
then — no chance to push a follow-up fix to the same PR, no maintainer
discretion to appeal to. Open a new PR once it's fixed. Failing checks are
an objective bar every PR must clear either way; the allowlist only affects
whether *passing* is enough to merge unattended, not whether *failing* gets
you a grace period.
This step *does* use `GITHUB_TOKEN` successfully — closing/commenting on a
PR isn't restricted the way auto-merge-enabling is. `close-stale-prs`
(`pr-policy.yml`, daily) is the backstop for what `close-on-failure` can't
catch: a check that never reaches a conclusive result (stuck, cancelled,
infra flake) closes after 7 days stale + 3 days grace instead of sitting
open indefinitely.

**A known limitation, not a design choice being hidden:** neither side of
this policy can act on a PR opened from a fork. `enable-auto-merge`
can't, because GitHub withholds every repo secret — not just
`GITHUB_TOKEN` — from a `pull_request`-triggered run opened from a fork,
so `PR_AUTOMERGE_TOKEN` is simply absent there. `close-on-failure` can't
either, for a related but distinct reason: `GITHUB_TOKEN` itself is
downgraded to read-only for a fork-triggered `pull_request` run,
regardless of the `permissions:` block a workflow declares, so `gh pr
close`/`gh pr comment` fail the same way `gh pr merge` would. A fork PR
falls through to a maintainer running the equivalent commands by hand, or
the 10-day stale close if no one does. Neither is a workaround-able gap:
letting an untrusted PR write to the repo, close itself, or grant itself
merge rights is exactly the vulnerability class GitHub's token/secret
scoping for forks exists to prevent, so this project isn't going to try to
route around it.

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
