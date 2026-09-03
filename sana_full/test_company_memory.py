import json
import os
import secrets
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from flask import render_template

os.environ.setdefault("SKIP_DB_INIT", "1")

import app as sana_app
from sana_company_memory import (
    capture_discovery,
    confirm_memory,
    ensure_schema,
    record_learning,
    record_memory,
    resolve_memory_conflict,
    retrieve_memory,
)


class CompanyMemoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sana_app.init_db()

    def setUp(self):
        self.app_context = sana_app.app.app_context()
        self.app_context.push()
        self.db = sana_app.get_db()
        ensure_schema(self.db)
        token = secrets.token_hex(5).upper()
        self.company_a = f"C-MEMA-{token}"
        self.company_b = f"C-MEMB-{token}"
        self.case_a = f"CASE-MEMA-{token}"
        self.case_b = f"CASE-MEMB-{token}"
        for company_id, name in (
            (self.company_a, "شركة ذاكرة أ"),
            (self.company_b, "شركة ذاكرة ب"),
        ):
            self.db.execute(
                "INSERT INTO companies (company_id,name) VALUES (?,?)",
                (company_id, name),
            )
        self.db.execute(
            """INSERT INTO cases
               (case_id,company_id,case_title,declared_problem,real_question,case_status)
               VALUES (?,?,?,?,?,?)""",
            (self.case_a, self.company_a, "قضية أ", "تذبذب التحويل", "لماذا؟", "Open"),
        )
        self.db.execute(
            """INSERT INTO cases
               (case_id,company_id,case_title,declared_problem,real_question,case_status)
               VALUES (?,?,?,?,?,?)""",
            (self.case_b, self.company_b, "قضية ب", "قضية أخرى", "لماذا؟", "Open"),
        )
        self.db.commit()

    def tearDown(self):
        for company_id in (self.company_a, self.company_b):
            self.db.execute(
                "DELETE FROM company_memory_conflicts WHERE company_id=?", (company_id,)
            )
            self.db.execute(
                "DELETE FROM company_memory_links WHERE company_id=?", (company_id,)
            )
            self.db.execute(
                "DELETE FROM company_memory_versions WHERE company_id=?", (company_id,)
            )
            self.db.execute(
                "DELETE FROM company_memory_items WHERE company_id=?", (company_id,)
            )
            self.db.execute("DELETE FROM cases WHERE company_id=?", (company_id,))
            self.db.execute("DELETE FROM companies WHERE company_id=?", (company_id,))
        self.db.commit()
        self.app_context.pop()

    def _record(self, company_id=None, **overrides):
        values = {
            "memory_key": "kpi:conversion",
            "memory_type": "current_state",
            "value": {"value": 12, "unit": "%"},
            "source_ref": "analytics:verified",
            "observed_at": date.today(),
            "period_start": date.today() - timedelta(days=30),
            "period_end": date.today(),
            "source_type": "analytics",
            "verification_status": "VERIFIED",
            "freshness_class": "FAST",
            "source_strength": 90,
            "verification_confidence": 85,
            "freshness_confidence": 95,
            "case_id": self.case_a,
            "context": {"problem": "تذبذب التحويل", "kpi": "conversion"},
        }
        values.update(overrides)
        return record_memory(self.db, company_id or self.company_a, **values)

    def test_reuse_history_staleness_and_unknown(self):
        first = self._record()
        current = retrieve_memory(
            self.db, self.company_a, case_id=self.case_a, kpi="conversion"
        )
        self.assertEqual(current["summary"]["reusable"], 1)
        self.assertFalse(current["items"][0]["needs_confirmation"])
        self.assertEqual(current["items"][0]["source_ref"], "analytics:verified")
        self.assertEqual(
            current["items"][0]["confidence"],
            {"source_strength": 90, "verification": 85, "freshness": 95},
        )

        confirmed = confirm_memory(
            self.db, self.company_a, first["memory_id"], actor_id="ACC-OWNER",
        )
        self.assertNotEqual(confirmed["version_id"], first["version_id"])
        history = retrieve_memory(
            self.db, self.company_a, memory_keys=["kpi:conversion"],
            include_history=True,
        )
        self.assertEqual(len(history["items"]), 2)
        self.assertEqual(sum(1 for item in history["items"] if item["is_current"]), 1)

        stale_company = f"{self.company_a}-STALE"
        stale_case = f"{self.case_a}-STALE"
        self.db.execute(
            "INSERT INTO companies (company_id,name) VALUES (?,?)", (stale_company, "قديم")
        )
        self.db.execute(
            """INSERT INTO cases
               (case_id,company_id,case_title,declared_problem,real_question,case_status)
               VALUES (?,?,?,?,?,?)""",
            (stale_case, stale_company, "قديم", "قديم", "؟", "Open"),
        )
        self._record(
            company_id=stale_company, case_id=stale_case,
            observed_at=date.today() - timedelta(days=100),
            period_start=date.today() - timedelta(days=130),
            period_end=date.today() - timedelta(days=100),
        )
        stale = retrieve_memory(self.db, stale_company)
        self.assertTrue(stale["items"][0]["needs_confirmation"])
        self.assertFalse(stale["items"][0]["reusable"])
        self.assertEqual(stale["items"][0]["freshness"], "STALE")
        unknown = retrieve_memory(
            self.db, self.company_a, memory_keys=["missing:key"]
        )
        self.assertTrue(unknown["unknown"])
        self.db.execute("DELETE FROM company_memory_versions WHERE company_id=?", (stale_company,))
        self.db.execute("DELETE FROM company_memory_items WHERE company_id=?", (stale_company,))
        self.db.execute("DELETE FROM cases WHERE company_id=?", (stale_company,))
        self.db.execute("DELETE FROM companies WHERE company_id=?", (stale_company,))

    def test_conflict_never_replaces_current_without_review(self):
        first = self._record()
        incoming = self._record(
            value={"value": 18, "unit": "%"}, source_ref="crm:verified",
            source_type="crm",
        )
        self.assertEqual(incoming["status"], "CONFLICT")
        self.assertEqual(incoming["current_version_id"], first["version_id"])
        memory = retrieve_memory(self.db, self.company_a)
        self.assertEqual(memory["summary"]["conflicts"], 1)
        self.assertFalse(memory["items"][0]["reusable"])
        resolved = resolve_memory_conflict(
            self.db, self.company_a, incoming["conflict_id"],
            action="accept_incoming", actor_id="ACC-REVIEWER",
            reason="تمت مطابقة تعريف KPI مع المصدر.",
        )
        self.assertEqual(resolved["current_version_id"], incoming["version_id"])
        current = retrieve_memory(self.db, self.company_a)
        self.assertEqual(current["items"][0]["value"]["value"], 18)
        self.assertEqual(current["summary"]["conflicts"], 0)

    def test_discovery_confirmation_rejects_open_conflict_without_new_version(self):
        first = self._record()
        incoming = self._record(
            value={"value": 18, "unit": "%"}, source_ref="crm:verified",
            source_type="crm",
        )
        self.assertEqual("CONFLICT", incoming["status"])
        self.db.commit()
        before_versions = self.db.execute(
            "SELECT COUNT(*) AS c FROM company_memory_versions WHERE memory_id=?",
            (first["memory_id"],),
        ).fetchone()["c"]
        before_conflicts = self.db.execute(
            "SELECT COUNT(*) AS c FROM company_memory_conflicts WHERE memory_id=?",
            (first["memory_id"],),
        ).fetchone()["c"]
        memory = retrieve_memory(self.db, self.company_a)
        shown = memory["items"][0]
        self.assertEqual(first["version_id"], shown["version_id"])
        self.assertEqual("OPEN", shown["conflict_status"])

        client = sana_app.app.test_client()
        path = f"/api/companies/{self.company_a}/memory/{first['memory_id']}/confirm"
        with patch.object(sana_app, "enforce_entity_company_scope", return_value=None), \
             patch.object(
                 sana_app, "current_account",
                 return_value={"account_id": "ACC-MEMORY", "company_id": self.company_a},
             ), patch.dict(sana_app.app.config, {"WTF_CSRF_ENABLED": False}):
            response = client.post(path, json={
                "reason": "تأكيد القيمة المعروضة",
                "presented_version_id": shown["version_id"],
            })
        self.assertEqual(400, response.status_code)
        self.assertEqual(
            "MEMORY_CONFLICT_REVIEW_REQUIRED", response.get_json()["error"]
        )
        self.assertEqual(
            before_versions,
            self.db.execute(
                "SELECT COUNT(*) AS c FROM company_memory_versions WHERE memory_id=?",
                (first["memory_id"],),
            ).fetchone()["c"],
        )
        self.assertEqual(
            before_conflicts,
            self.db.execute(
                "SELECT COUNT(*) AS c FROM company_memory_conflicts WHERE memory_id=?",
                (first["memory_id"],),
            ).fetchone()["c"],
        )

    def test_context_and_company_isolation(self):
        self._record()
        self._record(
            company_id=self.company_b, case_id=self.case_b,
            memory_key="goal:retention", memory_type="goal",
            value="زيادة الاحتفاظ", source_ref="case-b",
            context={"problem": "قضية أخرى", "kpi": "retention"},
            period_start=None, period_end=None,
        )
        context = retrieve_memory(
            self.db, self.company_a, case_id=self.case_a,
            problem="تذبذب التحويل", kpi="conversion",
        )
        self.assertEqual(len(context["items"]), 1)
        self.assertEqual(context["items"][0]["match_reason"], "linked_case")
        foreign = retrieve_memory(
            self.db, self.company_b, memory_keys=["kpi:conversion"]
        )
        self.assertTrue(foreign["unknown"])
        with self.assertRaises(ValueError):
            self._record(company_id=self.company_b, case_id=self.case_a)

    def test_discovery_prefill_is_unverified_until_confirmed(self):
        captured = capture_discovery(
            self.db, self.company_a, case_id=self.case_a,
            answers={"q1": "النمو", "q2": "تذبذب التحويل"},
        )
        self.assertEqual(len(captured), 2)
        memory = retrieve_memory(self.db, self.company_a)
        self.assertEqual(memory["summary"]["reusable"], 0)
        self.assertTrue(all(item["needs_confirmation"] for item in memory["items"]))
        self.assertTrue(all(not item["is_current"] for item in memory["items"]))

    def test_discovery_confirmation_url_targets_tenant_and_verifies_version(self):
        capture_discovery(
            self.db, self.company_a, case_id=self.case_a,
            answers={"q1": "النمو"},
        )
        memory = retrieve_memory(self.db, self.company_a)
        item = memory["items"][0]
        with sana_app.app.test_request_context("/discovery"):
            html = render_template(
                "06-sana-discovery.html", full_reassessment=False,
                company_memory=memory, company_id=self.company_a,
            )
        expected_path = (
            f"/api/companies/{self.company_a}/memory/"
            f"{item['memory_id']}/confirm"
        )
        self.assertIn(
            f"/api/companies/{self.company_a}/memory/", html
        )
        self.assertIn("encodeURIComponent(memoryId)", html)
        self.assertIn('name="csrf-token"', html)
        self.assertIn("'X-CSRFToken':t", html)

        client = sana_app.app.test_client()
        with patch.object(sana_app, "enforce_entity_company_scope", return_value=None), \
             patch.object(
                 sana_app, "current_account",
                 return_value={"account_id": "ACC-MEMORY", "company_id": self.company_a},
             ), patch.dict(sana_app.app.config, {"WTF_CSRF_ENABLED": False}):
            response = client.post(
                expected_path, json={
                    "reason": "تأكيد عبر Discovery",
                    "presented_version_id": item["version_id"],
                }
            )
        self.assertEqual(200, response.status_code, response.get_data(as_text=True))
        verified = retrieve_memory(
            self.db, self.company_a, memory_keys=["goal:primary"]
        )
        self.assertEqual(1, verified["summary"]["reusable"])
        self.assertTrue(verified["items"][0]["is_current"])
        self.assertEqual("VERIFIED", verified["items"][0]["verification_status"])

    def test_closed_case_learning_stays_private_and_out_of_public_knowledge(self):
        before = self.db.execute(
            "SELECT COUNT(*) AS c FROM knowledge_objects"
        ).fetchone()["c"]
        self.db.execute(
            "UPDATE cases SET case_status='Closed' WHERE case_id=? AND company_id=?",
            (self.case_a, self.company_a),
        )
        record_learning(
            self.db, self.company_a, case_id=self.case_a,
            changed=["ارتفع التحويل"], unchanged=["حجم الزيارات"],
            hypothesis_correct=True, decision_useful=True,
            execution_complete=True, remember="التجربة نافعة في هذا السياق فقط",
            source_ref="review:closed-case",
        )
        memory = retrieve_memory(self.db, self.company_a, case_id=self.case_a)
        learning = next(item for item in memory["items"] if item["memory_type"] == "learning")
        self.assertTrue(learning["context"]["private_client_learning"])
        after = self.db.execute(
            "SELECT COUNT(*) AS c FROM knowledge_objects"
        ).fetchone()["c"]
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()