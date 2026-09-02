"""Resolve Sana's PostgreSQL connection without exposing database credentials."""

import os
from urllib.parse import quote


SUPABASE_PROJECT_REF = os.environ.get(
    "SUPABASE_PROJECT_REF",
    "mdpdzwhlpuuynkhcpicb",
)
SUPABASE_POOLER_REGION = os.environ.get(
    "SUPABASE_POOLER_REGION",
    "ap-northeast-2",
)

SCHEMA_LOCK_NAME = "sana.schema.initialization"
DEFAULT_SCHEMA_LOCK_TIMEOUT_SECONDS = 5


def acquire_schema_lock(db):
    """Serialize schema DDL without allowing an indefinite database wait.

    The lock is transaction-scoped, so it is released automatically when the
    caller commits or rolls back.  A short lock timeout is intentional:
    startup must not make the web process wait forever behind a long-running
    business transaction.  The caller can retry initialization on a later
    startup/request after rolling back the timed-out transaction.
    """
    if getattr(db, "_schema_lock_acquired", False) is True:
        return

    try:
        timeout_seconds = int(
            os.environ.get(
                "SANA_SCHEMA_LOCK_TIMEOUT_SECONDS",
                DEFAULT_SCHEMA_LOCK_TIMEOUT_SECONDS,
            )
        )
    except (TypeError, ValueError):
        timeout_seconds = DEFAULT_SCHEMA_LOCK_TIMEOUT_SECONDS
    timeout_seconds = max(1, min(timeout_seconds, 30))

    # set_config(..., true) is transaction-local and works through the
    # _PGConn compatibility wrapper as well as a native psycopg connection.
    db.execute(
        "SELECT set_config('lock_timeout', ?, true)",
        (f"{timeout_seconds}s",),
    )
    db.execute(
        "SELECT pg_advisory_xact_lock(hashtext(?))",
        (SCHEMA_LOCK_NAME,),
    )
    try:
        db._schema_lock_acquired = True
    except AttributeError:
        # Native/fake connections may not allow arbitrary attributes.  The
        # transaction-scoped database lock was still acquired successfully.
        pass


def resolve_database_url() -> str:
    """Prefer the working Supabase pooler when its managed password is present."""
    password = os.environ.get("SUPABASE_DB_PASSWORD")
    if password:
        encoded_password = quote(password, safe="")
        host = f"aws-0-{SUPABASE_POOLER_REGION}.pooler.supabase.com"
        return (
            f"postgresql://postgres.{SUPABASE_PROJECT_REF}:{encoded_password}"
            f"@{host}:5432/postgres?sslmode=require"
        )

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "Database connection is unavailable: set SUPABASE_DB_PASSWORD "
            "or provide a working DATABASE_URL."
        )
    return database_url
