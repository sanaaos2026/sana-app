"""اختبارات P0 للوحة الإدارة الداخلية وRBAC."""

import unittest
import uuid

import app as sana_app


class AdminDashboardP0Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sana_app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        sana_app.init_db(force=True)
        cls.db = sana_app._connect_pg()
        cls.suffix = uuid.uuid4().hex[:8].upper()
        cls.company_a = f"ADM{cls.suffix}A"
        cls.company_b = f"ADM{cls.suffix}B"
        cls.super_id = f"ACC-ADM-S-{cls.suffix}"
        cls.admin_id = f"ACC-ADM-A-{cls.suffix}"
        cls.user_id = f"ACC-ADM-U-{cls.suffix}"
        for company_id, name in (
            (cls.company_a, "شركة إدارة أ"),
            (cls.company_b, "شركة إدارة ب"),
        ):
            cls.db.execute(
                """INSERT INTO companies
                   (company_id,name,sector,signup_code)
                   VALUES (?,?,?,?)""",
                (company_id, name, "اختبار", f"TEST-{company_id}"),
            )
        accounts = (
            (cls.super_id, f"super-{cls.suffix}@test.local", cls.company_a, "SUPER_ADMIN"),
            (cls.admin_id, f"admin-{cls.suffix}@test.local", cls.company_a, "ADMIN"),
            (cls.user_id, f"user-{cls.suffix}@test.local", cls.company_b, "USER"),
        )
        for account_id, email, company_id, role in accounts:
            cls.db.execute(
                """INSERT INTO user_accounts
                   (account_id,email,password_hash,company_id,is_admin,admin_role,account_status)
                   VALUES (?,?,?,?,?,?,?)""",
                (account_id, email, "not-used", company_id,
                 1 if role != "USER" else 0, role, "active"),
            )
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.db.execute(
                "DELETE FROM admin_audit_log WHERE actor_account_id IN (?,?,?)",
                (cls.super_id, cls.admin_id, cls.user_id),
            )
            cls.db.execute(
                "DELETE FROM user_accounts WHERE account_id IN (?,?,?)",
                (cls.super_id, cls.admin_id, cls.user_id),
            )
            cls.db.execute(
                "DELETE FROM companies WHERE company_id IN (?,?)",
                (cls.company_a, cls.company_b),
            )
            cls.db.commit()
        finally:
            cls.db.close()

    def _client(self, account_id, company_id):
        client = sana_app.app.test_client()
        with client.session_transaction() as session:
            session["account_id"] = account_id
            session["company_id"] = company_id
            session["email"] = f"{account_id}@test.local"
        return client

    def test_regular_user_cannot_reach_admin_page_or_api(self):
        client = self._client(self.user_id, self.company_b)
        page = client.get("/admin")
        self.assertEqual(302, page.status_code)
        response = client.get("/api/admin/overview")
        self.assertEqual(403, response.status_code)
        self.assertEqual("ADMIN_FORBIDDEN", response.get_json()["error"])

    def test_super_admin_can_view_dashboard_and_cross_company_access_is_audited(self):
        client = self._client(self.super_id, self.company_a)
        response = client.get("/api/admin/overview")
        self.assertEqual(200, response.status_code)
        self.assertEqual("SUPER_ADMIN", response.get_json()["data"]["role"])
        self.assertNotIn("password_hash", response.get_data(as_text=True))

        detail = client.get(f"/api/admin/companies/{self.company_b}")
        self.assertEqual(200, detail.status_code)
        audit = self.db.execute(
            """SELECT action,company_id FROM admin_audit_log
               WHERE actor_account_id=? AND target_id=?
               ORDER BY created_at DESC LIMIT 1""",
            (self.super_id, self.company_b),
        ).fetchone()
        self.assertIsNotNone(audit)
        self.assertEqual("company_cross_company_view", audit["action"])

    def test_admin_cannot_change_super_admin_role(self):
        client = self._client(self.admin_id, self.company_a)
        response = client.patch(
            f"/api/admin/users/{self.super_id}",
            json={"account_status": "disabled", "reason": "اختبار حدود الدور"},
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual("SUPER_ADMIN_REQUIRED", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()