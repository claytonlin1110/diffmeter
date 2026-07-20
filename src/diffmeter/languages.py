"""Language detection and tree-sitter grammar access, with a safe fallback
for files diffmeter doesn't have a grammar for."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Protocol


class _Parser(Protocol):
    def parse(self, source: bytes): ...


@lru_cache(maxsize=None)
def _backend():
    import tree_sitter_language_pack as tslp

    return tslp


def detect_language(path: str) -> Optional[str]:
    """Best-effort language name for a file path, or None if unknown."""
    try:
        return _backend().detect_language_from_path(path)
    except Exception:
        return None


@lru_cache(maxsize=None)
def get_parser(language: str) -> Optional[_Parser]:
    """A tree-sitter parser for `language`, or None if unavailable.

    Cached because constructing a parser has real cost and diffmeter parses
    many small snippets (before/after content per changed file).
    """
    try:
        return _backend().get_parser(language)
    except Exception:
        return None


@dataclass(frozen=True)
class LineComments:
    """Line-comment markers used by the regex fallback for languages diffmeter
    has no tree-sitter grammar for. Best-effort only: it can't see block
    comments or comments embedded mid-statement."""

    markers: tuple[str, ...] = ("#", "//", "--", ";", "%")


FALLBACK_MARKERS = LineComments()
