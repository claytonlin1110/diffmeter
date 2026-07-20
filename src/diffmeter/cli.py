from __future__ import annotations

import json as json_module
import sys
from pathlib import Path
from typing import Optional

import click

from diffmeter import __version__
from diffmeter.git_utils import GitError, changed_files, is_git_repo, resolve_side
from diffmeter.github_pr import GitHubError, parse_pr_reference, score_pull_request
from diffmeter.scorer import DiffScore, FileScore, score_file


@click.group()
@click.version_option(__version__, prog_name="diffmeter")
def main() -> None:
    """diffmeter: measure how much of a diff is real logic vs. noise."""


@main.command()
@click.argument(
    "path", default=".", type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option(
    "--base", default="HEAD", show_default=True, help="Base revision to compare against."
)
@click.option(
    "--head",
    default=None,
    help="Head revision to compare. Defaults to the working tree (uncommitted changes).",
)
@click.option(
    "--json", "as_json", is_flag=True, help="Print machine-readable JSON instead of a table."
)
@click.option(
    "--min-score",
    type=float,
    default=None,
    help="Exit with status 1 if the overall score is below this threshold (0-100). "
    "Useful as a CI gate against comment-only or whitespace-only PRs.",
)
@click.option(
    "--pr",
    "pr_ref",
    default=None,
    help="Score a GitHub pull request instead of a local diff: owner/repo#123 or a full PR "
    "URL. No local clone needed. Set GITHUB_TOKEN (or GH_TOKEN) to avoid GitHub's low "
    "unauthenticated rate limit. Ignores PATH/--base/--head.",
)
def score(
    path: Path,
    base: str,
    head: Optional[str],
    as_json: bool,
    min_score: Optional[float],
    pr_ref: Optional[str],
) -> None:
    """Score the diff between BASE and HEAD (default: working tree vs HEAD)
    in the repository at PATH (default: current directory)."""
    if pr_ref is not None:
        try:
            ref = parse_pr_reference(pr_ref)
            diff_score = score_pull_request(ref)
        except ValueError as exc:
            raise click.ClickException(str(exc))
        except GitHubError as exc:
            raise click.ClickException(str(exc))
    else:
        repo = path.resolve()
        if not is_git_repo(repo):
            raise click.ClickException(f"{repo} is not inside a git repository")

        try:
            files = changed_files(repo, base, head)
        except GitError as exc:
            raise click.ClickException(str(exc))

        results: list[FileScore] = []
        for cf in files:
            base_content = resolve_side(repo, base, cf.base_path)
            head_content = resolve_side(repo, head, cf.head_path)
            results.append(score_file(cf.display_path, base_content, head_content))

        diff_score = DiffScore(files=results)

    if as_json:
        _print_json(diff_score)
    else:
        _print_table(diff_score)

    overall = diff_score.overall_score
    if min_score is not None and (overall is None or overall < min_score):
        click.echo(f"\nFAIL: overall score {overall} is below --min-score {min_score}", err=True)
        sys.exit(1)


def _print_json(diff_score: DiffScore) -> None:
    payload = {
        "overall_score": diff_score.overall_score,
        "files": [
            {
                "path": r.path,
                "language": r.language,
                "heuristic": r.heuristic,
                "binary": r.binary,
                "added_total": r.added_total,
                "added_trivial": r.added_trivial,
                "removed_total": r.removed_total,
                "removed_trivial": r.removed_trivial,
                "moved": r.moved,
                "score": r.score,
                "note": r.note,
            }
            for r in diff_score.files
        ],
    }
    click.echo(json_module.dumps(payload, indent=2))


def _print_table(diff_score: DiffScore) -> None:
    if not diff_score.files:
        click.echo("No changes between the given revisions.")
        return

    header = f"{'FILE':<45} {'LANG':<12} {'+/-':>9} {'SCORE':>7}"
    click.echo(header)
    click.echo("-" * len(header))
    for r in diff_score.files:
        lang = (r.language or "?") + ("*" if r.heuristic else "")
        changes = f"+{r.added_total}/-{r.removed_total}"
        score_str = "n/a" if r.score is None else f"{r.score:.1f}"
        path_display = r.path if len(r.path) <= 45 else "…" + r.path[-44:]
        click.echo(f"{path_display:<45} {lang:<12} {changes:>9} {score_str:>7}")
    click.echo("-" * len(header))

    overall = diff_score.overall_score
    overall_str = "n/a" if overall is None else f"{overall:.1f}/100"
    click.echo(f"Overall substance score: {overall_str}")
    if any(r.heuristic for r in diff_score.files):
        click.echo("(* = no grammar available for this file type; used a best-effort heuristic)")
    if diff_score.moved:
        click.echo(f"({diff_score.moved} line(s) across the diff look moved rather than newly written)")


if __name__ == "__main__":
    main()
