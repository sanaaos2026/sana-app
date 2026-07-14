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
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))
);

-- حسابات دخول حقيقية للعملاء — كل حساب مرتبط بشركة واحدة فقط، ولا يمكنه
-- أبدًا رؤية بيانات أي شركة أخرى (العزل يُفرض في طبقة الخادم عبر الجلسة).
CREATE TABLE IF NOT EXISTS user_accounts (
    account_id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    company_id TEXT NOT NULL,
    referral_source TEXT,
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
    FOREIGN KEY (company_id) REFERENCES companies(company_id)
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
    FOREIGN KEY (company_id) REFERENCES companies(company_id),
    FOREIGN KEY (case_id) REFERENCES cases(case_id)
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
    due_date TEXT,
    status TEXT DEFAULT 'لم تبدأ',
    priority TEXT,
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
    completed_at TEXT,
    phase_label TEXT,
    value_note TEXT,
    FOREIGN KEY (company_id) REFERENCES companies(company_id),
    FOREIGN KEY (decision_id) REFERENCES decisions(decision_id)
);

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
    created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))
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
