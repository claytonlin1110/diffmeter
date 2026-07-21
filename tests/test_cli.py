import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from diffmeter.cli import main


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "a.py").write_text("def f():\n    return 1\n")
    _git(r, "add", "a.py")
    _git(r, "commit", "-q", "-m", "initial")
    return r


def test_cli_scores_uncommitted_change_as_trivial(repo: Path):
    (repo / "a.py").write_text("def f():\n    # just a note\n    return 1\n")
    runner = CliRunner()
    result = runner.invoke(main, ["score", str(repo), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["overall_score"] == 0.0
    assert payload["files"][0]["path"] == "a.py"


def test_cli_scores_uncommitted_change_as_substantive(repo: Path):
    (repo / "a.py").write_text("def f():\n    return 2\n")
    runner = CliRunner()
    result = runner.invoke(main, ["score", str(repo), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["overall_score"] == 100.0


def test_cli_min_score_gate_fails_build_on_trivial_change(repo: Path):
    (repo / "a.py").write_text("def f():\n    # just a note\n    return 1\n")
    runner = CliRunner()
    result = runner.invoke(main, ["score", str(repo), "--min-score", "50"])
    assert result.exit_code == 1


def test_cli_min_score_gate_passes_on_substantive_change(repo: Path):
    (repo / "a.py").write_text("def f():\n    return 2\n")
    runner = CliRunner()
    result = runner.invoke(main, ["score", str(repo), "--min-score", "50"])
    assert result.exit_code == 0


def test_cli_compares_two_commits(repo: Path):
    (repo / "a.py").write_text("def f():\n    return 2\n")
    _git(repo, "commit", "-q", "-am", "change return value")
    runner = CliRunner()
    result = runner.invoke(main, ["score", str(repo), "--base", "HEAD~1", "--head", "HEAD", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["overall_score"] == 100.0


def test_cli_reports_no_changes(repo: Path):
    runner = CliRunner()
    result = runner.invoke(main, ["score", str(repo)])
    assert result.exit_code == 0
    assert "No changes" in result.output


def test_cli_rejects_non_git_directory(tmp_path: Path):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    runner = CliRunner()
    result = runner.invoke(main, ["score", str(not_a_repo)])
    assert result.exit_code != 0
    assert "not inside a git repository" in result.output


def test_cli_ignore_flag_excludes_matching_file(repo: Path):
    (repo / "a.py").write_text("def f():\n    return 2\n")
    (repo / "generated.lock").write_text("v1\n")
    _git(repo, "add", "generated.lock")
    runner = CliRunner()
    result = runner.invoke(main, ["score", str(repo), "--ignore", "*.lock", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    by_path = {f["path"]: f for f in payload["files"]}
    assert by_path["generated.lock"]["ignored"] is True
    assert by_path["generated.lock"]["score"] is None
    assert by_path["a.py"]["ignored"] is False
    # the ignored file must not affect the overall score
    assert payload["overall_score"] == 100.0


def test_cli_loads_ignore_patterns_from_config_file(repo: Path):
    (repo / ".diffmeter.toml").write_text('ignore = ["*.lock"]\n')
    _git(repo, "add", ".diffmeter.toml")
    _git(repo, "commit", "-q", "-m", "add config")
    (repo / "generated.lock").write_text("v1\n")
    _git(repo, "add", "generated.lock")
    runner = CliRunner()
    result = runner.invoke(main, ["score", str(repo), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["files"][0]["path"] == "generated.lock"
    assert payload["files"][0]["ignored"] is True
    assert payload["overall_score"] is None


def test_cli_rejects_invalid_config_file(repo: Path):
    (repo / ".diffmeter.toml").write_text("not [ valid toml\n")
    runner = CliRunner()
    result = runner.invoke(main, ["score", str(repo)])
    assert result.exit_code != 0
    assert ".diffmeter.toml" in result.output
