import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from unittest.mock import patch

from sana_billing import billing_cleanup_health, run_billing_cleanup_once


class _Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _CleanupConnection:
    def __init__(self, acquired=True, state=None):
        self.acquired = acquired
        self.commits = 0
        self.rollbacks = 0
        self.sql = []
        self.state = state if state is not None else {
            "cleanup_run": None,
            "cleanup_skip": None,
        }

    @property
    def cleanup_run(self):
        return self.state["cleanup_run"]

    @property
    def cleanup_skip(self):
        return self.state["cleanup_skip"]

    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        if "pg_try_advisory_lock" in sql:
            return _Cursor({"acquired": self.acquired})
        if "pg_advisory_unlock" in sql:
            return _Cursor({"released": True})
        if "INSERT INTO sana_billing_cleanup_runs" in sql:
            self.state["cleanup_run"] = {
                "status": params[0],
                "scanned": params[1],
                "expired": params[2],
                "already_completed": params[3],
                "already_expired": params[4],
                "failed": params[5],
            }
            return _Cursor(None)
        if "INSERT INTO sana_billing_cleanup_skips" in sql:
            self.state["cleanup_skip"] = {
                "status": params[0],
                "scanned": params[1],
                "expired": params[2],
                "already_completed": params[3],
                "already_expired": params[4],
                "failed": params[5],
            }
            return _Cursor(None)
        raise AssertionError(f"unexpected query: {sql}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class _HealthConnection:
    def __init__(self, row=None):
        self.row = row

    def execute(self, sql, params=()):
        self.sql = sql
        return _Cursor(self.row)


class BillingCleanupWorkerTest(unittest.TestCase):
    def test_cleanup_health_needs_attention_when_no_result_exists(self):
        health = billing_cleanup_health(
            _HealthConnection(),
            now=datetime(2026, 9, 2, 12, 0, 0),
        )

        self.assertEqual("needs_attention", health["status"])
        self.assertTrue(health["is_stale"])
        self.assertIsNone(health["last_reliable_run_at"])
        self.assertNotIn("checkout", json.dumps(health).lower())
        self.assertNotIn("subscription", json.dumps(health).lower())

    def test_cleanup_health_uses_latest_completed_or_lock_skip(self):
        current = datetime(2026, 9, 2, 12, 0, 0)
        health = billing_cleanup_health(
            _HealthConnection({
                "run_at": current - timedelta(hours=47),
                "status": "skipped_locked",
            }),
            now=current,
        )

        self.assertEqual("healthy", health["status"])
        self.assertFalse(health["is_stale"])
        self.assertEqual("skipped_locked", health["last_reliable_status"])

    def test_cleanup_health_warns_after_more_than_48_hours(self):
        current = datetime(2026, 9, 2, 12, 0, 0)
        health = billing_cleanup_health(
            _HealthConnection({
                "run_at": current - timedelta(hours=48, seconds=1),
                "status": "completed",
            }),
            now=current,
        )

        self.assertEqual("needs_attention", health["status"])
        self.assertTrue(health["is_stale"])

    def test_cleanup_health_does_not_warn_at_exactly_48_hours(self):
        current = datetime(2026, 9, 2, 12, 0, 0)
        health = billing_cleanup_health(
            _HealthConnection({
                "run_at": current - timedelta(hours=48),
                "status": "completed",
            }),
            now=current,
        )

        self.assertEqual("healthy", health["status"])
        self.assertFalse(health["is_stale"])

    def test_cleanup_runs_once_and_redacts_unexpected_provider_fields(self):
        db = _CleanupConnection()
        with patch(
            "sana_billing.expire_stale_checkouts",
            return_value={
                "scanned": 2,
                "expired": 1,
                "already_completed": 1,
                "already_expired": 0,
                "failed": 0,
                "stripe_checkout_session_id": "cs_must_not_escape",
            },
        ) as cleanup:
            result = run_billing_cleanup_once(db)

        self.assertEqual(
            {
                "status": "completed",
                "scanned": 2,
                "expired": 1,
                "already_completed": 1,
                "already_expired": 0,
                "failed": 0,
            },
            result,
        )
        self.assertNotIn("cs_must_not_escape", json.dumps(result))
        cleanup.assert_called_once()
        self.assertEqual(2, db.commits)
        self.assertEqual("completed", db.cleanup_run["status"])
        self.assertNotIn("stripe_checkout_session_id", db.cleanup_run)

    def test_second_worker_skips_without_touching_checkout_rows(self):
        db = _CleanupConnection(acquired=False)
        with patch("sana_billing.expire_stale_checkouts") as cleanup:
            result = run_billing_cleanup_once(db)

        self.assertEqual("skipped_locked", result["status"])
        self.assertEqual(0, result["scanned"])
        cleanup.assert_not_called()
        self.assertFalse(any("pg_advisory_unlock" in sql for sql, _ in db.sql))
        self.assertIsNone(db.cleanup_run)
        self.assertEqual("skipped_locked", db.cleanup_skip["status"])

    def test_stripe_failure_keeps_last_saved_result(self):
        db = _CleanupConnection()
        with patch(
            "sana_billing.expire_stale_checkouts",
            return_value={
                "scanned": 3,
                "expired": 2,
                "already_completed": 1,
                "already_expired": 0,
                "failed": 0,
            },
        ):
            run_billing_cleanup_once(db)
        previous = dict(db.cleanup_run)

        with patch(
            "sana_billing.expire_stale_checkouts",
            side_effect=RuntimeError("Stripe checkout cs_private failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "cs_private"):
                run_billing_cleanup_once(db)

        self.assertEqual(previous, db.cleanup_run)

    def test_locked_contender_and_failing_owner_preserve_last_completed_run(self):
        state = {"cleanup_run": None, "cleanup_skip": None}
        previous_worker = _CleanupConnection(state=state)
        with patch(
            "sana_billing.expire_stale_checkouts",
            return_value={"scanned": 4, "expired": 2},
        ):
            run_billing_cleanup_once(previous_worker)
        previous = dict(state["cleanup_run"])

        contender = _CleanupConnection(acquired=False, state=state)
        with patch("sana_billing.expire_stale_checkouts") as cleanup:
            contender_result = run_billing_cleanup_once(contender)
        self.assertEqual("skipped_locked", contender_result["status"])
        cleanup.assert_not_called()

        failing_owner = _CleanupConnection(state=state)
        with patch(
            "sana_billing.expire_stale_checkouts",
            side_effect=RuntimeError("STRIPE_REQUEST_FAILED"),
        ):
            with self.assertRaisesRegex(RuntimeError, "STRIPE_REQUEST_FAILED"):
                run_billing_cleanup_once(failing_owner)

        self.assertEqual(previous, state["cleanup_run"])
        self.assertEqual("skipped_locked", state["cleanup_skip"]["status"])

    def test_worker_emits_only_counters_on_success(self):
        import billing_cleanup_worker

        db = _CleanupConnection()
        output = io.StringIO()
        with patch.dict(
            os.environ,
            {
                "SANA_ENV": "production",
                "SANA_BILLING_CLEANUP_WORKER": "1",
            },
            clear=False,
        ), patch.object(billing_cleanup_worker, "_connect_db", return_value=db), patch(
            "sana_billing.run_billing_cleanup_once",
            return_value={
                "status": "completed",
                "scanned": 1,
                "expired": 1,
                "already_completed": 0,
                "already_expired": 0,
                "failed": 0,
            },
        ):
            with redirect_stdout(output):
                exit_code = billing_cleanup_worker.main()

        self.assertEqual(0, exit_code)
        self.assertEqual(
            {
                "status": "completed",
                "scanned": 1,
                "expired": 1,
                "already_completed": 0,
                "already_expired": 0,
                "failed": 0,
            },
            json.loads(output.getvalue()),
        )

    def test_worker_redacts_failures_and_returns_nonzero(self):
        import billing_cleanup_worker

        db = _CleanupConnection()
        output = io.StringIO()
        with patch.dict(
            os.environ,
            {
                "SANA_ENV": "production",
                "SANA_BILLING_CLEANUP_WORKER": "1",
            },
            clear=False,
        ), patch.object(billing_cleanup_worker, "_connect_db", return_value=db), patch(
            "sana_billing.run_billing_cleanup_once",
            side_effect=RuntimeError("provider session cs_private"),
        ):
            with redirect_stdout(output):
                exit_code = billing_cleanup_worker.main()

        self.assertEqual(1, exit_code)
        self.assertEqual(
            {
                "status": "failed",
                "scanned": 0,
                "expired": 0,
                "already_completed": 0,
                "already_expired": 0,
                "failed": 1,
            },
            json.loads(output.getvalue()),
        )
        self.assertNotIn("cs_private", output.getvalue())


if __name__ == "__main__":
    unittest.main()
