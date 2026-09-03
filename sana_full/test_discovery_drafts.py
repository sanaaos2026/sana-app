"""اختبارات مسودات جلسة الاكتشاف وعزلها عن الأدلة والشركات."""

import unittest
import uuid

import app as sana_app


class DiscoveryDraftAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sana_app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        conn = sana_app._connect_pg()
        try:
            schema_ready = sana_app._table_exists(conn, "sana_discovery_drafts")
        finally:
            conn.close()
        if not schema_ready:
            sana_app.init_db()

    def setUp(self):
        suffix = uuid.uuid4().hex[:10].upper()
        self.company_a = f"DRA{suffix}"
        self.company_b = f"DRB{suffix}"
        self.account_a = f"DAA{suffix}"
        self.account_a_other = f"DAO{suffix}"
        self.account_b = f"DAB{suffix}"
        self.db = sana_app._connect_pg()
        for company_id in (self.company_a, self.company_b):
            self.db.execute(
                """INSERT INTO companies
                   (company_id, name, sector, signup_code, sds_done)
                   VALUES (?, ?, ?, ?, 0)""",
                (company_id, f"شركة {company_id}", "خدمات", f"CODE-{company_id}"),
            )
        for account_id, company_id in (
            (self.account_a, self.company_a),
            (self.account_a_other, self.company_a),
            (self.account_b, self.company_b),
        ):
            self.db.execute(
                """INSERT INTO user_accounts
                   (account_id, email, password_hash, company_id, admin_role,
                    account_status)
                   VALUES (?, ?, ?, ?, 'USER', 'active')""",
                (account_id, f"{account_id.lower()}@test.local", "not-used", company_id),
            )
        self.db.commit()
        self.client_a = self._client_for(self.account_a, self.company_a)
        self.client_a_other = self._client_for(
            self.account_a_other, self.company_a
        )
        self.client_b = self._client_for(self.account_b, self.company_b)

    def tearDown(self):
        self.db.execute(
            "DELETE FROM sana_discovery_drafts WHERE company_id IN (?, ?)",
            (self.company_a, self.company_b),
        )
        self.db.execute(
            "DELETE FROM user_accounts WHERE company_id IN (?, ?)",
            (self.company_a, self.company_b),
        )
        self.db.execute(
            "DELETE FROM companies WHERE company_id IN (?, ?)",
            (self.company_a, self.company_b),
        )
        self.db.commit()
        self.db.close()

    def _client_for(self, account_id, company_id):
        client = sana_app.app.test_client()
        with client.session_transaction() as session:
            session["account_id"] = account_id
            session["company_id"] = company_id
            session["email"] = f"{account_id.lower()}@test.local"
        return client

    @staticmethod
    def _payload(goal="زيادة المبيعات"):
        return {
            "fit_gate": {"operating_duration": "ONE_PLUS"},
            "q1": goal,
            "q5_text": "ملاحظة خاصة بالمسودة",
            "q7": ["قرارات أوضح"],
            "unknown_field": "لا يجب تخزينه",
        }

    def test_draft_resumes_for_same_account_only_and_stays_outside_evidence(self):
        saved = self.client_a.put(
            "/api/discovery/draft",
            json={"payload": self._payload(), "current_step": 3},
        )
        self.assertEqual(200, saved.status_code, saved.get_data(as_text=True))
        saved_data = saved.get_json()["data"]
        self.assertEqual(3, saved_data["current_step"])

        resumed = self.client_a.get("/api/discovery/draft")
        self.assertEqual(200, resumed.status_code)
        resumed_data = resumed.get_json()["data"]
        self.assertEqual(saved_data["draft_id"], resumed_data["draft_id"])
        self.assertEqual(3, resumed_data["current_step"])
        self.assertEqual("زيادة المبيعات", resumed_data["payload"]["q1"])
        self.assertEqual(
            "ملاحظة خاصة بالمسودة", resumed_data["payload"]["q5_text"]
        )
        self.assertNotIn("unknown_field", resumed_data["payload"])
        page = self.client_a.get("/discovery")
        self.assertEqual(200, page.status_code)
        self.assertIn("INITIAL_DISCOVERY_DRAFT", page.get_data(as_text=True))
        self.assertIn('"current_step": 3', page.get_data(as_text=True))

        other_account = self.client_a_other.get("/api/discovery/draft")
        self.assertEqual(200, other_account.status_code)
        self.assertIsNone(other_account.get_json()["data"])
        self.client_a_other.put(
            "/api/discovery/draft",
            json={"payload": self._payload("هدف الحساب الآخر"), "current_step": 1},
        )
        still_private = self.client_a.get("/api/discovery/draft").get_json()["data"]
        self.assertEqual("زيادة المبيعات", still_private["payload"]["q1"])

        foreign_company = self.client_b.get("/api/discovery/draft")
        self.assertEqual(200, foreign_company.status_code)
        self.assertIsNone(foreign_company.get_json()["data"])
        counts = self.db.execute(
            """SELECT
                 (SELECT count(*) FROM cases WHERE company_id IN (?, ?)) AS cases_count,
                 (SELECT count(*) FROM evidence WHERE company_id IN (?, ?)) AS evidence_count,
                 (SELECT sds_done FROM companies WHERE company_id=?) AS sds_done""",
            (
                self.company_a, self.company_b,
                self.company_a, self.company_b,
                self.company_a,
            ),
        ).fetchone()
        self.assertEqual(0, counts["cases_count"])
        self.assertEqual(0, counts["evidence_count"])
        self.assertEqual(0, counts["sds_done"])

    def test_expired_draft_is_deleted_and_cannot_resume(self):
        self.client_a.put(
            "/api/discovery/draft",
            json={"payload": self._payload(), "current_step": 2},
        )
        self.db.execute(
            """UPDATE sana_discovery_drafts
               SET expires_at=now() - interval '1 minute'
               WHERE company_id=? AND account_id=?""",
            (self.company_a, self.account_a),
        )
        self.db.commit()

        response = self.client_a.get("/api/discovery/draft")
        self.assertEqual(200, response.status_code)
        self.assertIsNone(response.get_json()["data"])
        remaining = self.db.execute(
            "SELECT count(*) AS count FROM sana_discovery_drafts WHERE company_id=?",
            (self.company_a,),
        ).fetchone()
        self.assertEqual(0, remaining["count"])

    def test_invalid_step_and_oversized_answer_are_rejected(self):
        invalid_step = self.client_a.put(
            "/api/discovery/draft",
            json={"payload": self._payload(), "current_step": 9},
        )
        self.assertEqual(400, invalid_step.status_code)
        self.assertEqual(
            "INVALID_DISCOVERY_DRAFT", invalid_step.get_json()["error"]
        )

        oversized = self.client_a.put(
            "/api/discovery/draft",
            json={"payload": {**self._payload(), "q1": "x" * 2500}, "current_step": 1},
        )
        self.assertEqual(400, oversized.status_code)

    def test_admin_preview_cannot_create_or_read_customer_draft(self):
        with self.client_a.session_transaction() as session:
            session.clear()
        response = self.client_a.put(
            f"/api/discovery/draft?admin_key={sana_app.ADMIN_PREVIEW_KEY}",
            json={"payload": self._payload(), "current_step": 1},
        )
        self.assertEqual(401, response.status_code)


if __name__ == "__main__":
    unittest.main()