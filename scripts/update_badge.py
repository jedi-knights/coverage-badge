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
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import unquote

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
    """Sum LF (lines found) and LH (lines hit) records across all source files."""
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


def parse_cobertura(path: str) -> float:
    """Read the line-rate attribute from the root coverage element (0–1 scale)."""
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
    """Read covered_percent from a Coveralls-format JSON file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    pct = data.get("covered_percent")
    if pct is None:
        raise ValueError(f"No 'covered_percent' field in Coveralls file: {path!r}")
    try:
        return float(pct)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Non-numeric covered_percent {pct!r} in {path!r}") from exc


def parse_istanbul(path: str) -> float:
    """Read total.lines.pct from an Istanbul/NYC coverage-summary.json file."""
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
        if not any(part in _SKIP_DIRS for part in path.parts):
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
    """Search the working tree for a supported coverage file and parse it."""
    for fmt, pattern in _CANDIDATES:
        for path in _find_files(pattern, root):
            print(f"Detected {fmt} coverage file: {path}", flush=True)
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
    """Inspect up to 64 KB of file content to determine format when the
    filename is non-standard. The 64 KB cap prevents memory exhaustion from
    accidentally large files; all coverage summary keys are near the top.
    """
    with open(path, encoding="utf-8", errors="replace") as f:
        head = f.read(65536)
    stripped = head.lstrip()
    if stripped.startswith("<"):
        return "cobertura"
    if stripped.startswith("{"):
        try:
            data = json.loads(head)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Cannot determine coverage format for {path!r}: "
                "file starts with '{' but is not valid JSON (first 64 KB)"
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
    """Infer format from filename, falling back to content inspection."""
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
    Remaining special characters are percent-encoded.
    """
    # Escape existing - and _ before mapping space to _.
    encoded = label.replace("-", "--").replace("_", "__").replace(" ", "_")
    return "".join(
        c if (c.isalnum() or c in "-_.~") else f"%{ord(c):02X}" for c in encoded
    )


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

    When pct is None, returns an 'unknown' badge with lightgrey color to
    indicate that no coverage data is available.
    """
    encoded_label = _shields_encode(label)
    if pct is None:
        return f"https://img.shields.io/badge/{encoded_label}-unknown-lightgrey"
    color = badge_color(pct)
    # shields.io requires % to be percent-encoded as %25 in static badge URLs.
    return f"https://img.shields.io/badge/{encoded_label}-{pct:.1f}%25-{color}"


# Matches any shields.io static badge URL. The label group uses -- to handle
# shields.io's double-dash escaping for literal hyphens within a label.
_BADGE_URL_RE = re.compile(
    r"https://img\.shields\.io/badge/(?P<label>(?:[^-]|--)*)(?P<rest>-[^)\s\"']+)",
    re.IGNORECASE,
)


def update_badge(readme_path: str, pct: float | None, label: str) -> bool:
    """Replace the matching badge URL in readme_path.

    Returns True when a replacement was made, False when no matching badge was
    found. Uses an atomic write (temp file + rename) to avoid corrupting the
    README on partial write failures.
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
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(Path(readme_path).resolve().parent))
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

    Falls back to stdout when outside a runner.
    """
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    else:
        # Fallback for local testing outside of a runner.
        print(f"output: {name}={value}", flush=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_fail_below(badge_label: str) -> float | None:
    """Validate badge_label is non-empty and parse the FAIL_BELOW env var.

    Returns the float threshold on success, or None on any validation failure
    (error is printed before returning None).
    """
    if not badge_label:
        print("::error::badge-label must not be empty", flush=True)
        return None
    raw = os.environ.get("FAIL_BELOW", "0").strip() or "0"
    try:
        value = float(raw)
    except ValueError:
        print(
            f"::error::Invalid fail-below value: {raw!r} "
            "— must be a number between 0 and 100",
            flush=True,
        )
        return None
    if not 0 <= value <= 100:
        print(
            f"::error::Invalid fail-below value: {value} — must be between 0 and 100",
            flush=True,
        )
        return None
    return value


def _parse_coverage_file(coverage_file: str) -> float | None:
    """Parse an explicitly supplied coverage file. Returns None on any error."""
    try:
        fmt = infer_format(coverage_file)
        print(f"Using explicit coverage file ({fmt}): {coverage_file}", flush=True)
        return _parse(fmt, coverage_file)
    except json.JSONDecodeError as exc:
        print(f"::error::Failed to parse JSON coverage file: {exc}", flush=True)
    except ET.ParseError as exc:
        print(f"::error::Failed to parse XML coverage file: {exc}", flush=True)
    except (OSError, ValueError) as exc:
        print(f"::error::{exc}", flush=True)
    return None


def _resolve_coverage(coverage_file: str) -> float | None:
    """Parse the coverage percentage from a file or auto-detection.

    When coverage_file is empty, searches the workspace automatically. Raises
    FileNotFoundError if no coverage files are found (caller decides how to
    handle the no-data case). Returns None when a parse error occurs (the error
    is already printed before returning).
    """
    if coverage_file:
        return _parse_coverage_file(coverage_file)
    try:
        return detect_and_parse()
    except FileNotFoundError:
        raise  # propagate "no coverage files in workspace" for main() to handle
    except json.JSONDecodeError as exc:
        print(f"::error::Failed to parse JSON coverage file: {exc}", flush=True)
    except ET.ParseError as exc:
        print(f"::error::Failed to parse XML coverage file: {exc}", flush=True)
    except (OSError, ValueError) as exc:
        print(f"::error::{exc}", flush=True)
    return None


def _update_readme_badge(readme_path: str, pct: float | None, badge_label: str) -> int:
    """Write the coverage badge to the README. Returns 0 on success, 1 on OSError."""
    try:
        found = update_badge(readme_path, pct, badge_label)
    except OSError as exc:
        print(f"::error::{exc}", flush=True)
        return 1
    if found:
        print(f"Badge updated in {readme_path}", flush=True)
    else:
        print(
            f"::warning::No '{badge_label}' badge found in {readme_path}"
            " — nothing to update",
            flush=True,
        )
    return 0


def main() -> int:
    # Empty coverage_file triggers auto-detection.
    coverage_file = os.environ.get("COVERAGE_FILE", "").strip()
    readme_path = os.environ.get("README_PATH", "README.md").strip()
    badge_label = os.environ.get("BADGE_LABEL", "coverage").strip()
    fail_below = _parse_fail_below(badge_label)
    if fail_below is None:
        return 1

    try:
        pct = _resolve_coverage(coverage_file)
    except FileNotFoundError:
        print(
            "::warning::No coverage data found — badge updated to show 'unknown'",
            flush=True,
        )
        return _update_readme_badge(readme_path, None, badge_label)

    if pct is None:
        return 1

    print(f"Coverage: {pct:.1f}%", flush=True)
    set_output("coverage-percentage", f"{pct:.1f}")

    if _update_readme_badge(readme_path, pct, badge_label):
        return 1

    if fail_below > 0 and pct < fail_below:
        print(
            f"::error::Coverage {pct:.1f}% is below the required "
            f"threshold of {fail_below:.1f}%",
            flush=True,
        )
        return 1

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
