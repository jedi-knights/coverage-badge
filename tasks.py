"""Local build tasks for coverage-badge."""

from invoke import Collection, task


@task
def lint(c):
    """Run ruff linter and format check."""
    c.run("uv run ruff check .")
    c.run("uv run ruff format --check .")


@task
def fmt(c):
    """Auto-format code with ruff."""
    c.run("uv run ruff check --fix .")
    c.run("uv run ruff format .")


@task
def test(c):
    """Run the test suite."""
    c.run("uv run pytest")


@task
def check(c):
    """Run syntax check on the worker script."""
    c.run("uv run python -m py_compile scripts/update_badge.py")


@task(pre=[lint, check, test])
def ci(c):
    """Run all CI checks locally (lint + syntax + tests)."""
    pass


ns = Collection()
ns.add_task(lint, aliases=["l"])
ns.add_task(fmt, aliases=["f"])
ns.add_task(test, aliases=["t"])
ns.add_task(check, aliases=["c"])
ns.add_task(ci, default=True)
