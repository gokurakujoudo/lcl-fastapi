# Changelog

## Unreleased

## 0.3.0 - 2026-09-12

- Fix review-discovered lifecycle races: drain submitted API Tasks through their
  cancellation cleanup before worker restart or business teardown; supervise
  runtime journal failure and enforce independent controller retirement deadlines.
  Preserve configured Exception/500 handlers even in debug mode.

- Add code-registered background workers with dedicated threads/event loops,
  nested configuration scopes, explicit service-resource bridges, restart controls,
  controller lifecycle events, isolated log files and status/health observations.
  Background registration forces one API worker; shutdown timeout terminates that
  process.

- Add configurable uncaught HTTP exception callbacks, original traceback argument
  diagnostics and resilient JSON 500 fallback.

- Add `use_lcl_frame()` with nested task-local configuration scopes, native Frame
  inheritance, and cleanup on exceptions and cancellation.

## 0.2.0 - 2026-09-12

- Breaking CLI change: status/logs output is always JSON. Remove `-o json` from
  scripts and parse the `paths` array instead of newline-delimited log paths.
  This preserves the native LCL JSON namespace.

- Add downstream pyproject entrances, native CLI options and configuration overrides,
  portable `using` of universal defaults, and separate controller/worker log files.
- Retry brief Windows state-file read and replacement sharing conflicts without suppressing
  persistent permission errors.

## 0.1.1 - 2026-09-12

- Add `serve -o hot_reload` with configurable recursive Python watch directories,
  one effective worker with a warning, and graceful native worker replacement.

## 0.1.0 - 2026-09-11

- Add a typed LCL-powered FastAPI application with native Router and lifespan composition.
- Add server-generated request IDs, contextual logging, health sampling, and offline Swagger UI.
- Return HTTP 503 with one failure access log when the upstream Snowflake generator
  reports clock rollback or sequence exhaustion; do not fabricate a fallback ID.
- Add Windows Uvicorn and Linux Gunicorn runtimes with local status, log-path, and graceful-stop commands.
- Propagate application import, configuration, and lifespan startup failures to a
  nonzero service exit after native worker cleanup on both platforms.
- Keep master state, control tokens, and POSIX locks owned by their creating
  process when a forked worker exits and is replaced.
- Publish worker observations outside the HTTP event loop and wait for active
  writes before releasing worker resources during shutdown.
- Use Uvicorn's Winloop integration on Windows so every idle worker keeps publishing
  observations after startup and replacement without new HTTP connections.
- Add Nginx and systemd configuration renderers that produce files without performing deployment operations.
