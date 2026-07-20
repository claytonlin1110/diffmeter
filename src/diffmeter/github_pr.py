"""Score a GitHub pull request directly via the GitHub API, with no local
clone required. Used by `diffmeter score --pr owner/repo#123`."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from diffmeter.scorer import DiffScore, score_file

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


def score_pull_request(ref: PullRequestRef) -> DiffScore:
    pr = _get_json(f"{_API_ROOT}/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}")
    base_sha = pr["base"]["sha"]
    head_sha = pr["head"]["sha"]

    results = []
    for f in _fetch_pr_files(ref):
        status = f["status"]  # "added" | "removed" | "modified" | "renamed" | "copied" | "changed"
        path = f["filename"]
        previous_path = f.get("previous_filename") or path

        base_content = None if status == "added" else _fetch_blob(ref.owner, ref.repo, base_sha, previous_path)
        head_content = None if status == "removed" else _fetch_blob(ref.owner, ref.repo, head_sha, path)
        results.append(score_file(path, base_content, head_content))

    return DiffScore(files=results)
