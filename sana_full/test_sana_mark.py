import unittest
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


TEMPLATES = Path(__file__).parent / "templates"


class SanaMarkTemplateTest(unittest.TestCase):
    def setUp(self):
        self.environment = Environment(
            loader=FileSystemLoader(TEMPLATES),
            autoescape=select_autoescape(("html",)),
        )

    def render_mark(self, **context):
        template = self.environment.from_string(
            "{% from '_sana-mark.html' import sana_mark %}"
            "{{ sana_mark(size=size, color=color, accent_color=accent_color, "
            "class_name=class_name, decorative=decorative, "
            "stroke_width=stroke_width) }}"
        )
        return template.render(**context)

    def test_mark_supports_size_color_and_accessible_context(self):
        mark = self.render_mark(
            size=44,
            color="#FFFFFF",
            accent_color="#F2B233",
            class_name="sana-logo",
            decorative=False,
            stroke_width=7,
        )

        self.assertIn('width="44"', mark)
        self.assertIn('height="44"', mark)
        self.assertIn('stroke="#FFFFFF"', mark)
        self.assertIn('fill="#F2B233"', mark)
        self.assertIn('class="sana-logo"', mark)
        self.assertIn('role="img"', mark)
        self.assertIn('aria-label="سنع"', mark)
        self.assertNotIn('aria-hidden="true"', mark)

    def test_decorative_mark_is_hidden_from_assistive_technology(self):
        mark = self.render_mark(
            size=15,
            color="currentColor",
            accent_color="currentColor",
            class_name="expert-review-mark",
            decorative=True,
            stroke_width=7,
        )

        self.assertIn('aria-hidden="true"', mark)
        self.assertIn('focusable="false"', mark)
        self.assertNotIn('role="img"', mark)

    def test_primary_templates_use_shared_mark(self):
        branded_templates = (
            "00-entry.html",
            "00-landing.html",
            "01-ceo-home.html",
            "06-sana-discovery.html",
            "09-signup.html",
            "10-login.html",
            "11-onboarding.html",
            "14-sector-select.html",
            "14-passport-report.html",
            "18-forgot-password.html",
            "19-reset-password.html",
            "25-expert-review-summary.html",
            "26-fit-gate-build-launch.html",
        )
        for name in branded_templates:
            source = (TEMPLATES / name).read_text(encoding="utf-8")
            self.assertIn(
                "{% from '_sana-mark.html' import sana_mark %}",
                source,
                name,
            )
            self.assertNotIn('d="M76 20 H24 V80 H76 V64"', source, name)
            self.assertNotIn('d="M78,32 L78,17 L19,17', source, name)

            calls = re.findall(r"\{\{\s*(sana_mark\([^}]+\))\s*\}\}", source)
            self.assertGreater(len(calls), 0, name)
            for call in calls:
                rendered = self.environment.from_string(
                    "{% from '_sana-mark.html' import sana_mark %}{{ "
                    + call
                    + " }}"
                ).render()
                self.assertIn('d="M76 20 H24 V80 H76 V64"', rendered, name)
                self.assertIn('d="M76 20 V36"', rendered, name)
                self.assertNotIn('d="M78,32 L78,17 L19,17', rendered, name)


if __name__ == "__main__":
    unittest.main()