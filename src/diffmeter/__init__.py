"""diffmeter: measure the semantic substance of a diff."""

from diffmeter.config import ConfigError, DiffmeterConfig, build_matcher, is_ignored, load_config
from diffmeter.github_pr import GitHubError, PullRequestRef, parse_pr_reference, score_pull_request
from diffmeter.scorer import DiffScore, FileScore, Verdict, score_diff, score_file

__version__ = "0.5.0"

__all__ = [
    "ConfigError",
    "DiffScore",
    "DiffmeterConfig",
    "FileScore",
    "GitHubError",
    "PullRequestRef",
    "Verdict",
    "build_matcher",
    "is_ignored",
    "load_config",
    "parse_pr_reference",
    "score_diff",
    "score_file",
    "score_pull_request",
    "__version__",
]
