"""Check dependency direction and the deliberately limited framework capabilities."""

from __future__ import annotations

import ast
from pathlib import Path


def import_names(tree: ast.AST) -> set[str]:
    """Return absolute imports, including imports inside functions."""
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def violations(root: Path) -> list[str]:
    """Check lower-layer independence, entry points and unsupported capabilities."""
    errors: list[str] = []
    modules = {
        "lcl_fastapi." + ".".join(path.relative_to(root).with_suffix("").parts): path
        for path in root.rglob("*.py")
    }
    graph: dict[str, set[str]] = {}
    for module, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        imports = import_names(tree)
        for imported in imports:
            if imported.split(".")[0] in {"argparse", "click", "typer"}:
                errors.append(f"{path}: use lclang.cli instead of {imported}")
            if not module.startswith("lcl_fastapi.cli.") and imported.startswith("lcl_fastapi.cli"):
                errors.append(f"{path}: lower layers must not depend on CLI adapters")
            if module.startswith("lcl_fastapi.render.") and imported.startswith(
                (
                    "lcl_fastapi.runtime",
                    "lcl_fastapi.service",
                )
            ):
                errors.append(f"{path}: renderers must not control running services")
            if module in {"lcl_fastapi.config", "lcl_fastapi.context"} and imported.startswith(
                (
                    "lcl_fastapi.runtime",
                    "lcl_fastapi.service",
                    "lcl_fastapi.middleware",
                )
            ):
                errors.append(f"{path}: core configuration/context depends on application assembly")
        graph[module] = imports.intersection(modules)
        if path.name == "__init__.py":
            for statement in tree.body:
                if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                    value = statement.value
                    if value is not None:
                        try:
                            ast.literal_eval(value)
                        except ValueError, TypeError:
                            errors.append(
                                f"{path}: package initialization must only curate exports"
                            )
                elif not isinstance(statement, (ast.Import, ast.ImportFrom, ast.Expr)):
                    errors.append(f"{path}: behavioral package initialization is prohibited")
        if path.stem in {"snowflake", "restart", "supervisor", "log_stream"}:
            errors.append(f"{path}: unsupported first-version subsystem")
    for start in graph:
        pending = [(start, [start])]
        while pending:
            current, ancestors = pending.pop()
            for target in graph[current]:
                if target in ancestors:
                    errors.append("Import cycle: " + " -> ".join([*ancestors, target]))
                else:
                    pending.append((target, [*ancestors, target]))
    return sorted(set(errors))


def main() -> None:
    """Fail with all observed architecture violations."""
    errors = violations(Path("src/lcl_fastapi"))
    if errors:
        raise SystemExit("\n".join(errors))


if __name__ == "__main__":
    main()
