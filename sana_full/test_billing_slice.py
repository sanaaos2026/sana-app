import secrets
import hashlib
import hmac
import json
import time
import unittest
import psycopg2
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from werkzeug.security import generate_password_hash

import app as sana_app
import os
import re
import shutil
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen
from sana_billing import (
    complete_checkout,
    expire_stale_checkouts,
    process_stripe_event,
)


class BillingSliceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sana_app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        cls.schema = sana_app.create_billing_test_schema()
        cls.addClassCleanup(cls._cleanup_test_schema)
        sana_app.init_billing_test_db()
        cls.run_token = secrets.token_hex(6).upper()
        cls.admin_id = "ACC-BILL-ADMIN-" + secrets.token_hex(4).upper()
        cls.company_ids = [
            "CO-BILL-" + secrets.token_hex(4).upper(),
            "CO-BILL-" + secrets.token_hex(4).upper(),
        ]
        cls.account_ids = [
            "ACC-BILL-" + secrets.token_hex(4).upper(),
            "ACC-BILL-" + secrets.token_hex(4).upper(),
        ]
        cls.additional_owner_id = "ACC-BILL-" + secrets.token_hex(4).upper()
        cls.coupon_codes = (
            f"BILLTESTFREE-{cls.run_token}",
            f"BILLTESTTEN-{cls.run_token}",
            f"BILLTESTIDEMPOTENT-{cls.run_token}",
        )
        with sana_app.app.app_context():
            db = sana_app.get_db()
            for index, company_id in enumerate(cls.company_ids):
                db.execute(
                    """INSERT INTO companies
                       (company_id,name,company_code,signup_code,lifecycle_status)
                       VALUES (?,?,?,?,?)""",
                    (
                        company_id, f"Billing Test {index}",
                        f"BILL-{index}-{secrets.token_hex(2)}",
                        f"BILL-SIGN-{index}-{secrets.token_hex(2)}", "Registered",
                    ),
                )
                db.execute(
                    """INSERT INTO user_accounts
                       (account_id,email,password_hash,company_id,is_admin,admin_role,
                        admin_permissions,account_status)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        cls.account_ids[index], f"billing-{secrets.token_hex(3)}@example.test",
                        generate_password_hash("test"), company_id, 0,
                        "COMPANY_OWNER", "[]", "active",
                    ),
                )
            db.execute(
                """INSERT INTO user_accounts
                   (account_id,email,password_hash,company_id,is_admin,admin_role,
                    admin_permissions,account_status)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    cls.additional_owner_id,
                    f"billing-owner-{secrets.token_hex(3)}@example.test",
                    generate_password_hash("test"), cls.company_ids[0], 0,
                    "COMPANY_OWNER", "[]", "active",
                ),
            )
            db.execute(
                """INSERT INTO user_accounts
                   (account_id,email,password_hash,company_id,is_admin,admin_role,
                    admin_permissions,account_status)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    cls.admin_id, f"billing-admin-{secrets.token_hex(3)}@example.test",
                    generate_password_hash("test"), None, 1, "SUPER_ADMIN", "[]", "active",
                ),
            )
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls._cleanup_test_schema()

    @classmethod
    def _cleanup_test_schema(cls):
        if getattr(cls, "schema", None):
            sana_app.drop_billing_test_schema(cls.schema)
            cls.schema = None
        sana_app.reset_database_schema()

    def test_billing_slice_uses_a_disposable_schema(self):
        with sana_app.app.app_context():
            active_schema = sana_app.get_db().execute(
                "SELECT current_schema()"
            ).fetchone()[0]
        self.assertEqual(self.schema, active_schema)
        self.assertNotEqual("public", active_schema)

    def test_stale_schema_cleanup_deletes_only_old_inactive_test_schemas(self):
        now = int(time.time())
        old_schema = sana_app.build_billing_test_schema_name(
            created_at=now - (48 * 60 * 60),
            token=secrets.token_hex(10),
        )
        recent_schema = sana_app.build_billing_test_schema_name(
            created_at=now - 60,
            token=secrets.token_hex(10),
        )
        legacy_schema = f"sana_billing_test_{secrets.token_hex(10)}"
        unrelated_schema = sana_app.build_billing_test_schema_name(
            created_at=now - (48 * 60 * 60),
            token=secrets.token_hex(10),
        ).replace("sana_billing_test_", "sana_other_test_", 1)
        conn = psycopg2.connect(sana_app.DATABASE_URL)
        conn.autocommit = True
        try:
            with conn.cursor() as cursor:
                for schema in (
                    old_schema,
                    recent_schema,
                    legacy_schema,
                    unrelated_schema,
                ):
                    cursor.execute(
                        f'CREATE SCHEMA "{schema}"'
                    )
            result = sana_app.cleanup_stale_billing_test_schemas(now=now)
            self.assertEqual([old_schema], result["deleted"])
            with conn.cursor() as cursor:
                for schema in (
                    old_schema,
                    recent_schema,
                    legacy_schema,
                    unrelated_schema,
                    self.schema,
                ):
                    cursor.execute(
                        "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=%s)",
                        (schema,),
                    )
                    exists = cursor.fetchone()[0]
                    if schema == old_schema:
                        self.assertFalse(exists)
                    else:
                        self.assertTrue(exists)
                cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='public')"
                )
                self.assertTrue(cursor.fetchone()[0])
        finally:
            with conn.cursor() as cursor:
                for schema in (recent_schema, legacy_schema, unrelated_schema):
                    cursor.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.close()

    def test_stale_schema_cleanup_never_connects_in_production(self):
        with patch.dict(os.environ, {"SANA_ENV": "production"}):
            with patch.object(sana_app.psycopg2, "connect") as connect:
                result = sana_app.cleanup_stale_billing_test_schemas()
        self.assertEqual("skipped_production", result["status"])
        self.assertEqual([], result["deleted"])
        connect.assert_not_called()

    def client_for(self, account_index=None, admin=False):
        client = sana_app.app.test_client()
        account_id = self.admin_id if admin else self.account_ids[account_index]
        with sana_app.app.app_context():
            row = sana_app.get_db().execute(
                "SELECT * FROM user_accounts WHERE account_id=?", (account_id,)
            ).fetchone()
        with client.session_transaction() as session:
            session.update({
                "account_id": row["account_id"], "company_id": row["company_id"],
                "email": row["email"], "admin_role": row["admin_role"],
                "account_status": row["account_status"],
            })
        return client

    def _insert_billing_notification(
        self, db, *, status="queued", attempt_count=0,
        notification_type="billing_subscription_past_due",
    ):
        notification_id = "NTF-BILL-RETRY-" + secrets.token_hex(4).upper()
        recovery_action = (
            "update_payment"
            if notification_type.endswith("past_due")
            else "restart_subscription"
        )
        db.execute(
            """INSERT INTO admin_notification_outbox
               (notification_id,notification_type,recipient_email,company_id,
                status,payload_json,created_by,attempt_count,next_attempt_at,
                error_code)
               VALUES (?,?,?,?,?,?,?,?,now() - INTERVAL '1 second',?)""",
            (
                notification_id,
                notification_type,
                f"retry-{secrets.token_hex(3)}@example.test",
                self.company_ids[1],
                status,
                json.dumps({
                    "event_id": "evt_retry_test",
                    "status": (
                        "past_due"
                        if notification_type.endswith("past_due")
                        else "canceled"
                    ),
                    "recovery_action": recovery_action,
                    "recovery_url": (
                        f"https://example.test/pricing?"
                        f"billing_action={recovery_action}"
                    ),
                }),
                self.account_ids[1],
                attempt_count,
                "PreviousError" if status == "failed" else None,
            ),
        )
        db.commit()
        return notification_id

    def test_billing_notification_retry_sends_due_message_and_clears_lock(self):
        with sana_app.app.app_context():
            db = sana_app.get_db()
            notification_id = self._insert_billing_notification(db)
            original_claim = sana_app._claim_billing_notification_for_retry
            with (
                patch.object(sana_app, "RESEND_API_KEY", "test-key"),
                patch.object(
                    sana_app,
                    "_claim_billing_notification_for_retry",
                    side_effect=lambda current_db: original_claim(
                        current_db, notification_id=notification_id
                    ),
                ),
                patch.object(sana_app.resend.Emails, "send", return_value={"id": "email_1"})
                as send,
            ):
                result = sana_app.process_billing_notification_retries(db)

            self.assertEqual(
                {"status": "processed", "processed": 1, "sent": 1, "failed": 0},
                result,
            )
            row = db.execute(
                """SELECT status,attempt_count,last_attempt_at,next_attempt_at,
                          error_code,delivery_lock_token,delivery_locked_at,
                          payload_json
                   FROM admin_notification_outbox WHERE notification_id=?""",
                (notification_id,),
            ).fetchone()
            self.assertEqual("sent", row["status"])
            self.assertEqual(1, row["attempt_count"])
            self.assertIsNotNone(row["last_attempt_at"])
            self.assertIsNone(row["next_attempt_at"])
            self.assertIsNone(row["error_code"])
            self.assertIsNone(row["delivery_lock_token"])
            self.assertIsNone(row["delivery_locked_at"])
            payload = json.loads(row["payload_json"])
            self.assertNotIn("stripe_customer_id", payload)
            self.assertNotIn("stripe_subscription_id", payload)
            self.assertEqual(1, send.call_count)

    def test_billing_notification_retry_backs_off_and_stops_at_max_attempts(self):
        with sana_app.app.app_context():
            db = sana_app.get_db()
            notification_id = self._insert_billing_notification(
                db,
                status="failed",
                attempt_count=sana_app._BILLING_NOTIFICATION_MAX_ATTEMPTS - 2,
                notification_type="billing_subscription_canceled",
            )
            original_claim = sana_app._claim_billing_notification_for_retry
            with (
                patch.object(sana_app, "RESEND_API_KEY", "test-key"),
                patch.object(
                    sana_app,
                    "_claim_billing_notification_for_retry",
                    side_effect=lambda current_db: original_claim(
                        current_db, notification_id=notification_id
                    ),
                ),
                patch.object(
                    sana_app.resend.Emails,
                    "send",
                    side_effect=RuntimeError("temporary outage"),
                ) as send,
            ):
                first = sana_app.process_billing_notification_retries(db)
                row = db.execute(
                    """SELECT status,attempt_count,last_attempt_at,next_attempt_at,
                              error_code
                       FROM admin_notification_outbox WHERE notification_id=?""",
                    (notification_id,),
                ).fetchone()
                self.assertEqual(1, first["failed"])
                self.assertEqual("failed", row["status"])
                self.assertEqual(
                    sana_app._BILLING_NOTIFICATION_MAX_ATTEMPTS - 1,
                    row["attempt_count"],
                )
                self.assertEqual("RuntimeError", row["error_code"])
                self.assertGreater(row["next_attempt_at"], row["last_attempt_at"])

                db.execute(
                    """UPDATE admin_notification_outbox
                       SET next_attempt_at=now() - INTERVAL '1 second'
                       WHERE notification_id=?""",
                    (notification_id,),
                )
                db.commit()
                second = sana_app.process_billing_notification_retries(db)
                exhausted = db.execute(
                    """SELECT attempt_count,next_attempt_at,error_code
                       FROM admin_notification_outbox WHERE notification_id=?""",
                    (notification_id,),
                ).fetchone()
                third = sana_app.process_billing_notification_retries(db)

            self.assertEqual(1, second["failed"])
            self.assertEqual(
                sana_app._BILLING_NOTIFICATION_MAX_ATTEMPTS,
                exhausted["attempt_count"],
            )
            self.assertIsNone(exhausted["next_attempt_at"])
            self.assertEqual("RuntimeError", exhausted["error_code"])
            self.assertEqual(0, third["processed"])
            self.assertEqual(2, send.call_count)

    def test_billing_notification_retry_claim_blocks_a_second_worker(self):
        with sana_app.app.app_context():
            db = sana_app.get_db()
            notification_id = self._insert_billing_notification(db)
            first_claim = sana_app._claim_billing_notification_for_retry(
                db, notification_id=notification_id
            )
            second_claim = sana_app._claim_billing_notification_for_retry(
                db, notification_id=notification_id
            )
            self.assertIsNotNone(first_claim)
            self.assertIsNone(second_claim)
            self.assertEqual(1, first_claim["attempt_count"])
            sana_app._finish_billing_notification_retry(
                db, first_claim, sent=True
            )

    def test_retry_migration_upgrades_a_preexisting_outbox(self):
        from billing_notification_retry_migration import apply_migration

        with sana_app.app.app_context():
            db = sana_app.get_db()
            db.execute(
                "DROP INDEX IF EXISTS idx_admin_notification_outbox_retry"
            )
            for column in (
                "delivery_locked_at",
                "delivery_lock_token",
                "next_attempt_at",
                "last_attempt_at",
                "attempt_count",
            ):
                db.execute(
                    f"ALTER TABLE admin_notification_outbox "
                    f"DROP COLUMN IF EXISTS {column}"
                )
            db.commit()

            apply_migration(db._conn)
            db.commit()
            columns = sana_app._columns_of(db, "admin_notification_outbox")
            self.assertTrue({
                "attempt_count",
                "last_attempt_at",
                "next_attempt_at",
                "delivery_lock_token",
                "delivery_locked_at",
            }.issubset(columns))
            index_exists = db.execute(
                """SELECT 1 FROM pg_indexes
                   WHERE schemaname=current_schema()
                     AND indexname='idx_admin_notification_outbox_retry'"""
            ).fetchone()
            self.assertIsNotNone(index_exists)

    def test_production_retry_worker_processes_a_durable_initial_failure(self):
        import run_billing_notification_retries as retry_worker

        with sana_app.app.app_context():
            db = sana_app.get_db()
            db.execute("DELETE FROM admin_notification_outbox")
            with (
                patch.object(sana_app, "RESEND_API_KEY", "test-key"),
                patch.object(
                    sana_app.resend.Emails,
                    "send",
                    side_effect=RuntimeError("temporary outage"),
                ),
            ):
                lifecycle_result = sana_app._send_billing_lifecycle_notification(
                    db,
                    {
                        "event_id": "evt_worker_retry",
                        "status": "past_due",
                    },
                    self.company_ids[1],
                )
                delivery = lifecycle_result["deliveries"][0]
            db.commit()
            self.assertEqual("failed", delivery["status"])
            db.execute(
                """UPDATE admin_notification_outbox
                   SET next_attempt_at=now() - INTERVAL '1 second'
                   WHERE notification_id=?""",
                (delivery["notification_id"],),
            )
            db.commit()

            with (
                patch.dict(os.environ, {
                    "SANA_ENV": "production",
                    "SANA_BILLING_NOTIFICATION_RETRY_WORKER": "1",
                    "SESSION_SECRET": "s" * 32,
                }),
                patch.object(sana_app, "RESEND_API_KEY", "test-key"),
                patch.object(
                    sana_app.resend.Emails,
                    "send",
                    return_value={"id": "email_worker"},
                ),
                patch("builtins.print"),
            ):
                exit_code = retry_worker.main()

            row = db.execute(
                """SELECT status,attempt_count,error_code,next_attempt_at
                   FROM admin_notification_outbox WHERE notification_id=?""",
                (delivery["notification_id"],),
            ).fetchone()
            self.assertEqual(0, exit_code)
            self.assertEqual("sent", row["status"])
            self.assertEqual(2, row["attempt_count"])
            self.assertIsNone(row["error_code"])
            self.assertIsNone(row["next_attempt_at"])

    def test_discount_free_activation_payment_and_tenant_isolation(self):
        with sana_app.app.app_context():
            db = sana_app.get_db()
            db.execute(
                "DELETE FROM sana_company_subscriptions WHERE company_id=?",
                (self.company_ids[1],),
            )
            db.commit()
        admin = self.client_for(admin=True)
        coupon = admin.post("/api/admin/billing/coupons", json={
            "code": self.coupon_codes[0], "discount_type": "percent",
            "discount_value": 100, "max_redemptions": 1,
            "reason": "اختبار التفعيل المجاني",
        })
        self.assertEqual(201, coupon.status_code, coupon.get_data(as_text=True))

        company_a = self.client_for(0)
        free = company_a.post(
            "/api/billing/checkout", json={"code": self.coupon_codes[0]}
        )
        self.assertEqual(200, free.status_code, free.get_data(as_text=True))
        self.assertTrue(free.get_json()["data"]["free"])
        self.assertEqual(
            "free", company_a.get("/api/billing/offer").get_json()["data"]["subscription"]["status"]
        )
        self.assertIsNone(
            self.client_for(1).get("/api/billing/offer").get_json()["data"]["subscription"]
        )

        paid_coupon = admin.post("/api/admin/billing/coupons", json={
            "code": self.coupon_codes[1], "discount_type": "percent",
            "discount_value": 10, "reason": "اختبار خصم الدفع",
        })
        self.assertEqual(201, paid_coupon.status_code)
        with patch("sana_billing.create_checkout", return_value={
            "session_id": f"cs_billtest_checkout_{self.run_token}",
            "url": "https://checkout.stripe.test/session",
        }) as checkout:
            paid = self.client_for(1).post(
                "/api/billing/checkout", json={"code": self.coupon_codes[1]}
            )
        self.assertEqual(200, paid.status_code)
        self.assertEqual("https://checkout.stripe.test/session", paid.get_json()["data"]["url"])
        self.assertEqual(44100, checkout.call_args.kwargs["amount_minor"])

    def test_admin_controls_require_admin_and_update_subscription(self):
        denied = self.client_for(0).get("/api/admin/billing")
        self.assertEqual(403, denied.status_code)
        admin = self.client_for(admin=True)
        granted = admin.post(
            f"/api/admin/billing/subscriptions/{self.company_ids[1]}/free",
            json={"period_days": 45, "reason": "اختبار المنحة"},
        )
        self.assertEqual(200, granted.status_code, granted.get_data(as_text=True))
        paused = admin.patch(
            f"/api/admin/billing/subscriptions/{self.company_ids[1]}",
            json={"action": "pause", "reason": "اختبار الإيقاف"},
        )
        self.assertEqual(200, paused.status_code)
        self.assertEqual("paused", paused.get_json()["data"]["status"])

    def test_stale_checkout_cleanup_expires_only_old_open_sessions(self):
        from sana_billing import STALE_CHECKOUT_MIN_AGE_HOURS

        old_session = "cs_billtest_stale_" + secrets.token_hex(4)
        recent_session = "cs_billtest_recent_" + secrets.token_hex(4)
        paid_session = "cs_billtest_paid_" + secrets.token_hex(4)
        interrupted_company_id = "CO-BILL-INTERRUPTED-" + secrets.token_hex(4).upper()
        interrupted_subscription_id = (
            "SUB-BILL-INTERRUPTED-" + secrets.token_hex(4).upper()
        )
        interrupted_session = "cs_billtest_interrupted_" + secrets.token_hex(4)
        now = datetime.utcnow()
        self.addCleanup(
            self._delete_explicit_fixture,
            interrupted_company_id,
            interrupted_subscription_id,
        )
        with sana_app.app.app_context():
            db = sana_app.get_db()
            db.execute(
                "DELETE FROM sana_company_subscriptions WHERE company_id IN (?,?)",
                (self.company_ids[0], self.company_ids[1]),
            )
            db.execute(
                """INSERT INTO companies
                   (company_id,name,company_code,signup_code,lifecycle_status)
                   VALUES (?,?,?,?,?)""",
                (
                    interrupted_company_id,
                    "Billing interrupted test residue",
                    "BILL-INTERRUPTED-" + secrets.token_hex(2),
                    "BILL-SIGN-INTERRUPTED-" + secrets.token_hex(2),
                    "Registered",
                ),
            )
            db.execute(
                """INSERT INTO sana_company_subscriptions
                   (subscription_id,company_id,status,stripe_checkout_session_id,
                    stripe_checkout_session_created_at,amount_minor)
                   VALUES (?,?,?,?,?,?)""",
                (
                    interrupted_subscription_id,
                    interrupted_company_id,
                    "pending",
                    interrupted_session,
                    now - timedelta(hours=STALE_CHECKOUT_MIN_AGE_HOURS + 3),
                    49000,
                ),
            )
            db.execute(
                """INSERT INTO sana_company_subscriptions
                   (subscription_id,company_id,status,stripe_checkout_session_id,
                    stripe_checkout_session_created_at,amount_minor)
                   VALUES (?,?,?,?,?,?)""",
                (
                    "SUB-BILL-STALE-" + secrets.token_hex(4).upper(),
                    self.company_ids[0], "pending", old_session,
                    now - timedelta(hours=STALE_CHECKOUT_MIN_AGE_HOURS + 2), 49000,
                ),
            )
            db.execute(
                """INSERT INTO sana_company_subscriptions
                   (subscription_id,company_id,status,stripe_checkout_session_id,
                    stripe_checkout_session_created_at,amount_minor)
                   VALUES (?,?,?,?,?,?)""",
                (
                    "SUB-BILL-RECENT-" + secrets.token_hex(4).upper(),
                    self.company_ids[1], "pending", recent_session,
                    now - timedelta(hours=2), 49000,
                ),
            )
            db.commit()

            with patch("sana_billing._stripe_request", side_effect=[
                {"id": old_session, "status": "open", "payment_status": "unpaid"},
                {"id": old_session, "status": "expired", "payment_status": "unpaid"},
            ]) as stripe_request:
                result = expire_stale_checkouts(
                    db, company_ids=self.company_ids
                )

            self.assertEqual(1, result["scanned"])
            self.assertEqual(1, result["expired"])
            self.assertEqual(0, result["already_completed"])
            self.assertEqual(
                "canceled",
                db.execute(
                    "SELECT status FROM sana_company_subscriptions WHERE company_id=?",
                    (self.company_ids[0],),
                ).fetchone()["status"],
            )
            self.assertEqual(
                "pending",
                db.execute(
                    "SELECT status FROM sana_company_subscriptions WHERE company_id=?",
                    (self.company_ids[1],),
                ).fetchone()["status"],
            )
            self.assertEqual(
                "pending",
                db.execute(
                    "SELECT status FROM sana_company_subscriptions WHERE company_id=?",
                    (interrupted_company_id,),
                ).fetchone()["status"],
            )
            self.assertEqual(2, stripe_request.call_count)
            self.assertIn(f"/v1/checkout/sessions/{old_session}/expire",
                          stripe_request.call_args_list[1].args)
            self.assertNotIn(recent_session, str(stripe_request.call_args_list))
            self.assertNotIn(interrupted_session, str(stripe_request.call_args_list))

            db.execute(
                """UPDATE sana_company_subscriptions
                   SET status='pending',stripe_checkout_session_id=?,
                       stripe_checkout_session_created_at=?
                   WHERE company_id=?""",
                (
                    paid_session,
                    now - timedelta(hours=STALE_CHECKOUT_MIN_AGE_HOURS + 2),
                    self.company_ids[0],
                ),
            )
            db.commit()
            with patch("sana_billing._stripe_request", return_value={
                "id": paid_session, "status": "complete", "payment_status": "paid",
            }) as completed_request:
                result = expire_stale_checkouts(
                    db, company_ids=self.company_ids
                )
            self.assertEqual(1, result["already_completed"])
            self.assertEqual(0, result["expired"])
            self.assertEqual("pending", db.execute(
                "SELECT status FROM sana_company_subscriptions WHERE company_id=?",
                (self.company_ids[0],),
            ).fetchone()["status"])
            self.assertEqual(1, completed_request.call_count)

    def _delete_explicit_fixture(self, company_id, subscription_id):
        with sana_app.app.app_context():
            db = sana_app.get_db()
            db.execute(
                "DELETE FROM sana_company_subscriptions WHERE subscription_id=?",
                (subscription_id,),
            )
            db.execute(
                "DELETE FROM companies WHERE company_id=?",
                (company_id,),
            )
            db.commit()

    def test_stale_cleanup_releases_failed_retrieve_lock_before_next_stripe_call(self):
        from sana_billing import STALE_CHECKOUT_MIN_AGE_HOURS

        failed_session = "cs_billtest_retrieve_timeout_" + secrets.token_hex(4)
        blocked_session = "cs_billtest_expire_blocked_" + secrets.token_hex(4)
        now = datetime.utcnow()
        with sana_app.app.app_context():
            db = sana_app.get_db()
            db.execute(
                "DELETE FROM sana_company_subscriptions WHERE company_id IN (?,?)",
                (self.company_ids[0], self.company_ids[1]),
            )
            for company_id, subscription_id, session_id, age_hours in (
                (
                    self.company_ids[0],
                    "SUB-BILL-RETRIEVE-TIMEOUT-" + secrets.token_hex(4).upper(),
                    failed_session,
                    STALE_CHECKOUT_MIN_AGE_HOURS + 4,
                ),
                (
                    self.company_ids[1],
                    "SUB-BILL-EXPIRE-BLOCKED-" + secrets.token_hex(4).upper(),
                    blocked_session,
                    STALE_CHECKOUT_MIN_AGE_HOURS + 2,
                ),
            ):
                db.execute(
                    """INSERT INTO sana_company_subscriptions
                       (subscription_id,company_id,status,stripe_checkout_session_id,
                        stripe_checkout_session_created_at,amount_minor)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        subscription_id,
                        company_id,
                        "pending",
                        session_id,
                        now - timedelta(hours=age_hours),
                        49000,
                    ),
                )
            db.commit()

        cleanup_db = sana_app._connect_pg()
        retry_db = sana_app._connect_pg()
        expire_started = Event()
        release_expire = Event()

        def retrieve_checkout_for_cleanup(session_id):
            if session_id == failed_session:
                raise TimeoutError("simulated Stripe retrieve timeout")
            self.assertEqual(blocked_session, session_id)
            return {
                "id": blocked_session,
                "status": "open",
                "payment_status": "unpaid",
            }

        def expire_checkout_for_cleanup(method, path, data=None):
            self.assertEqual("POST", method)
            self.assertIn(f"/v1/checkout/sessions/{blocked_session}/expire", path)
            expire_started.set()
            self.assertTrue(release_expire.wait(timeout=15))
            return {
                "id": blocked_session,
                "status": "expired",
                "payment_status": "unpaid",
            }

        try:
            with patch(
                "sana_billing.retrieve_checkout",
                side_effect=retrieve_checkout_for_cleanup,
            ), patch(
                "sana_billing._stripe_request",
                side_effect=expire_checkout_for_cleanup,
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    cleanup_future = executor.submit(
                        expire_stale_checkouts,
                        cleanup_db,
                        limit=2,
                        company_ids=self.company_ids,
                    )
                    self.assertTrue(expire_started.wait(timeout=15))

                    retry_row = retry_db.execute(
                        """SELECT status
                           FROM sana_company_subscriptions
                           WHERE stripe_checkout_session_id=?
                           FOR UPDATE NOWAIT""",
                        (failed_session,),
                    ).fetchone()
                    self.assertEqual("pending", retry_row["status"])
                    retry_db.rollback()

                    release_expire.set()
                    cleanup_result = cleanup_future.result(timeout=15)
        finally:
            release_expire.set()
            cleanup_db.close()
            retry_db.close()

        with sana_app.app.app_context():
            db = sana_app.get_db()
            rows = db.execute(
                """SELECT stripe_checkout_session_id,status
                   FROM sana_company_subscriptions
                   WHERE company_id IN (?,?)
                   ORDER BY stripe_checkout_session_id""",
                (self.company_ids[0], self.company_ids[1]),
            ).fetchall()

        statuses = {
            row["stripe_checkout_session_id"]: row["status"] for row in rows
        }
        self.assertEqual(2, cleanup_result["scanned"])
        self.assertEqual(1, cleanup_result["failed"])
        self.assertEqual(1, cleanup_result["expired"])
        self.assertEqual("pending", statuses[failed_session])
        self.assertEqual("canceled", statuses[blocked_session])

    def test_stale_cleanup_keeps_pending_when_stripe_expire_fails_and_releases_lock(self):
        from sana_billing import STALE_CHECKOUT_MIN_AGE_HOURS

        session_id = "cs_billtest_expire_timeout_" + secrets.token_hex(4)
        now = datetime.utcnow()
        with sana_app.app.app_context():
            db = sana_app.get_db()
            db.execute(
                "DELETE FROM sana_company_subscriptions WHERE company_id=?",
                (self.company_ids[0],),
            )
            db.execute(
                """INSERT INTO sana_company_subscriptions
                   (subscription_id,company_id,status,stripe_checkout_session_id,
                    stripe_checkout_session_created_at,amount_minor)
                   VALUES (?,?,?,?,?,?)""",
                (
                    "SUB-BILL-EXPIRE-TIMEOUT-" + secrets.token_hex(4).upper(),
                    self.company_ids[0],
                    "pending",
                    session_id,
                    now - timedelta(hours=STALE_CHECKOUT_MIN_AGE_HOURS + 2),
                    49000,
                ),
            )
            db.commit()

        cleanup_db = sana_app._connect_pg()
        retry_db = sana_app._connect_pg()
        try:
            with patch(
                "sana_billing.retrieve_checkout",
                return_value={
                    "id": session_id,
                    "status": "open",
                    "payment_status": "unpaid",
                },
            ), patch(
                "sana_billing._stripe_request",
                side_effect=TimeoutError("simulated Stripe expire timeout"),
            ) as stripe_request:
                cleanup_result = expire_stale_checkouts(
                    cleanup_db, company_ids=self.company_ids
                )

            retry_row = retry_db.execute(
                """SELECT status
                   FROM sana_company_subscriptions
                   WHERE stripe_checkout_session_id=?
                   FOR UPDATE NOWAIT""",
                (session_id,),
            ).fetchone()
            self.assertEqual("pending", retry_row["status"])
            retry_db.execute(
                """UPDATE sana_company_subscriptions
                   SET updated_at=now()
                   WHERE stripe_checkout_session_id=? AND status='pending'""",
                (session_id,),
            )
            retry_db.commit()
        finally:
            cleanup_db.close()
            retry_db.close()

        self.assertEqual(1, cleanup_result["scanned"])
        self.assertEqual(0, cleanup_result["expired"])
        self.assertEqual(1, cleanup_result["failed"])
        self.assertEqual(1, stripe_request.call_count)

    def test_stale_cleanup_race_with_paid_checkout_completion_keeps_active(self):
        from sana_billing import STALE_CHECKOUT_MIN_AGE_HOURS

        session_id = "cs_billtest_race_paid_" + secrets.token_hex(4)
        subscription_id = "SUB-BILL-RACE-PAID-" + secrets.token_hex(4).upper()
        now = datetime.utcnow()
        with sana_app.app.app_context():
            db = sana_app.get_db()
            db.execute(
                "DELETE FROM sana_billing_events WHERE company_id=?",
                (self.company_ids[0],),
            )
            db.execute(
                "DELETE FROM sana_company_subscriptions WHERE company_id=?",
                (self.company_ids[0],),
            )
            db.execute(
                """INSERT INTO sana_company_subscriptions
                   (subscription_id,company_id,status,stripe_checkout_session_id,
                    stripe_checkout_session_created_at,amount_minor)
                   VALUES (?,?,?,?,?,?)""",
                (
                    subscription_id,
                    self.company_ids[0],
                    "pending",
                    session_id,
                    now - timedelta(hours=STALE_CHECKOUT_MIN_AGE_HOURS + 2),
                    49000,
                ),
            )
            db.commit()

        cleanup_db = sana_app._connect_pg()
        completion_db = sana_app._connect_pg()
        stripe_recheck_started = Event()
        release_stripe_recheck = Event()

        def retrieve_paid_checkout(checkout_id):
            self.assertEqual(session_id, checkout_id)
            stripe_recheck_started.set()
            self.assertTrue(release_stripe_recheck.wait(timeout=15))
            return {
                "id": session_id,
                "status": "complete",
                "payment_status": "paid",
            }

        checkout = {
            "id": session_id,
            "payment_status": "paid",
            "customer": "cus_billtest_race_paid",
            "metadata": {"company_id": self.company_ids[0]},
            "subscription": {
                "id": "sub_billtest_race_paid",
                "current_period_end": 1_800_000_000,
            },
        }
        try:
            with patch(
                "sana_billing.retrieve_checkout",
                side_effect=retrieve_paid_checkout,
            ), patch("sana_billing._stripe_request") as stripe_request:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    cleanup_future = executor.submit(
                        expire_stale_checkouts,
                        cleanup_db,
                        company_ids=self.company_ids,
                    )
                    self.assertTrue(stripe_recheck_started.wait(timeout=15))
                    completion_future = executor.submit(
                        complete_checkout, completion_db, checkout
                    )
                    release_stripe_recheck.set()
                    cleanup_result = cleanup_future.result(timeout=15)
                    completion_result = completion_future.result(timeout=15)
        finally:
            release_stripe_recheck.set()
            cleanup_db.close()
            completion_db.close()

        with sana_app.app.app_context():
            row = sana_app.get_db().execute(
                """SELECT status,stripe_subscription_id
                   FROM sana_company_subscriptions WHERE company_id=?""",
                (self.company_ids[0],),
            ).fetchone()

        self.assertEqual(1, cleanup_result["scanned"])
        self.assertEqual(1, cleanup_result["already_completed"])
        self.assertEqual(0, cleanup_result["expired"])
        self.assertEqual("active", completion_result["status"])
        self.assertEqual("active", row["status"])
        self.assertEqual("sub_billtest_race_paid", row["stripe_subscription_id"])
        stripe_request.assert_not_called()

    def test_stale_cleanup_race_with_late_webhook_does_not_reactivate(self):
        from sana_billing import STALE_CHECKOUT_MIN_AGE_HOURS

        session_id = "cs_billtest_race_webhook_" + secrets.token_hex(4)
        subscription_id = "sub_billtest_race_webhook_" + secrets.token_hex(4)
        event_id = "evt_billtest_race_webhook_" + secrets.token_hex(4)
        now = datetime.utcnow()
        with sana_app.app.app_context():
            db = sana_app.get_db()
            db.execute(
                "DELETE FROM sana_billing_events WHERE company_id=?",
                (self.company_ids[0],),
            )
            db.execute(
                "DELETE FROM sana_company_subscriptions WHERE company_id=?",
                (self.company_ids[0],),
            )
            db.execute(
                """INSERT INTO sana_company_subscriptions
                   (subscription_id,company_id,status,stripe_checkout_session_id,
                    stripe_checkout_session_created_at,amount_minor)
                   VALUES (?,?,?,?,?,?)""",
                (
                    "SUB-BILL-RACE-WEBHOOK-" + secrets.token_hex(4).upper(),
                    self.company_ids[0],
                    "pending",
                    session_id,
                    now - timedelta(hours=STALE_CHECKOUT_MIN_AGE_HOURS + 2),
                    49000,
                ),
            )
            db.commit()

        cleanup_db = sana_app._connect_pg()
        webhook_db = sana_app._connect_pg()
        stripe_recheck_started = Event()
        release_stripe_recheck = Event()

        def retrieve_open_checkout(checkout_id):
            self.assertEqual(session_id, checkout_id)
            stripe_recheck_started.set()
            self.assertTrue(release_stripe_recheck.wait(timeout=15))
            return {
                "id": session_id,
                "status": "open",
                "payment_status": "unpaid",
            }

        late_webhook = {
            "id": event_id,
            "type": "customer.subscription.updated",
            "created": int(time.time()) + 60,
            "data": {"object": {
                "id": subscription_id,
                "customer": "cus_billtest_race_webhook",
                "status": "active",
                "metadata": {"company_id": self.company_ids[0]},
            }},
        }

        def apply_late_webhook():
            try:
                process_stripe_event(webhook_db, late_webhook)
            except ValueError as error:
                return str(error)
            return None

        try:
            with patch(
                "sana_billing.retrieve_checkout",
                side_effect=retrieve_open_checkout,
            ), patch(
                "sana_billing._stripe_request",
                return_value={
                    "id": session_id,
                    "status": "expired",
                    "payment_status": "unpaid",
                },
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    cleanup_future = executor.submit(
                        expire_stale_checkouts,
                        cleanup_db,
                        company_ids=self.company_ids,
                    )
                    self.assertTrue(stripe_recheck_started.wait(timeout=15))
                    webhook_future = executor.submit(apply_late_webhook)
                    release_stripe_recheck.set()
                    cleanup_result = cleanup_future.result(timeout=15)
                    webhook_error = webhook_future.result(timeout=15)
        finally:
            release_stripe_recheck.set()
            cleanup_db.close()
            webhook_db.close()

        with sana_app.app.app_context():
            db = sana_app.get_db()
            row = db.execute(
                "SELECT status FROM sana_company_subscriptions WHERE company_id=?",
                (self.company_ids[0],),
            ).fetchone()
            event_count = db.execute(
                "SELECT COUNT(*) FROM sana_billing_events WHERE event_id=?",
                (event_id,),
            ).fetchone()[0]

        self.assertEqual(1, cleanup_result["expired"])
        self.assertEqual("STRIPE_SUBSCRIPTION_NOT_FOUND", webhook_error)
        self.assertEqual("canceled", row["status"])
        self.assertEqual(0, event_count)

    def test_stale_checkout_cleanup_is_admin_only_and_redacts_session_ids(self):
        session_id = "cs_billtest_admin_cleanup_" + secrets.token_hex(4)
        admin = self.client_for(admin=True)
        denied = self.client_for(0).post(
            "/api/admin/billing/checkout-sessions/cleanup",
            json={"reason": "اختبار"},
        )
        self.assertEqual(403, denied.status_code)
        with patch("sana_billing.expire_stale_checkouts", return_value={
            "scanned": 1, "expired": 1, "already_completed": 0,
            "already_expired": 0, "failed": 0,
        }):
            response = admin.post(
                "/api/admin/billing/checkout-sessions/cleanup",
                json={"reason": "تنظيف جلسات قديمة"},
            )
        self.assertEqual(200, response.status_code)
        self.assertNotIn(session_id, response.get_data(as_text=True))
        with sana_app.app.app_context():
            audit = sana_app.get_db().execute(
                """SELECT metadata_json FROM admin_audit_log
                   WHERE actor_account_id=? AND action=?
                   ORDER BY created_at DESC LIMIT 1""",
                (self.admin_id, "billing_checkout_cleanup"),
            ).fetchone()
        self.assertIsNotNone(audit)
        self.assertNotIn(session_id, audit["metadata_json"])

    def _stripe_webhook(self, event):
        raw = json.dumps(event, separators=(",", ":")).encode("utf-8")
        timestamp = int(time.time())
        secret = "whsec_billing_test"
        signature = hmac.new(
            secret.encode("utf-8"),
            f"{timestamp}.".encode("utf-8") + raw,
            hashlib.sha256,
        ).hexdigest()
        with (
            patch("sana_billing._connector_settings", return_value={
                "webhook_secret": secret,
            }),
            patch.object(sana_app, "RESEND_API_KEY", ""),
        ):
            return sana_app.app.test_client().post(
                "/api/billing/stripe-webhook",
                data=raw,
                headers={
                    "Content-Type": "application/json",
                    "Stripe-Signature": f"t={timestamp},v1={signature}",
                },
            )

    def test_stripe_lifecycle_events_are_idempotent_and_tenant_bound(self):
        subscription_id = "sub_billtest_" + secrets.token_hex(4)
        customer_id = "cus_billtest_" + secrets.token_hex(4)
        with sana_app.app.app_context():
            db = sana_app.get_db()
            marks = ",".join("?" for _ in self.company_ids)
            db.execute(
                f"DELETE FROM sana_billing_events WHERE company_id IN ({marks})",
                self.company_ids,
            )
            db.execute(
                f"DELETE FROM sana_company_subscriptions WHERE company_id IN ({marks})",
                self.company_ids,
            )
            db.execute(
                """INSERT INTO sana_company_subscriptions
                   (subscription_id,company_id,status,stripe_customer_id,
                    stripe_subscription_id,amount_minor,current_period_end)
                   VALUES (?,?,?,?,?,?,now() + INTERVAL '10 day')""",
                (
                    "SUB-BILL-EVENT-" + secrets.token_hex(4).upper(),
                    self.company_ids[0], "active", customer_id,
                    subscription_id, 49000,
                ),
            )
            db.commit()

        failed = {
            "id": "evt_bill_payment_failed_" + secrets.token_hex(4),
            "type": "invoice.payment_failed",
            "created": int(time.time()) + 10,
            "data": {"object": {
                "id": "in_billtest_" + secrets.token_hex(4),
                "customer": customer_id,
                "subscription": subscription_id,
                "period_end": int(time.time()) + 86400 * 20,
            }},
        }
        self.assertEqual(200, self._stripe_webhook(failed).status_code)
        self.assertEqual(200, self._stripe_webhook(failed).status_code)

        with sana_app.app.app_context():
            db = sana_app.get_db()
            subscription = db.execute(
                "SELECT * FROM sana_company_subscriptions WHERE company_id=?",
                (self.company_ids[0],),
            ).fetchone()
            self.assertEqual("past_due", subscription["status"])
            self.assertEqual(
                1,
                db.execute(
                    "SELECT COUNT(*) FROM sana_billing_events WHERE event_id=?",
                    (failed["id"],),
                ).fetchone()[0],
            )
            self.assertEqual(
                2,
                db.execute(
                    """SELECT COUNT(*) FROM admin_notification_outbox
                       WHERE company_id=? AND notification_type=?""",
                    (self.company_ids[0], "billing_subscription_past_due"),
                ).fetchone()[0],
            )
            notifications = db.execute(
                """SELECT recipient_email,status,payload_json
                   FROM admin_notification_outbox
                   WHERE company_id=? AND notification_type=?
                   ORDER BY recipient_email""",
                (self.company_ids[0], "billing_subscription_past_due"),
            ).fetchall()
            owner_emails = {
                row["email"]
                for row in db.execute(
                    """SELECT email FROM user_accounts
                       WHERE account_id IN (?,?)""",
                    (self.account_ids[0], self.additional_owner_id),
                ).fetchall()
            }
            self.assertEqual(
                owner_emails,
                {notification["recipient_email"] for notification in notifications},
            )
            for notification in notifications:
                self.assertEqual("queued", notification["status"])
                self.assertIn(
                    "billing_action=update_payment",
                    notification["payload_json"] or "",
                )
            self.assertEqual(
                "Registered",
                db.execute(
                    "SELECT lifecycle_status FROM companies WHERE company_id=?",
                    (self.company_ids[1],),
                ).fetchone()["lifecycle_status"],
            )

        paid = {
            "id": "evt_bill_invoice_paid_" + secrets.token_hex(4),
            "type": "invoice.paid",
            "created": int(time.time()) + 20,
            "data": {"object": {
                "id": "in_billtest_paid_" + secrets.token_hex(4),
                "customer": customer_id,
                "subscription": subscription_id,
                "lines": {"data": [{
                    "period": {"end": int(time.time()) + 86400 * 45},
                }]},
            }},
        }
        self.assertEqual(200, self._stripe_webhook(paid).status_code)
        with sana_app.app.app_context():
            self.assertEqual(
                "active",
                sana_app.get_db().execute(
                    "SELECT status FROM sana_company_subscriptions WHERE company_id=?",
                    (self.company_ids[0],),
                ).fetchone()["status"],
            )

        updated = {
            "id": "evt_bill_subscription_updated_" + secrets.token_hex(4),
            "type": "customer.subscription.updated",
            "created": int(time.time()) + 30,
            "data": {"object": {
                "id": subscription_id,
                "customer": customer_id,
                "status": "past_due",
                "current_period_end": int(time.time()) + 86400 * 50,
            }},
        }
        self.assertEqual(200, self._stripe_webhook(updated).status_code)

        deleted = {
            "id": "evt_bill_subscription_deleted_" + secrets.token_hex(4),
            "type": "customer.subscription.deleted",
            "created": int(time.time()) + 40,
            "data": {"object": {
                "id": subscription_id,
                "customer": customer_id,
                "status": "canceled",
            }},
        }
        self.assertEqual(200, self._stripe_webhook(deleted).status_code)
        self.assertEqual(200, self._stripe_webhook(deleted).status_code)
        with sana_app.app.app_context():
            db = sana_app.get_db()
            self.assertEqual(
                "canceled",
                db.execute(
                    "SELECT status FROM sana_company_subscriptions WHERE company_id=?",
                    (self.company_ids[0],),
                ).fetchone()["status"],
            )
            canceled_notifications = db.execute(
                """SELECT status,payload_json
                   FROM admin_notification_outbox
                   WHERE company_id=? AND notification_type=?""",
                (self.company_ids[0], "billing_subscription_canceled"),
            ).fetchall()
            self.assertEqual(2, len(canceled_notifications))
            for canceled_notification in canceled_notifications:
                self.assertEqual("queued", canceled_notification["status"])
                canceled_payload = json.loads(canceled_notification["payload_json"])
                self.assertEqual(
                    "restart_subscription",
                    canceled_payload["recovery_action"],
                )
                self.assertIn(
                    "billing_action=restart_subscription",
                    canceled_payload["recovery_url"],
                )
                self.assertNotIn("stripe_customer_id", canceled_payload)
                self.assertNotIn("stripe_subscription_id", canceled_payload)
            self.assertEqual(
                2,
                db.execute(
                    """SELECT COUNT(*) FROM admin_notification_outbox
                       WHERE company_id=? AND notification_type=?""",
                    (self.company_ids[0], "billing_subscription_canceled"),
                ).fetchone()[0],
            )

        delayed_paid = {
            "id": "evt_bill_delayed_paid_" + secrets.token_hex(4),
            "type": "invoice.paid",
            "created": int(time.time()) + 35,
            "data": {"object": {
                "customer": customer_id,
                "subscription": subscription_id,
                "period_end": int(time.time()) + 86400 * 60,
            }},
        }
        self.assertEqual(200, self._stripe_webhook(delayed_paid).status_code)
        with sana_app.app.app_context():
            self.assertEqual(
                "canceled",
                sana_app.get_db().execute(
                    "SELECT status FROM sana_company_subscriptions WHERE company_id=?",
                    (self.company_ids[0],),
                ).fetchone()["status"],
            )

    def test_stripe_event_rejects_conflicting_subscription_for_customer(self):
        subscription_id = "sub_billtest_original_" + secrets.token_hex(4)
        customer_id = "cus_billtest_conflict_" + secrets.token_hex(4)
        event_id = "evt_bill_conflict_" + secrets.token_hex(4)
        with sana_app.app.app_context():
            db = sana_app.get_db()
            db.execute(
                "DELETE FROM sana_billing_events WHERE company_id=?",
                (self.company_ids[0],),
            )
            db.execute(
                "DELETE FROM sana_company_subscriptions WHERE company_id=?",
                (self.company_ids[0],),
            )
            db.execute(
                """INSERT INTO sana_company_subscriptions
                   (subscription_id,company_id,status,stripe_customer_id,
                    stripe_subscription_id,amount_minor)
                   VALUES (?,?,?,?,?,?)""",
                (
                    "SUB-BILL-CONFLICT-" + secrets.token_hex(4).upper(),
                    self.company_ids[0], "active", customer_id,
                    subscription_id, 49000,
                ),
            )
            db.commit()

        conflict = {
            "id": event_id,
            "type": "customer.subscription.deleted",
            "created": int(time.time()) + 50,
            "data": {"object": {
                "id": "sub_billtest_other_" + secrets.token_hex(4),
                "customer": customer_id,
                "status": "canceled",
            }},
        }
        self.assertEqual(400, self._stripe_webhook(conflict).status_code)
        with sana_app.app.app_context():
            db = sana_app.get_db()
            row = db.execute(
                """SELECT status,stripe_subscription_id
                   FROM sana_company_subscriptions WHERE company_id=?""",
                (self.company_ids[0],),
            ).fetchone()
            self.assertEqual("active", row["status"])
            self.assertEqual(subscription_id, row["stripe_subscription_id"])
            self.assertFalse(
                db.execute(
                    "SELECT 1 FROM sana_billing_events WHERE event_id=?",
                    (event_id,),
                ).fetchone()
            )

    def test_subscription_metadata_correlates_event_before_checkout_completion(self):
        subscription_id = "sub_billtest_early_" + secrets.token_hex(4)
        customer_id = "cus_billtest_early_" + secrets.token_hex(4)
        checkout_id = "cs_billtest_early_" + secrets.token_hex(4)
        with sana_app.app.app_context():
            db = sana_app.get_db()
            db.execute(
                "DELETE FROM sana_company_subscriptions WHERE company_id=?",
                (self.company_ids[0],),
            )
            db.execute(
                """INSERT INTO sana_company_subscriptions
                   (subscription_id,company_id,status,stripe_checkout_session_id,
                    amount_minor)
                   VALUES (?,?,?,?,?)""",
                (
                    "SUB-BILL-EARLY-" + secrets.token_hex(4).upper(),
                    self.company_ids[0], "pending", checkout_id, 49000,
                ),
            )
            db.commit()

        early = {
            "id": "evt_bill_early_" + secrets.token_hex(4),
            "type": "customer.subscription.updated",
            "created": int(time.time()) + 60,
            "data": {"object": {
                "id": subscription_id,
                "customer": customer_id,
                "status": "active",
                "current_period_end": int(time.time()) + 86400 * 30,
                "metadata": {"company_id": self.company_ids[0]},
            }},
        }
        self.assertEqual(200, self._stripe_webhook(early).status_code)
        with sana_app.app.app_context():
            row = sana_app.get_db().execute(
                """SELECT status,stripe_customer_id,stripe_subscription_id
                   FROM sana_company_subscriptions WHERE company_id=?""",
                (self.company_ids[0],),
            ).fetchone()
            self.assertEqual("active", row["status"])
            self.assertEqual(customer_id, row["stripe_customer_id"])
            self.assertEqual(subscription_id, row["stripe_subscription_id"])

    @patch.dict(
        os.environ,
        {
            "REPLIT_CONNECTORS_HOSTNAME": "connectors.example.test",
            "REPL_IDENTITY": "test-runtime-identity",
        },
        clear=False,
    )
    @patch("sana_billing.requests.get")
    def test_connector_accepts_current_stripe_secret_field(self, get):
        from sana_billing import _connector_settings

        get.return_value.raise_for_status.return_value = None
        get.return_value.json.return_value = {
            "items": [{"settings": {"secret": "sk_test_placeholder"}}]
        }
        stripe_settings = _connector_settings()
        self.assertEqual("sk_test_placeholder", stripe_settings["secret_key"])
        get.assert_called_once()

    def test_checkout_completion_is_idempotent_for_return_and_webhook(self):
        company_id = self.company_ids[1]
        coupon_code = self.coupon_codes[2]
        session_id = f"cs_billtest_idempotent_{self.run_token}"
        with sana_app.app.app_context():
            db = sana_app.get_db()
            db.execute(
                "DELETE FROM sana_billing_events WHERE event_id=?",
                (f"checkout:{session_id}",),
            )
            db.execute(
                "DELETE FROM sana_company_subscriptions WHERE company_id=?",
                (company_id,),
            )
            db.execute(
                "DELETE FROM sana_billing_coupons WHERE code=?",
                (coupon_code,),
            )
            db.execute(
                """INSERT INTO sana_billing_coupons
                   (coupon_id,code,discount_type,discount_value,max_redemptions,
                    used_count,is_active,created_by)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    "COUP-" + secrets.token_hex(6).upper(),
                    coupon_code,
                    "percent",
                    10,
                    1,
                    0,
                    True,
                    self.admin_id,
                ),
            )
            db.execute(
                """INSERT INTO sana_company_subscriptions
                   (subscription_id,company_id,status,stripe_checkout_session_id,
                    coupon_code,amount_minor)
                   VALUES (?,?,?,?,?,?)""",
                (
                    "SUB-" + secrets.token_hex(6).upper(),
                    company_id,
                    "pending",
                    session_id,
                    coupon_code,
                    44100,
                ),
            )
            db.commit()
            checkout = {
                "id": session_id,
                "payment_status": "paid",
                "customer": "cus_test_idempotent",
                "metadata": {
                    "company_id": company_id,
                    "coupon_code": coupon_code,
                },
                "subscription": {
                    "id": "sub_test_idempotent",
                    "current_period_end": 1_800_000_000,
                },
            }
            first = complete_checkout(db, checkout)
            second = complete_checkout(db, checkout)
            subscription = sana_app.get_db().execute(
                """SELECT status,stripe_subscription_id
                   FROM sana_company_subscriptions WHERE company_id=?""",
                (company_id,),
            ).fetchone()
            event_count = sana_app.get_db().execute(
                "SELECT COUNT(*) AS count FROM sana_billing_events WHERE event_id=?",
                (f"checkout:{session_id}",),
            ).fetchone()["count"]
            coupon = sana_app.get_db().execute(
                "SELECT used_count FROM sana_billing_coupons WHERE code=?",
                (coupon_code,),
            ).fetchone()

        self.assertEqual("active", first["status"])
        self.assertEqual("active", second["status"])
        self.assertEqual("active", subscription["status"])
        self.assertEqual("sub_test_idempotent", subscription["stripe_subscription_id"])
        self.assertEqual(1, event_count)
        self.assertEqual(1, coupon["used_count"])

@unittest.skipUnless(
    os.environ.get("RUN_SANA_STRIPE_BROWSER_TESTS") == "1"
    and (os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_PASSWORD")),
    "يتطلب RUN_SANA_STRIPE_BROWSER_TESTS=1 واتصال قاعدة البيانات",
)
class StripeCheckoutBrowserTest(unittest.TestCase):
    """قبول متصفح اختياري: Checkout حقيقي في Stripe test mode."""

    @classmethod
    def setUpClass(cls):
        cls.db = None
        cls.schema = None
        cls.schema = sana_app.create_billing_test_schema()
        cls.addClassCleanup(cls._cleanup_test_schema)
        sana_app.init_billing_test_db()
        cls.db = sana_app._connect_pg()
        cls.company_id = "STRIPE-BROWSER-" + uuid.uuid4().hex[:10].upper()
        cls.account_id = "ACC-STRIPE-BROWSER-" + uuid.uuid4().hex[:10].upper()
        cls.email = f"stripe-browser-{uuid.uuid4().hex[:10]}@example.test"
        cls.password = "Stripe-browser-test-2026!"
        cls.server = None
        cls.browser = None
        cls.playwright = None
        cls.port = None
        cls.base_url = None
        cls.stripe_subscription_id = None

        cls.db.execute(
            """INSERT INTO companies
               (company_id,name,sector,sds_done,main_goal,lifecycle_status)
               VALUES (?,?,?,?,?,?)""",
            (
                cls.company_id,
                "شركة اختبار دفع Stripe",
                "tech",
                1,
                "اختبار العودة من الدفع",
                "Active",
            ),
        )
        cls.db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id,is_admin,admin_role,
                admin_permissions,account_status)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                cls.account_id,
                cls.email,
                generate_password_hash(cls.password),
                cls.company_id,
                0,
                "COMPANY_OWNER",
                "[]",
                "active",
            ),
        )
        cls.db.commit()

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.port = sock.getsockname()[1]
        sock.close()
        server_env = os.environ.copy()
        server_env.update(
            {
                "PORT": str(cls.port),
                "SANA_ENV": "production",
                "REPLIT_DEPLOYMENT": "1",
                "SESSION_SECRET": "stripe-browser-test-session-secret-2026",
                "SANA_DATABASE_SCHEMA": cls.schema,
            }
        )
        cls.server = subprocess.Popen(
            [sys.executable, "app.py"],
            cwd=BASE_DIR,
            env=server_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        try:
            for _ in range(60):
                if cls.server.poll() is not None:
                    raise RuntimeError("توقف خادم اختبار Stripe قبل الجاهزية")
                try:
                    with urlopen(f"{cls.base_url}/login", timeout=1) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(0.25)
            else:
                raise RuntimeError("انتهت مهلة تشغيل خادم اختبار Stripe")
        except Exception:
            cls._stop_server()
            if cls.db and getattr(cls, "company_id", None):
                cls._cleanup_fixture()
            cls._cleanup_test_schema()
            raise

    @classmethod
    def tearDownClass(cls):
        if cls.stripe_subscription_id:
            try:
                from sana_billing import _stripe_request
                _stripe_request(
                    "DELETE",
                    f"/v1/subscriptions/{cls.stripe_subscription_id}",
                )
            except Exception:
                # The local fixture is still removed; no provider response is
                # printed because it may contain customer-identifying fields.
                pass
        if cls.browser:
            cls.browser.close()
        if cls.playwright:
            cls.playwright.stop()
        cls._stop_server()
        cls._cleanup_test_schema()

    @classmethod
    def _cleanup_test_schema(cls):
        if cls.db:
            cls.db.close()
            cls.db = None
        if cls.schema:
            sana_app.drop_billing_test_schema(cls.schema)
            cls.schema = None
        sana_app.reset_database_schema()

    @classmethod
    def _stop_server(cls):
        if cls.server and cls.server.poll() is None:
            cls.server.terminate()
            try:
                cls.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.server.kill()
                cls.server.wait(timeout=5)

    @classmethod
    def _cleanup_fixture(cls):
        if not getattr(cls, "db", None):
            return
        try:
            cls.db.execute(
                "DELETE FROM sana_billing_events WHERE company_id=?",
                (cls.company_id,),
            )
            cls.db.execute(
                "DELETE FROM sana_company_subscriptions WHERE company_id=?",
                (cls.company_id,),
            )
            cls.db.execute(
                "DELETE FROM user_accounts WHERE account_id=?",
                (cls.account_id,),
            )
            cls.db.execute(
                "DELETE FROM companies WHERE company_id=?",
                (cls.company_id,),
            )
            cls.db.commit()
        except Exception:
            cls.db.rollback()
            raise

    def setUp(self):
        self.page = None
        self._stage = "setup"
        self._failure_evidence_saved = False
        self._diagnostics_run_id = uuid.uuid4().hex
        self._diagnostics_dir = (
            BASE_DIR / ".browser-diagnostics" / f"stripe-checkout-{self._diagnostics_run_id}"
        )

    def tearDown(self):
        if not self._failure_evidence_saved:
            shutil.rmtree(self._diagnostics_dir, ignore_errors=True)
        if self.page:
            try:
                self.page.close()
            except Exception:
                pass

    def _save_safe_failure_evidence(self):
        self._diagnostics_dir.mkdir(parents=True, exist_ok=True)
        evidence = {
            "flow": "stripe_checkout_test_mode",
            "stage": self._stage,
            "page_path": None,
            "card_data_captured": False,
            "session_data_captured": False,
        }
        if self.page:
            try:
                parsed = urlparse(self.page.url)
                if parsed.netloc == urlparse(self.base_url).netloc:
                    evidence["page_path"] = parsed.path
                    self.page.screenshot(
                        path=str(self._diagnostics_dir / f"failure-{self._diagnostics_run_id}.png"),
                        full_page=True,
                    )
            except Exception:
                pass
        (self._diagnostics_dir / f"failure-{self._diagnostics_run_id}.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            "Stripe browser failure evidence saved safely "
            f"(run {self._diagnostics_run_id}): {self._diagnostics_dir}",
            file=sys.stderr,
            flush=True,
        )
        self._failure_evidence_saved = True

    def test_real_test_mode_checkout_returns_home_and_is_idempotent(self):
        try:
            self._exercise_real_test_mode_checkout()
        except Exception:
            self._save_safe_failure_evidence()
            raise

    def _exercise_real_test_mode_checkout(self):
        from playwright.sync_api import sync_playwright
        from sana_billing import retrieve_checkout

        type(self).playwright = sync_playwright().start()
        type(self).browser = self.playwright.chromium.launch(headless=True)
        page = self.browser.new_page(viewport={"width": 1440, "height": 1100})
        self.page = page

        page.goto(f"{self.base_url}/login", wait_until="networkidle")
        page.locator("#email").fill(self.email)
        page.locator("#password").fill(self.password)
        page.locator("#submitBtn").click()
        page.wait_for_url("**/home", timeout=10_000)

        self._stage = "pricing"
        page.goto(f"{self.base_url}/pricing", wait_until="networkidle")
        self.assertIn("٤٩٠", page.locator("#salePrice").inner_text())
        self.assertIn("٤٩٠", page.locator("#summaryNow").inner_text())

        self._stage = "checkout_creation"
        self.assertTrue(page.locator("#checkout").is_enabled())
        checkout_payload = page.evaluate(
            """async () => {
                const csrf = document.querySelector('meta[name="csrf-token"]').content;
                const response = await fetch('/api/billing/checkout', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrf,
                    },
                    body: JSON.stringify({code: ''}),
                });
                return await response.json();
            }"""
        )
        self.assertTrue(checkout_payload["success"])
        session_id = checkout_payload["data"]["session_id"]
        self.assertTrue(session_id.startswith("cs_"))
        page.goto(checkout_payload["data"]["url"], wait_until="domcontentloaded")
        page.wait_for_url("https://checkout.stripe.com/**", timeout=30_000)
        stripe_checkout = retrieve_checkout(session_id)
        self.assertFalse(
            stripe_checkout.get("livemode"),
            "رفض إدخال بطاقة الاختبار لأن جلسة Stripe ليست في test mode",
        )
        self.assertEqual(49000, stripe_checkout.get("amount_total"))
        self.assertEqual("sar", stripe_checkout.get("currency"))

        self._stage = "stripe_payment"
        page.locator('input[name="cardNumber"], input[autocomplete="cc-number"]').first.fill(
            "4242424242424242"
        )
        page.locator('input[name="cardExpiry"], input[autocomplete="cc-exp"]').first.fill(
            "1234"
        )
        page.locator('input[name="cardCvc"], input[autocomplete="cc-csc"]').first.fill("123")
        billing_name = page.locator(
            'input[name="billingName"], input[autocomplete="name"]'
        ).first
        if billing_name.is_visible():
            billing_name.fill("Sana Stripe Test")
        pay_button = page.get_by_role(
            "button",
            name=re.compile(r"Pay|Subscribe|دفع|اشتراك", re.IGNORECASE),
        ).last
        self.assertTrue(pay_button.is_visible())
        pay_button.click()

        self._stage = "billing_success_redirect"
        page.wait_for_url("**/home?payment=success", timeout=30_000)
        self.assertIn("home", urlparse(page.url).path)

        with sana_app.app.app_context():
            db = sana_app.get_db()
            subscription = db.execute(
                """SELECT status,stripe_subscription_id
                   FROM sana_company_subscriptions WHERE company_id=?""",
                (self.company_id,),
            ).fetchone()
            event_count = db.execute(
                """SELECT COUNT(*) AS count FROM sana_billing_events
                   WHERE company_id=? AND event_type='checkout.session.completed'""",
                (self.company_id,),
            ).fetchone()["count"]
        self.assertEqual("active", subscription["status"])
        self.assertTrue(subscription["stripe_subscription_id"].startswith("sub_"))
        self.assertEqual(1, event_count)
        type(self).stripe_subscription_id = subscription["stripe_subscription_id"]

        self._stage = "duplicate_return"
        page.goto(
            f"{self.base_url}/billing/success?session_id={session_id}",
            wait_until="networkidle",
        )
        page.wait_for_url("**/home?payment=success", timeout=10_000)
        with sana_app.app.app_context():
            db = sana_app.get_db()
            event_count = db.execute(
                """SELECT COUNT(*) AS count FROM sana_billing_events
                   WHERE company_id=? AND event_type='checkout.session.completed'""",
                (self.company_id,),
            ).fetchone()["count"]
        self.assertEqual(1, event_count)


if __name__ == "__main__":
    unittest.main()
