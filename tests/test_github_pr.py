import json
from unittest.mock import patch

import pytest

from diffmeter.github_pr import GitHubError, PullRequestRef, parse_pr_reference, score_pull_request


@pytest.mark.parametrize(
    "text,expected",
    [
        ("owner/repo#42", PullRequestRef("owner", "repo", 42)),
        ("https://github.com/owner/repo/pull/42", PullRequestRef("owner", "repo", 42)),
        ("https://github.com/owner/repo/pull/42/", PullRequestRef("owner", "repo", 42)),
        ("github.com/owner/repo/pull/7", PullRequestRef("owner", "repo", 7)),
    ],
)
def test_parse_pr_reference_accepts_known_formats(text, expected):
    assert parse_pr_reference(text) == expected


def test_parse_pr_reference_rejects_garbage():
    with pytest.raises(ValueError):
        parse_pr_reference("not a pr reference")


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(routes):
    def _urlopen(req, timeout=15):
        url = req.full_url
        for prefix, payload in routes.items():
            if url.startswith(prefix):
                return _FakeResponse(payload)
        raise AssertionError(f"unexpected URL requested: {url}")

    return _urlopen


def test_score_pull_request_scores_across_added_modified_removed_files():
    ref = PullRequestRef("acme", "widgets", 7)

    pr_payload = json.dumps({"base": {"sha": "base123"}, "head": {"sha": "head456"}}).encode()
    # Real GitHub responses include "previous_filename": null (present, not omitted) for
    # files that weren't renamed -- these fixtures intentionally match that shape.
    files_payload = json.dumps(
        [
            {"filename": "new_file.py", "status": "added", "previous_filename": None},
            {"filename": "changed.py", "status": "modified", "previous_filename": None},
            {"filename": "gone.py", "status": "removed", "previous_filename": None},
        ]
    ).encode()

    routes = {
        "https://api.github.com/repos/acme/widgets/pulls/7/files": files_payload,
        "https://api.github.com/repos/acme/widgets/pulls/7": pr_payload,
        "https://raw.githubusercontent.com/acme/widgets/head456/new_file.py": b"def f():\n    return 1\n",
        "https://raw.githubusercontent.com/acme/widgets/base123/changed.py": b"def g():\n    return 1\n",
        "https://raw.githubusercontent.com/acme/widgets/head456/changed.py": b"def g():\n    return 2\n",
        "https://raw.githubusercontent.com/acme/widgets/base123/gone.py": b"def h():\n    return 1\n",
    }

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen(routes)):
        result = score_pull_request(ref)

    by_path = {f.path: f for f in result.files}
    assert by_path["new_file.py"].score == 100.0
    assert by_path["changed.py"].score == 100.0
    assert by_path["gone.py"].score == 100.0
    assert result.overall_score == 100.0


def test_score_pull_request_handles_trivial_change():
    ref = PullRequestRef("acme", "widgets", 8)

    pr_payload = json.dumps({"base": {"sha": "b"}, "head": {"sha": "h"}}).encode()
    files_payload = json.dumps(
        [{"filename": "a.py", "status": "modified", "previous_filename": None}]
    ).encode()

    routes = {
        "https://api.github.com/repos/acme/widgets/pulls/8/files": files_payload,
        "https://api.github.com/repos/acme/widgets/pulls/8": pr_payload,
        "https://raw.githubusercontent.com/acme/widgets/b/a.py": b"x = 1\n",
        "https://raw.githubusercontent.com/acme/widgets/h/a.py": b"x = 1\n# note\n",
    }

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen(routes)):
        result = score_pull_request(ref)

    assert result.overall_score == 0.0


def test_score_pull_request_resolves_renamed_file_via_previous_filename():
    """GitHub sets previous_filename to the old path (present, non-null) only for
    renamed/copied files -- this exercises fetching the base blob from that old path."""
    ref = PullRequestRef("acme", "widgets", 9)

    pr_payload = json.dumps({"base": {"sha": "b"}, "head": {"sha": "h"}}).encode()
    files_payload = json.dumps(
        [
            {
                "filename": "new_name.py",
                "previous_filename": "old_name.py",
                "status": "renamed",
            }
        ]
    ).encode()

    routes = {
        "https://api.github.com/repos/acme/widgets/pulls/9/files": files_payload,
        "https://api.github.com/repos/acme/widgets/pulls/9": pr_payload,
        "https://raw.githubusercontent.com/acme/widgets/b/old_name.py": b"def f():\n    return 1\n",
        "https://raw.githubusercontent.com/acme/widgets/h/new_name.py": b"def f():\n    return 2\n",
    }

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen(routes)):
        result = score_pull_request(ref)

    assert result.files[0].path == "new_name.py"
    assert result.overall_score == 100.0


def test_get_json_raises_github_error_on_http_failure():
    import urllib.error

    def _raise(req, timeout=15):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)

    with patch("urllib.request.urlopen", side_effect=_raise):
        with pytest.raises(GitHubError):
            score_pull_request(PullRequestRef("acme", "widgets", 999))
