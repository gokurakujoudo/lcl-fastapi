"""Discover canonical guides, executable examples, and local Markdown targets."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


def documents(root: Path) -> list[Path]:
    """Return public Markdown documents, including downstream instructions."""
    return sorted(
        [
            root / "README.md",
            *root.joinpath("docs").rglob("*.md"),
            *root.joinpath("examples").rglob("README.md"),
        ]
    )


def snippets(path: Path) -> list[str]:
    """Extract the exact marked Python source and reject unclassified fences."""
    text = path.read_text(encoding="utf-8")
    examples = re.findall(r"<!-- python-doc-exec -->\s*```python\s*\n(.*?)```", text, re.S)
    python_fences = len(re.findall(r"^```python\s*$", text, re.M))
    fragments = len(re.findall(r"<!-- python-doc-fragment -->\s*```python", text))
    if python_fences != len(examples) + fragments:
        raise ValueError(f"{path}: classify Python fences as executable or incomplete fragments")
    return examples


def anchors(text: str) -> set[str]:
    """Approximate GitHub heading anchors, including repeated-heading suffixes."""
    result: set[str] = set()
    counts: dict[str, int] = {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        number = counts.get(slug, 0)
        result.add(f"{slug}-{number}" if number else slug)
        counts[slug] = number + 1
    result.update(re.findall(r"<a\s+(?:name|id)=[\"\']([^\"\']+)", text))
    return result


def link_errors(path: Path) -> list[str]:
    """Validate file and heading targets without contacting external sites."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    errors: list[str] = []
    for destination in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
        destination = destination.strip().strip("<>")
        parsed = urlsplit(destination)
        if parsed.scheme or parsed.netloc:
            continue
        target = path.parent / unquote(parsed.path) if parsed.path else path
        if not target.exists():
            errors.append(f"{path}: missing relative target {destination}")
        elif parsed.fragment and target.suffix == ".md":
            if unquote(parsed.fragment) not in anchors(target.read_text(encoding="utf-8")):
                errors.append(f"{path}: missing heading {destination}")
    return errors
