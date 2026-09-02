import io
import hashlib
import os
from pathlib import Path
import unittest
import zipfile

from app import _connect_pg
from sana_knowledge import (
    _extract_uploaded_text,
    compare_candidate_to_canonical,
    ensure_schema,
    sanitize_curated_text,
    search_knowledge,
)

ROOT = Path(__file__).resolve().parent.parent
ATTACHMENTS = ROOT / "attached_assets"
B2B_DOC = ATTACHMENTS / "نظام_تطوير_المشاريع_الخدمية_B2B_مستخرج_من_حالة_أراك_1788223044193.docx"
ARAK_DOC = ATTACHMENTS / "قاعدة_معرفة_موحدة_مجمع_أراك_طابا_الطبي_1788223050519.docx"


class CuratedKnowledgeExtractionTest(unittest.TestCase):
    def test_docx_preserves_paragraph_and_table_order(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p><w:r><w:t>فقرة أولى</w:t></w:r></w:p>
            <w:tbl><w:tr>
              <w:tc><w:p><w:r><w:t>حقيقة</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>مصدر</w:t></w:r></w:p></w:tc>
            </w:tr></w:tbl>
            <w:p><w:r><w:t>فقرة أخيرة</w:t></w:r></w:p>
          </w:body>
        </w:document>"""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", xml)
        text, status = _extract_uploaded_text(
            "knowledge.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            buffer.getvalue(),
        )
        self.assertEqual("extracted", status)
        self.assertEqual("فقرة أولى\n\nحقيقة | مصدر\n\nفقرة أخيرة", text)

    def test_curated_attachments_have_stable_extractable_fingerprints(self):
        for path in (B2B_DOC, ARAK_DOC):
            self.assertTrue(path.is_file(), path)
            raw = path.read_bytes()
            text, status = _extract_uploaded_text(
                path.name,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                raw,
            )
            self.assertEqual("extracted", status)
            self.assertGreater(len(text), 100)
            self.assertEqual(64, len(hashlib.sha256(raw).hexdigest()))
        b2b_text, _ = _extract_uploaded_text(B2B_DOC.name, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", B2B_DOC.read_bytes())
        self.assertIn("\n\n", b2b_text)

    def test_arabic_classification_and_redaction_are_conservative(self):
        from sana_knowledge import classify_curated_text
        self.assertEqual("Confirmed Fact", classify_curated_text("[حقيقة مؤكدة] التشغيل قائم"))
        self.assertEqual("Historical Fact", classify_curated_text("[حقيقة تاريخية] سعر سابق"))
        self.assertEqual("Proposal/Assumption", classify_curated_text("[اقتراح] خطة لاحقة"))
        self.assertEqual("Conflict/Unresolved", classify_curated_text("التعارض غير محسوم"))
        cleaned = sanitize_curated_text(
            "اتصل 0501234567 أو test@example.com عبر https://example.com/private"
        )
        self.assertNotIn("0501234567", cleaned)
        self.assertNotIn("test@example.com", cleaned)
        self.assertNotIn("https://example.com/private", cleaned)

    def test_b2b_comparison_has_both_canonical_references_without_merge(self):
        b2b_text, _ = _extract_uploaded_text(
            B2B_DOC.name,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            B2B_DOC.read_bytes(),
        )
        comparisons = compare_candidate_to_canonical(
            b2b_text, hashlib.sha256(B2B_DOC.read_bytes()).hexdigest()
        )
        self.assertEqual(2, len(comparisons))
        self.assertEqual(
            {"SRC-SANA-GENERAL-B2B-RULES", "SRC-B2B-SERVICE-OS"},
            {item["related_ref"] for item in comparisons},
        )
        self.assertTrue(all(item["candidate_file_hash"] for item in comparisons))
        self.assertTrue(all(item["relation"] == "semantic_overlap" for item in comparisons))

    @unittest.skipUnless(os.environ.get("DATABASE_URL"), "DATABASE_URL غير مضبوط")
    def test_live_ingestion_is_private_pending_and_idempotent(self):
        db = _connect_pg()
        try:
            ensure_schema(db)
            expected = [
                (B2B_DOC, "shared_candidate", 2),
                (ARAK_DOC, "private_case", 0),
            ]
            for path, scope, relation_count in expected:
                file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                rows = db.execute(
                    """SELECT rs.research_source_id, rs.review_status, rs.rights_status,
                              rs.is_private, rs.owner_account_id, rs.knowledge_scope,
                              rs.document_date,
                              rf.original_name, rf.extraction_status,
                              (SELECT COUNT(*) FROM research_source_chunks c
                               WHERE c.research_source_id=rs.research_source_id) AS chunks,
                              (SELECT COUNT(*) FROM research_source_annotations a
                               WHERE a.research_source_id=rs.research_source_id) AS annotations,
                              (SELECT COUNT(*) FROM research_source_relations rel
                               WHERE rel.research_source_id=rs.research_source_id) AS relations
                       FROM research_source_files rf
                       JOIN research_sources rs ON rs.research_source_id=rf.research_source_id
                       WHERE rf.content_hash=?""",
                    (file_hash,),
                ).fetchall()
                self.assertEqual(1, len(rows))
                row = rows[0]
                self.assertEqual(path.name, row["original_name"])
                self.assertEqual(("inbox", "pending", 1, scope), (
                    row["review_status"], row["rights_status"],
                    row["is_private"], row["knowledge_scope"],
                ))
                self.assertTrue(
                    row["owner_account_id"] == "SYSTEM_KNOWLEDGE_ADMIN"
                    or row["owner_account_id"].startswith("ZDB-")
                )
                self.assertEqual("extracted", row["extraction_status"])
                self.assertIsNone(row["document_date"])
                self.assertGreater(row["chunks"], 0)
                self.assertGreater(row["annotations"], 0)
                self.assertEqual(relation_count, row["relations"])
                self.assertEqual([], search_knowledge(db, path.stem, company_id="C001"))
        finally:
            db.close()

if __name__ == "__main__":
    unittest.main()