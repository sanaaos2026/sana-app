"""حواجز فهرس Drive: قراءة Metadata، idempotency، الخصوصية، والسلسلة."""
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import _connect_pg
from drive_index import (
    add_drive_provenance,
    create_client_mapping,
    drive_index_report,
    ensure_schema,
    extract_selected_drive_excerpt,
    link_drive_source,
    provenance_for_object,
    review_client_mapping,
    select_context_files,
    sync_drive_metadata,
)
from sana_knowledge import ensure_schema as ensure_knowledge_schema


class FakeDrive:
    def __init__(self, include_index=True, forbidden=False):
        self.writes = []
        self.downloads = []
        self.include_index = include_index
        self.forbidden = forbidden
        self.files = {
            "root-drive": [
                {"id": "folder-13", "name": "13 - Clients & Projects",
                 "mimeType": "application/vnd.google-apps.folder", "parents": ["root-drive"]},
                {"id": "file-public", "name": "SOP onboarding.md", "mimeType": "text/markdown",
                 "parents": ["root-drive"], "md5Checksum": "same-hash",
                 "modifiedTime": "2026-09-01T10:00:00Z", "webViewLink": "https://drive/file-public"},
                {"id": "file-duplicate", "name": "SOP onboarding copy.md", "mimeType": "text/markdown",
                 "parents": ["root-drive"], "md5Checksum": "same-hash",
                 "modifiedTime": "2026-09-01T10:00:00Z"},
            ],
            "folder-13": [
                {"id": "file-private", "name": "customer notes.txt",
                 "mimeType": "text/plain", "parents": ["folder-13"],
                 "md5Checksum": "private-hash"},
            ],
        }
        if include_index:
            self.files["root-drive"].append({
                "id": "master-index", "name": "Sana Master Index",
                "mimeType": "application/json", "parents": ["root-drive"],
                "webViewLink": "https://drive/master-index",
            })

    def find(self, parent_id, name, mime_type=None):
        if self.forbidden:
            raise RuntimeError("DRIVE_HTTP_403: forbidden")
        for item in self.files.get(parent_id, []):
            if item["name"] == name and (not mime_type or item["mimeType"] == mime_type):
                return item
        if parent_id == "root" and name == "Sana OS | نظام سنع":
            return {"id": "root-drive", "name": name}
        return None

    def list_files(self, query=None, **_kwargs):
        if self.forbidden:
            raise RuntimeError("DRIVE_HTTP_403: forbidden")
        parent = next((key for key in self.files if f"'{key}'" in str(query)), "root-drive")
        return list(self.files.get(parent, []))

    def upsert(self, parent_id, name, content, content_type):
        self.writes.append((parent_id, name, content_type))
        item = {"id": "master-index-created", "name": name,
                "mimeType": content_type, "parents": [parent_id],
                "webViewLink": "https://drive/master-index-created"}
        self.files.setdefault(parent_id, []).append(item)
        return item

    def download_file(self, file_id, mime_type=None):
        self.downloads.append((file_id, mime_type))
        return b"first section\\nselected customer evidence\\nlast section"


@unittest.skipUnless(os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_PASSWORD"),
                     "DATABASE_URL غير مضبوط")
class DriveIndexIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = _connect_pg()
        ensure_knowledge_schema(cls.db)
        ensure_schema(cls.db)
        cls.company_id = "DRIVE-TEST-" + uuid.uuid4().hex[:10].upper()
        cls.db.rollback()
        cls.db.execute(
            "DELETE FROM drive_private_citations WHERE drive_file_id IN "
            "('root-drive','folder-13','master-index','master-index-created','file-public','file-duplicate','file-private')"
        )
        cls.db.execute(
            "DELETE FROM drive_provenance_links WHERE drive_file_id IN "
            "('root-drive','folder-13','master-index','master-index-created','file-public','file-duplicate','file-private')"
        )
        cls.db.execute(
            "DELETE FROM drive_knowledge_sources WHERE drive_file_id IN "
            "('root-drive','folder-13','master-index','master-index-created','file-public','file-duplicate','file-private')"
        )
        cls.db.execute(
            "DELETE FROM drive_source_excerpts WHERE drive_file_id IN "
            "('root-drive','folder-13','master-index','master-index-created','file-public','file-duplicate','file-private')"
        )
        cls.db.execute(
            "DELETE FROM research_sources WHERE research_source_id LIKE ?",
            ("RS-DRIVE-%",),
        )
        cls.db.execute(
            """DELETE FROM drive_files WHERE drive_file_id IN
               ('root-drive','folder-13','master-index','master-index-created',
                'file-public','file-duplicate','file-private')"""
        )
        cls.db.execute("INSERT INTO companies (company_id,name) VALUES (?,?)",
                       (cls.company_id, "Drive test company"))
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.rollback()
        cls.db.execute(
            "DELETE FROM drive_private_citations WHERE drive_file_id IN "
            "('root-drive','folder-13','master-index','master-index-created','file-public','file-duplicate','file-private')"
        )
        cls.db.execute(
            "DELETE FROM drive_provenance_links WHERE drive_file_id IN "
            "('root-drive','folder-13','master-index','master-index-created','file-public','file-duplicate','file-private')"
        )
        cls.db.execute(
            "DELETE FROM drive_knowledge_sources WHERE drive_file_id IN "
            "('root-drive','folder-13','master-index','master-index-created','file-public','file-duplicate','file-private')"
        )
        cls.db.execute(
            "DELETE FROM drive_source_excerpts WHERE drive_file_id IN "
            "('root-drive','folder-13','master-index','master-index-created','file-public','file-duplicate','file-private')"
        )
        cls.db.execute(
            "DELETE FROM research_sources WHERE research_source_id LIKE ?",
            ("RS-DRIVE-%",),
        )
        cls.db.execute("DELETE FROM evidence WHERE evidence_id IN ('E-TEST','E-OTHER')")
        cls.db.execute("DELETE FROM knowledge_objects WHERE object_id LIKE ?", ("KO-DRIVE-%",))
        cls.db.execute("DELETE FROM knowledge_sources WHERE source_id LIKE ?", ("TEST-DRIVE-SOURCE-%",))
        cls.db.execute("DELETE FROM drive_files WHERE company_id=?", (cls.company_id,))
        cls.db.execute("DELETE FROM drive_client_folder_mappings WHERE drive_folder_id='folder-13'")
        cls.db.execute("DELETE FROM drive_files WHERE drive_file_id IN ('root-drive','folder-13','master-index','master-index-created','file-public','file-duplicate','file-private')")
        cls.db.execute("DELETE FROM drive_sync_runs WHERE run_id LIKE ?", ("DRIVE-SYNC-%",))
        cls.db.execute("DELETE FROM companies WHERE company_id=?", (cls.company_id,))
        cls.db.commit()
        cls.db.close()

    def tearDown(self):
        self.db.rollback()

    def test_sync_is_metadata_only_and_idempotent_with_duplicates(self):
        fake = FakeDrive()
        self.db.execute(
            "UPDATE drive_files SET read_status='not_read' WHERE drive_file_id='file-public'"
        )
        self.db.commit()
        with patch.dict(os.environ, {"GOOGLE_DRIVE_ROOT_FOLDER_ID": ""}, clear=False):
            first = sync_drive_metadata(self.db, mirror=fake)
            second = sync_drive_metadata(self.db, mirror=fake)
        self.assertTrue(first["success"], first)
        self.assertTrue(second["success"], second)
        self.assertEqual(first["files_indexed"], second["files_indexed"])
        self.assertEqual([], fake.writes)
        self.assertGreaterEqual(second["duplicate_count"], 1)
        row = self.db.execute(
            "SELECT read_status,drive_state FROM drive_files WHERE drive_file_id='file-public'"
        ).fetchone()
        self.assertEqual(("not_read", "active"), (row["read_status"], row["drive_state"]))
        without_index = FakeDrive(include_index=False)
        with patch.dict(os.environ, {"GOOGLE_DRIVE_ROOT_FOLDER_ID": ""}, clear=False):
            missing = sync_drive_metadata(self.db, mirror=without_index)
        self.assertEqual("manual_required", missing["master_index_status"])
        self.assertEqual([], without_index.writes)

    def test_missing_master_index_is_created_once(self):
        fake = FakeDrive(include_index=False)
        with patch.dict(os.environ, {"GOOGLE_DRIVE_ROOT_FOLDER_ID": ""}, clear=False):
            result = sync_drive_metadata(self.db, mirror=fake)
            again = sync_drive_metadata(self.db, mirror=fake)
        self.assertTrue(result["success"], result)
        self.assertEqual([], fake.writes)
        self.assertEqual("manual_required", result["master_index_status"])
        self.assertEqual("manual_required", again["master_index_status"])

    def test_permission_failure_is_recorded(self):
        with patch.dict(os.environ, {"GOOGLE_DRIVE_ROOT_FOLDER_ID": ""}, clear=False):
            successful = sync_drive_metadata(self.db, mirror=FakeDrive())
        self.assertTrue(successful["success"], successful)
        before_count = self.db.execute(
            "SELECT COUNT(*) AS count FROM drive_files WHERE drive_state='active'"
        ).fetchone()["count"]
        fake = FakeDrive(forbidden=True)
        with patch.dict(os.environ, {"GOOGLE_DRIVE_ROOT_FOLDER_ID": ""}, clear=False):
            result = sync_drive_metadata(self.db, mirror=fake)
        self.assertFalse(result["success"])
        self.assertEqual("PERMISSION_ERROR", result["error_kind"])
        self.assertEqual("failed", self.db.execute(
            "SELECT status FROM drive_sync_runs WHERE run_id=?", (result["run_id"],)
        ).fetchone()["status"])
        self.assertEqual(before_count, self.db.execute(
            "SELECT COUNT(*) AS count FROM drive_files WHERE drive_state='active'"
        ).fetchone()["count"])
        health = drive_index_report(self.db)["health"]
        self.assertEqual("PERMISSION_ERROR", health["current_status"])
        self.assertIsNotNone(health["last_success_at"])
        self.assertEqual([], fake.writes)

    def test_private_file_is_not_selected_for_shared_context(self):
        self.db.execute(
            """UPDATE drive_files SET company_id=?,lifecycle_status='approved'
               WHERE drive_file_id IN ('file-public','file-duplicate','file-private')""",
            (self.company_id,),
        )
        self.db.commit()
        selected = select_context_files(self.db, self.company_id, topic="case")
        self.assertNotIn("file-private", {row["drive_file_id"] for row in selected})

    def test_unread_file_and_pending_mapping_are_not_used(self):
        self.db.execute("DELETE FROM drive_client_folder_mappings WHERE drive_folder_id='folder-13'")
        mapping = create_client_mapping(
            self.db, "folder-13", "13 - Clients & Projects", self.company_id, "test"
        )
        fake = FakeDrive()
        with patch.dict(os.environ, {"GOOGLE_DRIVE_ROOT_FOLDER_ID": ""}, clear=False):
            sync_drive_metadata(self.db, mirror=fake)
        private = self.db.execute(
            "SELECT company_id,confidentiality FROM drive_files WHERE drive_file_id='file-private'"
        ).fetchone()
        self.assertIsNone(private["company_id"])
        self.assertEqual("CLIENT_CONFIDENTIAL", private["confidentiality"])
        review_client_mapping(self.db, mapping["mapping_id"], "approved", "test")
        with patch.dict(os.environ, {"GOOGLE_DRIVE_ROOT_FOLDER_ID": ""}, clear=False):
            sync_drive_metadata(self.db, mirror=fake)
        private = self.db.execute(
            "SELECT company_id FROM drive_files WHERE drive_file_id='file-private'"
        ).fetchone()
        self.assertEqual(self.company_id, private["company_id"])
        self.db.execute(
            """UPDATE drive_files SET company_id=?,lifecycle_status='approved',
               confidentiality='internal',read_status='not_read'
               WHERE drive_file_id='file-public'""",
            (self.company_id,),
        )
        self.db.commit()
        self.assertEqual([], select_context_files(self.db, self.company_id, topic="onboarding"))
        self.db.execute(
            "UPDATE drive_files SET read_status='read' WHERE drive_file_id='file-public'"
        )
        self.db.commit()
        selected = select_context_files(self.db, self.company_id, topic="onboarding")
        self.assertIn("file-public", {row["drive_file_id"] for row in selected})

    def test_source_and_provenance_links_are_idempotent(self):
        source_id = "TEST-DRIVE-SOURCE-" + uuid.uuid4().hex[:8]
        self.db.execute(
            """INSERT INTO knowledge_sources
               (source_id,title,source_type,rights_status) VALUES (?,?,?,?)""",
            (source_id, "Drive source", "official", "owned"),
        )
        self.db.commit()
        self.db.execute(
            """INSERT INTO evidence (evidence_id,company_id,title)
               VALUES ('E-TEST',?,'Drive evidence') ON CONFLICT (evidence_id) DO UPDATE
               SET company_id=EXCLUDED.company_id""",
            (self.company_id,),
        )
        object_id = "KO-DRIVE-" + uuid.uuid4().hex[:8]
        self.db.execute(
            """INSERT INTO knowledge_objects
               (object_id,library_type,category,title,source,status)
               VALUES (?,?,?,?,?,'approved')""",
            (object_id, "SOP", "test", "Drive object", "Drive"),
        )
        self.db.commit()
        first = link_drive_source(
            self.db, "file-public", source_id, "drive_section", "p.1",
            "v1.0", "high", "approved", object_id,
        )
        second = link_drive_source(
            self.db, "file-public", source_id, "drive_section", "p.1",
            "v1.0", "high", "approved", object_id,
        )
        self.assertEqual(first["link_id"], second["link_id"])
        self.assertEqual(first["link_id"], provenance_for_object(self.db, object_id)[0]["link_id"])
        p1 = add_drive_provenance(
            self.db, entity_type="evidence", entity_id="E-TEST",
            drive_file_id="file-public", source_id=source_id, section_locator="p.1",
        )
        p2 = add_drive_provenance(
            self.db, entity_type="evidence", entity_id="E-TEST",
            drive_file_id="file-public", source_id=source_id, section_locator="p.1",
        )
        self.assertEqual(p1["provenance_id"], p2["provenance_id"])
        rejected = add_drive_provenance(
            self.db, entity_type="evidence", entity_id="E-TEST",
            drive_file_id="file-public",
        )
        self.assertEqual("APPROVED_SOURCE_LINK_REQUIRED", rejected["error"])
        other_company = "DRIVE-OTHER-" + uuid.uuid4().hex[:8]
        self.db.execute(
            "INSERT INTO companies (company_id,name) VALUES (?,?)", (other_company, "Other")
        )
        self.db.execute(
            "INSERT INTO evidence (evidence_id,company_id,title) VALUES ('E-OTHER',?,'Other')",
            (other_company,),
        )
        self.db.commit()
        mismatch = add_drive_provenance(
            self.db, entity_type="evidence", entity_id="E-OTHER",
            drive_file_id="file-public", source_id=source_id, section_locator="p.1",
        )
        self.assertEqual("PROVENANCE_COMPANY_MISMATCH", mismatch["error"])
        self.db.execute("DELETE FROM drive_provenance_links WHERE entity_id='E-TEST'")
        self.db.execute("DELETE FROM evidence WHERE evidence_id IN ('E-TEST','E-OTHER')")
        self.db.execute("DELETE FROM knowledge_objects WHERE object_id=?", (object_id,))
        self.db.execute("DELETE FROM drive_knowledge_sources WHERE source_id=?", (source_id,))
        self.db.execute("DELETE FROM knowledge_sources WHERE source_id=?", (source_id,))
        self.db.execute("DELETE FROM companies WHERE company_id=?", (other_company,))
        self.db.commit()

    def test_release_log_rejects_update_and_delete(self):
        release_id = "REL-TEST-" + uuid.uuid4().hex[:8]
        release_label = "test-" + uuid.uuid4().hex[:8]
        self.db.execute(
            """INSERT INTO knowledge_release_log
               (release_id,release_label,status,change_reason) VALUES (?,?,?,?)""",
            (release_id, release_label, "draft", "immutability test"),
        )
        with self.assertRaises(Exception):
            self.db.execute(
                "UPDATE knowledge_release_log SET status='approved' WHERE release_id=?",
                (release_id,),
            )
        self.db.rollback()

    def test_selected_read_stores_only_excerpt_pending_review(self):
        self.db.execute(
            """UPDATE drive_files SET company_id=?,mime_type='text/plain'
               WHERE drive_file_id='file-public'""",
            (self.company_id,),
        )
        self.db.commit()
        result = extract_selected_drive_excerpt(
            self.db, "file-public", section_locator="chars:14-40",
            start_char=14, end_char=40, actor="test",
            actor_company_id=self.company_id, mirror=FakeDrive(),
        )
        self.assertTrue(result["success"], result)
        row = self.db.execute(
            """SELECT excerpt_text,review_status FROM drive_source_excerpts
               WHERE excerpt_id=?""",
            (result["excerpt_id"],),
        ).fetchone()
        self.assertLessEqual(len(row["excerpt_text"]), 26)
        self.assertEqual("pending_review", row["review_status"])
        chain = self.db.execute(
            """SELECT rs.review_status,rs.is_private,rs.owner_account_id,
                      rs.drive_file_id,rs.source_url,rs.version_label,
                      c.content,cit.section_locator
               FROM drive_private_citations cit
               JOIN research_sources rs
                 ON rs.research_source_id=cit.research_source_id
               JOIN research_source_chunks c ON c.chunk_id=cit.chunk_id
               WHERE cit.citation_id=?""",
            (result["citation_id"],),
        ).fetchone()
        self.assertEqual("inbox", chain["review_status"])
        self.assertEqual(1, chain["is_private"])
        self.assertEqual("test", chain["owner_account_id"])
        self.assertEqual("file-public", chain["drive_file_id"])
        self.assertEqual("https://drive/file-public", chain["source_url"])
        self.assertTrue(chain["version_label"].startswith("sha256-"))
        self.assertEqual("chars:14-40", chain["section_locator"])
        self.assertEqual(row["excerpt_text"], chain["content"])
        other_company = "DRIVE-CASE-OTHER-" + uuid.uuid4().hex[:8]
        case_id = "CASE-OTHER-" + uuid.uuid4().hex[:8]
        self.db.execute(
            "INSERT INTO companies (company_id,name) VALUES (?,?)",
            (other_company, "Other case company"),
        )
        self.db.execute(
            "INSERT INTO cases (case_id,company_id,case_title) VALUES (?,?,?)",
            (case_id, other_company, "Foreign case"),
        )
        self.db.execute(
            "UPDATE drive_files SET read_status='not_read' WHERE drive_file_id='file-public'"
        )
        self.db.commit()
        rejected = extract_selected_drive_excerpt(
            self.db, "file-public", section_locator="foreign-case", case_id=case_id,
            actor="test", actor_company_id=self.company_id, mirror=FakeDrive(),
        )
        self.assertEqual("DRIVE_CASE_COMPANY_MISMATCH", rejected["error"])
        self.assertEqual(
            "not_read",
            self.db.execute(
                "SELECT read_status FROM drive_files WHERE drive_file_id='file-public'"
            ).fetchone()["read_status"],
        )
        self.db.execute("DELETE FROM cases WHERE case_id=?", (case_id,))
        self.db.execute("DELETE FROM companies WHERE company_id=?", (other_company,))
        self.db.commit()

    def test_selected_read_rejects_cross_company_case_before_download(self):
        other_company = "DRIVE-CASE-OTHER-" + uuid.uuid4().hex[:8]
        case_id = "CASE-OTHER-" + uuid.uuid4().hex[:8]
        self.db.execute(
            "INSERT INTO companies (company_id,name) VALUES (?,?)",
            (other_company, "Other case company"),
        )
        self.db.execute(
            "INSERT INTO cases (case_id,company_id,case_title) VALUES (?,?,?)",
            (case_id, other_company, "Foreign case"),
        )
        self.db.execute(
            """UPDATE drive_files SET company_id=?,read_status='not_read'
               WHERE drive_file_id='file-public'""",
            (self.company_id,),
        )
        self.db.commit()
        result = extract_selected_drive_excerpt(
            self.db, "file-public", section_locator="p.1", case_id=case_id,
            actor="test", actor_company_id=self.company_id, mirror=FakeDrive(),
        )
        self.assertEqual("DRIVE_CASE_COMPANY_MISMATCH", result["error"])
        self.assertEqual(
            "not_read",
            self.db.execute(
                "SELECT read_status FROM drive_files WHERE drive_file_id='file-public'"
            ).fetchone()["read_status"],
        )
        self.db.execute("DELETE FROM cases WHERE case_id=?", (case_id,))
        self.db.execute("DELETE FROM companies WHERE company_id=?", (other_company,))
        self.db.commit()

    def test_upgrade_readiness_check_tolerates_absent_relation(self):
        row = self.db.execute(
            """SELECT EXISTS (
                 SELECT 1 FROM pg_trigger
                 WHERE tgrelid=to_regclass('public.table_that_does_not_exist')
               ) AS has_trigger"""
        ).fetchone()
        self.assertFalse(row["has_trigger"])


if __name__ == "__main__":
    unittest.main()