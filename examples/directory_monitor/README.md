# Directory Observatory

An independent lcl-fastapi 0.3.0 application with a packaged static browser UI.
Follow [the complete tutorial](../../docs/build-a-service/02-directory-monitor.md) for the
configuration, API, resource ownership, operation commands and limitations.

From this directory on Windows:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\lcl-fastapi.exe serve -o config service.lclcfg
```

On Linux, use `python3.14 -m venv .venv`, `.venv/bin/python -m pip install .`, then
`.venv/bin/lcl-fastapi serve -o config service.lclcfg`.

For source-change verification, install the built framework wheel and this project
in one command, replacing the installation command above:

```powershell
.venv\Scripts\python.exe -m pip install ../../dist/lcl_fastapi-0.3.0-py3-none-any.whl .
```

Stop the manual service before running `.venv\Scripts\python.exe verify.py`
(Linux: `.venv/bin/python verify.py`). It runs this exact installed source using
native service processes and temporary data. From the repository root,
`python -m scripts.verify_example directory_monitor` builds an independent environment,
installs the selected wheel plus the example, and invokes that verifier.

No frontend build, CDN, external account or framework source-path import is needed.
Run one example service at a time. These are trusted local learning applications;
keep their configured loopback listener and read the tutorial's trust boundary.
