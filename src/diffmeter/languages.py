"""Language detection and tree-sitter grammar access, with a safe fallback
for files diffmeter doesn't have a grammar for."""

from __future__ import annotations

import threading
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


_thread_local = threading.local()


def get_parser(language: str) -> Optional[_Parser]:
    """A tree-sitter parser for `language`, or None if unavailable.

    Cached per-thread, not process-wide: tree-sitter Parser objects aren't
    safe to call .parse() on concurrently from multiple threads (the
    underlying library hands back a fresh object per call -- confirmed by
    calling it twice and checking identity -- so a thread-local cache is
    both correct and still avoids reconstructing a parser for every file).
    A single shared cache would let score_diff's optional thread-pool
    concurrency silently corrupt parses under load.
    """
    cache = getattr(_thread_local, "parsers", None)
    if cache is None:
        cache = {}
        _thread_local.parsers = cache
    if language not in cache:
        try:
            cache[language] = _backend().get_parser(language)
        except Exception:
            cache[language] = None
    return cache[language]


@dataclass(frozen=True)
class LineComments:
    """Line-comment markers used by the regex fallback for languages diffmeter
    has no tree-sitter grammar for. Best-effort only: it can't see block
    comments or comments embedded mid-statement."""

    markers: tuple[str, ...] = ("#", "//", "--", ";", "%")


FALLBACK_MARKERS = LineComments()
