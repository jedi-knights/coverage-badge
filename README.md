# coverage-badge

A language-agnostic GitHub Action that reads a coverage output file and updates the shields.io badge in your README.

![CI](https://github.com/jedi-knights/coverage-badge/actions/workflows/ci.yml/badge.svg?branch=main)
![Release](https://github.com/jedi-knights/coverage-badge/actions/workflows/release.yml/badge.svg?branch=main)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![coverage](https://img.shields.io/badge/coverage-95.9%25-brightgreen)

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
  - [Keep the badge in sync with main](#keep-the-badge-in-sync-with-main)
- [Configuration](#configuration)
- [Outputs](#outputs)
- [Supported Formats](#supported-formats)
- [Badge Setup](#badge-setup)
  - [First-time setup](#first-time-setup)
  - [Token requirements](#token-requirements)
  - [Color thresholds](#color-thresholds)
  - [Multiple badges](#multiple-badges)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

Most coverage tools write a report file. This action reads that file, extracts the line coverage percentage, and rewrites the shields.io badge URL in your README — keeping your badge accurate without committing generated files or running a separate badge service.

It works with any language that produces LCOV, Cobertura XML, Coveralls JSON, or Istanbul/NYC JSON output. The file is detected automatically; you do not need to specify the format.

## Features

- Auto-detects coverage files — no format configuration required
- Supports LCOV, Cobertura XML, Coveralls JSON, and Istanbul/NYC JSON
- Updates the shields.io badge in any README without an external service
- Optional threshold gate — fails the job if coverage drops below a minimum
- Single output (`coverage-percentage`) for use in downstream steps
- No dependencies beyond Python 3, which is pre-installed on all GitHub-hosted runners

## Requirements

- A GitHub Actions workflow running on a GitHub-hosted runner (or a self-hosted runner with Python 3.8+)
- A coverage file produced by your test suite in one of the [supported formats](#supported-formats)
- A shields.io static badge in your README (see [Badge Setup](#badge-setup))
- For self-hosted runners: `python3` must be available in `PATH`
- For local development: Python 3.12 and [uv](https://docs.astral.sh/uv/)

## Installation

Reference the action in a workflow step:

```yaml
- uses: jedi-knights/coverage-badge@v1
```

No additional setup is required. Python 3 is pre-installed on all GitHub-hosted runners. See [Usage](#usage) for complete workflow examples.

## Usage

### Minimal — auto-detect the coverage file

```yaml
steps:
  - uses: actions/checkout@v4

  - name: Run tests
    run: pytest --cov=src --cov-report=xml   # produces coverage.xml

  - uses: jedi-knights/coverage-badge@v1
```

### With a threshold and explicit file

```yaml
- uses: jedi-knights/coverage-badge@v1
  with:
    coverage-file: coverage/lcov.info
    fail-below: "80"
```

### Capture the percentage in a later step

```yaml
- uses: jedi-knights/coverage-badge@v1
  id: badge

- run: echo "Coverage is ${{ steps.badge.outputs.coverage-percentage }}%"
```

### After neospec (Neovim / Lua)

```yaml
- uses: jedi-knights/neospec@v1
  with:
    formats: console,lcov          # produces coverage/lcov.info

- uses: jedi-knights/coverage-badge@v1
  with:
    fail-below: "80"
```

### After pytest-cov (Python)

```yaml
- run: pytest --cov=src --cov-report=xml   # produces coverage.xml

- uses: jedi-knights/coverage-badge@v1
  with:
    fail-below: "75"
```

### After go test (Go)

```yaml
- run: go test ./... -coverprofile=coverage/lcov.info

- uses: jedi-knights/coverage-badge@v1
```

### After Istanbul / NYC (JavaScript / TypeScript)

```yaml
- run: npx jest --coverage --coverageReporters=json-summary
  # produces coverage/coverage-summary.json

- uses: jedi-knights/coverage-badge@v1
```

### Keep the badge in sync with main

Run the badge update only on pushes to `main` — not on pull requests. Feature branches would each
try to commit a badge update, causing conflicts and stale values. A dedicated job with an `if`
condition isolates the update to the branch whose coverage actually matters.

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        run: pytest --cov=src --cov-report=xml

  badge:
    needs: test
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}   # see Token requirements below
      - name: Run tests
        run: pytest --cov=src --cov-report=xml
      - uses: jedi-knights/coverage-badge@v1
      - name: Commit badge
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git diff --quiet README.md || (git add README.md && git commit -m "chore: update coverage badge [skip ci]" && git push)
```

> **Note:** Include `[skip ci]` in the commit message. Without it, the badge commit triggers
> another CI run, which triggers another badge commit — an infinite loop.

## Configuration

| Input | Default | Description |
|:---|:---|:---|
| `coverage-file` | _(auto-detect)_ | Explicit path to a coverage file. If omitted, the action searches the working directory for supported files in priority order. |
| `readme-path` | `README.md` | Path to the README file containing the badge to update. |
| `badge-label` | `coverage` | The alt-text label of the badge to update — the text inside `![...]`. Must match your badge exactly. |
| `fail-below` | `"0"` | Minimum required coverage percentage. `"0"` disables the check. |

## Outputs

| Output | Description |
|:---|:---|
| `coverage-percentage` | Coverage percentage as a bare number, e.g. `"87.5"`. Only written when the action succeeds; absent on parse failure. |

## Supported Formats

The action searches for files in the following priority order. The first match is used.

| Priority | File pattern | Format | Common source |
|:---|:---|:---|:---|
| 1 | `**/lcov.info` | LCOV | Go (`go test -coverprofile`), Lua (neospec), C/C++ (gcov/lcov), Rust (grcov) |
| 2 | `**/cobertura.xml` | Cobertura XML | Python (`pytest-cov --cov-report=xml`), Java (JaCoCo) |
| 3 | `**/coverage.xml` | Cobertura XML | Python (`pytest-cov` default output name) |
| 4 | `**/coveralls.json` | Coveralls JSON | Any tool targeting the Coveralls API |
| 5 | `**/coverage-summary.json` | Istanbul JSON | JavaScript/TypeScript (Jest, NYC) |

Vendor directories are never searched: `node_modules`, `.git`, `vendor`, `venv`, `.venv`, `dist`, `build`.

To use a file with a non-standard name or path, set the `coverage-file` input explicitly.

## Badge Setup

### First-time setup

Add a placeholder badge to your README before the first run. The action matches the badge by its
alt-text label and rewrites the URL — the starting URL does not matter, but the label must match
the `badge-label` input (default: `coverage`):

```markdown
![coverage](https://img.shields.io/badge/coverage-95.9%25-brightgreen)
```

On the first push to `main` after setup, the action replaces `0%` with the real percentage and
updates the color automatically. All subsequent runs keep it current.

### Token requirements

The badge job commits a change directly to `main`. Whether that succeeds depends on your branch
protection configuration.

| Scenario | Token to use |
|:---|:---|
| No branch protection on `main` | `secrets.GITHUB_TOKEN` |
| Branch protection ruleset or classic protection requiring a pull request | A PAT with `repo` scope stored as `GH_TOKEN` |

**Why `GITHUB_TOKEN` fails with branch protection:**

`GITHUB_TOKEN` is a short-lived token scoped to the workflow run. It cannot bypass branch
protection rules. If your `main` branch requires all changes to come through a pull request,
the push will be rejected:

```
remote: error: GH013: Repository rule violations found for refs/heads/main.
remote: - Changes must be made through a pull request.
```

**How to set up a PAT that can bypass the ruleset:**

1. Go to **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens**
2. Create a new token scoped to the repository with **Contents: Read and write** permission
3. The token must belong to a GitHub user who is listed as a bypass actor on the ruleset —
   repository admins and organization admins are granted bypass by default on the standard
   "Protect main" ruleset pattern
4. Add the token as a repository secret: **Settings → Secrets and variables → Actions →
   New repository secret**, and name it `GH_TOKEN`
5. Reference it in the `badge` job's checkout step:

```yaml
- uses: actions/checkout@v4
  with:
    token: ${{ secrets.GH_TOKEN }}
```

The `git push` in the commit step inherits the credentials from the checkout, so no additional
token configuration is needed for the push itself.

### Color thresholds

| Coverage | Color |
|:---|:---|
| ≥ 90% | `brightgreen` |
| ≥ 75% | `green` |
| ≥ 60% | `yellow` |
| ≥ 40% | `orange` |
| < 40% | `red` |

### Multiple badges

If your README contains badges with different labels (e.g. `coverage` and `branch-coverage`), run the action twice with different `badge-label` values:

```yaml
- uses: jedi-knights/coverage-badge@v1
  with:
    coverage-file: coverage/lcov.info
    badge-label: coverage

- uses: jedi-knights/coverage-badge@v1
  with:
    coverage-file: coverage/branch.info
    badge-label: branch-coverage
```

## Development

### Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/jedi-knights/coverage-badge
cd coverage-badge
uv sync
```

### Build tasks

Common tasks are available via [invoke](https://www.pyinvoke.org/):

```bash
uv run invoke lint      # ruff linter + format check
uv run invoke fmt       # auto-format with ruff
uv run invoke test      # run the test suite
uv run invoke check     # syntax check the worker script
uv run invoke ci        # run all checks (lint + check + tests)
```

### Running the script locally

All inputs are read from environment variables, making it straightforward to test outside of a runner:

```bash
COVERAGE_FILE=coverage/lcov.info \
README_PATH=README.md \
BADGE_LABEL=coverage \
FAIL_BELOW=0 \
python3 scripts/update_badge.py
```

### Project layout

```
action.yml                  Action definition and input/output schema
scripts/
  update_badge.py           Worker script: detect, parse, and update
tasks.py                    Invoke build tasks
pyproject.toml              Project metadata and tool configuration
```

## Contributing

Contributions are welcome. Please open an issue before starting significant work so we can discuss the approach.

When adding support for a new coverage format:

1. Add a `parse_<format>` function in `scripts/update_badge.py`
2. Add an entry to `_CANDIDATES` with the glob pattern and format key
3. Add the format key to the `_parse` dispatcher
4. Update the [Supported Formats](#supported-formats) table in this README

## License

[MIT](./LICENSE)

---

<div align="center">
Made for the open-source community by <a href="https://github.com/jedi-knights">Jedi Knights</a>
</div>
