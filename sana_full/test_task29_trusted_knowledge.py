import hashlib
import os
import unittest
import uuid
from datetime import date, timedelta

from sana_knowledge import (
    TRUSTED_KNOWLEDGE_DOMAINS,
    _trusted_founding_batch,
    assess_source_eligibility,
    contextual_reference_knowledge,
    create_source,
    ensure_schema,
    review_source,
    search_knowledge,
    seed_knowledge,
)
from sana_scan import run_scan
from app import _connect_pg, app as flask_app


class TrustedKnowledgePolicyTest(unittest.TestCase):
    def valid_source(self):
        today = date.today()
        body = {
            "publisher": "Official Publisher",
            "jurisdiction": "Test jurisdiction",
            "publisher_trust": "statutory authority",
            "publisher_continuity": "active",
            "author_identity": "Institution",
            "material_type": "standard",
            "methodology_note": "published method",
            "rights_status": "public",
            "license_note": "public information with attribution",
            "source_url": "https://example.org/item",
            "site_age_evidence_url": "https://archive.example/evidence",
            "site_age_evidence_date": today.replace(year=today.year - 11).isoformat(),
            "document_date": today.isoformat(),
            "retrieved_at": today.isoformat(),
            "reviewed_at": today.isoformat(),
            "version_label": "v1",
            "review_due_at": (today + timedelta(days=30)).isoformat(),
            "content_fingerprint": hashlib.sha256(b"item").hexdigest(),
            "trust_level": "authoritative",
        }
        return body

    def test_site_age_and_document_freshness_are_separate_gates(self):
        source = self.valid_source()
        source["review_due_at"] = (date.today() - timedelta(days=1)).isoformat()
        result = assess_source_eligibility(source)
        self.assertTrue(result["site_age_eligible"])
        self.assertFalse(result["document_fresh"])
        self.assertEqual("pending_review", result["status"])
        source = self.valid_source()
        source["reviewed_at"] = (
            date.today() - timedelta(days=549)
        ).isoformat()
        source["review_due_at"] = (
            date.today() + timedelta(days=30)
        ).isoformat()
        stale = assess_source_eligibility(source)
        self.assertTrue(stale["site_age_eligible"])
        self.assertFalse(stale["document_fresh"])

    def test_missing_identity_method_rights_and_fingerprint_stay_pending(self):
        source = self.valid_source()
        for key in ("author_identity", "methodology_note", "license_note"):
            source[key] = ""
        source["rights_status"] = "restricted"
        source["content_fingerprint"] = "not-sha256"
        result = assess_source_eligibility(source)
        self.assertFalse(result["eligible"])
        self.assertIn("RIGHTS_NOT_CLEARED", result["reasons"])
        self.assertIn("FINGERPRINT_INVALID", result["reasons"])
        self.assertEqual("pending_review", result["status"])

    def test_unresolved_conflict_blocks_publication(self):
        source = self.valid_source()
        source["unresolved_conflict"] = True
        result = assess_source_eligibility(source)
        self.assertIn("UNRESOLVED_CONFLICT", result["reasons"])
        self.assertEqual("pending_review", result["status"])

    def test_founding_batch_covers_nine_domains_with_short_traceable_quotes(self):
        batch = _trusted_founding_batch()
        self.assertEqual(9, len(batch))
        self.assertEqual(set(TRUSTED_KNOWLEDGE_DOMAINS), {x["domain"] for x in batch})
        self.assertEqual(9, len({x["source_id"] for x in batch}))
        for item in batch:
            self.assertTrue(assess_source_eligibility(item, "2026-09-01")["eligible"])
            self.assertLessEqual(len(item["excerpt"]), 160)
            self.assertTrue(item["summary"])
            self.assertEqual(64, len(item["content_fingerprint"]))
            self.assertTrue(item["source_url"].startswith("https://"))
            self.assertTrue(item["site_age_evidence_url"].startswith("https://"))


@unittest.skipUnless(os.environ.get("DATABASE_URL"), "DATABASE_URL غير مضبوط")
class TrustedKnowledgeContextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = _connect_pg()
        ensure_schema(cls.db)
        seed_knowledge(cls.db)
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def setUp(self):
        self.db.rollback()

    def tearDown(self):
        self.db.rollback()

    def test_context_selects_goal_and_problem_specific_references(self):
        bundle = contextual_reference_knowledge(
            self.db,
            {
                "company_id": "NO-STORED-COMPANY-REQUIRED",
                "sector": "tech",
                "stage": "growth",
                "main_goal": "الأتمتة الآمنة وإدارة المخاطر",
            },
            {
                "case_type": "digital",
                "declared_problem": "مخاطر الذكاء الاصطناعي والتحول الرقمي",
                "real_question": "",
            },
            {"title": "الذكاء الاصطناعي"},
        )
        self.assertFalse(bundle["knowledge_gap"])
        self.assertEqual(
            "SRC-TRUSTED-NIST-AI-RMF",
            bundle["references"][0]["source_id"],
        )
        self.assertIn(
            "تطابق الهدف",
            bundle["references"][0]["match_reasons"],
        )
        self.assertTrue(all(item["reference_only"] for item in bundle["references"]))

    def test_no_specific_match_returns_explicit_knowledge_gap(self):
        bundle = contextual_reference_knowledge(
            self.db,
            {
                "company_id": "NO-STORED-COMPANY-REQUIRED",
                "sector": "other",
                "stage": "unknown",
                "main_goal": "هدف غير موجود إطلاقًا",
            },
            {
                "case_type": "نموذج غير موجود",
                "declared_problem": "مشكلة غير موجودة إطلاقًا",
                "real_question": "",
            },
            {"title": "اختناق غير موجود إطلاقًا"},
        )
        self.assertEqual([], bundle["references"])
        self.assertIsNotNone(bundle["knowledge_gap"])

    def test_duplicate_fingerprint_is_rejected(self):
        suffix = uuid.uuid4().hex[:10]
        payload = TrustedKnowledgePolicyTest().valid_source()
        payload.update({
            "source_id": f"TEST-TRUSTED-{suffix}",
            "title": "Temporary trusted source",
            "source_type": "official",
            "status": "pending_review",
        })
        first = create_source(self.db, payload)
        self.assertTrue(first["success"])
        duplicate = dict(payload)
        duplicate["source_id"] = f"TEST-TRUSTED-DUP-{suffix}"
        duplicate["title"] = "Duplicate temporary source"
        second = create_source(self.db, duplicate)
        self.assertFalse(second["success"])
        self.assertEqual("DUPLICATE_SOURCE", second["error"])
        self.db.execute(
            "DELETE FROM knowledge_sources WHERE source_id=?",
            (payload["source_id"],),
        )
        self.db.commit()

    def test_approved_version_hash_is_immutable(self):
        source_id = "SRC-TRUSTED-NIST-AI-RMF"
        stored = self.db.execute(
            """SELECT version_label,content_fingerprint
               FROM knowledge_sources WHERE source_id=?""",
            (source_id,),
        ).fetchone()
        self.db.execute(
            "UPDATE knowledge_sources SET content_fingerprint=? WHERE source_id=?",
            ("a" * 64, source_id),
        )
        result = review_source(self.db, source_id, "approved", "test-reviewer")
        self.assertFalse(result["success"])
        self.assertEqual("VERSION_HASH_CONFLICT", result["error"])
        version = self.db.execute(
            """SELECT content_hash FROM knowledge_versions
               WHERE source_id=? AND version_label=?""",
            (source_id, stored["version_label"]),
        ).fetchone()
        self.assertEqual(stored["content_fingerprint"], version["content_hash"])

    def test_pending_conflict_blocks_both_sides_and_is_reported(self):
        conflict_id = "TEST-CONFLICT-" + uuid.uuid4().hex[:10]
        left_id = "TRUSTED-INFORMATION-SYSTEMS-DATA-SECURITY-GOVERNANCE"
        right_id = "TRUSTED-DIGITAL-TRANSFORMATION-AI"
        self.db.execute(
            """INSERT INTO knowledge_conflicts
               (conflict_id,object_id,conflicting_object_id,conflict_note,review_status)
               VALUES (?,?,?,?,'pending')""",
            (conflict_id, left_id, right_id, "اختبار تعارض في الاتجاهين"),
        )
        left_results = search_knowledge(self.db, query="Cybersecurity Framework")
        right_results = search_knowledge(
            self.db, query="Artificial Intelligence Risk Management"
        )
        self.assertNotIn(left_id, {item["object_id"] for item in left_results})
        self.assertNotIn(right_id, {item["object_id"] for item in right_results})
        bundle = contextual_reference_knowledge(
            self.db,
            {
                "company_id": "NO-STORED-COMPANY-REQUIRED",
                "sector": "tech",
                "stage": "growth",
                "main_goal": "الأتمتة الآمنة وإدارة المخاطر",
            },
            {
                "case_type": "digital",
                "declared_problem": "مخاطر الذكاء الاصطناعي والأمن",
            },
            {"title": "الذكاء الاصطناعي"},
        )
        self.assertTrue(any(
            item["conflict_id"] == conflict_id for item in bundle["conflicts"]
        ))
        self.assertNotIn(
            right_id,
            {item["object_id"] for item in bundle["references"]},
        )

    def test_reference_batch_alone_cannot_complete_scan(self):
        suffix = uuid.uuid4().hex[:10].upper()
        company_id = "TEST-KNOWLEDGE-GATE-" + suffix
        case_id = "TEST-KNOWLEDGE-CASE-" + suffix
        try:
            self.db.execute(
                """INSERT INTO companies
                   (company_id,name,sector,stage,main_goal)
                   VALUES (?,?,?,?,?)""",
                (company_id, "شركة اختبار المعرفة", "tech", "growth", "الأتمتة الآمنة"),
            )
            self.db.execute(
                """INSERT INTO cases
                   (case_id,company_id,case_title,case_type,declared_problem)
                   VALUES (?,?,?,?,?)""",
                (case_id, company_id, "اختبار البوابة", "digital",
                 "مخاطر الذكاء الاصطناعي"),
            )
            for asset_type in (
                "Knowledge", "Operations", "Brand", "Data", "Independence"
            ):
                self.db.execute(
                    """INSERT INTO assets
                       (asset_id,company_id,asset_type,asset_name,status)
                       VALUES (?,?,?,?,?)""",
                    (f"{asset_type[:2]}-{suffix}", company_id, asset_type,
                     asset_type, "تحت المراجعة"),
                )
            self.db.commit()
            result = run_scan(self.db, case_id)
            self.assertEqual("INCOMPLETE", result["status"])
            self.assertTrue(all(
                item["score"] is None for item in result["asset_scores"]
            ))
            self.assertFalse(result["reference_knowledge_used_as_evidence"])
            self.assertEqual("none", result["reference_knowledge_effect"])
        finally:
            self.db.rollback()
            for table in ("scan_findings", "scan_runs", "evidence", "cases", "assets"):
                self.db.execute(
                    f"DELETE FROM {table} WHERE company_id=?",
                    (company_id,),
                )
            self.db.execute(
                "DELETE FROM companies WHERE company_id=?",
                (company_id,),
            )
            self.db.commit()

    def test_search_api_does_not_accept_a_foreign_case_context(self):
        suffix = uuid.uuid4().hex[:10].upper()
        own_company = "TEST-API-OWN-" + suffix
        foreign_company = "TEST-API-FOREIGN-" + suffix
        foreign_case = "TEST-API-CASE-" + suffix
        try:
            self.db.execute(
                "INSERT INTO companies(company_id,name) VALUES (?,?)",
                (own_company, "شركة الحساب"),
            )
            self.db.execute(
                "INSERT INTO companies(company_id,name) VALUES (?,?)",
                (foreign_company, "شركة أخرى"),
            )
            self.db.execute(
                """INSERT INTO cases(case_id,company_id,case_title)
                   VALUES (?,?,?)""",
                (foreign_case, foreign_company, "قضية أجنبية"),
            )
            self.db.commit()
            with flask_app.test_client() as client:
                with client.session_transaction() as session:
                    session["account_id"] = "TEST-ACCOUNT-" + suffix
                    session["company_id"] = own_company
                    session["email"] = "test@example.invalid"
                response = client.get(
                    "/api/knowledge/search",
                    query_string={
                        "company_id": foreign_company,
                        "case_id": foreign_case,
                    },
                )
                self.assertEqual(404, response.status_code)
                self.assertEqual("CASE_NOT_FOUND", response.get_json()["error"])
        finally:
            self.db.rollback()
            self.db.execute("DELETE FROM cases WHERE case_id=?", (foreign_case,))
            self.db.execute(
                "DELETE FROM companies WHERE company_id IN (?,?)",
                (own_company, foreign_company),
            )
            self.db.commit()


if __name__ == "__main__":
    unittest.main()