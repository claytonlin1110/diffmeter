from diffmeter.scorer import score_diff, score_file


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
