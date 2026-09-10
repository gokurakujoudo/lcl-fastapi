"""Configure Gunicorn's native ASGI worker without another CLI parser."""

import importlib
from collections.abc import Callable
from typing import Protocol, cast

from starlette.types import ASGIApp

from lcl_fastapi.config import Settings
from lcl_fastapi.runtime.application import load_application


class GunicornConfiguration(Protocol):
    """Describe the untyped upstream configuration mutation boundary."""

    def set(self, name: str, value: object) -> None:
        """Set one validated Gunicorn configuration value.

        :param name: Upstream option name.
        :param value: Value accepted by the upstream setting validator.
        """


class GunicornRunner(Protocol):
    """Describe Gunicorn's blocking arbiter entry point."""

    def run(self) -> None:
        """Run master supervision until native graceful shutdown finishes."""


class GunicornApplication:
    """Supply the documented application interface to Gunicorn's arbiter.

    :param settings: Validated master settings; not reloaded on worker replacement.
    """

    def __init__(self, settings: Settings) -> None:
        """Construct upstream defaults and apply explicit service settings.

        :param settings: Master listener and worker-management settings.
        :raises ImportError: If the Linux Gunicorn dependency is unavailable.
        """
        factory = cast(
            Callable[[], GunicornConfiguration],
            importlib.import_module("gunicorn.config").Config,
        )
        self.cfg = factory()
        self.callable: ASGIApp | None = None
        options: dict[str, object] = {
            "bind": [f"{settings.host}:{settings.port}"],
            "workers": settings.workers,
            "worker_class": "asgi",
            "asgi_lifespan": "on",
            "asgi_loop": "asyncio",
            "backlog": settings.backlog,
            "keepalive": settings.keep_alive_seconds,
            "graceful_timeout": settings.graceful_timeout_seconds,
            "accesslog": None,
            "preload_app": False,
            "root_path": "",
        }
        for name, value in options.items():
            self.cfg.set(name, value)

    def wsgi(self) -> ASGIApp:
        """Load the downstream ASGI application once in the calling worker.

        :returns: Worker-local ASGI application.
        :raises ImportError: If the configured application cannot be imported.
        """
        if self.callable is None:
            self.callable = load_application()
        return self.callable

    def reload(self) -> None:
        """Keep master settings unchanged during Gunicorn-native worker reload."""


def run_linux(settings: Settings, identity: dict[str, object]) -> None:
    """Run Gunicorn's arbiter in the already-recorded master process.

    :param settings: Detached settings used by the master for this complete start.
    :param identity: Published master identity, owned by the surrounding scope.
    :raises ImportError: If Gunicorn is not installed on Linux.
    """
    application = GunicornApplication(settings)
    factory = cast(
        Callable[[GunicornApplication], GunicornRunner],
        importlib.import_module("gunicorn.arbiter").Arbiter,
    )
    factory(application).run()
