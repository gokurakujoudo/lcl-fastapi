"""Assemble a FastAPI application around one independently owned worker scope."""

import asyncio
import os
import secrets
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from lclang.logger import resolve_logger_config, use_logger_handler
from lclang.utils import SnowflakeGenerator
from starlette.background import BackgroundTask
from starlette.middleware.errors import ServerErrorMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp, Lifespan, Receive, Scope, Send

from lcl_fastapi.background.config import worker_policies, worker_registry
from lcl_fastapi.background.context import BackgroundWorker
from lcl_fastapi.background.journal import BackgroundJournal
from lcl_fastapi.background.logging import background_log_config, background_log_routing
from lcl_fastapi.background.manager import BackgroundManager
from lcl_fastapi.config import configuration_frame, settings_from_frame
from lcl_fastapi.context import CONFIG_FRAME, get_request_context
from lcl_fastapi.docs.swagger import register_documentation
from lcl_fastapi.errors import (
    UncaughtExceptionContext,
    UncaughtExceptionHandler,
    handle_uncaught_exception,
)
from lcl_fastapi.health.sampler import HealthSampler
from lcl_fastapi.logging import (
    active_log_paths,
    get_logger,
    resolve_log_directories,
    suppress_server_access_logs,
)
from lcl_fastapi.middleware.request_context import RequestContextMiddleware, RequestRuntime
from lcl_fastapi.runtime.common import WorkerRuntime, worker_runtime


class LclFastAPI(FastAPI):
    """Create routes immediately and own resources only inside worker lifespan.

    :param lifespan: Optional business lifespan following FastAPI's convention.
    :param config_path: Optional path to the formal LCL file; CLI supplies it internally.
    :param uncaught_exception_handler: Optional asynchronous unhandled HTTP error callback.
    :param background_workers: Code-registered synchronous or asynchronous background callables.
    :param kwargs: Native FastAPI business options except framework-owned docs and root_path.
    """

    def __init__(
        self,
        *,
        lifespan: Lifespan[FastAPI] | None = None,
        config_path: str | Path | None = None,
        uncaught_exception_handler: UncaughtExceptionHandler | None = None,
        background_workers: Mapping[str, BackgroundWorker] | None = None,
        **kwargs: Any,
    ) -> None:
        """Create an application without opening files or runtime resources.

        :param lifespan: Optional business initialization and teardown context.
        :param config_path: Formal configuration source path, when explicitly supplied.
        :param uncaught_exception_handler: Optional unhandled HTTP failure callback.
        :param background_workers: Code-only registration of managed background workers.
        :param kwargs: Native FastAPI business configuration.
        :raises ValueError: If framework-owned URL settings are passed to the constructor.
        """
        reserved = {"docs_url", "redoc_url", "openapi_url", "root_path"} & kwargs.keys()
        if reserved:
            raise ValueError(f"configure framework URL settings in .lclcfg: {sorted(reserved)}")
        self.config_path = None if config_path is None else Path(config_path)
        self.business_lifespan = lifespan
        self.uncaught_exception_handler = uncaught_exception_handler
        self.background_workers = worker_registry(background_workers)
        self.background_manager: BackgroundManager | None = None
        self.worker_requests: RequestRuntime | None = None
        self.worker_identity: WorkerRuntime | None = None
        self.health_sampler: HealthSampler | None = None
        super().__init__(
            lifespan=self.worker_lifespan,
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
            root_path="",
            **kwargs,
        )
        self.request_middleware = RequestContextMiddleware(
            super().__call__,
            self.current_request_runtime,
        )

    def build_middleware_stack(self) -> ASGIApp:
        """Configure FastAPI's native final error boundary without changing inner handlers.

        :returns: Native middleware stack with the selected generic HTTP handler.
        :raises ValueError: If explicit generic error handlers conflict.
        :raises RuntimeError: If FastAPI changes its outer middleware contract.
        """
        generic = any(key in self.exception_handlers for key in (500, Exception))
        if generic and self.uncaught_exception_handler is not None:
            raise ValueError("uncaught_exception_handler conflicts with Exception/500 handlers")
        stack = super().build_middleware_stack()
        if not isinstance(stack, ServerErrorMiddleware):
            raise RuntimeError("expected FastAPI ServerErrorMiddleware boundary")
        if not generic:
            stack.handler = self.uncaught_response
            stack.debug = False
        return stack

    async def uncaught_response(self, request: Request, err: Exception) -> Response:
        """Adapt FastAPI's request-first callback to the public typed error interface.

        :param request: Current HTTP request with application and lifespan state.
        :param err: Original unhandled exception.
        :returns: Custom response or the default generic JSON 500.
        :raises RuntimeError: If called outside an active worker lifespan.
        """
        runtime = self.current_request_runtime()
        request_context = get_request_context()
        context = UncaughtExceptionContext(
            None if request_context is None else request_context.request_id,
            runtime.logger,
            CONFIG_FRAME.get() or runtime.frame,
            request.scope.get("endpoint"),
            {"name": self.title, "version": self.version, "worker_pid": os.getpid()},
        )
        return await handle_uncaught_exception(
            self.uncaught_exception_handler, err, request, context
        )

    def current_request_runtime(self) -> RequestRuntime:
        """Return worker resources only after successful initialization.

        :returns: Worker-long request resources.
        :raises RuntimeError: If the application lifespan has not started.
        """
        if self.worker_requests is None:
            raise RuntimeError("start with lcl-fastapi serve before sending requests")
        return self.worker_requests

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Place request identity outside FastAPI's complete error handling stack.

        :param scope: Incoming ASGI connection scope.
        :param receive: Incoming ASGI message source.
        :param send: Outgoing ASGI message sink.
        :raises BaseException: Propagates application failures after request cleanup.
        """
        await self.request_middleware(scope, receive, send)

    async def health(self) -> dict[str, object]:
        """Return cached host metrics and verified service identity.

        :returns: Public health data without control credentials.
        :raises RuntimeError: If called outside an initialized worker scope.
        """
        if self.worker_identity is None or self.health_sampler is None:
            raise RuntimeError("health requires an initialized worker")
        return {
            "status": "UP",
            "service": self.worker_identity.service_info()
            | (
                {"background_workers": self.background_manager.snapshot()}
                if self.background_manager is not None
                else {}
            ),
            "server": self.health_sampler.snapshot(),
        }

    async def shutdown(self, request: Request) -> Response:
        """Validate a local control token before scheduling whole-service shutdown.

        :param request: Incoming HTTP shutdown request.
        :returns: HTTP 202 with a post-response shutdown callback.
        :raises HTTPException: With status 403 when no valid token is supplied.
        """
        supplied = request.headers.get("X-LCL-Control-Token")
        runtime = self.worker_identity
        if (
            runtime is None
            or supplied is None
            or not secrets.compare_digest(
                supplied.encode("utf-8"), runtime.control_token.encode("utf-8")
            )
        ):
            raise HTTPException(status_code=403)
        return Response(status_code=202, background=BackgroundTask(runtime.request_shutdown))

    @asynccontextmanager
    async def worker_lifespan(self, app: FastAPI) -> AsyncIterator[Any]:
        """Compose worker resources before entering downstream business lifespan.

        :param app: Application passed by FastAPI lifespan dispatch.
        :returns: Context manager yielding optional business request state.
        :raises RuntimeError: If the CLI did not supply a configuration path or master identity.
        :raises BaseException: If initialization or business lifespan fails, after cleanup.
        """
        configured = self.config_path or os.environ.get("LCL_FASTAPI_CONFIG")
        if not configured:
            raise RuntimeError("configuration path is required; use lcl-fastapi serve")
        config_path = await asyncio.to_thread(Path(configured).resolve)
        original_routes = list(self.router.routes)
        async with AsyncExitStack() as stack:
            frame = await stack.enter_async_context(
                configuration_frame(config_path, os.getpid(), logger_role="worker")
            )
            frame_token = CONFIG_FRAME.set(frame)
            stack.callback(CONFIG_FRAME.reset, frame_token)
            settings = await settings_from_frame(frame, config_path)
            policies = await worker_policies(frame, self.background_workers)
            logger_config = resolve_log_directories(
                await resolve_logger_config(frame),
                config_path.parent,
            )
            logger_config = background_log_config(
                logger_config, policies, settings.app_name, os.getpid()
            )
            logger_config = resolve_log_directories(logger_config, config_path.parent)
            logger_runtime = await stack.enter_async_context(use_logger_handler(logger_config))
            stack.enter_context(background_log_routing(logger_runtime, bool(policies)))
            stack.enter_context(suppress_server_access_logs())
            runtime = stack.enter_context(worker_runtime(settings))
            logger = await get_logger("lcl_fastapi")
            self.worker_identity = runtime
            self.worker_requests = RequestRuntime(
                frame,
                SnowflakeGenerator(worker_id=runtime.worker_id),
                settings.id_header.lower().encode("ascii"),
                logger,
            )

            def publish(observed_at: float) -> None:
                """Register actual upstream file observations for this worker.

                :param observed_at: Collection time in Unix seconds.
                """
                if self.background_manager is not None:
                    runtime.background_workers = self.background_manager.snapshot()
                runtime.publish(active_log_paths(logger_runtime), observed_at)

            self.health_sampler = HealthSampler(
                settings.disk_paths,
                settings.sample_interval_seconds,
                logger,
                publish,
            )
            stack.push_async_callback(self.health_sampler.stop)
            try:
                await self.health_sampler.start()
                self.title, self.version = settings.app_name, settings.app_version
                for path, method, endpoint, enabled in (
                    (settings.health_path, "GET", self.health, settings.health_enabled),
                    ("/_lcl/shutdown", "POST", self.shutdown, True),
                ):
                    if enabled and not any(
                        getattr(route, "path", None) == path
                        and method in getattr(route, "methods", set())
                        for route in self.routes
                    ):
                        self.add_api_route(
                            path,
                            endpoint,
                            methods=[method],
                            include_in_schema=False,
                        )
                register_documentation(self, settings)
                self.openapi_schema = None
                async with AsyncExitStack() as business:
                    state = None
                    if self.business_lifespan is not None:
                        state = await business.enter_async_context(self.business_lifespan(app))
                    if self.background_workers:
                        if int(str(runtime.service["configured_workers"])) != 1:
                            raise RuntimeError(
                                "background workers require one effective API worker"
                            )
                        journal = BackgroundJournal(
                            runtime.directory,
                            runtime.identity | {"service_id": runtime.service["service_id"]},
                        )
                        self.background_manager = BackgroundManager(
                            self.background_workers,
                            policies,
                            app,
                            state,
                            config_path,
                            journal,
                            settings.graceful_timeout_seconds,
                        )
                        business.push_async_callback(self.background_manager.stop)
                        await self.background_manager.start()
                    yield state
            finally:
                self.background_manager = None
                self.worker_requests = None
                self.worker_identity = None
                self.router.routes[:] = original_routes
                self.openapi_schema = None
