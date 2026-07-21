"""Regression coverage for the thread-local parser cache in languages.py.

Sharing one tree-sitter Parser object across threads (the original
@lru_cache implementation did this) is a real correctness hazard once
score_diff/score_pull_request parse concurrently -- these tests check the
cache boundary directly rather than relying on timing to expose a race.
"""

import threading

from diffmeter.languages import get_parser


def test_get_parser_returns_a_parser_for_a_known_language():
    assert get_parser("python") is not None


def test_get_parser_returns_none_for_unknown_language():
    assert get_parser("not-a-real-language-xyz") is None


def test_get_parser_gives_each_thread_its_own_parser_instance():
    """The whole point of the thread-local cache: two threads asking for the
    same language must never receive the same Parser object, since
    tree-sitter Parsers aren't safe to call .parse() on concurrently.

    Uses raw threading.Thread (not a pool) so each call is guaranteed to run
    on a genuinely distinct OS thread -- a ThreadPoolExecutor given two
    sequential submit().result() calls may just reuse one idle worker for
    both, which would pass this assertion for the wrong reason.

    Stores the actual Parser *objects* (not id()) and compares with `is`:
    an earlier version of this test compared id(get_parser(...)) captured
    from each thread, which is unsound once the threads don't overlap --
    t1 fully exits (and threading.local tears down its slot) before t2
    even starts, so CPython is free to reuse t1's parser's memory address
    for t2's parser, making two genuinely different objects compare equal
    by id(). This failed for exactly that reason on macOS/Python 3.10 in
    CI. Holding a live reference to both objects until the assertion keeps
    both addresses pinned, which is what actually makes the comparison
    meaningful.
    """
    parsers: dict[str, object] = {}

    def _record(key: str) -> None:
        parsers[key] = get_parser("python")

    t1 = threading.Thread(target=_record, args=("a",))
    t2 = threading.Thread(target=_record, args=("b",))
    t1.start()
    t1.join()
    t2.start()
    t2.join()

    assert parsers["a"] is not parsers["b"]


def test_get_parser_reuses_the_same_instance_within_one_thread():
    """Not just correctness -- still cached, so we're not reconstructing a
    parser for every single file scored on the same thread."""
    first = get_parser("python")
    second = get_parser("python")
    assert first is second
