"""اختبارات اقتباسات مقاطع البحث المشترك ونطاق الشركة."""
import hashlib
import os
import sys
import unittest
import uuid
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import _connect_pg
from sana_knowledge import ensure_schema, search_knowledge


class KnowledgeSearchCitationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL غير مضبوط")
        cls.db = _connect_pg()
        ensure_schema(cls.db)
        cls.run_id = uuid.uuid4().hex[:10].upper()
        cls.company_id = f"TEST-CITATIONS-{cls.run_id}"
        cls.other_company_id = f"TEST-CITATIONS-OTHER-{cls.run_id}"
        cls.source_ids = []
        cls.file_ids = []
        cls.chunk_ids = []
        try:
            cls._insert_source(
                "shared", cls.company_id, "approved", 0, "اقتباس-مشترك"
            )
            cls._insert_source(
                "review", cls.company_id, "inbox", 0, "اقتباس-قيد-المراجعة"
            )
            cls._insert_source(
                "foreign", cls.other_company_id, "approved", 0, "اقتباس-شركة-أخرى"
            )
            cls._insert_source(
                "private", cls.company_id, "approved", 1, "اقتباس-خاص"
            )
            cls.db.commit()
        except Exception:
            cls.db.rollback()
            cls._cleanup()
            cls.db.close()
            raise

    @classmethod
    def _insert_source(cls, label, company_id, review_status, is_private, token):
        source_id = f"TEST-RS-{label.upper()}-{cls.run_id}"
        file_id = f"TEST-RF-{label.upper()}-{cls.run_id}"
        chunk_id = f"TEST-RC-{label.upper()}-{cls.run_id}"
        content = f"{token} {cls.run_id} نص قابل للمراجعة والتتبع."
        raw_file = content.encode("utf-8")
        cls.db.execute(
            """INSERT INTO research_sources
               (research_source_id, title, source_kind, origin, company_id,
                version_label, rights_status, review_status, is_private,
                owner_account_id)
               VALUES (?,?,?,?,?,'v2.3','owned',?,?,?)""",
            (
                source_id, f"مصدر {label}", "file", "upload", company_id,
                review_status, is_private, f"account-{cls.run_id}",
            ),
        )
        cls.db.execute(
            """INSERT INTO research_source_files
               (file_id, research_source_id, original_name, mime_type,
                file_size, content_hash, content, extraction_status, extracted_text)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                file_id, source_id, f"{label}.txt", "text/plain", len(raw_file),
                hashlib.sha256(raw_file).hexdigest(), raw_file, "extracted", content,
            ),
        )
        cls.db.execute(
            """INSERT INTO research_source_chunks
               (chunk_id, research_source_id, file_id, chunk_order, page_number, content)
               VALUES (?,?,?,?,?,?)""",
            (chunk_id, source_id, file_id, 2, 7, content),
        )
        cls.source_ids.append(source_id)
        cls.file_ids.append(file_id)
        cls.chunk_ids.append(chunk_id)

    @classmethod
    def _cleanup(cls):
        if getattr(cls, "db", None) is None:
            return
        for source_id in getattr(cls, "source_ids", []):
            cls.db.execute(
                "DELETE FROM research_sources WHERE research_source_id=?",
                (source_id,),
            )
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls._cleanup()
        if getattr(cls, "db", None) is not None:
            cls.db.close()

    def setUp(self):
        self.db.rollback()

    def tearDown(self):
        self.db.rollback()

    def test_search_returns_traceable_approved_shared_chunk(self):
        results = search_knowledge(
            self.db,
            query="اقتباس-مشترك",
            company_id=self.company_id,
        )
        result = next(
            item for item in results
            if item["result_type"] == "research_source_chunk"
        )
        raw_file = (
            f"اقتباس-مشترك {self.run_id} نص قابل للمراجعة والتتبع."
        ).encode("utf-8")
        self.assertEqual("مصدر shared", result["source"])
        self.assertEqual("v2.3", result["version"])
        self.assertEqual(3, result["chunk"])
        self.assertEqual(7, result["page_number"])
        self.assertEqual(result["quote"], result["citations"][0]["quote"])
        self.assertEqual(hashlib.sha256(raw_file).hexdigest(), result["fingerprint"])
        self.assertEqual(
            hashlib.sha256(result["quote"].encode("utf-8")).hexdigest(),
            result["chunk_fingerprint"],
        )

    def test_search_excludes_reviewing_private_and_foreign_chunks(self):
        results = search_knowledge(
            self.db,
            query="اقتباس",
            company_id=self.company_id,
        )
        returned_ids = {item["source_id"] for item in results}
        self.assertIn("TEST-RS-SHARED-" + self.run_id, returned_ids)
        self.assertNotIn("TEST-RS-REVIEW-" + self.run_id, returned_ids)
        self.assertNotIn("TEST-RS-PRIVATE-" + self.run_id, returned_ids)
        self.assertNotIn("TEST-RS-FOREIGN-" + self.run_id, returned_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)