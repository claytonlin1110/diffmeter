import pytest

from diffmeter.config import (
    ConfigError,
    DEFAULT_WEIGHT,
    build_matcher,
    build_weight_matchers,
    is_ignored,
    load_config,
    parse_weight_flag,
    resolve_weight,
)


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


def test_load_config_returns_empty_weights_when_absent(tmp_path):
    config = load_config(tmp_path)
    assert config.weights == {}


def test_load_config_parses_weights_table(tmp_path):
    (tmp_path / ".diffmeter.toml").write_text('[weights]\n"*.md" = 0.5\n"tests/**" = 0\n')
    config = load_config(tmp_path)
    assert config.weights == {"*.md": 0.5, "tests/**": 0.0}


def test_load_config_rejects_non_table_weights(tmp_path):
    (tmp_path / ".diffmeter.toml").write_text("weights = [1, 2]\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_load_config_rejects_non_numeric_weight_value(tmp_path):
    (tmp_path / ".diffmeter.toml").write_text('[weights]\n"*.md" = "half"\n')
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_load_config_rejects_negative_weight(tmp_path):
    (tmp_path / ".diffmeter.toml").write_text('[weights]\n"*.md" = -1\n')
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_load_config_rejects_boolean_weight_value(tmp_path):
    # bool is a subclass of int in Python, so this needs an explicit check --
    # `true` would otherwise silently pass as weight 1.
    (tmp_path / ".diffmeter.toml").write_text('[weights]\n"*.md" = true\n')
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_resolve_weight_returns_default_when_nothing_matches():
    matchers = build_weight_matchers([("*.md", 0.5)])
    assert resolve_weight("app.py", matchers) == DEFAULT_WEIGHT


def test_resolve_weight_returns_default_for_empty_matchers():
    assert resolve_weight("anything.py", []) == DEFAULT_WEIGHT


def test_resolve_weight_matches_a_pattern():
    matchers = build_weight_matchers([("*.md", 0.5)])
    assert resolve_weight("README.md", matchers) == 0.5


def test_resolve_weight_last_matching_pattern_wins():
    # Same precedence rule as .gitignore: later patterns override earlier
    # ones on a collision, regardless of specificity.
    matchers = build_weight_matchers([("docs/**", 0.3), ("docs/important.md", 0.9)])
    assert resolve_weight("docs/important.md", matchers) == 0.9
    assert resolve_weight("docs/other.md", matchers) == 0.3


def test_resolve_weight_config_then_cli_ordering_lets_cli_win():
    # Simulates the CLI's actual combination: config patterns first, CLI
    # --weight overrides appended after, so CLI always wins on collision --
    # even though a naive {**dict, **dict} merge would NOT guarantee this.
    config_weights = [("*.md", 0.5)]
    cli_weights = [("*.md", 0.9)]
    matchers = build_weight_matchers(config_weights + cli_weights)
    assert resolve_weight("README.md", matchers) == 0.9


@pytest.mark.parametrize(
    "text,expected",
    [
        ("*.md=0.5", ("*.md", 0.5)),
        ("dist/**=0", ("dist/**", 0.0)),
        (" *.md = 2 ", ("*.md", 2.0)),
    ],
)
def test_parse_weight_flag_accepts_valid_forms(text, expected):
    assert parse_weight_flag(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "no-equals-sign",
        "=0.5",
        "*.md=not-a-number",
        "*.md=-1",
    ],
)
def test_parse_weight_flag_rejects_invalid_forms(text):
    with pytest.raises(ConfigError):
        parse_weight_flag(text)
