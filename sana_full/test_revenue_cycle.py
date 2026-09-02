"""اختبار قبول دورة الإيراد والتعلّم التجاري."""
import os
import uuid
import unittest
import psycopg2
import psycopg2.extras

from app import _PGConn
from sana_revenue_cycle import (
    ensure_schema, record_transition, upsert_economics, create_invoice,
    record_payment, upsert_learning, opportunity_context, report,
)


class RevenueCycleAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = psycopg2.connect(os.environ["DATABASE_URL"])
        raw.autocommit = False
        cls.raw = raw
        cls.db = _PGConn(raw)
        ensure_schema(cls.db)
        cls.db.commit()
        cls.cid_a, cls.cid_b = "C001", "C002"

    @classmethod
    def tearDownClass(cls):
        cls.raw.close()

    def setUp(self):
        token = uuid.uuid4().hex[:10].upper()
        self.opp_a = f"OPP-RC-{token}"
        self.opp_b = f"OPP-RB-{token}"
        self.db.execute(
            """INSERT INTO opportunities
               (opp_id,company_id,title,stage,revenue_stage_id,amount,owner_id)
               VALUES (?,?,?,?,?,?,?)""",
            (self.opp_a, self.cid_a, "اختبار دورة الإيراد", "عميل محتمل",
             "lead", 100000, "owner-a"),
        )
        self.db.execute(
            """INSERT INTO opportunities
               (opp_id,company_id,title,stage,revenue_stage_id,amount,owner_id)
               VALUES (?,?,?,?,?,?,?)""",
            (self.opp_b, self.cid_b, "فرصة معزولة", "عميل محتمل",
             "lead", 900000, "owner-b"),
        )
        self.db.commit()

    def tearDown(self):
        for table in (
            "rc_deal_learning", "rc_invoices", "rc_projects",
            "rc_opportunity_economics", "rc_stage_history",
        ):
            self.db.execute(
                f"DELETE FROM {table} WHERE opp_id IN (?,?)",
                (self.opp_a, self.opp_b),
            )
        self.db.execute(
            "DELETE FROM gos_canonical_entities WHERE source_id IN (?,?)",
            (self.opp_a, self.opp_b),
        )
        self.db.execute(
            "DELETE FROM opportunities WHERE opp_id IN (?,?)",
            (self.opp_a, self.opp_b),
        )
        self.db.commit()

    def test_full_cycle_is_sourced_isolated_and_idempotent(self):
        for stage in (
            "qualification", "discovery", "opportunity", "proposal",
            "negotiation", "won",
        ):
            record_transition(
                self.db, company_id=self.cid_a, opp_id=self.opp_a,
                to_stage_id=stage, owner_id="owner-a",
                reason="تم الاتفاق" if stage == "won" else None,
                source_ref="acceptance-test",
            )
        # تكرار الفوز لا ينشئ مشروعًا أو انتقالًا ثانيًا.
        duplicate = record_transition(
            self.db, company_id=self.cid_a, opp_id=self.opp_a,
            to_stage_id="won", owner_id="owner-a", source_ref="acceptance-test",
        )
        self.assertFalse(duplicate["transitioned"])
        projects = self.db.execute(
            "SELECT COUNT(*) AS n FROM rc_projects WHERE opp_id=?",
            (self.opp_a,),
        ).fetchone()["n"]
        self.assertEqual(projects, 1)
        delivery_task_id = self.db.execute(
            "SELECT delivery_task_id FROM opportunities WHERE opp_id=?",
            (self.opp_a,),
        ).fetchone()["delivery_task_id"]
        self.assertIsNotNone(delivery_task_id)

        upsert_economics(self.db, self.cid_a, self.opp_a, {
            "service": "Sana Scan",
            "channel": "إحالة",
            "sector": "خدمات مهنية",
            "customer_problem": "تسرب في التحويل",
            "customer_language": "العروض لا تغلق",
            "decision_maker": "المالك",
            "objection": "مدة التنفيذ",
            "winning_offer": "تشخيص مع خطة 90 يومًا",
            "contract_value": 100000,
            "recognized_revenue": 100000,
            "delivery_cost": 40000,
            "acquisition_cost": 10000,
            "closed_at": "2026-09-01T10:00:00+03:00",
            "source_ref": "contract:SANA-TEST",
            "observed_at": "2026-09-01",
            "classification": "Fact",
        })
        invoice = create_invoice(self.db, self.cid_a, self.opp_a, {
            "external_invoice_id": f"EXT-{self.opp_a}",
            "amount_due": 100000,
            "amount_paid": 0,
            "issued_at": "2026-07-01",
            "due_at": "2026-07-31",
            "source_ref": "invoice:SANA-TEST",
            "classification": "Fact",
        })
        record_payment(self.db, self.cid_a, invoice["invoice_id"], {
            "amount_paid": 50000,
            "source_ref": "payment:SANA-TEST",
        })
        upsert_learning(self.db, self.cid_a, self.opp_a, {
            "outcome": "won",
            "outcome_reason": "ربط العرض بالأثر المالي",
            "objection": "مدة التنفيذ",
            "customer_problem": "تسرب في التحويل",
            "customer_language": "العروض لا تغلق",
            "winning_offer": "تشخيص مع خطة 90 يومًا",
            "sector": "خدمات مهنية",
            "decision_maker": "المالك",
            "evidence_ids": ["contract:SANA-TEST", "meeting:SANA-TEST"],
            "source_ref": "review:SANA-TEST",
            "review_status": "approved",
        })
        self.db.commit()

        data_a = report(self.db, self.cid_a)
        ids_a = {row["opp_id"] for row in data_a["opportunities"]}
        self.assertIn(self.opp_a, ids_a)
        self.assertNotIn(self.opp_b, ids_a)
        self.assertEqual(data_a["metrics"]["revenue"], 100000.0)
        self.assertEqual(data_a["metrics"]["gross_margin"], 60.0)
        self.assertEqual(data_a["metrics"]["collection_rate"], 50.0)
        self.assertEqual(data_a["metrics"]["actual_profit"], 50000.0)
        self.assertGreaterEqual(data_a["overdue_invoices"], 1)
        self.assertTrue(any(
            row["opp_id"] == self.opp_a for row in data_a["approved_learning"]
        ))
        self.assertEqual(len(data_a["stage_catalog"]["stages"]), 11)

        ctx = opportunity_context(self.db, self.cid_a, self.opp_a)
        self.assertEqual(len(ctx["invoices"]), 1)
        self.assertEqual(ctx["learning"]["review_status"], "approved")

        with self.assertRaises(ValueError):
            record_transition(
                self.db, company_id=self.cid_b, opp_id=self.opp_a,
                to_stage_id="delivery", owner_id="owner-b",
                source_ref="cross-tenant-test",
            )
        self.db.rollback()

    def test_missing_evidence_stays_deferred(self):
        data = report(self.db, self.cid_a, {"owner": "owner-a"})
        target = next(row for row in data["opportunities"]
                      if row["opp_id"] == self.opp_a)
        self.assertIsNone(target["recognized_revenue"])
        self.assertIsNone(data["metrics"]["revenue"])
        self.assertIn("N/A", data["metrics"]["source_policy"])


if __name__ == "__main__":
    unittest.main(verbosity=2)