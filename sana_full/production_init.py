"""One-shot production database initialization for Sana.

Run this as Railway's pre-deploy command, before starting Gunicorn. It is
deliberately separate from the web process so DDL and seed work never runs in
each worker.
"""

import os
import time


CONCURRENT_INIT_WAIT_SECONDS = 20


def _schema_ready(connect_db, has_required_tables, has_required_columns):
    db = connect_db()
    try:
        return (
            has_required_tables(
                db,
                (
                    "companies",
                    "tasks",
                    "user_accounts",
                    "admin_audit_log",
                    "company_memory_items",
                    "company_memory_versions",
                ),
            )
            and has_required_columns(
                db,
                {
                    "companies": (
                        "sds_done",
                        "main_goal",
                        "business_type",
                        "respondent_role",
                    ),
                    "user_accounts": (
                        "admin_role",
                        "account_status",
                        "pilot_cohort_number",
                    ),
                },
            )
        )
    finally:
        db.rollback()
        db.close()


def _wait_for_concurrent_initializer(
    connect_db,
    has_required_tables,
    has_required_columns,
):
    deadline = time.monotonic() + CONCURRENT_INIT_WAIT_SECONDS
    while time.monotonic() < deadline:
        if _schema_ready(connect_db, has_required_tables, has_required_columns):
            return True
        time.sleep(0.25)
    return False


def main():
    runtime = os.environ.get("SANA_ENV", "").strip().lower()
    if runtime not in {"production", "prod"}:
        raise RuntimeError("production_init.py requires SANA_ENV=production")
    if not os.environ.get("SESSION_SECRET"):
        raise RuntimeError("SESSION_SECRET is required for production initialization")

    from app import (
        _connect_pg,
        init_db,
        seed_db,
        seed_decision_impacts,
        seed_knowledge_db,
    )
    from database_config import has_required_columns, has_required_tables

    schema_check = (
        _connect_pg,
        has_required_tables,
        has_required_columns,
    )
    if _schema_ready(*schema_check):
        seed_db()
        seed_decision_impacts()
        seed_knowledge_db()
        print("[production-init] schema already ready; skipped DDL", flush=True)
        return

    try:
        init_db()
    except (TimeoutError, __import__("psycopg2").errors.QueryCanceled) as exc:
        if not _wait_for_concurrent_initializer(*schema_check):
            raise RuntimeError(
                "Concurrent schema initialization did not become ready "
                f"within {CONCURRENT_INIT_WAIT_SECONDS} seconds"
            ) from exc
        print(
            "[production-init] concurrent initializer completed schema setup",
            flush=True,
        )
        return
    seed_db()
    seed_decision_impacts()
    seed_knowledge_db()
    print("[production-init] database initialization and seeding completed", flush=True)


if __name__ == "__main__":
    main()