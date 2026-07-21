"""diffmeter: measure the semantic substance of a diff."""

from diffmeter.config import (
    ConfigError,
    DiffmeterConfig,
    build_matcher,
    build_weight_matchers,
    is_ignored,
    load_config,
    parse_weight_flag,
    resolve_weight,
)
from diffmeter.github_pr import GitHubError, PullRequestRef, parse_pr_reference, score_pull_request
from diffmeter.scorer import DiffScore, FileScore, Verdict, score_diff, score_file

__version__ = "0.6.1"

__all__ = [
    "ConfigError",
    "DiffScore",
    "DiffmeterConfig",
    "FileScore",
    "GitHubError",
    "PullRequestRef",
    "Verdict",
    "build_matcher",
    "build_weight_matchers",
    "is_ignored",
    "load_config",
    "parse_pr_reference",
    "parse_weight_flag",
    "resolve_weight",
    "score_diff",
    "score_file",
    "score_pull_request",
    "__version__",
]
