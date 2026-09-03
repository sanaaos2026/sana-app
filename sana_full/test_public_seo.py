import json
import re
import unittest
import xml.etree.ElementTree as ET

import app as sana_app


class PublicSeoAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.client = sana_app.app.test_client()

    def test_public_pages_have_unique_search_and_social_metadata(self):
        pages = {
            "/": "سنع للشركات الخدمية",
            "/pricing": "باقة سنع",
            "/guide": "دليل سنع",
            "/articles": "مقالات سنع",
        }
        titles = set()
        for path, expected_title_text in pages.items():
            response = self.client.get(path)
            self.assertEqual(200, response.status_code, path)
            html = response.get_data(as_text=True)
            title = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
            self.assertIsNotNone(title, path)
            self.assertIn(expected_title_text, title.group(1))
            titles.add(title.group(1).strip())
            self.assertRegex(html, r'<meta name="description" content="[^"]+"')
            self.assertIn(
                f'<link rel="canonical" href="https://sanaclarity.com{path}">',
                html,
            )
            self.assertIn('property="og:title"', html)
            self.assertIn('property="og:description"', html)
            self.assertIn('name="twitter:card"', html)
        self.assertEqual(len(pages), len(titles))

    def test_home_explains_sana_and_publishes_matching_schema(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        for question in (
            "ما هو سنع؟",
            "لمن سنع؟",
            "ماذا يفعل؟",
            "هل هو CRM؟",
            "ماذا أحصل عليه؟",
            "ما نوع الشركات الأنسب؟",
        ):
            self.assertIn(question, html)
        payloads = re.findall(
            r'<script type="application/ld\+json">(.*?)</script>',
            html,
            re.I | re.S,
        )
        self.assertTrue(payloads)
        schema = json.loads(payloads[0])
        schema_types = {item["@type"] for item in schema["@graph"]}
        self.assertEqual(
            {"Organization", "WebSite", "SoftwareApplication", "FAQPage"},
            schema_types,
        )

    def test_discovery_files_are_public_and_private_routes_are_excluded(self):
        robots = self.client.get("/robots.txt")
        self.assertEqual(200, robots.status_code)
        robots_text = robots.get_data(as_text=True)
        self.assertIn("Sitemap: https://sanaclarity.com/sitemap.xml", robots_text)
        for path in ("/login", "/signup", "/reset-password", "/admin", "/api/"):
            self.assertIn(f"Disallow: {path}", robots_text)

        sitemap = self.client.get("/sitemap.xml")
        self.assertEqual(200, sitemap.status_code)
        root = ET.fromstring(sitemap.get_data(as_text=True))
        locations = {
            node.text
            for node in root.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}url/"
                                     "{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
        }
        self.assertTrue(
            {
                "https://sanaclarity.com/",
                "https://sanaclarity.com/pricing",
                "https://sanaclarity.com/guide",
                "https://sanaclarity.com/articles",
            }.issubset(locations)
        )
        self.assertFalse(
            any(
                blocked in location
                for location in locations
                for blocked in (
                    "/login", "/signup", "/reset-password", "/admin",
                    "/api/", "/discovery", "/case/", "/methodology/",
                )
            )
        )

        llms = self.client.get("/llms.txt")
        self.assertEqual(200, llms.status_code)
        llms_text = llms.get_data(as_text=True)
        self.assertIn("سنع أداة عربية", llms_text)
        self.assertIn("Sana Clarity is an Arabic business clarity tool", llms_text)
        self.assertNotIn("https://sanaclarity.com/login", llms_text)

    def test_public_article_has_article_metadata_when_content_exists(self):
        with sana_app.app.app_context():
            row = sana_app.get_db().execute(
                "SELECT slug FROM methodology_docs WHERE doc_type='article' "
                "ORDER BY slug LIMIT 1"
            ).fetchone()
        if not row:
            self.skipTest("No public article exists")
        response = self.client.get(f"/articles/{row['slug']}")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn('property="og:type" content="article"', html)
        self.assertIn('type="application/ld+json"', html)
        self.assertIn(
            f'<link rel="canonical" href="https://sanaclarity.com/articles/{row["slug"]}">',
            html,
        )


if __name__ == "__main__":
    unittest.main()