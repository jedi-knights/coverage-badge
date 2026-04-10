# CLAUDE.md

## Python Version

The project targets Python 3.12 to match the GitHub Actions runners. All version
references must stay in sync:

- `.python-version` → `3.12`
- `pyproject.toml` `requires-python` → `>=3.12`
- `pyproject.toml` `[tool.ruff]` `target-version` → `py312`

Do not use Python 3.13+ syntax or standard library additions.

## Branch Policy

Never commit or push directly to `main`. All changes must be made on a
feature branch and submitted via pull request.

Before making any change:

1. Verify the current branch is not `main` — if it is, create a feature branch first
2. Use `/ship` to push the branch and open a PR
3. Never run `git push` while on `main`
