"""Sana Knowledge — قاعدة المعرفة ومحرك التشخيص الحتمي.

المبدأ: المعرفة -> الدليل -> القاعدة -> القرار -> التوصية.
لا يستدعي هذا الملف أي مزود AI ولا يسمح له بصناعة قرار غير موجود في القاعدة.
"""
import hashlib
import io
import json
import os
import re
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime
from database_config import acquire_schema_lock


LIBRARY_TYPES = [
    ("PROBLEM", "مكتبة المشكلات"),
    ("CAUSE", "مكتبة الأسباب"),
    ("DIAGNOSTIC_QUESTION", "مكتبة الأسئلة التشخيصية"),
    ("EVIDENCE_REQUIREMENT", "مكتبة الأدلة المطلوبة"),
    ("KPI", "سجل مؤشرات الأداء"),
    ("BENCHMARK", "مكتبة المعايير المرجعية"),
    ("DIAGNOSTIC_RULE", "قواعد التشخيص"),
    ("DECISION_RULE", "قواعد القرار"),
    ("DIAGNOSTIC_PATTERN", "أنماط التشخيص"),
    ("OPPORTUNITY", "مكتبة الفرص"),
    ("RECOMMENDATION", "مكتبة التوصيات"),
    ("SOP", "مكتبة إجراءات التشغيل"),
    ("CASE", "مكتبة الحالات"),
    ("FRAMEWORK", "مكتبة الأطر"),
]

RESEARCH_SOURCE_KINDS = {
    "file": "ملف أو وثيقة",
    "book": "كتاب",
    "research": "بحث أو دراسة",
    "summary": "ملخص أو ملاحظة",
}

SUPPORTED_SECTORS = {
    "professional_services": "الخدمات المهنية B2B",
    "experts": "الخبراء وأعمال المعرفة",
    "ecommerce_retail": "التجارة الإلكترونية والتجزئة",
}

GENERAL_B2B_RULES_SOURCE_ID = "SRC-SANA-GENERAL-B2B-RULES"
GENERAL_B2B_RULES_SLUG = "general-service-b2b-growth-rules"
GENERAL_B2B_RULES_TITLE = "قواعد عامة لتطوير ونمو المشاريع الخدمية وB2B"
B2B_SERVICE_OS_SOURCE_ID = "SRC-B2B-SERVICE-OS"
B2B_SERVICE_OS_SLUG = "b2b-service-operating-system"
B2B_SERVICE_OS_TITLE = "B2B Service Operating System — نظام تشغيل المشاريع الخدمية وB2B"
B2B_SERVICE_OS_FRAMEWORK_ID = "FRAMEWORK-B2B-SERVICE-OS"
B2B_SERVICE_OS_ID = "B2B-OS-001"
B2B_SERVICE_OS_VERSION = "v1.0"
B2B_SERVICE_OS_WORKFLOW = (
    "Opportunity → Qualification → Diagnostic → Baseline → Bottleneck → "
    "Solution Design → Proposal → Delivery → Quality Assurance → Collection → "
    "Results → Renewal/Expansion → Knowledge Capture"
)
_RESEARCH_SCOPE_SCHEMA_READY = False
_KNOWLEDGE_SCHEMA_READY = False
SYSTEM_KNOWLEDGE_OWNER_ID = "SYSTEM_KNOWLEDGE_ADMIN"
CURATED_CLASSIFICATIONS = (
    "Confirmed Fact",
    "Historical Fact",
    "Evidence",
    "Proposal/Assumption",
    "Inference",
    "Forecast",
    "Conflict/Unresolved",
    "Operating Logic",
    "Unclassified",
)

TRUSTED_KNOWLEDGE_DOMAINS = (
    "strategy_business_models",
    "marketing_customer_experience",
    "sales_crm",
    "operations_quality",
    "finance_unit_economics",
    "leadership_human_resources",
    "information_systems_data_security_governance",
    "digital_transformation_ai",
    "project_risk_management",
)

_TRUSTED_SOURCE_COLUMNS = {
    "author_identity": "TEXT",
    "material_type": "TEXT",
    "methodology_note": "TEXT",
    "publisher_trust": "TEXT",
    "publisher_continuity": "TEXT",
    "site_age_evidence_url": "TEXT",
    "site_age_evidence_date": "DATE",
    "document_date": "DATE",
    "version_label": "TEXT",
    "content_fingerprint": "TEXT",
    "trust_level": "TEXT",
    "reviewed_at": "DATE",
    "eligibility_json": "TEXT",
}

_TRUSTED_OBJECT_COLUMNS = {
    "source_excerpt": "TEXT",
    "original_summary": "TEXT",
    "domains": "TEXT",
    "sector_tags": "TEXT",
    "business_model_tags": "TEXT",
    "stage_tags": "TEXT",
    "problem_tags": "TEXT",
    "goal_tags": "TEXT",
    "bottleneck_tags": "TEXT",
}


def _load_general_b2b_rules():
    """يقرأ المرجع العام من ملفه canonical ويستخرج القواعد للبحث الداخلي."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "GENERAL_SERVICE_B2B_GROWTH_RULES.md")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"مرجع قواعد الخدمات وB2B غير موجود: {path}")
    with open(path, "r", encoding="utf-8") as handle:
        markdown = handle.read()

    rules = []
    current_rule = None
    for line in markdown.splitlines():
        match = re.match(r"^###\s+(\d+)\.\s+(.+?)\s*$", line)
        if match:
            number, title = match.groups()
            current_rule = f"{number}. {title}"
            rules.append(current_rule)
        elif current_rule and line.strip() and not line.startswith("#") and not line.startswith("|"):
            rules[-1] = f"{rules[-1]} — {line.strip()}"
            current_rule = None
    if len(rules) != 40:
        raise ValueError(f"مرجع قواعد B2B يجب أن يحتوي 40 قاعدة، ووجد {len(rules)}")
    return markdown, rules


def _load_b2b_service_os():
    """يقرأ الإطار التشغيلي canonical ويتحقق من بنيته قبل إدخاله للمكتبة."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "B2B_SERVICE_OPERATING_SYSTEM.md")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"إطار تشغيل الخدمات وB2B غير موجود: {path}")
    with open(path, "r", encoding="utf-8") as handle:
        markdown = handle.read()
    version_match = re.search(
        r"^\*\*الإصدار:\*\*\s*([^\s]+)\s*$", markdown, flags=re.MULTILINE
    )
    if not version_match:
        raise ValueError(
            "إطار تشغيل الخدمات وB2B لا يحتوي على خانة إصدار canonical واضحة"
        )
    if version_match.group(1) != B2B_SERVICE_OS_VERSION:
        raise ValueError(
            "إصدار إطار تشغيل الخدمات وB2B في الملف "
            f"({version_match.group(1)}) لا يطابق الإصدار المسجل "
            f"({B2B_SERVICE_OS_VERSION}); حدّث الإصدار صراحة قبل الإطلاق"
        )

    def numbered_section(heading, expected, label):
        match = re.search(
            rf"^##\s+{re.escape(heading)}\s*$([\s\S]*?)(?=^##\s+|\Z)",
            markdown,
            flags=re.MULTILINE,
        )
        if not match:
            raise ValueError(f"قسم {label} غير موجود في إطار تشغيل الخدمات وB2B")
        entries = re.findall(r"^\d+\.\s+(.+?)\s*$", match.group(1), flags=re.MULTILINE)
        if len(entries) != expected:
            raise ValueError(
                f"إطار تشغيل الخدمات وB2B يجب أن يحتوي {expected} {label}، ووجد {len(entries)}"
            )
        return entries

    rules = numbered_section("القواعد المعرفية", 16, "قاعدة")
    systems = numbered_section("الأنظمة المرجعية", 10, "نظامًا مرجعيًا")
    if B2B_SERVICE_OS_WORKFLOW not in markdown:
        raise ValueError("دورة العمل المرجعية غير موجودة أو تغيرت في إطار تشغيل الخدمات وB2B")
    return markdown, rules, systems, B2B_SERVICE_OS_WORKFLOW


def _json(value):
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _loads(value, default=None):
    if value in (None, ""):
        return default if default is not None else []
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default if default is not None else []


def ensure_schema(db):
    """ترقية غير هدّامة لقاعدة المعرفة فوق قاعدة سنع الحالية."""
    global _KNOWLEDGE_SCHEMA_READY, _RESEARCH_SCOPE_SCHEMA_READY
    if _KNOWLEDGE_SCHEMA_READY:
        return
    schema_ready = db.execute(
        """SELECT
             (SELECT COUNT(*) FROM information_schema.columns
              WHERE table_schema='public'
                AND ((table_name='knowledge_sources' AND column_name='eligibility_json')
                  OR (table_name='knowledge_objects' AND column_name='bottleneck_tags'))) AS columns_ready,
             EXISTS (
               SELECT 1 FROM information_schema.tables
               WHERE table_schema='public' AND table_name='knowledge_conflicts'
             ) AS conflicts_ready"""
    ).fetchone()
    if (
        schema_ready
        and int(schema_ready["columns_ready"] or 0) == 2
        and bool(schema_ready["conflicts_ready"])
    ):
        _KNOWLEDGE_SCHEMA_READY = True
        _RESEARCH_SCOPE_SCHEMA_READY = True
        return
    acquire_schema_lock(db)
    db.execute("""CREATE TABLE IF NOT EXISTS knowledge_objects (
        object_id TEXT PRIMARY KEY,
        library_type TEXT NOT NULL,
        category TEXT NOT NULL,
        sector TEXT,
        subsector TEXT,
        title TEXT NOT NULL,
        problem TEXT,
        symptoms TEXT,
        possible_causes TEXT,
        diagnostic_questions TEXT,
        required_evidence TEXT,
        kpis TEXT,
        benchmark TEXT,
        diagnostic_rule TEXT,
        decision_rule TEXT,
        recommendation TEXT,
        sop TEXT,
        case_studies TEXT,
        source TEXT NOT NULL,
        source_url TEXT,
        evidence_quality TEXT,
        confidence_level TEXT,
        applicable_when TEXT,
        do_not_apply_when TEXT,
        knowledge_level TEXT NOT NULL DEFAULT 'L0',
        version TEXT NOT NULL DEFAULT 'v1.0',
        last_reviewed TEXT,
        status TEXT NOT NULL DEFAULT 'approved',
        created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
        updated_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))
    )""")
    source_column = db.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_name='knowledge_objects' AND column_name='source_id'"""
    ).fetchone()
    if not source_column:
        db.execute("ALTER TABLE knowledge_objects ADD COLUMN source_id TEXT")
    db.execute("""CREATE TABLE IF NOT EXISTS knowledge_sources (
        source_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        source_type TEXT NOT NULL,
        publisher TEXT,
        jurisdiction TEXT,
        source_url TEXT,
        rights_status TEXT NOT NULL,
        license_note TEXT,
        retrieved_at TEXT,
        review_due_at TEXT,
        status TEXT NOT NULL DEFAULT 'pending_review',
        created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
        updated_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))
    )""")
    source_columns = {
        row["column_name"] for row in db.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name='knowledge_sources'"""
        ).fetchall()
    }
    for column_name, definition in _TRUSTED_SOURCE_COLUMNS.items():
        if column_name not in source_columns:
            db.execute(
                f"ALTER TABLE knowledge_sources ADD COLUMN {column_name} {definition}"
            )
    db.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_source_fingerprint
           ON knowledge_sources(content_fingerprint)
           WHERE content_fingerprint IS NOT NULL"""
    )
    object_columns = {
        row["column_name"] for row in db.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name='knowledge_objects'"""
        ).fetchall()
    }
    for column_name, definition in _TRUSTED_OBJECT_COLUMNS.items():
        if column_name not in object_columns:
            db.execute(
                f"ALTER TABLE knowledge_objects ADD COLUMN {column_name} {definition}"
            )
    db.execute("""CREATE TABLE IF NOT EXISTS research_sources (
        research_source_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        origin TEXT NOT NULL DEFAULT 'manual',
        company_id TEXT,
        drive_file_id TEXT,
        source_url TEXT,
        author TEXT,
        publisher TEXT,
        publication_year INTEGER,
        language TEXT,
        jurisdiction TEXT,
        summary TEXT,
        notes TEXT,
        tags TEXT,
        rights_status TEXT NOT NULL DEFAULT 'pending',
        rights_expires_at DATE,
        review_status TEXT NOT NULL DEFAULT 'inbox',
        is_private SMALLINT NOT NULL DEFAULT 1,
        owner_account_id TEXT,
        created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
        updated_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
        CHECK (source_kind IN ('file','book','research','summary')),
        CHECK (origin IN ('manual','drive','url','upload')),
        CHECK (rights_status IN ('pending','owned','licensed','public','restricted')),
        CHECK (review_status IN ('inbox','approved','rejected','archived'))
    )""")
    if not _RESEARCH_SCOPE_SCHEMA_READY:
        # Multiple Sana processes can initialize the schema at the same time.
        # Serialize only this one-time compatibility migration.
        research_source_columns = {
            row["column_name"] for row in db.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema='public' AND table_name='research_sources'"""
            ).fetchall()
        }
    else:
        research_source_columns = {"company_id", "version_label"}
    needs_research_scope_migration = not {
        "company_id", "version_label"
    }.issubset(research_source_columns)
    if "company_id" not in research_source_columns:
        db.execute("ALTER TABLE research_sources ADD COLUMN company_id TEXT")
    if "version_label" not in research_source_columns:
        db.execute(
            "ALTER TABLE research_sources ADD COLUMN version_label TEXT DEFAULT 'v1.0'"
        )
    # Older databases created the intake table with CHECK (is_private = 1).
    # Keep the private-owner rule, while allowing an explicitly company-scoped
    # shared source to be searched after review.
    if needs_research_scope_migration:
        legacy_private_checks = db.execute(
            """SELECT c.conname
               FROM pg_constraint c
               JOIN pg_class t ON t.oid=c.conrelid
               JOIN pg_namespace n ON n.oid=t.relnamespace
               WHERE n.nspname='public' AND t.relname='research_sources'
                 AND c.contype='c'
                 AND pg_get_constraintdef(c.oid) ILIKE '%%is_private = 1%%'"""
        ).fetchall()
        for check in legacy_private_checks:
            constraint_name = str(check["conname"])
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", constraint_name):
                db.execute(
                    f'ALTER TABLE research_sources DROP CONSTRAINT "{constraint_name}"'
                )
        owner_scope_constraint = db.execute(
            """SELECT 1
               FROM pg_constraint c
               JOIN pg_class t ON t.oid=c.conrelid
               JOIN pg_namespace n ON n.oid=t.relnamespace
               WHERE n.nspname='public' AND t.relname='research_sources'
                 AND c.conname='research_sources_owner_scope_check'"""
        ).fetchone()
        if not owner_scope_constraint:
            db.execute(
                """ALTER TABLE research_sources
                   ADD CONSTRAINT research_sources_owner_scope_check
                   CHECK (is_private = 0 OR owner_account_id IS NOT NULL) NOT VALID"""
            )
        shared_scope_constraint = db.execute(
            """SELECT 1
               FROM pg_constraint c
               JOIN pg_class t ON t.oid=c.conrelid
               JOIN pg_namespace n ON n.oid=t.relnamespace
               WHERE n.nspname='public' AND t.relname='research_sources'
                 AND c.conname='research_sources_shared_scope_check'"""
        ).fetchone()
        if not shared_scope_constraint:
            db.execute(
                """ALTER TABLE research_sources
                   ADD CONSTRAINT research_sources_shared_scope_check
                   CHECK (is_private = 1 OR company_id IS NOT NULL) NOT VALID"""
            )
    _RESEARCH_SCOPE_SCHEMA_READY = True
    research_columns = {
        row["column_name"] for row in db.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name='research_sources'"""
        ).fetchall()
    }
    research_column_defaults = {
        "document_date": "DATE",
        "sensitivity": "TEXT NOT NULL DEFAULT 'internal'",
        "knowledge_scope": "TEXT NOT NULL DEFAULT 'private'",
        "storage_destination": "TEXT",
        "framework_ref": "TEXT",
        "ingestion_event": "TEXT",
    }
    for column_name, definition in research_column_defaults.items():
        if column_name not in research_columns:
            db.execute(
                f"ALTER TABLE research_sources ADD COLUMN {column_name} {definition}"
            )
    if "rights_expires_at" not in research_columns:
        db.execute("ALTER TABLE research_sources ADD COLUMN rights_expires_at DATE")
    owner_constraint = db.execute(
        """SELECT 1
           FROM pg_constraint c
           JOIN pg_class t ON t.oid=c.conrelid
           JOIN pg_namespace n ON n.oid=t.relnamespace
           WHERE n.nspname='public' AND t.relname='research_sources'
             AND c.conname='research_sources_owner_scope_check'"""
    ).fetchone()
    if not owner_constraint:
        db.execute(
            """ALTER TABLE research_sources
               ADD CONSTRAINT research_sources_owner_scope_check
               CHECK (is_private = 0 OR owner_account_id IS NOT NULL) NOT VALID"""
        )
    db.execute("""CREATE TABLE IF NOT EXISTS research_source_files (
        file_id TEXT PRIMARY KEY,
        research_source_id TEXT NOT NULL REFERENCES research_sources(research_source_id) ON DELETE CASCADE,
        original_name TEXT NOT NULL,
        mime_type TEXT,
        file_size BIGINT NOT NULL,
        content_hash TEXT NOT NULL UNIQUE,
        content BYTEA NOT NULL,
        extraction_status TEXT NOT NULL DEFAULT 'pending',
        extracted_text TEXT,
        created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
        CHECK (extraction_status IN ('pending','extracted','empty','unsupported','failed'))
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS research_source_chunks (
        chunk_id TEXT PRIMARY KEY,
        research_source_id TEXT NOT NULL REFERENCES research_sources(research_source_id) ON DELETE CASCADE,
        file_id TEXT REFERENCES research_source_files(file_id) ON DELETE CASCADE,
        chunk_order INTEGER NOT NULL,
        page_number INTEGER,
        content TEXT NOT NULL,
        created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
        UNIQUE(research_source_id, file_id, chunk_order)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS research_source_annotations (
        annotation_id TEXT PRIMARY KEY,
        research_source_id TEXT NOT NULL REFERENCES research_sources(research_source_id) ON DELETE CASCADE,
        file_id TEXT REFERENCES research_source_files(file_id) ON DELETE CASCADE,
        chunk_id TEXT REFERENCES research_source_chunks(chunk_id) ON DELETE CASCADE,
        annotation_order INTEGER NOT NULL,
        classification TEXT NOT NULL,
        entity_scope TEXT,
        source_locator TEXT NOT NULL,
        raw_text TEXT NOT NULL,
        sanitized_text TEXT NOT NULL,
        eligibility TEXT NOT NULL DEFAULT 'needs_review',
        verification_status TEXT NOT NULL DEFAULT 'unverified',
        created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
        CHECK (classification IN (
            'Confirmed Fact','Historical Fact','Evidence','Proposal/Assumption',
            'Inference','Forecast','Conflict/Unresolved','Operating Logic','Unclassified'
        )),
        CHECK (eligibility IN ('private_only','shared_candidate','needs_review')),
        CHECK (verification_status IN ('unverified','reviewed','rejected')),
        UNIQUE(research_source_id, annotation_order)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS research_source_relations (
        relation_id TEXT PRIMARY KEY,
        research_source_id TEXT NOT NULL REFERENCES research_sources(research_source_id) ON DELETE CASCADE,
        related_ref TEXT NOT NULL,
        relation_type TEXT NOT NULL,
        comparison_json TEXT NOT NULL,
        review_status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
        CHECK (relation_type IN ('exact_duplicate','semantic_overlap','candidate_reconciliation')),
        CHECK (review_status IN ('pending','approved','rejected')),
        UNIQUE(research_source_id, related_ref, relation_type)
    )""")
    chunk_columns = {
        row["column_name"] for row in db.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name='research_source_chunks'"""
        ).fetchall()
    }
    if "page_number" not in chunk_columns:
        db.execute("ALTER TABLE research_source_chunks ADD COLUMN page_number INTEGER")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_research_chunks_source "
        "ON research_source_chunks(research_source_id, chunk_order)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_research_sources_status "
        "ON research_sources(review_status, source_kind, created_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_research_sources_owner "
        "ON research_sources(owner_account_id, review_status, created_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_research_annotations_source "
        "ON research_source_annotations(research_source_id, annotation_order)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_research_relations_source "
        "ON research_source_relations(research_source_id, review_status)"
    )
    db.execute("""CREATE TABLE IF NOT EXISTS knowledge_versions (
        version_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL REFERENCES knowledge_sources(source_id),
        version_label TEXT NOT NULL,
        content_hash TEXT,
        published_at TEXT,
        reviewed_at TEXT,
        reviewer TEXT,
        status TEXT NOT NULL DEFAULT 'pending_review',
        created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
        UNIQUE(source_id, version_label)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS knowledge_conflicts (
        conflict_id TEXT PRIMARY KEY,
        object_id TEXT NOT NULL REFERENCES knowledge_objects(object_id) ON DELETE CASCADE,
        conflicting_object_id TEXT NOT NULL REFERENCES knowledge_objects(object_id) ON DELETE CASCADE,
        conflict_note TEXT NOT NULL,
        review_status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
        CHECK (review_status IN ('pending','resolved','dismissed')),
        CHECK (object_id <> conflicting_object_id),
        UNIQUE(object_id, conflicting_object_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS knowledge_links (
        link_id TEXT PRIMARY KEY,
        from_object_id TEXT NOT NULL REFERENCES knowledge_objects(object_id),
        to_object_id TEXT NOT NULL REFERENCES knowledge_objects(object_id),
        relation TEXT NOT NULL,
        created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
        UNIQUE(from_object_id, to_object_id, relation)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS diagnostic_runs (
        run_id TEXT PRIMARY KEY,
        case_id TEXT NOT NULL REFERENCES cases(case_id),
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        status TEXT NOT NULL,
        result TEXT NOT NULL,
        knowledge_version TEXT NOT NULL DEFAULT 'v1.0',
        created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS diagnostic_findings (
        finding_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES diagnostic_runs(run_id),
        object_id TEXT,
        finding_type TEXT NOT NULL,
        title TEXT NOT NULL,
        summary TEXT,
        confidence_level TEXT,
        evidence_ids TEXT,
        missing_evidence TEXT,
        status TEXT NOT NULL,
        created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_sector ON knowledge_objects(sector, library_type, status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_category ON knowledge_objects(category, status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_diag_runs_case ON diagnostic_runs(case_id, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_diag_findings_run ON diagnostic_findings(run_id)")
    _KNOWLEDGE_SCHEMA_READY = True


def _base_fields(**kwargs):
    fields = {
        "subsector": None,
        "symptoms": [],
        "possible_causes": [],
        "diagnostic_questions": [],
        "required_evidence": [],
        "kpis": [],
        "benchmark": None,
        "diagnostic_rule": {},
        "decision_rule": {},
        "recommendation": [],
        "sop": [],
        "case_studies": [],
        "source": "Sana V1 Diagnostic Framework",
        "source_id": "SRC-SANA-V1",
        "source_url": None,
        "evidence_quality": "إطار داخلي — يحتاج دليل حالة قبل اعتماد القرار",
        "confidence_level": "Medium",
        "knowledge_level": "L1",
        "version": "v1.0",
        "last_reviewed": "2026-09-01",
        "status": "approved",
    }
    fields.update(kwargs)
    return fields


def _seed_objects():
    """أنماط V1 قليلة ومعلنة المصدر؛ لا تحتوي Benchmarks رقمية مخترعة."""
    objects = [
        _base_fields(
            object_id="KP-B2B-ACQUISITION-CONCENTRATION",
            library_type="DIAGNOSTIC_PATTERN",
            category="Marketing & Demand",
            sector="professional_services",
            title="اعتماد اكتساب العملاء على مصدر واحد",
            problem="تعرّض الطلب الجديد للخطر عندما يعتمد على مصدر أو علاقة واحدة.",
            symptoms=["مصدر اكتساب عملاء واحد", "هشاشة مصدر العملاء"],
            possible_causes=["غياب نظام متابعة للقنوات", "عدم توثيق مصدر الطلب", "الاعتماد على الإحالات الشخصية"],
            diagnostic_questions=["كم مصدرًا جلب عميلًا مؤهلًا خلال آخر 90 يومًا؟", "ماذا يحدث إذا توقف المصدر الرئيسي شهرًا؟"],
            required_evidence=["مصدر اكتساب العملاء", "دليل عدد العملاء أو الفرص حسب المصدر"],
            kpis=["عدد العملاء المؤهلين حسب المصدر", "معدل التحول من فرصة إلى عميل", "زمن الاستجابة"],
            benchmark={"status": "not_available", "note": "لا يوجد معيار رقمي موثق داخل مكتبة سنع لهذا النمط بعد."},
            diagnostic_rule={"match_any": ["مصدر اكتساب العملاء", "هشاشة مصدر العملاء"], "required_markers": ["مصدر اكتساب العملاء"]},
            decision_rule={"when": "تظهر هشاشة المصدر مع وجود دليل على تركّز الاكتساب", "decision": "بناء نظام اكتساب قابل للقياس متعدد المصادر"},
            recommendation=["وثّق مصدر كل فرصة وعميل لمدة 30 يومًا قبل تغيير القناة.", "اختر قناة ثانية قابلة للقياس كتجربة محدودة."],
            sop=["سجل المصدر عند دخول كل فرصة.", "راجع القنوات أسبوعيًا.", "لا ترفع الإنفاق قبل ظهور تحويل قابل للقياس."],
            applicable_when="شركة خدمات B2B تعتمد على الإحالات أو مصدر غير موثق.",
            do_not_apply_when="لا يوجد أي دليل على مصدر الطلب أو كانت المشكلة مالية بحتة.",
        ),
        _base_fields(
            object_id="KP-B2B-DELIVERY-SOP",
            library_type="DIAGNOSTIC_PATTERN",
            category="Operations & Delivery",
            sector="professional_services",
            title="تسليم الخدمة غير موثق",
            problem="تباين جودة التسليم وصعوبة تفويض العمل بسبب غياب إجراء قابل للتكرار.",
            symptoms=["لا يوجد SOP", "تسليم يعتمد على الذاكرة", "تفاوت في طريقة تنفيذ الخدمة"],
            possible_causes=["لم تُقسّم الخدمة إلى خطوات", "لا يوجد مالك للإجراء", "لا توجد مراجعة جودة"],
            diagnostic_questions=["هل يستطيع شخص آخر تنفيذ الخدمة من وثيقة مكتوبة؟", "أين تحدث إعادة العمل؟"],
            required_evidence=["نسخة من إجراء أو قائمة تحقق", "مثال على إعادة عمل أو تأخير"],
            kpis=["زمن التسليم", "نسبة إعادة العمل", "نسبة اكتمال قائمة التحقق"],
            benchmark={"status": "not_available", "note": "المكتبة لا تملك معيارًا رقميًا عامًا صالحًا لكل الخدمات."},
            diagnostic_rule={"match_any": ["لا يوجد SOP", "إجراءات العمل", "تسليم يعتمد"], "required_markers": ["دليل على تفاوت أو إعادة عمل"]},
            decision_rule={"when": "يثبت الدليل أن التسليم غير قابل للتكرار", "decision": "توثيق أول SOP للخدمة الأعلى أثرًا"},
            recommendation=["اختر خدمة واحدة متكررة ووثّقها من الطلب حتى التسليم.", "اختبر الإجراء مع شخص غير منفذه الأصلي."],
            sop=["حدد المدخلات والمخرجات.", "اكتب الخطوات ونقاط الجودة.", "سجل الاستثناءات وراجعها أسبوعيًا."],
            applicable_when="خدمة مهنية متكررة يتفاوت تسليمها بين الأشخاص.",
            do_not_apply_when="لا يوجد دليل على تباين التسليم أو كانت الخدمة مخصصة بالكامل.",
        ),
        _base_fields(
            object_id="KP-EXPERT-FOUNDER-DEPENDENCY",
            library_type="DIAGNOSTIC_PATTERN",
            category="People & Management",
            sector="experts",
            title="اعتماد عمل المعرفة على المؤسس",
            problem="توقف أو تباطؤ العمل عند غياب صاحب الخبرة الأساسية.",
            symptoms=["اعتماد الشركة على المؤسس", "ما سيتعطل عند غياب المؤسس"],
            possible_causes=["المعرفة غير موثقة", "القرارات لا تملك بديلًا", "المنتج قائم على حضور المؤسس"],
            diagnostic_questions=["ما القرار الذي لا يمر دون المؤسس؟", "أي جزء من التسليم لا يستطيع الفريق تكراره؟"],
            required_evidence=["وصف مهمة أو قرار يتوقف على المؤسس", "مثال غياب أو تأخير فعلي"],
            kpis=["نسبة المهام القابلة للتفويض", "عدد القرارات اليومية التي تحتاج المؤسس", "زمن الاستبدال"],
            benchmark={"status": "not_available", "note": "لا يوجد Benchmark موثق؛ يلزم قياس خط أساس للشركة."},
            diagnostic_rule={"match_any": ["اعتماد الشركة على المؤسس", "ما سيتعطل عند غياب المؤسس"], "required_markers": ["اعتماد الشركة على المؤسس"]},
            decision_rule={"when": "يثبت اعتماد مهمة حرجة على المؤسس", "decision": "تحويل معرفة حرجة واحدة إلى أصل قابل للتفويض"},
            recommendation=["اختر مهمة حرجة واحدة وسجّل خطواتها وقرارَاتها.", "عيّن بديلًا يختبر الإجراء أسبوعيًا."],
            sop=["سجل المهمة.", "استخرج نقاط القرار.", "اختبر التنفيذ دون تدخل المؤسس.", "وثّق الفجوات."],
            applicable_when="خبير أو شركة معرفة يعتمد فيها التسليم على شخص واحد.",
            do_not_apply_when="لا يوجد دليل على مهمة حرجة أو أثر فعلي للغياب.",
        ),
        _base_fields(
            object_id="KP-EXPERT-OFFER-CLARITY",
            library_type="DIAGNOSTIC_PATTERN",
            category="Offer & Value",
            sector="experts",
            title="المعرفة موجودة لكن العرض غير قابل للشراء",
            problem="صعوبة تحويل الخبرة إلى عرض مفهوم ومحدد النتيجة والسعر.",
            symptoms=["أصل المعرفة هو الأهم", "العميل لا يعرف ما الذي يشتريه", "خدمات كثيرة غير مرتبة"],
            possible_causes=["خلط الجمهور والنتيجة", "غياب حدود نطاق الخدمة", "تسعير بلا منطق قيمة"],
            diagnostic_questions=["ما النتيجة التي يشتريها العميل؟", "ما حدود ما يدخل وما لا يدخل في العرض؟"],
            required_evidence=["قائمة عروض أو خدمات حالية", "مثال عرض سعر أو صفحة خدمة"],
            kpis=["نسبة قبول العروض", "زمن اتخاذ قرار الشراء", "هامش العرض"],
            benchmark={"status": "not_available", "note": "لا يوجد معيار رقمي موثق؛ لا يُفترض سعر أو معدل تحويل."},
            diagnostic_rule={"match_any": ["أصل المعرفة", "خدمات كثيرة", "لا يعرف ما الذي يشتريه"], "required_markers": ["قائمة عروض أو خدمات"]},
            decision_rule={"when": "تظهر خبرة بلا عرض محدد ويمكن توفير قائمة عروض", "decision": "ترتيب عرض واحد بنتيجة ونطاق وسعر قابلين للشرح"},
            recommendation=["اختر شريحة واحدة ونتيجة واحدة.", "اكتب نطاق التسليم والاستثناءات قبل تحديد السعر."],
            sop=["عرّف العميل المناسب.", "اكتب النتيجة.", "حدد المخرجات والمدة.", "اختبر الرسالة مع ثلاثة عملاء."],
            applicable_when="أعمال الخبراء والاستشارات والمنتجات المعرفية.",
            do_not_apply_when="العرض محدد بالفعل والمشكلة في التسليم أو الطلب فقط.",
        ),
        _base_fields(
            object_id="KP-ECOM-DATA-GAP",
            library_type="DIAGNOSTIC_PATTERN",
            category="Systems & Data",
            sector="ecommerce_retail",
            title="القرارات التجارية بلا لوحة مؤشرات",
            problem="إدارة التجارة الإلكترونية بالانطباع بدل أرقام قابلة للمراجعة.",
            symptoms=["لا توجد لوحة متابعة", "لا نعرف أفضل المنتجات", "قرارات التسويق بلا قياس"],
            possible_causes=["البيانات موزعة", "لا يوجد تعريف موحد للمؤشرات", "التركيز على المبيعات دون الربحية"],
            diagnostic_questions=["ما الأرقام التي تراجع أسبوعيًا؟", "هل يمكن حساب الهامش بعد الشحن والإعلانات؟"],
            required_evidence=["تقرير مبيعات أسبوعي", "بيانات تكلفة أو هامش منتج واحد على الأقل"],
            kpis=["الإيراد", "الهامش بعد التكاليف المتغيرة", "معدل التحويل", "متوسط قيمة الطلب", "تكلفة اكتساب العميل"],
            benchmark={"status": "not_available", "note": "لا تقارن سنع بأرقام سوقية قبل مصدر موثق ومتشابه."},
            diagnostic_rule={"match_any": ["لوحة متابعة", "المبيعات", "الأداء"], "required_markers": ["تقرير مبيعات أسبوعي"]},
            decision_rule={"when": "لا توجد لوحة أو تعريفات أرقام قابلة للمراجعة", "decision": "إنشاء لوحة أسبوعية من خمسة مؤشرات مع مصدر كل رقم"},
            recommendation=["ابدأ بتقرير أسبوعي لمنتج أو قناة واحدة.", "افصل الإيراد عن الهامش ولا تستخدم المتابعين بدل الربحية."],
            sop=["حدد مصدر كل رقم.", "حدّث التقرير أسبوعيًا.", "سجل تفسير التغير.", "اتخذ قرارًا واحدًا بناءً على البيانات."],
            applicable_when="متجر إلكتروني أو تجارة تجزئة تملك مبيعات قابلة للتسجيل.",
            do_not_apply_when="لا توجد بيانات معاملات يمكن التحقق منها.",
        ),
        _base_fields(
            object_id="KP-ECOM-CHANNEL-CONCENTRATION",
            library_type="DIAGNOSTIC_PATTERN",
            category="Marketing & Demand",
            sector="ecommerce_retail",
            title="تركيز الطلب في قناة تجارة واحدة",
            problem="تذبذب المبيعات عند تغير أداء قناة اكتساب واحدة.",
            symptoms=["مصدر اكتساب العملاء", "حملة واحدة", "انخفاض الطلب عند توقف القناة"],
            possible_causes=["لا توجد قائمة عملاء مملوكة", "عدم قياس القنوات", "الاعتماد على منصة خارجية"],
            diagnostic_questions=["كم نسبة الطلب من القناة الأكبر؟", "هل توجد قناة مملوكة مثل البريد أو قاعدة العملاء؟"],
            required_evidence=["تقرير الطلب حسب القناة", "سجل عملاء أو إعادة شراء"],
            kpis=["الإيراد حسب القناة", "نسبة إعادة الشراء", "تكلفة اكتساب العميل", "هامش القناة"],
            benchmark={"status": "not_available", "note": "لا يوجد Benchmark رقمي موثق داخل المكتبة لهذا النمط."},
            diagnostic_rule={"match_any": ["مصدر اكتساب العملاء", "حملة واحدة", "توقف القناة"], "required_markers": ["تقرير الطلب حسب القناة"]},
            decision_rule={"when": "يثبت تركّز الطلب في قناة واحدة", "decision": "اختبار قناة ثانية مملوكة أو قابلة للقياس دون إيقاف القناة الحالية"},
            recommendation=["قسّم الطلبات حسب القناة.", "اختبر قناة ثانية بميزانية وتجربة محدودة.", "ابنِ وسيلة تواصل مملوكة."],
            sop=["أضف مصدر القناة لكل طلب.", "راجع تكلفة القناة وهامشها.", "وثّق قرار الاستمرار أو الإيقاف."],
            applicable_when="متجر لديه طلبات ويمكنه تحديد قناة كل طلب.",
            do_not_apply_when="لا يوجد دليل على تركّز الطلب أو لا يمكن تحديد مصدره.",
        ),
    ]

    catalog_descriptions = {
        "PROBLEM": "تعريفات المشكلات التجارية القابلة للملاحظة.",
        "CAUSE": "أسباب محتملة لا تتحول إلى حقيقة دون دليل حالة.",
        "DIAGNOSTIC_QUESTION": "أسئلة تقلل عدم اليقين قبل القرار.",
        "EVIDENCE_REQUIREMENT": "الأدلة المطلوبة لإثبات أو نفي نمط.",
        "KPI": "تعريف مؤشرات، لا أرقام سوقية مفترضة.",
        "BENCHMARK": "معايير موثقة فقط؛ الحالة الحالية بلا معيار رقمي عام.",
        "DIAGNOSTIC_RULE": "قواعد ربط الأدلة بالأنماط.",
        "DECISION_RULE": "قواعد اختيار قرار مشروط بالدليل.",
        "DIAGNOSTIC_PATTERN": "أنماط تشخيص مركبة تربط المشكلة والأدلة والقواعد.",
        "OPPORTUNITY": "فرص ناتجة عن فجوة مثبتة أو قابلة للاختبار.",
        "RECOMMENDATION": "توصيات مرتبطة بقرار مدعوم.",
        "SOP": "إجراءات تشغيل قابلة للتنفيذ والمراجعة.",
        "CASE": "حالات سنع التي ستضاف بعد مراجعة وتكرار.",
        "FRAMEWORK": "أطر العمل التي تربط المعرفة برحلة Sana Scan.",
    }
    for kind, label in LIBRARY_TYPES:
        objects.append(_base_fields(
            object_id=f"LIB-{kind}",
            library_type=kind,
            category="Systems & Data",
            sector="shared",
            title=label,
            problem=catalog_descriptions[kind],
            source="Sana Knowledge Governance v1",
            source_id="SRC-SANA-GOVERNANCE",
            evidence_quality="كتالوج بنيوي — لا يمثل دليل حالة",
            confidence_level="Low",
            knowledge_level="L0",
            applicable_when="عند بناء أو مراجعة مكتبة معرفة سنع.",
            do_not_apply_when="لا ينطبق على قرار شركة بمفرده.",
        ))
    _, general_rules = _load_general_b2b_rules()
    objects.append(_base_fields(
        object_id="FRAMEWORK-GENERAL-SERVICE-B2B-RULES",
        library_type="FRAMEWORK",
        category="Business Growth",
        sector="shared",
        title=GENERAL_B2B_RULES_TITLE,
        problem="إطار عام لتحليل وتطوير المشاريع الخدمية وB2B من المشكلة والقيمة إلى الإثبات والتوسع والتعلم.",
        symptoms=[
            "البدء بالخدمة قبل فهم المشكلة",
            "النمو بلا خط أساس أو اقتصاديات",
            "تشتت المبيعات والمتابعة",
            "الاعتماد على المؤسس",
            "أتمتة عملية غير مثبتة",
        ],
        possible_causes=[
            "عرض غير واضح أو غير متخصص",
            "خلط الحقيقة بالافتراض والتوقع",
            "غياب معيار نجاح وإيقاف للتجارب",
            "عدم توثيق النتائج والمعرفة",
        ],
        diagnostic_questions=[
            "ما المشكلة المكلفة التي يدفع العميل لحلها؟",
            "ما الدليل وخط الأساس قبل أي إجراء؟",
            "ما الافتراض الأخطر الذي يجب اختباره أولًا؟",
            "ما القرار الذي سيتغير إذا تغيّر هذا المؤشر؟",
            "هل النتيجة قابلة للتكرار دون اعتماد كامل على المؤسس؟",
        ],
        required_evidence=[
            "Baseline → Action → Result",
            "اقتصاديات الفرصة: الإيراد والتكلفة والهامش والتحصيل",
            "مسار المبيعات وآخر خطوة متابعة لكل فرصة",
            "نتيجة تجربة موثقة وحد نجاح وحد إيقاف",
        ],
        kpis=[
            "الإيراد والهامش وفترة الاسترداد",
            "زمن الاستجابة والتحويل والإغلاق",
            "الاحتفاظ والتجديد والإحالة",
            "زمن التسليم وإعادة العمل",
            "المؤشر الرئيسي North Star والمؤشرات الداعمة",
        ],
        benchmark={
            "status": "not_available",
            "note": "هذا إطار إرشادي عام؛ لا يضع Benchmark رقميًا دون مصدر موثق ومتشابه.",
        },
        diagnostic_rule={
            "match_any": [],
            "required_markers": [],
            "note": "لا تُستخدم هذه القواعد وحدها لإصدار تشخيص أو قرار.",
        },
        decision_rule={
            "when": "تُستخدم بعد جمع أدلة الحالة وتصنيفها ومراجعة اقتصادياتها.",
            "decision": "اختيار سؤال أو تجربة أو قرار قابل للقياس، لا تطبيق قاعدة عامة آليًا.",
        },
        recommendation=general_rules,
        sop=[
            "صنّف كل معلومة إلى Fact أو Evidence أو Assumption أو Hypothesis أو Inference أو Forecast أو Risk أو Recommendation.",
            "ابدأ بالمشكلة والأصل الموجود، ثم عرّف النتيجة والاقتصاديات وخط الأساس.",
            "صمّم تجربة بمدة وميزانية وKPI وحد نجاح وحد إيقاف.",
            "وثّق النتيجة والتعلم قبل التوسع أو الأتمتة.",
        ],
        source=GENERAL_B2B_RULES_TITLE,
        source_id=GENERAL_B2B_RULES_SOURCE_ID,
        evidence_quality="إطار إرشادي داخلي — لا يمثل دليل حالة ولا Benchmark رقميًا",
        confidence_level="Medium",
        knowledge_level="L1",
        version="v1.0",
        last_reviewed="2026-09-01",
        applicable_when="تحليل أو تطوير مشروع خدمي أو B2B بعد جمع أدلة الحالة.",
        do_not_apply_when="إصدار قرار عن شركة بلا دليل، أو تحويل القاعدة إلى توقع أو معيار رقمي عام.",
    ))
    _, b2b_os_rules, b2b_os_systems, b2b_os_workflow = _load_b2b_service_os()
    objects.append(_base_fields(
        object_id=B2B_SERVICE_OS_FRAMEWORK_ID,
        library_type="FRAMEWORK",
        category="Operating Systems & Governance",
        sector="shared",
        title=B2B_SERVICE_OS_TITLE,
        problem="إطار تشغيلي عام يربط الاستراتيجية والفرص والعروض والمبيعات والتسليم والمالية والجودة والمعرفة في دورة واحدة.",
        diagnostic_questions=[
            "هل المشكلة والعميل والنتيجة المطلوبة محددة قبل التنفيذ؟",
            "ما خط الأساس والدليل ومصدر كل معلومة؟",
            "من المالك الواحد لكل مهمة، وما تعريف اكتمال المخرج؟",
            "هل التكلفة والهامش واقتصاديات الوحدة معروفة قبل التسعير؟",
            "هل العملية مثبتة يدويًا قبل اقتراح الأتمتة؟",
        ],
        required_evidence=[
            "بيانات الحالة وBaseline قبل أي تحسين",
            "نطاق المشروع وجدوله ومخرجاته ومعايير جودته ومخاطره",
            "سجل الفرص والإجراء التالي ومسار التسليم والتحصيل",
            "توثيق النتيجة والتعلم القابل لإعادة الاستخدام",
        ],
        benchmark={
            "status": "not_available",
            "note": "هذا إطار تشغيلي عام؛ لا يضع Benchmark رقميًا دون مصدر موثق ومتشابه.",
        },
        diagnostic_rule={
            "match_any": [],
            "required_markers": [],
            "note": "لا يُستخدم هذا الإطار وحده لإصدار تشخيص أو قرار.",
        },
        decision_rule={
            "when": "بعد جمع أدلة الحالة وتصنيفها ومراجعة اقتصادياتها واعتمادها بشريًا.",
            "decision": "اختيار سؤال أو تجربة أو إجراء موثق؛ لا تطبيق قاعدة عامة آليًا.",
        },
        recommendation=b2b_os_rules,
        sop=[
            f"دورة العمل المرجعية: {b2b_os_workflow}",
            "الأنظمة المرجعية: " + "؛ ".join(b2b_os_systems),
        ],
        source=B2B_SERVICE_OS_TITLE,
        source_id=B2B_SERVICE_OS_SOURCE_ID,
        evidence_quality="إطار تشغيلي داخلي — لا يمثل دليل حالة ولا Benchmark رقميًا",
        confidence_level="Medium",
        knowledge_level="L1",
        version=B2B_SERVICE_OS_VERSION,
        last_reviewed="2026-09-01",
        applicable_when="بناء أو تحليل أو تطوير مشروع خدمي أو B2B بعد جمع أدلة الحالة.",
        do_not_apply_when="إصدار قرار عن شركة بلا دليل، أو فتح مواد خاصة، أو اختراع KPI أو Benchmark.",
    ))
    return objects


def assess_source_eligibility(payload, as_of=None):
    """بوابة نشر قابلة للتدقيق؛ عمر الموقع مستقل تمامًا عن حداثة الوثيقة."""
    as_of = as_of or date.today()
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of[:10])

    def parsed(name):
        value = payload.get(name)
        if not value:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None

    missing = [
        key for key in (
            "publisher", "publisher_trust", "publisher_continuity",
            "author_identity", "material_type", "methodology_note",
            "rights_status", "license_note", "source_url", "jurisdiction",
            "site_age_evidence_url", "site_age_evidence_date",
            "document_date", "version_label", "retrieved_at", "reviewed_at",
            "review_due_at",
            "content_fingerprint", "trust_level",
        )
        if not str(payload.get(key) or "").strip()
    ]
    reasons = []
    site_date = parsed("site_age_evidence_date")
    if not site_date or (as_of.year - site_date.year -
                         ((as_of.month, as_of.day) < (site_date.month, site_date.day))) < 10:
        reasons.append("SITE_AGE_UNDER_10_YEARS_OR_UNPROVEN")
    review_due = parsed("review_due_at")
    reviewed_at = parsed("reviewed_at")
    if (not review_due or review_due < as_of or not reviewed_at or
            reviewed_at > as_of or (as_of - reviewed_at).days > 548):
        reasons.append("DOCUMENT_REVIEW_EXPIRED_OR_MISSING")
    document_date = parsed("document_date")
    if not document_date or document_date > as_of:
        reasons.append("DOCUMENT_DATE_INVALID_OR_MISSING")
    if str(payload.get("rights_status") or "") not in {
        "public", "licensed", "owned", "open", "quotation"
    }:
        reasons.append("RIGHTS_NOT_CLEARED")
    fingerprint = str(payload.get("content_fingerprint") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        reasons.append("FINGERPRINT_INVALID")
    if str(payload.get("trust_level") or "") not in {"authoritative", "high"}:
        reasons.append("TRUST_NOT_SUFFICIENT")
    if payload.get("unresolved_conflict"):
        reasons.append("UNRESOLVED_CONFLICT")
    reasons = list(dict.fromkeys([f"MISSING_{key.upper()}" for key in missing] + reasons))
    return {
        "eligible": not reasons,
        "status": "approved" if not reasons else "pending_review",
        "site_age_eligible": "SITE_AGE_UNDER_10_YEARS_OR_UNPROVEN" not in reasons,
        "document_fresh": "DOCUMENT_REVIEW_EXPIRED_OR_MISSING" not in reasons,
        "reasons": reasons,
        "evaluated_at": as_of.isoformat(),
    }


def _trusted_founding_batch():
    """تسعة ملخصات أصلية قصيرة؛ لا تحتوي نسخًا كاملة من المواد الأصلية."""
    common = {
        "retrieved_at": "2026-09-01",
        "reviewed_at": "2026-09-01",
        "review_due_at": "2027-09-01",
        "trust_level": "authoritative",
        "publisher_continuity": "الجهة الناشرة ما زالت الجهة الرسمية/المهنية المسؤولة عن النطاق.",
    }
    records = [
        {
            "source_id": "SRC-TRUSTED-SBA-PLAN", "domain": TRUSTED_KNOWLEDGE_DOMAINS[0],
            "title": "Write your business plan", "source_type": "official",
            "publisher": "U.S. Small Business Administration", "jurisdiction": "United States",
            "source_url": "https://www.sba.gov/business-guide/plan-your-business/write-your-business-plan",
            "author_identity": "U.S. Small Business Administration",
            "material_type": "official_guidance", "methodology_note": "إرشاد حكومي يحدد مكونات خطة العمل واستخدامها التشغيلي.",
            "publisher_trust": "جهة حكومية اتحادية مختصة بدعم الأعمال الصغيرة.",
            "rights_status": "public", "license_note": "معلومات حكومية أمريكية؛ النسبة للمصدر مع استثناء مواد الطرف الثالث.",
            "site_age_evidence_url": "https://web.archive.org/web/20150101133106/https://www.sba.gov/",
            "site_age_evidence_date": "2015-01-01", "document_date": "2025-12-18",
            "version_label": "rev-2025-12-18",
            "excerpt": "roadmap for how to structure, run, and grow.",
            "summary": "تُعامل خطة العمل كخريطة حية تربط الهيكل والتشغيل والنمو واكتساب العملاء والتوقعات المالية.",
            "method": "FRAMEWORK", "category": "Strategy & Business Models",
            "tags": {"sector": ["shared"], "business_model": ["all"], "stage": ["startup", "growth"],
                     "problem": ["غياب الخطة", "نمو غير منظم"], "goal": ["النمو", "التخطيط"],
                     "bottleneck": ["استراتيجية", "نموذج العمل"]},
        },
        {
            "source_id": "SRC-TRUSTED-GDS-USER-NEEDS", "domain": TRUSTED_KNOWLEDGE_DOMAINS[1],
            "title": "Start by learning user needs", "source_type": "official",
            "publisher": "UK Government Digital Service", "jurisdiction": "United Kingdom",
            "source_url": "https://www.gov.uk/service-manual/user-research/start-by-learning-user-needs",
            "author_identity": "Government Digital Service",
            "material_type": "service_manual", "methodology_note": "منهج بحث مستخدم تكراري ضمن دليل تصميم الخدمات الحكومية.",
            "publisher_trust": "جهة حكومية مسؤولة عن معايير الخدمات الرقمية.",
            "rights_status": "licensed", "license_note": "Open Government Licence v3.0؛ يلزم الإسناد.",
            "site_age_evidence_url": "https://web.archive.org/web/20120201142953/https://www.gov.uk/",
            "site_age_evidence_date": "2012-02-01", "document_date": "2017-03-23",
            "version_label": "updated-2017-03-23",
            "excerpt": "keep researching throughout each development phase.",
            "summary": "يبدأ تصميم تجربة العميل باحتياجات بحثية مثبتة، ثم يعاد التحقق منها خلال مراحل التطوير.",
            "method": "DIAGNOSTIC_QUESTION", "category": "Marketing & Customer Experience",
            "tags": {"sector": ["shared"], "business_model": ["service", "digital"],
                     "stage": ["startup", "growth", "mature"], "problem": ["تجربة العميل", "احتياجات العملاء"],
                     "goal": ["تحسين التجربة", "الاحتفاظ"], "bottleneck": ["الطلب", "التجربة"]},
        },
        {
            "source_id": "SRC-TRUSTED-DFE-CRM", "domain": TRUSTED_KNOWLEDGE_DOMAINS[2],
            "title": "Customer Relationship Management (CRM)", "source_type": "official",
            "publisher": "UK Department for Education", "jurisdiction": "United Kingdom",
            "source_url": "https://standards.education.gov.uk/standard/customer-relationship-management",
            "author_identity": "Department for Education Digital and Technology",
            "material_type": "technology_standard", "methodology_note": "معيار تقني مؤسسي مع مراجعة امتثال دورية.",
            "publisher_trust": "وزارة حكومية تنشر معيارًا مرقمًا.",
            "rights_status": "quotation", "license_note": "حقوق Crown محفوظة؛ اقتباس قصير منسوب وفق fair dealing فقط، دون إعادة نشر جماعي.",
            "site_age_evidence_url": "https://web.archive.org/web/20120103092627/http://education.gov.uk/",
            "site_age_evidence_date": "2012-01-03", "document_date": "2025-07-23",
            "version_label": "DDTS-760-v1.00",
            "excerpt": "Conformance … must be recorded every 6 months.",
            "summary": "يُدار CRM كقدرة متكاملة لبيانات العميل وسير العمل، بملكية واضحة وفحص امتثال متكرر.",
            "method": "FRAMEWORK", "category": "Sales & CRM",
            "tags": {"sector": ["shared"], "business_model": ["b2b", "service"],
                     "stage": ["growth", "mature"], "problem": ["CRM", "متابعة المبيعات"],
                     "goal": ["رفع التحويل", "إدارة العملاء"], "bottleneck": ["المبيعات", "المتابعة"]},
        },
        {
            "source_id": "SRC-TRUSTED-NIST-BALDRIGE", "domain": TRUSTED_KNOWLEDGE_DOMAINS[3],
            "title": "2025 Baldrige Award Criteria", "source_type": "official",
            "publisher": "National Institute of Standards and Technology", "jurisdiction": "United States",
            "source_url": "https://www.nist.gov/system/files/documents/2025/01/08/2025-Baldrige-Award-Criteria_0.pdf",
            "author_identity": "NIST Baldrige Performance Excellence Program",
            "material_type": "assessment_criteria", "methodology_note": "معايير تقييم أداء مؤسسي تراجع العمليات والنتائج.",
            "publisher_trust": "معهد قياس ومعايير حكومي اتحادي.",
            "rights_status": "public", "license_note": "NIST public information؛ إسناد المصدر وفحص العلامات.",
            "site_age_evidence_url": "https://web.archive.org/web/20150103023954/http://www.nist.gov/",
            "site_age_evidence_date": "2015-01-03", "document_date": "2025-01-08",
            "version_label": "2025-edition", "excerpt": "regular and repeated.",
            "summary": "تُحوّل العمليات إلى مسارات متكررة تُقيّم وتُحسّن وتقاس جودتها وكفاءتها ومرونتها واستمراريتها.",
            "method": "FRAMEWORK", "category": "Operations & Quality",
            "tags": {"sector": ["shared"], "business_model": ["all"], "stage": ["growth", "mature"],
                     "problem": ["الجودة", "إعادة العمل"], "goal": ["الكفاءة", "الاستمرارية"],
                     "bottleneck": ["التشغيل", "الجودة"]},
        },
        {
            "source_id": "SRC-TRUSTED-SBA-BREAK-EVEN", "domain": TRUSTED_KNOWLEDGE_DOMAINS[4],
            "title": "Calculate Your Break-Even Point", "source_type": "official",
            "publisher": "U.S. Small Business Administration", "jurisdiction": "United States",
            "source_url": "https://bec.www.sba.gov/breakevenpointcalculator",
            "author_identity": "U.S. Small Business Administration",
            "material_type": "official_calculator", "methodology_note": "حاسبة حكومية تعرض معادلة نقطة التعادل ومدخلاتها.",
            "publisher_trust": "جهة حكومية اتحادية مختصة بدعم الأعمال الصغيرة.",
            "rights_status": "public", "license_note": "معلومات حكومية أمريكية؛ تُنسب المعادلة إلى SBA.",
            "site_age_evidence_url": "https://web.archive.org/web/20150101133106/https://www.sba.gov/",
            "site_age_evidence_date": "2015-01-01", "document_date": "2026-09-01",
            "version_label": "live-reviewed-2026-09-01",
            "excerpt": "Fixed Costs ÷ (Price - Variable Costs).",
            "summary": "حجم التعادل للوحدة يساوي التكاليف الثابتة مقسومة على هامش مساهمة الوحدة؛ يلزم إدخال أرقام العميل الفعلية.",
            "method": "KPI", "category": "Finance & Unit Economics",
            "tags": {"sector": ["shared"], "business_model": ["all"], "stage": ["startup", "growth", "mature"],
                     "problem": ["الربحية", "التسعير"], "goal": ["نقطة التعادل", "الهامش"],
                     "bottleneck": ["المالية", "اقتصاديات الوحدة"]},
        },
        {
            "source_id": "SRC-TRUSTED-CIPD-LEADERSHIP", "domain": TRUSTED_KNOWLEDGE_DOMAINS[5],
            "title": "Leadership in the workplace", "source_type": "professional",
            "publisher": "Chartered Institute of Personnel and Development", "jurisdiction": "United Kingdom",
            "source_url": "https://www.cipd.org/uk/knowledge/factsheets/leadership-factsheet/",
            "author_identity": "CIPD",
            "material_type": "professional_factsheet", "methodology_note": "مراجعة مهنية تلخص الأدلة والممارسات في تطوير القيادة.",
            "publisher_trust": "هيئة مهنية معتمدة للموارد البشرية.",
            "rights_status": "quotation", "license_note": "حقوق CIPD محفوظة؛ اقتباس قصير منسوب وفق fair dealing فقط.",
            "site_age_evidence_url": "https://rdap.publicinterestregistry.org/rdap/domain/cipd.org",
            "site_age_evidence_date": "2000-01-11", "document_date": "2025-07-21",
            "version_label": "factsheet-2025-07-21",
            "excerpt": "different approaches to leadership development.",
            "summary": "تُطوّر القيادة عمدًا وبأساليب متعددة، مع فصل القدرة القيادية عن الإدارة التشغيلية اليومية.",
            "method": "FRAMEWORK", "category": "Leadership & Human Resources",
            "tags": {"sector": ["shared"], "business_model": ["all"], "stage": ["growth", "mature"],
                     "problem": ["القيادة", "اعتماد المؤسس"], "goal": ["التفويض", "تطوير الفريق"],
                     "bottleneck": ["القيادة", "الموارد البشرية"]},
        },
        {
            "source_id": "SRC-TRUSTED-NIST-CSF", "domain": TRUSTED_KNOWLEDGE_DOMAINS[6],
            "title": "The NIST Cybersecurity Framework (CSF) 2.0", "source_type": "official",
            "publisher": "National Institute of Standards and Technology", "jurisdiction": "United States",
            "source_url": "https://www.nist.gov/publications/nist-cybersecurity-framework-csf-20",
            "author_identity": "National Institute of Standards and Technology",
            "material_type": "risk_framework", "methodology_note": "إطار نتائج لإدارة مخاطر الأمن السيبراني على مستوى المؤسسة.",
            "publisher_trust": "معهد قياس ومعايير حكومي اتحادي.",
            "rights_status": "public", "license_note": "NIST public information؛ إسناد المصدر.",
            "site_age_evidence_url": "https://web.archive.org/web/20150103023954/http://www.nist.gov/",
            "site_age_evidence_date": "2015-01-03", "document_date": "2024-02-26",
            "version_label": "NIST-CSWP-29-CSF-2.0",
            "excerpt": "Govern, Identify, Protect, Detect, Respond, and Recover.",
            "summary": "تبدأ إدارة الأمن السيبراني بالحوكمة كمخاطر مؤسسة، ثم تنظيم النتائج عبر التعرف والحماية والكشف والاستجابة والتعافي.",
            "method": "FRAMEWORK", "category": "Information Systems, Data, Security & Governance",
            "tags": {"sector": ["shared", "tech"], "business_model": ["digital", "all"],
                     "stage": ["growth", "mature"], "problem": ["الأمن", "حوكمة البيانات"],
                     "goal": ["المرونة", "إدارة المخاطر"], "bottleneck": ["البيانات", "الأمن"]},
        },
        {
            "source_id": "SRC-TRUSTED-NIST-AI-RMF", "domain": TRUSTED_KNOWLEDGE_DOMAINS[7],
            "title": "Artificial Intelligence Risk Management Framework 1.0", "source_type": "official",
            "publisher": "National Institute of Standards and Technology", "jurisdiction": "United States",
            "source_url": "https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10",
            "author_identity": "National Institute of Standards and Technology",
            "material_type": "risk_framework", "methodology_note": "إطار طوعي قائم على وظائف مترابطة لإدارة مخاطر الذكاء الاصطناعي.",
            "publisher_trust": "معهد قياس ومعايير حكومي اتحادي.",
            "rights_status": "public", "license_note": "NIST public information؛ إسناد المصدر.",
            "site_age_evidence_url": "https://web.archive.org/web/20150103023954/http://www.nist.gov/",
            "site_age_evidence_date": "2015-01-03", "document_date": "2023-01-26",
            "version_label": "NIST-AI-100-1-v1.0",
            "excerpt": "govern, map, measure, and manage.",
            "summary": "تدار مخاطر الذكاء الاصطناعي عبر الحوكمة ورسم السياق والقياس والإدارة، مع امتداد الحوكمة عبر دورة الحياة.",
            "method": "FRAMEWORK", "category": "Digital Transformation & AI",
            "tags": {"sector": ["shared", "tech"], "business_model": ["digital", "all"],
                     "stage": ["startup", "growth", "mature"], "problem": ["الذكاء الاصطناعي", "التحول الرقمي"],
                     "goal": ["الأتمتة الآمنة", "التحول"], "bottleneck": ["التقنية", "الذكاء الاصطناعي"]},
        },
        {
            "source_id": "SRC-TRUSTED-ORANGE-BOOK", "domain": TRUSTED_KNOWLEDGE_DOMAINS[8],
            "title": "The Orange Book: Management of Risk", "source_type": "official",
            "publisher": "HM Treasury and Government Finance Function", "jurisdiction": "United Kingdom",
            "source_url": "https://www.gov.uk/government/publications/orange-book/the-orange-book-management-of-risk-principles-and-concepts",
            "author_identity": "HM Treasury / Government Finance Function",
            "material_type": "risk_management_guidance", "methodology_note": "مبادئ حكومية لدمج إدارة المخاطر بالأهداف والقرار والضمان.",
            "publisher_trust": "وزارة خزانة ووظيفة مالية حكومية.",
            "rights_status": "licensed", "license_note": "Open Government Licence v3.0؛ يلزم الإسناد.",
            "site_age_evidence_url": "https://web.archive.org/web/20120201142953/https://www.gov.uk/",
            "site_age_evidence_date": "2012-02-01", "document_date": "2023-05-01",
            "version_label": "May-2023",
            "excerpt": "risk management enhances strategic planning and prioritisation.",
            "summary": "تُدمج إدارة المخاطر في الأهداف والاستراتيجية وترتيب الأولويات والقرار والضمان والمرونة التنظيمية.",
            "method": "FRAMEWORK", "category": "Project & Risk Management",
            "tags": {"sector": ["shared"], "business_model": ["all"], "stage": ["startup", "growth", "mature"],
                     "problem": ["مخاطر المشروع", "الأولويات"], "goal": ["إدارة المخاطر", "نجاح المشروع"],
                     "bottleneck": ["المشروع", "المخاطر"]},
        },
    ]
    for record in records:
        record.update(common)
        record["content_fingerprint"] = hashlib.sha256(
            (record["source_url"] + "\n" + record["version_label"] + "\n" +
             record["excerpt"] + "\n" + record["summary"]).encode("utf-8")
        ).hexdigest()
    return records


def _seed_trusted_founding_batch(db):
    for record in _trusted_founding_batch():
        eligibility = assess_source_eligibility(record, "2026-09-01")
        existing_version = db.execute(
            """SELECT content_hash FROM knowledge_versions
               WHERE source_id=? AND version_label=?""",
            (record["source_id"], record["version_label"]),
        ).fetchone()
        if (
            existing_version
            and existing_version["content_hash"]
            and existing_version["content_hash"] != record["content_fingerprint"]
        ):
            raise ValueError(
                "TRUSTED_SOURCE_VERSION_HASH_MISMATCH:"
                f"{record['source_id']}:{record['version_label']}"
            )
        source_values = (
            record["source_id"], record["title"], record["source_type"],
            record["publisher"], record["jurisdiction"], record["source_url"],
            record["rights_status"], record["license_note"], record["retrieved_at"],
            record["review_due_at"], eligibility["status"], record["author_identity"],
            record["material_type"], record["methodology_note"], record["publisher_trust"],
            record["publisher_continuity"], record["site_age_evidence_url"],
            record["site_age_evidence_date"], record["document_date"],
            record["version_label"], record["content_fingerprint"], record["trust_level"],
            record["reviewed_at"], _json(eligibility),
        )
        duplicate = db.execute(
            """SELECT source_id FROM knowledge_sources
               WHERE content_fingerprint=? AND source_id<>?""",
            (record["content_fingerprint"], record["source_id"]),
        ).fetchone()
        if duplicate:
            raise ValueError(
                f"DUPLICATE_TRUSTED_SOURCE:{record['source_id']}:{duplicate['source_id']}"
            )
        db.execute(
            """INSERT INTO knowledge_sources
               (source_id,title,source_type,publisher,jurisdiction,source_url,
                rights_status,license_note,retrieved_at,review_due_at,status,
                author_identity,material_type,methodology_note,publisher_trust,
                publisher_continuity,site_age_evidence_url,site_age_evidence_date,
                document_date,version_label,content_fingerprint,trust_level,
                reviewed_at,eligibility_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (source_id) DO UPDATE SET
                title=EXCLUDED.title, source_url=EXCLUDED.source_url,
                rights_status=EXCLUDED.rights_status, license_note=EXCLUDED.license_note,
                review_due_at=EXCLUDED.review_due_at, status=EXCLUDED.status,
                author_identity=EXCLUDED.author_identity, material_type=EXCLUDED.material_type,
                methodology_note=EXCLUDED.methodology_note,
                publisher_trust=EXCLUDED.publisher_trust,
                publisher_continuity=EXCLUDED.publisher_continuity,
                site_age_evidence_url=EXCLUDED.site_age_evidence_url,
                site_age_evidence_date=EXCLUDED.site_age_evidence_date,
                document_date=EXCLUDED.document_date, version_label=EXCLUDED.version_label,
                content_fingerprint=EXCLUDED.content_fingerprint,
                trust_level=EXCLUDED.trust_level, reviewed_at=EXCLUDED.reviewed_at,
                eligibility_json=EXCLUDED.eligibility_json,
                updated_at=to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')""",
            source_values,
        )
        db.execute(
            """INSERT INTO knowledge_versions
               (version_id,source_id,version_label,content_hash,published_at,
                reviewed_at,reviewer,status)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT (source_id,version_label) DO NOTHING""",
            (f"{record['source_id']}:{record['version_label']}", record["source_id"],
             record["version_label"], record["content_fingerprint"],
             record["document_date"], record["reviewed_at"], "Sana source review",
             eligibility["status"]),
        )
        tags = record["tags"]
        db.execute(
            """INSERT INTO knowledge_objects
               (object_id,library_type,category,sector,title,problem,diagnostic_questions,
                required_evidence,benchmark,diagnostic_rule,decision_rule,recommendation,
                source,source_id,source_url,evidence_quality,confidence_level,
                applicable_when,do_not_apply_when,knowledge_level,version,last_reviewed,
                status,source_excerpt,original_summary,domains,sector_tags,
                business_model_tags,stage_tags,problem_tags,goal_tags,bottleneck_tags)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (object_id) DO UPDATE SET
                title=EXCLUDED.title, problem=EXCLUDED.problem,
                source_url=EXCLUDED.source_url, version=EXCLUDED.version,
                last_reviewed=EXCLUDED.last_reviewed, status=EXCLUDED.status,
                source_excerpt=EXCLUDED.source_excerpt,
                original_summary=EXCLUDED.original_summary,
                domains=EXCLUDED.domains, sector_tags=EXCLUDED.sector_tags,
                business_model_tags=EXCLUDED.business_model_tags,
                stage_tags=EXCLUDED.stage_tags, problem_tags=EXCLUDED.problem_tags,
                goal_tags=EXCLUDED.goal_tags, bottleneck_tags=EXCLUDED.bottleneck_tags,
                updated_at=to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')""",
            (
                "TRUSTED-" + record["domain"].upper().replace("_", "-"),
                record["method"], record["category"], "shared", record["title"],
                record["summary"],
                _json(["ما دليل الحالة الذي يثبت أن هذا المرجع ينطبق على الشركة؟"]),
                _json(["Fact أو Evidence خاص بالشركة قبل التطبيق"]),
                _json({"status": "not_available",
                       "note": "المصدر المرجعي لا يصبح Benchmark للعميل دون تحقق التشابه."}),
                _json({"match_any": [], "required_markers": [],
                       "note": "مرجع عام غير تشخيصي."}),
                _json({"when": "بعد دليل العميل والمراجعة البشرية",
                       "decision": "لا ينشئ هذا المرجع قرارًا وحده."}),
                _json([]), record["publisher"], record["source_id"], record["source_url"],
                "مصدر مؤسسي/رسمي اجتاز بوابة العمر والثقة والحقوق والحداثة",
                "High", "عند تطابق السياق ووجود دليل عميل.",
                "لا يطبق كحقيقة عن العميل أو كسبب لتغيير Sana Scan.",
                "L0", record["version_label"], record["reviewed_at"],
                eligibility["status"], record["excerpt"], record["summary"],
                _json([record["domain"]]), _json(tags["sector"]),
                _json(tags["business_model"]), _json(tags["stage"]),
                _json(tags["problem"]), _json(tags["goal"]),
                _json(tags["bottleneck"]),
            ),
        )


def seed_knowledge(db):
    ensure_schema(db)
    objects = _seed_objects()
    _seed_trusted_founding_batch(db)
    b2b_os_markdown, _, _, _ = _load_b2b_service_os()
    b2b_os_hash = hashlib.sha256(b2b_os_markdown.encode("utf-8")).hexdigest()
    existing_b2b_version = db.execute(
        """SELECT content_hash FROM knowledge_versions
           WHERE source_id=? AND version_label=?""",
        (B2B_SERVICE_OS_SOURCE_ID, B2B_SERVICE_OS_VERSION),
    ).fetchone()
    if existing_b2b_version and existing_b2b_version["content_hash"] != b2b_os_hash:
        raise ValueError(
            "بصمة إطار تشغيل الخدمات وB2B لا تطابق الإصدار المسجل "
            f"{B2B_SERVICE_OS_VERSION}; حدّث رقم الإصدار قبل تعديل المصدر canonical"
        )
    sources = [
        ("SRC-SANA-V1", "Sana V1 Diagnostic Framework", "internal", "Sana", "shared",
         None, "owned", "محتوى داخلي مملوك لسنع؛ يراجع قبل استخدامه خارجيًا.",
         "2026-09-01", "2027-03-01", "approved"),
        ("SRC-SANA-GOVERNANCE", "Sana Knowledge Governance v1", "internal", "Sana", "shared",
         None, "owned", "سجل بنيوي داخلي مملوك لسنع.", "2026-09-01", "2027-03-01", "approved"),
        (GENERAL_B2B_RULES_SOURCE_ID, GENERAL_B2B_RULES_TITLE, "internal", "Sana", "shared",
         None, "owned", "مرجع إرشادي داخلي مملوك لسنع؛ لا يُستخدم كدليل حالة أو Benchmark رقمي.",
         "2026-09-01", "2027-03-01", "approved"),
        (B2B_SERVICE_OS_SOURCE_ID, B2B_SERVICE_OS_TITLE, "internal", "Sana", "shared",
         None, "owned", "إطار تشغيلي داخلي مملوك لسنع؛ لا يُستخدم كدليل حالة أو Benchmark رقمي.",
         "2026-09-01", "2027-03-01", "approved"),
    ]
    for source in sources:
        db.execute(
            """INSERT INTO knowledge_sources
               (source_id, title, source_type, publisher, jurisdiction, source_url,
                rights_status, license_note, retrieved_at, review_due_at, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (source_id) DO UPDATE SET
                 title=EXCLUDED.title, rights_status=EXCLUDED.rights_status,
                 license_note=EXCLUDED.license_note, status=EXCLUDED.status,
                 updated_at=to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')""",
            source
        )
    for source_id in ("SRC-SANA-V1", "SRC-SANA-GOVERNANCE", GENERAL_B2B_RULES_SOURCE_ID):
        db.execute(
            """INSERT INTO knowledge_versions
               (version_id, source_id, version_label, published_at, reviewed_at, reviewer, status)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT (source_id, version_label) DO NOTHING""",
            (f"{source_id}-v1.0", source_id, "v1.0", "2026-09-01",
             "2026-09-01", "Sana", "approved")
        )
    db.execute(
        """INSERT INTO knowledge_versions
           (version_id, source_id, version_label, content_hash, published_at,
            reviewed_at, reviewer, status)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT (source_id, version_label) DO NOTHING""",
        (
            f"{B2B_SERVICE_OS_SOURCE_ID}-{B2B_SERVICE_OS_VERSION}",
            B2B_SERVICE_OS_SOURCE_ID,
            B2B_SERVICE_OS_VERSION,
            b2b_os_hash,
            "2026-09-01",
            "2026-09-01",
            "Sana",
            "approved",
        ),
    )

    general_markdown, _ = _load_general_b2b_rules()
    methodology_table = db.execute(
        """SELECT 1 FROM information_schema.tables
           WHERE table_schema='public' AND table_name='methodology_docs'"""
    ).fetchone()
    if methodology_table:
        db.execute(
            """INSERT INTO methodology_docs
               (doc_id, slug, title, subtitle, content, doc_type, version, bos_id, sector_tags)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT (slug) DO UPDATE SET
                 title=EXCLUDED.title, subtitle=EXCLUDED.subtitle,
                 content=EXCLUDED.content, doc_type=EXCLUDED.doc_type,
                 version=EXCLUDED.version, sector_tags=EXCLUDED.sector_tags""",
            (
                "DOC-GENERAL-SERVICE-B2B",
                GENERAL_B2B_RULES_SLUG,
                GENERAL_B2B_RULES_TITLE,
                "مرجع عام قابل لإعادة الاستخدام — مستقل عن أي شركة أو عميل",
                general_markdown,
                "framework",
                "v1.0",
                None,
                None,
            ),
        )
        db.execute(
            """INSERT INTO methodology_docs
               (doc_id, slug, title, subtitle, content, doc_type, version, bos_id, sector_tags)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT (slug) DO UPDATE SET
                 title=EXCLUDED.title, subtitle=EXCLUDED.subtitle,
                 content=EXCLUDED.content, doc_type=EXCLUDED.doc_type,
                 version=EXCLUDED.version, bos_id=EXCLUDED.bos_id,
                 sector_tags=EXCLUDED.sector_tags""",
            (
                "DOC-B2B-SERVICE-OS",
                B2B_SERVICE_OS_SLUG,
                B2B_SERVICE_OS_TITLE,
                "إطار تشغيلي عام — مستقل عن أي شركة أو عميل",
                b2b_os_markdown,
                "framework",
                B2B_SERVICE_OS_VERSION,
                B2B_SERVICE_OS_ID,
                None,
            ),
        )
    columns = [
        "object_id", "library_type", "category", "sector", "subsector", "title",
        "problem", "symptoms", "possible_causes", "diagnostic_questions",
        "required_evidence", "kpis", "benchmark", "diagnostic_rule", "decision_rule",
        "recommendation", "sop", "case_studies", "source", "source_id", "source_url",
        "evidence_quality", "confidence_level", "applicable_when", "do_not_apply_when",
        "knowledge_level", "version", "last_reviewed", "status"
    ]
    values_sql = ",".join("?" for _ in columns)
    for obj in objects:
        values = [obj.get(col) for col in columns]
        for col in ("symptoms", "possible_causes", "diagnostic_questions", "required_evidence",
                    "kpis", "benchmark", "diagnostic_rule", "decision_rule",
                    "recommendation", "sop", "case_studies"):
            values[columns.index(col)] = _json(obj.get(col))
        db.execute(
            f"""INSERT INTO knowledge_objects ({','.join(columns)})
                VALUES ({values_sql})
                ON CONFLICT (object_id) DO UPDATE SET
                library_type=EXCLUDED.library_type, category=EXCLUDED.category,
                sector=EXCLUDED.sector, title=EXCLUDED.title, problem=EXCLUDED.problem,
                symptoms=EXCLUDED.symptoms, possible_causes=EXCLUDED.possible_causes,
                diagnostic_questions=EXCLUDED.diagnostic_questions,
                required_evidence=EXCLUDED.required_evidence, kpis=EXCLUDED.kpis,
                benchmark=EXCLUDED.benchmark, diagnostic_rule=EXCLUDED.diagnostic_rule,
                decision_rule=EXCLUDED.decision_rule, recommendation=EXCLUDED.recommendation,
                sop=EXCLUDED.sop, source=EXCLUDED.source,
                source_id=EXCLUDED.source_id,
                evidence_quality=EXCLUDED.evidence_quality,
                confidence_level=EXCLUDED.confidence_level,
                applicable_when=EXCLUDED.applicable_when,
                do_not_apply_when=EXCLUDED.do_not_apply_when,
                knowledge_level=EXCLUDED.knowledge_level, version=EXCLUDED.version,
                last_reviewed=EXCLUDED.last_reviewed, status=EXCLUDED.status,
                updated_at=to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')""",
            values
        )

    for obj in objects:
        if obj["library_type"] != "DIAGNOSTIC_PATTERN":
            continue
        for kind, _ in LIBRARY_TYPES:
            target = f"LIB-{kind}"
            db.execute(
                """INSERT INTO knowledge_links (link_id, from_object_id, to_object_id, relation)
                   VALUES (?,?,?,?)
                   ON CONFLICT (from_object_id, to_object_id, relation) DO NOTHING""",
                (f"LINK-{obj['object_id']}-{kind}", obj["object_id"], target, kind.lower())
            )
    db.execute(
        """INSERT INTO knowledge_links
           (link_id, from_object_id, to_object_id, relation)
           VALUES (?,?,?,?)
           ON CONFLICT (from_object_id, to_object_id, relation) DO NOTHING""",
        (
            f"LINK-{B2B_SERVICE_OS_FRAMEWORK_ID}-GENERAL-SERVICE-B2B-RULES",
            B2B_SERVICE_OS_FRAMEWORK_ID,
            "FRAMEWORK-GENERAL-SERVICE-B2B-RULES",
            "complements",
        ),
    )


def list_sources(db):
    ensure_schema(db)
    rows = db.execute(
        """SELECT source_id, title, source_type, publisher, jurisdiction, source_url,
                  rights_status, license_note, retrieved_at, review_due_at, status,
                  author_identity, material_type, methodology_note, publisher_trust,
                  publisher_continuity, site_age_evidence_url, site_age_evidence_date,
                  document_date, version_label, content_fingerprint, trust_level,
                  reviewed_at, eligibility_json
           FROM knowledge_sources ORDER BY title"""
    ).fetchall()
    return [dict(row) for row in rows]


def list_research_sources(
    db, query="", review_status=None, source_kind=None, limit=100,
    owner_account_id=None,
):
    """المصادر الخاصة فقط؛ لا تُخلط مع المعرفة المعتمدة."""
    ensure_schema(db)
    owner_account_id = str(owner_account_id or "").strip()
    if not owner_account_id:
        return []
    limit = max(1, min(int(limit or 100), 200))
    conditions = ["is_private=1", "owner_account_id=?"]
    params = [owner_account_id]
    if review_status in {"inbox", "approved", "rejected", "archived"}:
        conditions.append("review_status=?")
        params.append(review_status)
    if source_kind in RESEARCH_SOURCE_KINDS:
        conditions.append("source_kind=?")
        params.append(source_kind)
    query = (query or "").strip()
    if query:
        conditions.append(
            "LOWER(COALESCE(title,'') || ' ' || COALESCE(author,'') || ' ' || "
            "COALESCE(summary,'') || ' ' || COALESCE(notes,'') || ' ' || COALESCE(tags,'')) LIKE ?"
        )
        params.append(f"%{query.lower()}%")
    rows = db.execute(
        f"""SELECT rs.research_source_id, rs.title, rs.source_kind, rs.origin, rs.company_id,
                   rs.version_label, rs.drive_file_id, rs.document_date,
                   rs.sensitivity, rs.knowledge_scope, rs.storage_destination,
                   rs.framework_ref, rs.ingestion_event,
                   source_url, author, publisher, publication_year, language,
                   jurisdiction, summary, notes, tags, rights_status, rights_expires_at,
                   review_status,
                   rs.is_private, rs.owner_account_id, rs.created_at, rs.updated_at,
                   rf.file_id AS uploaded_file_id, rf.original_name AS uploaded_file_name,
                   rf.mime_type AS uploaded_mime_type, rf.file_size AS uploaded_file_size,
                   rf.content_hash, rf.extraction_status,
                   CASE WHEN rs.rights_expires_at IS NOT NULL
                          AND rs.rights_expires_at < CURRENT_DATE
                        THEN 1 ELSE 0 END AS rights_expired,
                    (SELECT COUNT(*) FROM research_source_chunks c
                     WHERE c.research_source_id=rs.research_source_id) AS chunk_count,
                    (SELECT COUNT(*) FROM research_source_annotations a
                     WHERE a.research_source_id=rs.research_source_id) AS annotation_count,
                    (SELECT COUNT(*) FROM research_source_relations rel
                     WHERE rel.research_source_id=rs.research_source_id) AS relation_count
            FROM research_sources rs
            LEFT JOIN research_source_files rf
              ON rf.research_source_id=rs.research_source_id
            WHERE {' AND '.join(conditions)}
            ORDER BY rs.created_at DESC, rs.research_source_id DESC
            LIMIT {limit}""",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _extract_uploaded_text(filename, mime_type, content):
    """استخراج محافظ؛ لا يساوي الاستخراج اعتمادًا معرفيًا."""
    name = str(filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    text_exts = {"txt", "md", "markdown", "csv", "json", "html", "htm", "xml", "yaml", "yml"}
    if ext in text_exts or str(mime_type or "").startswith("text/"):
        text = content.decode("utf-8-sig", errors="replace").strip()
        return text, "extracted" if text else "empty"
    if ext == "docx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                xml = archive.read("word/document.xml")
            root = ET.fromstring(xml)
            blocks = []
            body = next(
                (node for node in root.iter()
                 if node.tag.rsplit("}", 1)[-1] == "body"),
                root,
            )
            for node in list(body):
                kind = node.tag.rsplit("}", 1)[-1]
                if kind == "p":
                    paragraph = "".join(
                        child.text or "" for child in node.iter()
                        if child.tag.rsplit("}", 1)[-1] == "t"
                    ).strip()
                    if paragraph:
                        blocks.append(paragraph)
                elif kind == "tbl":
                    rows = []
                    for row in node.iter():
                        if row.tag.rsplit("}", 1)[-1] != "tr":
                            continue
                        cells = []
                        for cell in list(row):
                            if cell.tag.rsplit("}", 1)[-1] != "tc":
                                continue
                            value = " ".join(
                                child.text or "" for child in cell.iter()
                                if child.tag.rsplit("}", 1)[-1] == "t"
                            ).strip()
                            cells.append(value)
                        if cells:
                            rows.append(" | ".join(cells))
                    if rows:
                        blocks.append("\n".join(rows))
            text = "\n\n".join(blocks).strip()
            return text, "extracted" if text else "empty"
        except Exception:
            return "", "failed"
    if ext == "xlsx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                pieces = []
                for name in archive.namelist():
                    if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                        root = ET.fromstring(archive.read(name))
                        pieces.extend(
                            node.text or "" for node in root.iter()
                            if node.tag.rsplit("}", 1)[-1] in {"t", "v"}
                        )
            text = "\n".join(x.strip() for x in pieces if x.strip()).strip()
            return text, "extracted" if text else "empty"
        except Exception:
            return "", "failed"
    if ext == "pdf" or mime_type == "application/pdf":
        return "", "unsupported"
    return "", "unsupported"


def sanitize_curated_text(value):
    """ينزع بيانات الاتصال والروابط من النسخة المرشحة للعرض أو النشر."""
    text = str(value or "")
    text = re.sub(
        r"(?<!\d)(?:\+?966[\s-]?)?05\d[\s-]?\d{3}[\s-]?\d{4}(?!\d)",
        "[بيانات اتصال محجوبة]",
        text,
    )
    text = re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[بريد إلكتروني محجوب]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"https?://[^\s|)]+",
        "[رابط خارجي يحتاج تحققًا]",
        text,
        flags=re.IGNORECASE,
    )
    return text


def classify_curated_text(value, source_profile=None):
    """تصنيف محافظ لعبارات الوثائق المنقحة دون تحويلها إلى حقائق تشغيلية."""
    text = str(value or "").strip()
    if not text:
        return "Unclassified"
    if any(token in text for token in ("[تعارض/غير محسوم]", "[غير محسوم]", "غير محسوم")):
        return "Conflict/Unresolved"
    if any(token in text for token in ("[توقع", "توقع مستقبلي", "توقع")):
        return "Forecast"
    if any(token in text for token in ("[استنتاج", "استنتاج تحليلي", "استنتاج من")):
        return "Inference"
    if any(token in text for token in ("[اقتراح", "[افتراض", "اقتراح تاريخي", "خطة لا يثبت", "ميزانية مقترحة")):
        return "Proposal/Assumption"
    if any(token in text for token in ("[دليل", "دليل سلوكي", "Evidence")):
        return "Evidence"
    if any(token in text for token in ("[حقيقة تاريخية]", "حقيقة تاريخية")):
        return "Historical Fact"
    if any(token in text for token in ("[حقيقة مؤكدة]", "[حقيقة من التشخيص]", "حقيقة مؤكدة")):
        return "Confirmed Fact"
    if source_profile == "b2b_operating_logic":
        return "Operating Logic"
    return "Unclassified"


def _chunk_for_offset(chunks, offset):
    for chunk in chunks:
        if chunk["start"] <= offset < chunk["end"]:
            return chunk
    return chunks[-1] if chunks else None


def annotate_curated_source(db, research_source_id, source_profile, entity_scope):
    """يسجل التصريحات المصنفة فقط، مع موضع ومقطع ونسخة منقحة قابلة للمراجعة."""
    source = db.execute(
        """SELECT rf.file_id, rf.extracted_text, rs.knowledge_scope
           FROM research_sources rs
           JOIN research_source_files rf ON rf.research_source_id=rs.research_source_id
           WHERE rs.research_source_id=?""",
        (research_source_id,),
    ).fetchone()
    if not source or not source["extracted_text"]:
        return 0
    chunk_rows = db.execute(
        """SELECT chunk_id, chunk_order, content
           FROM research_source_chunks WHERE research_source_id=?
           ORDER BY chunk_order""",
        (research_source_id,),
    ).fetchall()
    chunks = []
    cursor = 0
    for row in chunk_rows:
        start = str(source["extracted_text"]).find(row["content"], cursor)
        start = start if start >= 0 else cursor
        chunks.append({
            "chunk_id": row["chunk_id"],
            "chunk_order": row["chunk_order"],
            "start": start,
            "end": start + len(row["content"]),
        })
        cursor = start + len(row["content"])

    text = str(source["extracted_text"])
    annotations = []
    offset = 0
    for line_number, line in enumerate(text.splitlines(), 1):
        raw = line.strip()
        line_start = text.find(line, offset)
        offset = max(line_start + len(line), offset)
        classification = classify_curated_text(raw, source_profile)
        is_section = bool(re.match(r"^(?:\d+[\.)]\s+|#+\s+|\[TABLE\])", raw))
        if classification == "Unclassified" and not (
            source_profile == "b2b_operating_logic" and is_section
        ):
            continue
        annotations.append({
            "classification": classification,
            "line_number": line_number,
            "raw_text": raw,
            "chunk": _chunk_for_offset(chunks, line_start),
        })

    for order, item in enumerate(annotations):
        chunk = item["chunk"] or {}
        eligibility = (
            "shared_candidate"
            if source_profile == "b2b_operating_logic"
            else "private_only"
        )
        db.execute(
            """INSERT INTO research_source_annotations
               (annotation_id, research_source_id, file_id, chunk_id, annotation_order,
                classification, entity_scope, source_locator, raw_text, sanitized_text,
                eligibility, verification_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (research_source_id, annotation_order) DO NOTHING""",
            (
                f"RA-{research_source_id}-{order:04d}",
                research_source_id,
                source["file_id"],
                chunk.get("chunk_id"),
                order,
                item["classification"],
                entity_scope,
                f"line:{item['line_number']};chunk:{int(chunk.get('chunk_order', 0)) + 1}"
                if chunk else f"line:{item['line_number']}",
                item["raw_text"],
                sanitize_curated_text(item["raw_text"]),
                eligibility,
                "unverified",
            ),
        )
    return len(annotations)


def _comparison_tokens(text):
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9\u0600-\u06ff]{3,}", str(text or ""))
        if token.lower() not in {"التي", "الذي", "هذا", "هذه", "على", "منها", "وهو"}
    }


def compare_candidate_to_canonical(candidate_text, candidate_file_hash=None):
    """يقارن النص المرشح بالبصمة وتداخل المفردات، دون دمج أو اعتماد آلي."""
    canonical_files = (
        (
            GENERAL_B2B_RULES_SOURCE_ID,
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "GENERAL_SERVICE_B2B_GROWTH_RULES.md"),
        ),
        (
            B2B_SERVICE_OS_SOURCE_ID,
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "B2B_SERVICE_OPERATING_SYSTEM.md"),
        ),
    )
    candidate_hash = hashlib.sha256(str(candidate_text or "").encode("utf-8")).hexdigest()
    candidate_tokens = _comparison_tokens(candidate_text)
    comparisons = []
    for related_ref, path in canonical_files:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as canonical_file:
                canonical = canonical_file.read()
        else:
            canonical = ""
        canonical_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        overlap = sorted(candidate_tokens & _comparison_tokens(canonical))
        comparisons.append({
            "related_ref": related_ref,
            "candidate_hash": candidate_hash,
            "candidate_file_hash": candidate_file_hash,
            "canonical_hash": canonical_hash,
            "hash_match": candidate_hash == canonical_hash,
            "overlap_terms": overlap[:80],
            "overlap_count": len(overlap),
            "relation": "exact_duplicate" if candidate_hash == canonical_hash else "semantic_overlap",
            "differences": [
                "المصدر المرفوع مستخرج من حالة أراك؛ المرجع canonical عام.",
                "لا يُنقل تاريخ أراك أو أسعاره أو أمثلته إلى المرجع المشترك.",
            ],
        })
    return comparisons


def record_candidate_reconciliation(
    db, research_source_id, candidate_text, candidate_file_hash=None
):
    """يحفظ مقارنة المصدر المرشح مع المرجعين القائمين مع إبقاء المراجعة معلقة."""
    comparisons = compare_candidate_to_canonical(candidate_text, candidate_file_hash)
    for comparison in comparisons:
        db.execute(
            """INSERT INTO research_source_relations
               (relation_id, research_source_id, related_ref, relation_type,
                comparison_json, review_status)
               VALUES (?,?,?,?,?,'pending')
               ON CONFLICT (research_source_id, related_ref, relation_type)
               DO UPDATE SET comparison_json=EXCLUDED.comparison_json""",
            (
                f"RREL-{research_source_id}-{comparison['related_ref']}",
                research_source_id,
                comparison["related_ref"],
                "candidate_reconciliation",
                _json(comparison),
            ),
        )
    return comparisons


def create_uploaded_research_source(
    db, *, filename, mime_type, content, payload, owner_account_id=None,
    company_id=None,
    max_bytes=50 * 1024 * 1024
):
    """يحفظ الملف ويستخرج النص المتاح ويجزئه للبحث، مع إبقاء الاعتماد منفصلًا."""
    ensure_schema(db)
    owner_account_id = str(owner_account_id or "").strip()
    if not owner_account_id:
        return {"success": False, "error": "PRIVATE_OWNER_REQUIRED"}
    filename = str(filename or "").strip()
    title = str(payload.get("title") or filename).strip()
    source_kind = str(payload.get("source_kind") or "file").strip()
    if not filename or not title or source_kind not in RESEARCH_SOURCE_KINDS:
        return {"success": False, "error": "UPLOAD_FIELDS_REQUIRED"}
    if not content:
        return {"success": False, "error": "EMPTY_FILE"}
    if len(content) > max_bytes:
        return {"success": False, "error": "FILE_TOO_LARGE"}
    content_hash = hashlib.sha256(content).hexdigest()
    duplicate = db.execute(
        """SELECT rf.research_source_id, rs.owner_account_id
           FROM research_source_files rf
           JOIN research_sources rs ON rs.research_source_id=rf.research_source_id
           WHERE rf.content_hash=?""",
        (content_hash,),
    ).fetchone()
    if duplicate:
        result = {
            "success": False,
            "error": "DUPLICATE_FILE",
        }
        # لا نكشف معرّف مادة يملكها حساب آخر؛ نعيده فقط في إعادة تشغيل
        # الإدخال من نفس النطاق الإداري.
        if duplicate["owner_account_id"] == owner_account_id:
            result["research_source_id"] = duplicate["research_source_id"]
        return result
    year_value = str(payload.get("publication_year") or "").strip()
    rights_status = str(payload.get("rights_status") or "pending").strip()
    if rights_status not in {"pending", "owned", "licensed", "public", "restricted"}:
        return {"success": False, "error": "RIGHTS_STATUS_INVALID"}
    rights_expires_at, expiry_error = _parse_rights_expiry(
        payload.get("rights_expires_at")
    )
    if expiry_error:
        return {"success": False, "error": expiry_error}
    source_id = "RS-" + uuid.uuid4().hex[:10].upper()
    file_id = "RF-" + uuid.uuid4().hex[:10].upper()
    extracted_text, extraction_status = _extract_uploaded_text(filename, mime_type, content)
    document_date = str(payload.get("document_date") or "").strip() or None
    if document_date:
        try:
            document_date = date.fromisoformat(document_date).isoformat()
        except ValueError:
            return {"success": False, "error": "DOCUMENT_DATE_INVALID"}
    knowledge_scope = str(payload.get("knowledge_scope") or "private").strip()
    if knowledge_scope not in {"private", "private_case", "shared_candidate", "shared"}:
        return {"success": False, "error": "KNOWLEDGE_SCOPE_INVALID"}
    is_private = 1 if knowledge_scope != "shared" else 0
    if not is_private and not company_id:
        return {"success": False, "error": "SHARED_COMPANY_REQUIRED"}
    db.execute(
        """INSERT INTO research_sources
           (research_source_id, title, source_kind, origin, company_id, author, publisher,
             publication_year, language, jurisdiction, summary, notes, tags,
             rights_status, rights_expires_at, version_label, document_date,
             sensitivity, knowledge_scope, storage_destination, framework_ref,
             ingestion_event, review_status, is_private, owner_account_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'v1.0',?,?,?,?,?,?,'inbox',?,?)""",
        (
            source_id, title, source_kind, "upload", company_id,
            str(payload.get("author") or "").strip() or None,
            str(payload.get("publisher") or "").strip() or None,
            int(year_value) if year_value.isdigit() else None,
            str(payload.get("language") or "").strip() or None,
            str(payload.get("jurisdiction") or "").strip() or None,
            str(payload.get("summary") or "").strip() or None,
            str(payload.get("notes") or "").strip() or None,
            str(payload.get("tags") or "").strip() or None,
            rights_status,
            rights_expires_at,
            document_date,
            str(payload.get("sensitivity") or "internal").strip() or "internal",
            knowledge_scope,
            str(payload.get("storage_destination") or "private research inbox").strip(),
            str(payload.get("framework_ref") or "").strip() or None,
            str(payload.get("ingestion_event") or "curated_upload").strip(),
            is_private,
            owner_account_id,
        ),
    )
    inserted_file = db.execute(
        """INSERT INTO research_source_files
           (file_id, research_source_id, original_name, mime_type, file_size,
            content_hash, content, extraction_status, extracted_text)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT (content_hash) DO NOTHING
           RETURNING file_id""",
        (file_id, source_id, filename, mime_type or None, len(content),
         content_hash, content, extraction_status, extracted_text or None),
    ).fetchone()
    if not inserted_file:
        # سباق إدخال آمن: أُنشئ سجل المصدر أولًا لكن بصمة الملف سبقتنا.
        # نحذف السجل المؤقت داخل نفس المعاملة ثم نعيد نتيجة التكرار دون
        # إنشاء مقاطع أو كائنات يتيمة.
        db.execute("DELETE FROM research_sources WHERE research_source_id=?", (source_id,))
        duplicate = db.execute(
            """SELECT rf.research_source_id, rs.owner_account_id
               FROM research_source_files rf
               JOIN research_sources rs ON rs.research_source_id=rf.research_source_id
               WHERE rf.content_hash=?""",
            (content_hash,),
        ).fetchone()
        result = {"success": False, "error": "DUPLICATE_FILE"}
        if duplicate and duplicate["owner_account_id"] == owner_account_id:
            result["research_source_id"] = duplicate["research_source_id"]
        return result
    chunk_count = 0
    if extracted_text:
        chunk_size = 2200
        for index, start in enumerate(range(0, len(extracted_text), chunk_size)):
            chunk = extracted_text[start:start + chunk_size].strip()
            if chunk:
                db.execute(
                    """INSERT INTO research_source_chunks
                       (chunk_id, research_source_id, file_id, chunk_order, content)
                       VALUES (?,?,?,?,?)""",
                    ("RC-" + uuid.uuid4().hex[:10].upper(), source_id, file_id, index, chunk),
                )
                chunk_count += 1
    source_profile = str(payload.get("source_profile") or "").strip()
    annotation_count = 0
    if source_profile:
        annotation_count = annotate_curated_source(
            db,
            source_id,
            source_profile,
            str(payload.get("entity_scope") or "").strip() or None,
        )
        if source_profile == "b2b_operating_logic" and extracted_text:
            record_candidate_reconciliation(db, source_id, extracted_text, content_hash)
    db.commit()
    return {
        "success": True,
        "research_source_id": source_id,
        "file_id": file_id,
        "file_name": filename,
        "content_hash": content_hash,
        "extraction_status": extraction_status,
        "chunk_count": chunk_count,
        "annotation_count": annotation_count,
        "review_status": "inbox",
    }


def create_research_source(db, payload, owner_account_id=None, company_id=None):
    """يسجل مادة في صندوق خاص وتبدأ دائمًا بقيد المراجعة."""
    ensure_schema(db)
    owner_account_id = str(owner_account_id or "").strip()
    if not owner_account_id:
        return {"success": False, "error": "PRIVATE_OWNER_REQUIRED"}
    title = str(payload.get("title") or "").strip()
    source_kind = str(payload.get("source_kind") or "").strip()
    if not title or source_kind not in RESEARCH_SOURCE_KINDS:
        return {"success": False, "error": "RESEARCH_SOURCE_FIELDS_REQUIRED"}
    origin = str(payload.get("origin") or "manual").strip()
    if origin not in {"manual", "drive", "url"}:
        return {"success": False, "error": "RESEARCH_SOURCE_ORIGIN_INVALID"}
    source_url = str(payload.get("source_url") or "").strip() or None
    drive_file_id = str(payload.get("drive_file_id") or "").strip() or None
    if origin == "drive" and not drive_file_id and source_url:
        drive_match = re.search(r"[-\w]{20,}", source_url)
        drive_file_id = drive_match.group(0) if drive_match else None
    if origin == "drive" and not (drive_file_id or source_url):
        return {"success": False, "error": "DRIVE_REFERENCE_REQUIRED"}
    rights_status = str(payload.get("rights_status") or "pending").strip()
    if rights_status not in {"pending", "owned", "licensed", "public", "restricted"}:
        return {"success": False, "error": "RIGHTS_STATUS_INVALID"}
    rights_expires_at, expiry_error = _parse_rights_expiry(
        payload.get("rights_expires_at")
    )
    if expiry_error:
        return {"success": False, "error": expiry_error}
    year = payload.get("publication_year")
    if year in ("", None):
        year = None
    else:
        try:
            year = int(year)
            if year < 1000 or year > 2100:
                raise ValueError
        except (TypeError, ValueError):
            return {"success": False, "error": "PUBLICATION_YEAR_INVALID"}
    source_id = "RS-" + uuid.uuid4().hex[:10].upper()
    db.execute(
        """INSERT INTO research_sources
           (research_source_id, title, source_kind, origin, company_id,
            drive_file_id, source_url,
            author, publisher, publication_year, language, jurisdiction, summary,
            notes, tags, rights_status, rights_expires_at, review_status,
            is_private, owner_account_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
        (
            source_id, title, source_kind, origin, company_id,
            drive_file_id, source_url,
            str(payload.get("author") or "").strip() or None,
            str(payload.get("publisher") or "").strip() or None,
            year,
            str(payload.get("language") or "").strip() or None,
            str(payload.get("jurisdiction") or "").strip() or None,
            str(payload.get("summary") or "").strip() or None,
            str(payload.get("notes") or "").strip() or None,
            str(payload.get("tags") or "").strip() or None,
            rights_status,
            rights_expires_at,
            "inbox",
            owner_account_id,
        ),
    )
    db.commit()
    return {"success": True, "research_source_id": source_id, "review_status": "inbox"}


def _parse_rights_expiry(value):
    """يتحقق من تاريخ الحقوق بصيغة ISO ويعيده مناسبًا لـ PostgreSQL DATE."""
    raw = str(value or "").strip()
    if not raw:
        return None, None
    try:
        return date.fromisoformat(raw).isoformat(), None
    except ValueError:
        return None, "RIGHTS_EXPIRY_INVALID"


def get_research_source_file(db, research_source_id, owner_account_id):
    """يقرأ ملفًا مرفوعًا بعد تصفية الملكية؛ لا يكشف وجود مادة لمالك آخر."""
    ensure_schema(db)
    owner_account_id = str(owner_account_id or "").strip()
    if not owner_account_id:
        return None, "RESEARCH_SOURCE_NOT_FOUND"
    row = db.execute(
        """SELECT rs.research_source_id, rs.rights_status, rs.rights_expires_at,
                  rf.original_name, rf.mime_type, rf.content
           FROM research_sources rs
           JOIN research_source_files rf
             ON rf.research_source_id=rs.research_source_id
           WHERE rs.research_source_id=? AND rs.is_private=1
             AND rs.owner_account_id=?""",
        (research_source_id, owner_account_id),
    ).fetchone()
    if not row:
        return None, "RESEARCH_SOURCE_NOT_FOUND"
    if row["rights_status"] == "restricted":
        return None, "RIGHTS_RESTRICTED"
    if row["rights_expires_at"] and row["rights_expires_at"] < date.today():
        return None, "RIGHTS_EXPIRED"
    return dict(row), None


def update_research_source_status(
    db, research_source_id, review_status, owner_account_id=None
):
    ensure_schema(db)
    owner_account_id = str(owner_account_id or "").strip()
    if not owner_account_id:
        return {"success": False, "error": "RESEARCH_SOURCE_NOT_FOUND"}
    if review_status not in {"inbox", "approved", "rejected", "archived"}:
        return {"success": False, "error": "REVIEW_STATUS_INVALID"}
    row = db.execute(
        """SELECT research_source_id, rights_status, rights_expires_at
           FROM research_sources
           WHERE research_source_id=? AND is_private=1 AND owner_account_id=?""",
        (research_source_id, owner_account_id),
    ).fetchone()
    if not row:
        return {"success": False, "error": "RESEARCH_SOURCE_NOT_FOUND"}
    if review_status == "approved":
        if row["rights_status"] == "restricted":
            return {"success": False, "error": "RIGHTS_RESTRICTED"}
        if row["rights_expires_at"] and row["rights_expires_at"] < date.today():
            return {"success": False, "error": "RIGHTS_EXPIRED"}
    db.execute(
        """UPDATE research_sources SET review_status=?,
           updated_at=to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')
           WHERE research_source_id=? AND is_private=1 AND owner_account_id=?""",
        (review_status, research_source_id, owner_account_id),
    )
    db.commit()
    return {"success": True, "research_source_id": research_source_id, "review_status": review_status}


def create_source(db, payload):
    """إنشاء مصدر جديد؛ لا يضيف معرفة حتى تُعرّف حقوق الاستخدام بوضوح."""
    ensure_schema(db)
    payload = dict(payload or {})
    if payload.get("source_url") and not payload.get("retrieved_at"):
        payload["retrieved_at"] = date.today().isoformat()
    required = ("source_id", "title", "source_type", "rights_status")
    if any(not str(payload.get(key) or "").strip() for key in required):
        return {"success": False, "error": "SOURCE_FIELDS_REQUIRED"}
    source_id = str(payload["source_id"]).strip()
    eligibility = assess_source_eligibility(payload)
    requested_status = str(payload.get("status") or "pending_review")
    status = "approved" if requested_status == "approved" and eligibility["eligible"] else "pending_review"
    fingerprint = str(payload.get("content_fingerprint") or "").strip() or None
    if fingerprint:
        duplicate = db.execute(
            "SELECT source_id FROM knowledge_sources WHERE content_fingerprint=?",
            (fingerprint,),
        ).fetchone()
        if duplicate:
            return {"success": False, "error": "DUPLICATE_SOURCE",
                    "duplicate_source_id": duplicate["source_id"]}
    db.execute(
        """INSERT INTO knowledge_sources
           (source_id, title, source_type, publisher, jurisdiction, source_url,
             rights_status, license_note, retrieved_at, review_due_at, status,
             author_identity,material_type,methodology_note,publisher_trust,
             publisher_continuity,site_age_evidence_url,site_age_evidence_date,
             document_date,version_label,content_fingerprint,trust_level,
             reviewed_at,eligibility_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (source_id, str(payload["title"]).strip(), str(payload["source_type"]).strip(),
         payload.get("publisher"), payload.get("jurisdiction"), payload.get("source_url"),
         str(payload["rights_status"]).strip(), payload.get("license_note"),
         payload.get("retrieved_at"), payload.get("review_due_at"),
         status, payload.get("author_identity"), payload.get("material_type"),
         payload.get("methodology_note"), payload.get("publisher_trust"),
         payload.get("publisher_continuity"), payload.get("site_age_evidence_url"),
         payload.get("site_age_evidence_date"), payload.get("document_date"),
         payload.get("version_label"), fingerprint, payload.get("trust_level"),
         payload.get("reviewed_at"), _json(eligibility))
    )
    db.commit()
    return {"success": True, "source_id": source_id, "status": status,
            "eligibility": eligibility}


def review_source(db, source_id, decision, reviewer):
    """اعتماد/رفض صريح؛ يعيد تشغيل كل البوابات ولا يثق بالحالة المطلوبة."""
    ensure_schema(db)
    if decision not in {"approved", "rejected", "pending_review"}:
        return {"success": False, "error": "REVIEW_DECISION_INVALID"}
    row = db.execute(
        "SELECT * FROM knowledge_sources WHERE source_id=?", (source_id,)
    ).fetchone()
    if not row:
        return {"success": False, "error": "SOURCE_NOT_FOUND"}
    payload = dict(row)
    if decision == "approved":
        payload["reviewed_at"] = date.today().isoformat()
    eligibility = assess_source_eligibility(payload)
    pending_conflict = db.execute(
        """SELECT 1 FROM knowledge_conflicts kc
           JOIN knowledge_objects left_object
             ON left_object.object_id=kc.object_id
           JOIN knowledge_objects right_object
             ON right_object.object_id=kc.conflicting_object_id
           WHERE (left_object.source_id=? OR right_object.source_id=?)
             AND kc.review_status='pending' LIMIT 1""",
        (source_id, source_id),
    ).fetchone()
    if pending_conflict:
        eligibility["eligible"] = False
        eligibility["status"] = "pending_review"
        eligibility["reasons"] = list(dict.fromkeys(
            eligibility["reasons"] + ["UNRESOLVED_CONFLICT"]
        ))
    if decision == "approved" and not eligibility["eligible"]:
        db.execute(
            """UPDATE knowledge_sources SET status='pending_review',
               eligibility_json=?,
               updated_at=to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')
               WHERE source_id=?""",
            (_json(eligibility), source_id),
        )
        db.commit()
        return {"success": False, "error": "SOURCE_NOT_ELIGIBLE",
                "eligibility": eligibility}
    if decision == "approved":
        existing_version = db.execute(
            """SELECT content_hash,status FROM knowledge_versions
               WHERE source_id=? AND version_label=?""",
            (source_id, row["version_label"]),
        ).fetchone()
        if (
            existing_version
            and existing_version["content_hash"]
            and existing_version["content_hash"] != row["content_fingerprint"]
        ):
            return {
                "success": False,
                "error": "VERSION_HASH_CONFLICT",
                "message": "تغيّر المحتوى يتطلب رقم إصدار جديدًا.",
            }
        if existing_version and existing_version["status"] != "approved":
            return {
                "success": False,
                "error": "VERSION_LABEL_ALREADY_RECORDED",
                "message": "هذا الإصدار مسجل بحالة سابقة؛ أنشئ رقم إصدار جديدًا للاعتماد.",
            }
    status = decision
    db.execute(
        """UPDATE knowledge_sources SET status=?, reviewed_at=CURRENT_DATE,
           eligibility_json=?,
           updated_at=to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')
           WHERE source_id=?""",
        (status, _json(eligibility), source_id),
    )
    db.execute(
        "UPDATE knowledge_objects SET status=? WHERE source_id=?",
        ("approved" if status == "approved" else "pending_review", source_id),
    )
    if status == "approved":
        db.execute(
            """INSERT INTO knowledge_versions
               (version_id,source_id,version_label,content_hash,published_at,
                reviewed_at,reviewer,status)
               VALUES (?,?,?,?,?,CURRENT_DATE,?,'approved')
               ON CONFLICT (source_id,version_label) DO NOTHING""",
            (f"{source_id}:{row['version_label']}", source_id,
             row["version_label"], row["content_fingerprint"],
             row["document_date"], reviewer),
        )
    db.commit()
    return {"success": True, "source_id": source_id, "status": status,
            "eligibility": eligibility}


def create_drive_knowledge_objects(
    db, *, excerpt, source, objects, published_text, provenance_link_id
):
    """إنشاء كائنات صغيرة من مقتطف معتمد، لا من وثيقة Drive كاملة."""
    ensure_schema(db)
    allowed_types = {key for key, _ in LIBRARY_TYPES}
    if not isinstance(objects, list) or not objects or len(objects) > 12:
        raise ValueError("KNOWLEDGE_OBJECTS_REQUIRED")
    created = []
    for item in objects:
        if not isinstance(item, dict):
            raise ValueError("KNOWLEDGE_OBJECT_INVALID")
        library_type = str(item.get("library_type") or "").strip().upper()
        category = str(item.get("category") or "").strip()
        title = str(item.get("title") or "").strip()
        statement = str(item.get("statement") or "").strip()
        applicable_when = str(item.get("applicable_when") or "").strip()
        do_not_apply_when = str(item.get("do_not_apply_when") or "").strip()
        if library_type not in allowed_types:
            raise ValueError("KNOWLEDGE_LIBRARY_TYPE_INVALID")
        if not category or not title or not statement:
            raise ValueError("KNOWLEDGE_OBJECT_FIELDS_REQUIRED")
        if len(title) > 180 or len(category) > 120 or len(statement) > 3000:
            raise ValueError("KNOWLEDGE_OBJECT_TOO_LARGE")
        if len(applicable_when) > 1000 or len(do_not_apply_when) > 1000:
            raise ValueError("KNOWLEDGE_OBJECT_SCOPE_TOO_LARGE")
        object_id = "KO-DRIVE-" + uuid.uuid4().hex[:12].upper()
        db.execute(
            """INSERT INTO knowledge_objects
               (object_id,library_type,category,title,problem,source,source_url,
                evidence_quality,confidence_level,applicable_when,do_not_apply_when,
                knowledge_level,version,last_reviewed,status,source_excerpt,
                original_summary,source_file_id,provenance_link_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_DATE,'approved',?,?,?,?)""",
            (
                object_id, library_type, category, title, statement,
                "مقتطف Drive معتمد ومنقح", None,
                "مقتطف Drive اعتمد بمراجعة بشرية",
                str(item.get("confidence_level") or "medium").strip(),
                applicable_when or None, do_not_apply_when or None,
                "L0", "drive-" + str(excerpt["content_hash"])[:12],
                published_text, statement, None, None,
            ),
        )
        created.append({
            "object_id": object_id,
            "title": title,
            "library_type": library_type,
            "category": category,
        })
    return created


def sector_for_company(company):
    raw = " ".join(str(company.get(k) or "") for k in ("sector", "sector_other")).lower()
    if any(token in raw for token in ("ecommerce", "e-commerce", "retail", "تجزئة", "تجارة")):
        return "ecommerce_retail"
    if any(token in raw for token in ("expert", "knowledge", "خبير", "معرفة")):
        return "experts"
    if any(token in raw for token in ("professional", "consult", "legal", "construction", "health", "education",
                                      "استشار", "قانون", "مقاول", "صحة", "تعليم")):
        return "professional_services"
    return None


def _object_dict(row):
    data = dict(row)
    for col in ("symptoms", "possible_causes", "diagnostic_questions", "required_evidence",
                "kpis", "benchmark", "diagnostic_rule", "decision_rule",
                "recommendation", "sop", "case_studies"):
        data[col] = _loads(data.get(col), {} if col in ("benchmark", "diagnostic_rule", "decision_rule") else [])
    return data


def _citation(
    *, source_id, source, version=None, chunk_id=None, chunk_order=None,
    page_number=None, quote=None, fingerprint=None
):
    """يبني عقد اقتباس ثابتًا يمكن للمستشار تتبعه إلى المصدر الأصلي."""
    chunk_fingerprint = (
        hashlib.sha256(str(quote).encode("utf-8")).hexdigest()
        if quote
        else None
    )
    return {
        "source_id": source_id,
        "source": source,
        "version": version,
        "chunk_id": chunk_id,
        "chunk": (int(chunk_order) + 1) if chunk_order is not None else None,
        "page": page_number,
        "quote": quote,
        "fingerprint": fingerprint,
        "chunk_fingerprint": chunk_fingerprint,
    }


def _research_chunk_results(db, query, company_id, library_type, limit):
    """يعيد المقاطع المنشورة للشركة فقط؛ لا يقرأ صندوق المواد الخاصة."""
    company_id = str(company_id or "").strip()
    if not company_id or (library_type and library_type != "RESEARCH_SOURCE_CHUNK"):
        return []
    conditions = [
        "rs.is_private=0",
        "rs.company_id=?",
        "rs.review_status='approved'",
        "rs.rights_status <> 'restricted'",
        "(rs.rights_expires_at IS NULL OR rs.rights_expires_at >= CURRENT_DATE)",
    ]
    params = [company_id]
    if query:
        conditions.append(
            "LOWER(COALESCE(rs.title,'') || ' ' || COALESCE(rs.author,'') || ' ' || "
            "COALESCE(rs.publisher,'') || ' ' || COALESCE(rs.summary,'') || ' ' || "
            "COALESCE(rs.tags,'') || ' ' || COALESCE(c.content,'')) LIKE ?"
        )
        params.append(f"%{query.lower()}%")
    rows = db.execute(
        f"""SELECT c.chunk_id, c.research_source_id, c.file_id, c.chunk_order,
                   c.page_number, c.content, rs.title AS source_title,
                   rs.version_label, rs.source_url, rs.updated_at,
                   rf.original_name, rf.content_hash
            FROM research_source_chunks c
            JOIN research_sources rs
              ON rs.research_source_id=c.research_source_id
            LEFT JOIN research_source_files rf
              ON rf.file_id=c.file_id
            WHERE {' AND '.join(conditions)}
            ORDER BY rs.title, c.chunk_order, c.chunk_id
            LIMIT {limit}""",
        params,
    ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        quote = item["content"]
        chunk_fingerprint = hashlib.sha256(
            str(quote).encode("utf-8")
        ).hexdigest()
        fingerprint = item["content_hash"] or chunk_fingerprint
        citation = _citation(
            source_id=item["research_source_id"],
            source=item["source_title"],
            version=item["version_label"] or "v1.0",
            chunk_id=item["chunk_id"],
            chunk_order=item["chunk_order"],
            page_number=item["page_number"],
            quote=quote,
            fingerprint=fingerprint,
        )
        results.append({
            "object_id": None,
            "result_type": "research_source_chunk",
            "library_type": "RESEARCH_SOURCE_CHUNK",
            "category": "Research",
            "sector": "company",
            "subsector": None,
            "title": item["source_title"],
            "problem": quote,
            "source": item["source_title"],
            "source_id": item["research_source_id"],
            "source_url": item["source_url"],
            "source_version": item["version_label"] or "v1.0",
            "evidence_quality": "مقطع من مصدر مشترك معتمد",
            "confidence_level": "Medium",
            "knowledge_level": "L2",
            "version": item["version_label"] or "v1.0",
            "last_reviewed": item["updated_at"],
            "quote": quote,
            "chunk_id": item["chunk_id"],
            "chunk_order": item["chunk_order"],
            "chunk": citation["chunk"],
            "page_number": item["page_number"],
            "page": item["page_number"],
            "fingerprint": fingerprint,
            "file_fingerprint": item["content_hash"],
            "chunk_fingerprint": chunk_fingerprint,
            "citations": [citation],
        })
    return results


def _tag_matches(raw_tags, value):
    if not value:
        return False
    tags = [str(item).lower() for item in _loads(raw_tags, [])]
    value = str(value).lower()
    return any(
        tag not in {"all", "shared"} and (tag in value or value in tag)
        for tag in tags
    )


def search_knowledge(
    db, query="", sector=None, library_type=None, limit=20, company_id=None,
    context=None, include_conflicted=False,
):
    ensure_schema(db)
    query = (query or "").strip()
    limit = max(1, min(int(limit or 20), 50))
    conditions = [
        "ko.status='approved'",
        "(ko.sector='shared' OR ko.sector IS NULL OR ko.sector=?)",
        "(ko.source_id IS NULL OR (ks.status='approved' AND kv.status='approved'))",
        "(ks.source_id IS NULL OR ks.source_type='internal' OR ks.eligibility_json LIKE '%%\"eligible\": true%%')",
        "(ks.source_id IS NULL OR ks.rights_status IN ('public','licensed','owned','open','quotation'))",
        "(ks.source_id IS NULL OR ks.review_due_at IS NULL OR ks.review_due_at >= CURRENT_DATE::text)",
    ]
    if not include_conflicted:
        conditions.append(
            """NOT EXISTS (
                 SELECT 1 FROM knowledge_conflicts kc
                 WHERE kc.review_status='pending'
                   AND (kc.object_id=ko.object_id
                     OR kc.conflicting_object_id=ko.object_id)
               )"""
        )
    params = [sector or "shared"]
    if library_type == "RESEARCH_SOURCE_CHUNK":
        conditions.append("1=0")
    if library_type and library_type != "RESEARCH_SOURCE_CHUNK":
        conditions.append("ko.library_type=?")
        params.append(library_type)
    if query:
        conditions.append(
            "LOWER(COALESCE(ko.title,'') || ' ' || COALESCE(ko.problem,'') || ' ' || "
            "COALESCE(ko.symptoms,'') || ' ' || COALESCE(ko.possible_causes,'') || ' ' || "
            "COALESCE(ko.diagnostic_questions,'') || ' ' || COALESCE(ko.kpis,'') || ' ' || "
            "COALESCE(ko.recommendation,'') || ' ' || COALESCE(ko.sop,'')) LIKE ?"
        )
        params.append(f"%{query.lower()}%")
    rows = db.execute(
        f"""SELECT ko.object_id, ko.library_type, ko.category, ko.sector, ko.subsector,
                   ko.title, ko.problem, ko.source, ko.source_id, ko.source_url,
                   ko.evidence_quality, ko.confidence_level, ko.knowledge_level,
                    ko.version, ko.last_reviewed, ko.source_excerpt, ko.original_summary,
                    ko.domains, ko.sector_tags, ko.business_model_tags, ko.stage_tags,
                    ko.problem_tags, ko.goal_tags, ko.bottleneck_tags,
                    ks.title AS source_title, ks.publisher, ks.rights_status,
                    ks.review_due_at, ks.document_date, ks.site_age_evidence_url,
                    ks.site_age_evidence_date, ks.trust_level,
                    kv.version_label AS source_version, kv.content_hash AS fingerprint,
                    EXISTS (
                      SELECT 1 FROM knowledge_conflicts pending_conflict
                      WHERE pending_conflict.review_status='pending'
                        AND (pending_conflict.object_id=ko.object_id
                          OR pending_conflict.conflicting_object_id=ko.object_id)
                    ) AS has_pending_conflict
            FROM knowledge_objects ko
            LEFT JOIN knowledge_sources ks ON ks.source_id=ko.source_id
            LEFT JOIN knowledge_versions kv
              ON kv.source_id=ko.source_id AND kv.version_label=ko.version
            WHERE {' AND '.join(conditions)}
             ORDER BY ko.category, ko.title
             LIMIT {max(limit, 200)}""",
        params
    ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        source = item["source_title"] or item["source"]
        citation = _citation(
            source_id=item["source_id"],
            source=source,
            version=item["source_version"] or item["version"],
            quote=item.get("source_excerpt"),
            fingerprint=item["fingerprint"],
        )
        item.update({
            "result_type": "knowledge_object",
            "source": source,
            "source_version": item["source_version"] or item["version"],
            "citations": [citation],
            "quote": item.get("source_excerpt"),
            "chunk_id": None,
            "chunk_order": None,
            "page_number": None,
            "chunk_fingerprint": None,
        })
        item["reference_only"] = True
        item["required_client_evidence"] = (
            "Fact أو Evidence خاص بالشركة يثبت قابلية التطبيق قبل أي تشخيص أو قرار."
        )
        score = 0
        reasons = []
        context = context or {}
        match_fields = (
            ("sector_tags", sector or context.get("sector"), "تطابق القطاع", 4),
            ("business_model_tags", context.get("business_model"), "تطابق نموذج العمل", 3),
            ("stage_tags", context.get("stage"), "تطابق مرحلة الشركة", 3),
            ("problem_tags", context.get("problem"), "تطابق المشكلة", 5),
            ("goal_tags", context.get("goal"), "تطابق الهدف", 4),
            ("bottleneck_tags", context.get("bottleneck"), "تطابق الاختناق", 5),
        )
        for field, value, reason, weight in match_fields:
            if _tag_matches(item.get(field), value):
                score += weight
                reasons.append(reason)
        if query:
            score += 6
            reasons.append("تطابق عبارة البحث")
        item["relevance_score"] = score
        item["match_reasons"] = reasons or ["مرجع مشترك عام"]
        results.append(item)

    if not library_type or library_type == "RESEARCH_SOURCE_CHUNK":
        results.extend(
            _research_chunk_results(db, query.lower(), company_id, library_type, limit)
        )
    results.sort(
        key=lambda item: (
            int(item.get("relevance_score") or 0),
            str(item.get("last_reviewed") or ""),
            str(item.get("title") or ""),
        ),
        reverse=True,
    )
    return results[:limit]


def contextual_reference_knowledge(db, company, case=None, bottleneck=None, limit=5):
    """مرجع مستقل لا يدخل جدول Evidence ولا نتيجة/درجة Scan."""
    company = dict(company or {})
    case = dict(case or {})
    context = {
        "sector": sector_for_company(company) or company.get("sector"),
        "business_model": case.get("case_type"),
        "stage": company.get("stage"),
        "problem": case.get("declared_problem") or case.get("real_question"),
        "goal": company.get("main_goal") or company.get("success_criteria"),
        "bottleneck": (
            bottleneck.get("title") if isinstance(bottleneck, dict) else bottleneck
        ),
    }
    candidates = search_knowledge(
        db, sector=sector_for_company(company), company_id=company.get("company_id"),
        context=context, limit=50, include_conflicted=True,
    )
    references = [
        item for item in candidates
        if int(item.get("relevance_score") or 0) > 0
        and not item.get("has_pending_conflict")
    ][:limit]
    conflicts = []
    object_ids = [
        item["object_id"] for item in candidates
        if item.get("object_id")
        and int(item.get("relevance_score") or 0) > 0
    ]
    if object_ids:
        placeholders = ",".join("?" for _ in object_ids)
        rows = db.execute(
            f"""SELECT kc.conflict_id,kc.object_id,kc.conflicting_object_id,
                       kc.conflict_note,
                       left_object.title AS object_title,
                       left_object.version AS object_version,
                       left_source.document_date AS object_document_date,
                       right_object.title AS conflicting_title,
                       right_object.version AS conflicting_version,
                       right_source.document_date AS conflicting_document_date
                FROM knowledge_conflicts kc
                JOIN knowledge_objects left_object
                  ON left_object.object_id=kc.object_id
                LEFT JOIN knowledge_sources left_source
                  ON left_source.source_id=left_object.source_id
                JOIN knowledge_objects right_object
                  ON right_object.object_id=kc.conflicting_object_id
                LEFT JOIN knowledge_sources right_source
                  ON right_source.source_id=right_object.source_id
                WHERE kc.review_status='pending'
                  AND (kc.object_id IN ({placeholders})
                    OR kc.conflicting_object_id IN ({placeholders}))""",
            object_ids + object_ids,
        ).fetchall()
        conflicts = [dict(row) for row in rows]
    return {
        "reference_only": True,
        "does_not_affect_scan": True,
        "context": context,
        "references": references,
        "knowledge_gap": None if references else (
            "لا توجد مادة موثوقة ومعتمدة ومطابقة؛ يلزم بحث ومراجعة بشرية."
        ),
        "conflicts": conflicts,
        "human_review_required": True,
    }


def library_summary(db, sector=None):
    ensure_schema(db)
    params = [sector or "shared"]
    rows = db.execute(
        """SELECT library_type, COUNT(*) AS count
           FROM knowledge_objects
           WHERE status='approved' AND (sector='shared' OR sector IS NULL OR sector=?)
           GROUP BY library_type ORDER BY library_type""",
        params
    ).fetchall()
    counts = {kind: 0 for kind, _ in LIBRARY_TYPES}
    for row in rows:
        counts[row["library_type"]] = row["count"]
    return [
        {"library_type": kind, "label": label, "count": counts[kind]}
        for kind, label in LIBRARY_TYPES
    ]


def _evidence_context(rows):
    text_parts = []
    for row in rows:
        text_parts.append(str(row["title"] or ""))
        text_parts.append(str(row["source_type"] or ""))
    return " ".join(text_parts).lower()


def _match_rule(rule, evidence_text):
    any_terms = [str(term).lower() for term in rule.get("match_any", [])]
    required_markers = [str(term).lower() for term in rule.get("required_markers", [])]
    matched = bool(any_terms) and any(term in evidence_text for term in any_terms)
    complete = matched and all(term in evidence_text for term in required_markers)
    return matched, complete


def run_diagnostic(db, case_id):
    """ينفذ Knowledge -> Evidence -> Rules دون أي استدعاء AI."""
    ensure_schema(db)
    case = db.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not case:
        return None
    company = db.execute("SELECT * FROM companies WHERE company_id=?", (case["company_id"],)).fetchone()
    evidence = db.execute(
        "SELECT evidence_id, title, source_type, confidence FROM evidence WHERE case_id=? ORDER BY date_collected",
        (case_id,)
    ).fetchall()
    company_data = dict(company) if company else {}
    sector = sector_for_company(company_data)
    evidence_text = _evidence_context(evidence)

    result = {
        "status": "NO_KNOWLEDGE",
        "message": "قاعدة معرفة سنع لا تحتوي على دليل كافٍ لاتخاذ هذا القرار.",
        "action_required": "Research Required",
        "sector": sector,
        "sector_label": SUPPORTED_SECTORS.get(sector, "قطاع غير مغطى في V1"),
        "matched_patterns": [],
        "supported_findings": [],
        "missing_evidence": [],
        "diagnostic_questions": [],
        "knowledge_path": "Sana Knowledge → Evidence → Diagnostic Rules → Decision → Recommendation",
        "ai_used": False,
        "knowledge_version": "v1.0",
    }
    if not sector:
        result["missing_evidence"].append("تحديد قطاع مدعوم في V1: B2B Services أو Experts أو E-commerce")
        result["action_required"] = "Research Required"
    else:
        rows = db.execute(
            """SELECT * FROM knowledge_objects
               WHERE library_type='DIAGNOSTIC_PATTERN' AND status='approved'
                 AND (sector=? OR sector='shared') ORDER BY object_id""",
            (sector,)
        ).fetchall()
        if not rows:
            result["missing_evidence"].append("إضافة قاعدة تشخيص لهذا القطاع")
        for row in rows:
            obj = _object_dict(row)
            matched, complete = _match_rule(obj["diagnostic_rule"], evidence_text)
            if not matched:
                continue
            evidence_ids = [
                e["evidence_id"] for e in evidence
                if any(term.lower() in str(e["title"] or "").lower()
                       for term in obj["diagnostic_rule"].get("match_any", []))
            ]
            finding = {
                "object_id": obj["object_id"],
                "title": obj["title"],
                "category": obj["category"],
                "problem": obj["problem"],
                "symptoms": obj["symptoms"],
                "possible_causes": obj["possible_causes"],
                "diagnostic_questions": obj["diagnostic_questions"],
                "required_evidence": obj["required_evidence"],
                "kpis": obj["kpis"],
                "benchmark": obj["benchmark"],
                "diagnostic_rule": obj["diagnostic_rule"],
                "decision_rule": obj["decision_rule"],
                "recommendation": obj["recommendation"],
                "sop": obj["sop"],
                "source": obj["source"],
                "evidence_quality": obj["evidence_quality"],
                "confidence_level": obj["confidence_level"],
                "knowledge_level": obj["knowledge_level"],
                "version": obj["version"],
                "last_reviewed": obj["last_reviewed"],
                "evidence_ids": evidence_ids,
                "status": "SUPPORTED" if complete else "INSUFFICIENT_EVIDENCE",
            }
            result["matched_patterns"].append(finding)
            if complete:
                result["supported_findings"].append(finding)
            else:
                missing = obj["required_evidence"] or ["دليل إضافي يثبت النمط"]
                result["missing_evidence"].extend(missing)
                result["diagnostic_questions"].extend(obj["diagnostic_questions"])

        if result["supported_findings"]:
            result["status"] = "SUPPORTED"
            result["message"] = "تم الوصول إلى قرار مدعوم بقاعدة معرفة سنع وأدلة القضية."
            result["action_required"] = "Consultant Review"
        elif result["matched_patterns"]:
            result["status"] = "INSUFFICIENT_EVIDENCE"
            result["message"] = "قاعدة معرفة سنع لا تحتوي على دليل كافٍ لاتخاذ هذا القرار."
            result["action_required"] = "Missing Evidence"
        else:
            result["status"] = "INSUFFICIENT_EVIDENCE"
            result["missing_evidence"].append("دليل يطابق مشكلة معروفة في مكتبة سنع")

    # إزالة التكرار مع الحفاظ على ترتيب العرض.
    result["missing_evidence"] = list(dict.fromkeys(result["missing_evidence"]))
    result["diagnostic_questions"] = list(dict.fromkeys(result["diagnostic_questions"]))

    run_id = "DR-" + uuid.uuid4().hex[:10].upper()
    db.execute(
        """INSERT INTO diagnostic_runs (run_id, case_id, company_id, status, result, knowledge_version)
           VALUES (?,?,?,?,?,?)""",
        (run_id, case_id, case["company_id"], result["status"],
         json.dumps(result, ensure_ascii=False), result["knowledge_version"])
    )
    for finding in result["matched_patterns"]:
        db.execute(
            """INSERT INTO diagnostic_findings
               (finding_id, run_id, object_id, finding_type, title, summary,
                confidence_level, evidence_ids, missing_evidence, status)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("DF-" + uuid.uuid4().hex[:10].upper(), run_id, finding["object_id"],
             "Diagnostic Finding", finding["title"], finding["problem"],
             finding["confidence_level"], _json(finding["evidence_ids"]),
             _json(finding.get("required_evidence", [])) if finding["status"] != "SUPPORTED" else None,
             finding["status"])
        )
    db.commit()
    result["run_id"] = run_id
    return result


def latest_diagnostic(db, case_id):
    ensure_schema(db)
    row = db.execute(
        "SELECT result, run_id, created_at FROM diagnostic_runs WHERE case_id=? ORDER BY created_at DESC LIMIT 1",
        (case_id,)
    ).fetchone()
    if not row:
        return None
    result = _loads(row["result"], {})
    result["run_id"] = row["run_id"]
    result["created_at"] = row["created_at"]
    return result
