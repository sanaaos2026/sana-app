"""اختبار قبول P0 لمسار Diagnostic Review → Impact Review."""

import json
import unittest
import uuid

import app as sana_app
import psycopg2
from sana_decision_room import ensure_schema as ensure_decision_room_schema


class P0ClosureAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sana_app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        try:
            sana_app.init_db()
        except psycopg2.errors.LockNotAvailable:
            # A concurrently starting app may own the broad compatibility-DDL lock.
            # This acceptance test only needs the already-initialized core schema
            # plus the narrowly scoped decision-room additions.
            db = sana_app._connect_pg()
            try:
                db.rollback()
                db.execute("SELECT 1 FROM companies LIMIT 1")
                ensure_decision_room_schema(db)
                db.commit()
            finally:
                db.close()

    def setUp(self):
        self.db = sana_app._connect_pg()
        suffix = uuid.uuid4().hex[:10].upper()
        self.company_id = f"P0C{suffix}"
        self.account_id = f"ACCP0{suffix}"
        self.asset_id = f"ASP0{suffix}"
        self.case_id = f"CASEP0{suffix}"
        self.evidence_id = f"EVP0{suffix}"
        self.scan_id = f"SCANP0{suffix}"

        self.db.execute(
            """INSERT INTO companies (company_id,name,sector,signup_code)
               VALUES (?,?,?,?)""",
            (self.company_id, "شركة اختبار إغلاق P0", "خدمات B2B",
             f"TEST-{self.company_id}"),
        )
        self.db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id,admin_role,account_status)
               VALUES (?,?,?,?,?,?)""",
            (self.account_id, f"{self.account_id.lower()}@test.local",
             "not-used", self.company_id, "USER", "active"),
        )
        self.db.execute(
            """INSERT INTO assets
               (asset_id,company_id,asset_type,asset_name,current_score,fragility_score,status)
               VALUES (?,?,?,?,?,?,?)""",
            (self.asset_id, self.company_id, "Operations", "التشغيل", 40, 60,
             "تحت المراجعة"),
        )
        self.db.execute(
            """INSERT INTO cases
               (case_id,company_id,case_title,case_type,case_status,declared_problem,
                real_question,related_asset_id,confidence_score)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (self.case_id, self.company_id, "تأخر التسليم", "تشخيص", "Open",
             "تتأخر المهام الحرجة", "كيف نغلق نقطة التعطل؟",
             self.asset_id, 80),
        )
        self.db.execute(
            """INSERT INTO evidence
               (evidence_id,company_id,case_id,asset_id,title,source_type,confidence,
                evidence_type,source_ref,information_type,verification_status,
                source_category)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (self.evidence_id, self.company_id, self.case_id, self.asset_id,
             "سجل تسليم موثق", "Operational Record", 90, "Evidence",
             "TEST:P0:EVIDENCE", "Metric", "VERIFIED", "INTERNAL"),
        )
        scan = {
            "status": "REVIEW_REQUIRED",
            "human_review_required": True,
            "classified_inputs": [{
                "source_id": self.evidence_id,
                "classification": "Evidence",
                "statement": "سجل تسليم موثق",
            }],
            "bottleneck": {
                "statement": "غياب مالك واضح لخطوة التسليم",
                "asset_type": "Operations",
            },
            "proposed_decision": {
                "title": "تعيين مالك واحد للتسليم",
                "statement": "تعيين مالك ومسار تحقق أسبوعي",
                "source_ids": [self.evidence_id],
            },
            "opportunity": {"statement": "تقليل تأخر التسليم"},
            "diagnostic_baseline": {
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
            },
        }
        self.db.execute(
            """INSERT INTO scan_runs
               (scan_id,case_id,company_id,status,result,methodology_version)
               VALUES (?,?,?,?,?,?)""",
            (self.scan_id, self.case_id, self.company_id, "REVIEW_REQUIRED",
             json.dumps(scan, ensure_ascii=False), "P0-TEST"),
        )
        self.db.commit()

        self.client = sana_app.app.test_client()
        with self.client.session_transaction() as session:
            session["account_id"] = self.account_id
            session["company_id"] = self.company_id
            session["email"] = f"{self.account_id.lower()}@test.local"

    def tearDown(self):
        self.db.rollback()
        decision_rows = self.db.execute(
            "SELECT decision_id FROM decisions WHERE company_id=?", (self.company_id,)
        ).fetchall()
        decision_ids = [row["decision_id"] for row in decision_rows]
        self.db.execute(
            "DELETE FROM p0_impact_reviews WHERE company_id=?", (self.company_id,)
        )
        self.db.execute("DELETE FROM tasks WHERE company_id=?", (self.company_id,))
        if decision_ids:
            placeholders = ",".join("?" for _ in decision_ids)
            self.db.execute(
                f"DELETE FROM decision_asset_impacts WHERE decision_id IN ({placeholders})",
                decision_ids,
            )
        self.db.execute("DELETE FROM decisions WHERE company_id=?", (self.company_id,))
        self.db.execute("DELETE FROM scan_findings WHERE company_id=?", (self.company_id,))
        self.db.execute("DELETE FROM scan_runs WHERE company_id=?", (self.company_id,))
        self.db.execute("DELETE FROM evidence WHERE company_id=?", (self.company_id,))
        self.db.execute("DELETE FROM cases WHERE company_id=?", (self.company_id,))
        self.db.execute("DELETE FROM assets WHERE company_id=?", (self.company_id,))
        self.db.execute("DELETE FROM user_accounts WHERE company_id=?", (self.company_id,))
        self.db.execute("DELETE FROM companies WHERE company_id=?", (self.company_id,))
        self.db.commit()
        self.db.close()

    def test_complete_p0_decision_task_result_and_impact_review(self):
        review = self.client.post(f"/api/cases/{self.case_id}/p0-decision")
        self.assertEqual(201, review.status_code, review.get_data(as_text=True))
        decision = review.get_json()["data"]
        self.assertEqual("P0", decision["phase_label"])
        self.assertEqual(self.scan_id, decision["scan_id"])
        self.assertEqual(self.company_id, decision["company_id"])
        self.assertEqual(self.case_id, decision["case_id"])
        self.assertEqual(
            "COMPLETE",
            self.db.execute(
                "SELECT status FROM scan_runs WHERE scan_id=?", (self.scan_id,)
            ).fetchone()["status"],
        )

        approval = self.client.post(
            f"/api/decisions/{decision['decision_id']}/approve",
            json={
                "owner_name": "مالك التشغيل",
                "due_date": "2026-12-31",
                "success_metric": "انخفاض المهام المتأخرة",
                "next_action": "مراجعة قائمة التسليم أسبوعيًا",
            },
        )
        self.assertEqual(200, approval.status_code, approval.get_data(as_text=True))
        task_id = approval.get_json()["data"]["task_id"]
        task = self.db.execute(
            """SELECT t.company_id,d.case_id,t.decision_id
               FROM tasks t JOIN decisions d ON d.decision_id=t.decision_id
               WHERE t.task_id=?""",
            (task_id,),
        ).fetchone()
        self.assertEqual(self.company_id, task["company_id"])
        self.assertEqual(self.case_id, task["case_id"])
        self.assertEqual(decision["decision_id"], task["decision_id"])
        client_workspace = self.client.get(f"/case/{self.case_id}")
        self.assertEqual(200, client_workspace.status_code)
        client_html = client_workspace.get_data(as_text=True)
        self.assertIn("submitClientApproval", client_html)
        self.assertIn("openClientImpact", client_html)
        self.assertIn("baseline_value", client_html)
        self.assertIn("/p0-result", client_html)

        invalid_order = self.client.post(
            f"/api/tasks/{task_id}/p0-result",
            json={
                "baseline_value": "12 مهمة", "baseline_numeric": 12,
                "baseline_source_ref": "BASE",
                "baseline_evidence_id": self.evidence_id,
                "baseline_observed_at": "2026-08-31",
                "target_value": "6 مهام", "target_numeric": 6,
                "target_source_ref": "TARGET",
                "target_observed_at": "2026-12-31",
                "actual_value": "5 مهام", "actual_numeric": 5,
                "actual_source_ref": "ACTUAL",
                "actual_evidence_id": self.evidence_id,
                "actual_observed_at": "2026-07-31",
                "result_summary": "نتيجة", "result_source_ref": "RESULT",
                "impact_outcome": "WORSE",
                "measurement_unit": "مهمة",
                "kpi_direction": "LOWER_IS_BETTER",
                "impact_notes": "مقارنة موثقة لكن ترتيب التاريخ غير صالح.",
            },
        )
        self.assertEqual(400, invalid_order.status_code)
        self.assertEqual(
            "P0_IMPACT_MEASURE_ORDER_INVALID",
            invalid_order.get_json()["error"],
        )
        self.assertIsNone(self.db.execute(
            "SELECT review_id FROM p0_impact_reviews WHERE task_id=?", (task_id,)
        ).fetchone())

        result = self.client.post(
            f"/api/tasks/{task_id}/p0-result",
            json={
                "baseline_value": "12 مهمة متأخرة",
                "baseline_numeric": 12,
                "baseline_source_ref": "TEST:P0:BASELINE",
                "baseline_evidence_id": self.evidence_id,
                "baseline_observed_at": "2026-08-31",
                "target_value": "6 مهام متأخرة",
                "target_numeric": 6,
                "target_source_ref": "TEST:P0:APPROVED_TARGET",
                "target_observed_at": "2026-12-31",
                "actual_value": "5 مهام متأخرة",
                "actual_numeric": 5,
                "actual_source_ref": "TEST:P0:ACTUAL",
                "actual_evidence_id": self.evidence_id,
                "actual_observed_at": "2026-12-31",
                "result_summary": "تم تعيين المالك وتطبيق المراجعة الأسبوعية.",
                "result_source_ref": "TEST:P0:RESULT",
                "impact_outcome": "WORSE",
                "measurement_unit": "مهمة",
                "kpi_direction": "LOWER_IS_BETTER",
                "impact_notes": "انخفض عدد المهام المتأخرة في فترة المقارنة.",
            },
        )
        self.assertEqual(201, result.status_code, result.get_data(as_text=True))
        self.assertFalse(result.get_json()["data"]["asset_scores_changed"])

        task = self.db.execute(
            "SELECT status,completed_at FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        self.assertEqual("منجزة", task["status"])
        self.assertIsNotNone(task["completed_at"])
        impact = self.db.execute(
            """SELECT company_id,case_id,decision_id,task_id,
                      baseline_value,baseline_source_ref,baseline_observed_at,
                      target_value,target_source_ref,target_observed_at,
                      actual_value,actual_source_ref,actual_observed_at,
                      baseline_numeric,target_numeric,actual_numeric,
                      measurement_unit,kpi_direction,
                      impact_outcome,result_source_ref FROM p0_impact_reviews
               WHERE task_id=?""",
            (task_id,),
        ).fetchone()
        self.assertEqual(self.company_id, impact["company_id"])
        self.assertEqual(self.case_id, impact["case_id"])
        self.assertEqual(decision["decision_id"], impact["decision_id"])
        self.assertEqual(task_id, impact["task_id"])
        self.assertEqual("IMPROVED", impact["impact_outcome"])
        self.assertEqual("TEST:P0:EVIDENCE", impact["result_source_ref"])
        self.assertEqual("12 مهمة", impact["baseline_value"])
        self.assertEqual("TEST:P0:EVIDENCE", impact["baseline_source_ref"])
        self.assertEqual("6 مهمة", impact["target_value"])
        self.assertEqual(f"DECISION:{decision['decision_id']}", impact["target_source_ref"])
        self.assertEqual("5 مهمة", impact["actual_value"])
        self.assertEqual("TEST:P0:EVIDENCE", impact["actual_source_ref"])
        self.assertEqual(12, float(impact["baseline_numeric"]))
        self.assertEqual(6, float(impact["target_numeric"]))
        self.assertEqual(5, float(impact["actual_numeric"]))
        self.assertEqual("مهمة", impact["measurement_unit"])
        self.assertEqual("LOWER_IS_BETTER", impact["kpi_direction"])


if __name__ == "__main__":
    unittest.main()