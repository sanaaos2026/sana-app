"""One-shot production worker for stale Stripe Checkout cleanup.

Railway runs this process from a separate cron service.  It deliberately does
not start Flask or an in-process scheduler, and it exits after one guarded run.
"""

import json
import os
import sys


def _result(status, **counts):
    return {
        "status": status,
        **{
            key: int(counts.get(key, 0) or 0)
            for key in (
                "scanned",
                "expired",
                "already_completed",
                "already_expired",
                "failed",
            )
        },
    }


def _connect_db():
    # Importing app here keeps module-level tests and help commands side-effect
    # free while reusing Sana's PostgreSQL compatibility wrapper in production.
    from app import _connect_pg

    return _connect_pg()


def main():
    runtime = os.environ.get("SANA_ENV", "").strip().lower()
    if runtime not in {"production", "prod"}:
        raise RuntimeError("billing_cleanup_worker.py requires SANA_ENV=production")
    if os.environ.get("SANA_BILLING_CLEANUP_WORKER") != "1":
        raise RuntimeError(
            "SANA_BILLING_CLEANUP_WORKER=1 is required for the cleanup worker"
        )

    from sana_billing import run_billing_cleanup_once

    db = None
    try:
        db = _connect_db()
        result = run_billing_cleanup_once(db)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        return 0
    except Exception:
        # Keep provider errors out of cron logs. The non-zero exit lets Railway
        # surface the failed run while the next daily invocation retries it.
        result = _result("failed", failed=1)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        return 1
    finally:
        if db is not None:
            db.close()


if __name__ == "__main__":
    sys.exit(main())