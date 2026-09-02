"""حواجز فهرس Drive: قراءة Metadata، idempotency، الخصوصية، والسلسلة."""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import unittest
import uuid
import time
from contextlib import suppress
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import _connect_pg, app as flask_app
from werkzeug.security import generate_password_hash
from drive_index import (
    add_drive_provenance,
    create_client_mapping,
    drive_index_report,
    ensure_schema,
    extract_selected_drive_excerpt,
    link_drive_source,
    list_pending_drive_excerpts,
    provenance_for_object,
    review_client_mapping,
    review_drive_excerpt,
    select_context_files,
    sync_drive_metadata,
)
from sana_knowledge import ensure_schema as ensure_knowledge_schema, search_knowledge


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
        cls.db.execute(
            "SELECT pg_advisory_lock(hashtext('sana.drive.metadata.sync'))"
        )
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
        cls.db.execute(
            "SELECT pg_advisory_unlock(hashtext('sana.drive.metadata.sync'))"
        )
        cls.db.close()

    def tearDown(self):
        self.db.rollback()

    def _cleanup_review_fixture(self, file_id, company_id=None):
        self.db.rollback()
        object_rows = self.db.execute(
            """SELECT entity_id FROM drive_provenance_links
               WHERE entity_type='knowledge_object' AND drive_file_id=?""",
            (file_id,),
        ).fetchall()
        object_ids = [row["entity_id"] for row in object_rows]
        self.db.execute(
            "DELETE FROM drive_provenance_links WHERE drive_file_id=?", (file_id,)
        )
        for object_id in object_ids:
            self.db.execute(
                "DELETE FROM knowledge_objects WHERE object_id=?", (object_id,)
            )
        self.db.execute(
            "DELETE FROM drive_knowledge_sources WHERE drive_file_id=?", (file_id,)
        )
        self.db.execute(
            "DELETE FROM drive_source_excerpts WHERE drive_file_id=?", (file_id,)
        )
        self.db.execute(
            "DELETE FROM research_sources WHERE drive_file_id=?", (file_id,)
        )
        self.db.execute("DELETE FROM drive_files WHERE drive_file_id=?", (file_id,))
        if company_id:
            self.db.execute("DELETE FROM companies WHERE company_id=?", (company_id,))
        self.db.commit()

    def _sync(self, **kwargs):
        result = None
        for _ in range(80):
            result = sync_drive_metadata(self.db, **kwargs)
            if result.get("error") != "DRIVE_SYNC_ALREADY_RUNNING":
                return result
            time.sleep(0.25)
        return result

    def test_sync_is_metadata_only_and_idempotent_with_duplicates(self):
        fake = FakeDrive()
        self.db.execute(
            "UPDATE drive_files SET read_status='not_read' WHERE drive_file_id='file-public'"
        )
        self.db.commit()
        with patch.dict(os.environ, {"GOOGLE_DRIVE_ROOT_FOLDER_ID": ""}, clear=False):
            first = self._sync(mirror=fake)
            second = self._sync(mirror=fake)
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
            missing = self._sync(mirror=without_index)
        self.assertEqual("manual_required", missing["master_index_status"])
        self.assertEqual([], without_index.writes)

    def test_missing_master_index_is_created_once(self):
        fake = FakeDrive(include_index=False)
        with patch.dict(os.environ, {"GOOGLE_DRIVE_ROOT_FOLDER_ID": ""}, clear=False):
            result = self._sync(mirror=fake)
            again = self._sync(mirror=fake)
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
            result = self._sync(mirror=fake)
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
        self.db.execute(
            """UPDATE drive_files SET company_id=NULL,lifecycle_status='unreviewed'
               WHERE drive_file_id='file-private'"""
        )
        self.db.commit()

    def test_unread_file_and_pending_mapping_are_not_used(self):
        self.db.execute("DELETE FROM drive_client_folder_mappings WHERE drive_folder_id='folder-13'")
        mapping = create_client_mapping(
            self.db, "folder-13", "13 - Clients & Projects", self.company_id, "test"
        )
        fake = FakeDrive()
        with patch.dict(os.environ, {"GOOGLE_DRIVE_ROOT_FOLDER_ID": ""}, clear=False):
            first_sync = self._sync(mirror=fake)
        self.assertTrue(first_sync["success"], first_sync)
        private = self.db.execute(
            "SELECT company_id,confidentiality FROM drive_files WHERE drive_file_id='file-private'"
        ).fetchone()
        self.assertIsNone(private["company_id"])
        self.assertEqual("CLIENT_CONFIDENTIAL", private["confidentiality"])
        review_client_mapping(self.db, mapping["mapping_id"], "approved", "test")
        with patch.dict(os.environ, {"GOOGLE_DRIVE_ROOT_FOLDER_ID": ""}, clear=False):
            second_sync = self._sync(mirror=fake)
        self.assertTrue(second_sync["success"], second_sync)
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
        self.db.execute(
            """UPDATE drive_files SET company_id=?,drive_state='active'
               WHERE drive_file_id='file-public'""",
            (self.company_id,),
        )
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
        with patch.dict(os.environ, {"GOOGLE_DRIVE_ROOT_FOLDER_ID": ""}, clear=False):
            synced = self._sync(mirror=FakeDrive())
        self.assertTrue(synced["success"], synced)
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
        with patch.dict(os.environ, {"GOOGLE_DRIVE_ROOT_FOLDER_ID": ""}, clear=False):
            synced = self._sync(mirror=FakeDrive())
        self.assertTrue(synced["success"], synced)
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

    def test_confidential_excerpt_requires_documented_anonymization_and_creates_small_objects(self):
        file_id = "review-file-private-" + uuid.uuid4().hex[:8]
        other_company_id = "REVIEW-OTHER-" + uuid.uuid4().hex[:8]
        self.addCleanup(
            self._cleanup_review_fixture, file_id, other_company_id
        )
        self.db.execute(
            "INSERT INTO companies (company_id,name) VALUES (?,?)",
            (other_company_id, "Other review company"),
        )
        self.db.execute(
            """INSERT INTO drive_files
               (drive_file_id,name,mime_type,is_folder,drive_state,access_status,
                company_id,confidentiality,web_view_link)
               VALUES (?,'Secret Customer Alpha notes.txt','text/plain',0,'active','ok',
                       ?,'CLIENT_CONFIDENTIAL','https://drive/secret-customer-alpha')
               ON CONFLICT (drive_file_id) DO UPDATE SET
                 company_id=EXCLUDED.company_id,mime_type=EXCLUDED.mime_type,
                 confidentiality=EXCLUDED.confidentiality,drive_state='active',
                 is_folder=0,access_status='ok'""",
            (file_id, self.company_id),
        )
        self.db.commit()
        extracted = extract_selected_drive_excerpt(
            self.db, file_id, section_locator="review-private",
            start_char=0, end_char=55, actor="reviewer",
            actor_company_id=self.company_id, mirror=FakeDrive(),
        )
        self.assertTrue(extracted["success"], extracted)
        pending_ids = {
            item["excerpt_id"]
            for item in list_pending_drive_excerpts(self.db, self.company_id)
        }
        self.assertIn(extracted["excerpt_id"], pending_ids)
        objects = [
            {
                "library_type": "SOP",
                "category": "onboarding",
                "title": "تحقق من دليل العميل قبل البدء",
                "statement": "اطلب دليلًا واحدًا محددًا قبل بدء الإجراء.",
            },
            {
                "library_type": "EVIDENCE_REQUIREMENT",
                "category": "onboarding",
                "title": "سجل مصدر الدليل",
                "statement": "اربط الدليل بمصدره وقسمه قبل اعتماده.",
            },
        ]
        blocked = review_drive_excerpt(
            self.db, extracted["excerpt_id"], decision="approved",
            reason="قواعد تشغيل قابلة لإعادة الاستخدام",
            references=["POLICY-PRIVACY-1"], reviewer="reviewer",
            objects=objects, actor_company_id=self.company_id,
        )
        self.assertEqual("ANONYMIZATION_DOCUMENTATION_REQUIRED", blocked["error"])
        result = review_drive_excerpt(
            self.db, extracted["excerpt_id"], decision="approved",
            reason="قواعد تشغيل قابلة لإعادة الاستخدام",
            references=["POLICY-PRIVACY-1", "CASE-REVIEW"],
            reviewer="reviewer", objects=objects,
            anonymized_text="دليل عميل منزوع الهوية",
            anonymization_notes="أزيل اسم العميل وتفاصيل المشروع.",
            actor_company_id=self.company_id,
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(2, len(result["knowledge_objects"]))
        object_ids = [item["object_id"] for item in result["knowledge_objects"]]
        stored = self.db.execute(
            """SELECT source,source_url,source_excerpt,original_summary,
                      source_file_id,provenance_link_id
               FROM knowledge_objects WHERE object_id IN (?,?)
               ORDER BY object_id""",
            object_ids,
        ).fetchall()
        self.assertEqual(2, len(stored))
        self.assertTrue(all(row["source_excerpt"] == "دليل عميل منزوع الهوية" for row in stored))
        self.assertTrue(all(row["source"] == "مقتطف Drive معتمد ومنقح" for row in stored))
        self.assertTrue(all(row["source_url"] is None for row in stored))
        self.assertTrue(all(row["source_file_id"] is None for row in stored))
        self.assertTrue(all(row["provenance_link_id"] is None for row in stored))
        admin_provenance = provenance_for_object(self.db, object_ids[0])
        self.assertEqual(file_id, admin_provenance[0]["drive_file_id"])
        self.assertEqual("Secret Customer Alpha notes.txt", admin_provenance[0]["name"])
        foreign_results = search_knowledge(
            self.db, query="تحقق من دليل العميل قبل البدء",
            sector="professional_services", company_id=other_company_id, limit=20,
        )
        foreign_object = next(
            item for item in foreign_results if item["object_id"] == object_ids[0]
        )
        exposed = json.dumps(foreign_object, ensure_ascii=False)
        self.assertEqual("مقتطف Drive معتمد ومنقح", foreign_object["source"])
        self.assertIsNone(foreign_object["source_url"])
        self.assertNotIn(file_id, exposed)
        self.assertNotIn("Secret Customer Alpha", exposed)
        self.assertNotIn("https://drive/secret-customer-alpha", exposed)
        reviewed = self.db.execute(
            """SELECT review_status,review_reason,review_references,
                      anonymization_notes,published_text
               FROM drive_source_excerpts WHERE excerpt_id=?""",
            (extracted["excerpt_id"],),
        ).fetchone()
        self.assertEqual("approved", reviewed["review_status"])
        self.assertEqual("دليل عميل منزوع الهوية", reviewed["published_text"])
        self.assertIn("POLICY-PRIVACY-1", reviewed["review_references"])

    def test_rejection_requires_reason_and_references(self):
        file_id = "review-file-reject-" + uuid.uuid4().hex[:8]
        self.addCleanup(self._cleanup_review_fixture, file_id)
        self.db.execute(
            """INSERT INTO drive_files
               (drive_file_id,name,mime_type,is_folder,drive_state,access_status,
                company_id,confidentiality,web_view_link)
               VALUES (?,'SOP onboarding.md','text/plain',0,'active','ok',
                       ?,'internal','https://drive/file-public')
               ON CONFLICT (drive_file_id) DO UPDATE SET
                 company_id=EXCLUDED.company_id,mime_type=EXCLUDED.mime_type,
                 confidentiality=EXCLUDED.confidentiality,drive_state='active',
                 is_folder=0,access_status='ok'""",
            (file_id, self.company_id),
        )
        self.db.commit()
        extracted = extract_selected_drive_excerpt(
            self.db, file_id, section_locator="review-reject",
            start_char=0, end_char=30, actor="reviewer",
            actor_company_id=self.company_id, mirror=FakeDrive(),
        )
        missing_reason = review_drive_excerpt(
            self.db, extracted["excerpt_id"], decision="rejected",
            reason="", references=["REVIEW-REF"], reviewer="reviewer",
            actor_company_id=self.company_id,
        )
        self.assertEqual("EXCERPT_REVIEW_REASON_REQUIRED", missing_reason["error"])
        missing_references = review_drive_excerpt(
            self.db, extracted["excerpt_id"], decision="rejected",
            reason="خاص بالحالة ولا يعمم", references=[], reviewer="reviewer",
            actor_company_id=self.company_id,
        )
        self.assertEqual(
            "EXCERPT_REVIEW_REFERENCES_REQUIRED", missing_references["error"]
        )
        result = review_drive_excerpt(
            self.db, extracted["excerpt_id"], decision="rejected",
            reason="خاص بالحالة ولا يعمم", references=["REVIEW-REF"],
            reviewer="reviewer", actor_company_id=self.company_id,
        )
        self.assertTrue(result["success"], result)
        self.assertEqual("rejected", result["review_status"])
        audit = self.db.execute(
            """SELECT decision,reason,references_json,reviewer
               FROM drive_excerpt_reviews WHERE excerpt_id=?""",
            (extracted["excerpt_id"],),
        ).fetchone()
        self.assertEqual("rejected", audit["decision"])
        self.assertEqual("reviewer", audit["reviewer"])

    def test_concurrent_review_creates_one_audit_and_one_object_set(self):
        file_id = "review-file-race-" + uuid.uuid4().hex[:8]
        self.addCleanup(self._cleanup_review_fixture, file_id)
        self.db.execute(
            """INSERT INTO drive_files
               (drive_file_id,name,mime_type,is_folder,drive_state,access_status,
                company_id,confidentiality,web_view_link)
               VALUES (?,'Concurrent review.txt','text/plain',0,'active','ok',
                       ?,'internal','https://drive/concurrent-review')""",
            (file_id, self.company_id),
        )
        self.db.commit()
        extracted = extract_selected_drive_excerpt(
            self.db, file_id, section_locator="concurrent-review",
            start_char=0, end_char=45, actor="race-reviewer",
            actor_company_id=self.company_id, mirror=FakeDrive(),
        )
        self.assertTrue(extracted["success"], extracted)
        barrier = threading.Barrier(2)
        results = []
        failures = []

        def submit_review(worker):
            worker_db = _connect_pg()
            try:
                barrier.wait(timeout=10)
                results.append(review_drive_excerpt(
                    worker_db, extracted["excerpt_id"], decision="approved",
                    reason="اختبار منع الاعتماد المكرر",
                    references=["CONCURRENCY-TEST"], reviewer=worker,
                    objects=[{
                        "library_type": "SOP",
                        "category": "testing",
                        "title": "اعتماد متزامن " + worker,
                        "statement": "يجب نشر مجموعة واحدة فقط.",
                    }],
                    actor_company_id=self.company_id,
                ))
            except Exception as exc:
                failures.append(exc)
            finally:
                worker_db.close()

        workers = [
            threading.Thread(target=submit_review, args=(f"worker-{index}",))
            for index in range(2)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=30)
        self.assertFalse(failures, failures)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(1, sum(bool(item.get("success")) for item in results))
        self.assertEqual(
            ["EXCERPT_ALREADY_REVIEWED"],
            [item.get("error") for item in results if not item.get("success")],
        )
        audit_count = self.db.execute(
            "SELECT COUNT(*) AS count FROM drive_excerpt_reviews WHERE excerpt_id=?",
            (extracted["excerpt_id"],),
        ).fetchone()["count"]
        object_count = self.db.execute(
            """SELECT COUNT(*) AS count FROM drive_provenance_links
               WHERE entity_type='knowledge_object' AND drive_file_id=?""",
            (file_id,),
        ).fetchone()["count"]
        self.assertEqual(1, audit_count)
        self.assertEqual(1, object_count)

    def test_upgrade_readiness_check_tolerates_absent_relation(self):
        row = self.db.execute(
            """SELECT EXISTS (
                 SELECT 1 FROM pg_trigger
                 WHERE tgrelid=to_regclass('public.table_that_does_not_exist')
               ) AS has_trigger"""
        ).fetchone()
        self.assertFalse(row["has_trigger"])

    def test_drive_excerpt_review_patch_route_is_registered(self):
        rule = next(
            (
                item for item in flask_app.url_map.iter_rules()
                if item.rule == "/api/knowledge/drive/excerpts/<excerpt_id>/review"
            ),
            None,
        )
        self.assertIsNotNone(rule)
        self.assertIn("PATCH", rule.methods)


@unittest.skipUnless(
    os.environ.get("RUN_SANA_BROWSER_TESTS") == "1"
    and (os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_PASSWORD")),
    "يتطلب RUN_SANA_BROWSER_TESTS=1 واتصال قاعدة البيانات",
)
class DriveExcerptBrowserTest(unittest.TestCase):
    """اختبار قبول المتصفح لمسار مراجعة مقتطف Drive من حساب إداري حقيقي."""

    @classmethod
    def setUpClass(cls):
        cls.db = _connect_pg()
        ensure_knowledge_schema(cls.db)
        ensure_schema(cls.db)
        cls.company_id = "DRIVE-BROWSER-" + uuid.uuid4().hex[:10].upper()
        cls.account_id = "ACC-DRIVE-BROWSER-" + uuid.uuid4().hex[:10].upper()
        cls.case_id = "CASE-DRIVE-BROWSER-" + uuid.uuid4().hex[:10].upper()
        cls.file_id = "FILE-DRIVE-BROWSER-" + uuid.uuid4().hex[:10].upper()
        cls.email = f"drive-browser-{uuid.uuid4().hex[:10]}@example.test"
        cls.password = "Browser-test-password-2026!"
        cls.base_url = None
        cls.server = None
        cls.browser = None
        cls.playwright = None

        cls.db.execute(
            """INSERT INTO companies
               (company_id,name,sector,sds_done,main_goal)
               VALUES (?,?,?,1,?)""",
            (
                cls.company_id,
                "شركة اختبار اعتماد Drive",
                "tech",
                "اختبار اعتماد مقتطفات Drive",
            ),
        )
        cls.db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id,is_admin)
               VALUES (?,?,?,?,1)""",
            (
                cls.account_id,
                cls.email,
                generate_password_hash(cls.password),
                cls.company_id,
            ),
        )
        cls.db.execute(
            """INSERT INTO cases
               (case_id,company_id,case_title)
               VALUES (?,?,?)""",
            (
                cls.case_id,
                cls.company_id,
                "قضية اختبار اعتماد Drive المرئي",
            ),
        )
        cls.db.execute(
            """INSERT INTO drive_files
               (drive_file_id,name,mime_type,is_folder,drive_state,access_status,
                company_id,confidentiality,knowledge_classification,web_view_link)
               VALUES (?,?,?,0,'active','ok',?,?,?,?)""",
            (
                cls.file_id,
                "ملف Drive سري لاختبار المتصفح.txt",
                "text/plain",
                cls.company_id,
                "CLIENT_CONFIDENTIAL",
                "client_context",
                "https://drive/browser-test",
            ),
        )
        cls.db.commit()

        extracted = extract_selected_drive_excerpt(
            cls.db,
            cls.file_id,
            section_locator="قسم المتصفح 1",
            start_char=0,
            end_char=4000,
            case_id=cls.case_id,
            actor=cls.account_id,
            actor_company_id=cls.company_id,
            mirror=FakeDrive(),
        )
        if not extracted.get("success"):
            cls._cleanup_fixture()
            raise RuntimeError(f"تعذر تجهيز مقتطف اختبار المتصفح: {extracted}")
        cls.excerpt_id = extracted["excerpt_id"]

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.port = sock.getsockname()[1]
        sock.close()
        server_env = os.environ.copy()
        server_env.update(
            {
                "PORT": str(cls.port),
                "SANA_ENV": "production",
                "REPLIT_DEPLOYMENT": "1",
                "SESSION_SECRET": "drive-browser-test-session-secret",
            }
        )
        cls.server = subprocess.Popen(
            [sys.executable, "app.py"],
            cwd=BASE_DIR,
            env=server_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        try:
            for _ in range(60):
                if cls.server.poll() is not None:
                    raise RuntimeError("خادم اختبار المتصفح توقف قبل الجاهزية")
                try:
                    with urlopen(f"{cls.base_url}/login", timeout=1) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(0.25)
            else:
                raise RuntimeError("انتهت مهلة تشغيل خادم اختبار المتصفح")
        except Exception:
            cls._stop_server()
            cls._cleanup_fixture()
            raise

    @classmethod
    def tearDownClass(cls):
        if cls.browser:
            cls.browser.close()
        if cls.playwright:
            cls.playwright.stop()
        cls._stop_server()
        cls._cleanup_fixture()
        cls.db.close()

    def setUp(self):
        self.page = None
        self._trace_started = False
        self._diagnostics_run_id = None
        self._diagnostics_dir = None
        self._diagnostics_screenshot = None
        self._diagnostics_trace = None

    def tearDown(self):
        try:
            if self._trace_started:
                if self._outcome.success:
                    self.page.context.tracing.stop()
                    if self._diagnostics_dir:
                        shutil.rmtree(self._diagnostics_dir, ignore_errors=True)
                else:
                    self._save_browser_failure_diagnostics()
        finally:
            if self.page:
                with suppress(Exception):
                    self.page.close()
                self.page = None

    def _start_browser_trace(self, page):
        """ابدأ التتبع بعد المصادقة حتى لا تدخل بيانات تسجيل الدخول في الأثر."""
        run_id = uuid.uuid4().hex
        self._diagnostics_run_id = run_id
        self._diagnostics_dir = (
            BASE_DIR / ".browser-diagnostics" / f"drive-excerpt-{run_id}"
        )
        self._diagnostics_screenshot = (
            self._diagnostics_dir / f"failure-{run_id}.png"
        )
        self._diagnostics_trace = self._diagnostics_dir / f"failure-{run_id}.zip"
        page.context.tracing.start(
            name=f"drive-excerpt-{run_id}",
            screenshots=True,
            snapshots=True,
            sources=False,
        )
        self._trace_started = True

    def _save_browser_failure_diagnostics(self):
        diagnostics_dir = self._diagnostics_dir
        if not diagnostics_dir:
            return
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        errors = []
        try:
            self.page.screenshot(
                path=str(self._diagnostics_screenshot),
                full_page=True,
            )
        except Exception as exc:
            errors.append(f"screenshot: {exc}")
        try:
            self.page.context.tracing.stop(path=str(self._diagnostics_trace))
        except Exception as exc:
            errors.append(f"trace: {exc}")
        print(
            "Drive browser failure diagnostics "
            f"(run {self._diagnostics_run_id}):\n"
            f"  screenshot: {self._diagnostics_screenshot}\n"
            f"  trace: {self._diagnostics_trace}"
            + (f"\n  capture errors: {'; '.join(errors)}" if errors else ""),
            file=sys.stderr,
            flush=True,
        )
        self._trace_started = False

    @classmethod
    def _stop_server(cls):
        if cls.server and cls.server.poll() is None:
            cls.server.terminate()
            try:
                cls.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.server.kill()
                cls.server.wait(timeout=5)

    @classmethod
    def _cleanup_fixture(cls):
        if not getattr(cls, "db", None):
            return
        try:
            cls.db.rollback()
            object_rows = cls.db.execute(
                """SELECT entity_id FROM drive_provenance_links
                   WHERE entity_type='knowledge_object' AND drive_file_id=?""",
                (getattr(cls, "file_id", ""),),
            ).fetchall()
            object_ids = [row["entity_id"] for row in object_rows]
            cls.db.execute(
                "DELETE FROM drive_provenance_links WHERE drive_file_id=?",
                (getattr(cls, "file_id", ""),),
            )
            cls.db.execute(
                "DELETE FROM drive_knowledge_sources WHERE drive_file_id=?",
                (getattr(cls, "file_id", ""),),
            )
            for object_id in object_ids:
                cls.db.execute(
                    "DELETE FROM knowledge_objects WHERE object_id=?",
                    (object_id,),
                )
            cls.db.execute(
                "DELETE FROM drive_source_excerpts WHERE drive_file_id=?",
                (getattr(cls, "file_id", ""),),
            )
            cls.db.execute(
                "DELETE FROM research_sources WHERE drive_file_id=?",
                (getattr(cls, "file_id", ""),),
            )
            cls.db.execute(
                "DELETE FROM drive_files WHERE drive_file_id=?",
                (getattr(cls, "file_id", ""),),
            )
            cls.db.execute(
                "DELETE FROM cases WHERE case_id=?",
                (getattr(cls, "case_id", ""),),
            )
            cls.db.execute(
                "DELETE FROM user_accounts WHERE account_id=?",
                (getattr(cls, "account_id", ""),),
            )
            cls.db.execute(
                "DELETE FROM companies WHERE company_id=?",
                (getattr(cls, "company_id", ""),),
            )
            cls.db.commit()
        except Exception:
            cls.db.rollback()
            raise

    def test_browser_admin_can_review_confidential_drive_excerpt(self):
        from playwright.sync_api import sync_playwright
        type(self).playwright = sync_playwright().start()
        type(self).browser = self.playwright.chromium.launch(headless=True)

        page = self.browser.new_page(viewport={"width": 1440, "height": 1100})
        page.goto(f"{self.base_url}/login", wait_until="networkidle")
        page.locator("#email").fill(self.email)
        page.locator("#password").fill(self.password)
        page.locator("#submitBtn").click()
        page.wait_for_url("**/home", timeout=10_000)
        self.page = page
        self._start_browser_trace(page)

        page.goto(f"{self.base_url}/knowledge", wait_until="networkidle")
        card = page.locator(f"#excerpt-{self.excerpt_id}")
        card.wait_for(state="visible", timeout=10_000)
        self.assertIn(
            "ملف Drive سري لاختبار المتصفح.txt",
            card.inner_text(),
        )
        self.assertIn("قسم المتصفح 1", card.inner_text())
        self.assertIn("شركة اختبار اعتماد Drive", card.inner_text())
        self.assertIn("قضية اختبار اعتماد Drive المرئي", card.inner_text())
        self.assertIn("CLIENT_CONFIDENTIAL", card.inner_text())
        self.assertIn("الاعتماد محظور", card.inner_text())

        card.locator(".review-reason").fill("المقتطف يحتاج إخفاء هوية قبل النشر.")
        card.locator(".review-references").fill("BROWSER-DRIVE-REVIEW")
        with page.expect_response(
            lambda response: (
                response.request.method == "PATCH"
                and "/api/knowledge/drive/excerpts/" in response.url
            )
        ) as blocked_response:
            card.get_by_role("button", name="اعتماد وإنشاء الكائنات").click()
        blocked_payload = blocked_response.value.json()
        self.assertFalse(blocked_payload["success"])
        self.assertEqual(
            "ANONYMIZATION_DOCUMENTATION_REQUIRED",
            blocked_payload["error"],
        )
        self.assertIn("المقتطف السري لا يُنشر", card.locator(".review-msg").inner_text())
        self.assertTrue(card.is_visible())

        card.locator(".anonymized-text").fill(
            "يجب مراجعة طلبات العملاء قبل اعتمادها."
        )
        card.locator(".anonymization-notes").fill(
            "أزيل اسم العميل والمعرف الداخلي، وعُمّم الوصف إلى طلبات العملاء."
        )
        first_object = card.locator(".ko-object").nth(0)
        first_object.locator(".ko-category").fill("browser-review")
        first_object.locator(".ko-title").fill("قاعدة مراجعة طلبات العملاء")
        first_object.locator(".ko-statement").fill(
            "راجع طلبات العملاء قبل اعتمادها."
        )
        first_object.locator(".ko-when").fill("عند وصول طلب عميل جديد.")
        first_object.locator(".ko-not-when").fill("لا ينطبق على الطلبات الداخلية.")

        card.get_by_role("button", name="+ كائن صغير آخر").click()
        self.assertEqual(2, card.locator(".ko-object").count())
        second_object = card.locator(".ko-object").nth(1)
        second_object.locator(".ko-category").fill("browser-review")
        second_object.locator(".ko-title").fill("توثيق قرار المراجعة")
        second_object.locator(".ko-statement").fill(
            "وثّق قرار المراجعة بمصدر واضح."
        )
        second_object.locator(".ko-when").fill("عند اعتماد مقتطف من Drive.")
        second_object.locator(".ko-not-when").fill("لا ينطبق دون مرجع.")

        with page.expect_response(
            lambda response: (
                response.request.method == "PATCH"
                and "/api/knowledge/drive/excerpts/" in response.url
            )
        ) as approved_response:
            card.get_by_role("button", name="اعتماد وإنشاء الكائنات").click()
        approved_payload = approved_response.value.json()
        self.assertTrue(approved_payload["success"], approved_payload)
        self.assertEqual(2, len(approved_payload["knowledge_objects"]))
        page.locator("#driveExcerpts .excerpt-card").wait_for(
            state="detached", timeout=10_000
        )
        self.assertNotIn(
            "ملف Drive سري لاختبار المتصفح.txt",
            page.locator("#driveExcerpts").inner_text(),
        )

        review = self.db.execute(
            """SELECT review_status FROM drive_source_excerpts
               WHERE excerpt_id=?""",
            (self.excerpt_id,),
        ).fetchone()
        object_count = self.db.execute(
            """SELECT COUNT(*) AS count FROM drive_provenance_links
               WHERE entity_type='knowledge_object' AND drive_file_id=?""",
            (self.file_id,),
        ).fetchone()["count"]
        self.assertEqual("approved", review["review_status"])
        self.assertEqual(2, object_count)


if __name__ == "__main__":
    unittest.main()
