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

    def test_e_fit_gate_uses_template_csrf_and_shows_only_safe_arabic_failure(self):
        fit_gate_script = DISCOVERY.split("async function submitFitGate()", 1)[1].split(
            "\n}\n", 1
        )[0]
        self.assertIn("fetch(withContext(JOURNEY_URLS.fit_gate)", fit_gate_script)
        self.assertIn("headers:{'Content-Type':'application/json'}", fit_gate_script)
        self.assertNotIn("csrfToken()", DISCOVERY)
        self.assertIn("await response.json()", fit_gate_script)
        self.assertIn(
            "error.textContent = 'تعذر التحقق الآن. حاول مرة أخرى بعد قليل.';",
            fit_gate_script,
        )
        self.assertIn("button.disabled = false;", fit_gate_script)
        self.assertIn("button.textContent = 'ابدأ التقييم';", fit_gate_script)

    def test_f_period_picker_keeps_arabic_copy_and_dates_readable(self):
        for copy in (
            "آخر 12 شهرًا",
            "أحدّد الفترة بنفسي",
            "اختر الفترة التي تعكس وضع شركتك الآن بأدق صورة.",
            ">متابعة</button>",
        ):
            self.assertIn(copy, DISCOVERY)
        for field_id, label in (
            ("baseline-start", "تاريخ بداية الفترة"),
            ("baseline-end", "تاريخ نهاية الفترة"),
        ):
            self.assertIn(
                f'id="{field_id}" dir="ltr" lang="en-CA"',
                DISCOVERY,
            )
            self.assertIn(f'aria-label="{label}"', DISCOVERY)
        self.assertIn("unicode-bidi: plaintext", DISCOVERY)
        self.assertIn("@media (max-width: 380px)", DISCOVERY)


if __name__ == "__main__":
    unittest.main()