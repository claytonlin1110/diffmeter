"""diffmeter: measure the semantic substance of a diff."""

from diffmeter.scorer import DiffScore, FileScore, Verdict, score_diff, score_file

__version__ = "0.1.0"

__all__ = [
    "DiffScore",
    "FileScore",
    "Verdict",
    "score_diff",
    "score_file",
    "__version__",
]
