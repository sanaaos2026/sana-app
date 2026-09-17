import unittest
import uuid

import app as sana_app


class ExperienceResetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sana_app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        sana_app.init_db()

    def setUp(self):
        self.ctx = sana_app.app.app_context()
        self.ctx.push()
        self.db = sana_app.get_db()
        suffix = uuid.uuid4().hex[:10].upper()
        self.current = self._create_company("RST" + suffix, "الحالية")
        self.other = self._create_company("OTH" + suffix, "الأخرى")
        self.db.commit()

        self.client = sana_app.app.test_client()
        with self.client.session_transaction() as login:
            login.update(
                account_id=self.current["account_id"],
                company_id=self.current["company_id"],
                email=self.current["email"],
            )

    def tearDown(self):
        self.db.rollback()
        for fixture in (self.current, self.other):
            sana_app._reset_company_experience(
                self.db, fixture["company_id"],
            )
        account_ids = (self.current["account_id"], self.other["account_id"])
        self.db.execute(
            "DELETE FROM admin_audit_log WHERE actor_account_id IN (?,?)",
            account_ids,
        )
        self.db.execute(
            "DELETE FROM user_accounts WHERE account_id IN (?,?)",
            account_ids,
        )
        self.db.execute(
            "DELETE FROM companies WHERE company_id IN (?,?)",
            (self.current["company_id"], self.other["company_id"]),
        )
        self.db.commit()
        self.ctx.pop()

    def _create_company(self, company_id, label):
        account_id = "ACC" + company_id
        email = f"{account_id.lower()}@test.local"
        asset_id = "AST" + company_id
        case_id = "CAS" + company_id
        evidence_id = "EVD" + company_id
        scan_id = "SCN" + company_id
        decision_id = "DEC" + company_id
        task_id = "TSK" + company_id
        memory_id = "MEM" + company_id
        legacy_memory_id = "LGM" + company_id

        self.db.execute(
            """INSERT INTO companies
               (company_id,name,sector,employee_count,business_description,
                goal_90_days,primary_challenge,sds_done,main_goal)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                company_id, f"شركة {label}", "consulting", 12,
                "خدمات اختبار", "زيادة الإيراد", "ضعف التسليم", 1,
                "نمو منضبط",
            ),
        )
        self.db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id,admin_role,account_status)
               VALUES (?,?,?,?,?,?)""",
            (account_id, email, "unchanged-password-hash", company_id, "USER", "active"),
        )
        self.db.execute(
            """INSERT INTO assets
               (asset_id,company_id,asset_type,asset_name,current_score,status)
               VALUES (?,?,?,?,?,?)""",
            (asset_id, company_id, "Operations", "التشغيل", 45, "تحت المراجعة"),
        )
        self.db.execute(
            """INSERT INTO cases
               (case_id,company_id,case_title,case_status,related_asset_id)
               VALUES (?,?,?,?,?)""",
            (case_id, company_id, "حالة اختبار", "Open", asset_id),
        )
        self.db.execute(
            """INSERT INTO evidence
               (evidence_id,company_id,case_id,asset_id,title)
               VALUES (?,?,?,?,?)""",
            (evidence_id, company_id, case_id, asset_id, "دليل اختبار"),
        )
        self.db.execute(
            """INSERT INTO scan_runs
               (scan_id,case_id,company_id,status,result,methodology_version)
               VALUES (?,?,?,?,?,?)""",
            (scan_id, case_id, company_id, "READY", "{}", "test"),
        )
        self.db.execute(
            """INSERT INTO decisions
               (decision_id,company_id,case_id,asset_id,title,scan_id)
               VALUES (?,?,?,?,?,?)""",
            (decision_id, company_id, case_id, asset_id, "قرار اختبار", scan_id),
        )
        self.db.execute(
            """INSERT INTO tasks
               (task_id,company_id,decision_id,title,status)
               VALUES (?,?,?,?,?)""",
            (task_id, company_id, decision_id, "مهمة اختبار", "لم تبدأ"),
        )
        self.db.execute(
            """INSERT INTO company_memory_items
               (memory_id,company_id,memory_key,memory_type,current_status)
               VALUES (?,?,?,?,?)""",
            (memory_id, company_id, "test:memory", "fact", "CURRENT"),
        )
        self.db.execute(
            """INSERT INTO sana_memory_entries
               (memory_id,memory_type,statement,company_id,case_id)
               VALUES (?,?,?,?,?)""",
            (legacy_memory_id, "fact", "ذاكرة اختبار", company_id, case_id),
        )
        return {
            "company_id": company_id,
            "account_id": account_id,
            "email": email,
        }

    def _reset(self):
        response = self.client.post("/api/testing/reset-experience")
        self.assertEqual(200, response.status_code, response.get_data(as_text=True))
        return response

    def test_1_reset_returns_one_test_company_to_the_first_step(self):
        response = self._reset()
        self.assertEqual("/onboarding", response.get_json()["data"]["redirect"])
        company = self.db.execute(
            """SELECT name,sector,sds_done,main_goal,business_description
               FROM companies WHERE company_id=?""",
            (self.current["company_id"],),
        ).fetchone()
        self.assertEqual("شركة جديدة", company["name"])
        self.assertIsNone(company["sector"])
        self.assertEqual(0, company["sds_done"])
        self.assertIsNone(company["main_goal"])
        self.assertIsNone(company["business_description"])
        start = self.client.get("/home")
        self.assertEqual(302, start.status_code)
        self.assertTrue(start.headers["Location"].endswith("/onboarding"))

    def test_2_reset_preserves_the_same_authenticated_login(self):
        self._reset()
        account = self.db.execute(
            """SELECT email,password_hash,company_id
               FROM user_accounts WHERE account_id=?""",
            (self.current["account_id"],),
        ).fetchone()
        self.assertEqual(self.current["email"], account["email"])
        self.assertEqual("unchanged-password-hash", account["password_hash"])
        self.assertEqual(self.current["company_id"], account["company_id"])
        with self.client.session_transaction() as login:
            self.assertEqual(self.current["account_id"], login["account_id"])
            self.assertEqual(self.current["company_id"], login["company_id"])

    def test_3_reset_does_not_touch_another_company_or_account(self):
        self._reset()
        company = self.db.execute(
            "SELECT name,sds_done,main_goal FROM companies WHERE company_id=?",
            (self.other["company_id"],),
        ).fetchone()
        self.assertEqual("شركة الأخرى", company["name"])
        self.assertEqual(1, company["sds_done"])
        self.assertEqual("نمو منضبط", company["main_goal"])
        for table in ("scan_runs", "decisions", "tasks", "company_memory_items"):
            count = self.db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE company_id=?",
                (self.other["company_id"],),
            ).fetchone()[0]
            self.assertEqual(1, count, table)
        self.assertIsNotNone(self.db.execute(
            "SELECT 1 FROM user_accounts WHERE account_id=?",
            (self.other["account_id"],),
        ).fetchone())

    def test_4_reset_removes_old_scan_decision_task_and_memory(self):
        self._reset()
        for table in (
            "scan_runs", "decisions", "tasks",
            "company_memory_items", "sana_memory_entries",
        ):
            count = self.db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE company_id=?",
                (self.current["company_id"],),
            ).fetchone()[0]
            self.assertEqual(0, count, table)


if __name__ == "__main__":
    unittest.main()