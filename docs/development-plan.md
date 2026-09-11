# Development plan

This records the original implementation plan. The user subsequently ended
subagent delegation and authorized review, squash merge, and the first `0.1.0`
release. That current authorization supersedes the historical PR-only delivery
and delegation instructions below. See [the release procedure](releasing.md).

This document describes planned work and acceptance evidence. It does not claim
that any component, check, example, or supported platform is implemented yet.
The Chinese requirements in [the specification](../lcl-fastapi%20spec.md) own the
business scope. Root `AGENTS.md` owns engineering and delivery policy once
materialized. Changes to this plan must preserve the user's accepted decisions.

## Delivery scope

Both independent downstream examples are mandatory GitHub Actions acceptance
jobs. Build the current head's wheel in isolation, then run each example in its
own environment on Windows with Uvicorn and Linux with Gunicorn. Install the
built wheel and the example package; do not import repository source through
PYTHONPATH. Each of the four jobs verifies availability, business and built-in
HTTP APIs, every CLI command, and graceful shutdown, retaining command output
and failure diagnostics. All four jobs must pass on the latest PR head.

Implement a Python 3.14+ MIT-licensed package named `lcl-fastapi`, imported as
`lcl_fastapi`, using `lclang==1.0.10`. Deliver implementation, tests, English
public documentation, two independently authored downstream examples, built
distributions, and a verified development pull request in
[the project repository](https://github.com/gokurakujoudo/lcl-fastapi).

Do not merge the pull request, promote a release branch, create a version tag,
publish to PyPI, or publish a GitHub Release. Installing the built wheel is the
installation acceptance criterion for this request. Public registry installation
belongs to a later, explicitly requested release.

The supervising agent coordinates work, reviews evidence, resolves integration
issues, and communicates progress and questions. Subagents perform the document,
implementation, test, example, and review work. Shared files must have one
assigned writer at a time. Agents must read applicable `AGENTS.md` instructions
before editing and must not commit another agent's unfinished files.

## Accepted contracts

| Area | Required behavior |
| --- | --- |
| CLI | Use the real `lclang.cli.CliEntrance`; no argparse, Click, Typer, or replacement parser. |
| CLI options | Use `-o config service.lclcfg`, `-o json` for status/logs, and `-o output filename` for render commands. Reject other runtime overrides and options inappropriate to the command. |
| Configuration | `.lclcfg` is the formal source. Load the service file independently of the CLI's own Frame. Relative filesystem paths resolve from the config directory. |
| Logging | Use the upstream schema, including `logger.file.default.directory` and `logger.file.service.filename`. Inject request context at emit time in the framework wrapper. |
| Business API | Support native-style route decorators, `include_router`, `LclFastAPI(lifespan=...)`, and asynchronous `get_config(key)` during an active worker scope. |
| Route paths | Remove `api.prefix`; downstream Routers own business prefixes. |
| Built-in overrides | A downstream route with the same HTTP method and exact path wins, whether registered directly or through a Router. Document consequences for health, docs, and shutdown/stop. |
| External URL | `server.root_path` is optional external HTTP(S) origin metadata for producing complete URLs. It is never an ASGI mount path and must not change routing. |
| Nginx configuration | Keep server name, listen port, and certificate settings as renderer inputs. An external origin may represent public NAT/proxy mappings; do not force its port into Nginx or the application listener. |
| Listener | Accept only `127.0.0.1` and `0.0.0.0`; default to loopback. Local stop and Nginx upstream use `127.0.0.1`. |
| Configuration changes | No snapshots or file watchers. Every newly started/replacement worker reads the current file. Existing workers do not reload; the master retains its startup listener/manager settings. Warn against editing a running service's file. |
| Instance scope | One service per machine; no multiple-instance coordinator. Worker leases cover concurrent workers of that service, not unbounded historical Snowflake uniqueness. |
| Multiple machines | Show the verified upstream LCL environment-variable syntax for choosing non-overlapping worker-ID ranges. Do not add an independent environment override mechanism. |
| Active logs | Publish actual upstream active-file paths with bounded eventual consistency, not directory-mtime guesses. Exclude dead workers and expose observation age/staleness in JSON. |
| License and delivery | MIT; development PR only; no merge or release. |

## Stage 1: specification and project policy

The requirements agent updates the Chinese specification and this plan. The
engineering agent materializes the complete selected skill policy in root
`AGENTS.md`, retaining actual project facts and marking missing tooling as
unimplemented. The dependency agent records the actual upstream CLI, Frame,
logger, active-file, and Snowflake interfaces used by implementation agents.

Before feature work, reconcile these documents around one contract. Do not copy
another project's identity, utility inventory, or interpreter architecture.

Acceptance:

- No remaining `api.prefix`, old logger aliases, old CLI syntax, snapshot
  requirement, or ASGI interpretation of the external origin is prescribed.
- The public business-configuration and lifespan scope is explicit.
- Built-in route overrides and their documented consequences are explicit.
- The policy identifies actual paths, commands, and gaps without claiming that
  planned quality checks have passed.

## Stage 2: package and authoritative quality gate

The engineering agent creates packaging metadata, MIT licensing, a curated typed
public package, and one authoritative quality entry point. Use the selected
policy's `python -m scripts.quality` unless a verified project equivalent exists.
The gate includes whitespace validation, Ruff, production-policy checks,
architecture checks, strict mypy for source/tests/scripts, documentation checks,
and behavioral tests with production branch coverage. Do not weaken the policy
to accommodate implementation shortcuts.

Create Windows and Linux CI verification with the supported interpreter range.
Platform-specific branches require real evidence from their corresponding
platform; coverage exclusions and invented skips cannot substitute for that
evidence. Keep generated reports and development environments out of packages.

Acceptance:

- Wheel and source-distribution builds are reproducible from the selected source.
- Package metadata, license, Python requirements, and typing data are present.
- The full gate is executable and emits useful test/coverage evidence.
- CI and release preparation remain within the authorized PR-only scope.

## Stage 3: worker runtime and application assembly

Assign separate writers to runtime/configuration and application concerns after
their shared public contracts have been agreed. The runtime agent implements
configuration loading, service identity, atomic state, process verification,
worker-ID leases, Windows/Uvicorn, Linux/Gunicorn, and whole-service graceful
shutdown. It must not introduce a separate management port or supervisor.

The application agent implements FastAPI assembly, business lifespan composition,
configuration access, pure ASGI Request Context, request-scoped logging and one
access log per request, health sampling, and locally packaged Swagger UI.

Resource order is contractual: establish worker configuration/Frame and logging,
acquire the lease and generator, start framework state/sampling, then enter the
business lifespan. Exit the business scope before flushing and closing framework
resources. Failure at any intermediate point must release resources already
acquired. Object construction and business-module import do not create runtime
resources.

Route precedence must be implemented deliberately. Merely appending a duplicate
FastAPI route is not proof of override behavior. Test both direct and Router-based
registration against the actual request dispatcher and OpenAPI behavior. Keep
method distinctions intact.

Acceptance:

- Concurrent requests have isolated server-generated IDs in context, state,
  response headers, and logs; supplied client IDs do not replace them.
- Logs identify business call sites and do not retain another request's context.
- Each worker has its own Frame, logging runtime, generator, and lifespan.
- Worker recovery, initialization failures, cancellation, and normal shutdown
  leave no owned resources or valid-looking stale process state behind.
- Replacement workers can read changed configuration without mutating existing
  workers or the current master listener.
- Health returns process/server data without blocking request-time CPU sampling;
  unavailable metrics are represented without a whole-endpoint HTTP 500.
- Swagger HTML, schema, JavaScript, CSS, and favicon work without external access.

## Stage 4: CLI, renderers, and platform integration

The CLI/rendering agent connects the upstream `CliEntrance` to the agreed runtime
contracts. Keep CLI diagnostics on stderr and structured/generated output on
stdout. The serve handler prepares launch data; platform runtime startup occurs
after the CLI's asynchronous scope and event loop have exited.

Implement all six operations: serve, status, logs, stop, nginx render, and systemd
render. Renderers only produce text/files and never install configuration, run
Nginx validation/reload, or execute systemctl. Preserve Nginx's independent
listener/TLS configuration and block the internal shutdown URL.

Acceptance:

- Real Windows and Linux multi-worker processes start, serve, recover workers,
  report verified service/worker identities, and stop gracefully.
- Linux health reports the actual Gunicorn master PID; Windows reports a null
  Gunicorn PID and the correct Uvicorn service identity.
- Missing/wrong control tokens return 403; accepted shutdown returns 202 before
  termination and flushes business/framework logs.
- Status/logs ignore dead or identity-mismatched workers. Log rollover produces
  updated paths within the documented observation window.
- CLI rejects unsupported options without creating service log files or mixing
  diagnostics into JSON or rendered text.
- Renderer tests prove output correctness and absence of deployment side effects.

## Stage 5: public documentation and independent downstream examples

An assigned documentation agent writes English reference contracts, an accessible
README, executable guides, an implemented-feature inventory, and a changelog.
Execute exact marked Markdown Python examples, validate links/navigation, and
state capabilities as implemented only after evidence exists.

After the package and documentation are usable, launch two new example agents.
Each receives the public documentation, wheel, and its desired business outcome;
it must not depend on private implementation knowledge or another example's
authoring history.

1. The minimal example agent installs the wheel in an isolated environment and
   builds a service with a business route and its own `.lclcfg`.
2. The composition example agent independently builds Router prefixes, business
   configuration access, a business lifespan, and request-associated logs.

Both examples must include their own reproducible run/check instructions and
must not use production credentials or external services. Missing documentation
or surprising API behavior is feedback to the responsible agent, followed by a
fix and a fresh example check. Put examples in the repository and verify that
neither is included in the wheel.

Acceptance:

- Both fresh agents succeed from the published-in-repository instructions and
  installed artifact rather than source-path imports.
- Docs explain origin semantics, native CLI syntax, override consequences,
  single-service scope, config-change behavior, and log eventual consistency.
- Every advertised capability has corresponding behavior and test evidence.

## Stage 6: independent review and development PR

A review agent compares the final diff and artifacts with the specification,
`AGENTS.md`, and this acceptance checklist. Implementation agents fix actionable
findings and rerun affected checks, then the full gate after the final changes.

Use the configured issue, feature branch, and PR workflow. Keep implementation,
tests, documentation, and reviewable limitations together. Wait for applicable
checks on the exact latest PR head, including both platform verification jobs
and builds. Report real commands, revisions, results, skips, and blockers.

The supervising agent's final handoff includes the PR link and concise evidence
for installation, platform lifecycle, documentation/example usability, packaging,
and quality. Leave the PR open for user review. Do not merge or release.
