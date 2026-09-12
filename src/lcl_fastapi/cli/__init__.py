"""Public downstream console entrance for pyproject script wrappers."""

from lcl_fastapi.cli.main import main as run_cli

# Unitless public entrance name; console wrappers return its process status.
__all__ = ["run_cli"]
