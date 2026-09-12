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
environment, then run `python -m scripts.quality` using that environment's Python.
The gate checks Git whitespace, Ruff, production policy, dependency direction,
strict mypy, canonical documentation examples and links, behavioral tests,
coverage, and distribution contents. Generated evidence lives in `reports/`.

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
Version `0.3.0` is maintained in pyproject.toml. Later releases use PEP 440
versions with compatibility-aware increments and exact version tags without a
`v` prefix, following the root release policy.

Changes are reviewed and squash-merged through a pull request linked to their
issue. An explicitly requested release advances `release` to the selected
verified commit on `main`. The [release workflow](releasing.md) repeats the
complete quality gate, publishes the tested distributions with PyPI Trusted
Publishing, and creates the matching version tag and GitHub Release.
