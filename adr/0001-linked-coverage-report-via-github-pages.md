# ADR-0001: Linked Coverage Report via GitHub Pages

**Status:** Proposed
**Date:** 2026-04-10
**Author:** Omar Crosby

---

## Context

Services like Coveralls and Codecov provide two things:

1. A status badge showing the headline coverage percentage
2. A web interface where you can navigate file-by-file through the codebase and see
   line-by-line coverage highlighting

This action currently provides only (1). Several users have asked whether (2) can be
achieved entirely within GitHub — without depending on an external service — so that
clicking the coverage badge takes you to a navigable HTML coverage report.

This ADR evaluates the available mechanisms within GitHub, selects an approach, and
records the constraints, limitations, and edge cases that affect implementation.

---

## Decision

Extend the action with an **opt-in** `report-url` input. When supplied:

1. The action rewrites the coverage badge in the README as a **linked badge** pointing
   to the URL the user provides:

   ```markdown
   # Before (current behaviour — bare badge)
   ![coverage](https://img.shields.io/badge/coverage-87.5%25-green)

   # After (new opt-in behaviour — linked badge)
   [![coverage](https://img.shields.io/badge/coverage-87.5%25-green)](https://owner.github.io/repo/)
   ```

2. The action optionally checks whether the repository is public or private and emits
   a warning when private-repo Pages may not be available (see
   [Detecting Repository Visibility and Pages Availability](#detecting-repository-visibility-and-pages-availability)).

The action does **not** generate the HTML report itself, and does **not** deploy it to
Pages. Both responsibilities stay with the user's existing workflow — this keeps the
action single-purpose and language-agnostic.

When `report-url` is omitted (the default), the action behaves exactly as it does
today — nothing changes for existing users.

---

## Alternatives Considered

### A. GitHub Pages (selected)

Host the HTML coverage report on the repository's GitHub Pages site
(`https://<owner>.github.io/<repo>/`) and link the badge to that URL.

**Pros:**
- Stable, persistent URL — survives across workflow runs
- Publicly browseable for open-source repos with no authentication required
- Works with every HTML report format that produces a directory of static files
- GitHub provides the hosting; no external service account needed
- The deploy step uses standard, maintained first-party actions
  (`actions/upload-pages-artifact`, `actions/deploy-pages`)

**Cons:**
- Requires the GitHub Pages feature to be enabled for the repository
- Private repositories require **GitHub Enterprise Cloud** — not available on Free,
  Pro, or Team plans for personal or organisational accounts (see Limitations)
- Adds `pages: write` and `id-token: write` permissions to the workflow job
- The user must configure Pages to deploy from GitHub Actions in repository settings
  (Settings → Pages → Source → GitHub Actions)
- The deployed report is not versioned by default; each push overwrites the previous
  report (acceptable for "current state of `main`" but means historical reports are
  not retained unless the user uses separate paths or branches)

### B. GitHub Actions Artifacts

Upload the HTML report directory as a workflow artifact.

**Rejected because:**
- Artifact URLs are per-run and not stable — you cannot link a badge to them because
  the badge would always point to the latest run's artifact ID, which changes on every
  push
- Artifacts require authentication to download; they are not directly browseable in a
  web browser
- Artifacts have a configurable retention period (default 90 days); they are not
  permanent
- There is no public-facing URL structure for artifacts suitable for embedding in a
  README badge link

### C. GitHub Actions Job Summary

Write an HTML coverage table to `$GITHUB_STEP_SUMMARY`, which renders in the
workflow run summary UI.

**Rejected because:**
- The summary URL is per-run and not stable for badge linking
- The summary is only visible to people who can access the Actions UI (requires repo
  access), not publicly browseable for private repos
- The format is constrained to the GitHub Flavored Markdown + limited HTML subset
  that GitHub allows in summaries; it cannot render a full interactive file browser

### D. This Action Deploys to Pages Itself

Make this action responsible for uploading the artifact and deploying to Pages, in
addition to updating the badge.

**Rejected because:**
- It would require the action to know where the HTML report directory is — that
  location differs by tool and language (see Test Runner Support Matrix below)
- It would require `pages: write` and `id-token: write` permissions to be injected
  into the composite action, which creates a large implicit permissions footprint
- Composite actions cannot use `actions/upload-pages-artifact` and
  `actions/deploy-pages` directly inside them because these actions emit workflow
  commands that must run in a job context, not a composite action step context
- Keeping the action single-purpose (badge update only) is the better architecture

---

## Detecting Repository Visibility and Pages Availability

GitHub Actions provides no automatic environment variable for repository visibility.
The approach relies on the GitHub Actions `github` context and the REST API.

### Step 1 — Check repository visibility (no API call needed)

The expression `${{ github.repository_visibility }}` is available in any workflow step
and returns `"public"`, `"private"`, or `"internal"` (enterprise). This requires no
API call and no extra permissions.

This value can be passed into the action as an environment variable:

```yaml
env:
  REPO_VISIBILITY: ${{ github.repository_visibility }}
```

### Step 2 — Check whether Pages is currently enabled (API call)

`GET /repos/{owner}/{repo}` (requires `contents: read`, granted by default) returns a
`has_pages` boolean field. This is simpler than calling the Pages-specific endpoint and
does not require `pages: read` permission.

```bash
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  "https://api.github.com/repos/$GITHUB_REPOSITORY" \
  | python3 -c "import json,sys; print(json.load(sys.stdin).get('has_pages', False))"
```

### Step 3 — Synthesise and warn

There is no GitHub API endpoint that directly reports whether an account plan supports
Pages for private repositories. The plan check must be derived from documented rules:

| Visibility | Plan           | Pages supported? |
|:-----------|:---------------|:----------------|
| public     | any            | Yes              |
| private    | Free           | No               |
| private    | Pro            | No               |
| private    | Team           | No               |
| private    | Enterprise Cloud | Yes            |
| internal   | Enterprise Cloud | Yes            |

Because plan cannot be reliably queried via the API from a workflow, the action takes a
**warn-and-proceed** approach:

- If `report-url` is provided and the repo is public → proceed silently
- If `report-url` is provided and the repo is private → emit a `::warning::` annotation
  explaining that GitHub Pages for private repositories requires Enterprise Cloud, and
  that the deploy step (outside this action) may fail with HTTP 422 if the plan does
  not support it
- The action still writes the linked badge regardless of the warning — the user decides
  whether to proceed

The `actions/deploy-pages` action will produce a clear failure with HTTP 422 if Pages
is unavailable for a private repo. The warning from this action gives the user advance
notice so they can act before the deploy step runs.

---

## Test Runner HTML Report Support Matrix

Different test runners vary in whether they produce a directory of static HTML files
natively, require an extra command, or produce only a single HTML file. All three
cases affect how the Pages deploy step is configured.

### Formats that produce a static HTML directory natively

| Language / Tool | Command | Output directory | Notes |
|:----------------|:--------|:-----------------|:------|
| Python — pytest-cov | `pytest --cov=src --cov-report=html` | `htmlcov/` | No extra step needed; produces an `index.html` plus per-file pages |
| JavaScript — Jest | `jest --coverage --coverageReporters=html` | `coverage/lcov-report/` | Produces `index.html` and per-file pages |
| JavaScript — NYC | `nyc --reporter=html mocha` | `coverage/` | Same structure as Jest |
| Java — JaCoCo | Gradle/Maven plugin | `build/reports/jacoco/test/html/` or similar | Build-tool-specific path; check plugin docs |

### Formats that require an extra step to produce HTML

| Language / Tool | Coverage file | Extra command | Output | Notes |
|:----------------|:-------------|:--------------|:-------|:------|
| Lua — neospec | `coverage/lcov.info` (LCOV) | `genhtml coverage/lcov.info --output-directory coverage/html` | `coverage/html/` | Requires `lcov` system package; must add `sudo apt-get install -y lcov` step on `ubuntu-latest` runners |
| C / C++ — gcov/lcov | `coverage.info` (LCOV) | `genhtml coverage.info --output-directory coverage/html` | `coverage/html/` | Same system dependency |
| Rust — grcov | `lcov.info` (LCOV) | `genhtml lcov.info --output-directory coverage/html` | `coverage/html/` | Same system dependency |

### Formats that produce a single HTML file (not a directory)

| Language / Tool | Coverage file | Command | Output | Edge case |
|:----------------|:-------------|:--------|:-------|:---------|
| Go — go test | `coverage.out` (Go's native format) | `go tool cover -html=coverage.out -o coverage.html` | Single `coverage.html` file | See below |

**Go edge case:** `go tool cover -html` writes a single self-contained HTML file, not
a directory tree. GitHub Pages can serve it only if it is named `index.html` (or if
the badge link points directly to the file path). The workaround:

```yaml
- run: go test ./... -coverprofile=coverage.out
- run: go tool cover -html=coverage.out -o coverage-report/index.html
```

This produces a directory `coverage-report/` with a single `index.html` that Pages can
serve. The report is a flat page showing all files in one view (no inter-file
navigation), which is less rich than pytest-cov's per-file HTML but functional.

An alternative for Go is [gocovsh](https://github.com/orlangure/gocovsh) or converting
via `go test -coverprofile=coverage.out` and then `genhtml` (via lcov) after converting
with `gcov-tool`, but this introduces multiple system dependencies and is not worth the
complexity for the majority of users.

### Formats with no HTML output path

| Format | Notes |
|:-------|:------|
| Coveralls JSON | This is a data interchange format (for the Coveralls service), not a report format. There is no native HTML renderer for it. Users who generate Coveralls JSON are typically sending data to coveralls.io rather than self-hosting a report. The action will warn when `report-url` is provided alongside a Coveralls-format coverage file that it cannot generate HTML from the file itself. |

---

## Implementation Required in This Action

### 1. New `action.yml` input

```yaml
report-url:
  description: >
    URL of the HTML coverage report to link from the badge.
    Typically a GitHub Pages URL (e.g. https://owner.github.io/repo/).
    When set, the badge in the README becomes a clickable link.
    When omitted, the action behaves as before — a bare badge image.
  required: false
  default: ""
```

### 2. Changes to `scripts/update_badge.py`

The badge regex and `update_badge()` function must handle two markdown patterns:

```
# Pattern A — bare badge (existing, no report-url)
![label](https://img.shields.io/badge/...)

# Pattern B — linked badge (new, with report-url)
[![label](https://img.shields.io/badge/...)](https://report-url)
```

The `update_badge()` function needs to:

- Detect Pattern B (linked badge) and update both the inner badge URL and the outer
  link URL in place
- Convert Pattern A → Pattern B when `report-url` is provided and the badge is
  currently bare
- Leave Pattern A unchanged when `report-url` is absent (backwards compatibility)
- Not convert Pattern B → Pattern A when `report-url` is absent (the user may have
  set the link manually and not be using this action to manage it)

### 3. Visibility warning

When `REPORT_URL` is non-empty, `update_badge.py` reads `REPO_VISIBILITY` from the
environment (set by the workflow from `${{ github.repository_visibility }}`). If the
value is `"private"` or `"internal"`, a `::warning::` annotation is emitted.

---

## Example Workflow (Full opt-in usage)

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pytest --cov=src --cov-report=xml --cov-report=html

      - uses: actions/upload-artifact@v4
        with:
          name: coverage-html
          path: htmlcov/

  badge:
    needs: test
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    permissions:
      contents: write       # commit badge to README
      pages: write          # deploy to GitHub Pages
      id-token: write       # OIDC token for Pages deploy
    environment:
      name: github-pages
      url: ${{ steps.deploy.outputs.page_url }}

    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.GH_TOKEN }}

      - run: pytest --cov=src --cov-report=xml --cov-report=html

      - uses: jedi-knights/coverage-badge@v0
        with:
          report-url: https://jedi-knights.github.io/my-repo/
        env:
          REPO_VISIBILITY: ${{ github.repository_visibility }}

      - name: Commit badge
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git diff --quiet README.md || \
            (git add README.md && \
             git commit -m "chore: update coverage badge [skip ci]" && \
             git push)

      - uses: actions/upload-pages-artifact@v3
        with:
          path: htmlcov/

      - id: deploy
        uses: actions/deploy-pages@v4
```

---

## Limitations

1. **Private repositories require GitHub Enterprise Cloud.** GitHub Free, Pro, and Team
   plans do not support GitHub Pages for private repositories. For personal accounts
   on Pro or Team, GitHub Pages is public-only. This action will warn but cannot
   prevent the subsequent deploy step from failing with HTTP 422.

2. **Pages must be pre-configured by a human.** The repository owner must navigate to
   Settings → Pages → Source and select "GitHub Actions" before the first deploy.
   This action cannot perform that configuration programmatically.

3. **The report URL must be known in advance.** The action requires the user to supply
   `report-url` explicitly. The URL for a GitHub Pages site is deterministic
   (`https://<owner>.github.io/<repo>/`) but the action does not derive it
   automatically, to avoid surprises when custom domains or subdirectory deployments
   are in use.

4. **Go produces a flat single-page report, not a navigable tree.** The `go tool cover`
   HTML output shows all source in one page with no file-level navigation. For teams
   wanting per-file navigation in Go, a third-party tool or LCOV + genhtml pipeline is
   required.

5. **LCOV-based HTML (Lua, C/C++, Rust) requires `genhtml` to be installed.** This is
   the `lcov` system package, which must be added as a step in the workflow
   (`sudo apt-get install -y lcov`) before calling `genhtml`. It is not pre-installed
   on GitHub-hosted `ubuntu-latest` runners.

6. **Coveralls JSON has no HTML output path.** Users generating Coveralls JSON are
   targeting the Coveralls service directly, not self-hosting. They cannot use this
   feature without switching to a different coverage output format.

7. **Each push overwrites the Pages report.** GitHub Pages serves a single deployment
   per site. There is no built-in retention of historical coverage reports. Users who
   need per-commit or per-branch coverage history must implement their own pathing
   strategy (e.g., deploying to subdirectories by commit SHA or branch name).

8. **The `has_pages` field cannot confirm plan eligibility.** If a private repo has
   `has_pages: false`, it could mean Pages is not yet enabled (fixable) or that the
   plan does not support it (not fixable without upgrade). The action cannot
   distinguish these two cases from the API response alone.

---

## Consequences

**Positive:**
- Existing users who do not provide `report-url` see zero change in behaviour
- Users of pytest, Jest, or NYC get a zero-extra-dependency path to a navigable
  coverage report hosted entirely within GitHub
- The badge becomes a meaningful navigation entry point rather than a static number
- No external service account (Coveralls, Codecov) is required

**Negative:**
- Users with private repos on Free/Pro/Team plans cannot use this feature without
  upgrading to Enterprise Cloud
- Go, Lua, C/C++, and Rust users need additional workflow steps (system deps or
  format conversion) before the Pages deploy is possible
- The README badge pattern changes from `![...]` to `[![...]]`, which must be
  documented clearly to avoid user confusion during first-time setup
- The `update_badge.py` regex becomes more complex to handle both bare and linked
  badge patterns safely

---

## Related Decisions

- None yet. Future ADRs may address per-branch or per-commit report retention,
  alternative report hosting targets, and support for generating HTML from LCOV
  within this action.
