"""Keep the public documentation navigable and execute its exact example source."""

import ast
import asyncio
import inspect
import re
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from scripts.documentation import documents, link_errors, snippets

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.documentation
def test_service_series_has_one_complete_ordered_index_and_exact_example_source() -> None:
    index = ROOT / "docs/build-a-service.md"
    chapters = re.findall(
        r"^\d+\. \[[^\]]+\]\((build-a-service/[^)]+)\)",
        index.read_text(encoding="utf-8"),
        re.M,
    )
    assert chapters == [
        "build-a-service/01-catalog.md",
        "build-a-service/02-directory-monitor.md",
        "build-a-service/03-lcl-playground.md",
    ]
    assert {ROOT / "docs" / name for name in chapters} == set(
        ROOT.joinpath("docs/build-a-service").glob("*.md")
    )
    navigation = ROOT.joinpath("mkdocs.yml").read_text(encoding="utf-8")
    for name in chapters:
        assert name in navigation
        source = ROOT.joinpath("docs", name).read_text(encoding="utf-8")
        for filename, code in re.findall(
            r"<!-- example-source: ([^ ]+) -->\s*<!-- python-doc-exec -->\s*```python\n(.*?)```",
            source,
            re.S,
        ):
            assert code == ROOT.joinpath(filename).read_text(encoding="utf-8")


@pytest.mark.documentation
def test_downstream_examples_require_current_release() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    for path in (ROOT / "examples").glob("*/pyproject.toml"):
        example = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
        assert f"lcl-fastapi=={project['version']}" in example["dependencies"], path


@pytest.mark.documentation
def test_pypi_description_links_are_absolute_and_homepage_is_declared() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
    links += re.findall(r'(?:src|href)="([^"]+)"', text)
    assert links
    assert all(urlsplit(link).scheme == "https" and urlsplit(link).netloc for link in links)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["urls"]["Homepage"] == "https://gokurakujoudo.github.io/lcl-fastapi/"


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
