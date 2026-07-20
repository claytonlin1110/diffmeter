# diffmeter

**How much of this diff is actually code?**

`diffmeter` scores a git diff by parsing the before/after AST of every
changed file (via [tree-sitter](https://tree-sitter.github.io/tree-sitter/))
and classifying each changed line as **substantive** (real logic) or
**trivial** (a comment, a docstring, or a blank line). It reports a 0-100
substance score per file and for the diff as a whole.

```
$ diffmeter score
FILE                                          LANG               +/-   SCORE
----------------------------------------------------------------------------
math_utils.py                                 python           +1/-0     0.0
----------------------------------------------------------------------------
Overall substance score: 0.0/100
```

That's a real example: the only change was adding a one-line comment above
a function. diffmeter says so, instead of a plain `git diff --stat` telling
you "1 file changed, 1 insertion(+)" as if it were equivalent to a genuine
change.

## Why

Line/file diff stats can't tell a real contribution from a drive-by edit —
a one-line comment tweak and a one-line bug fix look identical to `git diff
--stat`. That distinction matters in a few places:

- **Reviewers and maintainers** triaging a pile of pull requests, trying to
  tell which ones need careful review versus a quick glance.
- **Contribution-scoring systems** (several projects, including some
  Bittensor subnets, pay out rewards for merged PRs) that want to weight
  real work over trivial edits without hand-reviewing every diff.
- **CI gates** that want to flag "this PR only touches comments/formatting"
  automatically, e.g. to catch low-effort or bot-generated PRs before a
  human reviewer sees them.

diffmeter doesn't try to judge code *quality* — it only measures whether a
change touches real logic at all, using the same kind of AST-level analysis
a human reviewer does instinctively ("is this a real change or just a
comment?"), instead of trusting line counts.

**diffmeter is an independent, community-built tool.** It is not affiliated
with, endorsed by, or a product of any specific Bittensor subnet — it's a
general-purpose diff-quality tool that happens to be useful there too.

## Install

```
pip install diffmeter
```

Or from source:

```
git clone https://github.com/claytonlin1110/diffmeter.git
cd diffmeter
pip install -e .
```

Requires Python 3.9+. The only real dependencies are
[`tree-sitter-language-pack`](https://pypi.org/project/tree-sitter-language-pack/)
(precompiled grammars for 300+ languages, no compiler needed at install
time) and `click`.

## Usage

Score your uncommitted changes against `HEAD` (the common case — run it
before opening a PR):

```
diffmeter score
```

Score a specific commit range:

```
diffmeter score --base main --head feature-branch
diffmeter score --base HEAD~3 --head HEAD
```

Score a different repo without `cd`-ing into it:

```
diffmeter score /path/to/other/repo
```

Machine-readable output for scripting:

```
diffmeter score --json
```

Gate a CI job on it — fail the build if a PR is (say) more than 70% trivial:

```
diffmeter score --base origin/main --min-score 30
```

```yaml
# .github/workflows/diffmeter.yml
- name: Check diff substance
  run: |
    pip install diffmeter
    diffmeter score --base origin/${{ github.base_ref }} --min-score 30
```

### As a library

```python
from diffmeter import score_diff

pairs = [
    ("app.py", old_bytes, new_bytes),   # (path, base_content, head_content)
]
result = score_diff(pairs)
print(result.overall_score)   # 0-100, or None if nothing changed
for f in result.files:
    print(f.path, f.language, f.score)
```

## How it works

For each changed file, diffmeter:

1. Diffs the old and new content line-by-line (`difflib`) to find which
   lines were added (in the new file) and removed (from the old file).
2. Parses both sides with the matching tree-sitter grammar.
3. For each changed line, finds the smallest AST node covering it. If that
   node — or any of its ancestors — is a comment node, the line is
   **trivial**. Blank lines are also trivial. Everything else is
   **substantive**.
4. Score = substantive changed lines ÷ total changed lines, as a percentage.

Deletions count the same as additions — deleting a real function is a
substantive change; deleting a stale comment isn't.

Files with no available grammar (uncommon languages, or files tree-sitter
doesn't recognize) fall back to a best-effort heuristic that treats lines
starting with a common comment marker (`#`, `//`, `--`, `;`, `%`) as
trivial. Binary files are detected and excluded from scoring entirely.
This falls back gracefully, but it's still a heuristic — not a substitute
for an actual grammar, and it can't see block comments or inline comments.
Currently-supported languages with full AST-based scoring include Python,
JavaScript/TypeScript, Go, Rust, Java, C/C++, C#, Ruby, PHP, Bash, and
~300 others bundled by `tree-sitter-language-pack`.

## Limitations

- This measures *substance*, not *quality*. A confusing, buggy, or
  over-engineered change scores just as high as a clean one — diffmeter
  only tells you a change touches real logic, not that the logic is good.
  Line-count-based scoring can also be gamed by padding a change with
  verbose-but-real code; diffmeter is a signal, not a substitute for
  review.
- Line-based diffing (not a proper tree diff) means a change that
  reformats a block heavily can look more "substantive" than it really is
  if reformatting shifts code across many lines. This is a known
  trade-off for keeping the tool fast and dependency-light; a full
  structural diff is on the roadmap (see [CONTRIBUTING.md](CONTRIBUTING.md)).

## License

MIT — see [LICENSE](LICENSE).
