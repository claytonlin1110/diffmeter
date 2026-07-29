from diffmeter.config import build_matcher, build_weight_matchers
from diffmeter.scorer import DiffScore, score_diff, score_file


def test_pure_comment_addition_scores_zero():
    base = b"def f():\n    return 1\n"
    head = b"def f():\n    # explain\n    return 1\n"
    result = score_file("f.py", base, head)
    assert (result.added_total, result.added_trivial) == (1, 1)
    assert result.score == 0.0


def test_real_logic_change_scores_full():
    base = b"def f():\n    return 1\n"
    head = b"def f():\n    return 2\n"
    result = score_file("f.py", base, head)
    assert result.score == 100.0


def test_mixed_comment_and_logic_change_is_partial():
    base = b"def f():\n    return 1\n"
    head = b"def f():\n    # note\n    return 2\n"
    result = score_file("f.py", base, head)
    assert (result.added_total, result.added_trivial) == (2, 1)
    assert (result.removed_total, result.removed_trivial) == (1, 0)
    assert result.score == 66.7


def test_new_file_scores_on_added_lines_only():
    head = b"def f():\n    return 1\n"
    result = score_file("f.py", None, head)
    assert (result.added_total, result.removed_total) == (2, 0)
    assert result.score == 100.0


def test_deleted_file_scores_on_removed_lines_only():
    base = b"def f():\n    return 1\n"
    result = score_file("f.py", base, None)
    assert (result.added_total, result.removed_total) == (0, 2)
    assert result.score == 100.0


def test_binary_file_is_excluded_from_scoring():
    result = score_file("image.png", b"\x00\x01\x02", b"\x00\x01\x03")
    assert result.binary is True
    assert result.score is None


def test_unrecognized_extension_falls_back_to_heuristic():
    base = b"puts 'hi'\n"
    head = b"puts 'hi'\n# a note\nputs 'bye'\n"
    result = score_file("f.someweirdext", base, head)
    assert result.heuristic is True
    assert result.language is None


def test_blank_lines_count_as_trivial():
    base = b"def f():\n    return 1\n"
    head = b"def f():\n\n    return 1\n"
    result = score_file("f.py", base, head)
    assert (result.added_total, result.added_trivial) == (1, 1)
    assert result.score == 0.0


def test_javascript_block_comment_is_trivial():
    base = b"function f() { return 1; }\n"
    head = b"/* explain */\nfunction f() { return 1; }\n"
    result = score_file("f.js", base, head)
    assert result.score == 0.0
    assert result.language == "javascript"


def test_rust_two_comment_node_kinds_both_trivial():
    base = b"fn f() -> i32 { 1 }\n"
    head = b"// line comment\n/* block comment */\nfn f() -> i32 { 1 }\n"
    result = score_file("f.rs", base, head)
    assert result.score == 0.0


def test_no_changes_scores_none():
    content = b"def f():\n    return 1\n"
    result = score_file("f.py", content, content)
    assert result.changed_total == 0
    assert result.score is None


def test_score_diff_aggregates_across_files():
    pairs = [
        ("a.py", b"x = 1\n", b"x = 1\n# comment\n"),
        ("b.py", b"y = 1\n", b"y = 2\n"),
    ]
    result = score_diff(pairs)
    assert result.changed_total == 3
    assert result.changed_trivial == 1
    assert result.overall_score == 66.7


def test_score_diff_with_no_files_has_no_overall_score():
    result = score_diff([])
    assert result.files == []
    assert result.overall_score is None


def test_score_diff_preserves_input_order_under_concurrency():
    # Enough files that a thread pool genuinely interleaves their completion
    # order; results must still come back in input order regardless of
    # which finishes first (pool.map's contract, but worth locking in).
    pairs = [(f"file_{i}.py", b"x = 1\n", f"x = {i}\n".encode()) for i in range(20)]
    result = score_diff(pairs, max_workers=8)
    assert [f.path for f in result.files] == [f"file_{i}.py" for i in range(20)]


def test_score_diff_max_workers_one_matches_default_concurrency_result():
    pairs = [
        ("a.py", b"x = 1\n", b"x = 1\n# comment\n"),
        ("b.py", b"y = 1\n", b"y = 2\n"),
    ]
    sequential = score_diff(pairs, max_workers=1)
    concurrent = score_diff(pairs, max_workers=4)
    assert [f.score for f in sequential.files] == [f.score for f in concurrent.files]


def test_score_diff_max_workers_zero_does_not_crash():
    # Regression test for issue #6: ThreadPoolExecutor(max_workers=0) raises
    # ValueError, and the old `max_workers == 1` fast-path only special-cased
    # exactly 1, not anything <= 1 -- so 0 (or negative) fell through to the
    # crashing path whenever there was more than one file to score.
    pairs = [
        ("a.py", b"x = 1\n", b"x = 1\n# comment\n"),
        ("b.py", b"y = 1\n", b"y = 2\n"),
    ]
    result = score_diff(pairs, max_workers=0)
    assert [f.score for f in result.files] == [0.0, 100.0]


def test_score_diff_matcher_excludes_file_without_scoring_it():
    pairs = [
        ("a.py", b"x = 1\n", b"x = 2\n"),
        ("vendor/lib.py", b"x = 1\n", b"x = 1\n# noise\n"),
    ]
    matcher = build_matcher(["vendor/**"])
    result = score_diff(pairs, matcher=matcher)
    by_path = {f.path: f for f in result.files}
    assert by_path["vendor/lib.py"].ignored is True
    assert by_path["vendor/lib.py"].score is None
    # Only a.py's fully-substantive change counts toward the aggregate.
    assert result.overall_score == 100.0


def test_score_diff_weight_matchers_affect_overall_score_not_file_score():
    pairs = [
        ("a.py", b"x = 1\n", b"x = 2\n"),  # 100% substantive
        ("b.py", b"y = 1\n", b"y = 1\n# note\n"),  # 0% substantive (pure comment add)
    ]
    weight_matchers = build_weight_matchers([("b.py", 0.0)])
    result = score_diff(pairs, weight_matchers=weight_matchers)
    by_path = {f.path: f for f in result.files}
    assert by_path["b.py"].score == 0.0  # file's own score is unaffected by weight
    assert by_path["b.py"].weight == 0.0
    # b.py is weighted to zero, so only a.py's 100% counts toward the aggregate.
    assert result.overall_score == 100.0


def test_pure_reorder_is_detected_as_moved_not_new():
    base = (
        b"def f():\n"
        b"    a = compute_something()\n"
        b"    b = another_call()\n"
        b"    return a + b\n"
    )
    head = (
        b"def f():\n"
        b"    b = another_call()\n"
        b"    a = compute_something()\n"
        b"    return a + b\n"
    )
    result = score_file("f.py", base, head)
    assert result.moved > 0
    assert result.score == 0.0
    assert "moved" in result.note


def test_genuinely_new_line_is_not_treated_as_moved():
    base = b"def f():\n    a = compute_something()\n    return a\n"
    head = b"def f():\n    a = compute_something()\n    b = brand_new_call()\n    return a + b\n"
    result = score_file("f.py", base, head)
    assert result.moved == 0
    assert result.score == 100.0


def test_short_identical_lines_are_not_falsely_matched_as_moved():
    # `}` (and other short, extremely common lines) shouldn't be treated as
    # "moved" just because an identical short line exists on both sides --
    # that would silently deflate scores for unrelated real changes.
    base = b"function f() {\n    return 1;\n}\n"
    head = b"function f() {\n    return 2;\n}\n"
    result = score_file("f.js", base, head)
    assert result.moved == 0
    assert result.score == 100.0


def test_moved_line_and_genuine_change_are_distinguished_in_the_same_file():
    base = (
        b"def f():\n"
        b"    unique_helper_call_xyz()\n"
        b"    x = 1\n"
        b"    y = 2\n"
        b"    return x + y\n"
    )
    head = (
        b"def f():\n"
        b"    x = 1\n"
        b"    y = 3\n"
        b"    return x + y\n"
        b"    unique_helper_call_xyz()\n"
    )
    result = score_file("f.py", base, head)
    # unique_helper_call_xyz() (24 chars) is long enough to be matched as moved;
    # x = 1 (5 chars) is identical on both sides too but falls below
    # _MIN_MOVE_MATCH_CHARS, so it's *not* caught -- a known, deliberate
    # trade-off to avoid false-positive matches on short lines elsewhere.
    assert result.moved == 2
    assert result.added_trivial == 1
    assert result.removed_trivial == 1
    # y = 2 -> y = 3 is a genuine change and must still count as substantive.
    assert result.score == 66.7


def test_default_weight_does_not_change_a_files_own_score():
    base = b"def f():\n    return 1\n"
    head = b"def f():\n    return 2\n"
    result = score_file("f.py", base, head, weight=0.3)
    # weight affects DiffScore.overall_score, not this file's own score
    assert result.score == 100.0
    assert result.weight == 0.3


def test_score_file_does_not_detect_moves_across_files():
    """score_file only ever sees one file -- there's nothing else to pool
    against -- so a line removed from one file and added, unchanged, to
    another must NOT be detected as moved when each is scored alone. This
    is the behavior score_diff/score_pull_request improve on by pooling
    candidates across every file in the diff at once."""
    moved_line = b"    return 'a value long enough to be matched as moved'\n"
    old_file = score_file("old.py", b"def f():\n" + moved_line, b"def f():\n")
    new_file = score_file("new.py", None, b"def g():\n" + moved_line)
    assert old_file.moved == 0
    assert new_file.moved == 0
    assert new_file.score == 100.0


def test_score_diff_detects_a_function_extracted_to_a_new_file():
    # new.py's content is exactly the function body removed from old.py (plus
    # nothing else), so both of new.py's lines match something removed from
    # old.py -- a clean "fully moved" case with a predictable 0.0 score,
    # rather than a mix of moved + genuinely-new lines.
    extracted_function = (
        b"def helper_one():\n    return 'a value long enough to be matched'\n"
    )
    pairs = [
        ("old.py", extracted_function + b"\ndef other():\n    pass\n", b"def other():\n    pass\n"),
        ("new.py", None, extracted_function),
    ]
    result = score_diff(pairs)
    by_path = {f.path: f for f in result.files}

    # new.py: both of its 2 added lines matched something removed from
    # old.py, so moved == 2 (no removed lines of its own to also count).
    assert by_path["new.py"].moved == 2
    assert by_path["new.py"].score == 0.0
    assert "another file" in by_path["new.py"].note

    # old.py: 2 of its 3 removed lines (def + return; the blank separator
    # doesn't match anything) matched new.py's added lines.
    assert by_path["old.py"].moved == 2
    assert "another file" in by_path["old.py"].note

    # The whole point: this used to score as 100% new + (mostly) deleted.
    assert result.overall_score == 0.0


def test_score_diff_same_file_move_note_does_not_mention_another_file():
    # Sanity check that the "(N to/from another file)" detail only appears
    # for genuinely cross-file matches, not every move. `moved` counts both
    # sides of a match (the added-side line and the removed-side line), so
    # one swapped line contributes moved == 2, not 1.
    base = b"a = 1\nunique_reorderable_line_content_here\nb = 2\n"
    head = b"unique_reorderable_line_content_here\na = 1\nb = 2\n"
    result = score_diff([("f.py", base, head)])
    assert result.files[0].moved == 2
    assert "another file" not in result.files[0].note


def test_cross_file_matching_can_misfire_on_coincidental_identical_lines():
    """Documented, deliberate trade-off (see _find_moved_lines_global's
    docstring): two files that happen to share an identical unrelated line
    -- one adding it, another removing it -- are indistinguishable from a
    real move once matching is pooled across the whole diff. This test
    exists so the behavior is pinned down and explained, not rediscovered
    as a surprise bug report."""
    coincidental_line = b"    logger.info('starting the request handler')\n"
    pairs = [
        # removes the line as part of an unrelated cleanup
        ("service_a.py", b"def handle():\n" + coincidental_line + b"    pass\n", b"def handle():\n    pass\n"),
        # independently adds the *same* line -- not a real move
        ("service_b.py", b"def handle():\n    pass\n", b"def handle():\n" + coincidental_line + b"    pass\n"),
    ]
    result = score_diff(pairs)
    by_path = {f.path: f for f in result.files}
    assert by_path["service_a.py"].moved == 1
    assert by_path["service_b.py"].moved == 1


def test_overall_score_unweighted_when_no_weights_given():
    pairs = [
        ("a.py", b"x = 1\n", b"x = 1\n# comment\n"),  # trivial, drags score down
        ("b.py", b"y = 1\n", b"y = 2\n"),  # substantive
    ]
    result = score_diff(pairs)
    assert result.overall_score == 66.7
    assert all(f.weight == 1.0 for f in result.files)


def test_overall_score_excludes_zero_weighted_file():
    files = [
        score_file("a.py", b"x = 1\n", b"x = 1\n# comment\n", weight=0.0),
        score_file("b.py", b"y = 1\n", b"y = 2\n", weight=1.0),
    ]
    result = DiffScore(files=files)
    assert result.overall_score == 100.0
    # unweighted raw totals stay untouched, for transparency: a.py
    # contributes 1 changed (trivial) line, b.py contributes 2 (substantive)
    assert result.changed_total == 3
    assert result.changed_trivial == 1


def test_overall_score_is_none_when_all_weighted_mass_is_zero():
    files = [
        score_file("a.py", b"x = 1\n", b"x = 2\n", weight=0.0),
        score_file("b.py", b"y = 1\n", b"y = 2\n", weight=0.0),
    ]
    result = DiffScore(files=files)
    assert result.overall_score is None


def test_overall_score_partial_weight_shifts_the_average():
    files = [
        # 1 changed line, trivial (0 substantive)
        score_file("a.py", b"x = 1\n", b"x = 1\n# comment\n", weight=0.5),
        # 2 changed lines (1 added, 1 removed), both substantive
        score_file("b.py", b"y = 1\n", b"y = 2\n", weight=1.0),
    ]
    result = DiffScore(files=files)
    # weighted: (0.5*1 + 1.0*2) total "mass", (0.5*0 + 1.0*2) substantive
    # = 2.0 / 2.5 = 80%, vs. 66.7% if both files' lines counted equally
    # (2 substantive / 3 total) -- weighting a.py down shrinks its drag.
    assert result.overall_score == 80.0
