"""Acceptance tests for the private Zubair Deal Brain beta."""
import json
import os
import re
import unittest
import uuid
from datetime import date, timedelta
from pathlib import Path
import sys

from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import app as sana_app
from zubair_deal_brain import (
    GOVERNANCE_ID,
    attention_items,
    confirm_draft,
    create_draft,
    ensure_schema,
    get_draft,
    match_draft,
    metrics,
    review_packet,
    save_review,
)


class ZubairDealBrainAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL غير مضبوط")
        cls.db = sana_app._connect_pg()
        ensure_schema(cls.db)
        companies = cls.db.execute(
            "SELECT company_id FROM companies ORDER BY company_id LIMIT 2"
        ).fetchall()
        if len(companies) < 2:
            cls.db.close()
            raise unittest.SkipTest("يلزم شركتان لاختبار العزل")
        cls.cid_a, cls.cid_b = companies[0]["company_id"], companies[1]["company_id"]
        token = uuid.uuid4().hex[:10].upper()
        cls.admin_id = f"ZDB-ADMIN-{token}"
        cls.normal_id = f"ZDB-NORMAL-{token}"
        cls.db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id,is_admin)
               VALUES (?,?,?,?,1)""",
            (cls.admin_id, f"zdb-admin-{token}@test.local",
             generate_password_hash("test"), cls.cid_a),
        )
        cls.db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id,is_admin)
               VALUES (?,?,?,?,0)""",
            (cls.normal_id, f"zdb-normal-{token}@test.local",
             generate_password_hash("test"), cls.cid_b),
        )
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.rollback()
        cls.db.execute(
            "DELETE FROM user_accounts WHERE account_id IN (?,?)",
            (cls.admin_id, cls.normal_id),
        )
        cls.db.commit()
        cls.db.close()

    def setUp(self):
        self.db.rollback()
        self.run = uuid.uuid4().hex[:8].upper()

    def tearDown(self):
        self.db.rollback()

    def _draft(self, text, attachments=None, extractor=None):
        draft, duplicate = create_draft(
            self.db, self.cid_a, self.admin_id, "text", text,
            attachments or [], extractor,
        )
        self.assertFalse(duplicate)
        return draft

    def _admin_client(self):
        client = sana_app.app.test_client()
        with client.session_transaction() as session:
            session["account_id"] = self.admin_id
            session["company_id"] = self.cid_a
            session["email"] = f"zdb-admin-{self.run}@test.local"
            session["is_admin"] = True
        return client

    def _normal_client(self):
        client = sana_app.app.test_client()
        with client.session_transaction() as session:
            session["account_id"] = self.normal_id
            session["company_id"] = self.cid_b
            session["email"] = f"zdb-normal-{self.run}@test.local"
        return client

    def test_server_side_founder_gate_and_existing_sales_compatibility(self):
        self.assertEqual(200, self._admin_client().get("/zubair/deal-brain").status_code)
        self.assertEqual(403, self._normal_client().get("/zubair/deal-brain").status_code)
        self.assertEqual(200, self._admin_client().get(
            "/api/zubair/deal-brain/review"
        ).status_code)
        self.assertEqual(403, self._normal_client().get(
            "/api/zubair/deal-brain/review"
        ).status_code)
        self.assertEqual(200, self._admin_client().get(
            f"/api/companies/{self.cid_a}/opportunities"
        ).status_code)
        self.assertEqual(200, self._admin_client().get(
            f"/api/companies/{self.cid_a}/decision-room"
        ).status_code)

    def test_unknown_is_preserved_and_ungrounded_ai_values_are_rejected(self):
        def fake_ai(_system, _user):
            return {"raw_text": json.dumps({
                "person": {"value": "أحمد", "classification": "Fact", "source": "input:text"},
                "amount": {"value": "999999", "classification": "Inference", "source": "input:text"},
                "stage": {"value": "فوز", "classification": "Inference", "source": "input:text"},
            }, ensure_ascii=False)}
        draft = self._draft(f"تحدثت مع أحمد بخصوص الخدمة {self.run}", extractor=fake_ai)
        self.assertEqual("أحمد", draft["fields"]["person"]["value"])
        self.assertEqual("Unknown", draft["fields"]["amount"]["classification"])
        self.assertEqual("Unknown", draft["fields"]["stage"]["classification"])
        self.assertEqual("Unknown", draft["fields"]["objection"]["classification"])

    def test_duplicate_capture_is_idempotent_and_company_isolated(self):
        text = f"ملاحظة فريدة {self.run}"
        first, duplicate = create_draft(
            self.db, self.cid_a, self.admin_id, "text", text, [], None
        )
        second, duplicate = create_draft(
            self.db, self.cid_a, self.admin_id, "text", text, [], None
        )
        self.assertFalse(duplicate is False and first["draft_id"] != second["draft_id"])
        self.assertEqual(first["draft_id"], second["draft_id"])
        self.assertTrue(duplicate)
        self.assertIsNone(get_draft(self.db, self.cid_b, first["draft_id"]))

    def test_exact_and_ambiguous_matching_without_auto_create(self):
        for suffix, phone in (("A", "966511111111"), ("B", "966522222222")):
            self.db.execute(
                """INSERT INTO zubair_contacts
                   (contact_id,company_id,full_name,normalized_name,phone,normalized_phone,
                    source_ref,governance_id,case_link,asset_link,framework_link,business_event)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"ZC-{self.run}-{suffix}", self.cid_a, "أحمد الزهراني", "احمدالزهراني",
                    phone, phone, "test", GOVERNANCE_ID, "N/A — Deferred",
                    "N/A — Deferred", "framework:B2B-OS-001", "test.match",
                ),
            )
        ambiguous = self._draft(f"تحدثت مع أحمد الزهراني {self.run}")
        ambiguous["fields"]["person"] = {
            "value": "أحمد الزهراني", "classification": "Fact", "source": "founder-confirmed"
        }
        self.db.execute(
            "UPDATE zubair_capture_drafts SET extracted_json=? WHERE draft_id=?",
            (json.dumps(ambiguous["fields"], ensure_ascii=False), ambiguous["draft_id"]),
        )
        self.assertEqual("ambiguous", match_draft(
            self.db, self.cid_a, ambiguous["draft_id"]
        )["match_status"])
        exact = self._draft(f"جوال العميل 0511111111 {self.run}")
        exact["fields"]["phone"] = {
            "value": "0511111111", "classification": "Fact", "source": "founder-confirmed"
        }
        self.db.execute(
            "UPDATE zubair_capture_drafts SET extracted_json=? WHERE draft_id=?",
            (json.dumps(exact["fields"], ensure_ascii=False), exact["draft_id"]),
        )
        match = match_draft(self.db, self.cid_a, exact["draft_id"])
        self.assertEqual(1, len(match["contact"]))
        self.assertEqual(f"ZC-{self.run}-A", match["contact"][0]["contact_id"])

    def test_explicit_confirmation_creates_core_records_and_links_attachment_evidence(self):
        due = (date.today() + timedelta(days=2)).isoformat()
        draft = self._draft(
            f"اجتماع خاص {self.run}",
            [{"filename": f"screen-{self.run}.png", "mime_type": "image/png", "content": b"png"}],
        )
        result = confirm_draft(
            self.db, self.cid_a, self.admin_id, draft["draft_id"],
            edits={
                "person": f"عميل {self.run}",
                "prospect_company": f"شركة {self.run}",
                "opportunity": f"فرصة {self.run}",
                "stage": "مؤهل",
                "need": "تسريع المبيعات",
                "next_action": f"اتصال متابعة {self.run}",
                "next_action_due": due,
            },
            create_opportunity=True,
        )
        self.assertIsNotNone(result["contact_id"])
        self.assertIsNotNone(result["prospect_company_id"])
        self.assertIsNotNone(result["opp_id"])
        self.assertEqual(1, len(result["attached_evidence"]))
        self.assertEqual("confirmed", get_draft(
            self.db, self.cid_a, draft["draft_id"]
        )["status"])
        self.assertEqual(1, self.db.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE company_id=? AND title=?",
            (self.cid_a, f"اتصال متابعة {self.run}"),
        ).fetchone()["n"])
        self.assertEqual(1, self.db.execute(
            "SELECT COUNT(*) AS n FROM zubair_timeline_events WHERE draft_id=? AND event_type='evidence_attached'",
            (draft["draft_id"],),
        ).fetchone()["n"])
        self.assertEqual(1, self.db.execute(
            "SELECT COUNT(*) AS n FROM zubair_timeline_events WHERE draft_id=? AND event_type='opportunity_created'",
            (draft["draft_id"],),
        ).fetchone()["n"])

    def test_api_rejects_write_without_confirmation(self):
        draft = self._draft(f"لا تحفظ هذا الإدخال {self.run}")
        client = self._admin_client()
        page = client.get("/zubair/deal-brain")
        token = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            page.get_data(as_text=True),
        ).group(1)
        response = client.post(
            f"/api/zubair/deal-brain/drafts/{draft['draft_id']}/confirm",
            json={"confirm": False},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual("CONFIRMATION_REQUIRED", response.get_json()["error"])

    def test_browser_api_posts_work_with_csrf_enabled(self):
        client = self._admin_client()
        page = client.get("/zubair/deal-brain")
        token = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            page.get_data(as_text=True),
        ).group(1)
        response = client.post(
            "/api/zubair/deal-brain/captures",
            json={"input_type": "image", "raw_text": f"CSRF flow {self.run}"},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(201, response.status_code, response.get_data(as_text=True))
        created_id = response.get_json()["data"]["draft_id"]
        matched = client.post(
            f"/api/zubair/deal-brain/drafts/{created_id}/match",
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(200, matched.status_code)
        self.db.rollback()
        self.db.execute(
            "DELETE FROM zubair_capture_drafts WHERE draft_id=?",
            (created_id,),
        )
        self.db.commit()

    def test_metrics_exclude_records_older_than_30_days(self):
        baseline = metrics(self.db, self.cid_a)["entries"]
        old = self._draft(f"قديم {self.run}")
        self.db.execute(
            "UPDATE zubair_capture_drafts SET created_at=now()-interval '31 days' WHERE draft_id=?",
            (old["draft_id"],),
        )
        self._draft(f"حالي {self.run}")
        self.assertEqual(baseline + 1, metrics(self.db, self.cid_a)["entries"])

    def test_thirty_day_review_requires_audited_sample_and_keeps_beta_private(self):
        self._draft(f"مراجعة تجربة {self.run}")
        packet = review_packet(self.db, self.cid_a)
        self.assertEqual(30, packet["window_days"])
        self.assertTrue(packet["review_required"])
        self.assertEqual("private-beta-only", packet["rollout"])
        self.assertFalse(packet["automatic_pattern_promotion"])
        self.assertTrue(packet["audit_sample"]["drafts"])

        audit = {
            "drafts": [
                {
                    "draft_id": item["draft_id"],
                    "draft_accuracy": "accurate",
                    "match_accuracy": "accurate",
                    "no_fabrication": "accurate",
                    "recommendation_accuracy": "accurate",
                    "notes": "تمت مطابقة المصدر مع المسودة.",
                }
                for item in packet["audit_sample"]["drafts"]
            ],
            "recommendations": [
                {
                    "opp_id": item["opp_id"],
                    "accuracy": "accurate",
                    "no_fabrication": "accurate",
                    "notes": "التوصية مرتبطة بدليل الفرصة.",
                }
                for item in packet["audit_sample"]["recommendations"]
                if item.get("opp_id")
            ],
        }
        review = save_review(
            self.db, self.cid_a, self.admin_id, "pattern",
            "النتيجة قابلة للاقتراح فقط بعد تدقيق العينة؛ لا تعميم تلقائي.",
            audit,
        )
        self.assertEqual("pattern", review["decision"])
        self.assertEqual("private-beta-only", review["rollout"])
        self.assertFalse(review["access_scope_changed"])
        self.assertEqual(1, self.db.execute(
            """SELECT COUNT(*) AS n FROM zubair_experiment_reviews
               WHERE company_id=?""",
            (self.cid_a,),
        ).fetchone()["n"])

    def test_review_rejects_incomplete_manual_audit(self):
        self._draft(f"مسودة غير مكتملة {self.run}")
        packet = review_packet(self.db, self.cid_a)
        with self.assertRaisesRegex(ValueError, "REVIEW_DRAFT_AUDIT_REQUIRED"):
            save_review(
                self.db, self.cid_a, self.admin_id, "modify",
                "نحتاج تعديلًا بعد مراجعة فعلية.", {
                    "drafts": [
                        {"draft_id": item["draft_id"]}
                        for item in packet["audit_sample"]["drafts"]
                    ],
                    "recommendations": [],
                },
            )

    def test_attention_order_is_deterministic_and_explained(self):
        urgent = f"OPP-ZDB-U-{self.run}"
        later = f"OPP-ZDB-L-{self.run}"
        self.db.execute(
            """INSERT INTO opportunities
               (opp_id,company_id,title,stage,next_action,next_action_due)
               VALUES (?,?,?,'مؤهل','اتصال',?)""",
            (urgent, self.cid_a, f"عاجلة {self.run}", (date.today() - timedelta(days=1)).isoformat()),
        )
        self.db.execute(
            """INSERT INTO opportunities
               (opp_id,company_id,title,stage,next_action,next_action_due)
               VALUES (?,?,?,'مؤهل','انتظار',?)""",
            (later, self.cid_a, f"لاحقة {self.run}", (date.today() + timedelta(days=8)).isoformat()),
        )
        items = [item for item in attention_items(self.db, self.cid_a)
                 if item["opp_id"] in {urgent, later}]
        self.assertEqual([urgent, later], [item["opp_id"] for item in items])
        self.assertEqual("تدخل عاجل", items[0]["category"])
        self.assertTrue(items[0]["rule"])
        self.assertNotIn("probability", items[0]["rule"].lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)