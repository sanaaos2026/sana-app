from pathlib import Path
import unittest


DISCOVERY = (Path(__file__).parent / "templates" / "06-sana-discovery.html").read_text()


class SanaScanUserUxAcceptanceTests(unittest.TestCase):
    def test_a_first_screen_explains_audience_job_and_outcome(self):
        self.assertIn("اعرف أين تتعطل شركتك، وما الذي يستحق أن تصلحه أولًا.", DISCOVERY)
        self.assertIn("سنع يشخّص شركتك الخدمية من واقع معلوماتها", DISCOVERY)
        self.assertIn("احكِ لنا عن شركتك", DISCOVERY)
        self.assertIn("الأولويات والقرارات", DISCOVERY)

    def test_b_choices_are_real_tappable_buttons_with_immediate_state_feedback(self):
        self.assertIn(
            'return `<button type="button" class="choice${selectedCls}"',
            DISCOVERY,
        )
        self.assertIn("ans[key] = value;", DISCOVERY)
        self.assertIn("el.className = 'choice' +", DISCOVERY)
        self.assertIn("showToast(q.motivation, goNext);", DISCOVERY)
        self.assertIn("-webkit-tap-highlight-color: transparent", DISCOVERY)

    def test_c_decision_question_describes_company_reality(self):
        self.assertIn(
            "عند اتخاذ قرار مهم يخص الشركة — مثل توظيف، تسويق، تسعير أو مصروف كبير — ما المصدر الذي تعتمد عليه غالبًا؟",
            DISCOVERY,
        )
        for option in (
            "📈 الأرقام والتقارير",
            "💡 خبرتي الشخصية",
            "👥 رأي الفريق",
            "📚 رأي مستشار",
            "🔀 خليط من أكثر من مصدر",
        ):
            self.assertIn(option, DISCOVERY)
        self.assertNotIn("إذا جاء قرار مهم، على وش تعتمد؟", DISCOVERY)

    def test_d_three_questions_have_context_and_a_continuation_path(self):
        for key in ("q1:", "q2:", "q3:", "q4:", "q5:", "q6:", "q7:"):
            self.assertIn(key, DISCOVERY)
        self.assertIn("const journeyCopy = {", DISCOVERY)
        self.assertIn("transitionTo(render, false);", DISCOVERY)
        self.assertIn("showToast(q.motivation, goNext);", DISCOVERY)
        self.assertIn("وضوح الصورة ${pct}%", DISCOVERY)


if __name__ == "__main__":
    unittest.main()