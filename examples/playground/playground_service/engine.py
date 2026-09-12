"""Keep every session Frame on one dedicated thread and event loop."""

import asyncio
import logging
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from lclang import EvaluationLimits, Frame, LclError, Module, ModuleName, parse_expression
from lclang.config import ConfigDefinition, parse_config
from lclang.diagnostics import internal_verbose_scope

from playground_service.inspection import Trace, dependencies, diagnostic, tree


@dataclass
class Session:
    frame: Frame | None = None
    touched: float = field(default_factory=time.monotonic)
    source: str = ""
    expression: str = ""
    report: dict[str, Any] = field(default_factory=dict)


class Engine:
    """Serialize session operations; Frames never leave the evaluation thread."""

    def __init__(self, maximum: int, ttl: float) -> None:
        self.maximum, self.ttl = maximum, ttl
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lcl-playground")
        self.runner: asyncio.Runner | None = None
        self.sessions: dict[str, Session] = {}

    async def submit(self, action: str, session_id: str = "", **payload: str) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, self.call, action, session_id, payload)

    def call(self, action: str, session_id: str, payload: dict[str, str]) -> dict[str, Any]:
        if self.runner is None:
            self.runner = asyncio.Runner()
        return self.runner.run(self.dispatch(action, session_id, payload))

    async def dispatch(
        self, action: str, session_id: str, payload: dict[str, str]
    ) -> dict[str, Any]:
        now = time.monotonic()
        for key, session in list(self.sessions.items()):
            if now - session.touched >= self.ttl:
                await self.remove(key)
        if action == "create":
            if len(self.sessions) >= self.maximum:
                raise HTTPException(
                    429, "Session limit reached; delete a session or wait for expiry"
                )
            key = secrets.token_urlsafe(24)
            self.sessions[key] = Session()
            return {"id": key, "idle_seconds": self.ttl}
        if session_id not in self.sessions:
            raise HTTPException(404, "Session missing or expired")
        session = self.sessions[session_id]
        session.touched = now
        if action == "delete":
            await self.remove(session_id)
            return {"closed": True}
        if action == "read":
            return {"source": session.source, "expression": session.expression, **session.report}
        if action == "parse":
            return await self.parse(session, payload["source"], payload["expression"])
        if action == "evaluate":
            return await self.evaluate(session)
        raise ValueError("Unknown session operation")

    async def remove(self, key: str) -> None:
        session = self.sessions.pop(key)
        if session.frame is not None:
            await session.frame.close()

    async def parse(self, session: Session, source: str, expression: str) -> dict[str, Any]:
        try:
            document = parse_config(source, source_name="session.lclcfg")
            definitions = {}
            for declaration in document.declarations:
                if not isinstance(declaration, ConfigDefinition):
                    raise ValueError("using is not enabled in this in-memory playground")
                name = str(declaration.name)
                if name in definitions:
                    raise ValueError(f"Duplicate definition: {name}")
                definitions[name] = declaration.expression
            node = parse_expression(expression)
            module = Module(ModuleName("playground"), definitions)
            report = {
                "ast": {name: tree(value) for name, value in definitions.items()},
                "expression_ast": tree(node),
                "dependencies": dependencies({**definitions, "<expression>": node}),
            }
            frame = Frame(
                module,
                values={"len": len, "sum": sum, "min": min, "max": max, "abs": abs, "round": round},
                limits=EvaluationLimits(max_depth=60, max_steps=10000, max_collection_items=1000),
            )
        except (LclError, ValueError, RecursionError) as error:
            return {"error": diagnostic(error)}
        if session.frame is not None:
            await session.frame.close()
        session.frame, session.source, session.expression = frame, source, expression
        session.report = report
        return report

    async def evaluate(self, session: Session) -> dict[str, Any]:
        if session.frame is None:
            raise HTTPException(409, "Parse a program before evaluating")
        trace = Trace()
        logger = logging.Logger("playground.trace", level=logging.DEBUG)
        logger.addHandler(trace)
        report: dict[str, Any] = {}
        # This pinned-version adapter is the only internal lclang integration.
        # Native task-local diagnostics preserve branch skipping and cache hits.
        with internal_verbose_scope(logger):
            try:
                value = await session.frame.evaluate(session.expression)
                report["result"] = {"type": type(value).__name__, "repr": repr(value)[:8192]}
            except Exception as error:
                report["error"] = diagnostic(error)
        report.update(trace=trace.events, trace_truncated=trace.truncated)
        session.report["evaluation"] = report
        return report

    async def close(self) -> None:
        def finish() -> None:
            if self.runner is not None:
                for key in list(self.sessions):
                    self.runner.run(self.remove(key))
                self.runner.close()

        await asyncio.get_running_loop().run_in_executor(self.executor, finish)
        self.executor.shutdown(wait=True)
