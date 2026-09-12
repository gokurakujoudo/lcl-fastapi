# lcl-fastapi project facts

The maintained reference pages under `docs/` own the reviewed public contracts; `docs/development-plan.md` maps them to implementation and acceptance requirements. Development discussions may be Chinese; public documentation and production docstrings are English.

- Distribution: `lcl-fastapi`; import package: `lcl_fastapi`; production root: `src/lcl_fastapi`.
- License: MIT. Python metadata requires >=3.14; the initial target validation matrix is CPython 3.14 on Windows and Linux. Future versions are not claimed as tested.
- Runtime dependencies: lclang==1.0.10, FastAPI >=0.141,<0.142, Uvicorn >=0.52,<0.53, watchfiles >=1.1,<2, psutil >=7.2,<8, Winloop ==0.6.3 on Windows only, and Gunicorn >=26,<27 on Linux only. Use lclang.cli; argparse, Click and Typer are not allowed.
- Configuration comes from .lclcfg. `docs/configuration.md` governs key names and semantics, including the complete public origin. Use lclang's formal logger settings and Snowflake implementation.
- Default branch: main; persistent publication branch: release; feature prefix: codex/. The user has authorized reviewing and squash-merging the implementation PR, then publishing the 0.2.0 release through CI to PyPI and GitHub. Keep main and release; clean up the completed feature branch only after verified publication.
- This was an empty GitHub repository. An empty main bootstrap commit may establish a PR base. All product changes belong in the implementation PR.
- Version authority: pyproject.toml; any runtime version uses installed distribution metadata.
- Authoritative quality command: `venv/Scripts/python.exe -m scripts.quality` on Windows and `venv/bin/python -m scripts.quality` on Linux. The quality entry point, structural production policy, architecture, documentation/link, artifact-integrity checks, and cross-platform coverage CI are implemented. Semantic policy compliance requires review. Full compliance is established by the latest PR head's Windows/Linux jobs and strict aggregate coverage report, not by the presence of configured checks.
- Expected documentation entries: README.md, docs/development-plan.md, reference and guide pages under docs/. Examples are independent downstream projects and must not enter the wheel.
- Platform-specific real-process tests run on their respective operating systems. Combine Windows/Linux coverage data to enforce 100% production branch coverage without excluding platform modules.
- Agents share a workspace. Respect assigned file ownership and coordinate shared files before editing. Follow the user's current delegation instructions; the remaining work in this task is performed by the primary agent without subagents.
# Python project engineering requirements

## Scope, project facts, and sources of truth

These requirements govern engineering quality and delivery. Keep the project's
business purpose, domain model, public behavior, and specialized architecture
in their existing sections. Read AGENTS.md, README.md, pyproject.toml, the
feature/status inventory if present, and relevant reference/development
documentation before changing code.

Record the actual distribution/import names, production source root, supported
Python versions and platforms, license, dependency policy, version source,
default branch, release branch, quality command, and documentation entry points.
Use pyproject.toml for packaging/tool metadata. Resolve license and compatibility
decisions from the project's choices; do not assume a license, Python minimum,
fixed version series, or release destination.

Reference documentation owns public contracts; executable guides own workflows;
tests own behavioral evidence; the feature inventory and README describe
implemented capability. The changelog records user-visible changes. Requirements
are not evidence of compliance: identify missing enforcement or documentation
without claiming that it already exists.

## Design and public API

- Organize production code into small packages by responsibility. Dependencies
  flow toward core types and domain logic; CLI, filesystem, network, and other
  adapters depend on the core. Avoid cyclic imports and cross-layer shortcuts.
  Package __init__.py files curate re-exports without behavioral initialization.
- Prefer the standard library and existing project mechanisms. Keep runtime
  dependencies minimal and separate development, documentation, build, and
  publishing dependencies. Do not introduce speculative abstractions or new
  backends without a concrete requirement; preserve explicitly chosen native
  integrations or dependencies.
- Design typed public APIs with explicit input validation, stable result/error
  contracts, and documented compatibility. Use __all__ to curate exports and
  ship py.typed for typed distributions. Distinguish intentional exceptions
  from unexpected failures; preserve causes and useful context.
- For asynchronous I/O and resource workflows, prefer async public entry points.
  Keep synchronous convenience boundaries explicit and prevent nested event
  loops. Pure calculations need not become async merely for uniformity.
- Define resource ownership, cleanup order, cancellation behavior, and allowed
  concurrency scope. Close caller-owned resources explicitly or through context
  managers. Document whether objects are task-, thread-, or process-safe.
  Specify cache consistency and recalculation/invalidation rules where caching
  exists; do not import another project's cache semantics by default.
- State the actual trust boundary. Do not describe trusted configuration or
  static capability checks as a hostile-code sandbox. Dynamic execution,
  reflection, imports, ambient I/O, and connectivity need an explicit role in
  the design rather than appearing accidentally through convenience helpers.

## Production source requirements

- Each production Python file has at most 200 code-bearing physical lines,
  excluding imports, declaration/attribute docstrings, pure comments, and blank
  lines. Multiline signatures, expressions, and runtime strings count by their
  physical lines. Do not compress statements or embed code in strings to evade
  the limit. Split by responsibility, not arbitrary line count.
- Production functions and classes use descriptive names without an underscore
  prefix. Only required Python protocol methods use dunder spellings. Do not
  replace meaningful names with generic internal prefixes; curate exports with
  __all__. These declaration rules do not forbid ordinary private state fields.
- Every production docstring is English rST. Functions and methods, including
  nested helpers, explain how they work, every parameter with :param name:,
  returned values with :returns: unless returning no value, and intentional
  escaping exceptions with :raises Type:. Value classes document constructor
  fields and edge cases. Use notes for useful special behavior, not repetition.
- Document every named constant and coherent constant group, including enums:
  units or absence of units, actual source, purpose, and choice rationale.
  Never invent external sources or annotate every ordinary control-flow literal.
- These size, naming, docstring, and constant requirements apply to production
  source. Tests and scripts still pass Ruff and strict mypy. Existing explicit
  project exceptions must be recorded rather than silently broadened.

## Behavioral tests and isolation

- Mirror production subsystem responsibilities in tests. A test file may cover
  several related implementation modules; each behavior has one obvious owner.
  Keep integration contracts explicit and reusable fixtures in support modules
  when actually shared. Do not require one test file per source file.
- Assert public behavior, observable state, and meaningful diagnostics rather
  than private method calls or a second implementation of the same algorithm.
  Cover sunny, rainy, boundary, and composite cases where applicable.
- Unit tests mock external connectivity, clocks, randomness, and other
  nondeterminism as needed. Isolate file I/O and enabled logs in a separate
  TemporaryDirectory per test. CLI test configuration sources are static
  fixtures declared with their cases. No test should depend on a live account.
- Include deterministic stress, concurrency, cancellation, and lifecycle/leak
  tests for relevant behavior in the ordinary full gate. Use property or
  differential tests where there is a useful invariant or independent oracle.
  Keep performance benchmarks separate from correctness acceptance.
- Require 100% production branch coverage, with no threshold reductions,
  exclusions, ignores, weakened assertions, or invented skips merely to pass.
  Report genuine platform/dependency skips explicitly and cover supported
  platforms in the appropriate environment. Use strict test markers/config.
- If filesystem or temporary-directory permissions block a tool or test, stop
  that operation and obtain the required permission. Do not relocate temporary
  files, change temporary environment variables, weaken isolation, skip checks,
  or change paths solely to bypass the restriction. Continue independent work.

## Change workflow and quality gate

- For a bug: update the relevant English contract; add a behavioral regression
  test and prove the intended failure; implement the smallest correct fix;
  prove the test passes; refactor if needed and run focused/full checks.
- For a feature: prototypes may precede the settled contract, but acceptance
  requires reference documentation and behavioral tests. Update README,
  changelog, and the feature inventory when public claims change.
- For a refactor: pass existing tests first, change production code, pass the
  same tests, then reorganize test ownership and verify again.
- Use one authoritative, reproducible quality entry point, preferably
  python -m scripts.quality if no project equivalent exists. Define its concrete
  command and environment in this file; identify it as required but unimplemented
  if it does not exist yet.
- The gate fails fast through whitespace validation, Ruff, production-policy
  checks, architecture/capability checks, strict mypy over source/tests/scripts,
  documentation checks without coverage, and full behavioral tests with branch
  coverage, including stress tests. Keep all checks in the same environment.
- Run affected checks during editing. Each completed code-refactor stage and
  final code handoff runs the full gate. Re-run after new changes, failures, or
  unresolved concerns; do not repeat unchanged successful suites without cause.
- Pure prose needs relevant structure and link checks. Execute the exact source
  of changed examples. Structural documentation changes run the documentation
  suite once without coverage; avoid chapter/series/full-gate duplication.
- Produce useful JUnit and machine-readable/browsable coverage reports when
  supported. Keep generated reports ignored. Record actual commands, results,
  skips, and tested revisions; never equate configured checks with passing ones.

## Documentation and tutorials

- Keep public documentation in English with reference contracts, practical
  guides, an accessible README, and a concise implemented-feature inventory.
  Explain behavior and limitations; never present plans as implemented features.
- Each tutorial series has a useful standalone introduction and one ordered
  table of contents. Link every published chapter; do not list planned chapters
  as available or duplicate the inventory in other documentation entry points.
  Use stable ordered topic filenames, such as NN-topic-name.md.
- Mark every complete copyable Python example with a project-specific execution
  marker, using `<!-- python-doc-exec -->` when no marker exists. Documentation
  tests extract and execute that exact Markdown source, not a copied equivalent.
- Teach the smallest useful operation first, add one concept at a time, and end
  with a realistic composition. Put observable results in inline assertions
  where practical. Explain why each result follows, what resolves or executes,
  and who owns state; do not duplicate assertions in expected-result sections.
- Examples are deterministic and independent. Mock connectivity, isolate file
  examples in TemporaryDirectory, and close async/resource scopes explicitly.
  Examples must not use production credentials or persistent external effects.
- Test chapter discovery against the single table of contents and published
  files. Update navigation and changelog when adding, renaming, reordering, or
  retiring chapters. Do not impose word counts, slogans, one test per chapter,
  or arbitrary exact example counts.
- Check relative links and anchors, CLI/API inventories, and executable snippets.
  If a site/export exists, generate it from canonical Markdown after checks,
  preserve exact examples, and validate navigation/assets. Avoid a competing
  manually maintained copy of the same documentation.

## Packaging and release integrity

- Declare build metadata, supported Python versions, license, project identity,
  and tool settings in pyproject.toml. Keep a real license file and accurate
  compatibility claims; do not silently change the license or support matrix.
- Keep one authoritative version source or verify all maintained copies agree,
  including runtime __version__ when present. Respect established version and
  tag conventions; for new projects use a documented PEP 440-compatible policy
  with compatibility-aware version increments, not a fixed release series.
- Use isolated/reproducible build tooling appropriate to the project. Build a
  wheel and source distribution from the verified release revision; include
  production code, required runtime resources, typing data, packaging metadata,
  license, and README. Keep local secrets, reports, environments, development
  scratch files, and unrelated artifacts out of distributions.
- A release is a separate requested operation. Prepare a nonempty dated
  changelog section for its version, retaining unreleased work separately.
  Check notes and metadata before promotion. Preserve the selected source SHA
  and artifact identity through the release; never silently replace a version's
  published tag or distribution.
- Do not publish, push, merge, delete branches, or contact collaborators merely
  because this policy was installed. Follow the authorized task scope, preserve
  unrelated changes, and honor existing approvals without asking again.

## GitHub feature delivery

Use issue -> feature branch -> pull request -> squash merge -> branch cleanup.

1. Create or reuse an issue in the configured GitHub repository for authorized
   feature/bug work. Record scope, public behavior, and acceptance criteria.
   Fetch the configured remote and base a feature branch on the current default
   branch. Prefer codex/<issue-number>-<description> unless the project uses an
   explicit alternative.
2. Keep implementation, tests, and documentation together. Follow the change
   workflow and full gate, commit, push, and open a PR against the default
   branch. Link the issue with Closes #number. Describe the final behavior,
   material limitations, and actual verification results.
3. Wait for all applicable checks on the exact latest PR head, including push
   and PR verification/builds. Honor required reviews and branch rules. Fix
   failures and re-check the updated head; earlier green checks do not approve
   new commits. Intentional workflow-condition exclusions are not missing
   checks. Keep PR metadata current.
4. When merging is within scope, squash merge with an expected-head-SHA guard.
   Preserve draft/review-only requests. Confirm merge and issue closure.
   Synchronize the local default branch and confirm the merged content.
5. Verify no post-review work remains on the feature branch before deleting
   its remote and local copies. Squash merges lose feature-commit ancestry;
   inspect merged content before a necessary forced local deletion. Finish
   on the default branch without discarding unrelated work.

## GitHub version publication

Use version update -> verified PR -> squash merge -> fast-forward release
-> CI quality/build -> PyPI -> matching Git tag and GitHub Release.

1. On a feature or dedicated release-preparation branch, update the canonical
   version and all maintained copies, including runtime metadata. Finalize a
   dated changelog section. Validate notes, run the full gate, and verify the
   latest PR's CI before squash merging.
2. Select the verified merged commit on the default branch. Fast-forward the
   persistent release branch to exactly that commit and push it. Never add
   release-only commits, force the release branch, or publish a feature head.
3. The configured release workflow verifies version notes and default-branch
   ancestry, runs the full gate, builds distributions, and publishes those
   artifacts to PyPI with Trusted Publishing. Use least-privilege permissions,
   an appropriate publishing environment, and serialized uploads.
4. After PyPI succeeds, CI creates the matching version tag and GitHub Release
   at the same source commit with changelog notes and the same wheel/source
   artifacts attached. Follow the configured tag format; for a new project,
   use the exact version without a v prefix. Avoid a competing manual release.
5. Verify completion, not dispatch: latest applicable CI results, successful
   registry publication, tag target, published Release, attached artifacts, and
   any applicable documentation deployment. Fetch the tag and report links.
   If PyPI succeeds and Release creation fails, retry only the failed final
   stage; never repeat a successful immutable-version upload. After uncertain
   publication, inspect remote state before any retry.
6. Keep the default and release branches. For a combined feature/release task,
   clean up the implementation branch only after publication succeeds and
   verification shows no unmerged work remains.

Apply this pipeline only when publication is part of the project and requested
task. A package deliberately not published to PyPI must state its actual release
destination instead. Missing credentials, environments, CI workflows, or required
approvals are concrete blockers to the affected stage; do not bypass branch or
registry controls or claim a release succeeded.
