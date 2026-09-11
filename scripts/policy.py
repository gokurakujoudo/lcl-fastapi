"""Validate production source size, declarations, and documentation."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path


def inspect_source(path: Path) -> list[str]:
    """Return policy violations for one production Python module."""
    text = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(text, filename=str(path))
    excluded: set[int] = set()
    errors: list[str] = []
    source_lines = text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            excluded.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            for index, child in enumerate(node.body):
                if not (
                    isinstance(child, ast.Expr)
                    and isinstance(child.value, ast.Constant)
                    and isinstance(child.value.value, str)
                ):
                    continue
                previous = node.body[index - 1] if index else None
                if index == 0 or isinstance(previous, (ast.Assign, ast.AnnAssign)):
                    excluded.update(range(child.lineno, (child.end_lineno or child.lineno) + 1))
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        location = f"{path}:{node.lineno}: {node.name}"
        if node.name.startswith("_") and not (
            node.name.startswith("__") and node.name.endswith("__")
        ):
            errors.append(f"{location}: declaration has an underscore prefix")
        docstring = ast.get_docstring(node)
        if not docstring:
            errors.append(f"{location}: missing English rST docstring")
            continue
        if re.search(r"[\u4e00-\u9fff]", docstring):
            errors.append(f"{location}: production docstring must be English")
        if isinstance(node, ast.ClassDef) and any(
            isinstance(decorator, ast.Name)
            and decorator.id == "dataclass"
            or isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "dataclass"
            for decorator in node.decorator_list
        ):
            for field in node.body:
                if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                    if f":param {field.target.id}:" not in docstring:
                        errors.append(f"{location}: missing constructor field {field.target.id}")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parameters = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            parameters.extend(arg for arg in (node.args.vararg, node.args.kwarg) if arg)
            for parameter in parameters:
                if (
                    parameter.arg not in {"self", "cls"}
                    and f":param {parameter.arg}:" not in docstring
                ):
                    errors.append(f"{location}: missing :param {parameter.arg}:")
            if (
                node.returns is not None
                and not (isinstance(node.returns, ast.Constant) and node.returns.value is None)
                and ":returns:" not in docstring
            ):
                errors.append(f"{location}: missing :returns:")
    comments: set[int] = set()
    strings: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if (
            token.type == tokenize.COMMENT
            and not source_lines[token.start[0] - 1][: token.start[1]].strip()
        ):
            comments.add(token.start[0])
        elif token.type == tokenize.STRING:
            strings.update(range(token.start[0], token.end[0] + 1))
    count = sum(
        (bool(line.strip()) and number not in comments or number in strings)
        and number not in excluded
        for number, line in enumerate(source_lines, start=1)
    )
    if count > 200:
        errors.append(f"{path}: {count} production code lines exceed 200")
    return errors


def main() -> None:
    """Fail when production declarations violate the engineering baseline."""
    errors = [
        error
        for path in sorted(Path("src/lcl_fastapi").rglob("*.py"))
        for error in inspect_source(path)
    ]
    if errors:
        raise SystemExit("\n".join(errors))


if __name__ == "__main__":
    main()
