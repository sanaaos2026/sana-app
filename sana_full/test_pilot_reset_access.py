import unittest
import uuid
from unittest.mock import patch

import app as sana_app


class PilotResetAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sana_app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        sana_app.init_db()

    def setUp(self):
        self.ctx = sana_app.app.app_context()
        self.ctx.push()
        self.db = sana_app.get_db()
        suffix = uuid.uuid4().hex[:8].upper()
        used_slots = {
            int(row["pilot_cohort_number"])
            for row in self.db.execute(
                """SELECT pilot_cohort_number FROM user_accounts
                   WHERE pilot_cohort_number IS NOT NULL"""
            ).fetchall()
        }
        available_slots = [
            number for number in range(1, sana_app.PILOT_COHORT_LIMIT + 1)
            if number not in used_slots
        ]
        self.assertTrue(available_slots, "Pilot fixture requires one free slot")
        self.pilot = self._create_fixture(
            "PIL" + suffix, f"pilot{suffix.lower()}@example.org",
            available_slots[0],
        )
        self.other = self._create_fixture(
            "OUT" + suffix, f"outside{suffix.lower()}@example.org", None,
        )
        self.db.execute(
            "INSERT INTO tasks (task_id,company_id,title) VALUES (?,?,?)",
            ("TSK" + suffix, self.other["company_id"], "مهمة الشركة الأخرى"),
        )
        self.db.commit()
        self.client = sana_app.app.test_client()
        self._login(self.pilot)

    def tearDown(self):
        self.db.rollback()
        for fixture in (self.pilot, self.other):
            sana_app._reset_company_experience(self.db, fixture["company_id"])
        self.db.execute(
            "DELETE FROM admin_audit_log WHERE actor_account_id IN (?,?)",
            (self.pilot["account_id"], self.other["account_id"]),
        )
        self.db.execute(
            "DELETE FROM user_accounts WHERE company_id IN (?,?)",
            (self.pilot["company_id"], self.other["company_id"]),
        )
        self.db.execute(
            "DELETE FROM companies WHERE company_id IN (?,?)",
            (self.pilot["company_id"], self.other["company_id"]),
        )
        self.db.commit()
        self.ctx.pop()

    def _create_fixture(self, company_id, email, pilot_number):
        account_id = "ACC" + company_id
        self.db.execute(
            """INSERT INTO companies
               (company_id,name,sector,sds_done,main_goal)
               VALUES (?,?,?,?,?)""",
            (company_id, "شركة مهيأة", "consulting", 1, "هدف محفوظ"),
        )
        self.db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id,admin_role,
                account_status,pilot_cohort_number)
               VALUES (?,?,?,?,?,?,?)""",
            (account_id, email, "same-password-hash", company_id, "USER", "active",
             pilot_number),
        )
        return {
            "company_id": company_id,
            "account_id": account_id,
            "email": email,
        }

    def _login(self, fixture):
        with self.client.session_transaction() as login:
            login.clear()
            login.update(
                account_id=fixture["account_id"],
                company_id=fixture["company_id"],
                email=fixture["email"],
            )

    def test_1_pilot_account_sees_reset_button_in_production(self):
        with patch.object(sana_app, "IS_PRODUCTION", True):
            page = self.client.get("/home")
        self.assertEqual(200, page.status_code)
        self.assertIn("↺ إعادة ضبط التجربة", page.get_data(as_text=True))

    def test_2_account_outside_pilot_does_not_see_or_use_reset(self):
        self._login(self.other)
        with patch.object(sana_app, "IS_PRODUCTION", True):
            page = self.client.get("/home")
            reset = self.client.post("/api/testing/reset-experience")
        self.assertEqual(200, page.status_code)
        self.assertNotIn("↺ إعادة ضبط التجربة", page.get_data(as_text=True))
        self.assertEqual(403, reset.status_code)

    def test_3_account_number_21_does_not_get_a_slot_automatically(self):
        suffix = self.pilot["company_id"]
        used_slots = {
            int(row["pilot_cohort_number"])
            for row in self.db.execute(
                """SELECT pilot_cohort_number FROM user_accounts
                   WHERE pilot_cohort_number IS NOT NULL"""
            ).fetchall()
        }
        for number in range(1, sana_app.PILOT_COHORT_LIMIT + 1):
            if number in used_slots:
                continue
            account_id = f"FILL{number}{suffix}"
            self.db.execute(
                """INSERT INTO user_accounts
                   (account_id,email,password_hash,company_id,admin_role,
                    account_status,pilot_cohort_number)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    account_id, f"fill{number}{suffix.lower()}@example.org",
                    "hash", self.other["company_id"], "USER", "active", number,
                ),
            )
        incoming_id = "ACC21" + suffix
        self.db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id,admin_role,account_status)
               VALUES (?,?,?,?,?,?)""",
            (
                incoming_id, f"account21{suffix.lower()}@example.org",
                "hash", self.other["company_id"], "USER", "active",
            ),
        )
        self.assertIsNone(sana_app._assign_pilot_slot(self.db, incoming_id))
        self.db.commit()
        self._login({
            "account_id": incoming_id,
            "company_id": self.other["company_id"],
            "email": f"account21{suffix.lower()}@example.org",
        })
        with patch.object(sana_app, "IS_PRODUCTION", True):
            page = self.client.get("/home")
        self.assertNotIn("↺ إعادة ضبط التجربة", page.get_data(as_text=True))

    def test_4_reset_keeps_another_account_and_company_unchanged(self):
        with patch.object(sana_app, "IS_PRODUCTION", True):
            reset = self.client.post("/api/testing/reset-experience")
        self.assertEqual(200, reset.status_code)
        other_company = self.db.execute(
            "SELECT name,sds_done,main_goal FROM companies WHERE company_id=?",
            (self.other["company_id"],),
        ).fetchone()
        self.assertEqual("شركة مهيأة", other_company["name"])
        self.assertEqual(1, other_company["sds_done"])
        self.assertEqual("هدف محفوظ", other_company["main_goal"])
        self.assertEqual(
            1,
            self.db.execute(
                "SELECT COUNT(*) FROM tasks WHERE company_id=?",
                (self.other["company_id"],),
            ).fetchone()[0],
        )
        self.assertIsNotNone(self.db.execute(
            "SELECT 1 FROM user_accounts WHERE account_id=?",
            (self.other["account_id"],),
        ).fetchone())
if __name__ == "__main__":
    unittest.main()