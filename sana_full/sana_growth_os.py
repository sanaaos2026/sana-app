"""Business Growth OS — طبقة موحّدة للحقائق والـ Baseline والهوية.

هذه الطبقة لا تستبدل جداول Sana الحالية ولا تحوّل الإطار العام إلى تشخيص.
وظيفتها تسجيل ما نعرفه، وما لا نعرفه، ومصدر كل رقم قبل السماح باستخدامه.
"""
import json
import uuid
from datetime import date
from database_config import acquire_schema_lock


GOS_VERSION = "v1.0"
NA_DEFERRED = "N/A — Deferred"

CLASSIFICATIONS = ("Fact", "Assumption", "Forecast", "Opinion", "Conflict")
INFORMATION_TYPES = ("Actual", "Estimate", "Forecast", "Target", "Narrative")
VERIFICATION_STATUSES = ("UNVERIFIED", "VERIFIED", "CONTRADICTED", "STALE", "REJECTED")
SOURCE_CATEGORIES = ("SELF_REPORTED", "SYSTEM", "DOCUMENT", "MARKET", "EXPERT", "UNKNOWN")
_GROWTH_SCHEMA_READY = False

METRIC_DEFINITIONS = [
    ("leads", "Leads", "عدد العملاء المحتملين", "count", "عدد العملاء المحتملين الجدد خلال الفترة"),
    ("qualified_leads", "Qualified Leads", "عدد العملاء المحتملين المؤهلين", "count", "عدد العملاء المحتملين الذين استوفوا معيار التأهيل"),
    ("meetings", "Meetings", "الاجتماعات", "count", "عدد الاجتماعات التجارية المنعقدة"),
    ("proposals", "Proposals", "العروض", "count", "عدد العروض المرسلة"),
    ("close_rate", "Close Rate", "معدل الإغلاق", "percent", "عدد الفرص المغلقة بنجاح ÷ الفرص المغلقة × 100"),
    ("sales_cycle", "Sales Cycle", "دورة البيع", "days", "متوسط الأيام من إنشاء الفرصة إلى إغلاقها"),
    ("average_contract_value", "Average Contract Value", "متوسط قيمة العقد", "currency", "إجمالي قيمة العقود المغلقة ÷ عدد العقود المغلقة"),
    ("revenue", "Revenue", "الإيراد", "currency", "الإيراد المحقق خلال فترة القياس"),
    ("gross_margin", "Gross Margin", "الهامش الإجمالي", "percent", "(الإيراد − التكلفة المباشرة) ÷ الإيراد × 100"),
    ("cac", "CAC", "تكلفة اكتساب العميل", "currency", "تكلفة اكتساب العملاء ÷ عدد العملاء الجدد"),
    ("retention", "Retention", "الاحتفاظ", "percent", "العملاء المحتفظ بهم ÷ العملاء في بداية الفترة × 100"),
    ("collection_rate", "Collection Rate", "معدل التحصيل", "percent", "المبالغ المحصلة ÷ المبالغ المستحقة × 100"),
]
METRIC_KEYS = {item[0] for item in METRIC_DEFINITIONS}

PROJECT_PROFILES = {
    "ecommerce": {
        "label": "E-commerce",
        "label_ar": "التجارة الإلكترونية",
        "stages": ["Acquisition", "Conversion", "Fulfillment", "Retention"],
        "metrics": ["leads", "qualified_leads", "revenue", "gross_margin", "cac", "retention", "collection_rate"],
        "offer_fields": ["product", "price", "channel", "fulfillment_method", "return_policy"],
    },
    "b2b_services": {
        "label": "B2B Services",
        "label_ar": "الخدمات المهنية B2B",
        "stages": ["Opportunity", "Qualification", "Diagnostic", "Baseline", "Proposal", "Delivery", "Collection", "Renewal"],
        "metrics": [key for key, *_ in METRIC_DEFINITIONS],
        "offer_fields": ["service", "target_customer", "scope", "delivery_model", "price", "proof"],
    },
    "consulting": {
        "label": "Consulting",
        "label_ar": "الاستشارات",
        "stages": ["Lead", "Discovery", "Diagnostic", "Proposal", "Engagement", "Outcome", "Renewal"],
        "metrics": ["leads", "qualified_leads", "meetings", "proposals", "close_rate", "sales_cycle", "average_contract_value", "revenue", "gross_margin", "retention", "collection_rate"],
        "offer_fields": ["problem", "outcome", "method", "scope", "price", "case_proof"],
    },
    "agencies": {
        "label": "Agencies",
        "label_ar": "الوكالات",
        "stages": ["Lead", "Qualification", "Proposal", "Won", "Onboarding", "Delivery", "Renewal"],
        "metrics": ["leads", "qualified_leads", "meetings", "proposals", "close_rate", "sales_cycle", "average_contract_value", "revenue", "gross_margin", "cac", "retention", "collection_rate"],
        "offer_fields": ["service_line", "deliverables", "capacity", "timeline", "price", "portfolio_proof"],
    },
    "saas": {
        "label": "SaaS",
        "label_ar": "البرمجيات كخدمة",
        "stages": ["Acquisition", "Activation", "Conversion", "Expansion", "Retention"],
        "metrics": ["leads", "qualified_leads", "revenue", "gross_margin", "cac", "retention", "collection_rate"],
        "offer_fields": ["plan", "activation_event", "price", "usage_limit", "support_level"],
    },
    "real_estate_services": {
        "label": "Real Estate Services",
        "label_ar": "الخدمات العقارية",
        "stages": ["Lead", "Qualification", "Viewing", "Offer", "Contract", "Collection", "Referral"],
        "metrics": ["leads", "qualified_leads", "meetings", "proposals", "close_rate", "sales_cycle", "average_contract_value", "revenue", "gross_margin", "cac", "retention", "collection_rate"],
        "offer_fields": ["property_type", "location", "mandate", "commission", "proof", "timeline"],
    },
}

CANONICAL_MAP = [
    {"entity_type": "customer", "label": "العميل", "source_table": "companies", "source_field": "company_id", "identity_key": "company_id", "status": "available"},
    {"entity_type": "opportunity", "label": "الفرصة", "source_table": "opportunities", "source_field": "opp_id", "identity_key": "opp_id", "status": "available", "note": "لا يوجد كيان Deal مكرر"},
    {"entity_type": "service", "label": "الخدمة", "source_table": "decisions.structured_data", "source_field": "service", "identity_key": "service + company_id", "status": "available"},
    {"entity_type": "offer", "label": "العرض", "source_table": "opportunities", "source_field": "opp_id", "identity_key": "opp_id + stage", "status": "available"},
    {"entity_type": "project", "label": "المشروع", "source_table": "cases", "source_field": "case_id", "identity_key": "case_id", "status": "available"},
    {"entity_type": "invoice", "label": "الفاتورة", "source_table": NA_DEFERRED, "source_field": NA_DEFERRED, "identity_key": "external_invoice_id + company_id", "status": "deferred"},
    {"entity_type": "channel", "label": "القناة", "source_table": "leads.source", "source_field": "source", "identity_key": "normalized_channel + company_id", "status": "available"},
    {"entity_type": "file", "label": "الملف", "source_table": "research_sources", "source_field": "research_source_id", "identity_key": "research_source_id", "status": "available"},
    {"entity_type": "relationship", "label": "العلاقة", "source_table": "users/user_accounts", "source_field": "user_id/account_id", "identity_key": "relationship_type + external_party + company_id", "status": "available"},
]
CANONICAL_TYPES = {item["entity_type"] for item in CANONICAL_MAP}
SOURCE_TABLES = {"companies", "opportunities", "cases", "leads", "research_sources", "users", "user_accounts"}

# وثيقة الخانات الخمس لكل سجل بنيوي جديد.
GOVERNANCE_RECORDS = [
    ("truth_record", "gos_truth_records", "case_id", "asset_id", "framework:B2B-OS-001", "truth.recorded"),
    ("baseline", "gos_baselines", "case_id", "asset_id", "framework:B2B-OS-001", "baseline.recorded"),
    ("baseline_metric", "gos_baseline_metrics", "baseline_id", "asset_id", "framework:B2B-OS-001", "baseline.metric.recorded"),
    ("metric_definition", "gos_metric_definitions", NA_DEFERRED, NA_DEFERRED, "framework:B2B-OS-001", "metric.defined"),
    ("project_profile", "gos_project_profiles", NA_DEFERRED, NA_DEFERRED, "framework:B2B-OS-001", "profile.versioned"),
    ("company_profile", "gos_company_profiles", "company_id", NA_DEFERRED, "framework:B2B-OS-001", "profile.selected"),
    ("canonical_entity", "gos_canonical_entities", "case_id", "asset_id", "framework:B2B-OS-001", "entity.mapped"),
    ("canonical_merge", "gos_canonical_merges", "case_id", "asset_id", "framework:B2B-OS-001", "entity.merged"),
    ("zubair_deal_brain", "zubair_*", NA_DEFERRED, NA_DEFERRED, "framework:B2B-OS-001", "zubair.deal_brain.recorded"),
]


def _json(value):
    return json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value


def _loads(value, default=None):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _iso(value):
    if isinstance(value, date):
        return value.isoformat()
    value = (str(value or "")).strip()
    if not value:
        raise ValueError("observed_at مطلوب")
    try:
        date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ValueError("observed_at يجب أن يكون تاريخًا بصيغة YYYY-MM-DD") from exc
    return value[:10]


def _confidence(value):
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence يجب أن يكون رقمًا بين 0 و100") from exc
    if not 0 <= value <= 100:
        raise ValueError("confidence يجب أن يكون رقمًا بين 0 و100")
    return value


def _required_source(value):
    value = (str(value or "")).strip()
    if not value or value == NA_DEFERRED:
        raise ValueError("source_ref مطلوب لكل حقيقة أو رقم")
    return value


def ensure_schema(db):
    """ترقية إضافية فقط؛ لا تسقط جداول أو بيانات قائمة."""
    global _GROWTH_SCHEMA_READY
    if _GROWTH_SCHEMA_READY:
        return
    ready = db.execute(
        """SELECT
          EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='gos_truth_records'
              AND column_name='verification_status'
          ) AS truth_ready,
          EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='gos_baselines'
              AND column_name='comparison_start'
          ) AS baseline_ready,
          EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='gos_baseline_metrics'
              AND column_name='information_type'
          ) AS metrics_ready"""
    ).fetchone()
    if (
        ready
        and bool(ready["truth_ready"])
        and bool(ready["baseline_ready"])
        and bool(ready["metrics_ready"])
    ):
        _GROWTH_SCHEMA_READY = True
        return
    acquire_schema_lock(db)
    db.execute("""CREATE TABLE IF NOT EXISTS gos_governance_registry (
        governance_id TEXT PRIMARY KEY,
        record_type TEXT UNIQUE NOT NULL,
        storage_destination TEXT NOT NULL,
        case_link TEXT NOT NULL,
        asset_link TEXT NOT NULL,
        framework_link TEXT NOT NULL,
        business_event TEXT NOT NULL,
        version TEXT NOT NULL DEFAULT 'v1.0',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS gos_truth_records (
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
        information_type TEXT NOT NULL DEFAULT 'Actual',
        verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
        source_category TEXT NOT NULL DEFAULT 'UNKNOWN',
        period_start DATE,
        period_end DATE,
        governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_gos_truth_company ON gos_truth_records(company_id, observed_at DESC)")
    db.execute("""CREATE TABLE IF NOT EXISTS gos_baselines (
        baseline_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        case_id TEXT REFERENCES cases(case_id),
        period_start DATE NOT NULL,
        period_end DATE NOT NULL,
        source_ref TEXT NOT NULL,
        observed_at DATE NOT NULL,
        confidence INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
        status TEXT NOT NULL CHECK (status IN ('complete','incomplete')),
        comparison_start DATE,
        comparison_end DATE,
        seasonality_context TEXT,
        governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (period_end >= period_start)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS gos_metric_definitions (
        metric_key TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        label_ar TEXT NOT NULL,
        unit TEXT NOT NULL,
        formula TEXT NOT NULL,
        governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS gos_baseline_metrics (
        metric_id TEXT PRIMARY KEY,
        baseline_id TEXT NOT NULL REFERENCES gos_baselines(baseline_id) ON DELETE CASCADE,
        metric_key TEXT NOT NULL REFERENCES gos_metric_definitions(metric_key),
        value_numeric NUMERIC,
        unit TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        observed_at DATE NOT NULL,
        confidence INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
        classification TEXT NOT NULL CHECK (classification IN ('Fact','Assumption','Forecast','Opinion','Conflict')),
        information_type TEXT NOT NULL DEFAULT 'Actual',
        verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
        source_category TEXT NOT NULL DEFAULT 'UNKNOWN',
        availability TEXT NOT NULL CHECK (availability IN ('available','unavailable')),
        governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
        UNIQUE(baseline_id, metric_key)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS gos_project_profiles (
        profile_key TEXT NOT NULL,
        version TEXT NOT NULL,
        label TEXT NOT NULL,
        label_ar TEXT NOT NULL,
        stages_json TEXT NOT NULL,
        metrics_json TEXT NOT NULL,
        offer_fields_json TEXT NOT NULL,
        governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
        PRIMARY KEY(profile_key, version)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS gos_company_profiles (
        company_id TEXT PRIMARY KEY REFERENCES companies(company_id),
        profile_key TEXT NOT NULL,
        profile_version TEXT NOT NULL,
        selected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        source_ref TEXT NOT NULL,
        governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
        FOREIGN KEY (profile_key, profile_version) REFERENCES gos_project_profiles(profile_key, version)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS gos_canonical_entities (
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
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS gos_canonical_merges (
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
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_gos_canonical_company ON gos_canonical_entities(company_id, entity_type)")
    truth_columns = {
        row["column_name"] for row in db.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name='gos_truth_records'"""
        ).fetchall()
    }
    for column, definition in {
        "information_type": "TEXT NOT NULL DEFAULT 'Actual'",
        "verification_status": "TEXT NOT NULL DEFAULT 'UNVERIFIED'",
        "source_category": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "period_start": "DATE",
        "period_end": "DATE",
    }.items():
        if column not in truth_columns:
            db.execute(f"ALTER TABLE gos_truth_records ADD COLUMN {column} {definition}")
    baseline_columns = {
        row["column_name"] for row in db.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name='gos_baselines'"""
        ).fetchall()
    }
    for column, definition in {
        "comparison_start": "DATE",
        "comparison_end": "DATE",
        "seasonality_context": "TEXT",
    }.items():
        if column not in baseline_columns:
            db.execute(f"ALTER TABLE gos_baselines ADD COLUMN {column} {definition}")
    metric_columns = {
        row["column_name"] for row in db.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name='gos_baseline_metrics'"""
        ).fetchall()
    }
    for column, definition in {
        "information_type": "TEXT NOT NULL DEFAULT 'Actual'",
        "verification_status": "TEXT NOT NULL DEFAULT 'UNVERIFIED'",
        "source_category": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
    }.items():
        if column not in metric_columns:
            db.execute(f"ALTER TABLE gos_baseline_metrics ADD COLUMN {column} {definition}")
    _GROWTH_SCHEMA_READY = True


def seed_growth_os(db):
    """تهيئة versioned profiles والتعريفات بدون تكرار."""
    for record_type, storage, case_link, asset_link, framework_link, event in GOVERNANCE_RECORDS:
        governance_id = f"GOS-{record_type.upper()}"
        db.execute("""INSERT INTO gos_governance_registry
            (governance_id, record_type, storage_destination, case_link, asset_link, framework_link, business_event)
            VALUES (?,?,?,?,?,?,?) ON CONFLICT (record_type) DO NOTHING""",
            (governance_id, record_type, storage, case_link, asset_link, framework_link, event))
    metric_governance = "GOS-METRIC_DEFINITION"
    for key, label, label_ar, unit, formula in METRIC_DEFINITIONS:
        db.execute("""INSERT INTO gos_metric_definitions
            (metric_key, label, label_ar, unit, formula, governance_id)
            VALUES (?,?,?,?,?,?) ON CONFLICT (metric_key) DO NOTHING""",
            (key, label, label_ar, unit, formula, metric_governance))
    profile_governance = "GOS-PROJECT_PROFILE"
    for key, profile in PROJECT_PROFILES.items():
        db.execute("""INSERT INTO gos_project_profiles
            (profile_key, version, label, label_ar, stages_json, metrics_json, offer_fields_json, governance_id)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (profile_key, version) DO NOTHING""",
            (key, GOS_VERSION, profile["label"], profile["label_ar"], _json(profile["stages"]),
             _json(profile["metrics"]), _json(profile["offer_fields"]), profile_governance))


def list_profiles(db):
    rows = db.execute("""SELECT * FROM gos_project_profiles ORDER BY profile_key""").fetchall()
    return [{**dict(row), "stages": _loads(row["stages_json"], []),
             "metrics": _loads(row["metrics_json"], []),
             "offer_fields": _loads(row["offer_fields_json"], [])} for row in rows]


def get_company_profile(db, company_id):
    row = db.execute("""SELECT p.*, cp.selected_at, cp.source_ref AS selection_source
                       FROM gos_project_profiles p
                       LEFT JOIN gos_company_profiles cp
                         ON cp.profile_key=p.profile_key AND cp.profile_version=p.version AND cp.company_id=?
                       WHERE p.profile_key=COALESCE(cp.profile_key, 'b2b_services')
                         AND p.version=COALESCE(cp.profile_version, ?)""",
                    (company_id, GOS_VERSION)).fetchone()
    if not row:
        raise ValueError("Growth OS profiles غير مهيأة")
    data = dict(row)
    data["stages"] = _loads(data.pop("stages_json"), [])
    data["metrics"] = _loads(data.pop("metrics_json"), [])
    data["offer_fields"] = _loads(data.pop("offer_fields_json"), [])
    return data


def set_company_profile(db, company_id, profile_key, source_ref="company:profile-selection"):
    if profile_key not in PROJECT_PROFILES:
        raise ValueError("نوع المشروع غير مدعوم")
    if not db.execute("SELECT 1 FROM companies WHERE company_id=?", (company_id,)).fetchone():
        raise LookupError("COMPANY_NOT_FOUND")
    source_ref = _required_source(source_ref)
    db.execute("""INSERT INTO gos_company_profiles
        (company_id, profile_key, profile_version, source_ref, governance_id)
        VALUES (?,?,?,?,?)
        ON CONFLICT (company_id) DO UPDATE SET
          profile_key=EXCLUDED.profile_key, profile_version=EXCLUDED.profile_version,
          selected_at=now(), source_ref=EXCLUDED.source_ref""",
        (company_id, profile_key, GOS_VERSION, source_ref, "GOS-COMPANY_PROFILE"))
    return get_company_profile(db, company_id)


def create_truth_record(db, company_id, payload):
    classification = payload.get("classification")
    if classification not in CLASSIFICATIONS:
        raise ValueError("classification غير صالح")
    subject = str(payload.get("subject") or "").strip()
    if not subject:
        raise ValueError("subject مطلوب")
    source_ref = _required_source(payload.get("source_ref"))
    observed_at = _iso(payload.get("observed_at"))
    confidence = _confidence(payload.get("confidence"))
    information_type = payload.get("information_type", "Actual")
    verification_status = payload.get("verification_status", "UNVERIFIED")
    source_category = payload.get("source_category", "UNKNOWN")
    if information_type not in INFORMATION_TYPES:
        raise ValueError("information_type غير صالح")
    if verification_status not in VERIFICATION_STATUSES:
        raise ValueError("verification_status غير صالح")
    if source_category not in SOURCE_CATEGORIES:
        raise ValueError("source_category غير صالح")
    period_start = _iso(payload.get("period_start")) if payload.get("period_start") else None
    period_end = _iso(payload.get("period_end")) if payload.get("period_end") else None
    if bool(period_start) != bool(period_end):
        raise ValueError("period_start وperiod_end مطلوبان معًا")
    if period_start and period_end < period_start:
        raise ValueError("period_end يجب أن يساوي أو يتجاوز period_start")
    case_id = payload.get("case_id") or None
    if case_id and not db.execute("SELECT 1 FROM cases WHERE case_id=? AND company_id=?", (case_id, company_id)).fetchone():
        raise ValueError("case_id لا يتبع الشركة")
    truth_id = str(uuid.uuid4())
    db.execute("""INSERT INTO gos_truth_records
        (truth_id, company_id, case_id, asset_id, subject, value_json, unit, classification,
         source_ref, observed_at, confidence, information_type, verification_status,
         source_category, period_start, period_end, governance_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (truth_id, company_id, case_id, payload.get("asset_id") or None, subject, _json(payload.get("value")),
         payload.get("unit"), classification, source_ref, observed_at, confidence, information_type,
         verification_status, source_category, period_start, period_end, "GOS-TRUTH_RECORD"))
    return dict(db.execute("SELECT * FROM gos_truth_records WHERE truth_id=?", (truth_id,)).fetchone())


def list_truth_records(db, company_id):
    rows = db.execute("SELECT * FROM gos_truth_records WHERE company_id=? ORDER BY observed_at DESC, created_at DESC", (company_id,)).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        data["value"] = _loads(data.pop("value_json"), data.get("value_json"))
        result.append(data)
    return result


def create_baseline(db, company_id, payload):
    period_start = _iso(payload.get("period_start"))
    period_end = _iso(payload.get("period_end"))
    if period_end < period_start:
        raise ValueError("period_end يجب أن يساوي أو يتجاوز period_start")
    comparison_start = _iso(payload.get("comparison_start")) if payload.get("comparison_start") else None
    comparison_end = _iso(payload.get("comparison_end")) if payload.get("comparison_end") else None
    if bool(comparison_start) != bool(comparison_end):
        raise ValueError("comparison_start وcomparison_end مطلوبان معًا")
    if comparison_start and comparison_end < comparison_start:
        raise ValueError("comparison_end يجب أن يساوي أو يتجاوز comparison_start")
    baseline_source = _required_source(payload.get("source_ref"))
    observed_at = _iso(payload.get("observed_at"))
    confidence = _confidence(payload.get("confidence"))
    metrics = payload.get("metrics") or {}
    case_id = payload.get("case_id") or None
    if case_id and not db.execute("SELECT 1 FROM cases WHERE case_id=? AND company_id=?", (case_id, company_id)).fetchone():
        raise ValueError("case_id لا يتبع الشركة")
    if isinstance(metrics, list):
        metrics = {item.get("metric_key"): item for item in metrics}
    baseline_id = str(uuid.uuid4())
    rows = []
    complete = True
    for key, label, label_ar, unit, formula in METRIC_DEFINITIONS:
        item = metrics.get(key) or {}
        raw_value = item.get("value") if isinstance(item, dict) else item
        available = raw_value is not None and raw_value != ""
        if available:
            try:
                value = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"قيمة {key} يجب أن تكون رقمية") from exc
            source = _required_source(item.get("source_ref") if isinstance(item, dict) else None)
            metric_date = _iso((item.get("observed_at") or observed_at) if isinstance(item, dict) else observed_at)
            metric_confidence = _confidence(item.get("confidence", confidence) if isinstance(item, dict) else confidence)
            classification = item.get("classification", "Fact") if isinstance(item, dict) else "Fact"
            if classification not in CLASSIFICATIONS:
                raise ValueError(f"classification غير صالح للمؤشر {key}")
            information_type = item.get("information_type", "Actual") if isinstance(item, dict) else "Actual"
            verification_status = item.get("verification_status", "UNVERIFIED") if isinstance(item, dict) else "UNVERIFIED"
            source_category = item.get("source_category", "UNKNOWN") if isinstance(item, dict) else "UNKNOWN"
            if information_type not in INFORMATION_TYPES:
                raise ValueError(f"information_type غير صالح للمؤشر {key}")
            if verification_status not in VERIFICATION_STATUSES:
                raise ValueError(f"verification_status غير صالح للمؤشر {key}")
            if source_category not in SOURCE_CATEGORIES:
                raise ValueError(f"source_category غير صالح للمؤشر {key}")
        else:
            complete = False
            value, source, metric_date, metric_confidence, classification = None, NA_DEFERRED, observed_at, confidence, "Fact"
            information_type, verification_status, source_category = "Actual", "UNVERIFIED", "UNKNOWN"
        rows.append((key, value, unit, source, metric_date, metric_confidence, classification,
                     information_type, verification_status, source_category,
                     "available" if available else "unavailable"))
    db.execute("""INSERT INTO gos_baselines
        (baseline_id, company_id, case_id, period_start, period_end, source_ref, observed_at,
         confidence, status, comparison_start, comparison_end, seasonality_context, governance_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (baseline_id, company_id, payload.get("case_id") or None, period_start, period_end, baseline_source,
         observed_at, confidence, "complete" if complete else "incomplete", comparison_start,
         comparison_end, payload.get("seasonality_context") or None, "GOS-BASELINE"))
    for key, value, unit, source, metric_date, metric_confidence, classification, information_type, verification_status, source_category, availability in rows:
        db.execute("""INSERT INTO gos_baseline_metrics
            (metric_id, baseline_id, metric_key, value_numeric, unit, source_ref, observed_at,
             confidence, classification, information_type, verification_status, source_category,
             availability, governance_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), baseline_id, key, value, unit, source, metric_date, metric_confidence,
             classification, information_type, verification_status, source_category,
             availability, "GOS-BASELINE_METRIC"))
    return get_baseline(db, company_id, baseline_id)


def require_usable_baseline(db, company_id, baseline_id):
    """حارس مشترك للتجارب والقرارات القادمة؛ لا يسمح بقراءة baseline ناقص كمدخل قرار."""
    baseline = get_baseline(db, company_id, baseline_id)
    if not baseline["usable"]:
        raise ValueError("BASELINE_INCOMPLETE: لا يمكن استخدام Baseline غير مكتمل أو رقم بلا مصدر")
    return baseline


def get_baseline(db, company_id, baseline_id):
    baseline = db.execute("SELECT * FROM gos_baselines WHERE baseline_id=? AND company_id=?", (baseline_id, company_id)).fetchone()
    if not baseline:
        raise LookupError("BASELINE_NOT_FOUND")
    metrics = db.execute("""SELECT m.*, d.label, d.label_ar, d.formula
                            FROM gos_baseline_metrics m JOIN gos_metric_definitions d USING(metric_key)
                            WHERE m.baseline_id=? ORDER BY d.metric_key""", (baseline_id,)).fetchall()
    result = dict(baseline)
    result["metrics"] = [dict(row) for row in metrics]
    result["usable"] = result["status"] == "complete" and all(
        row["availability"] == "available"
        and row["source_ref"] != NA_DEFERRED
        and row["information_type"] == "Actual"
        and row["verification_status"] == "VERIFIED"
        and row["source_category"] not in {"SELF_REPORTED", "UNKNOWN"}
        for row in metrics
    )
    return result


def list_baselines(db, company_id):
    rows = db.execute("SELECT baseline_id FROM gos_baselines WHERE company_id=? ORDER BY period_end DESC, created_at DESC", (company_id,)).fetchall()
    return [get_baseline(db, company_id, row["baseline_id"]) for row in rows]


def register_canonical_entity(db, company_id, payload):
    entity_type = payload.get("entity_type")
    if entity_type not in CANONICAL_TYPES:
        raise ValueError("entity_type غير صالح")
    source_table = payload.get("source_table")
    source_id = str(payload.get("source_id") or "").strip()
    identity_key = str(payload.get("identity_key") or "").strip()
    if source_table not in SOURCE_TABLES or not source_id or not identity_key:
        raise ValueError("source_table وsource_id وidentity_key مطلوبة من خريطة canonical")
    if source_table in {"companies", "opportunities", "cases", "leads"}:
        if not db.execute(f"SELECT 1 FROM {source_table} WHERE company_id=? AND {('company_id' if source_table == 'companies' else {'opportunities':'opp_id','cases':'case_id','leads':'lead_id'}[source_table])}=?",
                          (company_id, company_id if source_table == "companies" else source_id)).fetchone():
            raise ValueError("المصدر غير موجود أو لا يتبع الشركة")
    existing = db.execute("""SELECT * FROM gos_canonical_entities
                             WHERE company_id=? AND entity_type=?
                               AND (identity_key=? OR (source_table=? AND source_id=?))""",
                          (company_id, entity_type, identity_key, source_table, source_id)).fetchone()
    if existing:
        if existing["source_table"] == source_table and existing["source_id"] == source_id:
            if existing["identity_key"] == identity_key:
                return dict(existing), False
            raise ValueError("DUPLICATE_CANONICAL_ENTITY: المصدر مسجل بهوية مختلفة")
        raise ValueError("DUPLICATE_CANONICAL_ENTITY: identity_key مستخدم لمصدر آخر")
    canonical_id = str(uuid.uuid4())
    db.execute("""INSERT INTO gos_canonical_entities
        (canonical_id, company_id, entity_type, source_table, source_id, identity_key, display_name, case_id, asset_id, governance_id)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (canonical_id, company_id, entity_type, source_table, source_id, identity_key, payload.get("display_name"),
         payload.get("case_id"), payload.get("asset_id"), "GOS-CANONICAL_ENTITY"))
    return dict(db.execute("SELECT * FROM gos_canonical_entities WHERE canonical_id=?", (canonical_id,)).fetchone()), True


def list_canonical_entities(db, company_id):
    return [dict(row) for row in db.execute(
        "SELECT * FROM gos_canonical_entities WHERE company_id=? AND merged_into_id IS NULL ORDER BY created_at DESC", (company_id,)).fetchall()]


def merge_canonical_entity(db, company_id, from_id, to_id, reason, source_ref):
    if not str(reason or "").strip():
        raise ValueError("سبب الدمج مطلوب")
    source_ref = _required_source(source_ref)
    rows = db.execute("""SELECT * FROM gos_canonical_entities
                        WHERE canonical_id IN (?,?) AND company_id=?""", (from_id, to_id, company_id)).fetchall()
    if len(rows) != 2:
        raise LookupError("CANONICAL_ENTITY_NOT_FOUND")
    db.execute("UPDATE gos_canonical_entities SET merged_into_id=? WHERE canonical_id=?", (to_id, from_id))
    merge_id = str(uuid.uuid4())
    db.execute("""INSERT INTO gos_canonical_merges
        (merge_id, company_id, from_canonical_id, to_canonical_id, reason, source_ref, governance_id)
        VALUES (?,?,?,?,?,?,?)""",
        (merge_id, company_id, from_id, to_id, reason.strip(), source_ref, "GOS-CANONICAL_MERGE"))
    return dict(db.execute("SELECT * FROM gos_canonical_merges WHERE merge_id=?", (merge_id,)).fetchone())