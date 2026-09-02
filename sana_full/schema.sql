-- سنع — النواة الأساسية (7 جداول فقط)
-- Sana Core Schema v1 — PostgreSQL
-- (محوَّلة من SQLite: أنواع البيانات TEXT/INTEGER/REAL متطابقة في Postgres،
--  التغيير الوحيد الفعلي هو datetime('now') -> تعبير Postgres مكافئ ينتج
--  نفس صيغة النص "YYYY-MM-DD HH:MM:SS" بالضبط حفاظًا على نفس شكل البيانات القديمة)

CREATE TABLE IF NOT EXISTS companies (
    company_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sector TEXT,
    city TEXT,
    stage TEXT,
    employee_count INTEGER,
    annual_revenue REAL,
    vision TEXT,
    main_goal TEXT,
    signup_code TEXT UNIQUE,
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
    sds_done SMALLINT DEFAULT 0,
    success_criteria TEXT,
    website_url TEXT,
    social_media_url TEXT,
    business_reference_url TEXT,
    business_description TEXT,
    goal_90_days TEXT,
    primary_challenge TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'Active'
);

-- حسابات العملاء مرتبطة بشركة واحدة. الاستثناء الوحيد هو SUPER_ADMIN العام:
-- لا يحمل عضوية شركة، ويصل للشركات عبر مسارات الإدارة الصريحة والمسجلة فقط.
CREATE TABLE IF NOT EXISTS user_accounts (
    account_id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    company_id TEXT,
    referral_source TEXT,
    is_admin SMALLINT NOT NULL DEFAULT 0,
    admin_role TEXT NOT NULL DEFAULT 'USER',
    account_status TEXT NOT NULL DEFAULT 'active',
    last_login_at TIMESTAMPTZ,
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
    FOREIGN KEY (company_id) REFERENCES companies(company_id),
    CONSTRAINT user_accounts_company_or_global_super_admin
      CHECK (
        company_id IS NOT NULL
        OR (admin_role = 'SUPER_ADMIN' AND is_admin = 1)
      )
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT,
    department TEXT,
    status TEXT DEFAULT 'نشط',
    FOREIGN KEY (company_id) REFERENCES companies(company_id)
);

CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    case_title TEXT NOT NULL,
    case_type TEXT,
    case_status TEXT DEFAULT 'Open',
    declared_problem TEXT,
    real_question TEXT,
    related_asset_id TEXT,
    confidence_score INTEGER,
    value_impact_estimate TEXT,
    ai_analysis TEXT,
    opened_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
    closed_at TEXT,
    FOREIGN KEY (company_id) REFERENCES companies(company_id)
);

CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    asset_name TEXT NOT NULL,
    current_score INTEGER,
    fragility_score INTEGER,
    owner_user_id TEXT,
    status TEXT,
    updated_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
    FOREIGN KEY (company_id) REFERENCES companies(company_id)
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    case_id TEXT,
    asset_id TEXT,
    title TEXT NOT NULL,
    source_type TEXT,
    confidence INTEGER,
    date_collected TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
    ai_analysis TEXT,
    ai_suggested_asset_id TEXT,
    evidence_type TEXT DEFAULT 'Evidence'
        CHECK (evidence_type IN ('Fact','Evidence','Hypothesis','Assumption','Inference','Recommendation')),
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
    seasonality_context TEXT,
    FOREIGN KEY (company_id) REFERENCES companies(company_id),
    FOREIGN KEY (case_id) REFERENCES cases(case_id)
);

CREATE TABLE IF NOT EXISTS diagnostic_baselines (
    baseline_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    case_id TEXT REFERENCES cases(case_id) ON DELETE CASCADE,
    baseline_start DATE NOT NULL,
    baseline_end DATE NOT NULL,
    comparison_start DATE,
    comparison_end DATE,
    seasonality_context TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id, case_id),
    CHECK (baseline_end >= baseline_start),
    CHECK (
        (comparison_start IS NULL AND comparison_end IS NULL)
        OR (comparison_start IS NOT NULL AND comparison_end >= comparison_start)
    )
);

CREATE TABLE IF NOT EXISTS evidence_relations (
    relation_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    case_id TEXT REFERENCES cases(case_id),
    from_evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE CASCADE,
    to_evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK (relation_type IN ('SUPPORTS','CONTRADICTS')),
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','RESOLVED')),
    verification_question TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(from_evidence_id, to_evidence_id, relation_type)
);

CREATE TABLE IF NOT EXISTS scan_runs (
    scan_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    status TEXT NOT NULL,
    result TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS scan_findings (
    finding_id TEXT PRIMARY KEY,
    scan_id TEXT NOT NULL REFERENCES scan_runs(scan_id),
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    asset_id TEXT,
    classification TEXT NOT NULL
        CHECK (classification IN ('Fact','Evidence','Hypothesis','Assumption','Inference','Recommendation')),
    title TEXT NOT NULL,
    statement TEXT NOT NULL,
    source_ids TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'Pending Review',
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    case_id TEXT,
    asset_id TEXT,
    title TEXT NOT NULL,
    recommended_action TEXT,
    reason TEXT,
    confidence_score INTEGER,
    expected_impact TEXT,
    status TEXT DEFAULT 'مقترح',
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
    phase_label TEXT,
    structured_data TEXT,
    owner_name TEXT,
    due_date TEXT,
    success_metric TEXT,
    scan_id TEXT,
    evidence_ids TEXT,
    FOREIGN KEY (company_id) REFERENCES companies(company_id),
    FOREIGN KEY (case_id) REFERENCES cases(case_id),
    FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    decision_id TEXT,
    title TEXT NOT NULL,
    owner_user_id TEXT,
    approver_user_id TEXT,
    due_date TEXT,
    kpi TEXT,
    status TEXT DEFAULT 'لم تبدأ',
    priority TEXT,
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
    completed_at TEXT,
    phase_label TEXT,
    value_note TEXT,
    FOREIGN KEY (company_id) REFERENCES companies(company_id),
    FOREIGN KEY (decision_id) REFERENCES decisions(decision_id)
);

-- سجل P0 تاريخي: النتيجة والأثر لا يكتبان فوق الحالة الحالية ولا يرفعان درجة أصل تلقائيًا.
CREATE TABLE IF NOT EXISTS p0_impact_reviews (
    review_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
    task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
    baseline_snapshot_json TEXT NOT NULL,
    result_summary TEXT NOT NULL,
    result_source_ref TEXT NOT NULL,
    impact_outcome TEXT NOT NULL
      CHECK (impact_outcome IN ('IMPROVED','UNCHANGED','WORSE','INCONCLUSIVE')),
    impact_notes TEXT NOT NULL,
    reviewed_by TEXT NOT NULL,
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_p0_impact_reviews_case
    ON p0_impact_reviews(company_id,case_id,reviewed_at DESC);

CREATE TABLE IF NOT EXISTS admin_audit_log (
    audit_id TEXT PRIMARY KEY,
    actor_account_id TEXT NOT NULL REFERENCES user_accounts(account_id),
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    company_id TEXT REFERENCES companies(company_id),
    reason TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_admin_audit_created
    ON admin_audit_log(created_at DESC);

-- وثائق منهجية عامة (مثل "نظام سنع لجلب العملاء") — مراجع مستقلة عن أي شركة،
-- يمكن الرجوع إليها وربطها من أي Case Workspace مستقبلي.
CREATE TABLE IF NOT EXISTS methodology_docs (
    doc_id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    subtitle TEXT,
    content TEXT NOT NULL,
    doc_type TEXT DEFAULT 'GENERIC',
    version TEXT DEFAULT 'v1.0',
    bos_id TEXT,
    sector_tags TEXT,
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))
);

-- حزم مهام قابلة لإعادة الاستخدام عبر أي شركة/قطاع (Task Packs)
CREATE TABLE IF NOT EXISTS task_packs (
    pack_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sector TEXT,
    description TEXT,
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS task_pack_items (
    item_id TEXT PRIMARY KEY,
    pack_id TEXT NOT NULL,
    category_label TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT,
    asset_type TEXT NOT NULL,
    score_impact INTEGER NOT NULL,
    sort_order INTEGER DEFAULT 0,
    FOREIGN KEY (pack_id) REFERENCES task_packs(pack_id)
);

CREATE TABLE IF NOT EXISTS decision_asset_impacts (
    impact_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    score_impact INTEGER NOT NULL,
    is_primary INTEGER DEFAULT 0,
    FOREIGN KEY (decision_id) REFERENCES decisions(decision_id),
    FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
);

-- جدول ربط القضايا بأطر العمل المنهجية (يُنشأ تلقائياً عند Discovery)
CREATE TABLE IF NOT EXISTS case_frameworks (
    cf_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    framework_id TEXT NOT NULL,
    linked_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
    FOREIGN KEY (case_id) REFERENCES cases(case_id),
    FOREIGN KEY (company_id) REFERENCES companies(company_id)
);

-- Sana Knowledge — المعرفة المشتركة المنظمة والنتائج التشخيصية القابلة للتتبع.
-- الحقول النصية التي تحتوي JSON مقصودة لتبقى متوافقة مع ترحيل SQLite السابق.
CREATE TABLE IF NOT EXISTS knowledge_objects (
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
    source_id TEXT,
    source_file_id TEXT,
    provenance_link_id TEXT,
    source_url TEXT,
    evidence_quality TEXT,
    confidence_level TEXT,
    applicable_when TEXT,
    do_not_apply_when TEXT,
    knowledge_level TEXT NOT NULL DEFAULT 'L0',
    version TEXT NOT NULL DEFAULT 'v1.0',
    last_reviewed TEXT,
    status TEXT NOT NULL DEFAULT 'approved',
    source_excerpt TEXT,
    original_summary TEXT,
    domains TEXT,
    sector_tags TEXT,
    business_model_tags TEXT,
    stage_tags TEXT,
    problem_tags TEXT,
    goal_tags TEXT,
    bottleneck_tags TEXT,
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
    updated_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS knowledge_sources (
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
    author_identity TEXT,
    material_type TEXT,
    methodology_note TEXT,
    publisher_trust TEXT,
    publisher_continuity TEXT,
    site_age_evidence_url TEXT,
    site_age_evidence_date DATE,
    document_date DATE,
    version_label TEXT,
    content_fingerprint TEXT,
    trust_level TEXT,
    reviewed_at DATE,
    eligibility_json TEXT,
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
    updated_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_source_fingerprint
    ON knowledge_sources(content_fingerprint)
    WHERE content_fingerprint IS NOT NULL;

-- صندوق استقبال خاص للكتب والملفات والأبحاث والملخصات قبل مراجعتها واعتمادها.
-- لا يدخل هذا الجدول في التشخيص أو البحث المعتمد تلقائيًا.
CREATE TABLE IF NOT EXISTS research_sources (
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
    version_label TEXT DEFAULT 'v1.0',
    document_date DATE,
    sensitivity TEXT NOT NULL DEFAULT 'internal',
    knowledge_scope TEXT NOT NULL DEFAULT 'private',
    storage_destination TEXT,
    framework_ref TEXT,
    ingestion_event TEXT,
    review_status TEXT NOT NULL DEFAULT 'inbox',
    is_private SMALLINT NOT NULL DEFAULT 1,
    owner_account_id TEXT,
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
    updated_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
    CHECK (source_kind IN ('file','book','research','summary')),
    CHECK (origin IN ('manual','drive','url','upload')),
    CHECK (rights_status IN ('pending','owned','licensed','public','restricted')),
    CHECK (knowledge_scope IN ('private','private_case','shared_candidate','shared')),
    CHECK (review_status IN ('inbox','approved','rejected','archived')),
    CHECK (is_private = 0 OR owner_account_id IS NOT NULL),
    CHECK (is_private = 1 OR company_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS research_source_files (
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
);

CREATE TABLE IF NOT EXISTS research_source_chunks (
    chunk_id TEXT PRIMARY KEY,
    research_source_id TEXT NOT NULL REFERENCES research_sources(research_source_id) ON DELETE CASCADE,
    file_id TEXT REFERENCES research_source_files(file_id) ON DELETE CASCADE,
    chunk_order INTEGER NOT NULL,
    page_number INTEGER,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
    UNIQUE(research_source_id, file_id, chunk_order)
);

-- تصنيف قابل للمراجعة لكل تصريح مختار من المصدر؛ لا يتحول إلى knowledge_object
-- ولا يُستخدم في التشخيص بمجرد رفع الملف.
CREATE TABLE IF NOT EXISTS research_source_annotations (
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
);

-- علاقة مقارنة صريحة بين المصدر المرشح والمرجع canonical؛ لا تعني اعتمادًا أو دمجًا.
CREATE TABLE IF NOT EXISTS research_source_relations (
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
);

CREATE INDEX IF NOT EXISTS idx_research_chunks_source
    ON research_source_chunks(research_source_id, chunk_order);

CREATE INDEX IF NOT EXISTS idx_research_sources_owner
    ON research_sources(owner_account_id, review_status, created_at);

CREATE INDEX IF NOT EXISTS idx_research_annotations_source
    ON research_source_annotations(research_source_id, annotation_order);

CREATE INDEX IF NOT EXISTS idx_research_relations_source
    ON research_source_relations(research_source_id, review_status);

CREATE TABLE IF NOT EXISTS knowledge_versions (
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
);

-- دورة البحث الدوري: إعداد واحد، سجل تشغيل، مرشحون للمراجعة، وتنبيهات.
CREATE TABLE IF NOT EXISTS knowledge_research_config (
    config_id TEXT PRIMARY KEY,
    enabled SMALLINT NOT NULL DEFAULT 0,
    interval_hours INTEGER NOT NULL DEFAULT 168,
    max_topics INTEGER NOT NULL DEFAULT 12,
    max_results_per_topic INTEGER NOT NULL DEFAULT 5,
    max_fetches INTEGER NOT NULL DEFAULT 20,
    max_bytes BIGINT NOT NULL DEFAULT 1500000,
    timeout_seconds INTEGER NOT NULL DEFAULT 12,
    retries INTEGER NOT NULL DEFAULT 2,
    per_domain_delay_seconds REAL NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO knowledge_research_config (config_id) VALUES ('default')
ON CONFLICT (config_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS knowledge_research_runs (
    run_id TEXT PRIMARY KEY,
    trigger_type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    topics_json TEXT NOT NULL DEFAULT '[]',
    discovered_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    fetch_count INTEGER NOT NULL DEFAULT 0,
    bytes_fetched BIGINT NOT NULL DEFAULT 0,
    estimated_cost NUMERIC,
    error_message TEXT,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS knowledge_research_candidates (
    candidate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES knowledge_research_runs(run_id),
    canonical_url TEXT NOT NULL,
    domain TEXT NOT NULL,
    title TEXT NOT NULL,
    publisher TEXT,
    source_age_years INTEGER,
    source_age_evidence_url TEXT,
    source_age_evidence_date DATE,
    rights_status TEXT NOT NULL,
    rights_evidence_url TEXT,
    rights_expires_at DATE,
    trust_level TEXT NOT NULL,
    eligibility_checked_at TIMESTAMPTZ,
    publisher_continuity_note TEXT,
    document_date DATE,
    domain_registry_source_id TEXT,
    topic TEXT NOT NULL,
    sector_tag TEXT,
    goal_tag TEXT,
    recommendation_reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending_review',
    rejection_reason TEXT,
    retrieved_at TIMESTAMPTZ,
    http_status INTEGER,
    etag TEXT,
    last_modified TEXT,
    version_label TEXT,
    content_fingerprint TEXT,
    content_text TEXT,
    previous_candidate_id TEXT,
    diff_text TEXT,
    reviewer TEXT,
    reviewed_at TIMESTAMPTZ,
    promoted_source_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (status IN ('pending_review','approved','rejected','duplicate','blocked','superseded')),
    UNIQUE(run_id, canonical_url)
);

CREATE TABLE IF NOT EXISTS knowledge_research_alerts (
    alert_id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES knowledge_research_runs(run_id),
    candidate_id TEXT REFERENCES knowledge_research_candidates(candidate_id),
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS knowledge_research_gaps (
    gap_key TEXT PRIMARY KEY,
    sector TEXT,
    library_type TEXT,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS knowledge_conflicts (
    conflict_id TEXT PRIMARY KEY,
    object_id TEXT NOT NULL REFERENCES knowledge_objects(object_id) ON DELETE CASCADE,
    conflicting_object_id TEXT NOT NULL REFERENCES knowledge_objects(object_id) ON DELETE CASCADE,
    conflict_note TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending','resolved','dismissed')),
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
    CHECK (object_id <> conflicting_object_id),
    UNIQUE(object_id, conflicting_object_id)
);

CREATE TABLE IF NOT EXISTS knowledge_links (
    link_id TEXT PRIMARY KEY,
    from_object_id TEXT NOT NULL REFERENCES knowledge_objects(object_id),
    to_object_id TEXT NOT NULL REFERENCES knowledge_objects(object_id),
    relation TEXT NOT NULL,
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
    UNIQUE(from_object_id, to_object_id, relation)
);

CREATE TABLE IF NOT EXISTS diagnostic_runs (
    run_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    status TEXT NOT NULL,
    result TEXT NOT NULL,
    knowledge_version TEXT NOT NULL DEFAULT 'v1.0',
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS diagnostic_findings (
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
);

-- فهرس Drive المعرفي: Drive مصدر الأصل، وهذه الجداول تسجل Metadata والسلسلة
-- دون نقل أو حذف أو إعادة تسمية أي ملف.
CREATE TABLE IF NOT EXISTS drive_index_config (
    config_id TEXT PRIMARY KEY,
    root_folder_id TEXT,
    root_folder_name TEXT,
    master_index_file_id TEXT,
    master_index_url TEXT,
    setup_status TEXT NOT NULL DEFAULT 'not_checked',
    last_sync_run_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO drive_index_config (config_id) VALUES ('default')
ON CONFLICT (config_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS drive_files (
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
    problem TEXT, cause TEXT, kpi TEXT, diagnostic_rule TEXT, decision_rule TEXT,
    sop TEXT, case_study TEXT, quality TEXT, confidence TEXT,
    confidentiality TEXT NOT NULL DEFAULT 'internal',
    version_label TEXT, related_version TEXT,
    relations_json TEXT NOT NULL DEFAULT '[]',
    keywords TEXT,
    reviewed_at TIMESTAMPTZ,
    metadata_missing_json TEXT NOT NULL DEFAULT '[]',
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drive_sync_runs (
    run_id TEXT PRIMARY KEY, trigger_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running', root_folder_id TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ,
    files_seen INTEGER NOT NULL DEFAULT 0, files_indexed INTEGER NOT NULL DEFAULT 0,
    unread_count INTEGER NOT NULL DEFAULT 0, missing_metadata_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0, orphan_count INTEGER NOT NULL DEFAULT 0,
    permission_error_count INTEGER NOT NULL DEFAULT 0, network_error_count INTEGER NOT NULL DEFAULT 0,
    unreadable_count INTEGER NOT NULL DEFAULT 0, error_message TEXT, summary_json TEXT
);

CREATE TABLE IF NOT EXISTS drive_client_folder_mappings (
    mapping_id TEXT PRIMARY KEY, drive_folder_id TEXT UNIQUE NOT NULL,
    drive_folder_name TEXT NOT NULL, company_id TEXT NOT NULL REFERENCES companies(company_id),
    confidence TEXT NOT NULL DEFAULT 'explicit',
    review_status TEXT NOT NULL DEFAULT 'pending_review',
    created_by TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), reviewed_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS drive_knowledge_sources (
    link_id TEXT PRIMARY KEY, drive_file_id TEXT NOT NULL REFERENCES drive_files(drive_file_id),
    source_id TEXT REFERENCES knowledge_sources(source_id), source_type TEXT NOT NULL,
    version_label TEXT, section_locator TEXT, quality TEXT, verified_at TIMESTAMPTZ,
    review_status TEXT NOT NULL DEFAULT 'pending_review', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(drive_file_id, source_id, section_locator)
);
CREATE TABLE IF NOT EXISTS drive_source_excerpts (
    excerpt_id TEXT PRIMARY KEY, drive_file_id TEXT NOT NULL REFERENCES drive_files(drive_file_id),
    section_locator TEXT NOT NULL, excerpt_text TEXT NOT NULL, content_hash TEXT NOT NULL,
    extraction_status TEXT NOT NULL, company_id TEXT,
    case_id TEXT REFERENCES cases(case_id), created_by TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending_review',
    review_reason TEXT, review_references TEXT NOT NULL DEFAULT '[]',
    anonymized_text TEXT, anonymization_notes TEXT, published_text TEXT,
    reviewed_by TEXT, reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(drive_file_id,section_locator,content_hash)
);
CREATE TABLE IF NOT EXISTS drive_excerpt_reviews (
    review_id TEXT PRIMARY KEY,
    excerpt_id TEXT NOT NULL UNIQUE REFERENCES drive_source_excerpts(excerpt_id) ON DELETE CASCADE,
    decision TEXT NOT NULL, reason TEXT NOT NULL,
    references_json TEXT NOT NULL DEFAULT '[]',
    anonymization_notes TEXT, reviewer TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (decision IN ('approved','rejected'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_drive_excerpt_reviews_excerpt
    ON drive_excerpt_reviews(excerpt_id);
CREATE TABLE IF NOT EXISTS drive_private_citations (
    citation_id TEXT PRIMARY KEY,
    drive_file_id TEXT NOT NULL REFERENCES drive_files(drive_file_id),
    research_source_id TEXT NOT NULL REFERENCES research_sources(research_source_id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL REFERENCES research_source_chunks(chunk_id) ON DELETE CASCADE,
    excerpt_id TEXT NOT NULL REFERENCES drive_source_excerpts(excerpt_id) ON DELETE CASCADE,
    section_locator TEXT NOT NULL, company_id TEXT,
    case_id TEXT REFERENCES cases(case_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(drive_file_id,research_source_id,chunk_id,excerpt_id)
);
CREATE TABLE IF NOT EXISTS sana_memory_entries (
    memory_id TEXT PRIMARY KEY, memory_type TEXT NOT NULL, statement TEXT NOT NULL,
    source_file_id TEXT REFERENCES drive_files(drive_file_id), source_id TEXT, section_locator TEXT,
    observed_at TEXT, confidence TEXT, company_id TEXT, case_id TEXT, kpi TEXT,
    verification_status TEXT NOT NULL DEFAULT 'unverified', conflict_status TEXT NOT NULL DEFAULT 'none',
    shared_scope TEXT NOT NULL DEFAULT 'private', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (memory_type IN ('fact','claim','note','hypothesis','unknown','conflict','evidence')),
    CHECK (shared_scope IN ('private','company','shared')),
    CHECK (shared_scope <> 'shared' OR verification_status = 'reviewed')
);
CREATE TABLE IF NOT EXISTS drive_provenance_links (
    provenance_id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
    drive_file_id TEXT NOT NULL REFERENCES drive_files(drive_file_id), source_id TEXT,
    section_locator TEXT, relationship TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(entity_type, entity_id, drive_file_id, section_locator, relationship)
);
CREATE TABLE IF NOT EXISTS knowledge_release_log (
    release_id TEXT PRIMARY KEY, release_label TEXT UNIQUE NOT NULL, status TEXT NOT NULL,
    previous_release TEXT, change_reason TEXT NOT NULL, related_release TEXT,
    content_hash TEXT, reviewer TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION reject_knowledge_release_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'KNOWLEDGE_RELEASE_APPEND_ONLY' USING ERRCODE = '55000';
END;
$$;
DO $$
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
$$;
CREATE TABLE IF NOT EXISTS capability_gaps (
    gap_id TEXT PRIMARY KEY, gap_key TEXT UNIQUE NOT NULL, sector TEXT, description TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1, impact TEXT, case_ids_json TEXT NOT NULL DEFAULT '[]',
    proposed_release TEXT, status TEXT NOT NULL DEFAULT 'recorded',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Business Growth OS — حقائق موثقة، Baseline قابل للاستخدام، وهوية canonical.
-- كل سجل جديد يحمل governance_id الذي يثبت الخانات الخمس المطلوبة.
CREATE TABLE IF NOT EXISTS gos_governance_registry (
    governance_id TEXT PRIMARY KEY,
    record_type TEXT UNIQUE NOT NULL,
    storage_destination TEXT NOT NULL,
    case_link TEXT NOT NULL,
    asset_link TEXT NOT NULL,
    framework_link TEXT NOT NULL,
    business_event TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT 'v1.0',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gos_metric_definitions (
    metric_key TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    label_ar TEXT NOT NULL,
    unit TEXT NOT NULL,
    formula TEXT NOT NULL,
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id)
);

CREATE TABLE IF NOT EXISTS gos_truth_records (
    truth_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    case_id TEXT REFERENCES cases(case_id),
    asset_id TEXT REFERENCES assets(asset_id),
    subject TEXT NOT NULL,
    value_json TEXT NOT NULL,
    unit TEXT,
    classification TEXT NOT NULL CHECK (classification IN ('Fact','Assumption','Forecast','Opinion','Conflict')),
    source_ref TEXT NOT NULL,
    observed_at DATE NOT NULL,
    confidence INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gos_baselines (
    baseline_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    case_id TEXT REFERENCES cases(case_id),
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    source_ref TEXT NOT NULL,
    observed_at DATE NOT NULL,
    confidence INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    status TEXT NOT NULL CHECK (status IN ('complete','incomplete')),
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (period_end >= period_start)
);

CREATE TABLE IF NOT EXISTS gos_baseline_metrics (
    metric_id TEXT PRIMARY KEY,
    baseline_id TEXT NOT NULL REFERENCES gos_baselines(baseline_id) ON DELETE CASCADE,
    metric_key TEXT NOT NULL REFERENCES gos_metric_definitions(metric_key),
    value_numeric NUMERIC,
    unit TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    observed_at DATE NOT NULL,
    confidence INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    classification TEXT NOT NULL CHECK (classification IN ('Fact','Assumption','Forecast','Opinion','Conflict')),
    availability TEXT NOT NULL CHECK (availability IN ('available','unavailable')),
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    UNIQUE(baseline_id, metric_key)
);

CREATE TABLE IF NOT EXISTS gos_project_profiles (
    profile_key TEXT NOT NULL,
    version TEXT NOT NULL,
    label TEXT NOT NULL,
    label_ar TEXT NOT NULL,
    stages_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    offer_fields_json TEXT NOT NULL,
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    PRIMARY KEY(profile_key, version)
);

CREATE TABLE IF NOT EXISTS gos_company_profiles (
    company_id TEXT PRIMARY KEY REFERENCES companies(company_id),
    profile_key TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    selected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_ref TEXT NOT NULL,
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    FOREIGN KEY (profile_key, profile_version) REFERENCES gos_project_profiles(profile_key, version)
);

CREATE TABLE IF NOT EXISTS gos_canonical_entities (
    canonical_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    entity_type TEXT NOT NULL CHECK (entity_type IN ('customer','opportunity','service','offer','project','invoice','channel','file','relationship')),
    source_table TEXT NOT NULL,
    source_id TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    display_name TEXT,
    case_id TEXT REFERENCES cases(case_id),
    asset_id TEXT REFERENCES assets(asset_id),
    merged_into_id TEXT REFERENCES gos_canonical_entities(canonical_id),
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id, entity_type, source_table, source_id),
    UNIQUE(company_id, entity_type, identity_key)
);

CREATE TABLE IF NOT EXISTS gos_canonical_merges (
    merge_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    from_canonical_id TEXT NOT NULL REFERENCES gos_canonical_entities(canonical_id),
    to_canonical_id TEXT NOT NULL REFERENCES gos_canonical_entities(canonical_id),
    reason TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    case_id TEXT REFERENCES cases(case_id),
    asset_id TEXT REFERENCES assets(asset_id),
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (from_canonical_id <> to_canonical_id)
);

-- Business Growth OS Engine — دورة اختناق واحدة، تجربة مقاسة، قرار، وحلقة تعلم.
CREATE TABLE IF NOT EXISTS gos_bottleneck_cycles (
    cycle_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    case_id TEXT REFERENCES cases(case_id),
    baseline_id TEXT NOT NULL REFERENCES gos_baselines(baseline_id),
    project_profile_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('EVIDENCE_GATE','READY','CLOSED')),
    evidence_gate_json TEXT,
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS gos_bottlenecks (
    bottleneck_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES gos_bottleneck_cycles(cycle_id) ON DELETE CASCADE,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    problem TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    financial_impact_json TEXT NOT NULL,
    candidate_cause TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN ('Hypothesis','Conflict')),
    confidence INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    priority INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 100),
    is_primary BOOLEAN NOT NULL DEFAULT false,
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_gos_one_primary_bottleneck
    ON gos_bottlenecks(cycle_id) WHERE is_primary = true;

CREATE TABLE IF NOT EXISTS gos_experiments (
    experiment_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    cycle_id TEXT NOT NULL REFERENCES gos_bottleneck_cycles(cycle_id),
    bottleneck_id TEXT REFERENCES gos_bottlenecks(bottleneck_id),
    hypothesis TEXT NOT NULL,
    one_change TEXT NOT NULL,
    target_segment TEXT NOT NULL,
    owner TEXT NOT NULL,
    start_date DATE NOT NULL,
    kpi TEXT NOT NULL,
    baseline_id TEXT NOT NULL REFERENCES gos_baselines(baseline_id),
    baseline_snapshot_json TEXT,
    target_numeric NUMERIC NOT NULL,
    success_boundary TEXT NOT NULL,
    stop_boundary TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft','running','closed','cancelled')),
    locked_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    actual_result_json TEXT,
    outcome TEXT,
    decision_id TEXT,
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS gos_experiment_process (
    experiment_id TEXT PRIMARY KEY REFERENCES gos_experiments(experiment_id) ON DELETE CASCADE,
    current_stage TEXT NOT NULL CHECK (current_stage IN ('Manual','Measure','Improve','Standardize','Automate')),
    manual_proven BOOLEAN NOT NULL DEFAULT false,
    repetitions INTEGER NOT NULL DEFAULT 0 CHECK (repetitions >= 0),
    source_ref TEXT NOT NULL,
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gos_experiment_decisions (
    decision_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES gos_experiments(experiment_id),
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    action TEXT NOT NULL CHECK (action IN ('Scale','Modify','Hold','Kill')),
    reason TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    baseline_id TEXT NOT NULL REFERENCES gos_baselines(baseline_id),
    source_ref TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('proposed','approved')),
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS gos_learning_links (
    link_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    from_type TEXT NOT NULL CHECK (from_type IN ('evidence','pattern','hypothesis','experiment','result','decision','knowledge','sop')),
    from_id TEXT NOT NULL,
    link_type TEXT NOT NULL,
    to_type TEXT NOT NULL CHECK (to_type IN ('evidence','pattern','hypothesis','experiment','result','decision','knowledge','sop')),
    to_id TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    knowledge_version TEXT NOT NULL,
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (from_type <> to_type OR from_id <> to_id),
    UNIQUE(company_id, from_type, from_id, link_type, to_type, to_id, knowledge_version)
);

-- Revenue Cycle — opportunities يبقى سجل الصفقة الوحيد.
-- هذان الجدولان يجب أن يسبقا جداول Revenue Cycle التي تشير إليهما بمفاتيح خارجية.
CREATE TABLE IF NOT EXISTS leads (
    lead_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    name TEXT NOT NULL,
    company_name TEXT,
    email TEXT,
    phone TEXT,
    source TEXT,
    service_interest TEXT,
    status TEXT NOT NULL DEFAULT 'جديد',
    owner_id TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS opportunities (
    opp_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    lead_id TEXT REFERENCES leads(lead_id),
    title TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'عميل محتمل',
    amount NUMERIC,
    probability INTEGER,
    expected_close_date DATE,
    next_action TEXT,
    next_action_due DATE,
    outcome_reason TEXT,
    owner_id TEXT,
    delivery_task_id TEXT,
    archived SMALLINT NOT NULL DEFAULT 0,
    archived_at TIMESTAMPTZ,
    archived_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_opp_company ON opportunities(company_id);
CREATE INDEX IF NOT EXISTS idx_opp_stage ON opportunities(company_id,stage);
CREATE INDEX IF NOT EXISTS idx_opp_due ON opportunities(company_id,next_action_due);

ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS revenue_stage_id TEXT;
ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS service_name TEXT;
ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS channel_name TEXT;
ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS sector_name TEXT;

CREATE TABLE IF NOT EXISTS rc_stage_profiles (
    profile_id TEXT PRIMARY KEY,
    project_type TEXT UNIQUE NOT NULL,
    stages_json TEXT NOT NULL,
    version TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rc_stage_history (
    transition_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    opp_id TEXT NOT NULL REFERENCES opportunities(opp_id) ON DELETE CASCADE,
    from_stage_id TEXT,
    to_stage_id TEXT NOT NULL,
    owner_id TEXT,
    reason TEXT,
    source_ref TEXT NOT NULL,
    transitioned_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rc_opportunity_economics (
    opp_id TEXT PRIMARY KEY REFERENCES opportunities(opp_id) ON DELETE CASCADE,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    customer_problem TEXT, customer_language TEXT, decision_maker TEXT,
    objection TEXT, winning_offer TEXT, outcome_reason TEXT,
    contract_value NUMERIC, recognized_revenue NUMERIC,
    delivery_cost NUMERIC, acquisition_cost NUMERIC,
    closed_at TIMESTAMPTZ, source_ref TEXT NOT NULL, observed_at DATE NOT NULL,
    classification TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rc_projects (
    project_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    opp_id TEXT NOT NULL UNIQUE REFERENCES opportunities(opp_id),
    delivery_task_id TEXT REFERENCES tasks(task_id),
    status TEXT NOT NULL DEFAULT 'open',
    source_ref TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rc_invoices (
    invoice_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    opp_id TEXT NOT NULL REFERENCES opportunities(opp_id),
    external_invoice_id TEXT, amount_due NUMERIC NOT NULL,
    amount_paid NUMERIC NOT NULL DEFAULT 0, issued_at DATE NOT NULL,
    due_at DATE NOT NULL, status TEXT NOT NULL DEFAULT 'issued',
    source_ref TEXT NOT NULL, classification TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id, external_invoice_id)
);

CREATE TABLE IF NOT EXISTS rc_deal_learning (
    learning_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    opp_id TEXT NOT NULL UNIQUE REFERENCES opportunities(opp_id),
    outcome TEXT NOT NULL, outcome_reason TEXT NOT NULL, objection TEXT,
    customer_problem TEXT, customer_language TEXT, winning_offer TEXT,
    sector TEXT, decision_maker TEXT, evidence_ids_json TEXT NOT NULL,
    source_ref TEXT NOT NULL, review_status TEXT NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Zubair Deal Brain — Private Beta فقط.
-- opportunities يبقى سجل الصفقة الوحيد؛ هذه الجداول للمسودة، الهوية، والتتبع.
CREATE TABLE IF NOT EXISTS zubair_capture_drafts (
    draft_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    account_id TEXT NOT NULL REFERENCES user_accounts(account_id),
    input_hash TEXT NOT NULL,
    input_type TEXT NOT NULL,
    raw_text TEXT,
    extracted_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'review',
    processing_ms INTEGER,
    confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    case_link TEXT NOT NULL, asset_link TEXT NOT NULL,
    framework_link TEXT NOT NULL, business_event TEXT NOT NULL,
    UNIQUE(company_id, input_hash)
);

CREATE TABLE IF NOT EXISTS zubair_attachments (
    attachment_id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL REFERENCES zubair_capture_drafts(draft_id) ON DELETE CASCADE,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    filename TEXT NOT NULL, mime_type TEXT, content BYTEA,
    evidence_id TEXT REFERENCES evidence(evidence_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    case_link TEXT NOT NULL, asset_link TEXT NOT NULL,
    framework_link TEXT NOT NULL, business_event TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zubair_contacts (
    contact_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    full_name TEXT NOT NULL, normalized_name TEXT NOT NULL,
    phone TEXT, normalized_phone TEXT, email TEXT, relationship_type TEXT,
    source_ref TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    case_link TEXT NOT NULL, asset_link TEXT NOT NULL,
    framework_link TEXT NOT NULL, business_event TEXT NOT NULL,
    UNIQUE(company_id, normalized_name, normalized_phone)
);

CREATE TABLE IF NOT EXISTS zubair_prospect_companies (
    prospect_company_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    name TEXT NOT NULL, normalized_name TEXT NOT NULL, domain TEXT,
    source_ref TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    case_link TEXT NOT NULL, asset_link TEXT NOT NULL,
    framework_link TEXT NOT NULL, business_event TEXT NOT NULL,
    UNIQUE(company_id, normalized_name)
);

CREATE TABLE IF NOT EXISTS zubair_timeline_events (
    event_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    draft_id TEXT REFERENCES zubair_capture_drafts(draft_id),
    contact_id TEXT REFERENCES zubair_contacts(contact_id),
    prospect_company_id TEXT REFERENCES zubair_prospect_companies(prospect_company_id),
    opp_id TEXT REFERENCES opportunities(opp_id),
    event_type TEXT NOT NULL, summary TEXT NOT NULL, source_ref TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}', actor_id TEXT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
    case_link TEXT NOT NULL, asset_link TEXT NOT NULL,
    framework_link TEXT NOT NULL, business_event TEXT NOT NULL
);
