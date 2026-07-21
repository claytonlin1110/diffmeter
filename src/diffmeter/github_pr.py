"""Score a GitHub pull request directly via the GitHub API, with no local
clone required. Used by `diffmeter score --pr owner/repo#123`."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

import pathspec

from diffmeter.config import is_ignored
from diffmeter.scorer import DiffScore, FileScore, score_file

_API_ROOT = "https://api.github.com"
_RAW_ROOT = "https://raw.githubusercontent.com"

_URL_RE = re.compile(r"^(?:(?:https?://)?github\.com/)?([^/\s]+)/([^/\s]+)/pull/(\d+)/?$")
_SHORT_RE = re.compile(r"^([^/\s]+)/([^/\s]+)#(\d+)$")


class GitHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class PullRequestRef:
    owner: str
    repo: str
    number: int


def parse_pr_reference(text: str) -> PullRequestRef:
    """Accepts either a github.com PR URL or the short form owner/repo#123."""
    text = text.strip()
    match = _URL_RE.match(text) or _SHORT_RE.match(text)
    if not match:
        raise ValueError(
            f"Not a recognizable pull request reference: {text!r} "
            "(expected a github.com PR URL or owner/repo#123)"
        )
    owner, repo, number = match.groups()
    return PullRequestRef(owner=owner, repo=repo, number=int(number))


def _headers() -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "diffmeter"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_json(url: str):
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        hint = ""
        if exc.code == 403:
            hint = " (GitHub's unauthenticated rate limit may be exhausted; set GITHUB_TOKEN)"
        elif exc.code == 404:
            hint = " (check the owner/repo/PR number)"
        raise GitHubError(f"GitHub API request to {url} failed: {exc.code} {exc.reason}{hint}") from exc
    except urllib.error.URLError as exc:
        raise GitHubError(f"Could not reach GitHub API: {exc.reason}") from exc


def _fetch_pr_files(ref: PullRequestRef) -> list[dict]:
    files: list[dict] = []
    page = 1
    while True:
        url = f"{_API_ROOT}/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}/files?per_page=100&page={page}"
        batch = _get_json(url)
        files.extend(batch)
        if len(batch) < 100:
            return files
        page += 1


def _fetch_blob(owner: str, repo: str, sha: str, path: str) -> Optional[bytes]:
    url = f"{_RAW_ROOT}/{owner}/{repo}/{sha}/{urllib.parse.quote(path)}"
    req = urllib.request.Request(url, headers={"User-Agent": "diffmeter"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise GitHubError(f"Failed to fetch {path}@{sha}: {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise GitHubError(f"Could not reach raw.githubusercontent.com: {exc.reason}") from exc


def _score_pr_entry(
    ref: PullRequestRef,
    base_sha: str,
    head_sha: str,
    entry: dict,
    matcher: Optional[pathspec.PathSpec],
) -> FileScore:
    status = entry["status"]  # "added" | "removed" | "modified" | "renamed" | "copied" | "changed"
    path = entry["filename"]

    if is_ignored(path, matcher):
        return score_file(path, None, None, ignored=True)

    previous_path = entry.get("previous_filename") or path
    base_content = None if status == "added" else _fetch_blob(ref.owner, ref.repo, base_sha, previous_path)
    head_content = None if status == "removed" else _fetch_blob(ref.owner, ref.repo, head_sha, path)
    return score_file(path, base_content, head_content)


def score_pull_request(
    ref: PullRequestRef,
    matcher: Optional[pathspec.PathSpec] = None,
    max_workers: Optional[int] = 8,
) -> DiffScore:
    """`matcher` (see diffmeter.config.build_matcher) excludes matching paths
    from scoring without fetching their blob content -- there's no local
    checkout to read a .diffmeter.toml from in this mode, so patterns must
    be passed in explicitly by the caller.

    Per-file blob fetching and scoring runs concurrently (max_workers
    threads, default 8): this is dominated by network round-trips to
    raw.githubusercontent.com, not CPU, so a PR touching many files no
    longer pays for each file serially. Pass max_workers=1 to disable.
    """
    pr = _get_json(f"{_API_ROOT}/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}")
    base_sha = pr["base"]["sha"]
    head_sha = pr["head"]["sha"]

    entries = _fetch_pr_files(ref)
    if max_workers == 1 or len(entries) <= 1:
        results = [_score_pr_entry(ref, base_sha, head_sha, e, matcher) for e in entries]
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            results = list(
                pool.map(lambda e: _score_pr_entry(ref, base_sha, head_sha, e, matcher), entries)
            )

    return DiffScore(files=results)
