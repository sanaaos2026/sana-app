import os
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

    def test_g_webkit_iphone_custom_dates_stay_readable_and_validate_order(self):
        browser_required = os.environ.get("SANA_REQUIRE_BROWSER_TESTS") == "1"
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            if browser_required:
                self.fail("Playwright is required for the WebKit iPhone date check")
            self.skipTest("Playwright is not installed")

        styles = DISCOVERY.split("<style>", 1)[1].split("</style>", 1)[0]
        html = f"""<!doctype html>
        <html lang="ar" dir="rtl"><head><meta name="viewport"
          content="width=device-width, initial-scale=1"><style>{styles}</style></head>
        <body><main id="stage"><div class="calibration-wrap">
          <button type="button" class="period-option" id="custom-period"
            aria-controls="custom-fields">أحدّد الفترة بنفسي</button>
          <div id="custom-fields" hidden>
            <div class="period-custom-fields" aria-label="حدد الفترة بنفسك">
              <div class="period-field"><label for="baseline-start">من</label>
                <input class="followup-input" type="date" id="baseline-start"
                  dir="ltr" lang="en-CA" aria-label="تاريخ بداية الفترة"></div>
              <div class="period-field"><label for="baseline-end">إلى</label>
                <input class="followup-input" type="date" id="baseline-end"
                  dir="ltr" lang="en-CA" aria-label="تاريخ نهاية الفترة"></div>
            </div>
          </div>
          <div id="calibration-error" class="period-error" role="alert"></div>
          <button type="button" class="outro-btn period-continue" id="continue">
            متابعة</button>
        </div></main><script>
          document.querySelector('#custom-period').onclick = () => {{
            document.querySelector('#custom-fields').hidden = false;
          }};
          document.querySelector('#continue').onclick = () => {{
            const start = document.querySelector('#baseline-start').value;
            const end = document.querySelector('#baseline-end').value;
            if (end < start) document.querySelector('#calibration-error').textContent =
              'تأكد أن تاريخ «إلى» بعد تاريخ «من».';
          }};
        </script></body></html>"""

        with sync_playwright() as playwright:
            launch_errors = []
            try:
                browser = playwright.webkit.launch(headless=True)
            except Exception as exc:
                launch_errors.append(str(exc))
                browser = None
                nix_webkits = sorted(
                    Path("/nix/store").glob("*-playwright-webkit/pw_run.sh")
                )
                for executable in reversed(nix_webkits):
                    try:
                        browser = playwright.webkit.launch(
                            headless=True, executable_path=str(executable)
                        )
                        break
                    except Exception as fallback_exc:
                        launch_errors.append(str(fallback_exc))
            if browser is None:
                message = "Playwright WebKit is unavailable: " + " | ".join(
                    launch_errors
                )
                if browser_required:
                    self.fail(f"{message}; browser verification is required")
                self.skipTest(message)

            context = browser.new_context(**playwright.devices["iPhone 13"])
            page = context.new_page()
            page.set_content(html, wait_until="domcontentloaded")
            page.get_by_role("button", name="أحدّد الفترة بنفسي").click()

            start = page.get_by_label("تاريخ بداية الفترة")
            end = page.get_by_label("تاريخ نهاية الفترة")
            for field, label in ((start, "من"), (end, "إلى")):
                self.assertTrue(field.is_visible())
                self.assertTrue(page.get_by_text(label, exact=True).is_visible())
                box = field.bounding_box()
                self.assertIsNotNone(box)
                self.assertGreaterEqual(box["width"], 120)
                self.assertEqual(
                    "ltr", field.evaluate("element => getComputedStyle(element).direction")
                )

            start.fill("2026-09-20")
            end.fill("2026-09-01")
            self.assertEqual("2026-09-20", start.input_value())
            self.assertEqual("2026-09-01", end.input_value())
            page.get_by_role("button", name="متابعة").click()

            error = page.get_by_role("alert")
            self.assertTrue(error.is_visible())
            self.assertEqual(
                "تأكد أن تاريخ «إلى» بعد تاريخ «من».", error.inner_text()
            )
            context.close()
            browser.close()


if __name__ == "__main__":
    unittest.main()
