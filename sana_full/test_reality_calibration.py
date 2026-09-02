import unittest
from decimal import Decimal

from sana_reliability import (
    diagnostic_quality,
    parse_diagnostic_number,
    triangulate_sources,
    validate_context,
)


class RealityCalibrationContractTest(unittest.TestCase):
    def test_arabic_decimal_and_zero_are_values_not_missing(self):
        self.assertEqual(Decimal("0"), parse_diagnostic_number("٠"))
        self.assertEqual(Decimal("1250.5"), parse_diagnostic_number("١٬٢٥٠٫٥"))
        self.assertEqual(Decimal("12.50"), parse_diagnostic_number("12.50"))

    def test_ambiguous_and_invalid_values_are_rejected_in_arabic(self):
        for value in ("", "1,25", "12 ريال", None, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_diagnostic_number(value)

    def test_discovery_number_stays_self_reported_and_unverified(self):
        result = validate_context({
            "value": "٠",
            "information_type": "Actual",
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "source_ref": "إفادة المؤسس",
            "unit": "عميل",
            "verification_status": "VERIFIED",
            "source_category": "SYSTEM",
        }, discovery=True, numeric=True)
        self.assertEqual("UNVERIFIED", result["verification_status"])
        self.assertEqual("SELF_REPORTED", result["source_category"])
        self.assertEqual(Decimal("0"), result["normalized_value"])

    def test_numeric_context_requires_meaning_period_source_and_unit(self):
        for missing in ("information_type", "period_start", "source_ref", "unit"):
            payload = {
                "value": "10",
                "information_type": "Actual",
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "source_ref": "ledger:august",
                "unit": "عميل",
            }
            payload.pop(missing)
            with self.subTest(missing=missing):
                with self.assertRaises(ValueError):
                    validate_context(payload, numeric=True)

    def test_actual_forecast_and_target_do_not_triangulate_together(self):
        base = {
            "topic_key": "customers",
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "unit": "count",
            "verification_status": "VERIFIED",
            "source_ref": "ref",
        }
        sources = [
            {**base, "source_id": "actual", "information_type": "Actual",
             "source_category": "SYSTEM", "normalized_value": 10},
            {**base, "source_id": "target", "information_type": "Target",
             "source_category": "DOCUMENT", "normalized_value": 20},
        ]
        result = triangulate_sources(sources)
        self.assertEqual([], result["agreements"])
        self.assertEqual([], result["conflicts"])

    def test_disagreement_opens_neutral_conflict_without_winner(self):
        sources = [
            {
                "source_id": "company", "topic_key": "customers",
                "information_type": "Actual", "period_start": "2026-08-01",
                "period_end": "2026-08-31", "unit": "count",
                "source_category": "SELF_REPORTED", "normalized_value": 10,
                "verification_status": "UNVERIFIED", "source_ref": "interview",
            },
            {
                "source_id": "ledger", "topic_key": "customers",
                "information_type": "Actual", "period_start": "2026-08-01",
                "period_end": "2026-08-31", "unit": "count",
                "source_category": "SYSTEM", "normalized_value": 8,
                "verification_status": "VERIFIED", "source_ref": "ledger",
            },
        ]
        conflicts = triangulate_sources(sources)["conflicts"]
        self.assertEqual(1, len(conflicts))
        self.assertEqual("CONTRADICTED", conflicts[0]["status"])
        self.assertEqual({"company", "ledger"}, set(conflicts[0]["source_ids"]))
        self.assertIn("دون افتراض", conflicts[0]["verification_question"])
        quality = diagnostic_quality(sources, conflicts)
        self.assertEqual("LOW", quality["data_reliability"])
        self.assertEqual(1, quality["open_conflicts_count"])


if __name__ == "__main__":
    unittest.main()