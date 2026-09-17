"""One-shot production worker for billing lifecycle notification retries."""

import json
import os
import sys


def _run_cycle():
    from app import _connect_pg, run_billing_notification_retry_cycle

    return run_billing_notification_retry_cycle(_connect_pg)


def main():
    runtime = os.environ.get("SANA_ENV", "").strip().lower()
    if runtime not in {"production", "prod"}:
        raise RuntimeError(
            "run_billing_notification_retries.py requires SANA_ENV=production"
        )
    if os.environ.get("SANA_BILLING_NOTIFICATION_RETRY_WORKER") != "1":
        raise RuntimeError(
            "SANA_BILLING_NOTIFICATION_RETRY_WORKER=1 is required"
        )
    try:
        result = _run_cycle()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        return 1 if int(result.get("failed", 0) or 0) else 0
    except Exception:
        print(json.dumps({
            "status": "failed",
            "processed": 0,
            "sent": 0,
            "failed": 1,
        }, sort_keys=True), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())