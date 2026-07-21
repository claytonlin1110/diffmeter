"""Optional per-repo configuration: which paths to exclude from scoring
entirely (lockfiles, vendored or generated code, build output) via a
`.diffmeter.toml` file in the repo root.

Excluding these matters for accuracy, not just convenience: a PR that
regenerates a lockfile or a minified bundle can add thousands of lines that
were never actually authored by the contributor, which would otherwise
swamp a real, small, substantive change with noise in either direction."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import pathspec

CONFIG_FILENAME = ".diffmeter.toml"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class DiffmeterConfig:
    ignore: tuple[str, ...] = ()


def load_config(repo_root: Path) -> DiffmeterConfig:
    """Reads `.diffmeter.toml` from `repo_root` if present. Returns an empty
    config (no ignore patterns) if the file doesn't exist."""
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
    return DiffmeterConfig(ignore=tuple(patterns))


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
