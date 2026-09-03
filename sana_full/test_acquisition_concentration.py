import unittest
from pathlib import Path

from sana_scan import BOTTLENECK_RULES, _matching_sources


RULE = next(
    rule
    for rule in BOTTLENECK_RULES
    if rule["rule_id"] == "SCAN-ACQUISITION-CONCENTRATION"
)


def source(source_id, statement, source_ref):
    return {
        "source_id": source_id,
        "statement": statement,
        "source_ref": source_ref,
        "classification": "Evidence",
        "information_type": "Narrative",
        "source_type": "اكتشاف_ذاتي",
        "asset_id": "ASSET-BRAND",
    }


class AcquisitionConcentrationTest(unittest.TestCase):
    def matching(self, impact):
        sources = [
            source("channel", "مصدر اكتساب العملاء: الإحالات", "SDS-001 Q4"),
            source(
                "impact",
                f"هشاشة مصدر العملاء: {impact}",
                "SDS-001 Q4 follow-up",
            ),
        ]
        return _matching_sources(RULE, sources)

    def test_new_high_and_medium_impacts_trigger_risk(self):
        for answer in ("😰 انخفاض كبير", "😟 انخفاض متوسط"):
            with self.subTest(answer=answer):
                self.assertEqual(2, len(self.matching(answer)))

    def test_limited_none_and_unknown_do_not_trigger_risk(self):
        for answer in ("😌 تأثير محدود", "🙂 لا تأثير", "🤷 معلومات غير كافية"):
            with self.subTest(answer=answer):
                self.assertEqual([], self.matching(answer))

    def test_historical_high_and_medium_answers_remain_compatible(self):
        for answer in ("😰 نعم، بشكل كبير", "🙂 نعم، بدرجة متوسطة"):
            with self.subTest(answer=answer):
                self.assertEqual(2, len(self.matching(answer)))

    def test_discovery_question_and_all_five_answers_are_clear(self):
        template = (
            Path(__file__).parent / "templates" / "06-sana-discovery.html"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "لو توقف مصدر العملاء الرئيسي شهرًا، كيف تتأثر المبيعات؟",
            template,
        )
        for answer in (
            "😰 انخفاض كبير",
            "😟 انخفاض متوسط",
            "😌 تأثير محدود",
            "🙂 لا تأثير",
            "🤷 معلومات غير كافية",
        ):
            with self.subTest(answer=answer):
                self.assertIn(answer, template)


if __name__ == "__main__":
    unittest.main()