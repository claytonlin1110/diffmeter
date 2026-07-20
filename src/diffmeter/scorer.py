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
content match for a line on the other side of the diff (added and removed
in the same file) are treated as *moved* rather than newly written -- this
catches reordering without needing a full AST tree-diff. See
`_find_moved_lines` for the matching rule and its limits (same-file only,
exact normalized match, a minimum length to avoid matching on stray `}` or
`else:` lines).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional

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
        """0-100, or None if there's nothing to score (binary file, or no
        lines actually changed)."""
        if self.binary or self.changed_total == 0:
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
        total = self.changed_total
        if total == 0:
            return None
        return round(100.0 * (total - self.changed_trivial) / total, 1)


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


def _movable_lines_by_content(
    lines: list[bytes], line_numbers: set[int], verdicts: dict[int, Verdict]
) -> dict[str, list[int]]:
    """Group changed line numbers by normalized (stripped) content, restricted
    to lines that are substantive on their own and long enough that a match
    is unlikely to be coincidental. Short lines like `}` or `else:` are
    excluded on purpose -- matching those would flag unrelated lines as
    "moved" just because they're common, silently deflating the score."""
    by_content: dict[str, list[int]] = {}
    for ln in sorted(line_numbers):
        if verdicts.get(ln) != Verdict.SUBSTANTIVE:
            continue
        content = lines[ln - 1].strip().decode("utf-8", errors="replace")
        if len(content) < _MIN_MOVE_MATCH_CHARS:
            continue
        by_content.setdefault(content, []).append(ln)
    return by_content


def _find_moved_lines(
    base_lines: list[bytes],
    head_lines: list[bytes],
    removed: set[int],
    added: set[int],
    base_verdicts: dict[int, Verdict],
    head_verdicts: dict[int, Verdict],
) -> tuple[set[int], set[int]]:
    """Lines that look substantive in isolation but are an exact
    (whitespace-normalized) content match for a line removed/added elsewhere
    in the same file's diff -- i.e. code that moved rather than code that's
    new. Matched by content, not position, so this catches reordering, not
    just reformatting. Limits: same file only (no cross-file move
    detection), and only lines meeting `_MIN_MOVE_MATCH_CHARS`."""
    removed_by_content = _movable_lines_by_content(base_lines, removed, base_verdicts)
    added_by_content = _movable_lines_by_content(head_lines, added, head_verdicts)

    moved_removed: set[int] = set()
    moved_added: set[int] = set()
    for content, removed_lines in removed_by_content.items():
        added_lines = added_by_content.get(content)
        if not added_lines:
            continue
        n = min(len(removed_lines), len(added_lines))
        moved_removed.update(removed_lines[:n])
        moved_added.update(added_lines[:n])
    return moved_removed, moved_added


def score_file(
    path: str, base_content: Optional[bytes], head_content: Optional[bytes]
) -> FileScore:
    """Score a single file's change. Pass base_content=None for a newly
    added file, head_content=None for a deleted file."""
    language = detect_language(path)
    heuristic = language is None or get_parser(language) is None

    sample = head_content if head_content is not None else base_content
    is_binary = sample is not None and _BINARY_MARKER in sample[:_BINARY_SNIFF_BYTES]

    result = FileScore(path=path, language=language, heuristic=heuristic, binary=is_binary)
    if is_binary:
        result.note = "binary file, excluded from scoring"
        return result

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
        result.added_total = len(added)
        result.added_trivial = sum(
            1 for ln in added if head_verdicts.get(ln) in (Verdict.TRIVIAL, Verdict.BLANK)
        )

    if removed:
        base_verdicts = _classify_lines(base_content, language)
        result.removed_total = len(removed)
        result.removed_trivial = sum(
            1 for ln in removed if base_verdicts.get(ln) in (Verdict.TRIVIAL, Verdict.BLANK)
        )

    if added and removed:
        moved_removed, moved_added = _find_moved_lines(
            base_lines, head_lines, removed, added, base_verdicts, head_verdicts
        )
        result.moved = len(moved_added) + len(moved_removed)
        result.added_trivial += len(moved_added)
        result.removed_trivial += len(moved_removed)
        if result.moved:
            notes.append(f"{result.moved} line(s) look moved rather than newly written")

    if notes:
        result.note = "; ".join(notes)

    return result


FilePair = tuple[str, Optional[bytes], Optional[bytes]]


def score_diff(file_pairs: Iterable[FilePair]) -> DiffScore:
    """Score a whole diff: an iterable of (path, base_content, head_content)."""
    return DiffScore(files=[score_file(p, b, h) for p, b, h in file_pairs])
