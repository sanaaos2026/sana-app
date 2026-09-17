import json
import secrets
import unittest
from pathlib import Path

import app as sana_app


class ExpertReviewTemplateTest(unittest.TestCase):
    def test_expert_review_mark_covers_every_report_action_state(self):
        template = (
            Path(__file__).parent / "templates" / "14-passport-report.html"
        ).read_text(encoding="utf-8")

        self.assertEqual(template.count("class_name='expert-review-mark'"), 6)
        self.assertEqual(
            template.count("class_name='expert-review-mark', decorative=true"),
            6,
        )
        self.assertIn(
            ".expert-review-mark{width:15px;height:15px;flex:0 0 15px;"
            "margin-left:6px;"
            "color:var(--gold)}",
            template,
        )


class ExpertReviewSliceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sana_app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        sana_app.init_db()
        suffix = secrets.token_hex(5).upper()
        cls.company_id = f"C-EXPERT-{suffix}"
        cls.case_id = f"CASE-EXPERT-{suffix}"
        cls.decision_id = f"DEC-EXPERT-{suffix}"
        cls.task_id = f"TASK-EXPERT-{suffix}"
        cls.evidence_id = f"EVD-EXPERT-{suffix}"
        cls.review_id = f"HR-EXPERT-{suffix}"
        cls.owner_id = f"ACC-OWNER-{suffix}"
        cls.admin_id = f"ACC-ADMIN-{suffix}"

        with sana_app.app.app_context():
            db = sana_app.get_db()
            db.execute(
                "INSERT INTO companies (company_id,name,company_code) VALUES (?,?,?)",
                (cls.company_id, f"Expert Review Test {suffix}", f"EX{suffix[-6:]}"),
            )
            db.execute(
                """INSERT INTO user_accounts
                   (account_id,email,password_hash,company_id,is_admin,admin_role,
                    admin_permissions,account_status)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    cls.owner_id, f"owner-{suffix}@example.test", "not-used",
                    cls.company_id, 0, "USER", "[]", "active",
                ),
            )
            db.execute(
                """INSERT INTO user_accounts
                   (account_id,email,password_hash,company_id,is_admin,admin_role,
                    admin_permissions,account_status)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    cls.admin_id, f"expert-{suffix}@example.test", "not-used",
                    None, 1, "ADMIN", json.dumps(["review_cases"]),
                    "active",
                ),
            )
            from sana_billing import activate_free
            activate_free(db, cls.company_id, period_days=30)
            db.execute(
                """INSERT INTO cases
                   (case_id,company_id,case_title,declared_problem,real_question)
                   VALUES (?,?,?,?,?)""",
                (
                    cls.case_id, cls.company_id, "تعثر التسليم",
                    "تأخر بعض المهام", "ما الذي يمنع التسليم في موعده؟",
                ),
            )
            db.execute(
                """INSERT INTO decisions
                   (decision_id,company_id,case_id,title,recommended_action,status)
                   VALUES (?,?,?,?,?,?)""",
                (
                    cls.decision_id, cls.company_id, cls.case_id,
                    "تثبيت مسؤول التسليم", "حدد مسؤولًا واحدًا للتسليم", "مقترح",
                ),
            )
            db.execute(
                """INSERT INTO tasks
                   (task_id,company_id,decision_id,title,status)
                   VALUES (?,?,?,?,?)""",
                (
                    cls.task_id, cls.company_id, cls.decision_id,
                    "تسمية مسؤول التسليم", "لم تبدأ",
                ),
            )
            db.execute(
                """INSERT INTO evidence
                   (evidence_id,company_id,case_id,title,source_type,source_ref,
                    verification_status)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    cls.evidence_id, cls.company_id, cls.case_id,
                    "سجل مواعيد التسليم", "Document", "delivery-log.csv",
                    "VERIFIED",
                ),
            )
            db.execute(
                """INSERT INTO case_human_reviews
                   (review_id,company_id,case_id,decision_id,requested_by,
                    reason_code,reason_label,status,before_snapshot_json)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    cls.review_id, cls.company_id, cls.case_id, cls.decision_id,
                    cls.owner_id, "human_review_request", "طلب مراجعة بشرية",
                    "REQUESTED", "{}",
                ),
            )
            db.commit()

    @classmethod
    def tearDownClass(cls):
        with sana_app.app.app_context():
            db = sana_app.get_db()
            db.execute(
                "DELETE FROM human_review_events WHERE review_id=?",
                (cls.review_id,),
            )
            db.execute(
                "DELETE FROM admin_audit_log WHERE company_id=?",
                (cls.company_id,),
            )
            db.execute(
                "DELETE FROM case_human_reviews WHERE review_id=?",
                (cls.review_id,),
            )
            db.execute("DELETE FROM tasks WHERE task_id=?", (cls.task_id,))
            db.execute(
                "DELETE FROM decisions WHERE decision_id=?", (cls.decision_id,)
            )
            db.execute(
                "DELETE FROM evidence WHERE evidence_id=?", (cls.evidence_id,)
            )
            db.execute("DELETE FROM cases WHERE case_id=?", (cls.case_id,))
            db.execute(
                "DELETE FROM user_accounts WHERE account_id IN (?,?)",
                (cls.owner_id, cls.admin_id),
            )
            db.execute(
                "DELETE FROM sana_company_subscriptions WHERE company_id=?",
                (cls.company_id,),
            )
            db.execute(
                "DELETE FROM research_sources WHERE notes LIKE ?",
                (f"%Review {cls.review_id}%",),
            )
            db.execute(
                "DELETE FROM companies WHERE company_id=?", (cls.company_id,)
            )
            db.commit()

    def _client_for(self, account_id, *, admin_company_id=None):
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
            if admin_company_id:
                session["admin_company_id"] = admin_company_id
        return client

    def test_save_format_approve_then_client_view_and_download(self):
        admin = self._client_for(
            self.admin_id, admin_company_id=self.company_id,
        )
        client = self._client_for(self.owner_id)
        notes = "ملاحظة داخلية: افحص مسؤولية التسليم قبل عرضها للعميل."
        client_observation = "لاحظ الخبير أن ملكية التسليم تحتاج وضوحًا أكبر."

        response = admin.patch(
            f"/api/admin/human-reviews/{self.review_id}/notes",
            json={"notes": notes},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

        response = admin.patch(
            f"/api/admin/human-reviews/{self.review_id}/expert-inputs",
            json={
                "current_situation": "توجد فجوة في وضوح مسؤولية التسليم.",
                "client_observation": client_observation,
                "priority": "تثبيت ملكية التسليم",
                "recommendation": "تحديد مسؤول واحد لكل تسليم",
                "plan": "تطبيق المسؤولية على مشروع واحد ثم مراجعة النتيجة",
                "next_action": "سمّ مسؤول التسليم للمشروع الجاري",
                "references": [{"label": "دليل التسليم", "url": "https://example.com/delivery"}],
                "knowledge_candidate": {
                    "text": f"في {self.company_id} يظهر نمط: وضوح مالك التسليم يقلل تعثر المتابعة",
                    "scope": "sector",
                    "sector": "consulting",
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

        client_payload = client.get(
            f"/api/cases/{self.case_id}/human-review"
        )
        self.assertEqual(client_payload.status_code, 200)
        serialized = json.dumps(client_payload.get_json(), ensure_ascii=False)
        self.assertNotIn(notes, serialized)
        self.assertNotIn(client_observation, serialized)
        self.assertNotIn("reviewer_note", serialized)
        self.assertNotIn("expert_inputs_json", serialized)

        response = admin.post(
            f"/api/admin/human-reviews/{self.review_id}/format-summary"
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        summary = response.get_json()["data"]["summary"]
        self.assertEqual(summary["expert_observations"], client_observation)
        self.assertNotIn(notes, json.dumps(summary, ensure_ascii=False))
        self.assertEqual(summary["priority"], "تثبيت ملكية التسليم")
        self.assertIn("مشروع واحد", summary["plan"])
        self.assertEqual(summary["references"][0]["label"], "دليل التسليم")
        self.assertIn("سجل مواعيد التسليم", json.dumps(summary, ensure_ascii=False))

        query = (
            f"?view=expert-summary&case_id={self.case_id}"
            f"&review_id={self.review_id}"
        )
        before_approval = client.get(
            f"/company/{self.company_id}/scan-report{query}"
        )
        self.assertEqual(before_approval.status_code, 404)

        response = admin.post(
            f"/api/admin/human-reviews/{self.review_id}/approve-summary"
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        candidate_id = response.get_json()["data"]["knowledge_candidate_id"]
        self.assertTrue(candidate_id)
        with sana_app.app.app_context():
            candidate = sana_app.get_db().execute(
                "SELECT summary,knowledge_scope,review_status FROM research_sources WHERE research_source_id=?",
                (candidate_id,),
            ).fetchone()
            self.assertEqual(candidate["knowledge_scope"], "shared_candidate")
            self.assertEqual(candidate["review_status"], "inbox")
            self.assertNotIn(self.company_id, candidate["summary"])

        html = client.get(f"/company/{self.company_id}/scan-report{query}")
        self.assertEqual(html.status_code, 200, html.get_data(as_text=True))
        self.assertIn("ملخص مراجعة الخبير", html.get_data(as_text=True))
        self.assertIn(client_observation, html.get_data(as_text=True))
        self.assertNotIn(notes, html.get_data(as_text=True))
        self.assertIn("خطة الخبير", html.get_data(as_text=True))
        self.assertIn("دليل التسليم", html.get_data(as_text=True))

        pdf = client.get(
            f"/api/companies/{self.company_id}/passport/report-pdf{query}"
        )
        self.assertEqual(pdf.status_code, 200, pdf.get_data()[:500])
        self.assertEqual(pdf.mimetype, "application/pdf")
        self.assertGreater(len(pdf.get_data()), 1000)

        with sana_app.app.app_context():
            actions = {
                row["action"] for row in sana_app.get_db().execute(
                    """SELECT action FROM admin_audit_log
                       WHERE company_id=? AND target_id=?""",
                    (self.company_id, self.review_id),
                ).fetchall()
            }
        self.assertTrue({
            "expert_review_notes_saved",
            "expert_review_inputs_saved",
            "expert_review_summary_formatted",
            "expert_review_summary_approved",
        }.issubset(actions))


if __name__ == "__main__":
    unittest.main()