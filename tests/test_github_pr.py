import json
import urllib.error
import urllib.request
from email.message import Message
from unittest.mock import patch

import pytest

from diffmeter.github_pr import (
    GitHubError,
    PullRequestRef,
    _urlopen_with_retry,
    parse_pr_reference,
    score_pull_request,
)


def _http_error(code: int, retry_after: "str | None" = None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://example.test/x", code, "error", headers, None)


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


def test_score_pull_request_skips_fetching_blobs_for_ignored_files():
    from diffmeter.config import build_matcher

    ref = PullRequestRef("acme", "widgets", 10)

    pr_payload = json.dumps({"base": {"sha": "b"}, "head": {"sha": "h"}}).encode()
    files_payload = json.dumps(
        [
            {"filename": "app.py", "status": "modified", "previous_filename": None},
            {"filename": "package-lock.json", "status": "modified", "previous_filename": None},
        ]
    ).encode()

    routes = {
        "https://api.github.com/repos/acme/widgets/pulls/10/files": files_payload,
        "https://api.github.com/repos/acme/widgets/pulls/10": pr_payload,
        "https://raw.githubusercontent.com/acme/widgets/b/app.py": b"x = 1\n",
        "https://raw.githubusercontent.com/acme/widgets/h/app.py": b"x = 2\n",
        # deliberately no route for package-lock.json -- if the code tries to
        # fetch it despite the ignore pattern, _fake_urlopen raises AssertionError
    }

    matcher = build_matcher(["package-lock.json"])
    with patch("urllib.request.urlopen", side_effect=_fake_urlopen(routes)):
        result = score_pull_request(ref, matcher=matcher)

    by_path = {f.path: f for f in result.files}
    assert by_path["package-lock.json"].ignored is True
    assert by_path["package-lock.json"].score is None
    assert by_path["app.py"].score == 100.0
    assert result.overall_score == 100.0


def _request():
    return urllib.request.Request("https://example.test/x")


def test_urlopen_with_retry_succeeds_immediately_without_sleeping():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(b"ok")) as mock_open:
        with patch("diffmeter.github_pr.time.sleep") as mock_sleep:
            result = _urlopen_with_retry(_request())
    assert result == b"ok"
    assert mock_open.call_count == 1
    mock_sleep.assert_not_called()


def test_urlopen_with_retry_retries_on_5xx_then_succeeds():
    side_effects = [_http_error(503), _http_error(502), _FakeResponse(b"ok")]
    with patch("urllib.request.urlopen", side_effect=side_effects) as mock_open:
        with patch("diffmeter.github_pr.time.sleep") as mock_sleep:
            result = _urlopen_with_retry(_request())
    assert result == b"ok"
    assert mock_open.call_count == 3
    assert mock_sleep.call_count == 2


def test_urlopen_with_retry_retries_on_connection_error_then_succeeds():
    side_effects = [urllib.error.URLError("connection reset"), _FakeResponse(b"ok")]
    with patch("urllib.request.urlopen", side_effect=side_effects):
        with patch("diffmeter.github_pr.time.sleep") as mock_sleep:
            result = _urlopen_with_retry(_request())
    assert result == b"ok"
    assert mock_sleep.call_count == 1


def test_urlopen_with_retry_honors_retry_after_on_secondary_rate_limit():
    side_effects = [_http_error(403, retry_after="5"), _FakeResponse(b"ok")]
    with patch("urllib.request.urlopen", side_effect=side_effects):
        with patch("diffmeter.github_pr.time.sleep") as mock_sleep:
            _urlopen_with_retry(_request())
    mock_sleep.assert_called_once_with(5.0)


def test_urlopen_with_retry_caps_retry_after_at_max_wait():
    side_effects = [_http_error(403, retry_after="99999"), _FakeResponse(b"ok")]
    with patch("urllib.request.urlopen", side_effect=side_effects):
        with patch("diffmeter.github_pr.time.sleep") as mock_sleep:
            _urlopen_with_retry(_request())
    mock_sleep.assert_called_once_with(60.0)


def test_urlopen_with_retry_does_not_retry_404():
    with patch("urllib.request.urlopen", side_effect=_http_error(404)) as mock_open:
        with patch("diffmeter.github_pr.time.sleep") as mock_sleep:
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                _urlopen_with_retry(_request())
    assert exc_info.value.code == 404
    assert mock_open.call_count == 1
    mock_sleep.assert_not_called()


def test_urlopen_with_retry_does_not_retry_403_without_retry_after():
    """A 403 with no Retry-After is GitHub's *primary* rate limit -- the
    quota is genuinely exhausted, not a transient blip, so retrying
    immediately would just fail again."""
    with patch("urllib.request.urlopen", side_effect=_http_error(403)) as mock_open:
        with patch("diffmeter.github_pr.time.sleep") as mock_sleep:
            with pytest.raises(urllib.error.HTTPError):
                _urlopen_with_retry(_request())
    assert mock_open.call_count == 1
    mock_sleep.assert_not_called()


def test_urlopen_with_retry_gives_up_after_max_retries():
    with patch("urllib.request.urlopen", side_effect=_http_error(503)) as mock_open:
        with patch("diffmeter.github_pr.time.sleep"):
            with pytest.raises(urllib.error.HTTPError):
                _urlopen_with_retry(_request(), max_retries=2)
    assert mock_open.call_count == 3  # 1 initial attempt + 2 retries


def test_score_pull_request_applies_weight_matchers():
    from diffmeter.config import build_weight_matchers

    ref = PullRequestRef("acme", "widgets", 11)

    pr_payload = json.dumps({"base": {"sha": "b"}, "head": {"sha": "h"}}).encode()
    files_payload = json.dumps(
        [
            {"filename": "app.py", "status": "modified", "previous_filename": None},
            {"filename": "README.md", "status": "modified", "previous_filename": None},
        ]
    ).encode()

    routes = {
        "https://api.github.com/repos/acme/widgets/pulls/11/files": files_payload,
        "https://api.github.com/repos/acme/widgets/pulls/11": pr_payload,
        "https://raw.githubusercontent.com/acme/widgets/b/app.py": b"x = 1\n",
        "https://raw.githubusercontent.com/acme/widgets/h/app.py": b"x = 2\n",
        "https://raw.githubusercontent.com/acme/widgets/b/README.md": b"line\n",
        "https://raw.githubusercontent.com/acme/widgets/h/README.md": b"line\n\n",
    }

    weight_matchers = build_weight_matchers([("README.md", 0.0)])
    with patch("urllib.request.urlopen", side_effect=_fake_urlopen(routes)):
        unweighted = score_pull_request(ref)
        weighted = score_pull_request(ref, weight_matchers=weight_matchers)

    by_path = {f.path: f for f in weighted.files}
    assert by_path["README.md"].weight == 0.0
    assert by_path["app.py"].weight == 1.0
    assert unweighted.overall_score != weighted.overall_score
    assert weighted.overall_score == 100.0


def test_get_json_raises_github_error_on_http_failure():
    import urllib.error

    def _raise(req, timeout=15):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)

    with patch("urllib.request.urlopen", side_effect=_raise):
        with pytest.raises(GitHubError):
            score_pull_request(PullRequestRef("acme", "widgets", 999))
