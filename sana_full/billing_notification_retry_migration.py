"""Idempotent production migration for billing notification retries."""

import json

import psycopg2

from database_config import resolve_database_url


MIGRATION_LOCK_NAME = "sana.billing_notification_retry.migration"
MIGRATION_STATEMENTS = (
    """ALTER TABLE admin_notification_outbox
       ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0""",
    """ALTER TABLE admin_notification_outbox
       ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ""",
    """ALTER TABLE admin_notification_outbox
       ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ""",
    """ALTER TABLE admin_notification_outbox
       ADD COLUMN IF NOT EXISTS delivery_lock_token TEXT""",
    """ALTER TABLE admin_notification_outbox
       ADD COLUMN IF NOT EXISTS delivery_locked_at TIMESTAMPTZ""",
    """CREATE INDEX IF NOT EXISTS idx_admin_notification_outbox_retry
       ON admin_notification_outbox(
         notification_type,status,next_attempt_at,created_at
       )""",
)


def apply_migration(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (MIGRATION_LOCK_NAME,),
        )
        for statement in MIGRATION_STATEMENTS:
            cursor.execute(statement)


def main():
    connection = psycopg2.connect(resolve_database_url())
    try:
        apply_migration(connection)
        connection.commit()
        print(json.dumps({"status": "ok", "migration": MIGRATION_LOCK_NAME}))
        return 0
    except Exception:
        connection.rollback()
        print(json.dumps({"status": "failed", "migration": MIGRATION_LOCK_NAME}))
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())