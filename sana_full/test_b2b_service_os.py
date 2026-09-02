"""اختبارات تراجع لإطار B2B Service Operating System.

تشغيل:
    cd sana_full && python3 -m unittest test_b2b_service_os.py -v

تُنفّذ التهيئة داخل معاملة ثم تُرجعها، لذلك لا تضيف بيانات اختبار إلى قاعدة
التطوير ولا تعتمد على حذف سجلات موجودة.
"""
import hashlib
import json
import os
import re
import sys
import time
import unittest
from pathlib import Path

import psycopg2


BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import _connect_pg
from sana_knowledge import (
    B2B_SERVICE_OS_FRAMEWORK_ID,
    B2B_SERVICE_OS_ID,
    B2B_SERVICE_OS_SOURCE_ID,
    B2B_SERVICE_OS_VERSION,
    B2B_SERVICE_OS_WORKFLOW,
    seed_knowledge,
)


CANONICAL_PATH = BASE_DIR / "B2B_SERVICE_OPERATING_SYSTEM.md"
SOURCE_SLUG = "b2b-service-operating-system"
GENERAL_FRAMEWORK_ID = "FRAMEWORK-GENERAL-SERVICE-B2B-RULES"


def _canonical_sections(markdown):
    def read_section(heading):
        match = re.search(
            rf"^##\s+{re.escape(heading)}\s*$([\s\S]*?)(?=^##\s+|\Z)",
            markdown,
            flags=re.MULTILINE,
        )
        if not match:
            raise AssertionError(f"القسم غير موجود: {heading}")
        return re.findall(
            r"^\d+\.\s+(.+?)\s*$", match.group(1), flags=re.MULTILINE
        )

    return read_section("القواعد المعرفية"), read_section("الأنظمة المرجعية")


class B2BServiceOperatingSystemRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL غير مضبوط")
        cls.db = _connect_pg()
        try:
            cls.db.execute("BEGIN")
            cls.markdown = CANONICAL_PATH.read_text(encoding="utf-8")
            cls.first_snapshot = cls._seed_with_schema_retry()
            cls.second_snapshot = cls._seed_and_snapshot()
        except Exception:
            cls.db.rollback()
            cls.db.close()
            raise

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "db", None) is not None:
            cls.db.rollback()
            cls.db.close()

    @classmethod
    def _seed_and_snapshot(cls):
        seed_knowledge(cls.db)
        return {
            "source": cls.db.execute(
                "SELECT COUNT(*) AS count FROM knowledge_sources WHERE source_id=?",
                (B2B_SERVICE_OS_SOURCE_ID,),
            ).fetchone()["count"],
            "version": cls.db.execute(
                """SELECT COUNT(*) AS count FROM knowledge_versions
                   WHERE source_id=? AND version_label=?""",
                (B2B_SERVICE_OS_SOURCE_ID, B2B_SERVICE_OS_VERSION),
            ).fetchone()["count"],
            "object": cls.db.execute(
                "SELECT COUNT(*) AS count FROM knowledge_objects WHERE object_id=?",
                (B2B_SERVICE_OS_FRAMEWORK_ID,),
            ).fetchone()["count"],
            "document": cls.db.execute(
                "SELECT COUNT(*) AS count FROM methodology_docs WHERE slug=?",
                (SOURCE_SLUG,),
            ).fetchone()["count"],
            "link": cls.db.execute(
                """SELECT COUNT(*) AS count FROM knowledge_links
                   WHERE from_object_id=? AND to_object_id=? AND relation=?""",
                (B2B_SERVICE_OS_FRAMEWORK_ID, GENERAL_FRAMEWORK_ID, "complements"),
            ).fetchone()["count"],
        }

    @classmethod
    def _seed_with_schema_retry(cls):
        """لا تفشل مصادقة المعرفة إذا تزامنت مع إقلاع Workflow آخر."""
        retryable_errors = (
            psycopg2.errors.DeadlockDetected,
            psycopg2.errors.LockNotAvailable,
            psycopg2.errors.SerializationFailure,
        )
        for attempt in range(12):
            try:
                return cls._seed_and_snapshot()
            except retryable_errors:
                cls.db.rollback()
                if attempt == 11:
                    raise
                time.sleep(1)
                cls.db.execute("BEGIN")

    def test_canonical_has_expected_structure(self):
        rules, systems = _canonical_sections(self.markdown)
        self.assertEqual(16, len(rules), "يجب أن يحتوي المصدر على 16 قاعدة")
        self.assertEqual(10, len(systems), "يجب أن يحتوي المصدر على 10 أنظمة")
        self.assertIn(B2B_SERVICE_OS_WORKFLOW, self.markdown)

    def test_repeated_seed_is_idempotent(self):
        self.assertEqual(
            self.first_snapshot,
            self.second_snapshot,
            "فشل فحص الإصدار: seed المتكرر أنشأ سجلات إطار B2B مكررة",
        )
        self.assertEqual(
            {
                "source": 1,
                "version": 1,
                "object": 1,
                "document": 1,
                "link": 1,
            },
            self.second_snapshot,
            "فشل فحص الإصدار: يجب أن يبقى لكل كيان B2B canonical سجل واحد فقط",
        )

    def test_canonical_drift_requires_version_bump(self):
        row = self.db.execute(
            """SELECT content_hash FROM knowledge_versions
               WHERE source_id=? AND version_label=?""",
            (B2B_SERVICE_OS_SOURCE_ID, B2B_SERVICE_OS_VERSION),
        ).fetchone()
        original_hash = row["content_hash"]
        self.db.execute(
            """UPDATE knowledge_versions SET content_hash=?
               WHERE source_id=? AND version_label=?""",
            ("0" * 64, B2B_SERVICE_OS_SOURCE_ID, B2B_SERVICE_OS_VERSION),
        )
        try:
            with self.assertRaisesRegex(
                ValueError,
                "بصمة إطار تشغيل الخدمات وB2B لا تطابق.*حدّث رقم الإصدار",
            ):
                seed_knowledge(self.db)
        finally:
            self.db.execute(
                """UPDATE knowledge_versions SET content_hash=?
                   WHERE source_id=? AND version_label=?""",
                (
                    original_hash,
                    B2B_SERVICE_OS_SOURCE_ID,
                    B2B_SERVICE_OS_VERSION,
                ),
            )

    def test_version_hash_matches_canonical_content(self):
        row = self.db.execute(
            """SELECT version_label, content_hash, status
               FROM knowledge_versions
               WHERE source_id=? AND version_label=?""",
            (B2B_SERVICE_OS_SOURCE_ID, B2B_SERVICE_OS_VERSION),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(
            B2B_SERVICE_OS_VERSION,
            row["version_label"],
            "فشل فحص الإصدار: إصدار السجل لا يطابق الإصدار البرمجي",
        )
        self.assertEqual(
            hashlib.sha256(self.markdown.encode("utf-8")).hexdigest(),
            row["content_hash"],
            "فشل فحص الإصدار: بصمة السجل لا تطابق الملف canonical",
        )
        self.assertEqual("approved", row["status"])

    def test_framework_remains_non_diagnostic_and_linked(self):
        row = self.db.execute(
            """SELECT library_type, sector, source_id, kpis, benchmark, diagnostic_rule
               FROM knowledge_objects WHERE object_id=?""",
            (B2B_SERVICE_OS_FRAMEWORK_ID,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual("FRAMEWORK", row["library_type"])
        self.assertEqual("shared", row["sector"])
        self.assertEqual(B2B_SERVICE_OS_SOURCE_ID, row["source_id"])
        self.assertEqual([], json.loads(row["kpis"]))
        self.assertEqual("not_available", json.loads(row["benchmark"])["status"])
        self.assertEqual([], json.loads(row["diagnostic_rule"])["match_any"])
        self.assertEqual(
            0,
            self.db.execute(
                """SELECT COUNT(*) AS count FROM knowledge_objects
                   WHERE object_id=? AND library_type='DIAGNOSTIC_PATTERN'""",
                (B2B_SERVICE_OS_FRAMEWORK_ID,),
            ).fetchone()["count"],
        )
        self.assertEqual(
            B2B_SERVICE_OS_ID,
            self.db.execute(
                "SELECT bos_id FROM methodology_docs WHERE slug=?",
                (SOURCE_SLUG,),
            ).fetchone()["bos_id"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
