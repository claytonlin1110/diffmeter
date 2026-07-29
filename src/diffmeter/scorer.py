"""Core scoring engine.

diffmeter classifies each changed line of a diff as SUBSTANTIVE, TRIVIAL
(comment-only), or BLANK by locating the smallest AST node that covers the
line (via tree-sitter) and checking whether that node is a comment node.
Lines with no available grammar fall back to a conservative regex heuristic.

The score is: (changed lines that are substantive) / (all changed lines),
computed separately for additions (checked against the new file's AST) and
deletions (checked against the old file's AST) so that deleting real logic
counts the same as adding it.

On top of that, lines that look substantive in isolation but are an exact
content match for a line removed elsewhere in the diff are treated as
*moved* rather than newly written -- this catches reordering (and moving
code to a different file, e.g. extracting a function) without needing a
full AST tree-diff. Matching happens across the *whole* diff, not just
within one file: scoring proceeds in two phases, a per-file `_prepare_file`
pass (parallelizable -- this is the CPU-heavy tree-sitter work) followed by
one `_finalize_diff` pass that pools every file's candidate lines and
matches content across all of them at once. See `_find_moved_lines_global`
for the matching rule and its limits (exact normalized match, a minimum
length to avoid matching on stray `}` or `else:` lines, and a real
trade-off: two files that happen to share an identical unrelated line, one
adding it and the other removing it, will be misread as a move).
"""

from __future__ import annotations

import difflib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional

import pathspec

from diffmeter.config import WeightMatchers, is_ignored, resolve_weight
from diffmeter.languages import FALLBACK_MARKERS, detect_language, get_parser

_BINARY_MARKER = b"\x00"
_BINARY_SNIFF_BYTES = 8192
_MIN_MOVE_MATCH_CHARS = 8


class Verdict(str, Enum):
    SUBSTANTIVE = "substantive"
    TRIVIAL = "trivial"
    BLANK = "blank"


@dataclass
class FileScore:
    path: str
    language: Optional[str]
    heuristic: bool
    binary: bool
    ignored: bool = False
    weight: float = 1.0
    added_total: int = 0
    added_trivial: int = 0
    removed_total: int = 0
    removed_trivial: int = 0
    moved: int = 0
    note: Optional[str] = None

    @property
    def changed_total(self) -> int:
        return self.added_total + self.removed_total

    @property
    def changed_trivial(self) -> int:
        return self.added_trivial + self.removed_trivial

    @property
    def score(self) -> Optional[float]:
        """0-100, or None if there's nothing to score (binary file, a file
        excluded via an ignore pattern, or no lines actually changed).

        This is the file's own substance ratio, unaffected by `weight` --
        weight only controls how much this file's result counts toward
        DiffScore.overall_score, not what "80% substantive" means for the
        file on its own.
        """
        if self.binary or self.ignored or self.changed_total == 0:
            return None
        substantive = self.changed_total - self.changed_trivial
        return round(100.0 * substantive / self.changed_total, 1)


@dataclass
class DiffScore:
    files: list[FileScore] = field(default_factory=list)

    @property
    def moved(self) -> int:
        return sum(f.moved for f in self.files)

    @property
    def changed_total(self) -> int:
        return sum(f.changed_total for f in self.files)

    @property
    def changed_trivial(self) -> int:
        return sum(f.changed_trivial for f in self.files)

    @property
    def overall_score(self) -> Optional[float]:
        """Weighted by each file's `weight` (see FileScore.weight, set from
        the .diffmeter.toml [weights] table): a file at weight 0.5 counts
        half as much toward this aggregate as one at the default weight of
        1.0. `changed_total`/`changed_trivial` above stay unweighted raw
        counts for transparency; only this aggregate applies weighting.
        With no weights configured (the default), this is identical to a
        plain unweighted score.
        """
        weighted_total = sum(f.weight * f.changed_total for f in self.files)
        if weighted_total == 0:
            return None
        weighted_trivial = sum(f.weight * f.changed_trivial for f in self.files)
        return round(100.0 * (weighted_total - weighted_trivial) / weighted_total, 1)


def _normalize(content: bytes) -> bytes:
    return content.replace(b"\r\n", b"\n")


def _classify_lines(content: bytes, language: Optional[str]) -> dict[int, Verdict]:
    content = _normalize(content)
    lines = content.splitlines()
    parser = get_parser(language) if language else None
    root = parser.parse(content).root_node if parser is not None else None

    result: dict[int, Verdict] = {}
    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped:
            result[idx + 1] = Verdict.BLANK
            continue
        if root is not None:
            col = len(raw_line) - len(raw_line.lstrip(b" \t"))
            node = root.descendant_for_point_range((idx, col), (idx, col + 1))
            is_comment = False
            while node is not None:
                if "comment" in node.type.lower():
                    is_comment = True
                    break
                node = node.parent
            result[idx + 1] = Verdict.TRIVIAL if is_comment else Verdict.SUBSTANTIVE
        else:
            text = stripped.decode("utf-8", errors="replace")
            is_comment = any(text.startswith(m) for m in FALLBACK_MARKERS.markers)
            result[idx + 1] = Verdict.TRIVIAL if is_comment else Verdict.SUBSTANTIVE
    return result


def _diff_line_numbers(
    base_lines: list[bytes], head_lines: list[bytes]
) -> tuple[set[int], set[int]]:
    """1-indexed line numbers touched by the diff: added lines are numbered
    in `head`, removed lines are numbered in `base`."""
    matcher = difflib.SequenceMatcher(a=base_lines, b=head_lines, autojunk=False)
    added: set[int] = set()
    removed: set[int] = set()
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag in ("replace", "insert"):
            added.update(range(j1 + 1, j2 + 1))
        if tag in ("replace", "delete"):
            removed.update(range(i1 + 1, i2 + 1))
    return added, removed


@dataclass
class _FilePrep:
    """Everything needed to finalize one file's FileScore, short of the
    move-detection pass -- which needs every file's preps at once, since
    matching now happens across the whole diff, not just within one file."""

    path: str
    language: Optional[str]
    heuristic: bool
    binary: bool
    ignored: bool
    weight: float
    notes: list[str] = field(default_factory=list)
    base_lines: list[bytes] = field(default_factory=list)
    head_lines: list[bytes] = field(default_factory=list)
    added: set[int] = field(default_factory=set)
    removed: set[int] = field(default_factory=set)
    base_verdicts: dict[int, Verdict] = field(default_factory=dict)
    head_verdicts: dict[int, Verdict] = field(default_factory=dict)


def _prepare_file(
    path: str,
    base_content: Optional[bytes],
    head_content: Optional[bytes],
    *,
    ignored: bool = False,
    weight: float = 1.0,
) -> _FilePrep:
    """Phase 1 of scoring one file: language/binary detection, line-level
    diffing, and AST classification -- everything that's independent of
    every *other* file in the diff, so callers can run this in parallel
    across files. Move detection is deliberately not done here; see
    `_finalize_diff`."""
    language = detect_language(path)
    heuristic = language is None or get_parser(language) is None

    if ignored:
        return _FilePrep(
            path=path,
            language=language,
            heuristic=heuristic,
            binary=False,
            ignored=True,
            weight=weight,
            notes=["matches a configured ignore pattern, excluded from scoring"],
        )

    sample = head_content if head_content is not None else base_content
    is_binary = sample is not None and _BINARY_MARKER in sample[:_BINARY_SNIFF_BYTES]
    if is_binary:
        return _FilePrep(
            path=path,
            language=language,
            heuristic=heuristic,
            binary=True,
            ignored=False,
            weight=weight,
            notes=["binary file, excluded from scoring"],
        )

    notes = []
    if heuristic:
        notes.append(
            f"no grammar for '{language}', used comment-prefix heuristic"
            if language
            else "unrecognized file type, used comment-prefix heuristic"
        )

    norm_base = _normalize(base_content) if base_content is not None else None
    norm_head = _normalize(head_content) if head_content is not None else None
    base_lines = norm_base.splitlines() if norm_base is not None else []
    head_lines = norm_head.splitlines() if norm_head is not None else []

    if norm_base is None:
        added, removed = set(range(1, len(head_lines) + 1)), set()
    elif norm_head is None:
        added, removed = set(), set(range(1, len(base_lines) + 1))
    else:
        added, removed = _diff_line_numbers(base_lines, head_lines)

    head_verdicts: dict[int, Verdict] = {}
    base_verdicts: dict[int, Verdict] = {}
    if added:
        head_verdicts = _classify_lines(head_content, language)
    if removed:
        base_verdicts = _classify_lines(base_content, language)

    return _FilePrep(
        path=path,
        language=language,
        heuristic=heuristic,
        binary=False,
        ignored=False,
        weight=weight,
        notes=notes,
        base_lines=base_lines,
        head_lines=head_lines,
        added=added,
        removed=removed,
        base_verdicts=base_verdicts,
        head_verdicts=head_verdicts,
    )


def _movable_lines_by_content(
    lines: list[bytes], line_numbers: set[int], verdicts: dict[int, Verdict]
) -> dict[str, list[int]]:
    """Group one file's changed line numbers by normalized (stripped)
    content, restricted to lines that are substantive on their own and long
    enough that a match is unlikely to be coincidental. Short lines like
    `}` or `else:` are excluded on purpose -- matching those would flag
    unrelated lines as "moved" just because they're common, silently
    deflating the score."""
    by_content: dict[str, list[int]] = {}
    for ln in sorted(line_numbers):
        if verdicts.get(ln) != Verdict.SUBSTANTIVE:
            continue
        content = lines[ln - 1].strip().decode("utf-8", errors="replace")
        if len(content) < _MIN_MOVE_MATCH_CHARS:
            continue
        by_content.setdefault(content, []).append(ln)
    return by_content


def _global_movable_lines_by_content(
    preps: list[_FilePrep], side: str
) -> dict[str, list[tuple[int, int]]]:
    """Same grouping as `_movable_lines_by_content`, but pooled across every
    file in the diff. `side` is "added" (matched against head_lines) or
    "removed" (matched against base_lines). Each entry maps content to a
    list of (file_index, line_number) locations, in a stable order (file
    order, then line order within a file) -- pairing later on zips two such
    lists positionally, so this order is what makes that deterministic."""
    by_content: dict[str, list[tuple[int, int]]] = {}
    for file_index, prep in enumerate(preps):
        if side == "added":
            lines, line_numbers, verdicts = prep.head_lines, prep.added, prep.head_verdicts
        else:
            lines, line_numbers, verdicts = prep.base_lines, prep.removed, prep.base_verdicts
        for content, locs in _movable_lines_by_content(lines, line_numbers, verdicts).items():
            by_content.setdefault(content, []).extend((file_index, ln) for ln in locs)
    return by_content


def _find_moved_lines_global(
    preps: list[_FilePrep],
) -> tuple[dict[int, set[int]], dict[int, set[int]], dict[int, int]]:
    """Lines that look substantive in isolation but are an exact
    (whitespace-normalized) content match for a line removed/added elsewhere
    in the *whole diff* -- not just the same file -- i.e. code that moved
    (including to/from a different file, e.g. extracting a function) rather
    than code that's new. Matched by content, not position or file, so this
    catches reordering and cross-file moves alike; it does NOT require a
    full AST tree-diff, but it does mean two files that happen to share an
    identical unrelated line -- one adding it, another removing it -- will
    be misread as a move. `_MIN_MOVE_MATCH_CHARS` keeps this to lines long
    enough that a coincidental match is unlikely.

    Returns three dicts keyed by file index: moved-removed line numbers,
    moved-added line numbers, and how many of that file's moved lines were
    matched to a *different* file (so callers can call that out explicitly
    rather than leaving a same-file move and a cross-file move looking
    identical in the output).
    """
    removed_by_content = _global_movable_lines_by_content(preps, "removed")
    added_by_content = _global_movable_lines_by_content(preps, "added")

    moved_removed: dict[int, set[int]] = {}
    moved_added: dict[int, set[int]] = {}
    cross_file_count: dict[int, int] = {}

    for content, removed_locs in removed_by_content.items():
        added_locs = added_by_content.get(content)
        if not added_locs:
            continue
        n = min(len(removed_locs), len(added_locs))
        for (r_idx, r_ln), (a_idx, a_ln) in zip(removed_locs[:n], added_locs[:n]):
            moved_removed.setdefault(r_idx, set()).add(r_ln)
            moved_added.setdefault(a_idx, set()).add(a_ln)
            if r_idx != a_idx:
                cross_file_count[r_idx] = cross_file_count.get(r_idx, 0) + 1
                cross_file_count[a_idx] = cross_file_count.get(a_idx, 0) + 1

    return moved_removed, moved_added, cross_file_count


def _finalize_file(
    prep: _FilePrep,
    file_moved_removed: set[int],
    file_moved_added: set[int],
    cross_file_moved: int,
) -> FileScore:
    """Phase 2: turn one file's prep plus its share of the whole-diff move
    results into a final FileScore."""
    if prep.ignored:
        return FileScore(
            path=prep.path,
            language=prep.language,
            heuristic=prep.heuristic,
            binary=False,
            ignored=True,
            weight=prep.weight,
            note="; ".join(prep.notes) or None,
        )
    if prep.binary:
        return FileScore(
            path=prep.path,
            language=prep.language,
            heuristic=prep.heuristic,
            binary=True,
            weight=prep.weight,
            note="; ".join(prep.notes) or None,
        )

    added_trivial = sum(
        1 for ln in prep.added if prep.head_verdicts.get(ln) in (Verdict.TRIVIAL, Verdict.BLANK)
    )
    removed_trivial = sum(
        1 for ln in prep.removed if prep.base_verdicts.get(ln) in (Verdict.TRIVIAL, Verdict.BLANK)
    )

    moved = len(file_moved_added) + len(file_moved_removed)
    added_trivial += len(file_moved_added)
    removed_trivial += len(file_moved_removed)

    notes = list(prep.notes)
    if moved:
        detail = f" ({cross_file_moved} to/from another file)" if cross_file_moved else ""
        notes.append(f"{moved} line(s) look moved rather than newly written{detail}")

    return FileScore(
        path=prep.path,
        language=prep.language,
        heuristic=prep.heuristic,
        binary=False,
        weight=prep.weight,
        added_total=len(prep.added),
        added_trivial=added_trivial,
        removed_total=len(prep.removed),
        removed_trivial=removed_trivial,
        moved=moved,
        note="; ".join(notes) if notes else None,
    )


def _finalize_diff(preps: list[_FilePrep]) -> DiffScore:
    """Phase 2 for a whole diff: one move-matching pass across every file's
    candidate lines, then finalize each file with its share of the result."""
    moved_removed, moved_added, cross_file_count = _find_moved_lines_global(preps)
    files = [
        _finalize_file(
            prep,
            moved_removed.get(i, set()),
            moved_added.get(i, set()),
            cross_file_count.get(i, 0),
        )
        for i, prep in enumerate(preps)
    ]
    return DiffScore(files=files)


def score_file(
    path: str,
    base_content: Optional[bytes],
    head_content: Optional[bytes],
    *,
    ignored: bool = False,
    weight: float = 1.0,
) -> FileScore:
    """Score a single file's change. Pass base_content=None for a newly
    added file, head_content=None for a deleted file. Pass ignored=True to
    record the file as excluded (e.g. by a configured ignore pattern)
    without doing any parsing -- the caller is expected to have already
    decided the file should be skipped, and content may not even be loaded
    in that case. `weight` (see diffmeter.config.resolve_weight) only
    affects how much this file counts toward a DiffScore's overall_score;
    it doesn't change this file's own `score`.

    Move detection only ever sees this one file here (there's nothing else
    to pool against) -- for cross-file move detection, score multiple files
    together via `score_diff` or `score_pull_request`.
    """
    prep = _prepare_file(path, base_content, head_content, ignored=ignored, weight=weight)
    return _finalize_diff([prep]).files[0]


FilePair = tuple[str, Optional[bytes], Optional[bytes]]


def score_diff(
    file_pairs: Iterable[FilePair],
    max_workers: Optional[int] = None,
    *,
    matcher: Optional[pathspec.PathSpec] = None,
    weight_matchers: Optional[WeightMatchers] = None,
) -> DiffScore:
    """Score a whole diff: an iterable of (path, base_content, head_content).

    Move detection is pooled across every file passed in here (see the
    module docstring) -- a function moved from one file to another in the
    same diff is recognized as moved on both sides, not scored as 100% new
    in one file and 100% deleted in the other.

    `matcher`/`weight_matchers` (see diffmeter.config.build_matcher /
    build_weight_matchers) apply the same ignore/weight rules used by the
    CLI and score_pull_request, so library callers don't have to reach into
    private scorer internals to get them. Unlike the CLI's local-scoring
    path or score_pull_request -- both of which check `matcher` *before*
    reading a file's content, so an ignored file's bytes are never fetched
    -- callers here have necessarily already read `base_content`/
    `head_content` to build each pair; matching against `matcher` still
    skips the parsing/diffing work for that file, just not the read that
    already happened to hand it to this function.

    Preparing each file (language detection, diffing, AST classification)
    is independent of every other file, so with max_workers > 1 (or None,
    which lets ThreadPoolExecutor pick a default) that phase runs
    concurrently via a thread pool -- safe because tree-sitter parsers are
    cached per-thread (see languages.get_parser), not shared. The
    move-matching finalize pass that follows is fast and runs once,
    sequentially, after every file's prep is in. Results preserve input
    order regardless of which thread finishes first.
    """
    pairs = list(file_pairs)

    def _prep(pair: FilePair) -> _FilePrep:
        path, base_content, head_content = pair
        weight = resolve_weight(path, weight_matchers or [])
        if is_ignored(path, matcher):
            return _prepare_file(path, None, None, ignored=True, weight=weight)
        return _prepare_file(path, base_content, head_content, weight=weight)

    if (max_workers is not None and max_workers <= 1) or len(pairs) <= 1:
        preps = [_prep(pair) for pair in pairs]
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            preps = list(pool.map(_prep, pairs))
    return _finalize_diff(preps)
