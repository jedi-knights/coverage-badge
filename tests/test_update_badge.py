"""Functional unit tests for scripts/update_badge.py."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import update_badge as ub

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_LCOV_80 = "SF:src/foo.py\nLF:10\nLH:8\nend_of_record\n"
_COBERTURA_90 = '<coverage line-rate="0.9"></coverage>'
_COVERALLS_75 = json.dumps({"covered_percent": 75.0})
_ISTANBUL_88 = json.dumps({"total": {"lines": {"pct": 88.0}}})
_BADGE = "![coverage](https://img.shields.io/badge/coverage-0%25-red)"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_lcov(tmp_path: Path, lf: int = 10, lh: int = 8) -> Path:
    f = tmp_path / "lcov.info"
    f.write_text(f"LF:{lf}\nLH:{lh}\n")
    return f


def _write_readme(tmp_path: Path, content: str) -> Path:
    f = tmp_path / "README.md"
    f.write_text(content)
    return f


def _setup_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    lf: int = 10,
    lh: int = 8,
    badge_label: str = "coverage",
    fail_below: str | None = None,
    readme_content: str | None = None,
) -> tuple[Path, Path]:
    """Populate tmp_path and set env vars for main() tests."""
    lcov = _write_lcov(tmp_path, lf=lf, lh=lh)
    readme = _write_readme(
        tmp_path,
        readme_content or f"![{badge_label}]({ub.badge_url(0, badge_label)})\n",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("README_PATH", str(readme))
    monkeypatch.setenv("BADGE_LABEL", badge_label)
    monkeypatch.delenv("COVERAGE_FILE", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    if fail_below is not None:
        monkeypatch.setenv("FAIL_BELOW", fail_below)
    else:
        monkeypatch.delenv("FAIL_BELOW", raising=False)
    return lcov, readme


# ---------------------------------------------------------------------------
# parse_lcov
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content,expected",
    [
        ("LF:10\nLH:8\n", 80.0),
        ("LF:5\nLH:5\n", 100.0),
        ("LF:10\nLH:0\n", 0.0),
        (
            "SF:src/a.py\nLF:10\nLH:8\nend_of_record\n"
            "SF:src/b.py\nLF:20\nLH:20\nend_of_record\n",
            28 / 30 * 100,  # (8+20) / (10+20)
        ),
        ("TN:\nSF:src/foo.py\nDA:1,1\nLF:4\nLH:3\nend_of_record\n", 75.0),
    ],
    ids=["basic", "full-coverage", "zero-hits", "multi-file", "ignores-other-records"],
)
def test_parse_lcov(tmp_path, content, expected):
    f = tmp_path / "lcov.info"
    f.write_text(content)
    assert ub.parse_lcov(str(f)) == pytest.approx(expected)


@pytest.mark.parametrize(
    "content,match",
    [
        ("SF:src/foo.py\nend_of_record\n", "No LF:"),
        ("LF:0\n", "No LF:"),
        ("LF:bad\n", "Malformed LF:"),
        ("LF:10\nLH:bad\n", "Malformed LH:"),
        ("LF:5\nLH:10\n", r"LH \(10\) exceeds LF \(5\)"),
    ],
    ids=["no-lf-records", "zero-lf", "malformed-lf", "malformed-lh", "lh-exceeds-lf"],
)
def test_parse_lcov_invalid_content_raises(tmp_path, content, match):
    f = tmp_path / "lcov.info"
    f.write_text(content)
    with pytest.raises(ValueError, match=match):
        ub.parse_lcov(str(f))


def test_parse_lcov_missing_file_raises():
    with pytest.raises(OSError):
        ub.parse_lcov("/nonexistent/lcov.info")


# ---------------------------------------------------------------------------
# parse_cobertura
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "xml_content,expected",
    [
        ('<coverage line-rate="0.9"></coverage>', 90.0),
        ('<coverage line-rate="1.0"></coverage>', 100.0),
        ('<coverage line-rate="0.875"></coverage>', 87.5),
        (
            '<?xml version="1.0"?><root><coverage line-rate="0.5"></coverage></root>',
            50.0,
        ),
    ],
    ids=["basic", "full-coverage", "fractional", "nested-element"],
)
def test_parse_cobertura(tmp_path, xml_content, expected):
    f = tmp_path / "coverage.xml"
    f.write_text(xml_content)
    assert ub.parse_cobertura(str(f)) == pytest.approx(expected)


@pytest.mark.parametrize(
    "xml_content,exc_type,match",
    [
        ('<?xml version="1.0"?><root></root>', ValueError, "No <coverage>"),
        ("<coverage></coverage>", ValueError, "No line-rate"),
        ('<coverage line-rate="bad"></coverage>', ValueError, "Non-numeric"),
        ("<coverage line-rate", ET.ParseError, None),
    ],
    ids=["no-coverage-element", "missing-line-rate", "non-numeric-rate", "bad-xml"],
)
def test_parse_cobertura_raises(tmp_path, xml_content, exc_type, match):
    f = tmp_path / "coverage.xml"
    f.write_text(xml_content)
    kwargs = {"match": match} if match else {}
    with pytest.raises(exc_type, **kwargs):
        ub.parse_cobertura(str(f))


# ---------------------------------------------------------------------------
# parse_coveralls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"covered_percent": 75.0}, 75.0),
        ({"covered_percent": 80}, 80.0),
        ({"covered_percent": 100.0}, 100.0),
    ],
    ids=["float", "integer", "full-coverage"],
)
def test_parse_coveralls(tmp_path, data, expected):
    f = tmp_path / "coveralls.json"
    f.write_text(json.dumps(data))
    assert ub.parse_coveralls(str(f)) == pytest.approx(expected)


@pytest.mark.parametrize(
    "data,match",
    [
        ({}, "No 'covered_percent'"),
        ({"covered_percent": "bad"}, "Non-numeric"),
        # JSON null → Python None — distinguished from missing key by "is null"
        ({"covered_percent": None}, "is null"),
    ],
    ids=["missing-key", "non-numeric", "null-value"],
)
def test_parse_coveralls_raises(tmp_path, data, match):
    f = tmp_path / "coveralls.json"
    f.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=match):
        ub.parse_coveralls(str(f))


# ---------------------------------------------------------------------------
# parse_istanbul
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"total": {"lines": {"pct": 88.0}}}, 88.0),
        ({"total": {"lines": {"pct": 95}}}, 95.0),
        ({"total": {"lines": {"pct": 100.0}}}, 100.0),
    ],
    ids=["float", "integer", "full-coverage"],
)
def test_parse_istanbul(tmp_path, data, expected):
    f = tmp_path / "coverage-summary.json"
    f.write_text(json.dumps(data))
    assert ub.parse_istanbul(str(f)) == pytest.approx(expected)


@pytest.mark.parametrize(
    "data,match",
    [
        ({}, "No 'total'"),
        ({"total": {}}, "'total.lines'"),
        ({"total": {"lines": {}}}, "'total.lines.pct'"),
        ({"total": {"lines": {"pct": "bad"}}}, "Non-numeric"),
        # JSON null → Python None → dict.get() returns None → "missing key" guard
        ({"total": {"lines": {"pct": None}}}, "'total.lines.pct'"),
    ],
    ids=["missing-total", "missing-lines", "missing-pct", "non-numeric", "null-pct"],
)
def test_parse_istanbul_raises(tmp_path, data, match):
    f = tmp_path / "coverage-summary.json"
    f.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=match):
        ub.parse_istanbul(str(f))


# ---------------------------------------------------------------------------
# _find_files
# ---------------------------------------------------------------------------


def test_find_files_at_root(tmp_path):
    (tmp_path / "lcov.info").write_text(_LCOV_80)
    assert len(list(ub._find_files("**/lcov.info", tmp_path))) == 1


def test_find_files_nested(tmp_path):
    (tmp_path / "coverage").mkdir()
    (tmp_path / "coverage" / "lcov.info").write_text(_LCOV_80)
    assert len(list(ub._find_files("**/lcov.info", tmp_path))) == 1


def test_find_files_no_match_returns_empty(tmp_path):
    assert list(ub._find_files("**/lcov.info", tmp_path)) == []


@pytest.mark.parametrize(
    "skip_dir",
    [
        "node_modules",
        ".git",
        "vendor",
        "venv",
        ".venv",
        "site-packages",
        "__pycache__",
        "dist",
        "build",
    ],
)
def test_find_files_skips_excluded_dirs(tmp_path, skip_dir):
    (tmp_path / skip_dir).mkdir()
    (tmp_path / skip_dir / "lcov.info").write_text(_LCOV_80)
    assert list(ub._find_files("**/lcov.info", tmp_path)) == []


# ---------------------------------------------------------------------------
# detect_and_parse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,content,expected",
    [
        ("lcov.info", _LCOV_80, 80.0),
        ("cobertura.xml", _COBERTURA_90, 90.0),
        ("coverage.xml", _COBERTURA_90, 90.0),
        ("coveralls.json", _COVERALLS_75, 75.0),
        ("coverage-summary.json", _ISTANBUL_88, 88.0),
    ],
    ids=["lcov", "cobertura", "coverage-xml", "coveralls", "istanbul"],
)
def test_detect_and_parse_finds_each_format(tmp_path, filename, content, expected):
    (tmp_path / filename).write_text(content)
    assert ub.detect_and_parse(tmp_path) == pytest.approx(expected)


def test_detect_and_parse_lcov_beats_cobertura(tmp_path):
    # lcov is priority 1; coverage.xml is priority 3
    (tmp_path / "lcov.info").write_text("LF:10\nLH:5\n")  # 50%
    (tmp_path / "coverage.xml").write_text(_COBERTURA_90)  # 90%
    assert ub.detect_and_parse(tmp_path) == pytest.approx(50.0)


def test_detect_and_parse_cobertura_beats_coverage_xml(tmp_path):
    # cobertura.xml is priority 2; coverage.xml is priority 3
    (tmp_path / "cobertura.xml").write_text('<coverage line-rate="0.5"></coverage>')
    (tmp_path / "coverage.xml").write_text(_COBERTURA_90)
    assert ub.detect_and_parse(tmp_path) == pytest.approx(50.0)


def test_detect_and_parse_no_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="No coverage file found"):
        ub.detect_and_parse(tmp_path)


def test_detect_and_parse_multiple_matches_warns(tmp_path, caplog):
    """When multiple files match a format tier, a warning must be logged."""
    import logging

    (tmp_path / "coverage").mkdir()
    (tmp_path / "lcov.info").write_text("LF:10\nLH:8\n")
    (tmp_path / "coverage" / "lcov.info").write_text("LF:10\nLH:5\n")
    with caplog.at_level(logging.WARNING, logger="update_badge"):
        ub.detect_and_parse(tmp_path)
    assert any("Multiple" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# infer_format
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,content,expected",
    [
        ("lcov.info", _LCOV_80, "lcov"),
        ("cobertura.xml", _COBERTURA_90, "cobertura"),
        ("coverage.xml", _COBERTURA_90, "cobertura"),
        ("coveralls.json", _COVERALLS_75, "coveralls"),
        ("coverage-summary.json", _ISTANBUL_88, "istanbul"),
        ("LCOV.INFO", _LCOV_80, "lcov"),  # filename match is case-insensitive
    ],
    ids=["lcov", "cobertura", "coverage-xml", "coveralls", "istanbul", "uppercase"],
)
def test_infer_format_by_filename(tmp_path, filename, content, expected):
    f = tmp_path / filename
    f.write_text(content)
    assert ub.infer_format(str(f)) == expected


@pytest.mark.parametrize(
    "content,expected",
    [
        (_COBERTURA_90, "cobertura"),  # XML start
        (_COVERALLS_75, "coveralls"),  # JSON with covered_percent
        (_ISTANBUL_88, "istanbul"),  # JSON with total
        (_LCOV_80, "lcov"),  # plain text → lcov fallback
    ],
    ids=["xml", "coveralls-json", "istanbul-json", "lcov-text-fallback"],
)
def test_infer_format_by_content(tmp_path, content, expected):
    # Non-standard filename forces content inspection
    f = tmp_path / "my-coverage.dat"
    f.write_text(content)
    assert ub.infer_format(str(f)) == expected


@pytest.mark.parametrize(
    "content,match",
    [
        (json.dumps({"unknown": "key"}), "Cannot determine"),
        ("{not valid json}", "Cannot determine"),
    ],
    ids=["unknown-json-keys", "invalid-json"],
)
def test_infer_format_unrecognised_content_raises(tmp_path, content, match):
    f = tmp_path / "my-coverage.json"
    f.write_text(content)
    with pytest.raises(ValueError, match=match):
        ub.infer_format(str(f))


# ---------------------------------------------------------------------------
# _shields_encode / _shields_decode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,expected",
    [
        ("coverage", "coverage"),
        ("branch-coverage", "branch--coverage"),
        ("line_coverage", "line__coverage"),
        ("line coverage", "line_coverage"),
        ("a-b_c d", "a--b__c_d"),
        ("a-b-c", "a--b--c"),
    ],
    ids=["plain", "hyphen", "underscore", "space", "combined", "multi-hyphen"],
)
def test_shields_encode(label, expected):
    assert ub._shields_encode(label) == expected


@pytest.mark.parametrize(
    "encoded,expected",
    [
        ("coverage", "coverage"),
        ("branch--coverage", "branch-coverage"),
        ("line__coverage", "line_coverage"),
        ("line_coverage", "line coverage"),  # single _ → space
        ("a--b__c_d", "a-b_c d"),
    ],
    ids=["plain", "dbl-hyphen", "dbl-underscore", "single-underscore", "combined"],
)
def test_shields_decode(encoded, expected):
    assert ub._shields_decode(encoded) == expected


@pytest.mark.parametrize(
    "label",
    ["coverage", "branch-coverage", "line_coverage", "my coverage", "a-b_c d"],
    ids=["plain", "hyphen", "underscore", "space", "combined"],
)
def test_shields_encode_decode_roundtrip(label):
    assert ub._shields_decode(ub._shields_encode(label)) == label


# ---------------------------------------------------------------------------
# badge_color
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pct,expected",
    [
        (100.0, "brightgreen"),
        (90.0, "brightgreen"),  # lower boundary of brightgreen
        (89.9, "green"),  # just below brightgreen
        (75.0, "green"),  # lower boundary of green
        (74.9, "yellow"),  # just below green
        (60.0, "yellow"),  # lower boundary of yellow
        (59.9, "orange"),  # just below yellow
        (40.0, "orange"),  # lower boundary of orange
        (39.9, "red"),  # just below orange
        (0.0, "red"),
    ],
    ids=["100", "90", "89.9", "75", "74.9", "60", "59.9", "40", "39.9", "0"],
)
def test_badge_color(pct, expected):
    assert ub.badge_color(pct) == expected


# ---------------------------------------------------------------------------
# badge_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pct,label,expected_fragment",
    [
        (95.0, "coverage", "https://img.shields.io/badge/coverage-95.0%25-brightgreen"),
        (87.5, "coverage", "87.5%25"),
        (60.0, "branch-coverage", "branch--coverage"),
        (80.0, "coverage", "80.0%25"),  # one decimal place
    ],
    ids=["full-url", "percentage-encoded", "hyphenated-label", "one-decimal"],
)
def test_badge_url_contains(pct, label, expected_fragment):
    assert expected_fragment in ub.badge_url(pct, label)


def test_badge_url_starts_with_shields_base():
    assert ub.badge_url(80.0, "coverage").startswith("https://img.shields.io/badge/")


def test_badge_url_none_returns_unknown_lightgrey():
    url = ub.badge_url(None, "coverage")
    assert "unknown" in url
    assert "lightgrey" in url


def test_badge_url_none_encodes_label():
    # Hyphenated label must be shields-encoded even for the unknown badge.
    url = ub.badge_url(None, "branch-coverage")
    assert "branch--coverage" in url


# ---------------------------------------------------------------------------
# update_badge
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "readme_content,label,pct,expected_in",
    [
        (
            "![coverage](https://img.shields.io/badge/coverage-0%25-red)\n",
            "coverage",
            87.5,
            "87.5%25",
        ),
        (
            "![branch-coverage]"
            "(https://img.shields.io/badge/branch--coverage-0%25-red)\n",
            "branch-coverage",
            70.0,
            "70.0%25",
        ),
        (
            "![Coverage](https://img.shields.io/badge/Coverage-0%25-red)\n",
            "coverage",
            80.0,
            "80.0%25",
        ),
        (
            "[![coverage](https://img.shields.io/badge/coverage-0%25-red)]"
            "(https://example.com)\n",
            "coverage",
            90.0,
            "90.0%25",
        ),
    ],
    ids=["simple", "hyphenated-label", "case-insensitive", "inside-link"],
)
def test_update_badge_replaces_matching_badge(
    tmp_path, readme_content, label, pct, expected_in
):
    f = _write_readme(tmp_path, readme_content)
    assert ub.update_badge(str(f), pct, label) is True
    assert expected_in in f.read_text()


@pytest.mark.parametrize(
    "readme_content,label",
    [
        ("No badge here.\n", "coverage"),
        ("![other](https://img.shields.io/badge/other-0%25-red)\n", "coverage"),
    ],
    ids=["no-badge", "wrong-label"],
)
def test_update_badge_returns_false_when_unmatched(tmp_path, readme_content, label):
    f = _write_readme(tmp_path, readme_content)
    assert ub.update_badge(str(f), 87.5, label) is False


def test_update_badge_removes_old_url(tmp_path):
    f = _write_readme(tmp_path, f"{_BADGE}\n")
    ub.update_badge(str(f), 87.5, "coverage")
    assert "0%25-red" not in f.read_text()


def test_update_badge_preserves_surrounding_content(tmp_path):
    content = f"# Title\n\n{_BADGE}\n\n## Section\n"
    f = _write_readme(tmp_path, content)
    ub.update_badge(str(f), 90.0, "coverage")
    result = f.read_text()
    assert "# Title" in result and "## Section" in result


def test_update_badge_only_replaces_matching_label(tmp_path):
    other = "![other](https://img.shields.io/badge/other-50%25-green)"
    f = _write_readme(tmp_path, f"{_BADGE}\n{other}\n")
    ub.update_badge(str(f), 90.0, "coverage")
    result = f.read_text()
    assert "90.0%25" in result
    assert "other-50%25-green" in result


def test_update_badge_none_pct_writes_unknown_badge(tmp_path):
    f = _write_readme(tmp_path, f"{_BADGE}\n")
    assert ub.update_badge(str(f), None, "coverage") is True
    result = f.read_text()
    assert "unknown" in result
    assert "lightgrey" in result


def test_update_badge_missing_readme_raises(tmp_path):
    with pytest.raises(OSError):
        ub.update_badge(str(tmp_path / "missing.md"), 80.0, "coverage")


def test_update_badge_leading_hyphen_label(tmp_path):
    """A label starting with '-' encodes to '--<rest>' and must match correctly."""
    label = "-coverage"
    readme_content = f"![{label}](https://img.shields.io/badge/--coverage-0%25-red)\n"
    f = _write_readme(tmp_path, readme_content)
    assert ub.update_badge(str(f), 80.0, label) is True
    assert "80.0%25" in f.read_text()


# ---------------------------------------------------------------------------
# set_output
# ---------------------------------------------------------------------------


def test_set_output_writes_to_github_output_file(tmp_path, monkeypatch):
    output_file = tmp_path / "output"
    output_file.touch()
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    ub.set_output("coverage-percentage", "87.5")
    assert output_file.read_text() == "coverage-percentage=87.5\n"


def test_set_output_appends_multiple_values(tmp_path, monkeypatch):
    output_file = tmp_path / "output"
    output_file.touch()
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    ub.set_output("a", "1")
    ub.set_output("b", "2")
    lines = output_file.read_text().splitlines()
    assert "a=1" in lines and "b=2" in lines


def test_set_output_falls_back_to_logger(monkeypatch, caplog):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    import logging

    with caplog.at_level(logging.INFO, logger="update_badge"):
        ub.set_output("coverage-percentage", "87.5")
    assert any("coverage-percentage=87.5" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _parse_inputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_value,expected",
    [
        ("80", 80.0),
        ("75.5", 75.5),
        ("0", 0.0),
        ("100", 100.0),
        (None, 0.0),  # absent → default 0
    ],
    ids=["integer", "float", "zero", "max", "absent"],
)
def test_parse_inputs_valid(monkeypatch, env_value, expected):
    if env_value is None:
        monkeypatch.delenv("FAIL_BELOW", raising=False)
    else:
        monkeypatch.setenv("FAIL_BELOW", env_value)
    assert ub._parse_inputs("coverage") == pytest.approx(expected)


@pytest.mark.parametrize(
    "env_value",
    ["abc", "-1", "101"],
    ids=["non-numeric", "negative", "over-100"],
)
def test_parse_inputs_invalid_returns_none(monkeypatch, env_value):
    monkeypatch.setenv("FAIL_BELOW", env_value)
    assert ub._parse_inputs("coverage") is None


def test_parse_inputs_empty_label_returns_none(monkeypatch):
    monkeypatch.delenv("FAIL_BELOW", raising=False)
    assert ub._parse_inputs("") is None


# ---------------------------------------------------------------------------
# _resolve_coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,content,expected",
    [
        ("lcov.info", _LCOV_80, 80.0),
        ("coverage.xml", _COBERTURA_90, 90.0),
    ],
    ids=["lcov", "cobertura"],
)
def test_resolve_coverage_explicit_file(tmp_path, filename, content, expected):
    f = tmp_path / filename
    f.write_text(content)
    assert ub._resolve_coverage(str(f)) == pytest.approx(expected)


def test_resolve_coverage_missing_file_returns_none():
    assert ub._resolve_coverage("/nonexistent/lcov.info") is None


def test_resolve_coverage_invalid_json_returns_none(tmp_path):
    # A known filename (coveralls.json) bypasses content sniffing and goes
    # straight to parse_coveralls, which raises json.JSONDecodeError on bad JSON.
    f = tmp_path / "coveralls.json"
    f.write_text("{invalid")
    assert ub._resolve_coverage(str(f)) is None


def test_resolve_coverage_autodetect(tmp_path, monkeypatch):
    # chdir is required here: _resolve_coverage("") calls detect_and_parse()
    # with no root arg, so it searches from cwd. This tests the default-root path.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lcov.info").write_text(_LCOV_80)
    assert ub._resolve_coverage("") == pytest.approx(80.0)


def test_resolve_coverage_no_file_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match="No coverage file found"):
        ub._resolve_coverage("")


@pytest.mark.parametrize(
    "filename,content",
    [
        ("lcov.info", "no lf records here\n"),
        ("coverage.xml", "<not valid xml"),
    ],
    ids=["malformed-lcov", "malformed-xml"],
)
def test_resolve_coverage_malformed_returns_none(tmp_path, filename, content):
    f = tmp_path / filename
    f.write_text(content)
    assert ub._resolve_coverage(str(f)) is None


@pytest.mark.parametrize(
    "filename,content",
    [
        ("lcov.info", "no lf records here\n"),  # ValueError from parse_lcov
        ("coverage.xml", "<not valid xml"),  # ET.ParseError
        ("coveralls.json", "{invalid"),  # json.JSONDecodeError
    ],
    ids=["malformed-lcov", "malformed-xml", "malformed-json"],
)
def test_resolve_coverage_autodetect_malformed_returns_none(
    tmp_path, monkeypatch, filename, content
):
    # Auto-detection finds the file but parsing fails → returns None (error printed).
    monkeypatch.chdir(tmp_path)
    (tmp_path / filename).write_text(content)
    assert ub._resolve_coverage("") is None


# ---------------------------------------------------------------------------
# _parse_coverage_file
# ---------------------------------------------------------------------------


def test_parse_coverage_file_valid(tmp_path):
    f = tmp_path / "lcov.info"
    f.write_text(_LCOV_80)
    assert ub._parse_coverage_file(str(f)) == pytest.approx(80.0)


def test_parse_coverage_file_missing_returns_none():
    assert ub._parse_coverage_file("/nonexistent/lcov.info") is None


def test_parse_coverage_file_malformed_returns_none(tmp_path):
    f = tmp_path / "lcov.info"
    f.write_text("no lf records\n")
    assert ub._parse_coverage_file(str(f)) is None


# ---------------------------------------------------------------------------
# _update_readme_badge
# ---------------------------------------------------------------------------


def test_update_readme_badge_success(tmp_path):
    readme = _write_readme(tmp_path, f"{_BADGE}\n")
    assert ub._update_readme_badge(str(readme), 80.0, "coverage") == 0
    assert "80.0%25" in readme.read_text()


def test_update_readme_badge_missing_readme_returns_1(tmp_path):
    assert ub._update_readme_badge(str(tmp_path / "missing.md"), 80.0, "coverage") == 1


def test_update_readme_badge_none_pct_writes_unknown(tmp_path):
    readme = _write_readme(tmp_path, f"{_BADGE}\n")
    assert ub._update_readme_badge(str(readme), None, "coverage") == 0
    assert "unknown" in readme.read_text()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_happy_path_returns_0(tmp_path, monkeypatch):
    _setup_main(tmp_path, monkeypatch)
    assert ub.main() == 0


def test_main_updates_badge(tmp_path, monkeypatch):
    _, readme = _setup_main(tmp_path, monkeypatch)
    ub.main()
    assert "80.0%25" in readme.read_text()


def test_main_explicit_coverage_file(tmp_path, monkeypatch):
    lcov, _ = _setup_main(tmp_path, monkeypatch)
    monkeypatch.setenv("COVERAGE_FILE", str(lcov))
    assert ub.main() == 0


@pytest.mark.parametrize(
    "lh,fail_below,expected_exit",
    [
        (9, "80", 0),  # 90% > 80%
        (8, "80", 0),  # 80% == 80%  — boundary passes
        (7, "80", 1),  # 70% < 80%
        (8, "0", 0),  # threshold disabled
    ],
    ids=["above-threshold", "at-boundary", "below-threshold", "disabled"],
)
def test_main_threshold(tmp_path, monkeypatch, lh, fail_below, expected_exit):
    _setup_main(tmp_path, monkeypatch, lh=lh, fail_below=fail_below)
    assert ub.main() == expected_exit


def test_main_empty_badge_label_returns_1(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BADGE_LABEL", "")
    monkeypatch.delenv("FAIL_BELOW", raising=False)
    assert ub.main() == 1


def test_main_no_coverage_file_updates_badge_to_unknown(tmp_path, monkeypatch):
    # No coverage files in workspace → badge shows "unknown", returns 0.
    readme = _write_readme(tmp_path, f"![coverage]({ub.badge_url(0, 'coverage')})\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BADGE_LABEL", "coverage")
    monkeypatch.setenv("README_PATH", str(readme))
    monkeypatch.delenv("COVERAGE_FILE", raising=False)
    monkeypatch.delenv("FAIL_BELOW", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert ub.main() == 0
    assert "unknown" in readme.read_text()
    assert "lightgrey" in readme.read_text()


def test_main_no_coverage_file_no_badge_warns_and_returns_0(
    tmp_path, monkeypatch, caplog
):
    # No coverage files and no matching badge — still returns 0 with a warning.
    import logging

    readme = _write_readme(tmp_path, "No badge here.\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BADGE_LABEL", "coverage")
    monkeypatch.setenv("README_PATH", str(readme))
    monkeypatch.delenv("COVERAGE_FILE", raising=False)
    monkeypatch.delenv("FAIL_BELOW", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    with caplog.at_level(logging.WARNING, logger="update_badge"):
        assert ub.main() == 0
    assert any("badge updated to show 'unknown'" in r.message for r in caplog.records)


def test_main_invalid_fail_below_returns_1(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BADGE_LABEL", "coverage")
    monkeypatch.setenv("FAIL_BELOW", "not-a-number")
    assert ub.main() == 1


def test_main_badge_not_found_warns_but_returns_0(tmp_path, monkeypatch):
    _setup_main(tmp_path, monkeypatch, readme_content="No badge in this file.\n")
    assert ub.main() == 0


def test_main_outputs_coverage_percentage(tmp_path, monkeypatch, caplog):
    import logging

    _setup_main(tmp_path, monkeypatch)
    with caplog.at_level(logging.INFO, logger="update_badge"):
        ub.main()
    assert any("80.0%" in r.message for r in caplog.records)


def test_main_update_badge_oserror_returns_1(tmp_path, monkeypatch):
    # Pass a directory as README_PATH so update_badge raises OSError when reading it.
    _write_lcov(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("README_PATH", str(tmp_path))  # directory, not a file
    monkeypatch.setenv("BADGE_LABEL", "coverage")
    monkeypatch.delenv("COVERAGE_FILE", raising=False)
    monkeypatch.delenv("FAIL_BELOW", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert ub.main() == 1


def test_update_badge_cleans_up_temp_file_on_write_error(tmp_path, monkeypatch):
    # Force os.replace to fail to exercise the cleanup branch.
    f = _write_readme(tmp_path, f"{_BADGE}\n")

    def _fail(*_args):
        raise OSError("simulated disk full")

    monkeypatch.setattr(ub.os, "replace", _fail)
    with pytest.raises(OSError, match="simulated disk full"):
        ub.update_badge(str(f), 80.0, "coverage")

    # Only the original README should remain — no leaked temp files.
    assert set(tmp_path.iterdir()) == {f}


# ---------------------------------------------------------------------------
# parse_cobertura — size guard
# ---------------------------------------------------------------------------


def test_parse_cobertura_large_file_raises(tmp_path, monkeypatch):
    """Files larger than 50 MB must be rejected before ET.parse."""
    f = tmp_path / "coverage.xml"
    f.write_text('<coverage line-rate="0.9"></coverage>')
    monkeypatch.setattr(ub.os.path, "getsize", lambda _p: 50 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="too large"):
        ub.parse_cobertura(str(f))


def test_parse_cobertura_rejects_doctype(tmp_path):
    """DOCTYPE declarations must be rejected to prevent entity expansion attacks."""
    f = tmp_path / "coverage.xml"
    f.write_text(
        '<!DOCTYPE foo [<!ENTITY x "y">]><coverage line-rate="0.9"></coverage>'
    )
    with pytest.raises(ValueError, match="DOCTYPE"):
        ub.parse_cobertura(str(f))


# ---------------------------------------------------------------------------
# _infer_format_from_content — size guard + full json.load
# ---------------------------------------------------------------------------


def test_infer_format_from_content_large_file_raises(tmp_path, monkeypatch):
    """Files larger than 50 MB must be rejected during content inspection."""
    f = tmp_path / "my-coverage.dat"
    f.write_text(_COVERALLS_75)
    monkeypatch.setattr(ub.os.path, "getsize", lambda _p: 50 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="too large"):
        ub._infer_format_from_content(str(f))


# ---------------------------------------------------------------------------
# _find_files — relative_to root for skip-dir check
# ---------------------------------------------------------------------------


def test_find_files_root_in_skip_dir_finds_files(tmp_path):
    """Files at the search root must be found even when the root's absolute
    path passes through a skip-dir component."""
    # root path deliberately goes through "vendor/" — a _SKIP_DIRS entry
    myproject = tmp_path / "vendor" / "myproject"
    myproject.mkdir(parents=True)
    (myproject / "lcov.info").write_text(_LCOV_80)
    results = list(ub._find_files("**/lcov.info", myproject))
    assert len(results) == 1


# ---------------------------------------------------------------------------
# set_output — newline injection guard
# ---------------------------------------------------------------------------


def test_set_output_newline_in_name_raises(tmp_path, monkeypatch):
    output_file = tmp_path / "output"
    output_file.touch()
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    with pytest.raises(ValueError, match="newline"):
        ub.set_output("bad\nname", "value")


def test_set_output_newline_in_value_raises(tmp_path, monkeypatch):
    output_file = tmp_path / "output"
    output_file.touch()
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    with pytest.raises(ValueError, match="newline"):
        ub.set_output("name", "bad\nvalue")


def test_set_output_carriage_return_in_name_raises(tmp_path, monkeypatch):
    # \r can corrupt the GitHub Actions output file on Windows runners.
    output_file = tmp_path / "output"
    output_file.touch()
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    with pytest.raises(ValueError, match="newline"):
        ub.set_output("bad\rname", "value")


def test_set_output_carriage_return_in_value_raises(tmp_path, monkeypatch):
    output_file = tmp_path / "output"
    output_file.touch()
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    with pytest.raises(ValueError, match="newline"):
        ub.set_output("name", "bad\rvalue")


# ---------------------------------------------------------------------------
# update_badge — regex must stop at ? (query string preserved)
# ---------------------------------------------------------------------------


def test_update_badge_preserves_query_string(tmp_path):
    """Query parameters appended to a badge URL must survive the replacement."""
    readme_content = (
        "![coverage](https://img.shields.io/badge/coverage-0%25-red?branch=main)\n"
    )
    f = _write_readme(tmp_path, readme_content)
    ub.update_badge(str(f), 87.5, "coverage")
    result = f.read_text()
    assert "87.5%25" in result
    assert "?branch=main" in result


# ---------------------------------------------------------------------------
# badge_url — rounding consistency between color and percentage string
# ---------------------------------------------------------------------------


def test_badge_url_rounding_matches_color():
    """badge_url at 74.95% must round to 75.0 before choosing the color,
    so the URL shows 75.0%25-green (not 75.0%25-yellow)."""
    url = ub.badge_url(74.95, "coverage")
    assert "75.0%25" in url
    assert "green" in url


# ---------------------------------------------------------------------------
# _shields_encode — non-BMP Unicode characters
# ---------------------------------------------------------------------------


def test_shields_encode_non_bmp():
    """Code points above U+FFFF must be percent-encoded as UTF-8 bytes,
    not as a single multi-digit hex escape."""
    # U+1F600 GRINNING FACE — a non-BMP character (4 UTF-8 bytes)
    encoded = ub._shields_encode("\U0001f600")
    # Must be percent-encoded (contains %)
    assert "%" in encoded
    # Must NOT contain the raw character
    assert "\U0001f600" not in encoded
    # Must not be the broken single-codepoint encoding %1F600
    assert encoded != "%1F600"


# ---------------------------------------------------------------------------
# parse_coveralls — null value vs missing key must produce distinct errors
# ---------------------------------------------------------------------------


def test_parse_coveralls_explicit_none_has_distinct_error(tmp_path):
    """covered_percent: null must produce an error different from missing key.

    The match pattern "is null" is used deliberately — it does not appear in
    the current code's "No 'covered_percent' field" message, so the test is a
    true RED until the implementation is updated to say "is null".
    """
    f = tmp_path / "data.json"
    f.write_text(json.dumps({"covered_percent": None}))
    # The message for null must say "is null" (not "No ... field" as for missing key)
    with pytest.raises(ValueError, match="is null"):
        ub.parse_coveralls(str(f))
