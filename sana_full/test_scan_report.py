import unittest
import uuid
import json
from datetime import date

import app as sana_app
from sana_scan import run_scan
import weasyprint
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
from unittest.mock import patch


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
        review = report["diagnostic_review"]
        self.assertEqual(scan["scan_id"], review["snapshot"]["scan_id"])
        self.assertEqual(self.case_id, review["snapshot"]["case_id"])
        self.assertEqual("SANA-DIAGNOSTIC-REVIEW-v1", review["contract_version"])
        self.assertTrue(all(source.get("source_type") for source in review["sources"]))
        self.assertTrue(all(source.get("source_date") for source in review["client_evidence"]))
        self.assertTrue(review["reference_knowledge"]["does_not_affect_scan"])

        template = sana_app.app.jinja_env.get_template("14-passport-report.html")
        main_html = template.render(**report)
        plan_html = template.render(**{**report, "report_view": "plan"})
        details_html = template.render(
            **{**report, "report_view": "details", "scan_report_tier": "DIAGNOSTIC"}
        )
        html = "\n".join((main_html, plan_html, details_html))
        for required_text in (
            "تقرير Sana Scan التنفيذي",
            "ما الشيء الأهم الذي يحتاج انتباهك أولًا؟",
            "نسبة العروض المعتمدة دون تدخل المؤسس",
        ):
            self.assertIn(required_text, main_html)
        for required_text in (
            "خطة التنفيذ — 90 يومًا",
            "إصلاح الاختناق الحرج",
        ):
            self.assertIn(required_text, plan_html)
        for required_text in (
            "جودة الصورة التشخيصية",
            "التشخيص: دليل أم فرضية؟",
            "الأدلة ومصادرها",
            "المؤشر التشغيلي",
            "معلومات عامة ذات صلة",
        ):
            self.assertIn(required_text, details_html)
        for internal_asset_name in (
            "Knowledge",
            "Operations",
            "Brand",
            "Data",
            "Independence",
        ):
            self.assertNotIn(internal_asset_name, html)
        for private_text in (
            "SELF_REPORTED",
            "UNVERIFIED",
            "ops-log:test",
            "Evidence",
            "Fact",
        ):
            self.assertNotIn(private_text, html)
        pdf = weasyprint.HTML(string=html, base_url="http://localhost/").write_pdf()
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 10000)
        self.assertGreaterEqual(len(weasyprint.HTML(
            string=html, base_url="http://localhost/"
        ).render().pages), 1)

    def test_asset_map_keeps_a_real_zero_score_while_translating_names(self):
        report = sana_app._build_passport_context(self.company_id)
        report["scan"]["asset_scores"] = [
            sana_app._client_asset_view({
                "asset_type": asset_type,
                "asset_name": asset_type,
                "score": score,
                "status": "COMPLETE",
            })
            for asset_type, score in (
                ("Knowledge", 0),
                ("Operations", 42),
                ("Brand", 100),
            )
        ]

        html = sana_app.app.jinja_env.get_template(
            "14-passport-report.html"
        ).render(**report)

        self.assertIn("المعرفة", html)
        self.assertIn("التشغيل", html)
        self.assertIn("البراند", html)
        self.assertIn("0/100", html)
        self.assertIn("42/100", html)
        self.assertIn("100/100", html)
        for internal_asset_name in ("Knowledge", "Operations", "Brand"):
            self.assertNotIn(internal_asset_name, html)

    def test_all_client_asset_outputs_translate_database_names_without_changing_scores(self):
        scores = {
            "Knowledge": 0,
            "Operations": 42,
            "Brand": 100,
            "Data": 7,
            "Independence": 88,
        }
        for asset_type, score in scores.items():
            self.db.execute(
                """UPDATE assets SET asset_name=?, current_score=?
                   WHERE company_id=? AND asset_type=?""",
                (asset_type, score, self.company_id, asset_type),
            )
        scan_result = {
            "status": "COMPLETE",
            "case_id": self.case_id,
            "asset_scores": [
                {
                    "asset_type": asset_type,
                    "asset_name": asset_type,
                    "score": score,
                    "status": "COMPLETE",
                }
                for asset_type, score in scores.items()
            ],
            "findings": [],
            "missing_evidence": [],
        }
        self.db.execute(
            """INSERT INTO scan_runs
               (scan_id,case_id,company_id,status,result,methodology_version)
               VALUES (?,?,?,?,?,?)""",
            (
                f"CLIENT{self.company_id.removeprefix('RPT')}",
                self.case_id,
                self.company_id,
                "COMPLETE",
                json.dumps(scan_result, ensure_ascii=False),
                "client-output-test",
            ),
        )
        self.db.commit()

        expected_names = {
            "Knowledge": "المعرفة",
            "Operations": "التشغيل",
            "Brand": "البراند",
            "Data": "البيانات",
            "Independence": "الاستقلال",
        }
        with sana_app.app.test_request_context():
            summary = sana_app.company_summary(self.company_id).get_json()["data"]
            with patch.object(
                sana_app, "enforce_entity_company_scope", return_value=None
            ):
                passport = sana_app.passport_summary(
                    self.company_id
                ).get_json()["data"]
                question = sana_app.sds_question(
                    self.company_id
                ).get_json()["data"]
                text = sana_app.passport_report_text(
                    self.company_id
                ).get_json()["data"]["report_text"]

        for output in (summary["assets"], passport["assets"]):
            self.assertEqual(
                expected_names,
                {item["asset_type"]: item["client_asset_name"] for item in output},
            )
            self.assertEqual(
                scores,
                {item["asset_type"]: item["current_score"] for item in output},
            )
            self.assertEqual(
                expected_names,
                {item["asset_type"]: item["asset_name"] for item in output},
            )

        self.assertEqual(
            scores,
            {
                item["asset_type"]: item["score"]
                for item in passport["scan_asset_scores"]
            },
        )
        self.assertEqual(
            expected_names,
            {
                item["asset_type"]: item["client_asset_name"]
                for item in passport["scan_asset_scores"]
            },
        )
        self.assertIn(question["client_asset_name"], expected_names.values())
        self.assertEqual(question["client_asset_name"], question["asset_name"])
        self.assertEqual(0, summary["assets"][0]["current_score"])
        self.assertEqual(0, passport["scan_asset_scores"][0]["score"])

        for internal_asset_name in expected_names:
            self.assertNotIn(internal_asset_name, text)

        passport_template = sana_app.app.jinja_env.loader.get_source(
            sana_app.app.jinja_env, "03-business-passport.html"
        )[0]
        new_case_template = sana_app.app.jinja_env.loader.get_source(
            sana_app.app.jinja_env, "04-new-case-client.html"
        )[0]
        self.assertIn("${a.client_asset_name}", passport_template)
        self.assertIn("${sds.client_asset_name}", passport_template)
        self.assertNotIn("${a.asset_name}", passport_template)
        self.assertIn("new Option(a.client_asset_name,a.asset_id)", new_case_template)
        self.assertNotIn("new Option(a.asset_name,a.asset_id)", new_case_template)

    def test_two_sourced_submissions_close_distinct_requests_and_unlock_conditional_review(self):
        suffix = self.company_id.removeprefix("RPT")
        self.db.execute(
            "DELETE FROM evidence WHERE evidence_id=?",
            (f"E2{suffix}",),
        )
        self.db.execute(
            """INSERT INTO evidence
               (evidence_id,company_id,case_id,asset_id,title,source_type,confidence,
                evidence_type,source_ref,information_type,verification_status,
                source_category)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"DISCOPS{suffix}", self.company_id, self.case_id,
                self.asset_ids["Operations"], "التحدي المعلن: التشغيل",
                "اكتشاف_ذاتي", 50, "Evidence", "SDS-001 Q2",
                "Narrative", "UNVERIFIED", "SELF_REPORTED",
            ),
        )
        first = run_scan(self.db, self.case_id)
        open_requests = [
            item for item in first["evidence_requests"] if item["status"] == "OPEN"
        ]
        self.assertGreaterEqual(len(open_requests), 2)

        for index, request in enumerate(open_requests[:2], start=1):
            self.db.execute(
                """INSERT INTO evidence
                   (evidence_id,company_id,case_id,asset_id,title,source_type,confidence,
                    evidence_type,source_ref,information_type,verification_status,
                    source_category)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"CLIENT{index}{suffix}", self.company_id, self.case_id,
                    request["asset_id"], f"معلومة عميل للمحور {index}",
                    "معلومة عميل بمصدر", 70, "Evidence", f"REPORT:{index}",
                    "Narrative", "UNVERIFIED", "SELF_REPORTED",
                ),
            )
        self.db.commit()

        second = run_scan(self.db, self.case_id)
        self.assertEqual(2, second["evidence_progress"]["completed"])
        submitted = [
            item for item in second["evidence_requests"]
            if item["status"] == "SUBMITTED"
        ]
        self.assertEqual(2, len(submitted))
        self.assertEqual("CONDITIONAL", second["decision_readiness"])
        self.assertEqual("REVIEW_REQUIRED", second["status"])
        self.assertFalse(second["proposed_decision"]["causal_claim"])

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
        self.assertEqual([], report["scan_unplanned_decisions"])
        report_decision_ids = {
            item["decision_id"] for item in report["scan_initiatives"]
            if item["decision_id"]
        }
        self.assertNotIn(f"OTHER{suffix}", report_decision_ids)
        html = sana_app.app.jinja_env.get_template(
            "14-passport-report.html"
        ).render(**report)
        executive_report = html.split("<!-- ملحق جواز الشركة", 1)[0]
        self.assertNotIn("قضية أحدث غير مفحوصة", executive_report)
        self.assertNotIn("مشكلة أحدث", executive_report)
        self.assertNotIn("قرار لا يخص Scan", executive_report)
        self.assertNotIn("قرار بلا مرحلة", executive_report)

        exact_case = sana_app._build_scan_report_context(
            self.company_id, case_id=newer_case_id
        )
        self.assertEqual("NOT_RUN", exact_case["scan_status"])
        self.assertEqual(newer_case_id, exact_case["diagnostic_review"]["snapshot"]["case_id"])
        self.assertNotEqual(
            report["scan"]["scan_id"],
            exact_case["diagnostic_review"]["snapshot"]["scan_id"],
        )
        self.assertTrue(
            exact_case["scan_journey"]["case_url"].endswith(newer_case_id)
        )

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
            report_html = sana_app.app.jinja_env.get_template(
                "14-passport-report.html"
            ).render(**report)
            self.assertIn("الأولوية الأولى", report_html)
            self.assertIn("ما القرار الآمن الآن؟", report_html)
            self.assertNotIn(">None<", report_html)
            self.assertNotIn("0 مثبتة · 0 من الشركة", report_html)
            self.db.execute("DELETE FROM scan_runs WHERE scan_id=?", (scan_id,))
            self.db.commit()

    def test_financial_value_gate_and_nested_scan_values_stay_deferred(self):
        gate = sana_app.financial_value_gate(
            sources=[{
                "source_type": "ACCOUNTING_LEDGER_EXPORT",
                "verification_status": "VERIFIED",
                "information_type": "Actual",
                "source_ref": "ledger:2026-08",
                "confidence": 95,
                "observed_at": "2026-08-31",
            }],
            today=date(2026, 9, 2),
        )
        self.assertEqual("DEFERRED", gate["status"])
        self.assertFalse(gate["eligible"])
        self.assertIn("اعتماد نسخة منهجية مالية منشورة", gate["missing_requirements"])
        self.assertIsNone(gate["current_value"])
        self.assertIsNone(gate["potential_value"])

        scan_id = f"FINANCE{self.company_id.removeprefix('RPT')}"
        self.db.execute(
            """INSERT INTO scan_runs
               (scan_id,case_id,company_id,status,result,methodology_version)
               VALUES (?,?,?,?,?,?)""",
            (
                scan_id,
                self.case_id,
                self.company_id,
                "COMPLETE",
                json.dumps({
                    "status": "COMPLETE",
                    "case_id": self.case_id,
                    "asset_scores": [],
                    "current_value": 800000,
                    "potential_value": 1200000,
                    "value_gap": 400000,
                    "nested": {"current_value": 777, "potential_value": 888},
                }),
                "test",
            ),
        )
        self.db.commit()

        context = sana_app._build_passport_context(self.company_id)
        self.assertIsNone(context["current_value"])
        self.assertIsNone(context["potential_value"])
        self.assertIsNone(context["scan"]["current_value"])
        self.assertIsNone(context["scan"]["potential_value"])
        self.assertIsNone(context["scan"]["value_gap"])
        self.assertIsNone(context["scan"]["nested"]["current_value"])
        self.assertIsNone(context["scan"]["nested"]["potential_value"])
        self.assertEqual("DEFERRED", context["financial_value_policy"]["status"])

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

    def test_text_alias_and_case_api_use_the_same_snapshot_and_cta(self):
        scan = run_scan(self.db, self.case_id)
        context = sana_app._build_passport_context(self.company_id)
        review = context["diagnostic_review"]
        self.assertEqual(scan["scan_id"], review["snapshot"]["scan_id"])

        with sana_app.app.test_request_context():
            response = sana_app.passport_report_text(self.company_id)
            payload = response.get_json()
        text = payload["data"]["report_text"]
        self.assertIn(review["journey"]["action_label"], text)
        self.assertIn("الثقة عبر مراحل القرار", text)
        self.assertIn("قبل القرار: مشروط", text)
        self.assertIn("قياس الأثر: غير جاهز", text)
        for private_text in (
            "SELF_REPORTED",
            "UNVERIFIED",
            scan["scan_id"],
            self.company_id,
            self.case_id,
            "Evidence",
            "Fact",
            "SDS-001",
            "ops-log:test",
        ):
            self.assertNotIn(private_text, text)

        with patch(
            "sana_knowledge.contextual_reference_knowledge",
            side_effect=RuntimeError("provider unavailable"),
        ):
            fallback = sana_app._build_scan_report_context(
                self.company_id, case_id=self.case_id
            )
        self.assertFalse(
            fallback["diagnostic_review"]["fallback"]["reference_knowledge_available"]
        )
        self.assertTrue(
            fallback["diagnostic_review"]["fallback"]["deterministic"]
        )
        self.assertIn(
            "تبقى نتيجة Scan قابلة للمراجعة",
            fallback["reference_knowledge"]["knowledge_gap"],
        )

    def test_scan_excludes_other_open_cases_and_latest_rescan_is_stable(self):
        suffix = self.company_id.removeprefix("RPT")
        other_case_id = f"OPEN{suffix}"
        self.db.execute(
            """INSERT INTO cases
               (case_id,company_id,case_title,case_status,declared_problem,real_question)
               VALUES (?,?,?,?,?,?)""",
            (
                other_case_id, self.company_id, "قضية مفتوحة أخرى", "مفتوح",
                "تعطل مختلف لا يخص القضية الحالية", "كيف نعالج التعطل الآخر؟",
            ),
        )
        self.db.commit()

        first = run_scan(self.db, self.case_id)
        second = run_scan(self.db, self.case_id)
        self.assertNotIn(
            f"CASE:{other_case_id}:DECLARED_PROBLEM",
            {item["source_id"] for item in second["classified_inputs"]},
        )
        latest = sana_app._build_scan_report_context(
            self.company_id, case_id=self.case_id
        )
        self.assertEqual(second["scan_id"], latest["scan"]["scan_id"])
        self.assertNotEqual(first["scan_id"], second["scan_id"])

    def test_self_reported_capacity_creates_low_risk_conditional_experiment(self):
        suffix = self.company_id.removeprefix("RPT")
        self.db.execute(
            """UPDATE evidence SET title=?, confidence=?, information_type='Actual',
                      verification_status='UNVERIFIED', source_category='SELF_REPORTED',
                      period_start=?, period_end=?, normalized_value=?, unit=?,
                      topic_key=?, source_ref=?
               WHERE evidence_id=?""",
            (
                "عدد الطلاب الحالي 20", 70, "2026-08-01", "2026-08-31",
                20, "student", "students", "founder:capacity", f"E1{suffix}",
            ),
        )
        self.db.execute(
            """UPDATE evidence SET title=?, confidence=?, information_type='Target',
                      verification_status='UNVERIFIED', source_category='SELF_REPORTED',
                      period_start=?, period_end=?, normalized_value=?, unit=?,
                      topic_key=?, source_ref=?
               WHERE evidence_id=?""",
            (
                "السعة المتاحة 100 طالب", 70, "2026-08-01", "2026-08-31",
                100, "student", "capacity", "founder:capacity", f"E2{suffix}",
            ),
        )
        self.db.commit()

        scan = run_scan(self.db, self.case_id)

        self.assertEqual("CONDITIONAL", scan["decision_readiness"])
        self.assertEqual("REVIEW_REQUIRED", scan["status"])
        self.assertEqual(70, scan["decision_confidence"]["score"])
        self.assertEqual(1, scan["decision_confidence"]["source_family_count"])
        self.assertEqual("SELF_REPORTED_ONLY", scan["diagnostic_quality"]["independence"])
        self.assertEqual(20.0, scan["proposed_decision"]["kpi"]["baseline"])
        self.assertFalse(scan["proposed_decision"]["causal_claim"])
        self.assertIn("7–14", scan["proposed_decision"]["statement"])

        with sana_app.app.test_request_context():
            response, status_code = sana_app.create_p0_case_decision(self.case_id)
        self.assertEqual(201, status_code)
        decision = response.get_json()["data"]
        self.assertEqual(70, decision["confidence_score"])
        self.assertIn("تجربة قياس آمنة", decision["title"])

    def test_human_p0_review_transitions_snapshot_to_complete(self):
        scan = run_scan(self.db, self.case_id)
        self.assertEqual("REVIEW_REQUIRED", scan["status"])

        with sana_app.app.test_request_context():
            response, status_code = sana_app.create_p0_case_decision(self.case_id)
        self.assertEqual(201, status_code)
        decision = response.get_json()["data"]

        completed = sana_app._build_scan_report_context(
            self.company_id, case_id=self.case_id
        )
        review = completed["diagnostic_review"]
        self.assertEqual("COMPLETE", review["journey"]["status"])
        self.assertEqual(scan["scan_id"], review["snapshot"]["scan_id"])
        self.assertFalse(completed["scan"]["human_review_required"])
        self.assertEqual(
            decision["decision_id"],
            completed["scan"]["human_review"]["decision_id"],
        )
        self.assertFalse(review["evidence_gate"]["final_score_allowed"])
        self.assertEqual("N/A — Deferred", review["financial_value"]["label"])

    def test_rescan_requires_and_completes_a_new_snapshot_review(self):
        first_scan = run_scan(self.db, self.case_id)
        with sana_app.app.test_request_context():
            first_response, first_status = sana_app.create_p0_case_decision(self.case_id)
        self.assertEqual(201, first_status)
        first_decision = first_response.get_json()["data"]

        second_scan = run_scan(self.db, self.case_id)
        self.assertNotEqual(first_scan["scan_id"], second_scan["scan_id"])
        before_review = sana_app._build_scan_report_context(
            self.company_id, case_id=self.case_id
        )
        self.assertEqual("REVIEW_REQUIRED", before_review["scan_status"])

        with sana_app.app.test_request_context():
            second_response, second_status = sana_app.create_p0_case_decision(self.case_id)
        self.assertEqual(201, second_status)
        second_decision = second_response.get_json()["data"]
        self.assertNotEqual(first_decision["decision_id"], second_decision["decision_id"])
        self.assertEqual(first_scan["scan_id"], first_decision["scan_id"])
        self.assertEqual(second_scan["scan_id"], second_decision["scan_id"])

        after_review = sana_app._build_scan_report_context(
            self.company_id, case_id=self.case_id
        )
        self.assertEqual("COMPLETE", after_review["scan_status"])
        self.assertEqual(second_scan["scan_id"], after_review["scan"]["scan_id"])

    def test_snapshot_stays_immutable_when_case_changes_and_decisions_are_rescoped(self):
        first_scan = run_scan(self.db, self.case_id)
        original_problem = first_scan["diagnostic_problem"]["statement"]
        self.db.execute(
            """INSERT INTO decisions
               (decision_id,company_id,case_id,title,status,phase_label,scan_id)
               VALUES (?,?,?,?,?,?,?)""",
            ("FIRSTDEC", self.company_id, self.case_id, "قرار أول", "مقترح", "0-30", first_scan["scan_id"]),
        )
        self.db.execute(
            "UPDATE cases SET declared_problem=?, real_question=? WHERE case_id=?",
            ("مشكلة بعد الفحص", "سؤال بعد الفحص", self.case_id),
        )
        self.db.commit()
        first_context = sana_app._build_scan_report_context(
            self.company_id, case_id=self.case_id
        )
        self.assertEqual(original_problem, first_context["diagnostic_review"]["problem"]["statement"])
        self.assertNotEqual("مشكلة بعد الفحص", first_context["diagnostic_review"]["problem"]["statement"])
        self.assertIn(
            "FIRSTDEC",
            {item.get("decision_id") for item in first_context["scan_initiatives"]},
        )

        # A decision from the earlier snapshot must not enter the new snapshot's plan.
        second_scan = run_scan(self.db, self.case_id)
        second_context = sana_app._build_scan_report_context(
            self.company_id, case_id=self.case_id
        )
        self.assertEqual(second_scan["scan_id"], second_context["scan"]["scan_id"])
        self.assertNotIn(
            "FIRSTDEC",
            {item.get("decision_id") for item in second_context["scan_initiatives"]},
        )

    def test_unknown_cycle_is_explicit_in_text_and_pdf_exports(self):
        self.db.execute(
            "DELETE FROM evidence WHERE company_id=? AND case_id=?",
            (self.company_id, self.case_id),
        )
        self.db.commit()
        initial = run_scan(self.db, self.case_id)
        request = initial["evidence_requests"][0]

        with patch.object(sana_app, "enforce_entity_company_scope", return_value=None):
            with sana_app.app.test_request_context(
                "/api/companies/evidence", method="POST", json={
                    "case_id": self.case_id,
                    "unknown": True,
                    "request_fingerprint": request["fingerprint"],
                }
            ):
                response, status_code = sana_app.add_evidence(self.company_id)
        self.assertEqual(201, status_code)
        self.assertEqual("UNKNOWN", response.get_json()["data"]["journey_outcome"])

        report = sana_app._build_passport_context(self.company_id)
        cycle = report["diagnostic_review"]["client_evidence_request_cycle"]
        self.assertEqual("UNKNOWN", cycle["outcome"])
        self.assertEqual(2, cycle["critical_request_count"])
        self.assertEqual(1, cycle["closed_request_count"])
        self.assertEqual(2, cycle["fingerprint_count"])

        with sana_app.app.test_request_context():
            text = sana_app.passport_report_text(self.company_id).get_json()
            with patch.object(
                sana_app, "enforce_entity_company_scope", return_value=None
            ):
                pdf_response = sana_app.passport_report_pdf(self.company_id)
        text = text["data"]["report_text"]
        for output in (text,):
            self.assertIn("UNKNOWN", output)
            self.assertIn("نهاية دورة الأدلة", output)
            self.assertIn("الطلبات الحرجة: 2", output)
            self.assertIn("الطلبات المغلقة: 1 من 2", output)
            self.assertIn("الطلبات المتتبعة: 2", output)
        for fingerprint in initial["evidence_request_cycle"]["requested_fingerprints"]:
            self.assertNotIn(fingerprint, text)
        pdf = pdf_response.get_data()
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 10000)
        extracted_pdf = subprocess.run(
            ["pdftotext", "-", "-"],
            input=pdf,
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="replace")
        for output in (text,):
            self.assertIn("UNKNOWN", output)
            self.assertIn("نهاية دورة الأدلة", output)
            self.assertIn("الطلبات الحرجة: 2", output)
            self.assertIn("الطلبات المغلقة: 1 من 2", output)
        for token in ("UNKNOWN", "نهاية", "الطلبات", "الحرجة", "المغلقة"):
            self.assertIn(token, extracted_pdf)
        for fingerprint in initial["evidence_request_cycle"]["requested_fingerprints"]:
            self.assertNotIn(fingerprint, extracted_pdf)

    def test_text_alias_and_case_api_use_the_same_snapshot_and_cta(self):
        scan = run_scan(self.db, self.case_id)
        context = sana_app._build_passport_context(self.company_id)
        review = context["diagnostic_review"]
        self.assertEqual(scan["scan_id"], review["snapshot"]["scan_id"])

        with sana_app.app.test_request_context():
            response = sana_app.passport_report_text(self.company_id)
            payload = response.get_json()
        text = payload["data"]["report_text"]
        self.assertIn(review["journey"]["action_label"], text)
        self.assertIn("الثقة عبر مراحل القرار", text)
        self.assertIn("قبل القرار: مشروط", text)
        self.assertIn("قياس الأثر: غير جاهز", text)
        for private_text in (
            "SELF_REPORTED",
            "UNVERIFIED",
            scan["scan_id"],
            self.company_id,
            self.case_id,
            "Evidence",
            "Fact",
            "SDS-001",
            "ops-log:test",
        ):
            self.assertNotIn(private_text, text)

        with patch(
            "sana_knowledge.contextual_reference_knowledge",
            side_effect=RuntimeError("provider unavailable"),
        ):
            fallback = sana_app._build_scan_report_context(
                self.company_id, case_id=self.case_id
            )
        self.assertFalse(
            fallback["diagnostic_review"]["fallback"]["reference_knowledge_available"]
        )
        self.assertTrue(
            fallback["diagnostic_review"]["fallback"]["deterministic"]
        )
        self.assertIn(
            "تبقى نتيجة Scan قابلة للمراجعة",
            fallback["reference_knowledge"]["knowledge_gap"],
        )

    def test_scan_excludes_other_open_cases_and_latest_rescan_is_stable(self):
        suffix = self.company_id.removeprefix("RPT")
        other_case_id = f"OPEN{suffix}"
        self.db.execute(
            """INSERT INTO cases
               (case_id,company_id,case_title,case_status,declared_problem,real_question)
               VALUES (?,?,?,?,?,?)""",
            (
                other_case_id, self.company_id, "قضية مفتوحة أخرى", "مفتوح",
                "تعطل مختلف لا يخص القضية الحالية", "كيف نعالج التعطل الآخر؟",
            ),
        )
        self.db.commit()

        first = run_scan(self.db, self.case_id)
        second = run_scan(self.db, self.case_id)
        self.assertNotIn(
            f"CASE:{other_case_id}:DECLARED_PROBLEM",
            {item["source_id"] for item in second["classified_inputs"]},
        )
        latest = sana_app._build_scan_report_context(
            self.company_id, case_id=self.case_id
        )
        self.assertEqual(second["scan_id"], latest["scan"]["scan_id"])
        self.assertNotEqual(first["scan_id"], second["scan_id"])

    def test_self_reported_capacity_creates_low_risk_conditional_experiment(self):
        suffix = self.company_id.removeprefix("RPT")
        self.db.execute(
            """UPDATE evidence SET title=?, confidence=?, information_type='Actual',
                      verification_status='UNVERIFIED', source_category='SELF_REPORTED',
                      period_start=?, period_end=?, normalized_value=?, unit=?,
                      topic_key=?, source_ref=?
               WHERE evidence_id=?""",
            (
                "عدد الطلاب الحالي 20", 70, "2026-08-01", "2026-08-31",
                20, "student", "students", "founder:capacity", f"E1{suffix}",
            ),
        )
        self.db.execute(
            """UPDATE evidence SET title=?, confidence=?, information_type='Target',
                      verification_status='UNVERIFIED', source_category='SELF_REPORTED',
                      period_start=?, period_end=?, normalized_value=?, unit=?,
                      topic_key=?, source_ref=?
               WHERE evidence_id=?""",
            (
                "السعة المتاحة 100 طالب", 70, "2026-08-01", "2026-08-31",
                100, "student", "capacity", "founder:capacity", f"E2{suffix}",
            ),
        )
        self.db.commit()

        scan = run_scan(self.db, self.case_id)

        self.assertEqual("CONDITIONAL", scan["decision_readiness"])
        self.assertEqual("REVIEW_REQUIRED", scan["status"])
        self.assertEqual(70, scan["decision_confidence"]["score"])
        self.assertEqual(1, scan["decision_confidence"]["source_family_count"])
        self.assertEqual("SELF_REPORTED_ONLY", scan["diagnostic_quality"]["independence"])
        self.assertEqual(20.0, scan["proposed_decision"]["kpi"]["baseline"])
        self.assertFalse(scan["proposed_decision"]["causal_claim"])
        self.assertIn("7–14", scan["proposed_decision"]["statement"])

        with sana_app.app.test_request_context():
            response, status_code = sana_app.create_p0_case_decision(self.case_id)
        self.assertEqual(201, status_code)
        decision = response.get_json()["data"]
        self.assertEqual(70, decision["confidence_score"])
        self.assertIn("تجربة قياس آمنة", decision["title"])

    def test_human_p0_review_transitions_snapshot_to_complete(self):
        scan = run_scan(self.db, self.case_id)
        self.assertEqual("REVIEW_REQUIRED", scan["status"])

        with sana_app.app.test_request_context():
            response, status_code = sana_app.create_p0_case_decision(self.case_id)
        self.assertEqual(201, status_code)
        decision = response.get_json()["data"]

        completed = sana_app._build_scan_report_context(
            self.company_id, case_id=self.case_id
        )
        review = completed["diagnostic_review"]
        self.assertEqual("COMPLETE", review["journey"]["status"])
        self.assertEqual(scan["scan_id"], review["snapshot"]["scan_id"])
        self.assertFalse(completed["scan"]["human_review_required"])
        self.assertEqual(
            decision["decision_id"],
            completed["scan"]["human_review"]["decision_id"],
        )
        self.assertFalse(review["evidence_gate"]["final_score_allowed"])
        self.assertEqual("N/A — Deferred", review["financial_value"]["label"])

    def test_rescan_requires_and_completes_a_new_snapshot_review(self):
        first_scan = run_scan(self.db, self.case_id)
        with sana_app.app.test_request_context():
            first_response, first_status = sana_app.create_p0_case_decision(self.case_id)
        self.assertEqual(201, first_status)
        first_decision = first_response.get_json()["data"]

        second_scan = run_scan(self.db, self.case_id)
        self.assertNotEqual(first_scan["scan_id"], second_scan["scan_id"])
        before_review = sana_app._build_scan_report_context(
            self.company_id, case_id=self.case_id
        )
        self.assertEqual("REVIEW_REQUIRED", before_review["scan_status"])

        with sana_app.app.test_request_context():
            second_response, second_status = sana_app.create_p0_case_decision(self.case_id)
        self.assertEqual(201, second_status)
        second_decision = second_response.get_json()["data"]
        self.assertNotEqual(first_decision["decision_id"], second_decision["decision_id"])
        self.assertEqual(first_scan["scan_id"], first_decision["scan_id"])
        self.assertEqual(second_scan["scan_id"], second_decision["scan_id"])

        after_review = sana_app._build_scan_report_context(
            self.company_id, case_id=self.case_id
        )
        self.assertEqual("COMPLETE", after_review["scan_status"])
        self.assertEqual(second_scan["scan_id"], after_review["scan"]["scan_id"])

    def test_snapshot_stays_immutable_when_case_changes_and_decisions_are_rescoped(self):
        first_scan = run_scan(self.db, self.case_id)
        original_problem = first_scan["diagnostic_problem"]["statement"]
        self.db.execute(
            """INSERT INTO decisions
               (decision_id,company_id,case_id,title,status,phase_label,scan_id)
               VALUES (?,?,?,?,?,?,?)""",
            ("FIRSTDEC", self.company_id, self.case_id, "قرار أول", "مقترح", "0-30", first_scan["scan_id"]),
        )
        self.db.execute(
            "UPDATE cases SET declared_problem=?, real_question=? WHERE case_id=?",
            ("مشكلة بعد الفحص", "سؤال بعد الفحص", self.case_id),
        )
        self.db.commit()
        first_context = sana_app._build_scan_report_context(
            self.company_id, case_id=self.case_id
        )
        self.assertEqual(original_problem, first_context["diagnostic_review"]["problem"]["statement"])
        self.assertNotEqual("مشكلة بعد الفحص", first_context["diagnostic_review"]["problem"]["statement"])
        self.assertIn(
            "FIRSTDEC",
            {item.get("decision_id") for item in first_context["scan_initiatives"]},
        )

        # A decision from the earlier snapshot must not enter the new snapshot's plan.
        second_scan = run_scan(self.db, self.case_id)
        second_context = sana_app._build_scan_report_context(
            self.company_id, case_id=self.case_id
        )
        self.assertEqual(second_scan["scan_id"], second_context["scan"]["scan_id"])
        self.assertNotIn(
            "FIRSTDEC",
            {item.get("decision_id") for item in second_context["scan_initiatives"]},
        )

@unittest.skipUnless(
    os.environ.get("RUN_SANA_SCAN_BROWSER_TESTS") == "1"
    and (os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_PASSWORD")),
    "يتطلب RUN_SANA_SCAN_BROWSER_TESTS=1 واتصال قاعدة البيانات",
)
class SanaScanJourneyBrowserTests(unittest.TestCase):
    """تغطية متصفح معزولة للحالات الأربع وارتباط ملف الأصول بالتقرير."""

    PREVIEW_KEY = "sana-scan-browser-preview-key-2026"
    STATES = (
        ("NOT_RUN", "لم يُشغّل بعد", "ابدأ تقييم الأصول", "/assessment"),
        ("INCOMPLETE", "غير مكتمل — بوابة الأدلة مفتوحة", "أكمل الأدلة المطلوبة", "/case/"),
        ("REVIEW_REQUIRED", "جاهز للمراجعة البشرية", "راجع التقرير التنفيذي", "/company/"),
        ("COMPLETE", "اكتملت المراجعة — التقرير جاهز", "افتح التقرير التنفيذي", "/company/"),
    )

    @classmethod
    def setUpClass(cls):
        cls.db = sana_app._connect_pg()
        suffix = uuid.uuid4().hex[:10].upper()
        cls.suffix = suffix
        cls.fixtures = {}

        for status, label, action, _ in cls.STATES:
            company_id = f"SCAN-BROWSER-{status}-{suffix}"
            case_id = f"CASE-SCAN-BROWSER-{status}-{suffix}"
            cls.fixtures[status] = {
                "company_id": company_id,
                "case_id": case_id,
                "label": label,
                "action": action,
            }
            cls.db.execute(
                """INSERT INTO companies
                   (company_id,name,sector,main_goal)
                   VALUES (?,?,?,?)""",
                (
                    company_id,
                    f"شركة اختبار حالة {status}",
                    "خدمات B2B",
                    "اختبار رحلة Sana Scan",
                ),
            )
            cls.db.execute(
                """INSERT INTO cases
                   (case_id,company_id,case_title,case_type,case_status,
                    declared_problem,real_question)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    case_id,
                    company_id,
                    f"قضية حالة {status}",
                    "تشخيص",
                    "مفتوح",
                    f"مشكلة اختبار {status}",
                    f"سؤال اختبار {status}",
                ),
            )
            for index, asset_type in enumerate(
                ("Knowledge", "Operations", "Brand", "Data", "Independence")
            ):
                cls.db.execute(
                    """INSERT INTO assets
                       (asset_id,company_id,asset_type,asset_name,current_score,
                        fragility_score,status)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        f"ASSET-SCAN-BROWSER-{status}-{index}-{suffix}",
                        company_id,
                        asset_type,
                        asset_type,
                        35 + index * 8,
                        50,
                        "تحت المراجعة",
                    ),
                )

            if status != "NOT_RUN":
                score_status = [
                    {
                        "asset_type": asset_type,
                        "score": 60 + index * 5,
                        "status": "COMPLETE",
                    }
                    for index, asset_type in enumerate(
                        ("Knowledge", "Operations", "Brand", "Data", "Independence")
                    )
                ]
                result = {
                    "status": status,
                    "case_id": case_id,
                    "asset_scores": score_status if status != "INCOMPLETE" else [],
                    "missing_evidence": (
                        ["سجل اختبار ناقص"] if status == "INCOMPLETE" else []
                    ),
                    "findings": [],
                }
                cls.db.execute(
                    """INSERT INTO scan_runs
                       (scan_id,case_id,company_id,status,result,methodology_version)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        f"SCAN-RUN-BROWSER-{status}-{suffix}",
                        case_id,
                        company_id,
                        status,
                        json.dumps(result, ensure_ascii=False),
                        "browser-test",
                    ),
                )
        cls.db.commit()

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.port = sock.getsockname()[1]
        sock.close()
        server_env = os.environ.copy()
        server_env.update(
            {
                "PORT": str(cls.port),
                "SANA_ENV": "production",
                "REPLIT_DEPLOYMENT": "1",
                "SESSION_SECRET": "sana-scan-browser-session-secret-2026",
                "ADMIN_PREVIEW_KEY": cls.PREVIEW_KEY,
            }
        )
        cls.server = subprocess.Popen(
            [sys.executable, "app.py"],
            cwd=os.path.dirname(__file__),
            env=server_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        try:
            for _ in range(60):
                if cls.server.poll() is not None:
                    raise RuntimeError("خادم اختبار رحلة Scan توقف قبل الجاهزية")
                try:
                    with urlopen(f"{cls.base_url}/login", timeout=1) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(0.25)
            else:
                raise RuntimeError("انتهت مهلة تشغيل خادم اختبار رحلة Scan")
        except Exception:
            cls._stop_server()
            cls._cleanup_fixtures()
            raise

    @classmethod
    def tearDownClass(cls):
        cls._stop_server()
        cls._cleanup_fixtures()
        cls.db.close()

    @classmethod
    def _stop_server(cls):
        if getattr(cls, "server", None) and cls.server.poll() is None:
            cls.server.terminate()
            try:
                cls.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.server.kill()
                cls.server.wait(timeout=5)

    @classmethod
    def _cleanup_fixtures(cls):
        if not getattr(cls, "db", None):
            return
        company_ids = [item["company_id"] for item in cls.fixtures.values()]
        placeholders = ",".join("?" for _ in company_ids)
        try:
            cls.db.rollback()
            cls.db.execute(
                f"DELETE FROM scan_findings WHERE company_id IN ({placeholders})",
                company_ids,
            )
            cls.db.execute(
                f"DELETE FROM scan_runs WHERE company_id IN ({placeholders})",
                company_ids,
            )
            cls.db.execute(
                f"DELETE FROM cases WHERE company_id IN ({placeholders})",
                company_ids,
            )
            cls.db.execute(
                f"DELETE FROM assets WHERE company_id IN ({placeholders})",
                company_ids,
            )
            cls.db.execute(
                f"DELETE FROM companies WHERE company_id IN ({placeholders})",
                company_ids,
            )
            cls.db.commit()
        except Exception:
            cls.db.rollback()
            raise

    def _preview_url(self, path, company_id):
        return (
            f"{self.base_url}{path}?company_id={company_id}"
            f"&view=client&admin_key={self.PREVIEW_KEY}"
        )

    def test_each_scan_state_is_clear_in_live_passport_and_executive_report(self):
        from playwright.sync_api import sync_playwright

        screenshot_dir = os.environ.get(
            "SANA_SCAN_SCREENSHOT_DIR", "/tmp/sana-scan-browser"
        )
        os.makedirs(screenshot_dir, exist_ok=True)
        with sync_playwright() as playwright:
            for status, expected_label, expected_action, expected_path in self.STATES:
                with self.subTest(status=status):
                    browser = playwright.chromium.launch(headless=True)
                    page = browser.new_page(viewport={"width": 1440, "height": 1100})
                    fixture = self.fixtures[status]
                    company_id = fixture["company_id"]
                    page.goto(
                        self._preview_url("/passport", company_id),
                        wait_until="domcontentloaded",
                    )
                    live = page.locator("[data-testid='live-scan-journey']")
                    live.wait_for(state="visible", timeout=30_000)
                    self.assertEqual(status, live.get_attribute("data-scan-status"))
                    self.assertIn(expected_label, live.inner_text())
                    action_link = live.locator(".journey-action")
                    self.assertIn(expected_action, action_link.inner_text())
                    action_href = action_link.get_attribute("href")
                    self.assertIn(f"company_id={company_id}", action_href)
                    self.assertIn("view=client", action_href)
                    self.assertIn("admin_key=", action_href)
                    if status == "INCOMPLETE":
                        self.assertIn(f"/case/{fixture['case_id']}", action_href)
                    elif status in {"REVIEW_REQUIRED", "COMPLETE"}:
                        self.assertIn(f"/company/{company_id}/scan-report", action_href)
                    else:
                        self.assertTrue(action_href.startswith("/assessment"))
                    live_summary = page.locator("[data-testid='live-passport-summary']")
                    live_summary_text = live_summary.inner_text()
                    self.assertTrue(
                        "الملخص الحي" in live_summary_text
                        or "صورة الشركة" in live_summary_text
                    )
                    self.assertIn("التقرير", live_summary_text)
                    page.screenshot(
                        path=os.path.join(
                            screenshot_dir, f"{status.lower()}-passport.png"
                        ),
                        full_page=True,
                    )

                    page.goto(
                        self._preview_url(
                            f"/company/{company_id}/scan-report", company_id
                        ),
                        wait_until="domcontentloaded",
                    )
                    report = page.locator("[data-testid='executive-report']")
                    report.wait_for(state="visible", timeout=30_000)
                    self.assertEqual(
                        1,
                        page.locator(
                            "[data-testid='executive-report-summary']"
                        ).count(),
                    )
                    journey = report.locator(
                        "[data-testid='report-journey-state']"
                    )
                    self.assertIn(expected_label, journey.inner_text())
                    self.assertIn(expected_action, journey.inner_text())
                    self.assertIn("تقرير Sana Scan التنفيذي", report.inner_text())
                    self.assertNotIn("الملخص الحي", report.inner_text())
                    self.assertNotIn("LIVE PASSPORT", report.inner_text())
                    page.screenshot(
                        path=os.path.join(
                            screenshot_dir, f"{status.lower()}-report.png"
                        ),
                        full_page=True,
                    )
                    page.close()
                    browser.close()

    def test_each_scan_state_is_clear_on_mobile(self):
        from playwright.sync_api import sync_playwright

        screenshot_dir = os.environ.get(
            "SANA_SCAN_SCREENSHOT_DIR", "/tmp/sana-scan-browser"
        )
        os.makedirs(screenshot_dir, exist_ok=True)
        mobile_viewport = {"width": 390, "height": 844}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for status, expected_label, expected_action, _ in self.STATES:
                with self.subTest(status=status):
                    page = browser.new_page(viewport=mobile_viewport)
                    fixture = self.fixtures[status]
                    company_id = fixture["company_id"]
                    page.goto(
                        self._preview_url("/passport", company_id),
                        wait_until="domcontentloaded",
                    )
                    live = page.locator("[data-testid='live-scan-journey']")
                    live.wait_for(state="visible", timeout=30_000)
                    action_link = live.locator(".journey-action")
                    action_link.wait_for(state="visible", timeout=10_000)

                    self.assertEqual(status, live.get_attribute("data-scan-status"))
                    self.assertIn(expected_label, live.inner_text())
                    self.assertIn(expected_action, action_link.inner_text())
                    live_box = live.bounding_box()
                    action_box = action_link.bounding_box()
                    self.assertIsNotNone(live_box)
                    self.assertIsNotNone(action_box)
                    self.assertGreaterEqual(live_box["x"], 0)
                    self.assertLessEqual(live_box["x"] + live_box["width"], 390)
                    self.assertGreaterEqual(action_box["height"], 44)
                    self.assertLessEqual(action_box["x"] + action_box["width"], 390)
                    self.assertLessEqual(
                        page.evaluate("() => document.documentElement.scrollWidth"),
                        390,
                    )

                    page.screenshot(
                        path=os.path.join(
                            screenshot_dir, f"{status.lower()}-passport-mobile.png"
                        ),
                        full_page=True,
                    )

                    page.goto(
                        self._preview_url(
                            f"/company/{company_id}/scan-report", company_id
                        ),
                        wait_until="domcontentloaded",
                    )
                    report = page.locator("[data-testid='executive-report']")
                    report.wait_for(state="visible", timeout=30_000)
                    report_kicker = report.locator(".report-kicker")
                    report_title = report.locator("h1")
                    journey = report.locator(
                        "[data-testid='report-journey-state']"
                    )
                    report_kicker.wait_for(state="visible", timeout=10_000)
                    report_title.wait_for(state="visible", timeout=10_000)
                    journey.wait_for(state="visible", timeout=10_000)
                    self.assertIn("تقرير Sana Scan التنفيذي", report_kicker.inner_text())
                    self.assertTrue(report_title.inner_text().strip())
                    self.assertIn(expected_label, journey.inner_text())
                    self.assertIn(expected_action, journey.inner_text())
                    title_box = report_title.bounding_box()
                    journey_box = journey.bounding_box()
                    self.assertIsNotNone(title_box)
                    self.assertIsNotNone(journey_box)
                    self.assertGreater(title_box["height"], 0)
                    self.assertGreater(journey_box["height"], 0)
                    self.assertLessEqual(
                        page.evaluate("() => document.documentElement.scrollWidth"),
                        390,
                    )
                    page.screenshot(
                        path=os.path.join(
                            screenshot_dir, f"{status.lower()}-report-mobile.png"
                        ),
                        full_page=True,
                    )
                    page.close()
            browser.close()

    def test_assessment_returns_to_the_same_preview_passport_context(self):
        from playwright.sync_api import sync_playwright

        company_id = self.fixtures["NOT_RUN"]["company_id"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(
                self._preview_url("/assessment", company_id),
                wait_until="domcontentloaded",
            )
            return_link = page.locator("[data-testid='passport-after-assessment']")
            return_link.wait_for(state="visible", timeout=10_000)
            return_box = return_link.bounding_box()
            self.assertIsNotNone(return_box)
            self.assertGreater(return_box["height"], 0)
            self.assertLessEqual(
                page.evaluate("() => document.documentElement.scrollWidth"),
                390,
            )
            href = return_link.get_attribute("href")
            self.assertTrue(href.startswith("/passport?"))
            self.assertIn(f"company_id={company_id}", href)
            self.assertIn("view=client", href)
            self.assertIn("admin_key=", href)
            return_link.click()
            page.wait_for_url("**/passport?**", timeout=10_000)
            self.assertIn(f"company_id={company_id}", page.url)
            self.assertIn("view=client", page.url)
            browser.close()


if __name__ == "__main__":
    unittest.main()
