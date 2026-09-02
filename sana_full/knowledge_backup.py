"""نسخ مكتبة معرفة سنع إلى Google Drive بحساب خدمة مستقل.

النسخة المحلية قابلة للتحقق والاستعادة دون اتصال Drive. الرفع لا يسجل نجاحًا
إلا بعد اكتمال رفع اللقطة والفهرس ورسائل التشغيل.
"""
import ast
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, urlencode

from database_config import acquire_schema_lock
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

RIYADH = timezone(timedelta(hours=3))
BACKUP_SCHEMA_VERSION = "1.0"
LOCK_KEY = 813_260_901
BACKUP_RETRY_SECONDS = 300
DRIVE_SCOPES = (
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
)
ROOT = Path(__file__).resolve().parent
CONNECTOR_BRIDGE = ROOT / "drive_connector.mjs"
SNAPSHOT_DIR = ROOT / ".backup_snapshots"
EXPORT_TABLES = (
    "knowledge_sources",
    "knowledge_versions",
    "knowledge_objects",
    "knowledge_links",
    "methodology_docs",
    "research_sources",
    "research_source_files",
    "research_source_chunks",
    "knowledge_research_config",
    "knowledge_research_runs",
    "knowledge_research_candidates",
    "knowledge_research_alerts",
    "knowledge_research_gaps",
)

README_ROOT = """# 000 - اقرأ أولًا / Read First

هذا المجلد مرآة تشغيلية لمكتبة معرفة سنع، وليس المصدر التشغيلي الوحيد.
This folder is an operational mirror of Sana Knowledge, not its only live source.

- النسخة المرجعية: أحدث الوثائق المعتمدة وفهرسها.
- النسخ اليومية: لقطات قابلة للتحقق والاستعادة.
- الحقوق: لا تضف مادة محمية دون حق استخدام واضح.
- المراجعة: المواد الخاصة وقيد المراجعة لا تصبح معرفة مشتركة تلقائيًا.
- الاستعادة: تحقق من manifest والبصمات قبل استبدال أي بيانات.
"""
README_REFERENCE = """# 000 - اقرأ أولًا / Read First

يحتوي هذا المجلد النسخة المرجعية الحالية فقط. تُحدّث الملفات بالاسم نفسه لمنع
تراكم نسخ غير محدودة. راجع `reference-manifest.json` لمعرفة الإصدار والبصمات.
"""
README_DAILY = """# 000 - اقرأ أولًا / Read First

كل ملف ZIP لقطة يومية مستقلة تحمل إصدار `vYYYY.MM.DD` بتوقيت الرياض.
لا تُعد النسخة ناجحة إلا إذا كانت حالة التشغيل `success` وتطابقت بصمات manifest.
إذا فات يوم بسبب توقف الخدمة، تُنشأ له لقطة مصالحة متأخرة ويظهر ذلك صراحةً في
`capture_timing=late_reconciliation` مع وقت الالتقاط الحقيقي في `generated_at`.
"""


def _now_riyadh():
    return datetime.now(RIYADH)


def _jsonable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, memoryview):
        return bytes(value)
    if isinstance(value, bytes):
        return {
            "encoding": "base64",
            "data": base64.b64encode(value).decode("ascii"),
        }
    raise TypeError(f"Unsupported snapshot value type: {type(value).__name__}")


def _safe_name(value):
    value = re.sub(r"[^\w.\-أ-ي]+", "-", str(value or "file"), flags=re.UNICODE)
    return value.strip("-")[:180] or "file"


def _sha256(data):
    return hashlib.sha256(data).hexdigest()

def _normalize_drive_folder_id(value):
    candidate = str(value or "").strip().strip("\"'")
    match = re.search(r"/folders/([A-Za-z0-9_-]+)", candidate)
    return match.group(1) if match else candidate


def _load_service_account_info(raw):
    candidate = str(raw or "").strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        lines = candidate.splitlines()
        candidate = "\n".join(lines[1:-1]).strip()
    if candidate.startswith("GOOGLE_SERVICE_ACCOUNT_JSON="):
        candidate = candidate.split("=", 1)[1].strip()

    parsed = None
    attempts = [candidate]
    try:
        decoded = base64.b64decode(candidate, validate=True).decode("utf-8")
        attempts.append(decoded.strip())
    except (ValueError, UnicodeDecodeError):
        pass

    for value in attempts:
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            try:
                parsed = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                continue
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError:
                continue
        if isinstance(parsed, dict):
            break

    if (
        not isinstance(parsed, dict)
        or parsed.get("type") != "service_account"
        or not parsed.get("client_email")
        or not parsed.get("private_key")
    ):
        raise RuntimeError("DRIVE_CREDENTIALS_INVALID: JSON حساب الخدمة غير صالح أو ناقص.")
    return parsed

class ConnectorResponse:
    def __init__(self, payload):
        self.status_code = int(payload.get("status") or 502)
        self.ok = bool(payload.get("ok"))
        body = base64.b64decode(payload.get("body_base64") or "")
        self.content = body
        self.text = body.decode("utf-8", errors="replace") or str(payload.get("error") or "")

    def json(self):
        return json.loads(self.text or "{}")


class ConnectorSession:
    def request(self, method, url, timeout=90, **kwargs):
        if not CONNECTOR_BRIDGE.is_file():
            raise RuntimeError("DRIVE_CONNECTOR_BRIDGE_MISSING")
        path = url.removeprefix("https://www.googleapis.com")
        params = kwargs.get("params") or {}
        if params:
            separator = "&" if "?" in path else "?"
            path = f"{path}{separator}{urlencode(params)}"
        body = kwargs.get("data")
        if isinstance(body, str):
            body = body.encode("utf-8")
        if kwargs.get("json") is not None:
            body = json.dumps(kwargs["json"], ensure_ascii=False).encode("utf-8")
        request = {
            "method": method,
            "path": path,
            "headers": kwargs.get("headers") or {},
            "body_base64": base64.b64encode(body).decode("ascii") if body else None,
        }
        try:
            completed = subprocess.run(
                ["node", str(CONNECTOR_BRIDGE)],
                input=json.dumps(request).encode("utf-8"),
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("DRIVE_CONNECTOR_UNAVAILABLE") from exc
        try:
            payload = json.loads(completed.stdout or b"{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("DRIVE_CONNECTOR_INVALID_RESPONSE") from exc
        return ConnectorResponse(payload)


def ensure_backup_schema(db):
    acquire_schema_lock(db)
    db.execute("""CREATE TABLE IF NOT EXISTS knowledge_backup_runs (
        run_id TEXT PRIMARY KEY,
        snapshot_version TEXT UNIQUE NOT NULL,
        trigger_type TEXT NOT NULL DEFAULT 'scheduled',
        status TEXT NOT NULL DEFAULT 'running',
        started_at TEXT NOT NULL,
        completed_at TEXT,
        file_count INTEGER NOT NULL DEFAULT 0,
        total_bytes BIGINT NOT NULL DEFAULT 0,
        snapshot_sha256 TEXT,
        drive_file_id TEXT,
        drive_web_link TEXT,
        manifest TEXT,
        error_message TEXT
    )""")
    db.commit()


def _table_exists(db, table):
    return bool(db.execute(
        """SELECT 1 FROM information_schema.tables
           WHERE table_schema='public' AND table_name=?""", (table,)
    ).fetchone())


def _export_rows(db, table):
    if not _table_exists(db, table):
        return []
    rows = [dict(row) for row in db.execute(f"SELECT * FROM {table}").fetchall()]
    if table == "research_source_files":
        for row in rows:
            row.pop("file_content", None)
    return rows


def build_snapshot(db, output_dir=None, when=None, snapshot_date=None):
    """يبني ZIP canonical ببيان وبصمة لكل ملف ويعيد مساره وبيانه."""
    when = when or _now_riyadh()
    snapshot_date = snapshot_date or when.date()
    version = f"v{snapshot_date:%Y.%m.%d}"
    output_dir = Path(output_dir or SNAPSHOT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_path = output_dir / f".{version}-{uuid.uuid4().hex}.tmp"
    final_path = output_dir / f"sana-knowledge-{version}.zip"
    entries = {}
    counts = {}

    for table in EXPORT_TABLES:
        rows = _export_rows(db, table)
        counts[table] = len(rows)
        data = json.dumps(rows, ensure_ascii=False, indent=2, default=_jsonable).encode("utf-8")
        entries[f"database/{table}.json"] = data

    if _table_exists(db, "research_source_files"):
        files = db.execute(
            """SELECT file_id, research_source_id, original_name, content, content_hash
               FROM research_source_files ORDER BY research_source_id, file_id"""
        ).fetchall()
        for row in files:
            content = bytes(row["content"] or b"")
            if row["content_hash"] and _sha256(content) != row["content_hash"]:
                raise RuntimeError(f"PRIVATE_FILE_HASH_MISMATCH: {row['file_id']}")
            name = _safe_name(row["original_name"])
            entries[
                f"private-files/{_safe_name(row['research_source_id'])}/{row['file_id']}-{name}"
            ] = content

    canonical = [
        ROOT / "GENERAL_SERVICE_B2B_GROWTH_RULES.md",
        ROOT.parent / "PRODUCT_MASTER_SPEC_AR.md",
        ROOT.parent / "SANA-v1.0-RELEASE" / "02-Scan-Framework.md",
    ]
    for path in canonical:
        if path.is_file():
            entries[f"canonical/{path.name}"] = path.read_bytes()

    manifest = {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "snapshot_version": version,
        "generated_at": when.isoformat(),
        "snapshot_date": snapshot_date.isoformat(),
        "capture_timing": "on_time" if snapshot_date == when.date() else "late_reconciliation",
        "timezone": "Asia/Riyadh",
        "restoration_mode": "verify-first; no automatic overwrite",
        "table_counts": counts,
        "files": {},
    }
    for name, data in sorted(entries.items()):
        manifest["files"][name] = {"sha256": _sha256(data), "bytes": len(data)}
    entries["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False, indent=2
    ).encode("utf-8")
    entries["000 - اقرأ أولًا.md"] = README_ROOT.encode("utf-8")

    with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries.items()):
            archive.writestr(name, data)
    os.replace(temp_path, final_path)
    snapshot_bytes = final_path.read_bytes()
    manifest["archive_sha256"] = _sha256(snapshot_bytes)
    manifest["archive_bytes"] = len(snapshot_bytes)
    manifest["file_count"] = len(entries)
    return final_path, manifest


def verify_snapshot(path, expected_archive_sha256=None):
    """يتحقق من سلامة اللقطة دون تعديل قاعدة البيانات."""
    path = Path(path)
    errors = []
    archive_sha256 = _sha256(path.read_bytes())
    if expected_archive_sha256 and archive_sha256 != expected_archive_sha256:
        errors.append("archive_sha256")
    with zipfile.ZipFile(path, "r") as archive:
        manifest = json.loads(archive.read("manifest.json"))
        names = set(archive.namelist())
        for name, expected in manifest.get("files", {}).items():
            if name not in names:
                errors.append(f"missing:{name}")
                continue
            data = archive.read(name)
            if _sha256(data) != expected["sha256"]:
                errors.append(f"sha256:{name}")
            if len(data) != expected["bytes"]:
                errors.append(f"bytes:{name}")
    return {
        "ok": not errors,
        "snapshot_version": manifest.get("snapshot_version"),
        "checked_files": len(manifest.get("files", {})),
        "errors": errors,
        "archive_sha256": archive_sha256,
        "trusted_archive_sha256": bool(expected_archive_sha256),
    }


def restore_snapshot(path, destination, expected_archive_sha256=None):
    """يفك لقطة متحققة إلى مساحة مؤقتة فقط؛ لا يستبدل قاعدة الإنتاج."""
    if not expected_archive_sha256:
        raise RuntimeError(
            "TRUSTED_ARCHIVE_SHA256_REQUIRED: استخدم بصمة سجل النسخ أو فهرس Drive."
        )
    verification = verify_snapshot(path, expected_archive_sha256)
    if not verification["ok"]:
        raise RuntimeError(f"SNAPSHOT_VERIFY_FAILED: {verification['errors']}")
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "r") as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if target != destination and destination not in target.parents:
                raise RuntimeError(f"UNSAFE_ARCHIVE_PATH: {member.filename}")
        archive.extractall(destination)
    return {
        "ok": True,
        "destination": str(destination),
        "restored_files": len(zipfile.ZipFile(path).namelist()),
        "snapshot_version": verification["snapshot_version"],
    }


class DriveMirror:
    def __init__(self):
        raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        self.root_id = _normalize_drive_folder_id(
            os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "")
        )
        connector_available = bool(
            os.environ.get("REPLIT_CONNECTORS_HOSTNAME")
            or os.environ.get("CONNECTORS_HOSTNAME")
        )
        if not self.root_id and not connector_available:
            raise RuntimeError(
                "DRIVE_NOT_CONFIGURED: أضف GOOGLE_DRIVE_ROOT_FOLDER_ID أو اربط Google Drive."
            )
        if not raw and not connector_available:
            raise RuntimeError(
                "DRIVE_NOT_CONFIGURED: أضف GOOGLE_DRIVE_ROOT_FOLDER_ID واربط "
                "Google Drive أو أضف حساب خدمة صالحًا."
            )
        try:
            info = _load_service_account_info(raw) if raw else None
        except RuntimeError:
            info = None
        if info:
            credentials = service_account.Credentials.from_service_account_info(
                info, scopes=list(DRIVE_SCOPES)
            )
            self.http = AuthorizedSession(credentials)
            self.auth_mode = "service_account"
        elif connector_available:
            self.http = ConnectorSession()
            self.auth_mode = "replit_google_drive"
        else:
            raise RuntimeError("DRIVE_CREDENTIALS_INVALID: JSON حساب الخدمة غير صالح أو ناقص.")

    def ensure_root(self):
        valid_id = bool(re.fullmatch(r"[A-Za-z0-9_-]{10,}", self.root_id))
        if self.auth_mode == "replit_google_drive" and not valid_id:
            root = self.ensure_folder("root", "Sana Knowledge Backups")
            self.root_id = root["id"]
        return self.root_id

    def _json_request(self, method, url, **kwargs):
        response = self.http.request(method, f"https://www.googleapis.com{url}", timeout=90, **kwargs)
        if not response.ok:
            raise RuntimeError(f"DRIVE_HTTP_{response.status_code}: {response.text[:500]}")
        return response.json()

    def find(self, parent_id, name, mime_type=None):
        escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        query = f"name = '{escaped}' and '{parent_id}' in parents and trashed = false"
        if mime_type:
            query += f" and mimeType = '{mime_type}'"
        exact = [
            item for item in self.list_files(
                query, fields="nextPageToken,files(id,name,mimeType,webViewLink,parents,modifiedTime,md5Checksum,size)"
            )
            if item.get("name") == name and (not mime_type or item.get("mimeType") == mime_type)
        ]
        return exact[0] if exact else None

    def list_files(self, query=None, fields=None, page_size=1000):
        """قراءة كل صفحات Drive؛ لا يغيّر أي ملف."""
        files = []
        token = None
        while True:
            params = {
                "q": query or "trashed = false",
                "pageSize": min(max(int(page_size), 1), 1000),
                "fields": fields or (
                    "nextPageToken,files(id,name,mimeType,parents,webViewLink,"
                    "modifiedTime,md5Checksum,size,trashed)"
                ),
                "orderBy": "folder,name",
            }
            if token:
                params["pageToken"] = token
            data = self._json_request("GET", "/drive/v3/files", params=params)
            files.extend(data.get("files", []))
            token = data.get("nextPageToken")
            if not token:
                return files

    def get_file(self, file_id):
        return self._json_request(
            "GET",
            f"/drive/v3/files/{quote(str(file_id), safe='')}",
            params={
                "supportsAllDrives": "true",
                "fields": "id,name,mimeType,trashed,webViewLink,parents,modifiedTime",
            },
        )

    def ensure_folder(self, parent_id, name):
        mime = "application/vnd.google-apps.folder"
        found = self.find(parent_id, name, mime)
        if found:
            return found
        return self._json_request(
            "POST", "/drive/v3/files?fields=id,name,mimeType,webViewLink",
            json={"name": name, "mimeType": mime, "parents": [parent_id]},
        )

    def upsert(self, parent_id, name, content, content_type="application/octet-stream"):
        found = self.find(parent_id, name)
        if found:
            return self._json_request(
                "PATCH",
                f"/upload/drive/v3/files/{found['id']}?uploadType=media&fields=id,name,webViewLink,modifiedTime",
                data=content,
                headers={"Content-Type": content_type},
            )
        boundary = f"sana-{uuid.uuid4().hex}"
        metadata = json.dumps({"name": name, "parents": [parent_id]}, ensure_ascii=False).encode("utf-8")
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
            + metadata
            + f"\r\n--{boundary}\r\nContent-Type: {content_type}\r\n\r\n".encode()
            + content
            + f"\r\n--{boundary}--\r\n".encode()
        )
        return self._json_request(
            "POST", "/upload/drive/v3/files?uploadType=multipart&fields=id,name,webViewLink,modifiedTime",
            data=body, headers={"Content-Type": f"multipart/related; boundary={boundary}"},
        )

    def upload_snapshot(self, snapshot_path, manifest):
        self.ensure_root()
        read_first = self.ensure_folder(self.root_id, "00 - اقرأ أولًا")
        reference = self.ensure_folder(self.root_id, "01 - النسخة المرجعية")
        daily = self.ensure_folder(self.root_id, "02 - النسخ اليومية")
        self.upsert(self.root_id, "000 - اقرأ أولًا.md", README_ROOT.encode(), "text/markdown")
        self.upsert(read_first["id"], "000 - اقرأ أولًا.md", README_ROOT.encode(), "text/markdown")
        self.upsert(reference["id"], "000 - اقرأ أولًا.md", README_REFERENCE.encode(), "text/markdown")
        self.upsert(daily["id"], "000 - اقرأ أولًا.md", README_DAILY.encode(), "text/markdown")

        reference_files = {}
        for path in (
            ROOT / "GENERAL_SERVICE_B2B_GROWTH_RULES.md",
            ROOT.parent / "PRODUCT_MASTER_SPEC_AR.md",
            ROOT.parent / "SANA-v1.0-RELEASE" / "02-Scan-Framework.md",
        ):
            if path.is_file():
                data = path.read_bytes()
                uploaded = self.upsert(reference["id"], path.name, data, "text/markdown")
                reference_files[path.name] = {
                    "sha256": _sha256(data), "drive_file_id": uploaded["id"]
                }
        ref_manifest = {
            "version": manifest["snapshot_version"],
            "updated_at": manifest["generated_at"],
            "files": reference_files,
        }
        self.upsert(
            reference["id"], "reference-manifest.json",
            json.dumps(ref_manifest, ensure_ascii=False, indent=2).encode(), "application/json",
        )
        uploaded = self.upsert(
            daily["id"], snapshot_path.name, snapshot_path.read_bytes(), "application/zip"
        )
        index = {
            "latest_successful_version": manifest["snapshot_version"],
            "updated_at": manifest["generated_at"],
            "snapshot_sha256": manifest["archive_sha256"],
            "snapshot_bytes": manifest["archive_bytes"],
            "snapshot_file_count": manifest["file_count"],
            "snapshot_file_id": uploaded["id"],
            "snapshot_web_link": uploaded.get("webViewLink"),
            "files": manifest["files"],
        }
        index_upload = self.upsert(
            self.root_id, "SANA_BACKUP_INDEX.json",
            json.dumps(index, ensure_ascii=False, indent=2).encode(), "application/json",
        )
        return {
            **uploaded,
            "index_file_id": index_upload["id"],
            "index_web_link": index_upload.get("webViewLink"),
        }

    def download_file(self, file_id, mime_type=None):
        export_types = {
            "application/vnd.google-apps.document": "text/plain",
            "application/vnd.google-apps.spreadsheet": "text/csv",
            "application/vnd.google-apps.presentation": "application/pdf",
        }
        export_type = export_types.get(str(mime_type or ""))
        path = (
            f"https://www.googleapis.com/drive/v3/files/{quote(file_id)}/export"
            if export_type else
            f"https://www.googleapis.com/drive/v3/files/{quote(file_id)}"
        )
        response = self.http.request(
            "GET",
            path,
            params={"mimeType": export_type} if export_type else {"alt": "media"},
            timeout=120,
        )
        if not response.ok:
            raise RuntimeError(
                f"DRIVE_HTTP_{response.status_code}: {response.text[:500]}"
            )
        return response.content


def run_backup(db, trigger_type="scheduled", output_dir=None, snapshot_date=None):
    ensure_backup_schema(db)
    when = _now_riyadh()
    snapshot_date = snapshot_date or when.date()
    version = f"v{snapshot_date:%Y.%m.%d}"
    run_id = f"BKP-{uuid.uuid4().hex[:12].upper()}"
    locked = db.execute("SELECT pg_try_advisory_lock(?) AS locked", (LOCK_KEY,)).fetchone()["locked"]
    if not locked:
        return {"success": False, "status": "skipped", "error": "BACKUP_ALREADY_RUNNING"}
    try:
        existing = db.execute(
            "SELECT * FROM knowledge_backup_runs WHERE snapshot_version=?", (version,)
        ).fetchone()
        if existing and existing["status"] == "success":
            return {"success": True, "status": "already_successful", "run": dict(existing)}
        if existing:
            run_id = existing["run_id"]
            db.execute(
                """UPDATE knowledge_backup_runs SET trigger_type=?, status='running',
                   started_at=?, completed_at=NULL, error_message=NULL WHERE run_id=?""",
                (trigger_type, when.isoformat(), run_id),
            )
        else:
            db.execute(
                """INSERT INTO knowledge_backup_runs
                   (run_id,snapshot_version,trigger_type,status,started_at)
                   VALUES (?,?,?,?,?)""",
                (run_id, version, trigger_type, "running", when.isoformat()),
            )
        db.commit()
        snapshot, manifest = build_snapshot(
            db,
            output_dir=output_dir,
            when=when,
            snapshot_date=snapshot_date,
        )
        verification = verify_snapshot(snapshot)
        if not verification["ok"]:
            raise RuntimeError(f"SNAPSHOT_VERIFY_FAILED: {verification['errors']}")
        uploaded = DriveMirror().upload_snapshot(snapshot, manifest)
        completed = _now_riyadh().isoformat()
        db.execute(
            """UPDATE knowledge_backup_runs SET status='success', completed_at=?,
               file_count=?, total_bytes=?, snapshot_sha256=?, drive_file_id=?,
               drive_web_link=?, manifest=?, error_message=NULL WHERE run_id=?""",
            (
                completed, manifest["file_count"], manifest["archive_bytes"],
                manifest["archive_sha256"], uploaded["id"], uploaded.get("webViewLink"),
                json.dumps(manifest, ensure_ascii=False), run_id,
            ),
        )
        db.commit()
        return {"success": True, "status": "success", "run_id": run_id, "manifest": manifest}
    except Exception as exc:
        db.rollback()
        db.execute(
            """UPDATE knowledge_backup_runs SET status='failed', completed_at=?,
               error_message=? WHERE run_id=?""",
            (_now_riyadh().isoformat(), str(exc)[:2000], run_id),
        )
        db.commit()
        return {"success": False, "status": "failed", "run_id": run_id, "error": str(exc)}
    finally:
        db.execute("SELECT pg_advisory_unlock(?)", (LOCK_KEY,))
        db.commit()

def missing_backup_dates(db, today=None):
    """Return every missing Riyadh date since the first recorded backup run."""
    ensure_backup_schema(db)
    today = today or _now_riyadh().date()
    rows = db.execute(
        "SELECT snapshot_version,status FROM knowledge_backup_runs "
        "ORDER BY snapshot_version"
    ).fetchall()
    recorded_dates = []
    successful = set()
    for row in rows:
        match = re.fullmatch(r"v(\d{4})\.(\d{2})\.(\d{2})", row["snapshot_version"])
        if not match:
            continue
        value = date(*(int(part) for part in match.groups()))
        if value <= today:
            recorded_dates.append(value)
            if row["status"] == "success":
                successful.add(value)
    start = min(recorded_dates) if recorded_dates else today
    days = []
    current = start
    while current <= today:
        if current not in successful:
            days.append(current)
        current += timedelta(days=1)
    return days


def reconcile_missing_backups(db, trigger_type="external_scheduler", today=None):
    """Create today's snapshot and honest late snapshots for all missed dates."""
    results = []
    for snapshot_date in missing_backup_dates(db, today=today):
        result = run_backup(
            db,
            trigger_type=trigger_type,
            snapshot_date=snapshot_date,
        )
        results.append(result)
        if not result.get("success"):
            break
    return {
        "success": all(item.get("success") for item in results),
        "attempted": len(results),
        "results": results,
    }


def audit_latest_drive_backup(db, output_dir=None):
    """Download the latest Drive ZIP and verify it against the DB-trusted hash."""
    ensure_backup_schema(db)
    row = db.execute(
        """SELECT snapshot_version,snapshot_sha256,drive_file_id
           FROM knowledge_backup_runs
           WHERE status='success'
           ORDER BY snapshot_version DESC LIMIT 1"""
    ).fetchone()
    if not row:
        raise RuntimeError("NO_SUCCESSFUL_BACKUP")
    data = DriveMirror().download_file(row["drive_file_id"])
    output_dir = Path(output_dir or SNAPSHOT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"audit-{row['snapshot_version']}.zip"
    path.write_bytes(data)
    verification = verify_snapshot(path, row["snapshot_sha256"])
    if verification["snapshot_version"] != row["snapshot_version"]:
        verification["ok"] = False
        verification["errors"].append("snapshot_version")
    return verification


def _next_scheduler_delay(attempt_date, status, now=None):
    now = now or _now_riyadh()
    if now.date() != attempt_date:
        return 1
    if status not in {"success", "already_successful"}:
        return BACKUP_RETRY_SECONDS
    next_run = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(1, (next_run - now).total_seconds())
def start_daily_scheduler(connect_db):
    """مشغل واحد منطقيًا؛ قفل PostgreSQL يمنع المضاعفة بين العمليات."""
    def loop():
        while True:
            attempt_date = _now_riyadh().date()
            db = None
            result = {"status": "failed"}
            try:
                db = connect_db()
                result = reconcile_missing_backups(db, "scheduled")
                status = "success" if result.get("success") else "failed"
                print(
                    f"[knowledge-backup] reconcile_{status}: "
                    f"attempted={result.get('attempted', 0)}"
                )
            except Exception as exc:
                print(f"[knowledge-backup] scheduler_error: {type(exc).__name__}")
            finally:
                if db is not None:
                    try:
                        db.close()
                    except Exception:
                        pass
            status = "success" if result.get("success") else "failed"
            time.sleep(_next_scheduler_delay(attempt_date, status))
    thread = threading.Thread(target=loop, name="sana-knowledge-backup", daemon=True)
    thread.start()
    return thread


if __name__ == "__main__":
    from app import _connect_pg, init_db, seed_knowledge_db
    command = sys.argv[1] if len(sys.argv) > 1 else "run"
    if command in {"run", "snapshot"}:
        init_db()
        seed_knowledge_db()
    connection = _connect_pg()
    try:
        if command == "snapshot":
            path, data = build_snapshot(connection)
            print(json.dumps({"path": str(path), "manifest": data}, ensure_ascii=False))
        elif command == "verify" and len(sys.argv) > 2:
            print(json.dumps(verify_snapshot(sys.argv[2]), ensure_ascii=False))
        elif command == "restore" and len(sys.argv) > 4:
            print(json.dumps(
                restore_snapshot(sys.argv[2], sys.argv[3], sys.argv[4]),
                ensure_ascii=False,
            ))
        elif command == "run":
            print(json.dumps(reconcile_missing_backups(connection, "manual"), ensure_ascii=False))
        elif command == "audit":
            print(json.dumps(audit_latest_drive_backup(connection), ensure_ascii=False))
        else:
            raise SystemExit(
                "usage: knowledge_backup.py "
                "[run|audit|snapshot|verify FILE|restore FILE DIR TRUSTED_SHA256]"
            )
    finally:
        connection.close()
