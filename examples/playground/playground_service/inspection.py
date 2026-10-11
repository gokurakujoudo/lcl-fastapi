"""Render native AST and capture real lclang diagnostics."""

import logging
from dataclasses import asdict

from lclang.error import LclError
from lclang.lang import LclAstNode, to_source
from lclang.lang.runtime import analyze_dependencies
from lclang.logger import LclExceptionFormatter


def tree(node: LclAstNode) -> dict[str, object]:
    return {
        "kind": type(node).__name__,
        "source": to_source(node),
        "span": asdict(node.span),
        "children": [tree(child) for child in node.children()],
    }


def dependencies(definitions: dict[str, LclAstNode]) -> list[dict[str, object]]:
    return [
        {
            "source": name,
            "target": str(reference.name),
            "kind": reference.kind.value,
            "span": asdict(reference.span),
        }
        for name, node in definitions.items()
        for reference in analyze_dependencies(node, scoped_names=definitions)
    ]


def diagnostic(error: Exception) -> dict[str, object]:
    return {
        "type": type(error).__name__,
        "message": str(error),
        "span": asdict(error.span) if isinstance(error, LclError) and error.span else None,
    }


class Trace(logging.Handler):
    """Bound actual diagnostic events; replay never evaluates a subtree again."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[str] = []
        self.truncated = False
        self.setFormatter(LclExceptionFormatter("%(levelname)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        if len(self.events) < 2000:
            self.events.append(self.format(record))
        else:
            self.truncated = True
