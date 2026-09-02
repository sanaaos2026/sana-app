"""فهرس Google Drive المعرفي لسنع.

هذه الطبقة تقرأ Metadata فقط. Drive يبقى مستودع الملفات الأصلية، بينما تحفظ
PostgreSQL الفهرس والسجلات المنظمة، ولا يتحول أي ملف إلى معرفة قابلة للاستخدام
إلا عبر اختيار ومراجعة صريحين.
"""

import hashlib
import json
import os
import re
import uuid
from collections import deque
from datetime import datetime, timezone

from database_config import acquire_schema_lock
from knowledge_backup import DriveMirror, _normalize_drive_folder_id


DRIVE_ROOT_NAME = "Sana OS | نظام سنع"
MASTER_INDEX_NAME = "Sana Master Index"
CLIENTS_FOLDER_NAME = "13 - Clients & Projects"
FOLDER_NAMES = [f"{number:02d}" for number in range(17)]
REQUIRED_METADATA = (
    "document_type",
    "knowledge_classification",
    "lifecycle_status",
    "confidentiality",
)
_SCHEMA_READY = False
_ENTITY_SOURCES = {
    "evidence": ("evidence", "evidence_id", "company_id", None),
    "decision": ("decisions", "decision_id", "company_id", None),
    "scan_finding": ("scan_findings", "finding_id", "company_id", None),
    "diagnostic_finding": (
        "diagnostic_findings", "finding_id", None,
        "JOIN diagnostic_runs owner ON owner.run_id=entity.run_id",
    ),
    "memory": ("sana_memory_entries", "memory_id", "company_id", None),
    "knowledge_object": ("knowledge_objects", "object_id", None, None),
}


def ensure_schema(db):
    """إنشاء/ترقية جداول الفهرس دون حذف أو تعديل أي سجل قائم."""
    global _SCHEMA_READY
    ready = db.execute(
        """SELECT
             EXISTS (
               SELECT 1 FROM information_schema.columns
               WHERE table_schema='public' AND table_name='knowledge_objects'
                 AND column_name='provenance_link_id'
             ) AS has_provenance_column,
             to_regclass('public.drive_source_excerpts') IS NOT NULL AS has_excerpt_table,
             to_regclass('public.drive_private_citations') IS NOT NULL AS has_citation_table,
             EXISTS (
               SELECT 1 FROM pg_constraint
               WHERE conname='drive_source_excerpts_case_id_fkey'
             ) AS has_case_fk,
             EXISTS (
               SELECT 1 FROM pg_trigger
               WHERE tgname='trg_knowledge_release_append_only'
                 AND tgrelid=to_regclass('public.knowledge_release_log')
             ) AS has_release_trigger"""
    ).fetchone()
    if (
        ready and ready["has_provenance_column"] and ready["has_excerpt_table"]
        and ready["has_citation_table"]
        and ready["has_case_fk"]
        and ready["has_release_trigger"]
    ):
        _SCHEMA_READY = True
        return
    acquire_schema_lock(db)
    db.execute(
        """CREATE TABLE IF NOT EXISTS drive_index_config (
            config_id TEXT PRIMARY KEY,
            root_folder_id TEXT,
            root_folder_name TEXT,
            master_index_file_id TEXT,
            master_index_url TEXT,
            setup_status TEXT NOT NULL DEFAULT 'not_checked',
            last_sync_run_id TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )
    db.execute(
        """INSERT INTO drive_index_config (config_id) VALUES ('default')
           ON CONFLICT (config_id) DO NOTHING"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS drive_files (
            drive_file_id TEXT PRIMARY KEY,
            drive_parent_id TEXT,
            name TEXT NOT NULL,
            current_folder_name TEXT,
            current_path TEXT,
            mime_type TEXT,
            document_type TEXT,
            web_view_link TEXT,
            modified_time TEXT,
            indexed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            md5_checksum TEXT,
            size_bytes BIGINT,
            is_folder SMALLINT NOT NULL DEFAULT 0,
            drive_state TEXT NOT NULL DEFAULT 'active',
            read_status TEXT NOT NULL DEFAULT 'not_read',
            access_status TEXT NOT NULL DEFAULT 'ok',
            orphaned SMALLINT NOT NULL DEFAULT 0,
            archived SMALLINT NOT NULL DEFAULT 0,
            duplicate_of TEXT,
            duplicate_group TEXT,
            knowledge_classification TEXT,
            sector TEXT,
            company_id TEXT,
            project_id TEXT,
            case_id TEXT,
            lifecycle_status TEXT NOT NULL DEFAULT 'unreviewed',
            problem TEXT,
            cause TEXT,
            kpi TEXT,
            diagnostic_rule TEXT,
            decision_rule TEXT,
            sop TEXT,
            case_study TEXT,
            quality TEXT,
            confidence TEXT,
            confidentiality TEXT NOT NULL DEFAULT 'internal',
            version_label TEXT,
            related_version TEXT,
            relations_json TEXT NOT NULL DEFAULT '[]',
            keywords TEXT,
            reviewed_at TIMESTAMPTZ,
            metadata_missing_json TEXT NOT NULL DEFAULT '[]',
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS drive_sync_runs (
            run_id TEXT PRIMARY KEY,
            trigger_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            root_folder_id TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            files_seen INTEGER NOT NULL DEFAULT 0,
            files_indexed INTEGER NOT NULL DEFAULT 0,
            unread_count INTEGER NOT NULL DEFAULT 0,
            missing_metadata_count INTEGER NOT NULL DEFAULT 0,
            duplicate_count INTEGER NOT NULL DEFAULT 0,
            orphan_count INTEGER NOT NULL DEFAULT 0,
            permission_error_count INTEGER NOT NULL DEFAULT 0,
            network_error_count INTEGER NOT NULL DEFAULT 0,
            unreadable_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            summary_json TEXT
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS drive_client_folder_mappings (
            mapping_id TEXT PRIMARY KEY,
            drive_folder_id TEXT UNIQUE NOT NULL,
            drive_folder_name TEXT NOT NULL,
            company_id TEXT NOT NULL REFERENCES companies(company_id),
            confidence TEXT NOT NULL DEFAULT 'explicit',
            review_status TEXT NOT NULL DEFAULT 'pending_review',
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            reviewed_at TIMESTAMPTZ
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS drive_knowledge_sources (
            link_id TEXT PRIMARY KEY,
            drive_file_id TEXT NOT NULL REFERENCES drive_files(drive_file_id),
            source_id TEXT REFERENCES knowledge_sources(source_id),
            source_type TEXT NOT NULL,
            version_label TEXT,
            section_locator TEXT,
            quality TEXT,
            verified_at TIMESTAMPTZ,
            review_status TEXT NOT NULL DEFAULT 'pending_review',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(drive_file_id, source_id, section_locator)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS drive_source_excerpts (
            excerpt_id TEXT PRIMARY KEY,
            drive_file_id TEXT NOT NULL REFERENCES drive_files(drive_file_id),
            section_locator TEXT NOT NULL,
            excerpt_text TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            extraction_status TEXT NOT NULL,
            company_id TEXT,
            case_id TEXT,
            created_by TEXT,
            review_status TEXT NOT NULL DEFAULT 'pending_review',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(drive_file_id,section_locator,content_hash)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS drive_private_citations (
            citation_id TEXT PRIMARY KEY,
            drive_file_id TEXT NOT NULL REFERENCES drive_files(drive_file_id),
            research_source_id TEXT NOT NULL REFERENCES research_sources(research_source_id) ON DELETE CASCADE,
            chunk_id TEXT NOT NULL REFERENCES research_source_chunks(chunk_id) ON DELETE CASCADE,
            excerpt_id TEXT NOT NULL REFERENCES drive_source_excerpts(excerpt_id) ON DELETE CASCADE,
            section_locator TEXT NOT NULL,
            company_id TEXT,
            case_id TEXT REFERENCES cases(case_id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(drive_file_id,research_source_id,chunk_id,excerpt_id)
        )"""
    )
    db.execute(
        """DO $$
           BEGIN
             IF NOT EXISTS (
               SELECT 1 FROM pg_constraint
               WHERE conname='drive_source_excerpts_case_id_fkey'
             ) THEN
               ALTER TABLE drive_source_excerpts
               ADD CONSTRAINT drive_source_excerpts_case_id_fkey
               FOREIGN KEY (case_id) REFERENCES cases(case_id);
             END IF;
           END;
           $$"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS sana_memory_entries (
            memory_id TEXT PRIMARY KEY,
            memory_type TEXT NOT NULL,
            statement TEXT NOT NULL,
            source_file_id TEXT REFERENCES drive_files(drive_file_id),
            source_id TEXT,
            section_locator TEXT,
            observed_at TEXT,
            confidence TEXT,
            company_id TEXT,
            case_id TEXT,
            kpi TEXT,
            verification_status TEXT NOT NULL DEFAULT 'unverified',
            conflict_status TEXT NOT NULL DEFAULT 'none',
            shared_scope TEXT NOT NULL DEFAULT 'private',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (memory_type IN ('fact','claim','note','hypothesis','unknown','conflict','evidence')),
            CHECK (shared_scope IN ('private','company','shared')),
            CHECK (shared_scope <> 'shared' OR verification_status = 'reviewed')
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS drive_provenance_links (
            provenance_id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            drive_file_id TEXT NOT NULL REFERENCES drive_files(drive_file_id),
            source_id TEXT,
            section_locator TEXT,
            relationship TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(entity_type, entity_id, drive_file_id, section_locator, relationship)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS knowledge_release_log (
            release_id TEXT PRIMARY KEY,
            release_label TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL,
            previous_release TEXT,
            change_reason TEXT NOT NULL,
            related_release TEXT,
            content_hash TEXT,
            reviewer TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )
    db.execute(
        """CREATE OR REPLACE FUNCTION reject_knowledge_release_mutation()
           RETURNS trigger LANGUAGE plpgsql AS $$
           BEGIN
             RAISE EXCEPTION 'KNOWLEDGE_RELEASE_APPEND_ONLY'
               USING ERRCODE = '55000';
           END;
           $$"""
    )
    db.execute(
        """DO $$
           BEGIN
             IF NOT EXISTS (
               SELECT 1 FROM pg_trigger
               WHERE tgname='trg_knowledge_release_append_only'
                 AND tgrelid='knowledge_release_log'::regclass
             ) THEN
               CREATE TRIGGER trg_knowledge_release_append_only
               BEFORE UPDATE OR DELETE ON knowledge_release_log
               FOR EACH ROW EXECUTE FUNCTION reject_knowledge_release_mutation();
             END IF;
           END;
           $$"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS capability_gaps (
            gap_id TEXT PRIMARY KEY,
            gap_key TEXT UNIQUE NOT NULL,
            sector TEXT,
            description TEXT NOT NULL,
            occurrence_count INTEGER NOT NULL DEFAULT 1,
            impact TEXT,
            case_ids_json TEXT NOT NULL DEFAULT '[]',
            proposed_release TEXT,
            status TEXT NOT NULL DEFAULT 'recorded',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_drive_files_company ON drive_files(company_id, lifecycle_status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_drive_files_parent ON drive_files(drive_parent_id, drive_state)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_drive_files_hash ON drive_files(md5_checksum, duplicate_of)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_drive_provenance_entity ON drive_provenance_links(entity_type, entity_id)")
    # قواعد البيانات القديمة لا تحتوي هذا الرابط؛ العمود اختياري للحفاظ على التوافق.
    columns = {
        row["column_name"] for row in db.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name='knowledge_objects'"""
        ).fetchall()
    }
    if "source_file_id" not in columns and columns:
        db.execute("ALTER TABLE knowledge_objects ADD COLUMN source_file_id TEXT")
    if "provenance_link_id" not in columns and columns:
        db.execute("ALTER TABLE knowledge_objects ADD COLUMN provenance_link_id TEXT")
    _SCHEMA_READY = True


def _now():
    return datetime.now(timezone.utc).isoformat()


def _error_kind(exc):
    text = str(exc).upper()
    if "403" in text or "401" in text or "PERMISSION" in text or "ACCESS" in text:
        return "PERMISSION_ERROR"
    if any(token in text for token in ("TIMEOUT", "NETWORK", "CONNECTION", "DNS")):
        return "CONNECTION_ERROR"
    if any(token in text for token in ("404", "NOT_FOUND", "UNAVAILABLE")):
        return "UNAVAILABLE"
    return "UNKNOWN_ERROR"


def _metadata_for(item, current_path, company_id=None):
    name = str(item.get("name") or "")
    lower = f"{current_path} {name}".lower()
    mime = item.get("mimeType") or item.get("mime_type")
    is_folder = mime == "application/vnd.google-apps.folder"
    if is_folder:
        document_type = "FOLDER"
    elif "sheet" in str(mime or "") or name.lower().endswith((".csv", ".xlsx")):
        document_type = "SPREADSHEET"
    elif "document" in str(mime or "") or name.lower().endswith((".doc", ".docx", ".md", ".txt", ".pdf")):
        document_type = "DOCUMENT"
    else:
        document_type = "FILE"
    classes = (
        ("EVIDENCE_RULE", ("evidence", "دليل", "قاعدة")),
        ("SECTOR_KNOWLEDGE", ("sector", "قطاع", "industry")),
        ("SOP", ("sop", "procedure", "إجراء")),
        ("CASE_STUDY", ("case study", "دراسة حالة")),
        ("CLIENT_ARTIFACT", ("client", "عميل")),
    )
    classification = next(
        (label for label, tokens in classes if any(token in lower for token in tokens)),
        "UNCLASSIFIED",
    )
    in_client_tree = CLIENTS_FOLDER_NAME.lower() in lower
    confidential = "CLIENT_CONFIDENTIAL" if in_client_tree else "internal"
    if "restricted" in lower or "مقيد" in lower:
        confidential = "RESTRICTED"
    elif "client_confidential" in lower or "سري" in lower:
        confidential = "CLIENT_CONFIDENTIAL"
    lifecycle = "unreviewed"
    if "archived" in lower or "مؤرشف" in lower:
        lifecycle = "archived"
    semantic_values = {
        "document_type": document_type,
        "knowledge_classification": None if classification == "UNCLASSIFIED" else classification,
        "lifecycle_status": None if lifecycle == "unreviewed" else lifecycle,
        "confidentiality": confidential,
    }
    missing = [] if is_folder else [
        field for field in REQUIRED_METADATA if not semantic_values.get(field)
    ]
    return {
        "drive_file_id": item.get("id"),
        "drive_parent_id": (item.get("parents") or [None])[0],
        "name": name,
        "current_folder_name": current_path.rsplit(" / ", 1)[-1] if current_path else None,
        "current_path": current_path,
        "mime_type": mime,
        "document_type": document_type,
        "web_view_link": item.get("webViewLink") or item.get("web_view_link"),
        "modified_time": item.get("modifiedTime") or item.get("modified_time"),
        "md5_checksum": item.get("md5Checksum") or item.get("md5_checksum"),
        "size_bytes": int(item["size"]) if str(item.get("size") or "").isdigit() else item.get("size_bytes"),
        "is_folder": 1 if is_folder else 0,
        "knowledge_classification": classification,
        "company_id": company_id,
        "lifecycle_status": lifecycle,
        "confidentiality": confidential,
        "metadata_missing_json": json.dumps(missing, ensure_ascii=False),
        "keywords": json.dumps([part for part in re.split(r"[\s/_-]+", name) if part], ensure_ascii=False),
        "relations_json": "[]",
    }


def _root_id(mirror):
    configured = _normalize_drive_folder_id(os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", ""))
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", configured):
        if hasattr(mirror, "get_file"):
            root = mirror.get_file(configured)
            if (
                root.get("mimeType") != "application/vnd.google-apps.folder"
                or root.get("trashed")
            ):
                raise RuntimeError("DRIVE_ROOT_INVALID")
        return configured
    found = mirror.find("root", DRIVE_ROOT_NAME, "application/vnd.google-apps.folder")
    return found.get("id") if found else None


def _tree(mirror, root_id):
    queue = deque([(root_id, DRIVE_ROOT_NAME, [])])
    seen = set()
    output = []
    while queue:
        parent_id, parent_name, ancestors = queue.popleft()
        if parent_id in seen:
            continue
        seen.add(parent_id)
        children = mirror.list_files(
            f"'{parent_id}' in parents and trashed = false"
        )
        for item in children:
            item = dict(item)
            item.setdefault("parents", [parent_id])
            path = " / ".join(ancestors + [parent_name, item.get("name", "")])
            output.append((item, path, [parent_id] + ancestors))
            if item.get("mimeType") == "application/vnd.google-apps.folder":
                queue.append((item["id"], item.get("name", ""), [parent_id] + ancestors))
    return output


def _master_index(mirror, root_id, tree):
    matches = [
        item for item, _, _ in tree
        if item.get("name") == MASTER_INDEX_NAME
    ]
    if matches:
        item = matches[0]
        return item.get("id"), item.get("webViewLink"), "existing"
    return None, None, "manual_required"


def _mark_missing(db, root_id, current_ids):
    params = [root_id]
    if current_ids:
        placeholders = ",".join("?" for _ in current_ids)
        params.extend(current_ids)
        where = f"drive_file_id NOT IN ({placeholders})"
    else:
        where = "TRUE"
    db.execute(
        f"""UPDATE drive_files SET drive_state='missing', orphaned=1, updated_at=now()
            WHERE drive_file_id <> ? AND {where}""",
        params,
    )


def sync_drive_metadata(db, trigger_type="manual", mirror=None):
    """يفهرس الشجرة الحالية بقراءة فقط ويعيد إحصاءً قابلاً لإعادة التشغيل."""
    ensure_schema(db)
    run_id = "DRIVE-SYNC-" + uuid.uuid4().hex[:12].upper()
    started = _now()
    lock = db.execute(
        "SELECT pg_try_advisory_xact_lock(hashtext('sana.drive.metadata.sync')) AS acquired"
    ).fetchone()
    if not lock or not lock["acquired"]:
        db.rollback()
        return {
            "success": False, "run_id": run_id, "status": "skipped",
            "error": "DRIVE_SYNC_ALREADY_RUNNING", "error_kind": "overlap",
        }
    db.execute(
        "INSERT INTO drive_sync_runs (run_id,trigger_type,status,started_at) VALUES (?,?,?,?)",
        (run_id, trigger_type, "running", started),
    )
    mirror = mirror or DriveMirror()
    root_id = None
    try:
        root_id = _root_id(mirror)
        if not root_id:
            raise RuntimeError("DRIVE_ROOT_NOT_FOUND")
        tree = _tree(mirror, root_id)
        master_id, master_url, master_status = _master_index(mirror, root_id, tree)
        mappings = {
            row["drive_folder_id"]: row["company_id"]
            for row in db.execute(
                "SELECT drive_folder_id,company_id FROM drive_client_folder_mappings WHERE review_status='approved'"
            ).fetchall()
        }
        current_ids = []
        indexed = 0
        for item, path, ancestors in tree:
            file_id = item.get("id")
            if not file_id:
                continue
            current_ids.append(file_id)
            company_id = next((mappings.get(parent) for parent in ancestors if mappings.get(parent)), None)
            metadata = _metadata_for(item, path, company_id)
            db.execute(
                """INSERT INTO drive_files
                   (drive_file_id,drive_parent_id,name,current_folder_name,current_path,
                    mime_type,document_type,web_view_link,modified_time,indexed_at,
                    md5_checksum,size_bytes,is_folder,drive_state,read_status,access_status,
                    orphaned,knowledge_classification,company_id,lifecycle_status,confidentiality,
                    relations_json,keywords,metadata_missing_json,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'active','not_read','ok',0,?,?,?,?,?,?,?,now())
                   ON CONFLICT (drive_file_id) DO UPDATE SET
                    drive_parent_id=EXCLUDED.drive_parent_id,name=EXCLUDED.name,
                    current_folder_name=EXCLUDED.current_folder_name,current_path=EXCLUDED.current_path,
                    mime_type=EXCLUDED.mime_type,document_type=EXCLUDED.document_type,
                    web_view_link=EXCLUDED.web_view_link,modified_time=EXCLUDED.modified_time,
                    indexed_at=EXCLUDED.indexed_at,md5_checksum=EXCLUDED.md5_checksum,
                    size_bytes=EXCLUDED.size_bytes,is_folder=EXCLUDED.is_folder,
                    drive_state='active',access_status='ok',orphaned=0,
                    knowledge_classification=EXCLUDED.knowledge_classification,
                     company_id=EXCLUDED.company_id,
                     confidentiality=CASE
                       WHEN drive_files.reviewed_at IS NOT NULL THEN drive_files.confidentiality
                       ELSE EXCLUDED.confidentiality END,
                    metadata_missing_json=EXCLUDED.metadata_missing_json,
                    updated_at=now(),last_error=NULL""",
                (
                    metadata["drive_file_id"], metadata["drive_parent_id"], metadata["name"],
                    metadata["current_folder_name"], metadata["current_path"], metadata["mime_type"],
                    metadata["document_type"], metadata["web_view_link"], metadata["modified_time"],
                    started, metadata["md5_checksum"], metadata["size_bytes"], metadata["is_folder"],
                    metadata["knowledge_classification"], metadata["company_id"],
                    metadata["lifecycle_status"], metadata["confidentiality"],
                    metadata["relations_json"], metadata["keywords"], metadata["metadata_missing_json"],
                ),
            )
            indexed += 1
        _mark_missing(db, root_id, current_ids)
        db.execute(
            """UPDATE drive_files SET duplicate_of = NULL, duplicate_group = NULL
               WHERE md5_checksum IS NOT NULL"""
        )
        db.execute(
            """UPDATE drive_files d SET duplicate_of = x.first_id, duplicate_group = d.md5_checksum
               FROM (
                 SELECT md5_checksum, MIN(drive_file_id) AS first_id
                 FROM drive_files
                 WHERE md5_checksum IS NOT NULL AND drive_state='active'
                 GROUP BY md5_checksum HAVING COUNT(*) > 1
               ) x
               WHERE d.md5_checksum=x.md5_checksum AND d.drive_file_id<>x.first_id"""
        )
        stats = db.execute(
            """SELECT COUNT(*) FILTER (WHERE is_folder=0 AND drive_state='active') AS files,
                      COUNT(*) FILTER (WHERE is_folder=0 AND drive_state='active' AND read_status<>'read') AS unread,
                      COUNT(*) FILTER (WHERE is_folder=0 AND drive_state='active' AND metadata_missing_json<>'[]') AS missing,
                      COUNT(*) FILTER (WHERE duplicate_of IS NOT NULL) AS duplicates,
                      COUNT(*) FILTER (WHERE orphaned=1) AS orphans
               FROM drive_files"""
        ).fetchone()
        summary = {
            "master_index_file_id": master_id,
            "master_index_url": master_url,
            "master_index_status": master_status,
            "root_name": DRIVE_ROOT_NAME,
            "reference_folders": sorted(
                {item.get("name") for item, _, _ in tree
                 if item.get("mimeType") == "application/vnd.google-apps.folder"
                 and re.match(r"^(?:0[0-9]|1[0-6])(?:\\s|-)", item.get("name", ""))}
            ),
        }
        summary["missing_reference_folders"] = [
            prefix for prefix in FOLDER_NAMES
            if not any(str(name or "").startswith(prefix) for name in summary["reference_folders"])
        ]
        values = (
            run_id, "success", root_id, len(tree), int(indexed), int(stats["unread"] or 0),
            int(stats["missing"] or 0), int(stats["duplicates"] or 0),
            int(stats["orphans"] or 0), 0, 0, 0, None, json.dumps(summary, ensure_ascii=False),
        )
        db.execute(
            """UPDATE drive_sync_runs SET status=?,root_folder_id=?,completed_at=now(),
               files_seen=?,files_indexed=?,unread_count=?,missing_metadata_count=?,
               duplicate_count=?,orphan_count=?,permission_error_count=?,
               network_error_count=?,unreadable_count=?,error_message=?,summary_json=?
               WHERE run_id=?""",
            values[1:] + (run_id,),
        )
        db.execute(
            """UPDATE drive_index_config SET root_folder_id=?,root_folder_name=?,
               master_index_file_id=?,master_index_url=?,setup_status=?,
               last_sync_run_id=?,updated_at=now() WHERE config_id='default'""",
            (root_id, DRIVE_ROOT_NAME, master_id, master_url,
             "ready" if master_id else "manual_required", run_id),
        )
        db.commit()
        return {"success": True, "run_id": run_id, "status": "success", **summary,
                "files_seen": len(tree), "files_indexed": indexed,
                "unread_count": int(stats["unread"] or 0),
                "missing_metadata_count": int(stats["missing"] or 0),
                "duplicate_count": int(stats["duplicates"] or 0),
                "orphan_count": int(stats["orphans"] or 0)}
    except Exception as exc:
        kind = _error_kind(exc)
        db.rollback()
        db.execute(
            """INSERT INTO drive_sync_runs
               (run_id,trigger_type,status,started_at,completed_at,root_folder_id,error_message,
                permission_error_count,network_error_count,unreadable_count)
               VALUES (?,?,'failed',?,now(),?,?,?,?,?)
               ON CONFLICT (run_id) DO UPDATE SET status='failed',completed_at=now(),
                 root_folder_id=EXCLUDED.root_folder_id,error_message=EXCLUDED.error_message,
                 permission_error_count=EXCLUDED.permission_error_count,
                 network_error_count=EXCLUDED.network_error_count,
                 unreadable_count=EXCLUDED.unreadable_count""",
            (run_id, trigger_type, started, root_id, str(exc),
             int(kind == "PERMISSION_ERROR"), int(kind == "CONNECTION_ERROR"),
             int(kind in ("UNAVAILABLE", "UNKNOWN_ERROR"))),
        )
        db.execute(
            "UPDATE drive_index_config SET setup_status=?,last_sync_run_id=?,updated_at=now() WHERE config_id='default'",
            ("permission_error" if kind == "PERMISSION_ERROR" else "error", run_id),
        )
        db.commit()
        return {"success": False, "run_id": run_id, "status": "failed",
                "error": str(exc), "error_kind": kind}


def drive_index_report(db):
    ensure_schema(db)
    config = db.execute("SELECT * FROM drive_index_config WHERE config_id='default'").fetchone()
    latest = db.execute(
        "SELECT * FROM drive_sync_runs ORDER BY started_at DESC,run_id DESC LIMIT 1"
    ).fetchone()
    latest_success = db.execute(
        """SELECT completed_at FROM drive_sync_runs WHERE status='success'
           ORDER BY completed_at DESC NULLS LAST,run_id DESC LIMIT 1"""
    ).fetchone()
    stats = db.execute(
        """SELECT COUNT(*) FILTER (WHERE is_folder=0 AND drive_state='active') AS indexed,
                  COUNT(*) FILTER (WHERE is_folder=0 AND drive_state='active' AND read_status<>'read') AS unread,
                  COUNT(*) FILTER (WHERE is_folder=0 AND drive_state='active' AND metadata_missing_json<>'[]') AS missing,
                  COUNT(*) FILTER (WHERE duplicate_of IS NOT NULL) AS duplicates,
                  COUNT(*) FILTER (WHERE orphaned=1) AS orphans,
                  COUNT(*) FILTER (WHERE access_status='permission_error') AS permission_errors
           FROM drive_files"""
    ).fetchone()
    return {
        "config": dict(config) if config else None,
        "latest_run": dict(latest) if latest else None,
        "health": {
            "current_status": (
                "SUCCESS" if latest and latest["status"] == "success"
                else (_error_kind(latest["error_message"]) if latest else "UNAVAILABLE")
            ),
            "last_attempt_at": (
                (latest["completed_at"] or latest["started_at"]) if latest else None
            ),
            "last_success_at": latest_success["completed_at"] if latest_success else None,
            "error_code": (
                _error_kind(latest["error_message"])
                if latest and latest["status"] != "success" else None
            ),
        },
        "stats": {key: int(stats[key] or 0) for key in (
            "indexed", "unread", "missing", "duplicates", "orphans", "permission_errors"
        )},
        "manual_required": [
            "اعتماد تصنيف المعرفة والنسخة لكل ملف مهم",
            "قراءة المحتوى واستخراج المقاطع والAnnotations بعد اختيار سياقي",
            "مراجعة خصوصية ملفات العملاء وإزالة الهوية قبل النشر المشترك",
        ],
    }


def list_drive_files(db, company_id=None, query="", limit=100):
    ensure_schema(db)
    limit = max(1, min(int(limit or 100), 500))
    conditions = ["drive_state='active'"]
    params = []
    if company_id:
        conditions.append(
            "(company_id=? OR (company_id IS NULL AND confidentiality='internal'))"
        )
        params.append(company_id)
    query = str(query or "").strip().lower()
    if query:
        conditions.append("LOWER(COALESCE(name,'') || ' ' || COALESCE(current_path,'') || ' ' || COALESCE(keywords,'')) LIKE ?")
        params.append(f"%{query}%")
    rows = db.execute(
        f"""SELECT drive_file_id,drive_parent_id,name,current_folder_name,current_path,
                   mime_type,document_type,web_view_link,modified_time,indexed_at,
                   md5_checksum,size_bytes,is_folder,drive_state,read_status,access_status,
                   orphaned,archived,duplicate_of,knowledge_classification,sector,
                   company_id,project_id,case_id,lifecycle_status,quality,confidence,
                   confidentiality,version_label,related_version,keywords,
                   metadata_missing_json,last_error
            FROM drive_files WHERE {' AND '.join(conditions)}
            ORDER BY is_folder DESC,current_path,name LIMIT {limit}""",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def select_context_files(db, company_id, case_id=None, topic=None, limit=25):
    """اختيار محدود من الملفات، لا يعادل قراءة مجلد العميل كاملًا."""
    ensure_schema(db)
    company_id = str(company_id or "").strip()
    if not company_id:
        return []
    terms = []
    if case_id:
        case = db.execute(
            "SELECT declared_problem,real_question,case_type FROM cases WHERE case_id=? AND company_id=?",
            (case_id, company_id),
        ).fetchone()
        if not case:
            return []
        terms.extend(str(case[key] or "") for key in ("declared_problem", "real_question", "case_type"))
    if topic:
        terms.append(str(topic))
    terms = [term.strip().lower() for term in terms if term and term.strip()]
    conditions = [
        "drive_state='active'", "is_folder=0",
        "(company_id=? OR (company_id IS NULL AND confidentiality='internal'))",
        "confidentiality NOT IN ('CLIENT_CONFIDENTIAL','RESTRICTED')",
        "lifecycle_status='approved'",
        "read_status='read'",
    ]
    params = [company_id]
    if terms:
        clauses = []
        for term in terms[:8]:
            clauses.append("LOWER(COALESCE(name,'') || ' ' || COALESCE(current_path,'') || ' ' || COALESCE(keywords,'')) LIKE ?")
            params.append(f"%{term.lower()}%")
        conditions.append("(" + " OR ".join(clauses) + ")")
    rows = db.execute(
        f"""SELECT * FROM drive_files WHERE {' AND '.join(conditions)}
            ORDER BY CASE WHEN company_id=? THEN 0 ELSE 1 END, modified_time DESC NULLS LAST
            LIMIT {max(1, min(int(limit or 25), 100))}""",
        params + [company_id],
    ).fetchall()
    return [dict(row) for row in rows]


def create_client_mapping(db, drive_folder_id, drive_folder_name, company_id, actor=None):
    ensure_schema(db)
    company = db.execute("SELECT 1 FROM companies WHERE company_id=?", (company_id,)).fetchone()
    if not company:
        return {"success": False, "error": "COMPANY_NOT_FOUND"}
    mapping_id = "DCM-" + uuid.uuid4().hex[:12].upper()
    db.execute(
        """INSERT INTO drive_client_folder_mappings
           (mapping_id,drive_folder_id,drive_folder_name,company_id,created_by)
           VALUES (?,?,?,?,?) ON CONFLICT (drive_folder_id) DO UPDATE SET
           drive_folder_name=EXCLUDED.drive_folder_name,company_id=EXCLUDED.company_id,
           review_status='pending_review',reviewed_at=NULL,created_by=EXCLUDED.created_by
           RETURNING mapping_id""",
        (mapping_id, str(drive_folder_id).strip(), str(drive_folder_name).strip(), company_id, actor),
    )
    stored = db.execute(
        "SELECT mapping_id FROM drive_client_folder_mappings WHERE drive_folder_id=?",
        (str(drive_folder_id).strip(),),
    ).fetchone()
    mapping_id = stored["mapping_id"]
    db.commit()
    return {"success": True, "mapping_id": mapping_id, "review_status": "pending_review"}


def review_client_mapping(db, mapping_id, review_status, actor=None):
    ensure_schema(db)
    if review_status not in {"approved", "rejected"}:
        return {"success": False, "error": "MAPPING_REVIEW_STATUS_INVALID"}
    row = db.execute(
        "SELECT mapping_id FROM drive_client_folder_mappings WHERE mapping_id=?",
        (mapping_id,),
    ).fetchone()
    if not row:
        return {"success": False, "error": "MAPPING_NOT_FOUND"}
    db.execute(
        """UPDATE drive_client_folder_mappings
           SET review_status=?,reviewed_at=now(),created_by=COALESCE(?,created_by)
           WHERE mapping_id=?""",
        (review_status, actor, mapping_id),
    )
    db.commit()
    return {"success": True, "mapping_id": mapping_id, "review_status": review_status}


def extract_selected_drive_excerpt(
    db, drive_file_id, *, section_locator, start_char=0, end_char=4000,
    case_id=None, actor=None, actor_company_id=None, mirror=None,
):
    """يقرأ ملفًا منتقى صراحة ويحفظ مقتطفًا محدودًا قيد المراجعة، لا نسخة الوثيقة."""
    ensure_schema(db)
    row = db.execute(
        """SELECT drive_file_id,name,mime_type,company_id,drive_state,is_folder,
                  access_status,web_view_link,modified_time,confidentiality
           FROM drive_files WHERE drive_file_id=?""",
        (drive_file_id,),
    ).fetchone()
    if not row:
        return {"success": False, "error": "DRIVE_FILE_NOT_INDEXED"}
    if row["drive_state"] != "active" or row["is_folder"]:
        return {"success": False, "error": "DRIVE_FILE_NOT_READABLE"}
    if row["company_id"] and actor_company_id != row["company_id"]:
        return {"success": False, "error": "DRIVE_FILE_COMPANY_MISMATCH"}
    if not actor:
        return {"success": False, "error": "PRIVATE_OWNER_REQUIRED"}
    if case_id:
        if not row["company_id"]:
            return {"success": False, "error": "DRIVE_FILE_UNASSIGNED_FOR_CASE"}
        case = db.execute(
            "SELECT company_id FROM cases WHERE case_id=?", (case_id,)
        ).fetchone()
        if not case:
            return {"success": False, "error": "CASE_NOT_FOUND"}
        if case["company_id"] != row["company_id"]:
            return {"success": False, "error": "DRIVE_CASE_COMPANY_MISMATCH"}
    section_locator = str(section_locator or "").strip()
    if not section_locator:
        return {"success": False, "error": "SECTION_LOCATOR_REQUIRED"}
    try:
        start_char = max(0, int(start_char or 0))
        end_char = int(end_char or start_char + 4000)
    except (TypeError, ValueError):
        return {"success": False, "error": "EXCERPT_RANGE_INVALID"}
    if end_char <= start_char or end_char - start_char > 12000:
        return {"success": False, "error": "EXCERPT_RANGE_INVALID"}
    mirror = mirror or DriveMirror()
    try:
        content = mirror.download_file(drive_file_id, row["mime_type"])
        from sana_knowledge import _extract_uploaded_text
        extracted_text, extraction_status = _extract_uploaded_text(
            row["name"], row["mime_type"], content
        )
    except Exception as exc:
        return {"success": False, "error": str(exc), "error_kind": _error_kind(exc)}
    if not extracted_text:
        return {"success": False, "error": "DRIVE_CONTENT_NOT_EXTRACTABLE",
                "extraction_status": extraction_status}
    excerpt = extracted_text[start_char:end_char].strip()
    if not excerpt:
        return {"success": False, "error": "EXCERPT_RANGE_EMPTY"}
    content_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
    existing = db.execute(
        """SELECT excerpt_id FROM drive_source_excerpts
           WHERE drive_file_id=? AND section_locator=? AND content_hash=?""",
        (drive_file_id, section_locator, content_hash),
    ).fetchone()
    excerpt_id = existing["excerpt_id"] if existing else "DEX-" + uuid.uuid4().hex[:12].upper()
    linked = db.execute(
        """SELECT c.citation_id,c.research_source_id,c.chunk_id
           FROM drive_private_citations c WHERE c.excerpt_id=?""",
        (excerpt_id,),
    ).fetchone() if existing else None
    resolved_company = row["company_id"] or actor_company_id
    if linked:
        return {
            "success": True, "excerpt_id": excerpt_id,
            "research_source_id": linked["research_source_id"],
            "chunk_id": linked["chunk_id"], "citation_id": linked["citation_id"],
            "drive_file_id": drive_file_id, "section_locator": section_locator,
            "content_hash": content_hash, "extraction_status": extraction_status,
            "review_status": "pending_review", "excerpt_chars": len(excerpt),
        }
    research_source_id = "RS-DRIVE-" + uuid.uuid4().hex[:12].upper()
    chunk_id = "RC-DRIVE-" + uuid.uuid4().hex[:12].upper()
    citation_id = "CIT-DRIVE-" + uuid.uuid4().hex[:12].upper()
    db.execute(
        """INSERT INTO research_sources
           (research_source_id,title,source_kind,origin,company_id,drive_file_id,
            source_url,rights_status,version_label,sensitivity,knowledge_scope,
            storage_destination,ingestion_event,review_status,is_private,owner_account_id)
           VALUES (?,?,?,'drive',?,?,?,?,?,?,?,?,?,'inbox',1,?)""",
        (
            research_source_id, row["name"], "file", resolved_company,
            drive_file_id, row["web_view_link"], "pending",
            "sha256-" + content_hash[:12],
            str(row["confidentiality"] or "internal").lower(),
            "private_case" if case_id else "private",
            "drive_reference_only", "selected_drive_excerpt", actor,
        ),
    )
    db.execute(
        """INSERT INTO research_source_chunks
           (chunk_id,research_source_id,file_id,chunk_order,content)
           VALUES (?,?,NULL,0,?)""",
        (chunk_id, research_source_id, excerpt),
    )
    if not existing:
        db.execute(
            """INSERT INTO drive_source_excerpts
               (excerpt_id,drive_file_id,section_locator,excerpt_text,content_hash,
                extraction_status,company_id,case_id,created_by)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (excerpt_id, drive_file_id, section_locator, excerpt, content_hash,
             extraction_status, resolved_company, case_id, actor),
        )
    db.execute(
        """INSERT INTO drive_private_citations
           (citation_id,drive_file_id,research_source_id,chunk_id,excerpt_id,
            section_locator,company_id,case_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (citation_id, drive_file_id, research_source_id, chunk_id, excerpt_id,
         section_locator, resolved_company, case_id),
    )
    db.execute(
        """UPDATE drive_files SET read_status='read',updated_at=now()
           WHERE drive_file_id=?""",
        (drive_file_id,),
    )
    db.commit()
    return {
        "success": True, "excerpt_id": excerpt_id, "drive_file_id": drive_file_id,
        "research_source_id": research_source_id, "chunk_id": chunk_id,
        "citation_id": citation_id,
        "section_locator": section_locator, "content_hash": content_hash,
        "extraction_status": extraction_status, "review_status": "pending_review",
        "excerpt_chars": len(excerpt),
    }


def link_drive_source(db, drive_file_id, source_id=None, source_type="drive_metadata",
                      section_locator=None, version_label=None, quality=None,
                      review_status="pending_review", object_id=None):
    ensure_schema(db)
    file_row = db.execute(
        """SELECT confidentiality,drive_state,company_id,web_view_link
           FROM drive_files WHERE drive_file_id=?""",
        (drive_file_id,),
    ).fetchone()
    if not file_row:
        return {"success": False, "error": "DRIVE_FILE_NOT_INDEXED"}
    if file_row["drive_state"] != "active":
        return {"success": False, "error": "DRIVE_FILE_MISSING"}
    if review_status == "approved" and file_row["confidentiality"] in ("CLIENT_CONFIDENTIAL", "RESTRICTED"):
        return {"success": False, "error": "PRIVATE_SOURCE_REQUIRES_ANONYMIZATION"}
    if source_id and not db.execute("SELECT 1 FROM knowledge_sources WHERE source_id=?", (source_id,)).fetchone():
        return {"success": False, "error": "KNOWLEDGE_SOURCE_NOT_FOUND"}
    existing = db.execute(
        """SELECT link_id FROM drive_knowledge_sources
           WHERE drive_file_id=? AND source_id IS NOT DISTINCT FROM ?
             AND section_locator IS NOT DISTINCT FROM ?""",
        (drive_file_id, source_id, section_locator),
    ).fetchone()
    link_id = existing["link_id"] if existing else "DKS-" + uuid.uuid4().hex[:12].upper()
    if existing:
        db.execute(
            """UPDATE drive_knowledge_sources SET source_type=?,version_label=?,
               quality=?,verified_at=?,review_status=? WHERE link_id=?""",
            (source_type, version_label, quality,
             _now() if review_status == "approved" else None, review_status, link_id),
        )
    else:
        db.execute(
            """INSERT INTO drive_knowledge_sources
               (link_id,drive_file_id,source_id,source_type,version_label,section_locator,
                quality,verified_at,review_status)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (link_id, drive_file_id, source_id, source_type, version_label, section_locator,
             quality, _now() if review_status == "approved" else None, review_status),
        )
    if object_id:
        if file_row["confidentiality"] in ("CLIENT_CONFIDENTIAL", "RESTRICTED"):
            return {"success": False, "error": "PRIVATE_SOURCE_CANNOT_LINK_SHARED_OBJECT"}
        object_exists = db.execute(
            "SELECT 1 FROM knowledge_objects WHERE object_id=?", (object_id,)
        ).fetchone()
        if not object_exists:
            return {"success": False, "error": "KNOWLEDGE_OBJECT_NOT_FOUND"}
        db.execute(
            """UPDATE knowledge_objects SET source_file_id=?,source_id=?,
               source_url=?,provenance_link_id=?,updated_at=now() WHERE object_id=?""",
            (drive_file_id, source_id, file_row["web_view_link"], link_id, object_id),
        )
    db.commit()
    return {"success": True, "link_id": link_id, "drive_file_id": drive_file_id}


def add_drive_provenance(db, *, entity_type, entity_id, drive_file_id,
                         source_id=None, source_link_id=None, section_locator=None,
                         relationship="supports"):
    """يربط Finding/Evidence/Decision أو أي كيان بمصدر Drive محدد."""
    ensure_schema(db)
    file_row = db.execute(
        "SELECT company_id,drive_state FROM drive_files WHERE drive_file_id=?",
        (drive_file_id,),
    ).fetchone()
    if not file_row:
        return {"success": False, "error": "DRIVE_FILE_NOT_INDEXED"}
    if file_row["drive_state"] != "active":
        return {"success": False, "error": "DRIVE_FILE_MISSING"}
    entity_spec = _ENTITY_SOURCES.get(entity_type)
    if not entity_spec:
        return {"success": False, "error": "PROVENANCE_ENTITY_TYPE_INVALID"}
    table, key, company_column, join_clause = entity_spec
    if join_clause:
        entity = db.execute(
            f"""SELECT owner.company_id FROM {table} entity {join_clause}
                WHERE entity.{key}=?""",
            (entity_id,),
        ).fetchone()
    else:
        projection = f"entity.{company_column} AS company_id" if company_column else "NULL AS company_id"
        entity = db.execute(
            f"SELECT {projection} FROM {table} entity WHERE entity.{key}=?",
            (entity_id,),
        ).fetchone()
    if not entity:
        return {"success": False, "error": "PROVENANCE_ENTITY_NOT_FOUND"}
    if entity["company_id"] and file_row["company_id"] != entity["company_id"]:
        return {"success": False, "error": "PROVENANCE_COMPANY_MISMATCH"}
    if source_link_id:
        source_link = db.execute(
            """SELECT link_id,source_id,section_locator FROM drive_knowledge_sources
               WHERE link_id=? AND drive_file_id=? AND review_status='approved'""",
            (source_link_id, drive_file_id),
        ).fetchone()
    elif source_id:
        source_link = db.execute(
            """SELECT link_id,source_id,section_locator FROM drive_knowledge_sources
               WHERE drive_file_id=? AND source_id=? AND review_status='approved'
                 AND section_locator IS NOT DISTINCT FROM ?""",
            (drive_file_id, source_id, section_locator),
        ).fetchone()
    else:
        source_link = None
    if not source_link:
        return {"success": False, "error": "APPROVED_SOURCE_LINK_REQUIRED"}
    source_id = source_link["source_id"]
    section_locator = source_link["section_locator"]
    existing = db.execute(
        """SELECT provenance_id FROM drive_provenance_links
           WHERE entity_type=? AND entity_id=? AND drive_file_id=?
             AND section_locator IS NOT DISTINCT FROM ? AND relationship=?""",
        (entity_type, entity_id, drive_file_id, section_locator, relationship),
    ).fetchone()
    provenance_id = existing["provenance_id"] if existing else "PROV-" + uuid.uuid4().hex[:12].upper()
    if not existing:
        db.execute(
            """INSERT INTO drive_provenance_links
               (provenance_id,entity_type,entity_id,drive_file_id,source_id,section_locator,relationship)
               VALUES (?,?,?,?,?,?,?)""",
            (provenance_id, entity_type, entity_id, drive_file_id, source_id,
             section_locator, relationship),
        )
    db.commit()
    return {"success": True, "provenance_id": provenance_id}


def provenance_for_object(db, object_id):
    ensure_schema(db)
    rows = db.execute(
        """SELECT dks.link_id,dks.source_id,dks.source_type,dks.version_label,
                  dks.section_locator,dks.quality,dks.verified_at,
                  df.drive_file_id,df.name,df.web_view_link,df.mime_type,
                  df.modified_time,df.confidentiality
           FROM drive_knowledge_sources dks
           JOIN drive_files df ON df.drive_file_id=dks.drive_file_id
            JOIN knowledge_objects ko ON ko.provenance_link_id=dks.link_id
           WHERE ko.object_id=?
           ORDER BY dks.created_at""",
        (object_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def add_memory_entry(db, *, memory_type, statement, company_id=None, case_id=None,
                     source_file_id=None, source_id=None, section_locator=None,
                     observed_at=None, confidence=None, kpi=None,
                     verification_status="unverified", shared_scope="private"):
    ensure_schema(db)
    if memory_type not in {"fact", "claim", "note", "hypothesis", "unknown", "conflict", "evidence"}:
        return {"success": False, "error": "MEMORY_TYPE_INVALID"}
    if shared_scope == "shared" and verification_status != "reviewed":
        return {"success": False, "error": "SHARED_MEMORY_REQUIRES_REVIEW"}
    if source_file_id:
        source = db.execute(
            "SELECT company_id,confidentiality,drive_state FROM drive_files WHERE drive_file_id=?",
            (source_file_id,),
        ).fetchone()
        if not source:
            return {"success": False, "error": "DRIVE_FILE_NOT_INDEXED"}
        if source["company_id"]:
            if company_id and company_id != source["company_id"]:
                return {"success": False, "error": "MEMORY_COMPANY_MISMATCH"}
            company_id = source["company_id"]
        if shared_scope == "shared" and source["confidentiality"] in ("CLIENT_CONFIDENTIAL", "RESTRICTED"):
            return {"success": False, "error": "PRIVATE_SOURCE_CANNOT_BE_SHARED"}
        if source["drive_state"] != "active":
            return {"success": False, "error": "DRIVE_FILE_MISSING"}
    memory_id = "MEM-" + uuid.uuid4().hex[:12].upper()
    db.execute(
        """INSERT INTO sana_memory_entries
           (memory_id,memory_type,statement,source_file_id,source_id,section_locator,
            observed_at,confidence,company_id,case_id,kpi,verification_status,shared_scope)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (memory_id, memory_type, statement, source_file_id, source_id, section_locator,
         observed_at, confidence, company_id, case_id, kpi, verification_status, shared_scope),
    )
    db.commit()
    return {"success": True, "memory_id": memory_id}


def create_release(db, release_label, status, change_reason, previous_release=None,
                   related_release=None, content_hash=None, reviewer=None):
    ensure_schema(db)
    existing = db.execute(
        "SELECT 1 FROM knowledge_release_log WHERE release_label=?", (release_label,)
    ).fetchone()
    if existing:
        return {"success": False, "error": "RELEASE_IMMUTABLE"}
    release_id = "REL-" + uuid.uuid4().hex[:12].upper()
    db.execute(
        """INSERT INTO knowledge_release_log
           (release_id,release_label,status,previous_release,change_reason,related_release,
            content_hash,reviewer) VALUES (?,?,?,?,?,?,?,?)""",
        (release_id, release_label, status, previous_release, change_reason,
         related_release, content_hash, reviewer),
    )
    db.commit()
    return {"success": True, "release_id": release_id, "release_label": release_label}


def record_capability_gap(db, gap_key, description, sector=None, impact=None,
                          case_id=None, proposed_release=None):
    ensure_schema(db)
    row = db.execute(
        "SELECT gap_id,occurrence_count,case_ids_json FROM capability_gaps WHERE gap_key=?",
        (gap_key,),
    ).fetchone()
    if row:
        case_ids = json.loads(row["case_ids_json"] or "[]")
        if case_id and case_id not in case_ids:
            case_ids.append(case_id)
        db.execute(
            """UPDATE capability_gaps SET occurrence_count=occurrence_count+1,
               case_ids_json=?,updated_at=now(),impact=COALESCE(?,impact),
               proposed_release=COALESCE(?,proposed_release) WHERE gap_key=?""",
            (json.dumps(case_ids, ensure_ascii=False), impact, proposed_release, gap_key),
        )
        gap_id = row["gap_id"]
    else:
        gap_id = "GAP-" + uuid.uuid4().hex[:12].upper()
        db.execute(
            """INSERT INTO capability_gaps
               (gap_id,gap_key,sector,description,impact,case_ids_json,proposed_release)
               VALUES (?,?,?,?,?,?,?)""",
            (gap_id, gap_key, sector, description, impact,
             json.dumps([case_id] if case_id else [], ensure_ascii=False), proposed_release),
        )
    db.commit()
    return {"success": True, "gap_id": gap_id}


def coverage_report(db):
    report = drive_index_report(db)
    latest = report["latest_run"] or {}
    return {
        "A_drive_root_and_master_index": {
            "status": "complete" if report["config"] and report["config"].get("master_index_file_id") else "manual_required",
            "details": report["config"],
        },
        "B_metadata_index": {"status": "complete" if latest.get("status") == "success" else "manual_required", "details": report["stats"]},
        "C_idempotent_read_only_sync": {"status": "complete", "details": "upsert by Drive ID; no Drive delete/move/rename"},
        "D_client_folder_mapping": {"status": "manual_required", "details": "explicit company mapping is required"},
        "E_contextual_file_selection": {"status": "complete", "details": "company/case/topic filters"},
        "F_private_source_boundary": {"status": "complete", "details": "restricted files excluded from shared context"},
        "G_memory_and_provenance": {"status": "complete", "details": "structured memory and source links available"},
        "H_append_only_releases": {"status": "complete", "details": "immutable release labels"},
        "I_capability_gaps": {"status": "complete", "details": "recurring gaps are recorded"},
        "J_customer_coverage": {"status": "manual_required", "details": "map each client folder explicitly"},
        "K_backup_boundary": {"status": "manual_required", "details": "operational backup remains separate"},
        "L_content_extraction_review": {"status": "manual_required", "details": "select sections and review before creating knowledge"},
    }