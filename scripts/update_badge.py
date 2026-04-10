#!/usr/bin/env python3
"""
Detect a coverage output file, extract the line coverage percentage,
and update the shields.io badge URL in a README file.

Supported formats (auto-detected in priority order):
  LCOV        **/lcov.info
  Cobertura   **/cobertura.xml, **/coverage.xml
  Coveralls   **/coveralls.json
  Istanbul    **/coverage-summary.json
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import quote, unquote

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


class _GitHubActionsFormatter(logging.Formatter):
    """Format log records as GitHub Actions workflow commands.

    ERROR and WARNING records are prefixed with the corresponding annotation
    command so GitHub renders them in the job summary and step log.
    """

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if record.levelno >= logging.ERROR:
            return f"::error::{msg}"
        if record.levelno >= logging.WARNING:
            return f"::warning::{msg}"
        return msg


def _configure_logging() -> None:
    """Attach a stdout handler with the GitHub Actions formatter to the module logger.

    Called only when the script runs directly so that test imports do not
    install handlers; tests capture records via pytest's caplog fixture instead.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_GitHubActionsFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _parse_lcov_int(field: str, raw: str, path: str) -> int:
    """Parse an integer from an LCOV field value, raising ValueError on failure."""
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(
            f"Malformed {field}: record in {path!r}: {field}:{raw!r}"
        ) from exc


def parse_lcov(path: str) -> float:
    """Sum LF (lines found) and LH (lines hit) records across all source files.

    Args:
        path: Path to the LCOV file to parse.

    Returns:
        Line coverage as a percentage in the range [0, 100].

    Raises:
        ValueError: If LF records are missing, malformed, or LH exceeds LF.
        OSError: If the file cannot be opened.
    """
    lf = lh = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("LF:"):
                lf += _parse_lcov_int("LF", line[3:], path)
            elif line.startswith("LH:"):
                lh += _parse_lcov_int("LH", line[3:], path)
    if not lf:
        raise ValueError(f"No LF: records found in LCOV file: {path!r}")
    if lh > lf:
        raise ValueError(f"Invalid LCOV data in {path!r}: LH ({lh}) exceeds LF ({lf})")
    return lh / lf * 100


_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB


def _check_xml_safety(path: str) -> None:
    """Raise ValueError if the XML file contains a DOCTYPE declaration.

    Coverage files never require DOCTYPE declarations; their presence indicates
    either a non-coverage file or a crafted input that could trigger XML entity
    expansion attacks (billion laughs).

    Args:
        path: Path to the XML file to inspect.

    Raises:
        ValueError: If a DOCTYPE declaration is detected in the first 4096 bytes.
        OSError: If the file cannot be opened.
    """
    with open(path, encoding="utf-8", errors="replace") as f:
        header = f.read(4096)
    if re.search(r"<!DOCTYPE", header, re.IGNORECASE):
        raise ValueError(
            f"DOCTYPE declarations are not permitted in coverage files: {path!r}"
        )


def parse_cobertura(path: str) -> float:
    """Read the line-rate attribute from the root coverage element (0–1 scale).

    Args:
        path: Path to the Cobertura XML file to parse.

    Returns:
        Line coverage as a percentage in the range [0, 100].

    Raises:
        ValueError: If the file exceeds 50 MB, contains a DOCTYPE declaration,
            is missing a ``<coverage>`` element, is missing the ``line-rate``
            attribute, or has a non-numeric ``line-rate`` value.
        xml.etree.ElementTree.ParseError: If the file is not valid XML.
        OSError: If the file cannot be opened.
    """
    size = os.path.getsize(path)
    if size > _MAX_FILE_BYTES:
        raise ValueError(
            f"Coverage file is too large to parse safely: {path!r} ({size} bytes)"
        )
    _check_xml_safety(path)
    tree = ET.parse(path)
    root = tree.getroot()
    # Some generators wrap the root in a different tag; search for <coverage>.
    target = root if root.tag == "coverage" else root.find(".//coverage")
    if target is None:
        raise ValueError(f"No <coverage> element found in {path}")
    rate = target.get("line-rate")
    if rate is None:
        raise ValueError(f"No line-rate attribute on <coverage> element in {path}")
    try:
        return float(rate) * 100
    except ValueError as exc:
        raise ValueError(f"Non-numeric line-rate {rate!r} in {path}") from exc


def parse_coveralls(path: str) -> float:
    """Read covered_percent from a Coveralls-format JSON file.

    Args:
        path: Path to the Coveralls JSON file to parse.

    Returns:
        Line coverage as a percentage in the range [0, 100].

    Raises:
        ValueError: If ``covered_percent`` is missing, null, or non-numeric.
        json.JSONDecodeError: If the file is not valid JSON.
        OSError: If the file cannot be opened.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if "covered_percent" not in data:
        raise ValueError(f"No 'covered_percent' field in Coveralls file: {path!r}")
    pct = data["covered_percent"]
    if pct is None:
        raise ValueError(f"'covered_percent' is null in Coveralls file: {path!r}")
    try:
        return float(pct)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Non-numeric covered_percent {pct!r} in {path!r}") from exc


def parse_istanbul(path: str) -> float:
    """Read total.lines.pct from an Istanbul/NYC coverage-summary.json file.

    Args:
        path: Path to the Istanbul coverage-summary.json file to parse.

    Returns:
        Line coverage as a percentage in the range [0, 100].

    Raises:
        ValueError: If ``total``, ``total.lines``, or ``total.lines.pct`` is
            missing, null, or non-numeric.
        json.JSONDecodeError: If the file is not valid JSON.
        OSError: If the file cannot be opened.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    total = data.get("total")
    if total is None:
        raise ValueError(f"No 'total' key in Istanbul coverage file: {path!r}")
    lines = total.get("lines")
    if lines is None:
        raise ValueError(f"No 'total.lines' key in Istanbul coverage file: {path!r}")
    pct = lines.get("pct")
    if pct is None:
        raise ValueError(
            f"No 'total.lines.pct' key in Istanbul coverage file: {path!r}"
        )
    try:
        return float(pct)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Non-numeric 'total.lines.pct' value {pct!r} in {path!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------

# Checked in priority order; first match wins.
_CANDIDATES = [
    ("lcov", "**/lcov.info"),
    ("cobertura", "**/cobertura.xml"),
    ("cobertura", "**/coverage.xml"),
    ("coveralls", "**/coveralls.json"),
    ("istanbul", "**/coverage-summary.json"),
]

# Directories that are never searched for coverage files.
_SKIP_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        "vendor",
        "venv",
        ".venv",
        "site-packages",
        "__pycache__",
        "dist",
        "build",
    }
)


def _find_files(pattern: str, root: Path = Path(".")) -> Iterator[str]:
    for path in root.glob(pattern):
        # Check only the directory components relative to root (not the filename
        # or any parent directories outside root) to avoid false positives when
        # root itself lives inside a directory whose name matches a skip entry.
        if not any(part in _SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
            yield str(path)


# Parser dispatch table — defined once at module load.
_PARSERS: dict[str, Callable[[str], float]] = {
    "lcov": parse_lcov,
    "cobertura": parse_cobertura,
    "coveralls": parse_coveralls,
    "istanbul": parse_istanbul,
}


def _parse(fmt: str, path: str) -> float:
    return _PARSERS[fmt](path)


def detect_and_parse(root: Path = Path(".")) -> float:
    """Search the working tree for a supported coverage file and parse it.

    Candidates are checked in priority order (see ``_CANDIDATES``). The first
    match within the highest-priority format is used. When multiple files match
    the same format, a warning is logged and the first result is used.

    Args:
        root: Directory to search. Defaults to the current working directory.

    Returns:
        Line coverage as a percentage in the range [0, 100].

    Raises:
        FileNotFoundError: If no supported coverage file is found under ``root``.
        ValueError: If the detected file cannot be parsed.
        OSError: If a matched file cannot be opened.
    """
    for fmt, pattern in _CANDIDATES:
        matches = list(_find_files(pattern, root))
        if len(matches) > 1:
            logger.warning("Multiple %s files found; using %s", fmt, matches[0])
        for path in matches:
            logger.info("Detected %s coverage file: %s", fmt, path)
            return _parse(fmt, path)
    raise FileNotFoundError(
        "No coverage file found. Provide one via the coverage-file input or "
        "generate a supported format: lcov.info, cobertura.xml, coverage.xml, "
        "coveralls.json, or coverage-summary.json."
    )


_FILENAME_TO_FORMAT: dict[str, str] = {
    "lcov.info": "lcov",
    "cobertura.xml": "cobertura",
    "coverage.xml": "cobertura",
    "coveralls.json": "coveralls",
    "coverage-summary.json": "istanbul",
}


def _infer_format_from_content(path: str) -> str:
    """Inspect file content to determine format when the filename is non-standard.

    Files larger than 50 MB are rejected to prevent memory exhaustion. Content
    is read in full so JSON is parsed completely (not truncated).
    """
    size = os.path.getsize(path)
    if size > _MAX_FILE_BYTES:
        raise ValueError(
            f"Coverage file is too large to parse safely: {path!r} ({size} bytes)"
        )
    # Read only the opening bytes for format detection; loading the full file is
    # deferred to the JSON path where schema inspection requires complete content.
    with open(path, encoding="utf-8", errors="replace") as f:
        header = f.read(4096)
    stripped = header.lstrip()
    if stripped.startswith("<"):
        return "cobertura"
    if stripped.startswith("{"):
        with open(path, encoding="utf-8") as f:
            content = f.read()
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Cannot determine coverage format for {path!r}: "
                "file starts with '{' but is not valid JSON"
            ) from exc
        if "covered_percent" in data:
            return "coveralls"
        if "total" in data:
            return "istanbul"
        raise ValueError(
            f"Cannot determine coverage format for {path!r}: "
            "JSON file has neither 'covered_percent' (Coveralls) nor 'total' "
            "(Istanbul). Is this a coverage file?"
        )
    # Content is neither XML nor JSON. LCOV is a line-based format with no
    # magic byte; treat unrecognised text as LCOV and let the parser validate.
    return "lcov"


def infer_format(path: str) -> str:
    """Infer format from filename, falling back to content inspection.

    Recognised filenames (case-insensitive): ``lcov.info``, ``cobertura.xml``,
    ``coverage.xml``, ``coveralls.json``, ``coverage-summary.json``.
    Non-standard filenames trigger content inspection via
    :func:`_infer_format_from_content`.

    Args:
        path: Path to the coverage file.

    Returns:
        One of ``"lcov"``, ``"cobertura"``, ``"coveralls"``, or ``"istanbul"``.

    Raises:
        ValueError: If the format cannot be determined from the file contents.
        OSError: If the file cannot be opened during content inspection.
    """
    name = Path(path).name.lower()
    fmt = _FILENAME_TO_FORMAT.get(name)
    if fmt is not None:
        return fmt
    return _infer_format_from_content(path)


# ---------------------------------------------------------------------------
# Badge helpers
# ---------------------------------------------------------------------------


def _shields_encode(label: str) -> str:
    """Encode a plain-text label for use in a shields.io static badge URL.

    shields.io convention: space → _, - → --, _ → __.
    Remaining special characters are percent-encoded as UTF-8 bytes so that
    non-BMP Unicode (code points > U+FFFF) is encoded correctly.
    """
    # Escape existing - and _ before mapping space to _.
    encoded = label.replace("-", "--").replace("_", "__").replace(" ", "_")
    # quote() encodes everything except unreserved characters (ALPHA, DIGIT,
    # - . _ ~) using UTF-8 percent-encoding, which correctly handles non-BMP
    # characters that would otherwise produce an oversized hex escape.
    return quote(encoded, safe="")


def _shields_decode(label: str) -> str:
    """Decode a shields.io static badge URL label back to plain text.

    Reverses the encoding applied by _shields_encode.
    """
    # Protect doubled escapes before converting single ones.
    decoded = (
        label.replace("--", "\x00")
        .replace("__", "\x01")
        .replace("_", " ")
        .replace("\x00", "-")
        .replace("\x01", "_")
    )
    return unquote(decoded)


def badge_color(pct: float) -> str:
    """Return a shields.io color name for the given percentage.

    Thresholds mirror jedi-knights/neospec and common open-source conventions.

    Args:
        pct: Coverage percentage in the range [0, 100].

    Returns:
        A shields.io color string: ``"brightgreen"``, ``"green"``, ``"yellow"``,
        ``"orange"``, or ``"red"``.
    """
    if pct >= 90:
        return "brightgreen"
    if pct >= 75:
        return "green"
    if pct >= 60:
        return "yellow"
    if pct >= 40:
        return "orange"
    return "red"


def badge_url(pct: float | None, label: str) -> str:
    """Build a static shields.io badge URL for the given percentage and label.

    When ``pct`` is ``None``, returns an ``unknown`` badge with ``lightgrey``
    color to indicate that no coverage data is available.

    Args:
        pct: Coverage percentage in the range [0, 100], or ``None`` for unknown.
        label: Plain-text badge label (e.g. ``"coverage"``). Encoded for
            shields.io using :func:`_shields_encode`.

    Returns:
        A fully-formed ``https://img.shields.io/badge/…`` URL string.
    """
    encoded_label = _shields_encode(label)
    if pct is None:
        return f"https://img.shields.io/badge/{encoded_label}-unknown-lightgrey"
    # Round first so the color threshold and the displayed value agree.
    pct_r = round(pct, 1)
    color = badge_color(pct_r)
    # shields.io requires % to be percent-encoded as %25 in static badge URLs.
    return f"https://img.shields.io/badge/{encoded_label}-{pct_r:.1f}%25-{color}"


# Matches any shields.io static badge URL. The label group uses -- to handle
# shields.io's double-dash escaping for literal hyphens within a label.
_BADGE_URL_RE = re.compile(
    r"https://img\.shields\.io/badge/(?P<label>(?:[^-]|--)*)(?P<rest>-[^)\s\"'?]+)",
    re.IGNORECASE,
)


def update_badge(readme_path: str, pct: float | None, label: str) -> bool:
    """Replace the matching badge URL in readme_path.

    Locates all shields.io static badge URLs whose decoded label matches
    ``label`` (case-insensitive) and replaces them with the URL built by
    :func:`badge_url`. Uses an atomic write (temp file + rename) to avoid
    corrupting the README on partial write failures.

    Args:
        readme_path: Path to the README file to update.
        pct: Coverage percentage for the new badge, or ``None`` for unknown.
        label: Plain-text badge label to match (e.g. ``"coverage"``).

    Returns:
        ``True`` when at least one badge was replaced, ``False`` when no
        matching badge was found.

    Raises:
        OSError: If the README cannot be read, or if the atomic write fails.
    """
    with open(readme_path, encoding="utf-8") as f:
        content = f.read()

    new_url = badge_url(pct, label)
    replacements_made = 0

    def replacer(m: re.Match) -> str:
        nonlocal replacements_made
        if _shields_decode(m.group("label")).lower() == label.lower():
            replacements_made += 1
            return new_url
        return m.group(0)

    updated = _BADGE_URL_RE.sub(replacer, content)
    if replacements_made == 0:
        return False

    # Atomic write: close the fd immediately and open by name to avoid leaking
    # the descriptor if open() raises after mkstemp succeeds.
    tmp_fd, tmp_path = tempfile.mkstemp(
        suffix=".md.tmp", dir=str(Path(readme_path).resolve().parent)
    )
    os.close(tmp_fd)
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(updated)
        os.replace(tmp_path, readme_path)
    except OSError:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    return True


# ---------------------------------------------------------------------------
# GitHub Actions output
# ---------------------------------------------------------------------------


def set_output(name: str, value: str) -> None:
    """Write a GitHub Actions step output.

    Falls back to a ``logger.info`` call when outside a runner (i.e. when
    ``GITHUB_OUTPUT`` is not set).

    Args:
        name: Output variable name. Must not contain ``\\r`` or ``\\n``.
        value: Output value. Must not contain ``\\r`` or ``\\n``.

    Raises:
        ValueError: If ``name`` or ``value`` contains a carriage return or
            newline, which would corrupt the GitHub Actions output file.
    """
    if any(c in name or c in value for c in "\r\n"):
        raise ValueError(
            f"set_output name and value must not contain newlines: "
            f"name={name!r}, value={value!r}"
        )
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    else:
        # Fallback for local testing outside of a runner.
        logger.info("output: %s=%s", name, value)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_inputs(badge_label: str) -> float | None:
    """Validate badge_label is non-empty and parse the FAIL_BELOW env var.

    Returns the float threshold on success, or None on any validation failure
    (error is logged before returning None).
    """
    if not badge_label:
        logger.error("badge-label must not be empty")
        return None
    raw = os.environ.get("FAIL_BELOW", "0").strip() or "0"
    try:
        value = float(raw)
    except ValueError:
        logger.error(
            "Invalid fail-below value: %r — must be a number between 0 and 100", raw
        )
        return None
    if not 0 <= value <= 100:
        logger.error("Invalid fail-below value: %s — must be between 0 and 100", value)
        return None
    return value


def _parse_coverage_file(coverage_file: str) -> float | None:
    """Parse an explicitly supplied coverage file. Returns None on any error."""
    try:
        fmt = infer_format(coverage_file)
        logger.info("Using explicit coverage file (%s): %s", fmt, coverage_file)
        return _parse(fmt, coverage_file)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse JSON coverage file: %s", exc)
    except ET.ParseError as exc:
        logger.error("Failed to parse XML coverage file: %s", exc)
    except (OSError, ValueError) as exc:
        logger.error("%s", exc)
    return None


def _resolve_coverage(coverage_file: str) -> float | None:
    """Parse the coverage percentage from a file or auto-detection.

    When coverage_file is empty, searches the workspace automatically. Raises
    FileNotFoundError if no coverage files are found (caller decides how to
    handle the no-data case). Returns None when a parse error occurs (the error
    is already logged before returning).
    """
    if coverage_file:
        return _parse_coverage_file(coverage_file)
    try:
        return detect_and_parse()
    except FileNotFoundError:
        raise  # propagate "no coverage files in workspace" for main() to handle
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse JSON coverage file: %s", exc)
    except ET.ParseError as exc:
        logger.error("Failed to parse XML coverage file: %s", exc)
    except (OSError, ValueError) as exc:
        logger.error("%s", exc)
    return None


def _update_readme_badge(readme_path: str, pct: float | None, badge_label: str) -> int:
    """Write the coverage badge to the README. Returns 0 on success, 1 on OSError."""
    try:
        found = update_badge(readme_path, pct, badge_label)
    except OSError as exc:
        logger.error("%s", exc)
        return 1
    if found:
        logger.info("Badge updated in %s", readme_path)
    else:
        logger.warning(
            "No '%s' badge found in %s — nothing to update", badge_label, readme_path
        )
    return 0


def main() -> int:
    """Detect coverage, update the README badge, and enforce the threshold.

    Reads all inputs from environment variables:

    - ``COVERAGE_FILE``: explicit path to a coverage file; auto-detects if empty.
    - ``README_PATH``: path to the README to update (default: ``README.md``).
    - ``BADGE_LABEL``: alt-text label of the badge to replace (default: ``coverage``).
    - ``FAIL_BELOW``: minimum coverage percentage; ``0`` disables the check.

    Returns:
        ``0`` on success, ``1`` on any validation or I/O failure, or when
        coverage falls below the ``FAIL_BELOW`` threshold.
    """
    # Empty coverage_file triggers auto-detection.
    coverage_file = os.environ.get("COVERAGE_FILE", "").strip()
    readme_path = os.environ.get("README_PATH", "README.md").strip()
    badge_label = os.environ.get("BADGE_LABEL", "coverage").strip()
    fail_below = _parse_inputs(badge_label)
    if fail_below is None:
        return 1

    try:
        pct = _resolve_coverage(coverage_file)
    except FileNotFoundError:
        logger.warning("No coverage data found — badge updated to show 'unknown'")
        return _update_readme_badge(readme_path, None, badge_label)

    if pct is None:
        return 1

    logger.info("Coverage: %s%%", f"{pct:.1f}")
    set_output("coverage-percentage", f"{pct:.1f}")

    if _update_readme_badge(readme_path, pct, badge_label):
        return 1

    if fail_below > 0 and pct < fail_below:
        logger.error(
            "Coverage %s%% is below the required threshold of %s%%",
            f"{pct:.1f}",
            f"{fail_below:.1f}",
        )
        return 1

    return 0


if __name__ == "__main__":  # pragma: no cover
    _configure_logging()
    sys.exit(main())
