"""Minimal git plumbing: enumerate changed files between two revisions (or a
revision and the working tree) and fetch blob content at each side."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class GitError(RuntimeError):
    pass


def _run(args: list[str], cwd: Path) -> bytes:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, check=True
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(stderr or f"git {' '.join(args)} failed") from exc
    return result.stdout


@dataclass(frozen=True)
class ChangedFile:
    status: str  # git's raw status letter: A, M, D, R100, ...
    base_path: Optional[str]
    head_path: Optional[str]

    @property
    def display_path(self) -> str:
        return self.head_path or self.base_path or "<unknown>"


def changed_files(repo: Path, base: str, head: Optional[str]) -> list[ChangedFile]:
    """Files changed between `base` and `head`. head=None compares against
    the working tree (uncommitted changes, staged and unstaged)."""
    args = ["diff", "--name-status", "-M", base]
    if head is not None:
        args.append(head)
    out = _run(args, repo).decode("utf-8", errors="replace")

    files: list[ChangedFile] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") or status.startswith("C"):
            files.append(ChangedFile(status, parts[1], parts[2]))
        elif status == "A":
            files.append(ChangedFile(status, None, parts[1]))
        elif status == "D":
            files.append(ChangedFile(status, parts[1], None))
        else:
            files.append(ChangedFile(status, parts[1], parts[1]))
    return files


def blob_at_rev(repo: Path, rev: str, path: str) -> Optional[bytes]:
    """File content at a given revision, or None if the path doesn't exist
    there."""
    try:
        return _run(["show", f"{rev}:{path}"], repo)
    except GitError:
        return None


def working_tree_content(repo: Path, path: str) -> Optional[bytes]:
    full = repo / path
    if not full.exists():
        return None
    return full.read_bytes()


def resolve_side(repo: Path, rev: Optional[str], path: Optional[str]) -> Optional[bytes]:
    """Content of `path` on one side of the diff. rev=None means the working
    tree; path=None means the file doesn't exist on this side."""
    if path is None:
        return None
    if rev is None:
        return working_tree_content(repo, path)
    return blob_at_rev(repo, rev, path)


def is_git_repo(path: Path) -> bool:
    try:
        _run(["rev-parse", "--is-inside-work-tree"], path)
        return True
    except GitError:
        return False
