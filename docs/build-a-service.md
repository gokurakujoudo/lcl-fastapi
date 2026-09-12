# Build a service

Learn lcl-fastapi by building three complete services. Each chapter starts with a
concrete application and explains its configuration, resource ownership, API,
verification and limitations. Start with the catalog to learn the framework, or
open either browser-based example independently.

## Series contents

1. [Build a product catalog service](build-a-service/01-catalog.md) — typed routes,
   configuration, logging, a console entrance, and deployment preparation.
2. [Monitor a directory with a live browser UI](build-a-service/02-directory-monitor.md) —
   recursive background work, file APIs, static assets and server-sent events.
3. [Build an interactive LCL playground](build-a-service/03-lcl-playground.md) —
   worker-local sessions, native syntax/dependency inspection and evaluation replay.

## Before you start

Use CPython 3.14 on Windows or Linux and lcl-fastapi 0.3.0. Run one service at a
time; the framework supports one service per machine. All examples bind to
loopback, use local resources, and need no external account. Installation requires
package-index access or an operator-prepared wheelhouse.

Every chapter starts in an empty directory and prints the complete files to create
and extend. No repository checkout is needed. Build and verify one capability at a
time: configuration, lifespan resources, routes, background work or sessions, then
the browser and operations. The complete example code is linked at each chapter's
start as a reference. Chapters 2 and 3 include all static assets in their own wheels.
Documentation tests reconstruct those projects from the exact printed source;
their `verify.py` programs exercise installed native services in temporary directories.
The framework wheel does not contain downstream projects. See
[engineering checks](engineering.md) for the Windows/Linux installation matrix.

These examples teach local trusted workflows. Read each chapter's filesystem or
expression-execution boundary before using it with other people's data or serving
it outside your machine.
