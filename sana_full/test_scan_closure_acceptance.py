import json
import unittest
import uuid

import app as sana_app
from sana_scan import run_scan


class ScanClosureAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sana_app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        sana_app.init_db()

    def setUp(self):
        self.ctx = sana_app.app.app_context()
        self.ctx.push()
        self.db = sana_app.get_db()
        suffix = uuid.uuid4().hex[:8].upper()
        self.company_id = f"CLS{suffix}"
        self.case_id = f"CLC{suffix}"
        self.account_id = f"CLA{suffix}"
        self.asset_ids = {
            kind: f"{kind[:2].upper()}{suffix}"
            for kind in ("Knowledge", "Operations", "Brand", "Data", "Independence")
        }
        self.db.execute(
            """INSERT INTO companies
               (company_id,name,sector,signup_code,sds_done,main_goal)
               VALUES (?,?,?,?,?,?)""",
            (self.company_id, "شركة قبول الإغلاق", "خدمات", f"T-{suffix}", 0, "نمو منضبط"),
        )
        self.db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id,admin_role,account_status)
               VALUES (?,?,?,?,?,?)""",
            (self.account_id, f"{self.account_id.lower()}@test.local", "x",
             self.company_id, "USER", "active"),
        )
        for kind, asset_id in self.asset_ids.items():
            self.db.execute(
                """INSERT INTO assets
                   (asset_id,company_id,asset_type,asset_name,current_score,fragility_score,status)
                   VALUES (?,?,?,?,?,?,?)""",
                (asset_id, self.company_id, kind, kind, 40, 50, "تحت المراجعة"),
            )
        self.db.execute(
            """INSERT INTO cases
               (case_id,company_id,case_title,case_type,case_status,declared_problem,
                real_question,related_asset_id,confidence_score)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (self.case_id, self.company_id, "اعتماد التشغيل على المؤسس", "تشخيص",
             "مفتوح", "يتعطل التسليم عند غياب المؤسس",
             "كيف ننقل عملية حرجة إلى الفريق؟", self.asset_ids["Independence"], 80),
        )
        self.db.execute(
            """INSERT INTO evidence
               (evidence_id,company_id,case_id,asset_id,title,source_type,confidence,
                evidence_type,source_ref,information_type,verification_status,source_category)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"E1{suffix}", self.company_id, self.case_id, self.asset_ids["Independence"],
             "الشركة تتعطل عند غياب المؤسس", "اكتشاف_ذاتي", 60, "Evidence",
             "SDS-001 Q5", "Narrative", "UNVERIFIED", "SELF_REPORTED"),
        )
        self.db.execute(
            """INSERT INTO diagnostic_baselines
               (baseline_id,company_id,case_id,baseline_start,baseline_end,
                comparison_start,comparison_end,seasonality_context)
               VALUES (?,?,?,?,?,?,?,?)""",
            (f"DB{suffix}", self.company_id, self.case_id, "2026-08-01", "2026-08-31",
             "2026-07-01", "2026-07-31", "فترة اعتيادية"),
        )
        self.db.commit()
        self.client = sana_app.app.test_client()
        with self.client.session_transaction() as session:
            session.update(account_id=self.account_id, company_id=self.company_id,
                           email=f"{self.account_id.lower()}@test.local")

    def tearDown(self):
        self.db.rollback()
        for table in (
            "leads", "company_memory_links", "company_memory_versions",
            "company_memory_items", "scan_findings", "scan_runs", "tasks",
            "decisions", "evidence_relations", "diagnostic_baselines",
            "evidence", "cases", "assets", "user_accounts",
        ):
            self.db.execute(f"DELETE FROM {table} WHERE company_id=?", (self.company_id,))
        self.db.execute("DELETE FROM companies WHERE company_id=?", (self.company_id,))
        self.db.commit()
        self.ctx.pop()

    def _add_verified_fact(self):
        suffix = self.company_id.removeprefix("CLS")
        self.db.execute(
            """INSERT INTO evidence
               (evidence_id,company_id,case_id,asset_id,title,source_type,confidence,
                evidence_type,source_ref,information_type,verification_status,source_category)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"E2{suffix}", self.company_id, self.case_id, self.asset_ids["Independence"],
             "تعطل اعتماد عرضين عند غياب المؤسس", "سجل تشغيل", 95, "Fact",
             "ops-log:test", "Narrative", "VERIFIED", "SYSTEM"),
        )
        self.db.commit()

    def test_incomplete_company_closes_gap_without_repeat_and_report_refreshes(self):
        first = run_scan(self.db, self.case_id)
        question = sana_app._next_sds_question(self.db, self.company_id, self.case_id)
        self.assertEqual("SDS-002:Independence", question["question_id"])
        suffix = self.company_id.removeprefix("CLS")
        self.db.execute(
            """INSERT INTO evidence
               (evidence_id,company_id,case_id,asset_id,title,source_type,confidence,
                evidence_type,source_ref,information_type,verification_status,source_category)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"EA{suffix}", self.company_id, self.case_id, self.asset_ids["Independence"],
             "يغطي مدير التشغيل الاعتماد أثناء الغياب", "SDS-002 تشخيص تراكمي", 50,
             "Evidence", "SDS-002:Independence", "Narrative", "UNVERIFIED", "SELF_REPORTED"),
        )
        self.db.commit()
        second = run_scan(self.db, self.case_id)
        self.assertIsNone(sana_app._next_sds_question(self.db, self.company_id, self.case_id))
        self.assertNotEqual(first["scan_id"], second["scan_id"])
        self.assertEqual(second["scan_id"], sana_app._build_passport_context(self.company_id)["scan"]["scan_id"])

    def test_self_report_does_not_raise_asset_score(self):
        before = run_scan(self.db, self.case_id)
        before_score = next(x for x in before["asset_scores"] if x["asset_type"] == "Independence")["score"]
        suffix = self.company_id.removeprefix("CLS")
        self.db.execute(
            """INSERT INTO evidence
               (evidence_id,company_id,case_id,asset_id,title,source_type,confidence,
                evidence_type,source_ref,information_type,verification_status,source_category)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"ES{suffix}", self.company_id, self.case_id, self.asset_ids["Independence"],
             "إجابة ذاتية إضافية", "SDS-002 تشخيص تراكمي", 50, "Evidence",
             "SDS-002:Independence", "Narrative", "UNVERIFIED", "SELF_REPORTED"),
        )
        self.db.commit()
        after = run_scan(self.db, self.case_id)
        after_score = next(x for x in after["asset_scores"] if x["asset_type"] == "Independence")["score"]
        self.assertEqual(before_score, after_score)

    def test_ready_report_has_ranked_decisions_and_smart_swot(self):
        self._add_verified_fact()
        scan = run_scan(self.db, self.case_id)
        snapshot = json.loads(self.db.execute(
            "SELECT result FROM scan_runs WHERE scan_id=?", (scan["scan_id"],)
        ).fetchone()["result"])
        snapshot["diagnostic_quality"]["evidence_strength"] = "STRONG"
        snapshot["diagnostic_quality"]["data_reliability"] = "HIGH"
        snapshot["status"] = "REVIEW_REQUIRED"
        snapshot["decision_readiness"] = "CONDITIONAL"
        self.db.execute("UPDATE scan_runs SET result=? WHERE scan_id=?",
                        (json.dumps(snapshot, ensure_ascii=False), scan["scan_id"]))
        suffix = self.company_id.removeprefix("CLS")
        for index in range(6):
            self.db.execute(
                """INSERT INTO decisions
                   (decision_id,company_id,case_id,title,recommended_action,reason,
                    confidence_score,expected_impact,status,owner_name,due_date,
                    success_metric,scan_id,evidence_ids)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f"D{index}{suffix}", self.company_id, self.case_id, f"قرار {index+1}",
                 f"نفّذ الإجراء {index+1}", "مرتبط بالاختناق المثبت", 90-index,
                 "تحسن تشغيلي قابل للقياس", "معتمد", "مدير التشغيل", "2026-10-01",
                 "مؤشر نجاح", scan["scan_id"], json.dumps([f"E2{suffix}"])),
            )
        self.db.commit()
        context = sana_app._build_passport_context(self.company_id)
        self.assertEqual("EXPANDED", context["scan_report_tier"])
        self.assertEqual(5, len(context["scan_report_decisions"]))
        self.assertTrue(any(context["scan_swot"].values()))

    def test_details_always_has_exit_and_local_actions(self):
        self._add_verified_fact()
        run_scan(self.db, self.case_id)
        page = self.client.get(f"/company/{self.company_id}/scan-report/details")
        html = page.get_data(as_text=True)
        self.assertEqual(200, page.status_code)
        self.assertIn("العودة للملخص", html)
        self.assertIn("تابع الخطوة التالية", html)
        self.assertIn(f"/case/{self.case_id}#evidenceSection", html)

    def test_idea_fails_fit_gate_and_can_register_interest(self):
        check = self.client.post("/api/fit-gate/check", json={"fit_gate": {
            "operating_duration": "IDEA", "paying_customers": "NO", "delivery_mode": "BUILDING",
        }})
        self.assertFalse(check.get_json()["data"]["qualified"])
        page = self.client.get("/fit-gate/build-launch")
        page_text = page.get_data(as_text=True)
        self.assertIn("مشروعك ليس مرفوضًا", page_text)
        self.assertIn("الأنسب الآن: ابنِ خدمتك وانطلق", page_text)
        self.assertIn("حوّل فكرتك إلى عرض واضح", page_text)
        self.assertIn("رقم الجوال (اختياري)", page_text)
        self.assertIn("أرغب أن تتواصلوا معي", page_text)
        self.assertIn('href="/home"', page_text)
        self.assertNotIn("الأنسب الآن: Build &amp; Launch", page_text)
        saved = self.client.post("/api/fit-gate/interest", json={"phone": "+966 50 123 4567"})
        self.assertIn(saved.status_code, (200, 201))
        row = self.db.execute(
            "SELECT service_interest FROM leads WHERE company_id=?", (self.company_id,)
        ).fetchone()
        self.assertEqual("Build & Launch", row["service_interest"])

    def test_operating_company_passes_fit_gate_and_can_start_assessment(self):
        check = self.client.post("/api/fit-gate/check", json={"fit_gate": {
            "operating_duration": "ONE_PLUS",
            "paying_customers": "YES",
            "delivery_mode": "TEAM_DELIVERY",
        }})
        payload = check.get_json()
        self.assertEqual(200, check.status_code)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["data"]["qualified"])
        self.assertIsNone(payload["data"]["redirect"])


if __name__ == "__main__":
    unittest.main()