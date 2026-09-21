"""ASGI entrypoint.

The single place an application instance is constructed at import time. Servers
point here:

    gunicorn -c gunicorn.conf.py src.asgi:app
    uvicorn src.asgi:app --reload

Keeping this separate from :mod:`src.main` means the factory can be imported by
tooling -- schema export, tests -- without configuration being required.
"""

from src.main import create_app

app = create_app()
