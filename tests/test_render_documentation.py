"""Execute the exact canonical renderer examples published for downstream users."""

import re
from pathlib import Path

import pytest


@pytest.mark.documentation
@pytest.mark.parametrize("filename", ["nginx.md", "systemd.md"])
def test_published_renderer_example(filename: str) -> None:
    path = Path(__file__).parents[1] / "docs" / filename
    source = path.read_text(encoding="utf-8")
    examples = re.findall(r"<!-- python-doc-exec -->\s*```python\n(.*?)```", source, re.DOTALL)
    assert examples, f"{path} has no executable example"
    for example in examples:
        exec(compile(example, str(path), "exec"), {})
