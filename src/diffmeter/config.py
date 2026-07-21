"""Optional per-repo configuration via a `.diffmeter.toml` file in the repo
root: which paths to exclude from scoring entirely (lockfiles, vendored or
generated code, build output), and how much weight different paths should
carry in the overall score.

Excluding files matters for accuracy, not just convenience: a PR that
regenerates a lockfile or a minified bundle can add thousands of lines that
were never actually authored by the contributor, which would otherwise
swamp a real, small, substantive change with noise in either direction.
Weighting is the softer version of the same idea -- down-weight docs or
test fixtures relative to application code instead of excluding them
outright."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import pathspec

CONFIG_FILENAME = ".diffmeter.toml"
DEFAULT_WEIGHT = 1.0


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class DiffmeterConfig:
    ignore: tuple[str, ...] = ()
    weights: Mapping[str, float] = field(default_factory=dict)


def load_config(repo_root: Path) -> DiffmeterConfig:
    """Reads `.diffmeter.toml` from `repo_root` if present. Returns an empty
    config (no ignore patterns, no weights) if the file doesn't exist."""
    config_path = repo_root / CONFIG_FILENAME
    if not config_path.exists():
        return DiffmeterConfig()

    with config_path.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{config_path}: invalid TOML: {exc}") from exc

    patterns = data.get("ignore", [])
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        raise ConfigError(f"{config_path}: 'ignore' must be a list of strings")

    weights = data.get("weights", {})
    if not isinstance(weights, dict) or not all(
        isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
        for k, v in weights.items()
    ):
        raise ConfigError(f"{config_path}: 'weights' must be a table of pattern -> non-negative number")

    return DiffmeterConfig(ignore=tuple(patterns), weights={k: float(v) for k, v in weights.items()})


def build_matcher(patterns: "list[str] | tuple[str, ...]") -> Optional[pathspec.PathSpec]:
    """A gitignore-style matcher for `patterns`, or None if there are none
    (so callers can skip matching entirely when nothing's configured)."""
    if not patterns:
        return None
    return pathspec.PathSpec.from_lines("gitignore", patterns)


def is_ignored(path: str, matcher: Optional[pathspec.PathSpec]) -> bool:
    if matcher is None:
        return False
    return matcher.match_file(path)


WeightMatchers = list[tuple[pathspec.PathSpec, float]]


def build_weight_matchers(weights: Iterable["tuple[str, float]"]) -> WeightMatchers:
    """One matcher per (pattern, weight) pair, built once and reused across
    every file in a run rather than rebuilt per lookup.

    Takes an ordered sequence, not a dict: when combining config-file
    weights with CLI --weight overrides, callers should pass config
    patterns first and CLI patterns last (e.g.
    `list(config.weights.items()) + cli_pairs`) so CLI overrides always win
    on a pattern collision -- a plain `{**config_weights, **cli_weights}`
    dict merge would NOT guarantee that, since Python dict merges keep a
    colliding key's position from the first dict, which can let an
    unrelated later config pattern still win over an "overridden" one.
    """
    return [(pathspec.PathSpec.from_lines("gitignore", [pattern]), weight) for pattern, weight in weights]


def resolve_weight(path: str, matchers: WeightMatchers) -> float:
    """The weight for `path`: last matching pattern wins (same precedence
    rule as .gitignore), or DEFAULT_WEIGHT if nothing matches."""
    result = DEFAULT_WEIGHT
    for matcher, weight in matchers:
        if matcher.match_file(path):
            result = weight
    return result


def parse_weight_flag(text: str) -> "tuple[str, float]":
    """Parses a `PATTERN=NUMBER` --weight CLI argument."""
    if "=" not in text:
        raise ConfigError(f"--weight {text!r} must be in the form PATTERN=NUMBER (e.g. '*.md=0.5')")
    pattern, _, raw_value = text.partition("=")
    pattern = pattern.strip()
    if not pattern:
        raise ConfigError(f"--weight {text!r} has an empty pattern")
    try:
        value = float(raw_value.strip())
    except ValueError as exc:
        raise ConfigError(f"--weight {text!r}: {raw_value.strip()!r} is not a number") from exc
    if value < 0:
        raise ConfigError(f"--weight {text!r}: weight must be non-negative")
    return pattern, value
