"""Write complete coverage diagnostics before enforcing the aggregate threshold."""

from __future__ import annotations

import sys
from pathlib import Path

from coverage import Coverage


def main() -> None:
    """Record line/branch evidence, rejecting incomplete aggregate coverage."""
    if sys.argv[1:] not in ([], ["--platform"]):
        raise SystemExit("Usage: python -m scripts.coverage_report [--platform]")
    Path("reports").mkdir(exist_ok=True)
    measurement = Coverage()
    measurement.load()
    measurement.json_report(outfile="reports/coverage.json")
    measurement.xml_report()
    measurement.html_report()
    total = measurement.report()
    if len(sys.argv) == 1 and total < 100:
        raise SystemExit("Aggregate production line/branch coverage must be exactly 100%")


if __name__ == "__main__":
    main()
