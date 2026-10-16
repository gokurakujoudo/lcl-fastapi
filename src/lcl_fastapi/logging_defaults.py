"""Stable logger names supplied to Python applications and LCL composition."""

# Unitless stdlib source names cover FastAPI and the two supported ASGI runtimes.
# Explicit names reset borrowed handlers and levels through native lclang takeover.
DEFAULT_TAKEOVER_LOGGERS = (
    "fastapi",
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "gunicorn",
    "gunicorn.error",
    "gunicorn.access",
)
