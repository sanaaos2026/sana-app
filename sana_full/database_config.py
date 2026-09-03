"""Resolve Sana's PostgreSQL connection without exposing database credentials."""

import os
import re
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
DATABASE_SCHEMA_ENV = "SANA_DATABASE_SCHEMA"
BILLING_TEST_SCHEMA_PREFIX = "sana_billing_test_"
_BILLING_TEST_SCHEMA_PATTERN = re.compile(
    rf"^{re.escape(BILLING_TEST_SCHEMA_PREFIX)}(?P<created_at>[0-9]{{10}})_[a-f0-9]{{20}}$"
)
_DATABASE_SCHEMA_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def resolve_database_schema():
    """Return the optional PostgreSQL schema used by this process.

    Production keeps the PostgreSQL default (``public``).  A schema is
    opt-in so disposable test runs can point every connection at an isolated
    namespace without changing the database URL or the production path.
    """
    schema = os.environ.get(DATABASE_SCHEMA_ENV, "").strip().lower()
    if not schema:
        return None
    if not _DATABASE_SCHEMA_PATTERN.fullmatch(schema):
        raise RuntimeError(
            f"{DATABASE_SCHEMA_ENV} must be a lowercase PostgreSQL identifier"
        )
    return schema


def build_billing_test_schema_name(created_at=None, token=None):
    """Build a disposable billing schema name with verifiable creation time."""
    import secrets
    import time

    created_at = int(time.time() if created_at is None else created_at)
    token = secrets.token_hex(10) if token is None else str(token).lower()
    if not re.fullmatch(r"[a-f0-9]{20}", token):
        raise ValueError("BILLING_TEST_SCHEMA_TOKEN_INVALID")
    return f"{BILLING_TEST_SCHEMA_PREFIX}{created_at:010d}_{token}"


def parse_billing_test_schema_created_at(schema):
    """Return the encoded creation timestamp, or None for untracked names."""
    match = _BILLING_TEST_SCHEMA_PATTERN.fullmatch(str(schema or "").lower())
    if match is None:
        return None
    return int(match.group("created_at"))


def has_required_tables(db, table_names):
    """Return whether the active database schema has the named tables.

    Table names are compared through ``information_schema`` rather than
    interpolated into SQL, so this helper is safe to use from test setup
    before deciding whether a full schema initialization is necessary.
    """
    names = tuple(dict.fromkeys(str(name) for name in table_names))
    if not names:
        return True
    for table_name in names:
        row = db.execute(
            """SELECT 1
               FROM information_schema.tables
               WHERE table_schema=current_schema() AND table_name=?""",
            (table_name,),
        ).fetchone()
        if row is None:
            return False
    return True


def has_required_columns(db, table_columns):
    """Return whether each listed table contains all required columns."""
    for table_name, columns in table_columns.items():
        required = tuple(dict.fromkeys(str(column) for column in columns))
        if not required:
            continue
        rows = db.execute(
            """SELECT column_name
               FROM information_schema.columns
               WHERE table_schema=current_schema() AND table_name=?""",
            (str(table_name),),
        ).fetchall()
        available = {row[0] for row in rows}
        if not set(required).issubset(available):
            return False
    return True


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
        "SELECT pg_advisory_xact_lock(hashtext(? || ':' || current_schema()))",
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
