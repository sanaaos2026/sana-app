-- SCHEMA_SNAPSHOT.sql — بنية قاعدة سنع الفعلية (Postgres)
-- تاريخ الاستخراج: 2026-07-16
-- ملاحظة: هذا الملف يحتوي بنية الجداول فقط، لا بيانات العملاء

-- ── assets ──
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    asset_name TEXT NOT NULL,
    current_score INTEGER,
    fragility_score INTEGER,
    owner_user_id TEXT,
    status TEXT,
    updated_at TEXT DEFAULT to_char((now() AT TIME ZONE 'utc'::text), 'YYYY-MM-DD HH24:MI:SS'::text)
);


-- ── case_frameworks ──
CREATE TABLE IF NOT EXISTS case_frameworks (
    cf_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    framework_id TEXT NOT NULL,
    linked_at TEXT DEFAULT to_char((now() AT TIME ZONE 'utc'::text), 'YYYY-MM-DD HH24:MI:SS'::text)
);


-- ── cases ──
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    case_title TEXT NOT NULL,
    case_type TEXT,
    case_status TEXT DEFAULT 'Open'::text,
    declared_problem TEXT,
    real_question TEXT,
    related_asset_id TEXT,
    confidence_score INTEGER,
    value_impact_estimate TEXT,
    ai_analysis TEXT,
    opened_at TEXT DEFAULT to_char((now() AT TIME ZONE 'utc'::text), 'YYYY-MM-DD HH24:MI:SS'::text),
    closed_at TEXT
);


-- ── companies ──
CREATE TABLE IF NOT EXISTS companies (
    company_id TEXT NOT NULL,
    name TEXT NOT NULL,
    sector TEXT,
    city TEXT,
    stage TEXT,
    employee_count INTEGER,
    annual_revenue REAL,
    vision TEXT,
    main_goal TEXT,
    signup_code TEXT,
    created_at TEXT DEFAULT to_char((now() AT TIME ZONE 'utc'::text), 'YYYY-MM-DD HH24:MI:SS'::text),
    sds_done SMALLINT DEFAULT 0,
    success_criteria TEXT
);

-- INDEX: CREATE UNIQUE INDEX companies_signup_code_key ON public.companies USING btree (signup_code);

-- ── decision_asset_impacts ──
CREATE TABLE IF NOT EXISTS decision_asset_impacts (
    impact_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    score_impact INTEGER NOT NULL,
    is_primary INTEGER DEFAULT 0
);


-- ── decisions ──
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    case_id TEXT,
    asset_id TEXT,
    title TEXT NOT NULL,
    recommended_action TEXT,
    reason TEXT,
    confidence_score INTEGER,
    expected_impact TEXT,
    status TEXT DEFAULT 'مقترح'::text,
    created_at TEXT DEFAULT to_char((now() AT TIME ZONE 'utc'::text), 'YYYY-MM-DD HH24:MI:SS'::text),
    phase_label TEXT,
    structured_data TEXT,
    owner_name TEXT,
    due_date TEXT,
    success_metric TEXT
);


-- ── evidence ──
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    case_id TEXT,
    asset_id TEXT,
    title TEXT NOT NULL,
    source_type TEXT,
    confidence INTEGER,
    date_collected TEXT DEFAULT to_char((now() AT TIME ZONE 'utc'::text), 'YYYY-MM-DD HH24:MI:SS'::text),
    ai_analysis TEXT,
    ai_suggested_asset_id TEXT,
    evidence_type TEXT DEFAULT 'Evidence',
    source_ref TEXT,
    information_type TEXT NOT NULL DEFAULT 'Narrative',
    verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
    source_category TEXT NOT NULL DEFAULT 'UNKNOWN',
    period_start DATE,
    period_end DATE,
    raw_value TEXT,
    normalized_value NUMERIC,
    unit TEXT,
    topic_key TEXT,
    seasonality_context TEXT
);

-- ── diagnostic_baselines ──
CREATE TABLE IF NOT EXISTS diagnostic_baselines (
    baseline_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    case_id TEXT,
    baseline_start DATE NOT NULL,
    baseline_end DATE NOT NULL,
    comparison_start DATE,
    comparison_end DATE,
    seasonality_context TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── evidence_relations ──
CREATE TABLE IF NOT EXISTS evidence_relations (
    relation_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    case_id TEXT,
    from_evidence_id TEXT NOT NULL,
    to_evidence_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    verification_question TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ── methodology_docs ──
CREATE TABLE IF NOT EXISTS methodology_docs (
    doc_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    subtitle TEXT,
    content TEXT NOT NULL,
    doc_type TEXT DEFAULT 'GENERIC'::text,
    version TEXT DEFAULT 'v1.0'::text,
    bos_id TEXT,
    created_at TEXT DEFAULT to_char((now() AT TIME ZONE 'utc'::text), 'YYYY-MM-DD HH24:MI:SS'::text)
);

-- INDEX: CREATE UNIQUE INDEX methodology_docs_slug_key ON public.methodology_docs USING btree (slug);

-- ── task_pack_items ──
CREATE TABLE IF NOT EXISTS task_pack_items (
    item_id TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    category_label TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT,
    asset_type TEXT NOT NULL,
    score_impact INTEGER NOT NULL,
    sort_order INTEGER DEFAULT 0
);


-- ── task_packs ──
CREATE TABLE IF NOT EXISTS task_packs (
    pack_id TEXT NOT NULL,
    name TEXT NOT NULL,
    sector TEXT,
    description TEXT,
    created_at TEXT DEFAULT to_char((now() AT TIME ZONE 'utc'::text), 'YYYY-MM-DD HH24:MI:SS'::text)
);


-- ── tasks ──
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    decision_id TEXT,
    title TEXT NOT NULL,
    owner_user_id TEXT,
    due_date TEXT,
    status TEXT DEFAULT 'لم تبدأ'::text,
    priority TEXT,
    created_at TEXT DEFAULT to_char((now() AT TIME ZONE 'utc'::text), 'YYYY-MM-DD HH24:MI:SS'::text),
    completed_at TEXT,
    phase_label TEXT,
    value_note TEXT
);


-- ── user_accounts ──
CREATE TABLE IF NOT EXISTS user_accounts (
    account_id TEXT NOT NULL,
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    company_id TEXT NOT NULL,
    referral_source TEXT,
    created_at TEXT DEFAULT to_char((now() AT TIME ZONE 'utc'::text), 'YYYY-MM-DD HH24:MI:SS'::text)
);

-- INDEX: CREATE UNIQUE INDEX user_accounts_email_key ON public.user_accounts USING btree (email);

-- ── users ──
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT,
    department TEXT,
    status TEXT DEFAULT 'نشط'::text
);

-- ── company memory ──
CREATE TABLE IF NOT EXISTS company_memory_governance (
    governance_id TEXT NOT NULL, storage_destination TEXT NOT NULL,
    case_link TEXT NOT NULL, asset_link TEXT NOT NULL, framework_link TEXT NOT NULL,
    business_event TEXT NOT NULL, version TEXT NOT NULL DEFAULT 'v1.0',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS company_memory_items (
    memory_id TEXT NOT NULL, company_id TEXT NOT NULL, memory_key TEXT NOT NULL,
    memory_type TEXT NOT NULL, current_version_id TEXT, current_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    owner_id TEXT, access_level TEXT NOT NULL DEFAULT 'company',
    confidentiality TEXT NOT NULL DEFAULT 'private', retention_policy TEXT NOT NULL DEFAULT 'retain_history',
    usage_rights TEXT NOT NULL DEFAULT 'no_cross_company',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS company_memory_versions (
    version_id TEXT NOT NULL, memory_id TEXT NOT NULL, company_id TEXT NOT NULL,
    memory_type TEXT NOT NULL, value_json TEXT NOT NULL, context_json TEXT NOT NULL DEFAULT '{}',
    period_start DATE, period_end DATE, observed_at DATE NOT NULL, source_ref TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'unknown', case_id TEXT, asset_id TEXT, decision_id TEXT,
    task_id TEXT, result_ref TEXT, reason TEXT, lifecycle_status TEXT NOT NULL DEFAULT 'CAPTURED',
    verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED', freshness_class TEXT NOT NULL DEFAULT 'MEDIUM',
    source_strength INTEGER NOT NULL DEFAULT 0, verification_confidence INTEGER NOT NULL DEFAULT 0,
    freshness_confidence INTEGER NOT NULL DEFAULT 0, owner_id TEXT, governance_id TEXT NOT NULL,
    supersedes_version_id TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS company_memory_links (
    link_id TEXT NOT NULL, company_id TEXT NOT NULL, version_id TEXT NOT NULL,
    source_type TEXT NOT NULL, source_id TEXT NOT NULL, relationship TEXT NOT NULL DEFAULT 'supports',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS company_memory_conflicts (
    conflict_id TEXT NOT NULL, company_id TEXT NOT NULL, memory_id TEXT NOT NULL,
    existing_version_id TEXT NOT NULL, incoming_version_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN', conflict_reason TEXT NOT NULL,
    resolution_action TEXT, resolved_version_id TEXT, resolved_by TEXT, resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

