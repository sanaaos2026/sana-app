import unittest
import uuid
import json

import app as sana_app
from sana_scan import run_scan
import weasyprint


class SanaScanReportAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.context = sana_app.app.app_context()
        self.context.push()
        self.db = sana_app.get_db()
        self.db.execute("ALTER TABLE decisions ADD COLUMN IF NOT EXISTS scan_id TEXT")
        self.db.execute("ALTER TABLE decisions ADD COLUMN IF NOT EXISTS evidence_ids TEXT")
        self.db.commit()
        suffix = uuid.uuid4().hex[:8].upper()
        self.company_id = f"RPT{suffix}"
        self.case_id = f"CASE{suffix}"
        self.asset_ids = {
            asset_type: f"{asset_type[:2].upper()}{suffix}"
            for asset_type in ("Knowledge", "Operations", "Brand", "Data", "Independence")
        }
        self.db.execute(
            """INSERT INTO companies
               (company_id,name,sector,city,employee_count,annual_revenue,main_goal)
               VALUES (?,?,?,?,?,?,?)""",
            (self.company_id, "شركة اختبار التقرير", "خدمات B2B", "الرياض", 12, 800000, "نمو منضبط"),
        )
        self.db.execute(
            """INSERT INTO users(user_id,company_id,name,role,status)
               VALUES (?,?,?,?,?)""",
            (f"USR{suffix}", self.company_id, "مالك المبادرة", "Owner", "نشط"),
        )
        for asset_type, asset_id in self.asset_ids.items():
            self.db.execute(
                """INSERT INTO assets
                   (asset_id,company_id,asset_type,asset_name,current_score,fragility_score,status)
                   VALUES (?,?,?,?,?,?,?)""",
                (asset_id, self.company_id, asset_type, asset_type, 40, 50, "تحت المراجعة"),
            )
        self.db.execute(
            """INSERT INTO cases
               (case_id,company_id,case_title,case_type,case_status,declared_problem,
                real_question,related_asset_id,confidence_score)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                self.case_id, self.company_id, "اعتماد التشغيل على المؤسس",
                "تشخيص", "مفتوح", "يتعطل التسليم عند غياب المؤسس",
                "كيف ننقل عملية حرجة إلى الفريق؟",
                self.asset_ids["Independence"], 80,
            ),
        )
        self.db.execute(
            """INSERT INTO evidence
               (evidence_id,company_id,case_id,asset_id,title,source_type,confidence,
                evidence_type,source_ref,information_type,verification_status,
                source_category)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"E1{suffix}", self.company_id, self.case_id,
                self.asset_ids["Independence"],
                "مستوى اعتماد الشركة على المؤسس: الشركة لا تعمل بدوني",
                "اكتشاف_ذاتي", 70, "Evidence", "SDS-001 Q5",
                "Narrative", "UNVERIFIED", "SELF_REPORTED",
            ),
        )
        self.db.execute(
            """INSERT INTO evidence
               (evidence_id,company_id,case_id,asset_id,title,source_type,confidence,
                evidence_type,source_ref,information_type,verification_status,
                source_category)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"E2{suffix}", self.company_id, self.case_id,
                self.asset_ids["Independence"],
                "توقف اعتماد عرض السعر مرتين عند غياب المؤسس",
                "سجل تشغيل", 95, "Fact", "ops-log:test",
                "Narrative", "VERIFIED", "SYSTEM",
            ),
        )
        self.db.execute(
            """INSERT INTO diagnostic_baselines
               (baseline_id,company_id,case_id,baseline_start,baseline_end,
                comparison_start,comparison_end,seasonality_context)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                f"DBL{suffix}", self.company_id, self.case_id,
                "2026-08-01", "2026-08-31",
                "2026-07-01", "2026-07-31", "فترة تشغيل اعتيادية",
            ),
        )
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        for table in (
            "scan_findings", "scan_runs", "tasks", "decisions",
            "evidence_relations", "diagnostic_baselines", "evidence",
            "cases", "assets", "users",
        ):
            self.db.execute(f"DELETE FROM {table} WHERE company_id=?", (self.company_id,))
        self.db.execute("DELETE FROM companies WHERE company_id=?", (self.company_id,))
        self.db.commit()
        self.context.pop()

    def test_report_contains_traceable_diagnosis_and_complete_90_day_fields(self):
        scan = run_scan(self.db, self.case_id)
        self.assertEqual("REVIEW_REQUIRED", scan["status"])
        self.assertFalse(scan["reference_knowledge_used_as_evidence"])
        self.assertEqual("none", scan["reference_knowledge_effect"])
        suffix = self.company_id.removeprefix("RPT")
        decision_id = f"DEC{suffix}"
        evidence_ids = [f"E1{suffix}", f"E2{suffix}"]
        self.db.execute(
            """INSERT INTO decisions
               (decision_id,company_id,case_id,asset_id,title,recommended_action,
                reason,confidence_score,expected_impact,status,owner_name,due_date,
                success_metric,phase_label,scan_id,evidence_ids)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                decision_id, self.company_id, self.case_id,
                self.asset_ids["Independence"], "تفويض اعتماد عرض السعر",
                "نقل اعتماد عروض أقل من حد محدد إلى مدير التشغيل",
                "الدليل يثبت توقف القرار عند غياب المؤسس", 90,
                "رفع نسبة العروض المعتمدة دون تدخل المؤسس من 0 إلى 80",
                "معتمد", "مدير التشغيل", "2026-09-30",
                "نسبة العروض المعتمدة دون تدخل المؤسس", "0-30",
                scan["scan_id"], json.dumps(evidence_ids),
            ),
        )
        self.db.execute(
            """INSERT INTO tasks
               (task_id,company_id,decision_id,title,owner_user_id,due_date,status,priority)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                f"TSK{suffix}", self.company_id, decision_id,
                "توثيق حد التفويض وتجربته", f"USR{suffix}",
                "2026-09-30", "لم تبدأ", "عالية",
            ),
        )
        self.db.commit()

        report = sana_app._build_passport_context(self.company_id)
        self.assertEqual("REVIEW_REQUIRED", report["scan_status"])
        self.assertEqual(3, len(report["scan_phases"]))
        initiative = report["scan_phases"][0]["initiatives"][0]
        self.assertEqual("مدير التشغيل", initiative["owner"])
        self.assertEqual("2026-09-30", initiative["due_date"])
        self.assertTrue(initiative["kpi"])
        self.assertTrue(initiative["evidence"])
        self.assertEqual("0", initiative["baseline"])
        self.assertEqual("80", initiative["target"])
        self.assertTrue(initiative["impact"])
        self.assertTrue(initiative["completeness"])

        html = sana_app.app.jinja_env.get_template(
            "14-passport-report.html"
        ).render(**report)
        for required_text in (
            "تقرير Sana Scan التنفيذي",
            "الاختناق المرشح",
            "ما لا نفعله الآن",
            "خطة التنفيذ — 90 يومًا",
            "0–30 يومًا",
            "31–60 يومًا",
            "61–90 يومًا",
            "نسبة العروض المعتمدة دون تدخل المؤسس",
            "ops-log:test",
            "المعرفة المرجعية المرتبطة",
            "لا تصبح Evidence للعميل",
        ):
            self.assertIn(required_text, html)
        pdf = weasyprint.HTML(string=html, base_url="http://localhost/").write_pdf()
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 10000)

    def test_report_does_not_mix_newer_case_or_unrelated_decisions(self):
        run_scan(self.db, self.case_id)
        suffix = self.company_id.removeprefix("RPT")
        newer_case_id = f"NEW{suffix}"
        self.db.execute(
            """INSERT INTO cases
               (case_id,company_id,case_title,case_status,declared_problem,real_question)
               VALUES (?,?,?,?,?,?)""",
            (
                newer_case_id, self.company_id, "قضية أحدث غير مفحوصة", "مفتوح",
                "مشكلة أحدث", "سؤال أحدث لا يخص التقرير",
            ),
        )
        self.db.execute(
            """INSERT INTO decisions
               (decision_id,company_id,case_id,asset_id,title,expected_impact,status,
                owner_name,due_date,success_metric,phase_label)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"OTHER{suffix}", self.company_id, newer_case_id,
                self.asset_ids["Brand"], "قرار لا يخص Scan",
                "رفع مؤشر من 10 إلى 20", "معتمد", "مالك آخر",
                "2026-10-15", "مؤشر آخر", "0-30",
            ),
        )
        self.db.execute(
            """INSERT INTO decisions
               (decision_id,company_id,case_id,asset_id,title,expected_impact,status,
                owner_name,due_date,success_metric)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                f"NOPHASE{suffix}", self.company_id, self.case_id,
                self.asset_ids["Independence"], "قرار بلا مرحلة",
                "رفع مؤشر من 0 إلى 50", "معتمد", "مدير التشغيل",
                "2026-10-15", "نسبة التفويض",
            ),
        )
        self.db.commit()

        report = sana_app._build_passport_context(self.company_id)
        self.assertEqual(self.case_id, report["scan_case"]["case_id"])
        self.assertEqual(
            [f"NOPHASE{suffix}"],
            [item["decision_id"] for item in report["scan_unplanned_decisions"]],
        )
        report_decision_ids = {
            item["decision_id"] for item in report["scan_initiatives"]
            if item["decision_id"]
        }
        self.assertNotIn(f"OTHER{suffix}", report_decision_ids)
        html = sana_app.app.jinja_env.get_template(
            "14-passport-report.html"
        ).render(**report)
        executive_report = html.split("APPENDIX · BUSINESS PASSPORT", 1)[0]
        self.assertNotIn("قضية أحدث غير مفحوصة", executive_report)
        self.assertNotIn("مشكلة أحدث", executive_report)
        self.assertNotIn("قرار لا يخص Scan", executive_report)
        self.assertIn("قرارات لم تدخل خطة الـ90 يومًا", executive_report)

    def test_company_decisions_are_not_a_scan_plan_before_scan_runs(self):
        suffix = self.company_id.removeprefix("RPT")
        self.db.execute(
            """INSERT INTO decisions
               (decision_id,company_id,case_id,asset_id,title,expected_impact,status,
                owner_name,due_date,success_metric,phase_label)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"PRESCAN{suffix}", self.company_id, self.case_id,
                self.asset_ids["Independence"], "قرار سابق للفحص",
                "رفع مؤشر من 0 إلى 50", "معتمد", "مدير التشغيل",
                "2026-10-15", "نسبة التفويض", "0-30",
            ),
        )
        self.db.commit()

        report = sana_app._build_passport_context(self.company_id)
        self.assertEqual("NOT_RUN", report["scan_status"])
        self.assertEqual([], report["scan_initiatives"])
        self.assertEqual([], report["scan_unplanned_decisions"])

    def test_unified_journey_exposes_explicit_states_and_defers_financial_value(self):
        report = sana_app._build_passport_context(self.company_id)
        self.assertEqual("NOT_RUN", report["scan_status"])
        self.assertEqual("ابدأ تقييم الأصول", report["scan_journey"]["action_label"])
        self.assertIsNone(report["current_value"])
        self.assertIsNone(report["potential_value"])

        expected = {
            "INCOMPLETE": "أكمل الأدلة المطلوبة",
            "REVIEW_REQUIRED": "راجع التقرير التنفيذي",
            "COMPLETE": "افتح التقرير التنفيذي",
        }
        for index, (status, action) in enumerate(expected.items()):
            scan_id = f"STATE{index}{self.company_id.removeprefix('RPT')}"
            self.db.execute(
                """INSERT INTO scan_runs
                   (scan_id,case_id,company_id,status,result,methodology_version)
                   VALUES (?,?,?,?,?,?)""",
                (
                    scan_id,
                    self.case_id,
                    self.company_id,
                    status,
                    json.dumps({
                        "status": status,
                        "case_id": self.case_id,
                        "asset_scores": [],
                        "missing_evidence": [],
                    }),
                    "test",
                ),
            )
            self.db.commit()
            report = sana_app._build_passport_context(self.company_id)
            self.assertEqual(status, report["scan_status"])
            self.assertEqual(action, report["scan_journey"]["action_label"])
            self.assertTrue(report["scan_journey"]["label"])
            self.assertTrue(report["scan_journey"]["message"])
            self.assertIn("Sana Score", sana_app.app.jinja_env.get_template(
                "14-passport-report.html"
            ).render(**report))
            self.db.execute("DELETE FROM scan_runs WHERE scan_id=?", (scan_id,))
            self.db.commit()

    def test_decisions_do_not_inherit_evidence_and_snapshot_type_is_enforced(self):
        scan = run_scan(self.db, self.case_id)
        suffix = self.company_id.removeprefix("RPT")
        for decision_id, evidence_ids in (
            (f"LINKED{suffix}", [f"E1{suffix}"]),
            (f"UNLINKED{suffix}", []),
            (f"NONFACT{suffix}", [f"E2{suffix}"]),
        ):
            self.db.execute(
                """INSERT INTO decisions
                   (decision_id,company_id,case_id,asset_id,title,expected_impact,status,
                    owner_name,due_date,success_metric,phase_label,scan_id,evidence_ids)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    decision_id, self.company_id, self.case_id,
                    self.asset_ids["Independence"], decision_id,
                    "رفع مؤشر من 0 إلى 50", "معتمد", "مدير التشغيل",
                    "2026-10-15", "نسبة التفويض", "0-30",
                    scan["scan_id"], json.dumps(evidence_ids),
                ),
            )
        row = self.db.execute(
            "SELECT result FROM scan_runs WHERE scan_id=?", (scan["scan_id"],)
        ).fetchone()
        snapshot = json.loads(row["result"])
        for source in snapshot["classified_inputs"]:
            if source["source_id"] == f"E2{suffix}":
                source["classification"] = "Hypothesis"
        self.db.execute(
            "UPDATE scan_runs SET result=? WHERE scan_id=?",
            (json.dumps(snapshot, ensure_ascii=False), scan["scan_id"]),
        )
        self.db.commit()

        report = sana_app._build_passport_context(self.company_id)
        initiatives = {
            item["decision_id"]: item for item in report["scan_initiatives"]
        }
        self.assertTrue(initiatives[f"LINKED{suffix}"]["evidence"])
        self.assertTrue(initiatives[f"LINKED{suffix}"]["completeness"])
        self.assertEqual([], initiatives[f"UNLINKED{suffix}"]["evidence"])
        self.assertFalse(initiatives[f"UNLINKED{suffix}"]["completeness"])
        self.assertEqual([], initiatives[f"NONFACT{suffix}"]["evidence"])
        self.assertFalse(initiatives[f"NONFACT{suffix}"]["completeness"])


if __name__ == "__main__":
    unittest.main()