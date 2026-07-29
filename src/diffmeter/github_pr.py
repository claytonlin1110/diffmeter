"""Score a GitHub pull request directly via the GitHub API, with no local
clone required. Used by `diffmeter score --pr owner/repo#123`."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

import pathspec

from diffmeter.config import (
    CONFIG_FILENAME,
    DiffmeterConfig,
    WeightMatchers,
    build_matcher,
    build_weight_matchers,
    is_ignored,
    parse_config,
    resolve_weight,
)
from diffmeter.scorer import DiffScore, _FilePrep, _finalize_diff, _prepare_file

_API_ROOT = "https://api.github.com"
_RAW_ROOT = "https://raw.githubusercontent.com"

_MAX_RETRIES = 3
_RETRYABLE_STATUS = {500, 502, 503, 504}
_MAX_RETRY_AFTER_SECONDS = 60

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


def _urlopen_with_retry(
    req: urllib.request.Request, *, timeout: int = 15, max_retries: int = _MAX_RETRIES
) -> bytes:
    """GET `req`, retrying transient failures with backoff.

    Retries: connection-level errors (URLError -- DNS hiccups, timeouts,
    resets), 5xx server errors, and GitHub's *secondary* rate limit (a 403
    that carries a Retry-After header, per GitHub's own docs on abuse
    detection -- respected exactly, capped at _MAX_RETRY_AFTER_SECONDS so a
    huge value can't stall a CI job for an hour).

    Does NOT retry: 404 (won't fix itself), 401 (auth failure), or a 403
    with no Retry-After -- that's GitHub's *primary* rate limit, which
    means the quota is actually exhausted, not a transient blip. Retrying
    that immediately would just fail again; the existing GITHUB_TOKEN hint
    in the caller is the real fix.
    """
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            retryable = exc.code in _RETRYABLE_STATUS or (exc.code == 403 and retry_after is not None)
            if not retryable or attempt >= max_retries:
                raise
            delay = min(float(retry_after), _MAX_RETRY_AFTER_SECONDS) if retry_after else 2**attempt
        except urllib.error.URLError:
            if attempt >= max_retries:
                raise
            delay = 2**attempt
        time.sleep(delay)
        attempt += 1


def _get_json(url: str):
    req = urllib.request.Request(url, headers=_headers())
    try:
        return json.loads(_urlopen_with_retry(req))
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
        return _urlopen_with_retry(req)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise GitHubError(f"Failed to fetch {path}@{sha}: {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise GitHubError(f"Could not reach raw.githubusercontent.com: {exc.reason}") from exc


def fetch_pr_config(ref: PullRequestRef, sha: str) -> DiffmeterConfig:
    """Fetches and parses `.diffmeter.toml` from `sha` via the same
    raw-content mechanism used for every other file's blob, so `--pr` mode
    can honor a repo's own ignore/weight policy without a local checkout.
    Returns an empty config if the file doesn't exist at that commit (the
    normal case for most repos), same as `load_config`'s local behavior.

    Deliberately called with the PR's *base* sha, not its head: fetching
    from head would let a PR add or edit `.diffmeter.toml` in the same PR
    to exclude its own changes from scoring -- exactly the kind of gaming
    this tool exists to catch, and directly relevant given this repo's own
    `diffmeter-self-score` CI gate uses `--pr`-equivalent scoring on real
    PRs. Reading from base means the policy that applies is whatever the
    target branch already had, which a PR can't rewrite to exempt itself.
    """
    content = _fetch_blob(ref.owner, ref.repo, sha, CONFIG_FILENAME)
    if content is None:
        return DiffmeterConfig()
    return parse_config(content, f"{ref.owner}/{ref.repo}@{sha}:{CONFIG_FILENAME}")


def _prepare_pr_entry(
    ref: PullRequestRef,
    base_sha: str,
    head_sha: str,
    entry: dict,
    is_ignored_fn: Callable[[str], bool],
    weight_matchers: Optional[WeightMatchers],
) -> _FilePrep:
    status = entry["status"]  # "added" | "removed" | "modified" | "renamed" | "copied" | "changed"
    path = entry["filename"]
    weight = resolve_weight(path, weight_matchers or [])

    if is_ignored_fn(path):
        return _prepare_file(path, None, None, ignored=True, weight=weight)

    previous_path = entry.get("previous_filename") or path
    base_content = None if status == "added" else _fetch_blob(ref.owner, ref.repo, base_sha, previous_path)
    head_content = None if status == "removed" else _fetch_blob(ref.owner, ref.repo, head_sha, path)
    return _prepare_file(path, base_content, head_content, weight=weight)


def score_pull_request(
    ref: PullRequestRef,
    matcher: Optional[pathspec.PathSpec] = None,
    weight_matchers: Optional[WeightMatchers] = None,
    max_workers: Optional[int] = 8,
) -> DiffScore:
    """`matcher` (see diffmeter.config.build_matcher) excludes matching paths
    from scoring without fetching their blob content; `weight_matchers`
    (see diffmeter.config.build_weight_matchers) controls how much matching
    paths count toward the overall score. Both are layered on top of a
    `.diffmeter.toml` this function fetches itself from the PR's base
    commit (see `fetch_pr_config`) -- config first, these CLI-style
    overrides win on a pattern collision, same precedence as local scoring.
    Unlike local mode, ignore matching here is the union of the config
    matcher and `matcher` (each evaluated independently, then OR'd) rather
    than one matcher built from a single concatenated pattern list, so a
    `matcher` pattern can't use gitignore-style negation (`!pattern`) to
    un-ignore something the fetched config already excludes -- a real but
    narrow gap versus local mode's precedence, not expected to matter for
    the common case of a CLI override adding more exclusions, not undoing
    the repo's own policy.

    Per-file blob fetching and AST classification runs concurrently
    (max_workers threads, default 8): this is dominated by network
    round-trips to raw.githubusercontent.com, not CPU, so a PR touching
    many files no longer pays for each file serially. Move detection (see
    diffmeter.scorer's module docstring) then runs once across every
    file's results, so a function moved to a new file in the same PR is
    recognized as moved rather than scored as 100% new/100% deleted. Pass
    max_workers=1 to disable the per-file concurrency.
    """
    pr = _get_json(f"{_API_ROOT}/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}")
    base_sha = pr["base"]["sha"]
    head_sha = pr["head"]["sha"]

    config = fetch_pr_config(ref, base_sha)
    config_matcher = build_matcher(list(config.ignore))
    combined_weight_matchers = build_weight_matchers(list(config.weights.items())) + list(weight_matchers or [])

    def _is_ignored(path: str) -> bool:
        return is_ignored(path, config_matcher) or is_ignored(path, matcher)

    entries = _fetch_pr_files(ref)
    if (max_workers is not None and max_workers <= 1) or len(entries) <= 1:
        preps = [
            _prepare_pr_entry(ref, base_sha, head_sha, e, _is_ignored, combined_weight_matchers) for e in entries
        ]
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            preps = list(
                pool.map(
                    lambda e: _prepare_pr_entry(ref, base_sha, head_sha, e, _is_ignored, combined_weight_matchers),
                    entries,
                )
            )

    return _finalize_diff(preps)
