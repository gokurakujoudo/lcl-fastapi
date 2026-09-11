"""Validate built distribution contents against the package release contract."""

from __future__ import annotations

import hashlib
import re
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path


def main() -> None:
    """Check the current version's wheel and sdist without extracting them."""
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    wheel = Path("dist") / f"lcl_fastapi-{version}-py3-none-any.whl"
    source = Path("dist") / f"lcl_fastapi-{version}.tar.gz"
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        required = {
            "lcl_fastapi/__init__.py",
            "lcl_fastapi/py.typed",
            "lcl_fastapi/static/defaults.lclcfg",
            "lcl_fastapi/static/swagger/swagger-ui-bundle.js",
            "lcl_fastapi/static/swagger/swagger-ui.css",
            "lcl_fastapi/static/swagger/favicon-32x32.png",
            "lcl_fastapi/static/swagger/LICENSE",
            "lcl_fastapi/static/swagger/NOTICE.md",
            "lcl_fastapi/static/swagger/NOTICE",
            "lcl_fastapi/static/swagger/swagger-ui-bundle.js.LICENSE.txt",
        }
        if missing := required.difference(names):
            raise SystemExit(f"Wheel missing runtime resources: {sorted(missing)}")
        provenance = archive.read("lcl_fastapi/static/swagger/NOTICE.md").decode("utf-8")
        checksums = re.findall(r"\| ([^|]+) \| `([0-9a-f]{64})` \|", provenance)
        if not checksums:
            raise SystemExit("Swagger provenance has no recorded asset checksums")
        for filename, expected in checksums:
            content = archive.read("lcl_fastapi/static/swagger/" + filename)
            if hashlib.sha256(content).hexdigest() != expected:
                raise SystemExit(f"Bundled Swagger resource checksum differs: {filename}")
        for name in names:
            if not name.startswith(("lcl_fastapi/", f"lcl_fastapi-{version}.dist-info/")):
                raise SystemExit(f"Unexpected wheel content: {name}")
        metadata = BytesParser().parsebytes(
            archive.read(f"lcl_fastapi-{version}.dist-info/METADATA")
        )
        if metadata["Name"] != "lcl-fastapi" or metadata["Version"] != version:
            raise SystemExit("Distribution identity does not match pyproject.toml")
        if metadata["License-Expression"] != "MIT" or metadata["Requires-Python"] != ">=3.14":
            raise SystemExit("License or interpreter metadata does not match the contract")
        if not any(name.endswith(".dist-info/licenses/LICENSE") for name in names):
            raise SystemExit("Wheel does not carry its MIT license")
        if not metadata.get_payload():
            raise SystemExit("Wheel metadata does not include the README")
    with tarfile.open(source) as archive:
        source_names = [
            Path(member.name).parts[1:] for member in archive.getmembers() if member.isfile()
        ]
    allowed = {
        ".gitignore",
        "src",
        "tests",
        "scripts",
        "docs",
        "examples",
        "README.md",
        "LICENSE",
        "CHANGELOG.md",
        "AGENTS.md",
        "pyproject.toml",
        "PKG-INFO",
        "lcl-fastapi spec.md",
    }
    forbidden = {"__pycache__", "venv", ".venv", "run", "logs", "reports", ".pytest_cache"}
    for parts in source_names:
        if not parts or parts[0] not in allowed or forbidden.intersection(parts):
            raise SystemExit(f"Unexpected source distribution content: {'/'.join(parts)}")
    print(f"Validated {wheel.name} and {source.name}")


if __name__ == "__main__":
    main()
