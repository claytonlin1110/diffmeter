import pytest

from diffmeter.config import ConfigError, build_matcher, is_ignored, load_config


def test_load_config_returns_empty_when_file_absent(tmp_path):
    config = load_config(tmp_path)
    assert config.ignore == ()


def test_load_config_parses_ignore_list(tmp_path):
    (tmp_path / ".diffmeter.toml").write_text('ignore = ["*.lock", "dist/**"]\n')
    config = load_config(tmp_path)
    assert config.ignore == ("*.lock", "dist/**")


def test_load_config_rejects_non_list_ignore(tmp_path):
    (tmp_path / ".diffmeter.toml").write_text('ignore = "*.lock"\n')
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_load_config_rejects_non_string_items(tmp_path):
    (tmp_path / ".diffmeter.toml").write_text("ignore = [1, 2]\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_load_config_rejects_invalid_toml(tmp_path):
    (tmp_path / ".diffmeter.toml").write_text("this is not [ valid toml\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_build_matcher_returns_none_for_empty_patterns():
    assert build_matcher([]) is None


def test_is_ignored_returns_false_when_matcher_is_none():
    assert is_ignored("anything.py", None) is False


@pytest.mark.parametrize(
    "pattern,path,expected",
    [
        ("*.lock", "package.lock", True),
        ("*.lock", "src/app.py", False),
        ("dist/**", "dist/bundle.js", True),
        ("dist/**", "src/dist_notes.py", False),
        ("package-lock.json", "package-lock.json", True),
        ("**/vendor/**", "third_party/vendor/lib.js", True),
    ],
)
def test_is_ignored_matches_gitignore_style_patterns(pattern, path, expected):
    matcher = build_matcher([pattern])
    assert is_ignored(path, matcher) == expected
