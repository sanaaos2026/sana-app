"""Company Memory — ذاكرة خاصة بكل شركة فوق سجلات Sana الأصلية.

هذه الوحدة لا تنسخ الجداول المصدرية ولا تستبدلها. كل كتابة تنشئ إصدارًا
إضافيًا، بينما يشير سجل الذاكرة المنطقي إلى قيمة حالية صالحة فقط عندما تكون
موثقة وحديثة ولا يوجد تعارض مفتوح.
"""

import hashlib
import json
import os
import uuid
from datetime import date, datetime, timedelta

from database_config import acquire_schema_lock


MEMORY_TYPES = {
    "stable_profile", "current_state", "historical_state", "goal", "event",
    "problem", "finding", "evidence", "decision", "execution", "outcome",
    "learning", "unknown",
}
FRESHNESS_CLASSES = {"STATIC", "SLOW", "MEDIUM", "FAST", "REALTIME"}
VERIFICATION_STATUSES = {"UNVERIFIED", "VERIFIED", "CONTRADICTED", "STALE", "REJECTED"}
LIFECYCLE_STATUSES = {
    "CAPTURED", "NORMALIZED", "VERIFIED", "UNVERIFIED", "ACTIVE", "UPDATED",
    "OUTDATED", "HISTORICAL", "CONFLICT", "REJECTED",
}
ACCESS_LEVELS = {"company", "internal", "admin"}
CONFIDENTIALITIES = {"private", "confidential", "internal"}
USAGE_RIGHTS = {"company_only", "internal_only", "no_cross_company"}
FRESHNESS_DAYS = {
    "STATIC": 3650,
    "SLOW": 365,
    "MEDIUM": 90,
    "FAST": 30,
    "REALTIME": 1,
}

MEMORY_GOVERNANCE_ID = "CM-COMPANY-MEMORY"
MEMORY_VERSION = "v1.0"
_SCHEMA_READY = False


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _loads(value, default=None):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _date(value, required=False):
    if value is None or value == "":
        if required:
            raise ValueError("observed_at مطلوب")
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise ValueError("التاريخ يجب أن يكون بصيغة YYYY-MM-DD") from exc


def _confidence(value, field):
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} يجب أن يكون بين 0 و100") from exc
    if not 0 <= result <= 100:
        raise ValueError(f"{field} يجب أن يكون بين 0 و100")
    return result


def ensure_schema(db):
    """ترقية إضافية غير هدامة؛ يثبت خانات الحوكمة الخمس قبل أي سجل."""
    global _SCHEMA_READY
    if _SCHEMA_READY or os.environ.get("SANA_PRODUCTION_SCHEMA_READY") == "1":
        _SCHEMA_READY = True
        return
    acquire_schema_lock(db)
    db.execute("""CREATE TABLE IF NOT EXISTS company_memory_governance (
        governance_id TEXT PRIMARY KEY,
        storage_destination TEXT NOT NULL,
        case_link TEXT NOT NULL,
        asset_link TEXT NOT NULL,
        framework_link TEXT NOT NULL,
        business_event TEXT NOT NULL,
        version TEXT NOT NULL DEFAULT 'v1.0',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS company_memory_items (
        memory_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
        memory_key TEXT NOT NULL,
        memory_type TEXT NOT NULL,
        current_version_id TEXT,
        current_status TEXT NOT NULL DEFAULT 'UNKNOWN',
        owner_id TEXT,
        access_level TEXT NOT NULL DEFAULT 'company',
        confidentiality TEXT NOT NULL DEFAULT 'private',
        retention_policy TEXT NOT NULL DEFAULT 'retain_history',
        usage_rights TEXT NOT NULL DEFAULT 'no_cross_company',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(company_id, memory_key),
        CHECK (access_level IN ('company','internal','admin')),
        CHECK (confidentiality IN ('private','confidential','internal')),
        CHECK (usage_rights IN ('company_only','internal_only','no_cross_company'))
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS company_memory_versions (
        version_id TEXT PRIMARY KEY,
        memory_id TEXT NOT NULL REFERENCES company_memory_items(memory_id) ON DELETE CASCADE,
        company_id TEXT NOT NULL,
        memory_type TEXT NOT NULL,
        value_json TEXT NOT NULL,
        context_json TEXT NOT NULL DEFAULT '{}',
        period_start DATE,
        period_end DATE,
        observed_at DATE NOT NULL,
        source_ref TEXT NOT NULL,
        source_type TEXT NOT NULL DEFAULT 'unknown',
        case_id TEXT,
        asset_id TEXT,
        decision_id TEXT,
        task_id TEXT,
        result_ref TEXT,
        reason TEXT,
        lifecycle_status TEXT NOT NULL DEFAULT 'CAPTURED',
        verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
        freshness_class TEXT NOT NULL DEFAULT 'MEDIUM',
        source_strength INTEGER NOT NULL DEFAULT 0 CHECK (source_strength BETWEEN 0 AND 100),
        verification_confidence INTEGER NOT NULL DEFAULT 0 CHECK (verification_confidence BETWEEN 0 AND 100),
        freshness_confidence INTEGER NOT NULL DEFAULT 0 CHECK (freshness_confidence BETWEEN 0 AND 100),
        owner_id TEXT,
        governance_id TEXT NOT NULL REFERENCES company_memory_governance(governance_id),
        supersedes_version_id TEXT REFERENCES company_memory_versions(version_id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (memory_type IN (
          'stable_profile','current_state','historical_state','goal','event',
          'problem','finding','evidence','decision','execution','outcome',
          'learning','unknown'
        )),
        CHECK (lifecycle_status IN (
          'CAPTURED','NORMALIZED','VERIFIED','UNVERIFIED','ACTIVE','UPDATED',
          'OUTDATED','HISTORICAL','CONFLICT','REJECTED'
        )),
        CHECK (verification_status IN ('UNVERIFIED','VERIFIED','CONTRADICTED','STALE','REJECTED')),
        CHECK (freshness_class IN ('STATIC','SLOW','MEDIUM','FAST','REALTIME')),
        CHECK (period_end IS NULL OR period_start IS NULL OR period_end >= period_start)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS company_memory_links (
        link_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        version_id TEXT NOT NULL REFERENCES company_memory_versions(version_id) ON DELETE CASCADE,
        source_type TEXT NOT NULL,
        source_id TEXT NOT NULL,
        relationship TEXT NOT NULL DEFAULT 'supports',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(company_id, version_id, source_type, source_id, relationship)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS company_memory_conflicts (
        conflict_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        memory_id TEXT NOT NULL REFERENCES company_memory_items(memory_id) ON DELETE CASCADE,
        existing_version_id TEXT NOT NULL REFERENCES company_memory_versions(version_id),
        incoming_version_id TEXT NOT NULL REFERENCES company_memory_versions(version_id),
        status TEXT NOT NULL DEFAULT 'OPEN',
        conflict_reason TEXT NOT NULL,
        resolution_action TEXT,
        resolved_version_id TEXT REFERENCES company_memory_versions(version_id),
        resolved_by TEXT,
        resolved_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (status IN ('OPEN','RESOLVED','KEPT_OPEN'))
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_memory_items_company ON company_memory_items(company_id, updated_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_memory_versions_company ON company_memory_versions(company_id, observed_at DESC, created_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_memory_versions_context ON company_memory_versions(company_id, memory_type, case_id, decision_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_memory_conflicts_company ON company_memory_conflicts(company_id, status, created_at DESC)")
    # روابط السجلات المصدرية مدققة عند الكتابة لكنها soft references: الذاكرة
    # تحفظ التاريخ ولا تملك القضية/القرار ولا تمنع سياسات حذفهما الأصلية.
    for constraint in (
        "company_memory_versions_case_id_fkey",
        "company_memory_versions_asset_id_fkey",
        "company_memory_versions_decision_id_fkey",
        "company_memory_versions_company_id_fkey",
        "company_memory_links_company_id_fkey",
        "company_memory_conflicts_company_id_fkey",
    ):
        table = (
            "company_memory_versions" if "_versions_" in constraint
            else "company_memory_links" if "_links_" in constraint
            else "company_memory_conflicts"
        )
        db.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}")
    db.execute(
        """ALTER TABLE company_memory_items
           DROP CONSTRAINT IF EXISTS company_memory_items_company_id_fkey"""
    )
    db.execute(
        """ALTER TABLE company_memory_items
           ADD CONSTRAINT company_memory_items_company_id_fkey
           FOREIGN KEY (company_id) REFERENCES companies(company_id) ON DELETE CASCADE"""
    )
    db.execute(
        """INSERT INTO company_memory_governance
           (governance_id,storage_destination,case_link,asset_link,framework_link,business_event,version)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT (governance_id) DO NOTHING""",
        (
            MEMORY_GOVERNANCE_ID, "company_memory_items/company_memory_versions",
            "case_id → cases", "asset_id → assets",
            "framework:B2B-OS-001", "company.memory.version.recorded", MEMORY_VERSION,
        ),
    )
    _SCHEMA_READY = True


def _freshness_state(observed_at, freshness_class, today=None):
    today = today or date.today()
    observed = _date(observed_at)
    if not observed:
        return "UNKNOWN", None, True
    age_days = max(0, (today - observed).days)
    limit = FRESHNESS_DAYS.get(freshness_class, FRESHNESS_DAYS["MEDIUM"])
    return ("FRESH" if age_days <= limit else "STALE"), age_days, age_days > limit


def _value_equal(left, right):
    return _json(left) == _json(right)


def _memory_row(db, company_id, memory_key):
    return db.execute(
        "SELECT * FROM company_memory_items WHERE company_id=? AND memory_key=? FOR UPDATE",
        (company_id, memory_key),
    ).fetchone()


def record_memory(
    db, company_id, *, memory_key, memory_type, value, source_ref,
    observed_at=None, period_start=None, period_end=None, source_type="unknown",
    context=None, verification_status="UNVERIFIED", freshness_class="MEDIUM",
    source_strength=0, verification_confidence=0, freshness_confidence=0,
    owner_id=None, access_level="company", confidentiality="private",
    retention_policy="retain_history", usage_rights="no_cross_company",
    case_id=None, asset_id=None, decision_id=None, task_id=None, result_ref=None,
    reason=None, source_id=None, lifecycle_status=None,
):
    """سجّل إصدارًا جديدًا، وحدّث المؤشر الحالي فقط لقيمة موثقة غير متعارضة."""
    ensure_schema(db)
    memory_key = str(memory_key or "").strip()
    if not memory_key:
        raise ValueError("memory_key مطلوب")
    if memory_type not in MEMORY_TYPES:
        raise ValueError("memory_type غير صالح")
    if not str(source_ref or "").strip():
        raise ValueError("source_ref مطلوب")
    if freshness_class not in FRESHNESS_CLASSES:
        raise ValueError("freshness_class غير صالح")
    if verification_status not in VERIFICATION_STATUSES:
        raise ValueError("verification_status غير صالح")
    if access_level not in ACCESS_LEVELS or confidentiality not in CONFIDENTIALITIES:
        raise ValueError("صلاحية الذاكرة غير صالحة")
    if usage_rights not in USAGE_RIGHTS:
        raise ValueError("حقوق استخدام الذاكرة غير صالحة")
    observed = _date(observed_at, required=True)
    start, end = _date(period_start), _date(period_end)
    if bool(start) != bool(end):
        raise ValueError("period_start وperiod_end مطلوبان معًا")
    if start and end < start:
        raise ValueError("period_end يجب أن يساوي أو يتجاوز period_start")
    if case_id and not db.execute(
        "SELECT 1 FROM cases WHERE case_id=? AND company_id=?", (case_id, company_id)
    ).fetchone():
        raise ValueError("case_id لا يتبع الشركة")
    if asset_id and not db.execute(
        "SELECT 1 FROM assets WHERE asset_id=? AND company_id=?", (asset_id, company_id)
    ).fetchone():
        raise ValueError("asset_id لا يتبع الشركة")
    if decision_id and not db.execute(
        "SELECT 1 FROM decisions WHERE decision_id=? AND company_id=?", (decision_id, company_id)
    ).fetchone():
        raise ValueError("decision_id لا يتبع الشركة")

    source_strength = _confidence(source_strength, "source_strength")
    verification_confidence = _confidence(verification_confidence, "verification_confidence")
    freshness_confidence = _confidence(freshness_confidence, "freshness_confidence")
    existing = _memory_row(db, company_id, memory_key)
    if not existing:
        memory_id = _id("MEM")
        db.execute(
            """INSERT INTO company_memory_items
               (memory_id,company_id,memory_key,memory_type,owner_id,access_level,
                confidentiality,retention_policy,usage_rights)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (memory_id, company_id, memory_key, memory_type, owner_id, access_level,
             confidentiality, retention_policy, usage_rights),
        )
        existing = db.execute(
            "SELECT * FROM company_memory_items WHERE memory_id=?", (memory_id,)
        ).fetchone()
    else:
        memory_id = existing["memory_id"]
        if existing["memory_type"] != memory_type:
            raise ValueError("memory_key موجود بنوع مختلف")

    prior = None
    if existing["current_version_id"]:
        prior = db.execute(
            "SELECT * FROM company_memory_versions WHERE version_id=?",
            (existing["current_version_id"],),
        ).fetchone()
    if not prior:
        prior = db.execute(
            """SELECT * FROM company_memory_versions WHERE memory_id=?
               ORDER BY observed_at DESC,created_at DESC LIMIT 1""",
            (memory_id,),
        ).fetchone()

    freshness_state, _age, is_stale = _freshness_state(observed, freshness_class)
    is_verified = verification_status == "VERIFIED"
    conflicts = bool(
        prior and not _value_equal(_loads(prior["value_json"]), value)
        and (prior["period_start"] == start or not (prior["period_start"] and start))
    )
    version_id = _id("MEMV")
    status = lifecycle_status or ("VERIFIED" if is_verified else "UNVERIFIED")
    if is_stale:
        status = "OUTDATED"
    if conflicts:
        status = "CONFLICT"
        verification_status = "CONTRADICTED"
    db.execute(
        """INSERT INTO company_memory_versions
           (version_id,memory_id,company_id,memory_type,value_json,context_json,
            period_start,period_end,observed_at,source_ref,source_type,case_id,
            asset_id,decision_id,task_id,result_ref,reason,lifecycle_status,
            verification_status,freshness_class,source_strength,
            verification_confidence,freshness_confidence,owner_id,
            governance_id,supersedes_version_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            version_id, memory_id, company_id, memory_type, _json(value),
            _json(context or {}), start, end, observed, str(source_ref).strip(),
            source_type, case_id, asset_id, decision_id, task_id, result_ref, reason,
            status, verification_status, freshness_class, source_strength,
            verification_confidence, freshness_confidence, owner_id,
            MEMORY_GOVERNANCE_ID, prior["version_id"] if prior else None,
        ),
    )
    if source_id:
        db.execute(
            """INSERT INTO company_memory_links
               (link_id,company_id,version_id,source_type,source_id,relationship)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT (company_id,version_id,source_type,source_id,relationship) DO NOTHING""",
            (_id("ML"), company_id, version_id, source_type, str(source_id), "supports"),
        )

    current_id = existing["current_version_id"]
    current_status = existing["current_status"] or "UNKNOWN"
    if conflicts:
        conflict_id = _id("MC")
        db.execute(
            """INSERT INTO company_memory_conflicts
               (conflict_id,company_id,memory_id,existing_version_id,
                incoming_version_id,conflict_reason)
               VALUES (?,?,?,?,?,?)""",
            (
                conflict_id, company_id, memory_id,
                prior["version_id"] if prior else version_id, version_id,
                "القيمة الجديدة تختلف عن قيمة الذاكرة الحالية؛ لم يحدث استبدال تلقائي.",
            ),
        )
        current_status = "CONFLICT"
    elif is_verified and not is_stale:
        if prior and prior["version_id"] != version_id:
            db.execute(
                "UPDATE company_memory_versions SET lifecycle_status='HISTORICAL' WHERE version_id=?",
                (prior["version_id"],),
            )
        current_id, current_status = version_id, "ACTIVE"
    elif not current_id:
        current_status = "UNVERIFIED" if not is_stale else "OUTDATED"
    db.execute(
        """UPDATE company_memory_items
           SET current_version_id=?,current_status=?,owner_id=COALESCE(?,owner_id),
               access_level=?,confidentiality=?,retention_policy=?,usage_rights=?,updated_at=now()
           WHERE memory_id=? AND company_id=?""",
        (
            current_id, current_status, owner_id, access_level, confidentiality,
            retention_policy, usage_rights, memory_id, company_id,
        ),
    )
    return {
        "memory_id": memory_id,
        "version_id": version_id,
        "current_version_id": current_id,
        "status": current_status,
        "conflict_id": conflict_id if conflicts else None,
        "historical_preserved": bool(prior),
    }


def _version_view(row, *, current=False, conflict_status=None, today=None):
    keys = set(row.keys())
    def field(name, alias=None):
        if name in keys:
            return row[name]
        return row[alias] if alias and alias in keys else None

    value = _loads(row["value_json"], row["value_json"])
    freshness, age_days, needs_confirmation = _freshness_state(
        row["observed_at"], row["freshness_class"], today=today
    )
    verified = row["verification_status"] == "VERIFIED"
    return {
        "memory_id": row["memory_id"],
        "version_id": row["version_id"],
        "memory_key": row["memory_key"],
        "memory_type": row["memory_type"],
        "value": value,
        "context": _loads(row["context_json"], {}),
        "period_start": row["period_start"].isoformat() if row["period_start"] else None,
        "period_end": row["period_end"].isoformat() if row["period_end"] else None,
        "observed_at": row["observed_at"].isoformat() if hasattr(row["observed_at"], "isoformat") else str(row["observed_at"]),
        "source_ref": row["source_ref"],
        "source_type": row["source_type"],
        "case_id": field("case_id", "v_case_id"),
        "asset_id": field("asset_id", "v_asset_id"),
        "decision_id": field("decision_id", "v_decision_id"),
        "task_id": field("task_id", "v_task_id"),
        "result_ref": row["result_ref"],
        "reason": row["reason"],
        "verification_status": row["verification_status"],
        "lifecycle_status": row["lifecycle_status"],
        "freshness_class": row["freshness_class"],
        "freshness": freshness,
        "age_days": age_days,
        "needs_confirmation": bool(needs_confirmation or not verified),
        "reusable": bool(current and verified and freshness == "FRESH" and not conflict_status),
        "confidence": {
            "source_strength": row["source_strength"],
            "verification": row["verification_confidence"],
            "freshness": row["freshness_confidence"],
        },
        "is_current": current,
        "conflict_status": conflict_status,
    }


def retrieve_memory(
    db, company_id, *, case_id=None, problem=None, kpi=None, decision_id=None,
    task_id=None, memory_keys=None, include_history=False, today=None,
):
    """استرجاع سياقي محدود؛ لا يعيد ذاكرة شركة أخرى ولا يخلط المعرفة العامة."""
    ensure_schema(db)
    params = [company_id]
    rows = db.execute(
        """SELECT i.*, v.version_id, v.memory_id AS v_memory_id, v.memory_type AS v_memory_type,
                  v.value_json,v.context_json,v.period_start,v.period_end,v.observed_at,
                  v.source_ref,v.source_type,v.case_id AS v_case_id,v.asset_id AS v_asset_id,
                  v.decision_id AS v_decision_id,v.task_id AS v_task_id,v.result_ref,
                  v.reason,v.lifecycle_status,v.verification_status,v.freshness_class,
                  v.source_strength,v.verification_confidence,v.freshness_confidence
           FROM company_memory_items i
           LEFT JOIN LATERAL (
             SELECT *
             FROM company_memory_versions candidate
             WHERE candidate.memory_id=i.memory_id AND candidate.company_id=i.company_id
             ORDER BY
               CASE WHEN candidate.version_id=i.current_version_id THEN 0 ELSE 1 END,
               candidate.observed_at DESC,candidate.created_at DESC
             LIMIT 1
           ) v ON TRUE
           WHERE i.company_id=?
           ORDER BY i.updated_at DESC, i.memory_id DESC""",
        params,
    ).fetchall()
    requested = {str(key).strip() for key in (memory_keys or []) if str(key).strip()}
    tokens = {
        str(value or "").casefold()
        for value in (problem, kpi)
        if str(value or "").strip()
    }
    result = []
    for row in rows:
        if requested and row["memory_key"] not in requested:
            continue
        context = _loads(row["context_json"], {}) if row["version_id"] else {}
        row_text = " ".join([
            row["memory_key"], row["memory_type"], json.dumps(context, ensure_ascii=False),
        ]).casefold()
        linked_case = row["v_case_id"] if row["version_id"] else None
        linked_decision = row["v_decision_id"] if row["version_id"] else None
        linked_task = row["v_task_id"] if row["version_id"] else None
        if case_id and linked_case not in {None, case_id}:
            continue
        if decision_id and linked_decision not in {None, decision_id}:
            continue
        if task_id and linked_task not in {None, task_id}:
            continue
        if tokens and not any(token in row_text for token in tokens):
            continue
        if not row["version_id"]:
            continue
        conflicts = db.execute(
            """SELECT conflict_id,status,incoming_version_id,existing_version_id
               FROM company_memory_conflicts
               WHERE company_id=? AND memory_id=? AND status IN ('OPEN','KEPT_OPEN')
               ORDER BY created_at DESC""",
            (company_id, row["memory_id"]),
        ).fetchall()
        conflict_status = "OPEN" if conflicts else None
        is_current = bool(
            row["current_version_id"]
            and row["current_version_id"] == row["version_id"]
        )
        current = _version_view(
            row, current=is_current, conflict_status=conflict_status, today=today
        )
        current["match_reason"] = (
            "linked_case" if case_id and linked_case == case_id else
            "linked_decision" if decision_id and linked_decision == decision_id else
            "linked_task" if task_id and linked_task == task_id else
            "problem_or_kpi" if tokens else "company_context"
        )
        if conflict_status:
            current["reusable"] = False
            current["needs_confirmation"] = True
        result.append(current)
        if include_history:
            history = db.execute(
                """SELECT v.*, i.memory_key FROM company_memory_versions v
                   JOIN company_memory_items i ON i.memory_id=v.memory_id
                   WHERE v.company_id=? AND v.memory_id=? AND v.version_id<>?
                   ORDER BY v.observed_at DESC,v.created_at DESC""",
                (company_id, row["memory_id"], row["version_id"]),
            ).fetchall()
            result.extend(
                _version_view(item, current=False, conflict_status=None, today=today)
                for item in history
            )
    result.sort(key=lambda item: (
        0 if item["reusable"] else 1 if item["verification_status"] == "VERIFIED" else 2,
        item["age_days"] if item["age_days"] is not None else 999999,
    ))
    return {
        "company_id": company_id,
        "items": result,
        "unknown": not bool(result),
        "summary": {
            "reusable": sum(1 for item in result if item["reusable"]),
            "needs_confirmation": sum(1 for item in result if item["needs_confirmation"]),
            "conflicts": sum(1 for item in result if item["conflict_status"]),
            "historical_included": bool(include_history),
        },
    }


def confirm_memory(
    db, company_id, memory_id, *, actor_id, observed_at=None, reason=None,
    presented_version_id=None,
):
    """تأكيد سريع ينشئ إصدارًا موثقًا؛ لا يعدّل الإصدار السابق."""
    ensure_schema(db)
    open_conflict = db.execute(
        """SELECT conflict_id FROM company_memory_conflicts
           WHERE company_id=? AND memory_id=? AND status IN ('OPEN','KEPT_OPEN')
           ORDER BY created_at DESC LIMIT 1""",
        (company_id, memory_id),
    ).fetchone()
    if open_conflict:
        raise ValueError("MEMORY_CONFLICT_REVIEW_REQUIRED")
    row = db.execute(
        """SELECT i.memory_key,i.memory_type,i.owner_id,i.access_level,
                  i.confidentiality,i.retention_policy,i.usage_rights,v.*
           FROM company_memory_items i
           JOIN LATERAL (
             SELECT * FROM company_memory_versions
             WHERE memory_id=i.memory_id AND company_id=i.company_id
             ORDER BY
               CASE WHEN version_id=i.current_version_id THEN 0 ELSE 1 END,
               observed_at DESC,created_at DESC
             LIMIT 1
           ) v ON TRUE
           WHERE i.memory_id=? AND i.company_id=?""",
        (memory_id, company_id),
    ).fetchone()
    if not row:
        raise LookupError("MEMORY_NOT_FOUND")
    if presented_version_id and row["version_id"] != presented_version_id:
        raise ValueError("MEMORY_VERSION_CHANGED_RELOAD_REQUIRED")
    confirmed = record_memory(
        db, company_id, memory_key=row["memory_key"], memory_type=row["memory_type"],
        value=_loads(row["value_json"], row["value_json"]),
        source_ref=f"memory-confirmation:{memory_id}",
        observed_at=observed_at or date.today(), period_start=row["period_start"],
        period_end=row["period_end"], source_type="account-confirmation",
        context={**(_loads(row["context_json"], {}) or {}), "confirmed_by": actor_id},
        verification_status="VERIFIED", freshness_class=row["freshness_class"],
        source_strength=max(60, int(row["source_strength"] or 0)),
        verification_confidence=max(75, int(row["verification_confidence"] or 0)),
        freshness_confidence=90, owner_id=row["owner_id"] or actor_id,
        access_level=row["access_level"], confidentiality=row["confidentiality"],
        retention_policy=row["retention_policy"], usage_rights=row["usage_rights"],
        case_id=row["case_id"], asset_id=row["asset_id"], decision_id=row["decision_id"],
        task_id=row["task_id"], result_ref=row["result_ref"],
        reason=reason or "تأكيد صريح من مالك سياق الشركة.",
    )
    if confirmed["current_version_id"] != confirmed["version_id"] or confirmed["status"] != "ACTIVE":
        raise ValueError("MEMORY_CONFIRMATION_NOT_ACTIVE")
    return confirmed


def list_memory_history(db, company_id, memory_id):
    ensure_schema(db)
    rows = db.execute(
        """SELECT v.*,i.memory_key FROM company_memory_versions v
           JOIN company_memory_items i ON i.memory_id=v.memory_id
           WHERE v.company_id=? AND v.memory_id=?
           ORDER BY v.observed_at DESC,v.created_at DESC""",
        (company_id, memory_id),
    ).fetchall()
    return [_version_view(row, current=False) for row in rows]


def resolve_memory_conflict(db, company_id, conflict_id, *, action, actor_id, reason):
    """اختر قيمة بمراجعة صريحة أو أبقِ التعارض مفتوحًا."""
    ensure_schema(db)
    if action not in {"accept_existing", "accept_incoming", "keep_open"}:
        raise ValueError("CONFLICT_ACTION_INVALID")
    if not str(reason or "").strip():
        raise ValueError("CONFLICT_REASON_REQUIRED")
    conflict = db.execute(
        """SELECT * FROM company_memory_conflicts
           WHERE conflict_id=? AND company_id=? AND status IN ('OPEN','KEPT_OPEN')
           FOR UPDATE""",
        (conflict_id, company_id),
    ).fetchone()
    if not conflict:
        raise LookupError("MEMORY_CONFLICT_NOT_FOUND")
    if action == "keep_open":
        db.execute(
            """UPDATE company_memory_conflicts
               SET status='KEPT_OPEN',resolution_action=?,resolved_by=?,resolved_at=now(),
                   conflict_reason=?
               WHERE conflict_id=? AND company_id=?""",
            (action, actor_id, f"{conflict['conflict_reason']} {reason}", conflict_id, company_id),
        )
        return {"conflict_id": conflict_id, "status": "KEPT_OPEN"}
    chosen = (
        conflict["existing_version_id"]
        if action == "accept_existing" else conflict["incoming_version_id"]
    )
    other = (
        conflict["incoming_version_id"]
        if action == "accept_existing" else conflict["existing_version_id"]
    )
    db.execute(
        "UPDATE company_memory_versions SET lifecycle_status='HISTORICAL' WHERE version_id=?",
        (other,),
    )
    db.execute(
        "UPDATE company_memory_versions SET lifecycle_status='ACTIVE',verification_status='VERIFIED' WHERE version_id=?",
        (chosen,),
    )
    db.execute(
        """UPDATE company_memory_items SET current_version_id=?,current_status='ACTIVE',updated_at=now()
           WHERE memory_id=? AND company_id=?""",
        (chosen, conflict["memory_id"], company_id),
    )
    db.execute(
        """UPDATE company_memory_conflicts
           SET status='RESOLVED',resolution_action=?,resolved_version_id=?,
               resolved_by=?,resolved_at=now(),conflict_reason=?
           WHERE conflict_id=? AND company_id=?""",
        (action, chosen, actor_id, f"{conflict['conflict_reason']} {reason}", conflict_id, company_id),
    )
    return {"conflict_id": conflict_id, "status": "RESOLVED", "current_version_id": chosen}


def capture_discovery(db, company_id, *, case_id, answers, owner_id=None):
    """حوّل إجابات SDS إلى إصدارات خاصة؛ لا تسجل التعبئة المسبقة كإجابة."""
    answers = answers or {}
    observed = date.today()
    records = [
        ("goal:primary", "goal", answers.get("q1"), "SDS-001 Q1", "SDS-001"),
        ("problem:declared", "problem", answers.get("q2"), "SDS-001 Q2", "SDS-001"),
        ("profile:top_asset", "stable_profile", answers.get("q3"), "SDS-001 Q3", "SDS-001"),
        ("acquisition:source", "current_state", answers.get("q4"), "SDS-001 Q4", "SDS-001"),
        ("acquisition:fragility", "current_state", answers.get("q4_fu"), "SDS-001 Q4 follow-up", "SDS-001"),
        ("founder:dependency", "current_state", answers.get("q5"), "SDS-001 Q5", "SDS-001"),
        ("decision:style", "current_state", answers.get("q6"), "SDS-001 Q6", "SDS-001"),
        ("goal:success_criteria", "goal", answers.get("q7"), "SDS-001 Q7", "SDS-001"),
    ]
    captured = []
    for key, memory_type, value, source_ref, source_type in records:
        if value in (None, "", []):
            continue
        captured.append(record_memory(
            db, company_id, memory_key=key, memory_type=memory_type, value=value,
            source_ref=source_ref, observed_at=observed, source_type=source_type,
            verification_status="UNVERIFIED", freshness_class="MEDIUM",
            source_strength=35, verification_confidence=0, freshness_confidence=40,
            owner_id=owner_id, case_id=case_id,
            context={"flow": "SDS-001", "self_reported": True},
            reason="إجابة Discovery أولية؛ تحتاج تحققًا قبل استخدامها كحقيقة.",
        ))
    return captured


def record_learning(
    db, company_id, *, case_id, decision_id=None, task_id=None, result_ref=None,
    changed=None, unchanged=None, hypothesis_correct=None, decision_useful=None,
    execution_complete=None, remember=None, source_ref="p0-impact-review",
    owner_id=None,
):
    value = {
        "what_changed": changed or [],
        "what_did_not_change": unchanged or [],
        "hypothesis_correct": hypothesis_correct,
        "decision_useful": decision_useful,
        "execution_complete": execution_complete,
        "what_sana_should_remember": remember,
    }
    digest = hashlib.sha256(_json(value).encode("utf-8")).hexdigest()[:16]
    return record_memory(
        db, company_id, memory_key=f"learning:{case_id}:{digest}",
        memory_type="learning", value=value, source_ref=source_ref,
        observed_at=date.today(), source_type="p0-impact-review",
        verification_status="VERIFIED", freshness_class="SLOW",
        source_strength=80, verification_confidence=80, freshness_confidence=70,
        owner_id=owner_id, case_id=case_id, decision_id=decision_id, task_id=task_id,
        result_ref=result_ref, context={"private_client_learning": True},
        reason="تعلم ناتج عن نتيجة ومراجعة أثر؛ لا يُرقّى إلى Sana Knowledge تلقائيًا.",
    )


# أسماء صريحة للاستهلاك من المسارات والاختبارات.
create_memory_version = record_memory
retrieve_company_memory = retrieve_memory
