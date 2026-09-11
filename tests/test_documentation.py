"""Keep the public documentation navigable and execute its exact example source."""

import ast
import asyncio
import inspect
from pathlib import Path

import pytest

from scripts.documentation import documents, link_errors, snippets

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.documentation
def test_public_document_links_and_example_inventory() -> None:
    paths = documents(ROOT)
    assert paths and all(path.is_file() for path in paths)
    errors = [error for path in paths for error in link_errors(path)]
    assert not errors, "\n".join(errors)
    assert sum(len(snippets(path)) for path in paths), "No executable public examples"


@pytest.mark.documentation
@pytest.mark.parametrize("path", documents(ROOT), ids=lambda path: path.name)
def test_public_python_examples(path: Path) -> None:
    # Renderer source has one existing test owner; still inventory it above.
    if path.parent == ROOT / "docs" and path.name in {"nginx.md", "systemd.md"}:
        return
    for example in snippets(path):
        code = compile(example, str(path), "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        result = eval(code, {})
        if inspect.iscoroutine(result):
            asyncio.run(result)
