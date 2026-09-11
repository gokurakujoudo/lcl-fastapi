# Engineering and delivery

Read [the root policy](../AGENTS.md), [the implementation plan](development-plan.md),
and [the feature inventory](features.md) before changing public behavior.

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
the checked-out commit. Four downstream jobs each create their own virtual
environment, install that wheel plus one example project, and run its `verify.py`.
These jobs do not use source-path imports. `python -m scripts.verify_example minimal`
and `python -m scripts.verify_example composed` reproduce their installation and
verification steps locally after a build. Reports under `reports/examples/`
contain the wheel hash, platform, commands, output, and failure diagnostics.

The build metadata declares Python >=3.14. The initial evidence matrix is
CPython 3.14 on Windows and Linux; future interpreters are not yet verified.
Version `0.1.0` is maintained in pyproject.toml. Later releases use PEP 440
versions with compatibility-aware increments and exact version tags without a
`v` prefix, following the root release policy.

Changes are reviewed and squash-merged through a pull request linked to their
issue. An explicitly requested release advances `release` to the selected
verified commit on `main`. The [release workflow](releasing.md) repeats the
complete quality gate, publishes the tested distributions with PyPI Trusted
Publishing, and creates the matching version tag and GitHub Release.
