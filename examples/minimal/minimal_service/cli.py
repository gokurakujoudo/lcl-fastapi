"""Expose the downstream service's installed console command."""

from lcl_fastapi.cli import run_cli


def main() -> int:
    """Run service operations with a local configuration default."""
    return run_cli(config_path="service.lclcfg", prog="minimal-service", version_text="1.0.0")
