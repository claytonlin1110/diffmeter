from __future__ import annotations

import json as json_module
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import click

from diffmeter import __version__
from diffmeter.config import (
    ConfigError,
    build_matcher,
    build_weight_matchers,
    is_ignored,
    load_config,
    parse_weight_flag,
    resolve_weight,
)
from diffmeter.git_utils import ChangedFile, GitError, changed_files, is_git_repo, resolve_side
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
@click.option(
    "--ignore",
    "ignore_patterns",
    multiple=True,
    metavar="PATTERN",
    help="Gitignore-style pattern for paths to exclude from scoring entirely (e.g. "
    "generated files, vendored code). Repeatable. Local scoring also auto-loads "
    "patterns from a .diffmeter.toml `ignore` list in the repo root; --pr mode only "
    "sees patterns passed explicitly here, since there's no local checkout to read.",
)
@click.option(
    "--weight",
    "weight_args",
    multiple=True,
    metavar="PATTERN=NUMBER",
    help="Gitignore-style pattern and a multiplier for how much matching files count toward "
    "the overall score, e.g. --weight '*.md=0.5'. Repeatable; later ones win on a pattern "
    "collision (same precedence as .gitignore). Local scoring also auto-loads a [weights] "
    "table from .diffmeter.toml (applied before, so CLI values win); --pr mode only sees "
    "weights passed here.",
)
@click.option(
    "--jobs",
    "-j",
    type=int,
    default=8,
    show_default=True,
    help="Score up to this many files concurrently. Set to 1 to disable concurrency "
    "(useful if you need fully deterministic timing, e.g. profiling).",
)
def score(
    path: Path,
    base: str,
    head: Optional[str],
    as_json: bool,
    min_score: Optional[float],
    pr_ref: Optional[str],
    ignore_patterns: tuple[str, ...],
    weight_args: tuple[str, ...],
    jobs: int,
) -> None:
    """Score the diff between BASE and HEAD (default: working tree vs HEAD)
    in the repository at PATH (default: current directory)."""
    try:
        cli_weights = [parse_weight_flag(w) for w in weight_args]
    except ConfigError as exc:
        raise click.ClickException(str(exc))

    if pr_ref is not None:
        try:
            ref = parse_pr_reference(pr_ref)
            matcher = build_matcher(list(ignore_patterns))
            weight_matchers = build_weight_matchers(cli_weights)
            diff_score = score_pull_request(
                ref, matcher=matcher, weight_matchers=weight_matchers, max_workers=jobs
            )
        except ValueError as exc:
            raise click.ClickException(str(exc))
        except GitHubError as exc:
            raise click.ClickException(str(exc))
    else:
        repo = path.resolve()
        if not is_git_repo(repo):
            raise click.ClickException(f"{repo} is not inside a git repository")

        try:
            config = load_config(repo)
        except ConfigError as exc:
            raise click.ClickException(str(exc))
        matcher = build_matcher(list(config.ignore) + list(ignore_patterns))
        weight_matchers = build_weight_matchers(list(config.weights.items()) + cli_weights)

        try:
            files = changed_files(repo, base, head)
        except GitError as exc:
            raise click.ClickException(str(exc))

        def _score_local(cf: ChangedFile) -> FileScore:
            weight = resolve_weight(cf.display_path, weight_matchers)
            if is_ignored(cf.display_path, matcher):
                return score_file(cf.display_path, None, None, ignored=True, weight=weight)
            base_content = resolve_side(repo, base, cf.base_path)
            head_content = resolve_side(repo, head, cf.head_path)
            return score_file(cf.display_path, base_content, head_content, weight=weight)

        if jobs == 1 or len(files) <= 1:
            results = [_score_local(cf) for cf in files]
        else:
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                results = list(pool.map(_score_local, files))

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
                "ignored": r.ignored,
                "weight": r.weight,
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

    show_weights = any(r.weight != 1.0 for r in diff_score.files)
    weight_col = f" {'WEIGHT':>6}" if show_weights else ""
    header = f"{'FILE':<45} {'LANG':<12} {'+/-':>9} {'SCORE':>7}{weight_col}"
    click.echo(header)
    click.echo("-" * len(header))
    for r in diff_score.files:
        lang = (r.language or "?") + ("*" if r.heuristic else "")
        changes = f"+{r.added_total}/-{r.removed_total}"
        score_str = "n/a" if r.score is None else f"{r.score:.1f}"
        path_display = r.path if len(r.path) <= 45 else "…" + r.path[-44:]
        weight_str = f" {r.weight:>6.2f}" if show_weights else ""
        click.echo(f"{path_display:<45} {lang:<12} {changes:>9} {score_str:>7}{weight_str}")
    click.echo("-" * len(header))

    overall = diff_score.overall_score
    overall_str = "n/a" if overall is None else f"{overall:.1f}/100"
    click.echo(f"Overall substance score: {overall_str}")
    if any(r.heuristic for r in diff_score.files):
        click.echo("(* = no grammar available for this file type; used a best-effort heuristic)")
    if diff_score.moved:
        click.echo(f"({diff_score.moved} line(s) across the diff look moved rather than newly written)")
    if show_weights:
        click.echo("(overall score is weighted per-file; WEIGHT shows each file's multiplier)")
    ignored_count = sum(1 for r in diff_score.files if r.ignored)
    if ignored_count:
        click.echo(f"({ignored_count} file(s) excluded via an ignore pattern)")


if __name__ == "__main__":
    main()
