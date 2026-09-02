import json
import re
import secrets
import unittest
from unittest.mock import patch

from werkzeug.security import generate_password_hash

import app as sana_app


class AdminManagementSliceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sana_app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        sana_app.init_db()
        cls.super_id = "ACC-TEST-SUPER-" + secrets.token_hex(5).upper()
        with sana_app.app.app_context():
            db = sana_app.get_db()
            db.execute(
                """INSERT INTO user_accounts
                   (account_id,email,password_hash,company_id,is_admin,admin_role,
                    admin_permissions,account_status)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    cls.super_id,
                    f"slice-super-{secrets.token_hex(4)}@example.test",
                    generate_password_hash(secrets.token_urlsafe(32)),
                    None, 1, "SUPER_ADMIN", "[]", "active",
                ),
            )
            db.commit()

    @classmethod
    def tearDownClass(cls):
        with sana_app.app.app_context():
            db = sana_app.get_db()
            account_ids = [
                row["account_id"] for row in db.execute(
                    """SELECT account_id FROM user_accounts
                       WHERE account_id=? OR email LIKE ?""",
                    (cls.super_id, "slice-%@example.test"),
                ).fetchall()
            ]
            company_ids = [
                row["company_id"] for row in db.execute(
                    "SELECT company_id FROM companies WHERE name LIKE ?",
                    ("Slice Test %",),
                ).fetchall()
            ]
            if account_ids:
                marks = ",".join("?" for _ in account_ids)
                db.execute(
                    f"DELETE FROM password_reset_tokens WHERE account_id IN ({marks})",
                    account_ids,
                )
            if company_ids:
                marks = ",".join("?" for _ in company_ids)
                db.execute(
                    f"DELETE FROM admin_notification_outbox WHERE company_id IN ({marks})",
                    company_ids,
                )
                db.execute(
                    f"DELETE FROM company_invitations WHERE company_id IN ({marks})",
                    company_ids,
                )
                db.execute(
                    f"DELETE FROM admin_audit_log WHERE company_id IN ({marks})",
                    company_ids,
                )
                db.execute(f"DELETE FROM assets WHERE company_id IN ({marks})", company_ids)
            if account_ids:
                marks = ",".join("?" for _ in account_ids)
                db.execute(
                    f"DELETE FROM admin_audit_log WHERE actor_account_id IN ({marks})",
                    account_ids,
                )
                db.execute(
                    f"DELETE FROM admin_notification_outbox WHERE created_by IN ({marks})",
                    account_ids,
                )
                db.execute(f"DELETE FROM user_accounts WHERE account_id IN ({marks})", account_ids)
            if company_ids:
                marks = ",".join("?" for _ in company_ids)
                db.execute(f"DELETE FROM companies WHERE company_id IN ({marks})", company_ids)
            db.commit()

    def _client_for(self, account_id):
        client = sana_app.app.test_client()
        with sana_app.app.app_context():
            row = sana_app.get_db().execute(
                "SELECT * FROM user_accounts WHERE account_id=?", (account_id,)
            ).fetchone()
        with client.session_transaction() as session:
            session.update({
                "account_id": row["account_id"],
                "email": row["email"],
                "company_id": row["company_id"],
                "is_admin": bool(row["is_admin"]),
                "admin_role": row["admin_role"],
                "account_status": row["account_status"],
            })
        return client

    def test_acceptance_a_through_h(self):
        super_client = self._client_for(self.super_id)

        # A — حساب عام بلا عضوية يصل إلى Command Center.
        response = super_client.get("/api/admin/overview")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["role"], "SUPER_ADMIN")

        # B — Admin بصلاحية واحدة فقط، بلا كلمة مرور مكشوفة.
        admin_email = f"slice-admin-{secrets.token_hex(4)}@example.test"
        response = super_client.post("/api/admin/admins", json={
            "email": admin_email,
            "permissions": ["manage_companies"],
            "reason": "اختبار الصلاحيات الدقيقة",
        })
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        admin_data = response.get_json()["data"]
        self.assertNotIn("token", json.dumps(admin_data).lower())
        self.assertNotIn("password", json.dumps(admin_data).lower())
        admin_id = admin_data["account_id"]
        with sana_app.app.app_context():
            db = sana_app.get_db()
            db.execute(
                "UPDATE user_accounts SET account_status='active' WHERE account_id=?",
                (admin_id,),
            )
            db.commit()
        limited_client = self._client_for(admin_id)

        # C/D — إنشاء شركتين بأكواد فريدة، ثم تحديث معلومات الشركة الأولى.
        created = []
        for suffix, client in (("A", limited_client), ("B", super_client)):
            response = client.post("/api/admin/companies", json={
                "name": f"Slice Test {suffix}",
                "contact_email": f"slice-{suffix.lower()}@example.test",
                "lifecycle_status": (
                    "Registered" if suffix == "A" else "Internal Managed"
                ),
                "reason": "اختبار إنشاء شركة",
            })
            self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
            created.append(response.get_json()["data"])
        self.assertNotEqual(created[0]["company_code"], created[1]["company_code"])
        company_a, company_b = created
        response = limited_client.patch(
            f"/api/admin/companies/{company_a['company_id']}",
            json={
                "name": "Slice Test A Updated",
                "lifecycle_status": "Internal Managed",
                "reason": "اختبار تعديل الشركة",
            },
        )
        self.assertEqual(response.status_code, 200)
        forbidden = limited_client.post(
            f"/api/admin/companies/{company_a['company_id']}/invitations",
            json={
                "email": "slice-denied@example.test",
                "company_role": "COMPANY_MEMBER",
            },
        )
        self.assertEqual(forbidden.status_code, 403)

        # E — دعوة hash-only ثم reset بلا token أو كلمة مرور في الاستجابة.
        member_email = f"slice-member-{secrets.token_hex(4)}@example.test"
        response = super_client.post(
            f"/api/admin/companies/{company_a['company_id']}/invitations",
            json={
                "email": member_email,
                "company_role": "COMPANY_OWNER",
                "reason": "اختبار الدعوة",
            },
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        invite_data = response.get_json()["data"]
        serialized = json.dumps(invite_data).lower()
        self.assertNotIn("token", serialized)
        self.assertNotIn("password", serialized)
        member_id = invite_data["account_id"]
        response = super_client.post(f"/api/admin/users/{member_id}/reset-password")
        self.assertEqual(response.status_code, 200)
        serialized = json.dumps(response.get_json()["data"]).lower()
        self.assertNotIn("token", serialized)
        self.assertNotIn("password", serialized)

        # The retired compatibility route must never accept the old secret path.
        response = super_client.post("/api/admin/attach-account", json={
            "admin_key": "legacy-key",
            "email": "slice-legacy@example.test",
            "password": "NeverAccepted123!",
            "company_id": company_a["company_id"],
        })
        self.assertEqual(response.status_code, 410)
        self.assertNotIn("NeverAccepted123!", response.get_data(as_text=True))

        # One invitation is accepted through the real public activation path.
        accepted_email = f"slice-accepted-{secrets.token_hex(4)}@example.test"
        sent_html = []
        with patch.object(
            sana_app, "_admin_send_email",
            side_effect=lambda _db, **kwargs: (
                sent_html.append(kwargs["html_body"])
                or {"notification_id": "test", "status": "queued"}
            ),
        ):
            response = super_client.post(
                f"/api/admin/companies/{company_a['company_id']}/invitations",
                json={
                    "email": accepted_email,
                    "company_role": "COMPANY_MEMBER",
                    "reason": "اختبار قبول الدعوة",
                },
            )
        self.assertEqual(response.status_code, 201)
        invite_token = re.search(r"token=([^'&]+)", sent_html[-1]).group(1)
        response = super_client.post("/accept-invitation", json={
            "token": invite_token,
            "password": "AcceptedPassword123!",
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.get_json()["success"])

        # A second invitation covers cancellation without exposing its token.
        cancel_email = f"slice-cancel-{secrets.token_hex(4)}@example.test"
        response = super_client.post(
            f"/api/admin/companies/{company_a['company_id']}/invitations",
            json={
                "email": cancel_email,
                "company_role": "COMPANY_MEMBER",
                "reason": "اختبار إلغاء الدعوة",
            },
        )
        self.assertEqual(response.status_code, 201)
        cancel_invitation_id = response.get_json()["data"]["invitation_id"]
        response = super_client.post(
            f"/api/admin/companies/{company_a['company_id']}/invitations/"
            f"{cancel_invitation_id}/cancel"
        )
        self.assertEqual(response.status_code, 200)

        # F — العضو لا يستطيع قراءة شركة أخرى.
        with sana_app.app.app_context():
            db = sana_app.get_db()
            db.execute(
                "UPDATE user_accounts SET account_status='active' WHERE account_id=?",
                (member_id,),
            )
            db.commit()
        member_client = self._client_for(member_id)
        response = member_client.get(
            f"/api/companies/{company_b['company_id']}/summary"
        )
        self.assertEqual(response.status_code, 403)

        # G — التقرير يحمل معرف الشركة ورمزها الصحيحين.
        response = super_client.get(
            f"/api/admin/companies/{company_a['company_id']}/reports/export?format=text"
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(
            response.headers["X-Sana-Company-Id"], company_a["company_id"]
        )
        self.assertEqual(
            response.headers["X-Sana-Company-Code"], company_a["company_code"]
        )
        report = response.get_data(as_text=True)
        self.assertIn(company_a["company_id"], report)
        self.assertIn(company_a["company_code"], report)
        response = super_client.get(
            f"/api/admin/companies/{company_a['company_id']}/reports/export?format=pdf"
        )
        self.assertEqual(response.status_code, 200, response.get_data()[:500])
        self.assertEqual(
            response.headers["X-Sana-Company-Code"], company_a["company_code"]
        )

        # H — العمليات الحساسة الأساسية موجودة في Audit.
        response = super_client.get("/api/admin/audit?limit=200")
        self.assertEqual(response.status_code, 200)
        actions = {row["action"] for row in response.get_json()["data"]}
        self.assertTrue({
            "admin_created", "company_created", "company_updated",
            "company_invitation_issued", "password_reset_issued",
            "company_report_exported",
        }.issubset(actions))


if __name__ == "__main__":
    unittest.main()