"""اختبار مركز للحساب الرسمي لقيادة سنع."""

import unittest

import app as sana_app


OFFICIAL_EMAIL = "sanaaos2026@gmail.com"


class OfficialSuperAdminAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sana_app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        sana_app.init_db()

    def test_official_account_is_active_super_admin_audited_and_reset_ready(self):
        db = sana_app._connect_pg()
        try:
            account = db.execute(
                """SELECT account_id,email,company_id,is_admin,admin_role,account_status
                   FROM user_accounts WHERE LOWER(email)=?""",
                (OFFICIAL_EMAIL,),
            ).fetchone()
            self.assertIsNotNone(account)
            self.assertEqual("active", account["account_status"])
            self.assertEqual("SUPER_ADMIN", account["admin_role"])
            self.assertTrue(account["is_admin"])

            reset_token = db.execute(
                """SELECT 1 FROM password_reset_tokens
                   WHERE account_id=? AND used=false AND expires_at > now()
                   LIMIT 1""",
                (account["account_id"],),
            ).fetchone()
            self.assertIsNotNone(reset_token)

            audit = db.execute(
                """SELECT action,target_type,target_id FROM admin_audit_log
                   WHERE actor_account_id=?
                     AND action='super_admin_account_provisioned'
                   ORDER BY created_at DESC LIMIT 1""",
                (account["account_id"],),
            ).fetchone()
            self.assertIsNotNone(audit)
            self.assertEqual("user_account", audit["target_type"])
            self.assertEqual(account["account_id"], audit["target_id"])
        finally:
            db.close()

    def test_dashboard_requires_authentication_and_accepts_authenticated_super_admin(self):
        anonymous = sana_app.app.test_client()
        response = anonymous.get("/admin")
        self.assertEqual(302, response.status_code)
        self.assertIn("/login", response.headers["Location"])

        db = sana_app._connect_pg()
        try:
            account = db.execute(
                """SELECT account_id,company_id,email,admin_role,account_status
                   FROM user_accounts WHERE LOWER(email)=?""",
                (OFFICIAL_EMAIL,),
            ).fetchone()
        finally:
            db.close()

        authenticated = sana_app.app.test_client()
        with authenticated.session_transaction() as session:
            session["account_id"] = account["account_id"]
            session["company_id"] = account["company_id"]
            session["email"] = account["email"]
            session["admin_role"] = account["admin_role"]
            session["account_status"] = account["account_status"]

        page = authenticated.get("/admin")
        self.assertEqual(200, page.status_code)
        overview = authenticated.get("/api/admin/overview")
        self.assertEqual(200, overview.status_code)
        self.assertEqual("SUPER_ADMIN", overview.get_json()["data"]["role"])


if __name__ == "__main__":
    unittest.main()