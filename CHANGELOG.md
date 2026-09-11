# Changelog

## Unreleased

- Consolidate the reviewed requirements into existing reference and development
  documentation, remove the standalone specification, and update source packaging.
- Fix CI lookup of unpublished GitHub release drafts and add GitHub-only recovery
  using an original successful PyPI run's source commit and tested artifacts.
- Check HTTP status before parsing readiness responses in the native-worker
  recovery verifier; retain its bounded retry and worker-count requirements.

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
- Establish executable documentation, independent downstream examples, and cross-platform engineering checks.
