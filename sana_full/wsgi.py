"""Production WSGI entrypoint for Sana.

Database initialization is intentionally not performed on import. Railway runs
production_init.py once before starting Gunicorn, while this module only
exposes the Flask application to the web workers.
"""

from app import app

__all__ = ["app"]