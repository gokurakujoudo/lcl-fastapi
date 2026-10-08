# Engineering and delivery

Read [the root policy](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/AGENTS.md), [the implementation plan](development-plan.md),
and [the feature inventory](features.md) before changing public behavior.

## Responsibility boundaries

| Component | Responsibility |
| --- | --- |
| lclang | Formal `.lclcfg` loading and evaluation, CLI parsing/scopes, logger runtime, and the Snowflake generation algorithm. |
| lcl-fastapi | Application assembly, worker resource ownership and ID leases, request context/propagation, health sampling, local observations/operations, offline documentation, and deployment-file rendering. |
| FastAPI | Native business routing, dependency injection, REST handling, and OpenAPI generation. |
| Uvicorn and Winloop | Windows ASGI serving and event-loop integration; Uvicorn owns native worker creation and recovery. |
| Gunicorn | Linux native ASGI serving and master/worker lifecycle. |
| psutil | Cross-platform process identity and host/resource inspection. |
| Nginx and optional systemd | Operator-managed TLS/reverse proxying and Linux service supervision; the framework only renders their configuration. |
| Downstream application | Business Routers, async lifespan, trusted business configuration, and business logic. |

Production dependencies flow toward configuration/context and core operations;
CLI, runtime, filesystem, and rendering adapters depend on those contracts.
Public re-exports are curated in package `__init__.py` files without starting
resources. Do not add a second Snowflake algorithm, CLI parser, supervisor,
authentication subsystem, management service, configuration restart mechanism, or log-stream
backend. The [development plan](development-plan.md) maps the accepted scope to
reference pages and behavioral acceptance evidence.

## lclang 1.0.15 integration assessment

Version 0.4.0 requires `lclang>=1.0.15`, without an upper bound. Review the upstream
[1.0.11 through 1.0.15 changes](https://github.com/gokurakujoudo/lclang/compare/1.0.11...1.0.15)
and [1.0.15 release notes](https://github.com/gokurakujoudo/lclang/releases/tag/1.0.15).
The existing quality and installed-example jobs resolve the current stable
version; separate Windows/Linux compatibility jobs run the complete tests with
`lclang==1.0.15`. Passing checks establish evidence for those actual resolved
versions, not every future release allowed by the requirement.

The framework adopts native `logger.timezone`, defaulting to local timestamps
with microseconds and a numeric offset. Explicit UTC remains available through
LCL and CLI overrides. Role filtering and logger configuration transformations
retain this setting; permanent segment naming and aligned rotation remain UTC.
The short-lived CLI logger remains independent of service configuration.

`utils.invoke`, added in 1.0.13, now resolves HTTP exception callbacks exactly
once. The typed interface accepts a synchronous Response or an awaitable, while
existing asynchronous callbacks continue to work. Invocation stays in the
request task, with native cancellation and the existing diagnostic/500 fallback.
Synchronous callbacks must remain nonblocking.

`Config.to_frame(preset=...)`, added in 1.0.15, is a useful shortcut for directly
running loaded configuration. The framework still composes CLI expression
winners, sticky masks and controller/worker sink filtering into a Module before
creating its Frame. The shortcut has no Module-filtering argument and presets
are below configuration definitions, so it cannot directly replace this flow.

The 1.0.12-1.0.14 workflow additions cover subtree skipping, typed defaults,
dataclass mappings, scoped nested execution and multiline business events.
They benefit downstream finite workflows. The service's native supervisors and
owned resource scopes do not need a workflow migration. In particular, invoking
synchronous background entries inside an active loop would break their explicit
`BackgroundWorkerContext.run()` boundary. Full traceback diagnostics also retain
their untruncated representation contract rather than adopting bounded field
formatting. No performance improvement is claimed without measurements.

1.0.15 removes standalone `evaluate` and `evaluate_sync`; this project uses
`Frame.evaluate` and named Frame lookup already. The playground's sole internal
adapter, `internal_verbose_scope`, remains version-sensitive and is revalidated
by native trace, skipped-branch, cache, session and cleanup tests plus its exact
executable tutorial. The open dependency range does not make that adapter public.

## Documentation website

The [documentation site](https://gokurakujoudo.github.io/lcl-fastapi/) renders
canonical `docs/` Markdown with MkDocs and its bundled Read the Docs theme.
The GitHub Wiki provides a curated entry point linking to these maintained pages.
Keep reference text and executable examples in `docs/`; do not copy them into
the Wiki. Add published pages to the navigation in `mkdocs.yml`.

Install the documentation tools in your development environment with
`python -m pip install -e ".[dev,docs]"`. Run
`python -m pytest -m documentation --no-cov` and
`python -m mkdocs build --strict` before publishing. Preview locally with
`python -m mkdocs serve`; generated HTML lives in the ignored `site/` directory.
Links to repository files outside `docs/` use GitHub URLs so they work both in
the Markdown source and on the published site.

The Documentation workflow validates examples and builds the site on pull
requests. On `main`, it publishes the checked site artifact using GitHub Pages
Actions. The repository's Pages source must be **GitHub Actions**. Site deployment
does not advance `release`, upload packages, or create a version tag.

## Quality and artifact verification

Install the project and its development dependencies in a Python 3.14 virtual
environment, with Node.js 18 or later available for the standalone JavaScript
highlighter check, then run `python -m scripts.quality` using that environment's Python.
The gate checks Git whitespace, Ruff, production policy, dependency direction,
strict mypy, canonical documentation examples and links, behavioral tests,
coverage, standalone syntax-highlighter behavior, and distribution contents.
Generated evidence lives in `reports/`.

Production policy checks validate structure: code-bearing physical line counts,
declaration names, documented parameters and return values, and value-class
constructor fields. Reviewers additionally verify the meaning and completeness
of English rST, intentional escaping exception contracts, constant sources and
units, and resource/cancellation ownership. Static checks cannot prove those
semantic requirements.

The Windows and Linux CI jobs use `python -m scripts.quality --collect-coverage`
to retain platform measurements without prematurely rejecting genuine paths
that belong to the other operating system. The dependent job combines both
datasets and enforces 100% production branch coverage. The separate artifact
build can run concurrently, but publication waits for the complete quality gate.
Subprocess and multiprocessing instrumentation is configured in pyproject.toml;
normal workers must exit gracefully to preserve their measurements.
The implementation follows the [coverage process guidance](https://coverage.readthedocs.io/en/latest/subprocess.html).

The separate build job constructs an isolated wheel and source distribution from
the checked-out commit. Eight downstream jobs each create their own virtual
environment, install that wheel plus one example project, and run its `verify.py`.
These jobs do not use source-path imports. `python -m scripts.verify_example minimal`
and `python -m scripts.verify_example composed` reproduce their installation and
verification steps locally after a build. Reports under `reports/examples/`
contain the wheel hash, platform, commands, output, and failure diagnostics.

The build metadata declares Python >=3.14. The initial evidence matrix is
CPython 3.14 on Windows and Linux; future interpreters are not yet verified.
Version `0.4.0` is maintained in pyproject.toml. Later releases use PEP 440
versions with compatibility-aware increments and exact version tags without a
`v` prefix, following the root release policy.

Changes are reviewed and squash-merged through a pull request linked to their
issue. An explicitly requested release advances `release` to the selected
verified commit on `main`. The [release workflow](releasing.md) repeats the
complete quality gate, publishes the tested distributions with PyPI Trusted
Publishing, and creates the matching version tag and GitHub Release.
