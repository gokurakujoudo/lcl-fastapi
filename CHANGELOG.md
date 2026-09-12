# Changelog

## Unreleased

## 0.2.0 - 2026-09-12

- Replace the README and documentation logo with the updated project artwork.

- Breaking CLI change: status/logs output is always JSON. Remove `-o json` from
  scripts and parse the `paths` array instead of newline-delimited log paths.
  This preserves the native LCL JSON namespace.
- Expand the catalog tutorial with installed entrances,
  inherited defaults, CLI overrides, and log rotation with file samples.

- Add downstream pyproject entrances, native CLI options and configuration overrides,
  portable `using` of universal defaults, and separate controller/worker log files.
- Retry brief Windows state-file read and replacement sharing conflicts without suppressing
  persistent permission errors.

- Pin downstream examples to the release version and resolve installation together with
  the selected wheel so incompatible pins cannot silently downgrade CI validation.

## 0.1.1 - 2026-09-12

- Make README links and the logo absolute for PyPI, add the project homepage
  metadata, and update installation examples for 0.1.1.

- Add `serve -o hot_reload` with configurable recursive Python watch directories,
  one effective worker with a warning, and graceful native worker replacement.

- Let documentation use the available browser width beside the navigation sidebar.

- Add the project logo to the README and documentation home page.

- Add a from-scratch product catalog guide with executable source, native service
  verification, local operations, and deployment preparation.

- Publish searchable MkDocs documentation with the Read the Docs theme on GitHub
  Pages, and add a curated GitHub Wiki entry point and repository homepage.

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
