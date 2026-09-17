import json
import unittest
import uuid

import app as sana_app


class ReturningCheckinAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sana_app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        sana_app.init_db()

    def setUp(self):
        self.db = sana_app._connect_pg()
        suffix = uuid.uuid4().hex[:10].upper()
        self.company_id = f"RC{suffix}"
        self.other_company_id = f"RO{suffix}"
        self.account_id = f"RA{suffix}"
        self.case_id = f"RK{suffix}"
        self.decision_id = f"RD{suffix}"
        self.task_id = f"RT{suffix}"
        for company_id, name in (
            (self.company_id, "شركة متابعة"),
            (self.other_company_id, "شركة أخرى"),
        ):
            self.db.execute(
                """INSERT INTO companies
                   (company_id,name,sector,signup_code,sds_done,main_goal,success_criteria)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    company_id, name, "خدمات", f"TEST-{company_id}", 1,
                    "زيادة الإيرادات", "مبيعات أعلى",
                ),
            )
        self.db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id,admin_role,account_status)
               VALUES (?,?,?,?,?,?)""",
            (
                self.account_id, f"{self.account_id.lower()}@test.local",
                "not-used", self.company_id, "USER", "active",
            ),
        )
        self.db.execute(
            """INSERT INTO cases
               (case_id,company_id,case_title,case_type,case_status,declared_problem)
               VALUES (?,?,?,?,?,?)""",
            (
                self.case_id, self.company_id, "ضعف إغلاق الفرص", "تشخيص",
                "مفتوح", "📉 المبيعات",
            ),
        )
        self.db.execute(
            """INSERT INTO decisions
               (decision_id,company_id,case_id,title,recommended_action,status,
                success_metric,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                self.decision_id, self.company_id, self.case_id,
                "تحسين متابعة الفرص", "مراجعة الفرص أسبوعيًا", "معتمد",
                "عدد الفرص المغلقة", "2026-08-01 00:00:00",
            ),
        )
        self.db.execute(
            """INSERT INTO tasks
               (task_id,company_id,decision_id,title,status,kpi,created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                self.task_id, self.company_id, self.decision_id,
                "مراجعة الفرص أسبوعيًا", "قيد التنفيذ",
                "عدد الفرص المغلقة", "2026-08-02 00:00:00",
            ),
        )
        self.db.execute(
            """INSERT INTO evidence
               (evidence_id,company_id,case_id,title,source_type,confidence,
                evidence_type,source_ref,information_type,verification_status,
                source_category,period_start,period_end,raw_value,normalized_value,
                unit,topic_key,seasonality_context)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"RE{suffix}", self.company_id, self.case_id,
                "عدد الفرص المغلقة: 10 فرصة", "اكتشاف_ذاتي", 50,
                "Evidence", "SDS-001 NUMERIC", "Metric", "UNVERIFIED",
                "SELF_REPORTED", "2026-07-01", "2026-07-31", "10", 10,
                "فرصة", "closed_opportunities", "فترة تشغيل اعتيادية",
            ),
        )
        self.db.commit()
        self.client = sana_app.app.test_client()
        with self.client.session_transaction() as session:
            session["account_id"] = self.account_id
            session["company_id"] = self.company_id
            session["email"] = f"{self.account_id.lower()}@test.local"

    def tearDown(self):
        self.db.rollback()
        self.db.execute(
            "DELETE FROM returning_checkins WHERE company_id IN (?,?)",
            (self.company_id, self.other_company_id),
        )
        self.db.execute(
            "DELETE FROM evidence WHERE company_id IN (?,?)",
            (self.company_id, self.other_company_id),
        )
        self.db.execute("DELETE FROM tasks WHERE company_id=?", (self.company_id,))
        self.db.execute("DELETE FROM decisions WHERE company_id=?", (self.company_id,))
        self.db.execute("DELETE FROM cases WHERE company_id=?", (self.company_id,))
        self.db.execute("DELETE FROM user_accounts WHERE account_id=?", (self.account_id,))
        self.db.execute(
            "DELETE FROM companies WHERE company_id IN (?,?)",
            (self.company_id, self.other_company_id),
        )
        self.db.commit()
        self.db.close()

    def test_returning_path_selects_only_relevant_questions_and_compares_kpi(self):
        page = self.client.get("/check-in")
        self.assertEqual(200, page.status_code)
        self.assertIn("وش تغير من آخر مرة؟", page.get_data(as_text=True))
        started = self.client.post("/api/returning-checkin")
        self.assertEqual(201, started.status_code, started.get_data(as_text=True))
        data = started.get_json()["data"]
        questions = data["questions"]
        self.assertLessEqual(len(questions), 3)
        ids = {item["id"] for item in questions}
        self.assertIn("challenge_change", ids)
        self.assertIn("task_execution", ids)
        self.assertIn("kpi_current", ids)
        self.assertFalse(ids.intersection({"q1", "q2", "q3", "q4", "q5", "q6", "q7"}))
        answers = []
        for question in questions:
            if question["id"] == "challenge_change":
                answers.append({"question_id": question["id"], "value": "صار أفضل"})
            elif question["id"] == "task_execution":
                answers.append({"question_id": question["id"], "value": "نعم"})
            elif question["id"] == "kpi_current":
                answers.append({
                    "question_id": question["id"], "numeric_value": 15,
                    "period_start": "2026-08-01", "period_end": "2026-08-31",
                    "seasonality_context": "فترة تشغيل اعتيادية",
                })
        completed = self.client.post(
            f"/api/returning-checkin/{data['checkin_id']}", json={"answers": answers}
        )
        self.assertEqual(200, completed.status_code, completed.get_data(as_text=True))
        comparison = completed.get_json()["data"]["comparison"]
        self.assertTrue(comparison["comparable"])
        self.assertEqual(10.0, comparison["before"]["value"])
        self.assertEqual(15.0, comparison["after"]["value"])
        self.assertEqual(5.0, comparison["change"]["absolute"])
        self.assertEqual(f"/case/{self.case_id}", completed.get_json()["data"]["next_url"])

    def test_incomparable_period_does_not_claim_change_and_tenant_isolation_holds(self):
        started = self.client.post("/api/returning-checkin").get_json()["data"]
        answers = []
        for question in started["questions"]:
            if question["id"] == "kpi_current":
                answers.append({
                    "question_id": question["id"], "numeric_value": 99,
                    "period_start": "2026-07-15", "period_end": "2026-08-15",
                    "seasonality_context": "فترة تشغيل اعتيادية",
                })
            elif question["id"] == "task_execution":
                answers.append({"question_id": question["id"], "value": "لا"})
            elif question["id"] == "challenge_change":
                answers.append({"question_id": question["id"], "value": "مثل ما هو"})
        completed = self.client.post(
            f"/api/returning-checkin/{started['checkin_id']}",
            json={"answers": answers},
        )
        self.assertEqual(200, completed.status_code)
        comparison = completed.get_json()["data"]["comparison"]
        self.assertFalse(comparison["comparable"])
        self.assertIsNone(comparison["change"])
        foreign_id = "RCI-" + uuid.uuid4().hex[:12].upper()
        self.db.execute(
            """INSERT INTO returning_checkins
               (checkin_id,company_id,questions_json) VALUES (?,?,?)""",
            (foreign_id, self.other_company_id, "[]"),
        )
        self.db.commit()
        foreign = self.client.post(
            f"/api/returning-checkin/{foreign_id}", json={"answers": []}
        )
        self.assertEqual(404, foreign.status_code)


class NewUserDiscoveryRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sana_app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    def test_full_discovery_question_set_remains_present(self):
        with open("templates/06-sana-discovery.html", encoding="utf-8") as source:
            template = source.read()
        for question_id in ("q1", "q2", "q3", "q4", "q5", "q6", "q7"):
            self.assertIn(f"id: '{question_id}'", template)