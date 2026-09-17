"""اختبار قبول رحلة العميل والعزل دون الحاجة إلى pytest.

تشغيل:
    python3 -m unittest test_customer_journey_isolation.py -v
"""

import json
import uuid
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import app as sana_app
import sana_knowledge
from sana_company_memory import retrieve_memory


DISCOVERY_ANSWERS = {
    "fit_gate": {
        "operating_duration": "ONE_PLUS",
        "paying_customers": "YES",
        "delivery_mode": "TEAM_DELIVERY",
    },
    "q1": "زيادة المبيعات بشكل منضبط",
    "q2": "📉 المبيعات",
    "q3": "📊 البيانات والتقارير",
    "q4": "الإحالات",
    "q4_fu": "😟 انخفاض متوسط",
    "q5": "😰 يتعطل أغلب العمل",
    "q5_text": "تتوقف بعض العمليات المهمة",
    "q6": "الحدس والخبرة الشخصية",
    "q7": ["قرارات أوضح", "نمو الإيرادات"],
    "diagnostic_baseline": {
        "baseline_start": "2026-08-01",
        "baseline_end": "2026-08-31",
        "comparison_start": "2026-07-01",
        "comparison_end": "2026-07-31",
        "seasonality_context": "فترة تشغيل اعتيادية",
    },
}


class CustomerJourneyIsolationAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sana_app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        cls.created_company_ids = []

    @classmethod
    def tearDownClass(cls):
        if not cls.created_company_ids:
            return
        db = sana_app._connect_pg()
        placeholders = ",".join("?" for _ in cls.created_company_ids)
        try:
            decision_ids = [
                row["decision_id"]
                for row in db.execute(
                    f"SELECT decision_id FROM decisions WHERE company_id IN ({placeholders})",
                    cls.created_company_ids,
                ).fetchall()
            ]
            scan_ids = [
                row["scan_id"]
                for row in db.execute(
                    f"SELECT scan_id FROM scan_runs WHERE company_id IN ({placeholders})",
                    cls.created_company_ids,
                ).fetchall()
            ]
            if scan_ids:
                scan_placeholders = ",".join("?" for _ in scan_ids)
                db.execute(
                    f"DELETE FROM scan_findings WHERE scan_id IN ({scan_placeholders})",
                    scan_ids,
                )
            if decision_ids:
                decision_placeholders = ",".join("?" for _ in decision_ids)
                db.execute(
                    "DELETE FROM decision_asset_impacts "
                    f"WHERE decision_id IN ({decision_placeholders})",
                    decision_ids,
                )
            db.execute(
                """DELETE FROM gos_baseline_metrics
                   WHERE baseline_id IN (
                     SELECT baseline_id FROM gos_baselines
                     WHERE company_id IN (""" + placeholders + "))",
                cls.created_company_ids,
            )
            for table in (
                "scan_runs",
                "tasks",
                "decisions",
                "case_frameworks",
                "evidence_relations",
                "diagnostic_baselines",
                "gos_baselines",
                "evidence",
                "cases",
                "assets",
                "user_accounts",
                "companies",
            ):
                db.execute(
                    f"DELETE FROM {table} WHERE company_id IN ({placeholders})",
                    cls.created_company_ids,
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _new_customer(self, label):
        client = sana_app.app.test_client()
        token = uuid.uuid4().hex
        email = f"journey-{label}-{token}@test.local"
        password = "JourneyPass-2026"

        response = client.post("/signup", json={"email": email, "password": password})
        self.assertEqual(201, response.status_code, response.get_data(as_text=True))
        company_id = response.get_json()["data"]["company_id"]
        self.created_company_ids.append(company_id)
        self.assertEqual("/onboarding", response.get_json()["data"]["redirect"])

        start = client.get("/home")
        self.assertEqual(302, start.status_code)
        self.assertTrue(start.headers["Location"].endswith("/onboarding"))

        incomplete_setup = client.post(
            "/onboarding",
            json={"name": f"شركة رحلة {label}", "sector": "consulting"},
        )
        self.assertEqual(400, incomplete_setup.status_code)
        self.assertEqual(
            "INCOMPLETE_COMPANY_SETUP",
            incomplete_setup.get_json()["error"],
        )

        empty = client.get(f"/api/companies/{company_id}/summary")
        self.assertEqual(200, empty.status_code)
        empty_data = empty.get_json()["data"]
        self.assertEqual([], empty_data["cases"])
        self.assertEqual([], empty_data["decisions"])
        self.assertEqual(0, empty_data["evidence_count"])

        response = client.post(
            "/onboarding",
            json={
                "name": f"شركة رحلة {label}",
                "sector": "consulting",
                "employee_count": 12,
                "business_type": "management_consulting",
                "respondent_role": "owner_founder",
                "goal_90_days": "تحسين التحويل خلال 90 يومًا",
                "primary_challenge": "تشتت الأدلة والقرارات",
            },
        )
        self.assertEqual(200, response.status_code, response.get_data(as_text=True))
        self.assertEqual("/discovery", response.get_json()["data"]["redirect"])
        start = client.get("/home")
        self.assertEqual(302, start.status_code)
        self.assertTrue(start.headers["Location"].endswith("/discovery"))

        response = client.post("/api/discovery/save", json=DISCOVERY_ANSWERS)
        self.assertEqual(200, response.status_code, response.get_data(as_text=True))
        case_id = response.get_json()["data"]["case_id"]
        self.assertTrue(case_id)

        return {
            "client": client,
            "email": email,
            "password": password,
            "company_id": company_id,
            "case_id": case_id,
        }

    def test_complete_journey_and_two_company_isolation(self):
        landing = sana_app.app.test_client().get("/")
        landing_html = landing.get_data(as_text=True)
        self.assertIn('href="/signup"', landing_html)
        self.assertIn("ابدأ الآن", landing_html)
        self.assertIn("سنع يشخّص شركتك الخدمية من واقع معلوماتها", landing_html)
        self.assertIn("ويرتّب المشاكل والفرص", landing_html)
        self.assertIn("أولويات وقرارات واضحة بدل التخمين", landing_html)
        self.assertIn("احكِ لنا عن شركتك", landing_html)
        self.assertIn("سنع يرتب الصورة", landing_html)
        self.assertIn("يسألك فقط عما ينقص", landing_html)
        self.assertIn("يعطيك التشخيص والأولويات والقرارات", landing_html)
        self.assertIn("دقائق قليلة · بدون نموذج طويل", landing_html)

        customer_a = self._new_customer("A")
        customer_b = self._new_customer("B")
        client_a = customer_a["client"]
        client_b = customer_b["client"]

        later_case = client_a.post(
            f"/api/companies/{customer_a['company_id']}/cases",
            json={
                "declared_problem": "قضية لاحقة تحتاج معايرة مستقلة",
                "case_title": "قضية ما بعد onboarding",
                "related_asset_id": next(
                    asset["asset_id"]
                    for asset in client_a.get(
                        f"/api/companies/{customer_a['company_id']}/summary"
                    ).get_json()["data"]["assets"]
                    if asset["asset_type"] == "Data"
                ),
            },
        )
        self.assertEqual(201, later_case.status_code, later_case.get_data(as_text=True))
        later_case_id = later_case.get_json()["data"]["case_id"]
        calibrated = client_a.put(
            f"/api/cases/{later_case_id}/diagnostic-baseline",
            json={
                "baseline_start": "2026-08-01",
                "baseline_end": "2026-08-31",
                "comparison_start": "2026-07-01",
                "comparison_end": "2026-07-31",
                "seasonality_context": "فترة تشغيل اعتيادية",
            },
        )
        self.assertEqual(200, calibrated.status_code, calibrated.get_data(as_text=True))
        later_scan = client_a.post(f"/api/cases/{later_case_id}/scan")
        self.assertEqual(200, later_scan.status_code, later_scan.get_data(as_text=True))
        later_scan_data = later_scan.get_json()["data"]
        self.assertTrue(later_scan_data["diagnostic_baseline_valid"])
        self.assertFalse(any(
            "فترة الأساس التشخيصي" in item
            for item in later_scan_data["missing_evidence"]
        ))

        case_page = client_a.get(
            f"/case/{later_case_id}",
            follow_redirects=True,
        )
        case_html = case_page.get_data(as_text=True)
        self.assertIn("وش الفترة اللي عندك عنها بيانات فعلية؟", case_html)
        self.assertIn("آخر 30 يوم", case_html)
        self.assertIn("آخر 3 أشهر", case_html)
        self.assertIn("هل هذه الفترة مختلفة عن المعتاد؟", case_html)
        self.assertIn("احفظ وكمل", case_html)
        self.assertNotIn(">بداية الأساس<", case_html)
        self.assertNotIn(">بداية المقارنة<", case_html)

        future_comparison = client_a.put(
            f"/api/cases/{later_case_id}/diagnostic-baseline",
            json={
                "baseline_start": "2026-08-01",
                "baseline_end": "2026-08-31",
                "comparison_start": "2099-07-01",
                "comparison_end": "2099-07-31",
                "seasonality_context": "فترة تشغيل اعتيادية",
            },
        )
        self.assertEqual(400, future_comparison.status_code)
        self.assertEqual(
            "المقارنة تحتاج فترة انتهت فعليًا",
            future_comparison.get_json()["message"],
        )

        baseline_only = client_a.put(
            f"/api/cases/{later_case_id}/diagnostic-baseline",
            json={
                "baseline_start": "2026-08-01",
                "baseline_end": "2026-08-31",
                "comparison_start": None,
                "comparison_end": None,
                "seasonality_context": "غير معروف — يحتاج تحقق",
            },
        )
        self.assertEqual(200, baseline_only.status_code, baseline_only.get_data(as_text=True))
        self.assertIsNone(baseline_only.get_json()["data"]["comparison_start"])
        self.assertIsNone(baseline_only.get_json()["data"]["comparison_end"])

        restored = client_a.put(
            f"/api/cases/{later_case_id}/diagnostic-baseline",
            json={
                "baseline_start": "2026-08-01",
                "baseline_end": "2026-08-31",
                "comparison_start": "2026-07-01",
                "comparison_end": "2026-07-31",
                "seasonality_context": "فترة تشغيل اعتيادية",
            },
        )
        self.assertEqual(200, restored.status_code)

        from sana_growth_os import METRIC_DEFINITIONS
        forged_metrics = {
            key: {
                "value": 1,
                "source_ref": f"customer:{key}",
                "confidence": 99,
                "information_type": "Actual",
                "verification_status": "VERIFIED",
                "source_category": "SYSTEM",
            }
            for key, *_ in METRIC_DEFINITIONS
        }
        forged_baseline = client_a.post(
            f"/api/companies/{customer_a['company_id']}/growth-os/baselines",
            json={
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "comparison_start": "2026-07-01",
                "comparison_end": "2026-07-31",
                "observed_at": "2026-09-01",
                "confidence": 99,
                "source_ref": "customer:claimed-system",
                "metrics": forged_metrics,
            },
        )
        self.assertEqual(201, forged_baseline.status_code, forged_baseline.get_data(as_text=True))
        forged_data = forged_baseline.get_json()["data"]
        self.assertFalse(forged_data["usable"])
        self.assertTrue(all(
            metric["verification_status"] == "UNVERIFIED"
            and metric["source_category"] == "SELF_REPORTED"
            for metric in forged_data["metrics"]
        ))
        rejected_validation = client_a.get(
            f"/api/companies/{customer_a['company_id']}/growth-os/baselines/"
            f"{forged_data['baseline_id']}/validate"
        )
        self.assertEqual(422, rejected_validation.status_code)

        today = client_a.get(
            f"/home?company_id={customer_b['company_id']}&view=owner"
        )
        self.assertEqual(200, today.status_code)
        today_html = today.get_data(as_text=True)
        self.assertIn(customer_a["company_id"], today_html)
        self.assertNotIn(customer_b["company_id"], today_html)
        self.assertNotIn('href="/growth-os"', today_html)
        self.assertNotIn("/growth-os/decisions/", today_html)
        self.assertNotIn("التجربة الجارية", today_html)
        self.assertNotIn("تنبيهات التنفيذ", today_html)
        self.assertNotIn("Knowledge Console", today_html)
        self.assertNotIn("Research Library", today_html)
        self.assertNotIn("Deal Brain", today_html)
        self.assertIn("اليوم", today_html)
        self.assertNotIn(">ملف القرار</a>", today_html)

        legacy_new_case = client_a.get("/case/new")
        self.assertEqual(302, legacy_new_case.status_code)
        self.assertTrue(
            legacy_new_case.headers["Location"].endswith(
                f"/case/{later_case_id}"
            )
        )

        decision_file = client_a.get(
            f"/case/{customer_a['case_id']}",
            follow_redirects=True,
        )
        self.assertEqual(200, decision_file.status_code)
        decision_html = decision_file.get_data(as_text=True)
        self.assertNotIn(">ملف القرار</a>", decision_html)
        self.assertIn("وش ظهر لنا؟", decision_html)
        self.assertIn("وش عرفنا؟", decision_html)
        self.assertIn("رتّب الصورة", decision_html)
        self.assertIn("كمّل", decision_html)
        self.assertIn("وش ناقصنا؟", decision_html)
        self.assertIn("وش ظهر لنا؟", decision_html)
        self.assertIn("وش تسوي الآن؟", decision_html)
        self.assertIn("وش تغيّر؟", decision_html)
        self.assertIn("وش ظهر لنا الآن", decision_html)
        self.assertNotIn("ما فيه قرار حتى الآن.", decision_html)
        self.assertNotIn("المعلومة غير متاحة الآن", decision_html)
        self.assertIn("اكتب الرقم أو المعلومة", decision_html)
        self.assertIn("مثال: تقرير المبيعات", decision_html)
        self.assertNotIn("N/A — Deferred", decision_html)
        self.assertNotIn("حفظ الدليل وإعادة التحليل", decision_html)
        self.assertIn("شوف القرار", decision_html)
        self.assertNotIn("Sana Scan", decision_html)
        self.assertNotIn("المعرفة المرجعية — ليست Evidence", decision_html)
        self.assertNotIn("Knowledge Console", decision_html)
        self.assertNotIn("Research Library", decision_html)

        for legacy_path, legal_target in (
            ("/growth-os", "/home"),
            ("/assessment", "/passport"),
            ("/sop-builder", "/home"),
            (
                f"/company/{customer_a['company_id']}/tasks-board",
                "/home",
            ),
            (f"/case/{customer_a['case_id']}/next-step", f"/case/{customer_a['case_id']}"),
        ):
            legacy = client_a.get(legacy_path)
            self.assertEqual(302, legacy.status_code, legacy_path)
            self.assertTrue(
                legacy.headers["Location"].endswith(legal_target),
                (legacy_path, legacy.headers["Location"]),
            )

        null_case = client_a.get("/case/null")
        self.assertEqual(404, null_case.status_code)

        pre_scan_summary = client_a.get(
            f"/api/companies/{customer_a['company_id']}/summary"
        ).get_json()["data"]
        independence_asset_id = next(
            asset["asset_id"]
            for asset in pre_scan_summary["assets"]
            if asset["asset_type"] == "Independence"
        )
        for title, source_ref in (
            (
                "تتوقف بعض العمليات المهمة عند غياب المؤسس",
                "محضر مراجعة التشغيل الأسبوعي",
            ),
            (
                "الشركة لا تعمل بدوني في اعتماد التسليم",
                "سجل تأخر التسليم الشهري",
            ),
        ):
            evidence_response = client_a.post(
                f"/api/companies/{customer_a['company_id']}/evidence",
                json={
                    "case_id": customer_a["case_id"],
                    "asset_id": independence_asset_id,
                    "title": title,
                    "source_type": "دليل عميل",
                    "source_ref": source_ref,
                    "evidence_type": "Evidence",
                    "confidence": 80,
                    "information_type": "Narrative",
                    "verification_status": "VERIFIED",
                    "source_category": "DOCUMENT",
                },
            )
            self.assertIn(
                evidence_response.status_code,
                (200, 201),
                evidence_response.get_data(as_text=True),
            )

        review_db = sana_app._connect_pg()
        try:
            submitted = review_db.execute(
                """SELECT verification_status, source_category
                   FROM evidence WHERE company_id=? AND case_id=?
                     AND source_type='دليل عميل'""",
                (customer_a["company_id"], customer_a["case_id"]),
            ).fetchall()
            self.assertTrue(submitted)
            self.assertTrue(all(
                row["verification_status"] == "UNVERIFIED"
                and row["source_category"] == "SELF_REPORTED"
                for row in submitted
            ))
            # محاكاة خطوة مراجعة مستقلة؛ العميل نفسه لا يستطيع تنفيذ هذه الترقية.
            review_db.execute(
                """UPDATE evidence
                   SET verification_status='VERIFIED', source_category='DOCUMENT'
                   WHERE company_id=? AND case_id=? AND source_type='دليل عميل'""",
                (customer_a["company_id"], customer_a["case_id"]),
            )
            review_db.commit()
        finally:
            review_db.close()

        scan = client_a.post(f"/api/cases/{customer_a['case_id']}/scan")
        self.assertEqual(200, scan.status_code, scan.get_data(as_text=True))
        scan_data = scan.get_json()["data"]
        self.assertEqual(customer_a["company_id"], scan_data["company_id"])
        self.assertFalse(scan_data["ai_used"])
        self.assertTrue(scan_data["human_review_required"])
        self.assertEqual("REVIEW_REQUIRED", scan_data["status"])
        self.assertTrue(scan_data["missing_evidence"])

        supported_diagnostic = {
            "status": "SUPPORTED",
            "message": "نتيجة مدعومة",
            "action_required": "مراجعة بشرية",
            "supported_findings": [
                {
                    "title": "اعتماد مرتفع على المؤسس",
                    "problem": "الدليل يشير إلى اعتماد تشغيلي على المؤسس.",
                    "source": "Sana Knowledge",
                    "version": "test",
                    "decision_rule": {
                        "decision": "اعتماد تجربة تفويض موثقة لمدة 30 يومًا"
                    },
                }
            ],
        }
        with patch.object(
            sana_knowledge, "run_diagnostic", return_value=supported_diagnostic
        ), patch.object(
            sana_app, "ask_sana_ai", return_value={"error": "AI unavailable"}
        ):
            ai_fallback = client_a.post(
                f"/api/cases/{customer_a['case_id']}/analyze"
            )
        self.assertEqual(
            200, ai_fallback.status_code, ai_fallback.get_data(as_text=True)
        )
        ai_body = ai_fallback.get_json()
        self.assertFalse(ai_body["meta"]["ai_used"])
        self.assertEqual("sana_knowledge", ai_body["meta"]["source"])
        self.assertEqual(
            "الدليل يشير إلى اعتماد تشغيلي على المؤسس.",
            ai_body["data"]["presentation"],
        )

        concurrent_client = sana_app.app.test_client()
        concurrent_login = concurrent_client.post(
            "/login",
            json={
                "email": customer_a["email"],
                "password": customer_a["password"],
            },
        )
        self.assertEqual(200, concurrent_login.status_code)
        with ThreadPoolExecutor(max_workers=2) as pool:
            concurrent_results = list(pool.map(
                lambda client: client.post(
                    f"/api/cases/{customer_a['case_id']}/p0-decision"
                ),
                (client_a, concurrent_client),
            ))
        self.assertTrue(all(
            response.status_code in (200, 201)
            for response in concurrent_results
        ))
        self.assertEqual(
            1,
            len({
                response.get_json()["data"]["decision_id"]
                for response in concurrent_results
            }),
        )
        p0_decision = next(
            response for response in concurrent_results
            if response.status_code == 201
        )
        self.assertIn(
            p0_decision.status_code,
            (200, 201),
            p0_decision.get_data(as_text=True),
        )
        decision_data = p0_decision.get_json()["data"]
        decision_id = decision_data["decision_id"]
        self.assertEqual(customer_a["case_id"], decision_data["case_id"])
        self.assertEqual(scan_data["scan_id"], decision_data["scan_id"])
        linked_evidence = json.loads(decision_data["evidence_ids"])
        self.assertTrue(linked_evidence)
        source_ids = {
            str(item["source_id"])
            for item in scan_data["classified_inputs"]
            if item["classification"] in {"Fact", "Evidence"}
        }
        self.assertTrue(set(linked_evidence).issubset(source_ids))

        duplicate_decision = client_a.post(
            f"/api/cases/{customer_a['case_id']}/p0-decision"
        )
        self.assertEqual(200, duplicate_decision.status_code)
        self.assertEqual(
            decision_id, duplicate_decision.get_json()["data"]["decision_id"]
        )

        substituted_provenance = client_a.post(
            f"/api/decisions/{decision_id}/approve",
            json={
                "owner_name": "مالك القرار",
                "due_date": "2026-10-01",
                "success_metric": "مراجعة المؤشرات أسبوعيًا",
                "next_action": "جمع خط أساس",
                "scan_id": "SCAN-FOREIGN",
                "evidence_ids": linked_evidence,
            },
        )
        self.assertEqual(409, substituted_provenance.status_code)
        self.assertEqual(
            "P0_DECISION_PROVENANCE_IMMUTABLE",
            substituted_provenance.get_json()["error"],
        )

        missing_metric = client_a.post(
            f"/api/decisions/{decision_id}/approve",
            json={"owner_name": "مالك القرار", "due_date": "2026-10-01"},
        )
        self.assertEqual(400, missing_metric.status_code)
        self.assertEqual("SUCCESS_METRIC_REQUIRED", missing_metric.get_json()["error"])

        missing_next_action = client_a.post(
            f"/api/decisions/{decision_id}/approve",
            json={
                "owner_name": "مالك القرار",
                "due_date": "2026-10-01",
                "success_metric": "مراجعة المؤشرات أسبوعيًا لمدة 30 يومًا",
            },
        )
        self.assertEqual(400, missing_next_action.status_code)
        self.assertEqual(
            "DECISION_RESPONSIBILITY_FIELDS_REQUIRED",
            missing_next_action.get_json()["error"],
        )

        approved = client_a.post(
            f"/api/decisions/{decision_id}/approve",
            json={
                "owner_name": "مالك القرار",
                "due_date": "2026-10-01",
                "success_metric": "مراجعة المؤشرات أسبوعيًا لمدة 30 يومًا",
                "next_action": "جمع خط أساس هذا الأسبوع",
            },
        )
        self.assertEqual(200, approved.status_code, approved.get_data(as_text=True))
        self.assertEqual("معتمد", approved.get_json()["data"]["status"])
        approved_summary = client_a.get(
            f"/api/companies/{customer_a['company_id']}/summary"
        ).get_json()["data"]
        decision_tasks = [
            task for task in approved_summary["tasks"]
            if task["decision_id"] == decision_id
        ]
        self.assertEqual(1, len(decision_tasks))
        self.assertEqual("جمع خط أساس هذا الأسبوع", decision_tasks[0]["title"])
        self.assertEqual(
            "جمع خط أساس هذا الأسبوع",
            approved_summary["next_task"]["title"],
        )

        report = client_a.get(
            f"/api/companies/{customer_a['company_id']}/passport/report-text"
        )
        self.assertEqual(200, report.status_code, report.get_data(as_text=True))
        self.assertIn("شركة رحلة A", report.get_json()["data"]["report_text"])

        pdf_report = client_a.get(
            f"/api/companies/{customer_a['company_id']}/passport/report-pdf"
        )
        self.assertEqual(302, pdf_report.status_code)
        self.assertIn("/pricing", pdf_report.headers["Location"])
        self.assertIn("feature=report_pdf", pdf_report.headers["Location"])

        own_case = client_a.get(f"/api/cases/{customer_a['case_id']}")
        self.assertEqual(200, own_case.status_code)
        self.assertEqual(
            customer_a["company_id"],
            own_case.get_json()["data"]["case"]["company_id"],
        )

        self.assertEqual(
            403,
            client_a.get(
                f"/api/companies/{customer_b['company_id']}/summary"
            ).status_code,
        )
        self.assertEqual(
            403,
            client_a.get(f"/api/cases/{customer_b['case_id']}").status_code,
        )
        self.assertEqual(
            403,
            client_a.get(f"/case/{customer_b['case_id']}").status_code,
        )
        self.assertEqual(
            403,
            client_b.post(
                f"/api/cases/{customer_a['case_id']}/scan"
            ).status_code,
        )

        overridden = client_a.get(
            f"/home?company_id={customer_b['company_id']}"
        )
        self.assertEqual(200, overridden.status_code)
        page = overridden.get_data(as_text=True)
        self.assertIn(customer_a["company_id"], page)
        self.assertNotIn(customer_b["company_id"], page)

        anonymous = sana_app.app.test_client()
        self.assertEqual(
            401,
            anonymous.get(
                f"/api/companies/{customer_a['company_id']}/summary"
            ).status_code,
        )
        self.assertEqual(
            302,
            anonymous.get(f"/case/{customer_a['case_id']}").status_code,
        )

        client_a.get("/logout")
        invalid_login = client_a.post(
            "/login",
            json={
                "email": customer_a["email"],
                "password": "definitely-wrong-password",
                "next": f"/case/{customer_a['case_id']}",
            },
        )
        self.assertEqual(401, invalid_login.status_code)
        self.assertEqual(
            "INVALID_CREDENTIALS",
            invalid_login.get_json()["error"],
        )
        login = client_a.post(
            "/login",
            json={
                "email": customer_a["email"],
                "password": customer_a["password"],
                "next": f"/case/{customer_a['case_id']}",
            },
        )
        self.assertEqual(200, login.status_code)
        self.assertEqual(
            f"/case/{customer_a['case_id']}",
            login.get_json()["data"]["redirect"],
        )
        post_login_case = client_a.get(f"/case/{customer_a['case_id']}")
        self.assertEqual(302, post_login_case.status_code)
        self.assertTrue(
            post_login_case.headers["Location"].endswith(
                f"/case/{customer_a['case_id']}/result"
            )
        )
        self.assertEqual(200, client_a.get("/home").status_code)

    def test_new_acquisition_impact_is_saved_as_self_report_and_links_risk(self):
        customer = self._new_customer("acquisition-impact")
        db = sana_app._connect_pg()
        try:
            evidence = db.execute(
                """SELECT title, verification_status, source_category
                   FROM evidence
                   WHERE company_id=? AND source_ref='SDS-001 Q4 follow-up'""",
                (customer["company_id"],),
            ).fetchone()
            self.assertEqual(
                "هشاشة مصدر العملاء: 😟 انخفاض متوسط",
                evidence["title"],
            )
            self.assertEqual("UNVERIFIED", evidence["verification_status"])
            self.assertEqual("SELF_REPORTED", evidence["source_category"])

            framework = db.execute(
                """SELECT framework_id FROM case_frameworks
                   WHERE company_id=? AND case_id=?""",
                (customer["company_id"], customer["case_id"]),
            ).fetchone()
            self.assertEqual("sana-acquisition-system", framework["framework_id"])

            memory = retrieve_memory(
                db,
                customer["company_id"],
                memory_keys=["acquisition:fragility"],
            )
            self.assertEqual(1, len(memory["items"]))
            self.assertEqual("😟 انخفاض متوسط", memory["items"][0]["value"])
            self.assertTrue(memory["items"][0]["needs_confirmation"])
        finally:
            db.close()

    def test_internal_preview_does_not_create_customer_session(self):
        customer = self._new_customer("preview")
        preview_client = sana_app.app.test_client()

        with patch.object(sana_app, "ADMIN_PREVIEW_KEY", "preview-secret"), patch.dict(
            sana_app.os.environ, {"ADMIN_PREVIEW_KEY": "preview-secret"}
        ):
            preview = preview_client.get(
                "/discovery",
                query_string={
                    "admin_key": "preview-secret",
                    "company_id": customer["company_id"],
                },
            )
            self.assertEqual(200, preview.status_code)

            session_status = preview_client.get("/api/session").get_json()["data"]
            self.assertFalse(session_status["authenticated"])
            self.assertFalse(session_status["admin_preview"])

    def test_decision_maker_followup_is_specific_optional_and_conditionally_hidden(self):
        template = (
            Path(__file__).parent / "templates" / "06-sana-discovery.html"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "لو غاب صاحب القرار شهرًا، ما أول عملية ستتباطأ أو تتوقف؟ "
            "اذكر مثالًا واقعيًا يساعدنا نحدد أين يبدأ التحسين. (اختياري)",
            template,
        )
        self.assertIn("مثال: اعتماد الأسعار أو متابعة العملاء", template)
        self.assertIn("hideFollowupFor: ['😎 العمل يستمر طبيعيًا']", template)
        self.assertIn("const showFu = shouldShowFollowup(q, chosen);", template)

    def test_sales_pipeline_is_not_public(self):
        anonymous = sana_app.app.test_client()
        response = anonymous.get("/sales")
        self.assertEqual(302, response.status_code)
        self.assertIn("/login", response.headers["Location"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
