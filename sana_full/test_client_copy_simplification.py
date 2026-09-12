from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class ClientCopySimplificationTest(unittest.TestCase):
    def test_case_workspace_hides_internal_discovery_language(self):
        html = (ROOT / "templates" / "02-case-workspace-client.html").read_text(encoding="utf-8")
        self.assertIn("وش ناقصنا؟", html)
        self.assertIn("ما أعرف الآن", html)
        self.assertIn("مثال: تقرير المبيعات", html)
        self.assertIn("شوف القرار", html)
        self.assertIn("replaceAll('Discovery','جلسة التعريف')", html)
        self.assertNotIn("ما عندي هذه المعلومة الآن", html)
        self.assertNotIn("وش يرفع موثوقية التقرير؟", html)
        self.assertNotIn("وش الاتجاه المقترح؟", html)

    def test_discovery_copy_is_short_and_owner_friendly(self):
        html = (ROOT / "templates" / "06-sana-discovery.html").read_text(encoding="utf-8")
        self.assertIn("خلنا نشوف صورة شركتك بوضوح", html)
        self.assertIn("7 أسئلة قصيرة · سؤال واحد كل مرة", html)
        self.assertIn("وش أكثر شيء مأثر على الشغل حاليًا؟", html)
        self.assertIn("لما تتخذ قرار مهم في الشركة، وش تعتمد عليه غالبًا؟", html)
        self.assertNotIn("ابدأ التقييم", html)


if __name__ == "__main__":
    unittest.main()
