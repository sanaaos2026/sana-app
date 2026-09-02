"""اختبارات عزل صندوق البحث الخاص وحقوق استخدام مواده.

تشغيل:
    cd sana_full && python3 -m unittest test_research_source_isolation.py -v

تنشئ الاختبارات موادًا مؤقتة بحسابات موجودة، ثم تحذفها في tearDownClass.
لا تُستخدم إعادة تهيئة قاعدة البيانات ولا تُلمس مواد المستخدمين الموجودة.
"""
import os
import sys
import unittest
import uuid
from datetime import date, timedelta
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import _connect_pg, app
from sana_knowledge import (
    create_research_source,
    create_uploaded_research_source,
    get_research_source_file,
    list_research_sources,
    update_research_source_status,
)


class PrivateResearchIsolationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL غير مضبوط")
        cls.db = _connect_pg()
        admin = cls.db.execute(
            """SELECT account_id, company_id, email
               FROM user_accounts WHERE is_admin=1
               ORDER BY account_id LIMIT 1"""
        ).fetchone()
        other = cls.db.execute(
            """SELECT account_id, company_id, email
               FROM user_accounts
               WHERE account_id <> ?
               ORDER BY account_id LIMIT 1""",
            (admin["account_id"],),
        ).fetchone() if admin else None
        if not admin or not other:
            cls.db.close()
            raise unittest.SkipTest("تحتاج قاعدة الاختبار حسابًا إداريًا وحسابًا آخر")
        cls.admin = dict(admin)
        cls.other = dict(other)
        cls.created_ids = []
        cls.run_id = uuid.uuid4().hex[:10].upper()

        try:
            cls.private_source = cls._upload(
                f"private-owner-{cls.run_id}.txt",
                f"private material owned by the second account {cls.run_id}".encode(),
                rights_status="owned",
            )
            cls.expired_source = cls._upload(
                f"expired-rights-{cls.run_id}.txt",
                f"material whose rights have expired {cls.run_id}".encode(),
                rights_status="owned",
                rights_expires_at=(date.today() - timedelta(days=1)).isoformat(),
            )
            cls.restricted_source = cls._upload(
                f"restricted-rights-{cls.run_id}.txt",
                f"material with restricted rights {cls.run_id}".encode(),
                rights_status="restricted",
            )
        except Exception:
            cls._cleanup()
            raise

    @classmethod
    def _upload(cls, filename, content, **extra):
        payload = {
            "title": filename,
            "source_kind": "file",
            "rights_status": "owned",
            **extra,
        }
        result = create_uploaded_research_source(
            cls.db,
            filename=filename,
            mime_type="text/plain",
            content=content,
            payload=payload,
            owner_account_id=cls.other["account_id"],
        )
        if not result["success"]:
            raise AssertionError(result)
        cls.created_ids.append(result["research_source_id"])
        return result["research_source_id"]

    @classmethod
    def _cleanup(cls):
        if getattr(cls, "db", None) is None:
            return
        for source_id in getattr(cls, "created_ids", []):
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
        # تطلق القراءات السابقة أقفال PostgreSQL قبل اختبار HTTP على اتصال آخر.
        self.db.rollback()

    def tearDown(self):
        self.db.rollback()

    def test_owner_is_required_for_private_material(self):
        result = create_research_source(
            self.db,
            {
                "title": "مادة بلا مالك",
                "source_kind": "summary",
                "origin": "manual",
            },
        )
        self.assertEqual(
            {"success": False, "error": "PRIVATE_OWNER_REQUIRED"},
            result,
        )
        self.assertEqual(
            [],
            list_research_sources(self.db, owner_account_id=None),
        )
        upload_result = create_uploaded_research_source(
            self.db,
            filename=f"missing-owner-{self.run_id}.txt",
            mime_type="text/plain",
            content=f"missing owner {self.run_id}".encode(),
            payload={"title": "مادة مرفوعة بلا مالك", "source_kind": "file"},
            owner_account_id=None,
        )
        self.assertEqual(
            {"success": False, "error": "PRIVATE_OWNER_REQUIRED"},
            upload_result,
        )

    def test_database_rejects_private_material_without_owner(self):
        with self.assertRaises(Exception):
            self.db.execute(
                """INSERT INTO research_sources
                   (research_source_id, title, source_kind, origin, is_private)
                   VALUES (?, ?, 'summary', 'manual', 1)""",
                (f"RS-NO-OWNER-{self.run_id}", "مادة خاصة بلا مالك"),
            )
        self.db.rollback()

    def test_rights_statuses_have_explicit_owner_scoped_behavior(self):
        statuses = ("pending", "owned", "licensed", "public", "restricted")
        for rights_status in statuses:
            source_id = self._upload(
                f"{rights_status}-{self.run_id}.txt",
                f"{rights_status} rights {self.run_id}".encode(),
                rights_status=rights_status,
            )
            row = next(
                item for item in list_research_sources(
                    self.db, owner_account_id=self.other["account_id"]
                )
                if item["research_source_id"] == source_id
            )
            self.assertEqual(rights_status, row["rights_status"])
            file_row, error = get_research_source_file(
                self.db, source_id, self.other["account_id"]
            )
            if rights_status == "restricted":
                self.assertIsNone(file_row)
                self.assertEqual("RIGHTS_RESTRICTED", error)
            else:
                self.assertIsNotNone(file_row)
                self.assertIsNone(error)

    def test_other_account_cannot_read_or_download_owner_material(self):
        owner_rows = list_research_sources(
            self.db, owner_account_id=self.other["account_id"]
        )
        self.assertIn(
            self.private_source,
            {row["research_source_id"] for row in owner_rows},
        )

        foreign_rows = list_research_sources(
            self.db, owner_account_id=self.admin["account_id"]
        )
        self.assertNotIn(
            self.private_source,
            {row["research_source_id"] for row in foreign_rows},
        )

        foreign_file, foreign_error = get_research_source_file(
            self.db, self.private_source, self.admin["account_id"]
        )
        missing_file, missing_error = get_research_source_file(
            self.db, "RS-DOES-NOT-EXIST", self.admin["account_id"]
        )
        self.assertIsNone(foreign_file)
        self.assertIsNone(missing_file)
        self.assertEqual("RESEARCH_SOURCE_NOT_FOUND", foreign_error)
        self.assertEqual(missing_error, foreign_error)
        self.assertEqual(
            "RESEARCH_SOURCE_NOT_FOUND",
            update_research_source_status(
                self.db,
                self.private_source,
                "approved",
                self.admin["account_id"],
            )["error"],
        )

        self.db.rollback()
        client = app.test_client()
        with client.session_transaction() as session:
            session["account_id"] = self.admin["account_id"]
            session["company_id"] = self.admin["company_id"]
            session["email"] = self.admin["email"]
        csrf_enabled = app.config.get("WTF_CSRF_ENABLED", True)
        app.config["WTF_CSRF_ENABLED"] = False
        try:
            update_request = client.patch(
                f"/api/knowledge/private-sources/{self.private_source}",
                json={"review_status": "approved"},
            )
        finally:
            app.config["WTF_CSRF_ENABLED"] = csrf_enabled
        self.assertEqual(404, update_request.status_code)
        self.assertEqual(
            "RESEARCH_SOURCE_NOT_FOUND",
            update_request.get_json()["error"],
        )

    def test_authenticated_admin_api_does_not_leak_foreign_material(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["account_id"] = self.admin["account_id"]
            session["company_id"] = self.admin["company_id"]
            session["email"] = self.admin["email"]

        listing = client.get("/api/knowledge/private-sources")
        self.assertEqual(200, listing.status_code)
        listed_ids = {
            row["research_source_id"] for row in listing.get_json()["data"]
        }
        self.assertNotIn(self.private_source, listed_ids)

        download = client.get(
            f"/api/knowledge/private-sources/{self.private_source}/download"
        )
        self.assertEqual(404, download.status_code)
        self.assertEqual(
            "RESEARCH_SOURCE_NOT_FOUND",
            download.get_json()["error"],
        )

    def test_review_states_are_owner_scoped(self):
        for status in ("approved", "rejected", "archived", "inbox"):
            result = update_research_source_status(
                self.db,
                self.private_source,
                status,
                self.other["account_id"],
            )
            self.assertTrue(result["success"], result)
            self.assertEqual(status, result["review_status"])
            rows = list_research_sources(
                self.db,
                review_status=status,
                owner_account_id=self.other["account_id"],
            )
            self.assertIn(
                self.private_source,
                {row["research_source_id"] for row in rows},
            )

    def test_expired_rights_stay_visible_but_block_approval_and_download(self):
        rows = list_research_sources(
            self.db, owner_account_id=self.other["account_id"]
        )
        expired = next(
            row for row in rows
            if row["research_source_id"] == self.expired_source
        )
        self.assertEqual(1, expired["rights_expired"])
        self.assertEqual(
            "RIGHTS_EXPIRED",
            update_research_source_status(
                self.db,
                self.expired_source,
                "approved",
                self.other["account_id"],
            )["error"],
        )
        file_row, error = get_research_source_file(
            self.db, self.expired_source, self.other["account_id"]
        )
        self.assertIsNone(file_row)
        self.assertEqual("RIGHTS_EXPIRED", error)

        rejected = update_research_source_status(
            self.db,
            self.expired_source,
            "rejected",
            self.other["account_id"],
        )
        self.assertTrue(rejected["success"], rejected)

    def test_restricted_rights_block_approval_without_cross_account_leak(self):
        result = update_research_source_status(
            self.db,
            self.restricted_source,
            "approved",
            self.other["account_id"],
        )
        self.assertEqual("RIGHTS_RESTRICTED", result["error"])
        file_row, error = get_research_source_file(
            self.db, self.restricted_source, self.other["account_id"]
        )
        self.assertIsNone(file_row)
        self.assertEqual("RIGHTS_RESTRICTED", error)


if __name__ == "__main__":
    unittest.main(verbosity=2)