"""Exercise the public takeover baseline through native configuration composition."""

from pathlib import Path

import pytest
from lclang.error import LclError
from lclang.logger import resolve_logger_config

from lcl_fastapi.sources import configuration_frame


def test_public_takeover_baseline_names_are_stable() -> None:
    from lcl_fastapi import DEFAULT_TAKEOVER_LOGGERS

    assert DEFAULT_TAKEOVER_LOGGERS == (
        "fastapi",
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "gunicorn",
        "gunicorn.error",
        "gunicorn.access",
    )


@pytest.mark.parametrize(
    "expression", [None, '[*DEFAULT_TAKEOVER_LOGGERS, "business"]', '["business"]', "[]"]
)
async def test_takeover_defaults_are_available_and_replaceable(
    tmp_path: Path, expression: str | None
) -> None:
    from lcl_fastapi import DEFAULT_TAKEOVER_LOGGERS

    path = tmp_path / "service.lclcfg"
    text = 'app.name: "takeover"\napp.version: "1"\napp.target: "app:service"\n'
    if expression is not None:
        text += f"logger.takeover_loggers: {expression}\n"
    path.write_text(text, encoding="utf-8")
    for role in ("controller", "worker"):
        async with configuration_frame(path, 123, logger_role=role) as frame:
            assert await frame.get("DEFAULT_TAKEOVER_LOGGERS") == DEFAULT_TAKEOVER_LOGGERS
            config = await resolve_logger_config(frame)
            expected: tuple[str, ...] = DEFAULT_TAKEOVER_LOGGERS
            if expression == "[]":
                expected = ()
            elif expression == '["business"]':
                expected = ("business",)
            elif expression is not None:
                expected += ("business",)
            assert config.takeover_loggers == expected


async def test_takeover_baseline_cannot_be_redefined(tmp_path: Path) -> None:
    path = tmp_path / "service.lclcfg"
    path.write_text("DEFAULT_TAKEOVER_LOGGERS: []\n", encoding="utf-8")
    with pytest.raises((ValueError, LclError), match="DEFAULT_TAKEOVER_LOGGERS"):
        async with configuration_frame(path):
            pytest.fail("framework baseline was replaced")
