# Release procedure

Releases use one source commit and the same wheel and source distribution on
PyPI and GitHub. Version metadata lives in `pyproject.toml`; tags use the exact
version without a `v` prefix. Finalize a nonempty dated changelog section before
reviewing and squash-merging the release changes into `main`.

Version increments reflect compatibility. During the 0.x series, a minor
increment introduces new features or incompatible public behavior; patch releases
contain compatible fixes. Version 0.2.0 changes `status` and `logs` to fixed JSON
output: remove the former JSON switch and read log paths from the `paths` array.
The [CLI reference](cli.md#output-and-errors) defines the output contract.

## One-time publisher configuration

The workflow uses the GitHub environment `pypi`, which GitHub creates when first
referenced if it does not exist. Publication requires the `release` branch;
the repository owner can additionally restrict that environment to this branch.
Retain any configured environment approval requirements. On PyPI, configure
the following GitHub Trusted Publisher. For the first upload, use a pending
publisher from the owning PyPI account's publishing page:

| Field | Value |
| --- | --- |
| PyPI project name | `lcl-fastapi` |
| GitHub owner | `gokurakujoudo` |
| Repository | `lcl-fastapi` |
| Workflow filename | `release.yml` |
| Environment | `pypi` |

No long-lived PyPI token belongs in this repository. See PyPI's
[publisher setup](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

## Publish a reviewed version

Wait for every applicable check on the exact PR head, including Windows/Linux
quality, combined 100% production branch coverage, distribution validation, and
the four independent downstream example jobs. Squash-merge with a head-SHA
guard and select the resulting commit on `main`. Create `release` at that commit
for the first publication, or fast-forward it for later versions; never force
the publication branch or add release-only commits.

The [release workflow](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/.github/workflows/release.yml) verifies the changelog
and `main` ancestry, then invokes the same complete quality workflow. Only
after all checks pass does the dedicated `pypi` job acquire an OIDC identity and
publish the tested distributions. The following job compares PyPI SHA-256
digests with those files, creates the matching tag and a draft GitHub Release,
verifies attached assets, and makes the release public.

Each job has a finite timeout; publications are serialized. The normal quality
workflow has read-only permissions. Only the PyPI job receives `id-token: write`,
and only the final GitHub publication job receives `contents: write`.

## Recover and verify

Inspect the workflow result rather than assuming dispatch means success. If
PyPI publication succeeds and GitHub publication fails, rerun only failed jobs.
The GitHub stage accepts an existing matching draft, verifies existing assets,
and uploads only missing files; it never replaces a published version's bytes.
Draft lookup uses the authenticated release collection because GitHub's
release-by-tag endpoint does not return unpublished drafts.
Do not rerun a successful PyPI upload, enable `skip-existing`, move a released
tag, or rebuild replacement artifacts for an existing version.

If the failure requires fixing publication tooling, review and merge that fix
into `main` without advancing `release` for the already-published version.
Run [Recover GitHub publication](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/.github/workflows/recover-release.yml) on
`main`, supplying the original release workflow's `publication_run_id`. It
requires a completed release-branch run with a successful PyPI job, checks out
that run's original source, downloads its retained distribution artifact, and
executes only the GitHub stage using the corrected tooling. It neither builds
distributions nor invokes the PyPI uploader. The tag and assets retain the
original release identity even though recovery tooling comes from newer `main`.

Confirm the PyPI version and hashes, Git tag target, public GitHub Release, and
attached wheel/source archive all refer to the selected source and artifacts.
Then synchronize local `main`, preserve `main` and `release`, and remove the
completed feature branch only after verifying no unmerged content remains.
