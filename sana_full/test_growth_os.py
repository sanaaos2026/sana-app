"""اختبارات تراجع لنواة Business Growth OS.

كل اختبار يعمل داخل معاملة ويُرجعها، لذلك لا يضيف بيانات اختبار دائمة.
"""
import os
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import _connect_pg
from sana_growth_os import (
    GOS_VERSION,
    NA_DEFERRED,
    METRIC_DEFINITIONS,
    PROJECT_PROFILES,
    create_baseline,
    create_truth_record,
    ensure_schema,
    get_company_profile,
    list_profiles,
    register_canonical_entity,
    require_usable_baseline,
    seed_growth_os,
)


class GrowthOSRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL غير مضبوط")
        cls.db = _connect_pg()
        cls.db.execute("BEGIN")
        ensure_schema(cls.db)
        seed_growth_os(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.rollback()
        cls.db.close()

    def test_profiles_are_versioned_and_idempotent(self):
        first = list_profiles(self.db)
        seed_growth_os(self.db)
        second = list_profiles(self.db)
        self.assertEqual(6, len(first))
        self.assertEqual(first, second)
        self.assertTrue(all(row["version"] == GOS_VERSION for row in second))
        self.assertEqual("b2b_services", get_company_profile(self.db, "C001")["profile_key"])

    def test_truth_requires_classification_source_date_and_confidence(self):
        with self.assertRaisesRegex(ValueError, "source_ref"):
            create_truth_record(self.db, "C001", {
                "subject": "عدد العملاء", "value": 3, "classification": "Fact",
                "observed_at": "2026-08-01", "confidence": 90,
            })
        with self.assertRaises(ValueError):
            create_truth_record(self.db, "C001", {
                "subject": "عدد العملاء", "value": 3, "classification": "Unknown",
                "source_ref": "crm:report", "observed_at": "2026-08-01", "confidence": 90,
            })
        row = create_truth_record(self.db, "C001", {
            "subject": "عدد العملاء", "value": 3, "classification": "Fact",
            "source_ref": "crm:report:2026-08", "observed_at": "2026-08-01", "confidence": 90,
        })
        self.assertEqual("Fact", row["classification"])
        self.assertEqual("crm:report:2026-08", row["source_ref"])

    def test_incomplete_baseline_preserves_unavailable_as_not_zero(self):
        baseline = create_baseline(self.db, "C001", {
            "period_start": "2026-08-01", "period_end": "2026-08-31",
            "observed_at": "2026-09-01", "confidence": 70, "source_ref": "finance:august",
            "metrics": {"leads": {"value": 12, "source_ref": "crm:august"}},
        })
        self.assertEqual("incomplete", baseline["status"])
        self.assertFalse(baseline["usable"])
        leads = next(item for item in baseline["metrics"] if item["metric_key"] == "leads")
        revenue = next(item for item in baseline["metrics"] if item["metric_key"] == "revenue")
        self.assertEqual(12, float(leads["value_numeric"]))
        self.assertEqual("available", leads["availability"])
        self.assertIsNone(revenue["value_numeric"])
        self.assertEqual("unavailable", revenue["availability"])
        self.assertEqual(NA_DEFERRED, revenue["source_ref"])
        with self.assertRaisesRegex(ValueError, "BASELINE_INCOMPLETE"):
            require_usable_baseline(self.db, "C001", baseline["baseline_id"])

    def test_complete_baseline_is_usable_only_when_every_metric_has_evidence(self):
        metrics = {
            key: {
                "value": 1,
                "source_ref": f"ledger:{key}",
                "confidence": 85,
                "information_type": "Actual",
                "verification_status": "VERIFIED",
                "source_category": "SYSTEM",
            }
            for key, *_ in METRIC_DEFINITIONS
        }
        baseline = create_baseline(self.db, "C001", {
            "period_start": "2026-08-01", "period_end": "2026-08-31",
            "observed_at": "2026-09-01", "confidence": 85, "source_ref": "ledger:august",
            "metrics": metrics,
        })
        self.assertEqual("complete", baseline["status"])
        self.assertTrue(baseline["usable"])
        self.assertTrue(require_usable_baseline(self.db, "C001", baseline["baseline_id"]))

    def test_canonical_mapping_is_idempotent_and_rejects_identity_drift(self):
        payload = {
            "entity_type": "customer", "source_table": "companies",
            "source_id": "C001", "identity_key": "company:C001",
        }
        first, created = register_canonical_entity(self.db, "C001", payload)
        self.assertTrue(created)
        second, created_again = register_canonical_entity(self.db, "C001", payload)
        self.assertFalse(created_again)
        self.assertEqual(first["canonical_id"], second["canonical_id"])
        with self.assertRaisesRegex(ValueError, "DUPLICATE_CANONICAL_ENTITY"):
            register_canonical_entity(self.db, "C001", {
                **payload, "identity_key": "company:renamed-C001",
            })


if __name__ == "__main__":
    unittest.main()