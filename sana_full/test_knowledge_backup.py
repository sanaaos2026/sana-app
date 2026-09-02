import json
import os
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

from knowledge_backup import (
    ConnectorSession,
    DRIVE_SCOPES,
    DriveMirror,
    _next_scheduler_delay,
    _jsonable,
    _load_service_account_info,
    _normalize_drive_folder_id,
    missing_backup_dates,
    build_snapshot,
    restore_snapshot,
    run_backup,
    verify_snapshot,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class KnowledgeBackupTests(unittest.TestCase):
    def test_service_account_scopes_can_read_existing_shared_root_without_full_drive_access(self):
        self.assertIn("https://www.googleapis.com/auth/drive.readonly", DRIVE_SCOPES)
        self.assertIn("https://www.googleapis.com/auth/drive.file", DRIVE_SCOPES)
        self.assertNotIn("https://www.googleapis.com/auth/drive", DRIVE_SCOPES)

    def test_missing_backup_dates_includes_gaps_and_today(self):
        from datetime import date

        class Result:
            def __init__(self, rows=None):
                self.rows = rows or []

            def fetchall(self):
                return self.rows

        class Db:
            def execute(self, query, params=()):
                if query.lstrip().startswith("SELECT snapshot_version"):
                    return Result([
                        {"snapshot_version": "v2026.08.30", "status": "success"},
                        {"snapshot_version": "v2026.09.01", "status": "failed"},
                    ])
                return Result()

            def commit(self):
                pass

        self.assertEqual(
            [date(2026, 8, 31), date(2026, 9, 1)],
            missing_backup_dates(Db(), today=date(2026, 9, 1)),
        )

    def test_missing_drive_configuration_is_explicit(self):
        with patch.dict(
            os.environ,
            {
                "GOOGLE_SERVICE_ACCOUNT_JSON": "",
                "GOOGLE_DRIVE_ROOT_FOLDER_ID": "",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "DRIVE_NOT_CONFIGURED"):
                DriveMirror()

    def test_invalid_service_account_json_is_explicit(self):
        with patch.dict(
            os.environ,
            {
                "GOOGLE_SERVICE_ACCOUNT_JSON": "{not-json",
                "GOOGLE_DRIVE_ROOT_FOLDER_ID": "shared-folder",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "DRIVE_CREDENTIALS_INVALID"):
                DriveMirror()

    def test_service_account_reader_accepts_secure_form_wrappers(self):
        payload = {
            "type": "service_account",
            "client_email": "backup@example.invalid",
            "private_key": "private-key",
        }
        direct = json.dumps(payload)
        wrapped = f"```json\n{direct}\n```"
        quoted = json.dumps(direct)
        for value in (direct, wrapped, quoted):
            with self.subTest(value=value[:10]):
                self.assertEqual(payload, _load_service_account_info(value))

    def test_permission_failure_is_not_silent(self):
        mirror = object.__new__(DriveMirror)
        mirror.http = FakeHttp(
            [FakeResponse(403, text="The service account cannot access this folder")]
        )
        with self.assertRaisesRegex(RuntimeError, "DRIVE_HTTP_403"):
            mirror._json_request("GET", "/drive/v3/files")

    @patch("knowledge_backup.subprocess.run")
    def test_connector_session_passes_binary_without_exposing_credentials(self, run):
        body = json.dumps({"files": [{"id": "folder-1"}]}).encode()
        run.return_value.stdout = json.dumps({
            "ok": True,
            "status": 200,
            "body_base64": __import__("base64").b64encode(body).decode(),
        }).encode()
        response = ConnectorSession().request(
            "POST",
            "https://www.googleapis.com/upload/drive/v3/files",
            data=b"snapshot",
            headers={"Content-Type": "application/zip"},
        )
        self.assertTrue(response.ok)
        self.assertEqual("folder-1", response.json()["files"][0]["id"])
        bridge_request = json.loads(run.call_args.kwargs["input"])
        self.assertEqual("c25hcHNob3Q=", bridge_request["body_base64"])

    def test_permission_recovery_can_retry_after_access_is_restored(self):
        mirror = object.__new__(DriveMirror)
        mirror.http = FakeHttp(
            [
                FakeResponse(403, text="forbidden"),
                FakeResponse(200, {"files": [{"id": "folder-1", "name": "00 - اقرأ أولًا"}]}),
            ]
        )
        with self.assertRaisesRegex(RuntimeError, "DRIVE_HTTP_403"):
            mirror.find("root", "00 - اقرأ أولًا")
        recovered = mirror.find("root", "00 - اقرأ أولًا")
        self.assertEqual("folder-1", recovered["id"])
        self.assertEqual(2, len(mirror.http.calls))

    def test_connector_mode_bootstraps_a_stable_root_folder(self):
        mirror = object.__new__(DriveMirror)
        mirror.auth_mode = "replit_google_drive"
        mirror.root_id = "."
        with patch.object(
            mirror,
            "ensure_folder",
            return_value={"id": "sana-root-123", "name": "Sana Knowledge Backups"},
        ) as ensure_folder:
            self.assertEqual("sana-root-123", mirror.ensure_root())
            self.assertEqual("sana-root-123", mirror.ensure_root())
        ensure_folder.assert_called_once_with("root", "Sana Knowledge Backups")

    def test_folder_id_normalizes_quotes_and_drive_urls(self):
        self.assertEqual(".", _normalize_drive_folder_id('"."'))
        self.assertEqual(
            "folder_123-abc",
            _normalize_drive_folder_id(
                "https://drive.google.com/drive/folders/folder_123-abc?usp=sharing"
            ),
        )

    def test_snapshot_verification_and_restore_manifest_survive_round_trip(self):
        class EmptyDb:
            def execute(self, query, params=()):
                class Result:
                    def fetchone(self):
                        return None

                    def fetchall(self):
                        return []

                return Result()

        with tempfile.TemporaryDirectory() as directory:
            path, manifest = build_snapshot(EmptyDb(), output_dir=directory)
            verification = verify_snapshot(path)
            self.assertTrue(verification["ok"], verification["errors"])
            self.assertEqual(manifest["snapshot_version"], verification["snapshot_version"])
            destination = os.path.join(directory, "restored")
            with self.assertRaisesRegex(RuntimeError, "TRUSTED_ARCHIVE_SHA256_REQUIRED"):
                restore_snapshot(path, destination)
            restored = restore_snapshot(path, destination, manifest["archive_sha256"])
            self.assertTrue(restored["ok"])
            with open(path, "ab") as archive:
                archive.write(b"tampered")
            tampered = verify_snapshot(path, manifest["archive_sha256"])
            self.assertFalse(tampered["ok"])
            self.assertIn("archive_sha256", tampered["errors"])

    def test_failed_drive_run_can_recover_without_duplicate_daily_record(self):
        class Result:
            def __init__(self, one=None):
                self.one = one

            def fetchone(self):
                return self.one

        class BackupDb:
            def __init__(self):
                self.row = None

            def execute(self, query, params=()):
                normalized = " ".join(query.split())
                if "pg_try_advisory_lock" in normalized:
                    return Result({"locked": True})
                if "pg_advisory_unlock" in normalized:
                    return Result()
                if normalized.startswith("SELECT * FROM knowledge_backup_runs"):
                    return Result(dict(self.row) if self.row else None)
                if normalized.startswith("INSERT INTO knowledge_backup_runs"):
                    self.row = {
                        "run_id": params[0],
                        "snapshot_version": params[1],
                        "trigger_type": params[2],
                        "status": params[3],
                        "started_at": params[4],
                    }
                elif "SET trigger_type=" in normalized:
                    self.row.update(
                        trigger_type=params[0],
                        status="running",
                        started_at=params[1],
                        completed_at=None,
                        error_message=None,
                    )
                elif "SET status='success'" in normalized:
                    self.row.update(
                        status="success",
                        completed_at=params[0],
                        file_count=params[1],
                        total_bytes=params[2],
                        snapshot_sha256=params[3],
                        drive_file_id=params[4],
                        drive_web_link=params[5],
                        manifest=params[6],
                        error_message=None,
                    )
                elif "SET status='failed'" in normalized:
                    self.row.update(
                        status="failed",
                        completed_at=params[0],
                        error_message=params[1],
                    )
                return Result()

            def commit(self):
                pass

            def rollback(self):
                pass

        manifest = {
            "snapshot_version": "v2026.09.01",
            "archive_sha256": "trusted-sha",
            "archive_bytes": 10,
            "file_count": 1,
        }
        db = BackupDb()
        with tempfile.NamedTemporaryFile() as snapshot, \
                patch("knowledge_backup._now_riyadh") as now, \
                patch("knowledge_backup.build_snapshot", return_value=(snapshot.name, manifest)), \
                patch("knowledge_backup.verify_snapshot", return_value={"ok": True}), \
                patch("knowledge_backup.DriveMirror") as mirror:
            from datetime import datetime
            from knowledge_backup import RIYADH
            now.return_value = datetime(2026, 9, 1, 12, 0, tzinfo=RIYADH)
            mirror.return_value.upload_snapshot.side_effect = [
                RuntimeError("DRIVE_HTTP_403"),
                {"id": "file-1", "webViewLink": "https://drive.invalid/file-1"},
            ]
            failed = run_backup(db, "manual")
            recovered = run_backup(db, "manual")
        self.assertEqual("failed", failed["status"])
        self.assertEqual("success", recovered["status"])
        self.assertEqual(failed["run_id"], recovered["run_id"])
        self.assertEqual("success", db.row["status"])

    def test_scheduler_reconciles_midnight_crossing_and_retries_failures(self):
        from datetime import datetime
        from knowledge_backup import BACKUP_RETRY_SECONDS, RIYADH

        before_midnight = datetime(2026, 9, 1, 23, 59, 59, tzinfo=RIYADH).date()
        after_midnight = datetime(2026, 9, 2, 0, 0, 1, tzinfo=RIYADH)
        same_day = datetime(2026, 9, 1, 12, 0, tzinfo=RIYADH)
        self.assertEqual(
            1,
            _next_scheduler_delay(before_midnight, "success", after_midnight),
        )
        self.assertEqual(
            BACKUP_RETRY_SECONDS,
            _next_scheduler_delay(before_midnight, "failed", same_day),
        )

    def test_database_specific_values_are_serialized_without_recursion(self):
        encoded = json.loads(json.dumps(
            {"amount": Decimal("10.50"), "binary": b"\x00\xff"},
            default=_jsonable,
        ))
        self.assertEqual("10.50", encoded["amount"])
        self.assertEqual("base64", encoded["binary"]["encoding"])
        self.assertEqual("AP8=", encoded["binary"]["data"])


if __name__ == "__main__":
    unittest.main()