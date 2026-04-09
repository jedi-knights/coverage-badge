"""Local build tasks for coverage-badge."""

from invoke import task


@task
def lint(c):
    """Run ruff linter and format check."""
    c.run("ruff check .")
    c.run("ruff format --check .")


@task
def fmt(c):
    """Auto-format code with ruff."""
    c.run("ruff format .")
    c.run("ruff check --fix .")


@task
def test(c):
    """Run the test suite."""
    c.run("python3 -m pytest tests/ -v")


@task
def check(c):
    """Run syntax check on the worker script."""
    c.run("python3 -m py_compile scripts/update_badge.py")


@task(pre=[lint, check, test])
def ci(c):
    """Run all CI checks locally (lint + syntax + tests)."""
