"""The domain layer must not know about the web framework, the database, or the LLM.

This is checked mechanically rather than asserted in a README, because a layering
rule nobody enforces is a layering rule that lasts about three weeks.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

DOMAIN_DIR = Path(__file__).resolve().parents[1] / "intake" / "domain"

FORBIDDEN_PREFIXES = (
    "fastapi",
    "starlette",
    "sqlite3",
    "sqlalchemy",
    "anthropic",
    "httpx",
    "requests",
    "uvicorn",
    "intake.db",
    "intake.api",
    "intake.llm",
    "intake.corpus",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def _domain_files() -> list[Path]:
    return sorted(p for p in DOMAIN_DIR.glob("*.py") if p.name != "__init__.py")


def test_domain_dir_is_not_empty() -> None:
    assert _domain_files(), "no domain modules found -- is the path wrong?"


@pytest.mark.parametrize("path", _domain_files(), ids=lambda p: p.name)
def test_domain_module_has_no_infrastructure_imports(path: Path) -> None:
    offenders = [
        module
        for module in sorted(_imported_modules(path))
        if module.startswith(FORBIDDEN_PREFIXES)
    ]
    assert not offenders, (
        f"{path.name} imports infrastructure: {offenders}. Domain logic must stay "
        f"testable without FastAPI, SQLite, or an API key."
    )
