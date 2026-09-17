"""اختبار قبول منع الدوران في طلبات الأدلة الحرجة."""

import unittest
import uuid
from unittest.mock import patch

import app as sana_app
from sana_scan import run_scan


class EvidenceRequestCycleAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sana_app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        sana_app.init_db()

    def setUp(self):
        self.db = sana_app._connect_pg()
        suffix = uuid.uuid4().hex[:10].upper()
        self.company_id = f"EVC{suffix}"
        self.case_id = f"CASEEVC{suffix}"
        self.asset_ids = {
            asset_type: f"{asset_type[:2].upper()}EVC{suffix}"
            for asset_type in ("Knowledge", "Operations", "Brand", "Data", "Independence")
        }
        self.db.execute(
            """INSERT INTO companies (company_id,name,sector)
               VALUES (?,?,?)""",
            (self.company_id, "شركة اختبار دورة الأدلة", "خدمات B2B"),
        )
        for asset_type, asset_id in self.asset_ids.items():
            self.db.execute(
                """INSERT INTO assets
                   (asset_id,company_id,asset_type,asset_name,current_score,
                    fragility_score,status)
                   VALUES (?,?,?,?,?,?,?)""",
                (asset_id, self.company_id, asset_type, asset_type, 40, 50, "تحت المراجعة"),
            )
        self.db.execute(
            """INSERT INTO cases
               (case_id,company_id,case_title,case_type,case_status,
                declared_problem,real_question,related_asset_id,confidence_score)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                self.case_id, self.company_id, "دورة دليل محدودة", "تشخيص", "Open",
                "نحتاج معلومة مستقلة", "هل يوجد سبب قابل للإثبات؟",
                self.asset_ids["Operations"], 45,
            ),
        )
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        scan_ids = [
            row["scan_id"]
            for row in self.db.execute(
                "SELECT scan_id FROM scan_runs WHERE company_id=?", (self.company_id,)
            ).fetchall()
        ]
        if scan_ids:
            placeholders = ",".join("?" for _ in scan_ids)
            self.db.execute(
                f"DELETE FROM scan_findings WHERE scan_id IN ({placeholders})",
                scan_ids,
            )
        for table in ("scan_runs", "evidence_relations", "evidence", "cases", "assets"):
            self.db.execute(f"DELETE FROM {table} WHERE company_id=?", (self.company_id,))
        self.db.execute("DELETE FROM companies WHERE company_id=?", (self.company_id,))
        self.db.commit()
        self.db.close()

    def test_cycle_caps_questions_and_keeps_fingerprints(self):
        first = run_scan(self.db, self.case_id)
        second = run_scan(self.db, self.case_id)

        first_cycle = first["evidence_request_cycle"]
        second_cycle = second["evidence_request_cycle"]
        self.assertEqual(2, first_cycle["critical_request_limit"])
        self.assertEqual(2, first_cycle["critical_request_count"])
        self.assertEqual(
            first_cycle["requested_fingerprints"],
            second_cycle["requested_fingerprints"],
        )
        self.assertEqual(
            first_cycle["pending_requests"],
            second_cycle["pending_requests"],
        )
        self.assertEqual(2, len(second["evidence_requests"]))
        self.assertEqual(
            len(set(second_cycle["requested_fingerprints"])),
            len(second_cycle["requested_fingerprints"]),
        )

    def test_unknown_answer_is_evidence_and_closes_journey(self):
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
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual("UNKNOWN", payload["data"]["journey_outcome"])
        self.assertEqual("UNKNOWN", payload["data"]["scan"]["evidence_request_cycle"]["outcome"])
        self.assertEqual([], payload["data"]["scan"]["evidence_requests"])
        self.assertEqual(1, payload["meta"]["reevaluated"])

        saved = self.db.execute(
            """SELECT evidence_type,source_category,verification_status
               FROM evidence WHERE case_id=? ORDER BY date_collected DESC LIMIT 1""",
            (self.case_id,),
        ).fetchone()
        self.assertEqual("Evidence", saved["evidence_type"])
        self.assertEqual("UNKNOWN", saved["source_category"])
        self.assertEqual("UNVERIFIED", saved["verification_status"])

        with patch.object(sana_app, "enforce_entity_company_scope", return_value=None):
            with sana_app.app.test_request_context(
                "/api/companies/evidence", method="POST", json={
                    "case_id": self.case_id,
                    "unknown": True,
                    "request_fingerprint": request["fingerprint"],
                }
            ):
                repeated = sana_app.add_evidence(self.company_id)
        repeated_response, repeated_status = repeated
        self.assertEqual(409, repeated_status)
        self.assertEqual(
            "EVIDENCE_REQUEST_NOT_AVAILABLE",
            repeated_response.get_json()["error"],
        )


if __name__ == "__main__":
    unittest.main()