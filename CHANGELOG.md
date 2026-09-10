# Changelog

## Unreleased

- Add a typed LCL-powered FastAPI application with native Router and lifespan composition.
- Add server-generated request IDs, contextual logging, health sampling, and offline Swagger UI.
- Return HTTP 503 with one failure access log when the upstream Snowflake generator
  reports clock rollback or sequence exhaustion; do not fabricate a fallback ID.
- Add Windows Uvicorn and Linux Gunicorn runtimes with local status, log-path, and graceful-stop commands.
- Propagate application import, configuration, and lifespan startup failures to a
  nonzero service exit after native worker cleanup on both platforms.
- Keep master state, control tokens, and POSIX locks owned by their creating
  process when a forked worker exits and is replaced.
- Add Nginx and systemd configuration renderers that produce files without performing deployment operations.
- Establish executable documentation, independent downstream examples, and cross-platform engineering checks.

This development series has not been published. Version `0.1.0` identifies the
initial development artifacts; no version tag, PyPI upload, or GitHub Release
is part of the current review task.
