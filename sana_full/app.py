"""
سنع — الخادم الأساسي (MVP الحقيقي)
Sana Core Backend — Flask + SQLite

تشغيل:
    python3 app.py
ثم افتح المتصفح على:
    http://localhost:5000
"""
import psycopg2
import psycopg2.extras
import os
import json
import uuid
import secrets
import hashlib
import hmac
import decimal
import re
import time
import html
from urllib.parse import urlencode, urlparse
from datetime import datetime, date, timedelta
from flask import Flask, jsonify, request, render_template, g, session, redirect, url_for, Response, abort, send_file
from flask.json.provider import DefaultJSONProvider
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFError, CSRFProtect
import resend
from database_config import (
    acquire_schema_lock,
    BILLING_TEST_SCHEMA_PREFIX,
    build_billing_test_schema_name,
    has_required_columns,
    has_required_tables,
    parse_billing_test_schema_created_at,
    resolve_database_url,
    resolve_database_schema,
)
# weasyprint يُستورد داخل الدالة فقط لتفادي crash عند غياب libpango وقت التشغيل

# ------------------------------------------------------------------
# قائمة القطاعات الثابتة — مرجع مشترك بين الخادم والعميل
# ------------------------------------------------------------------
SECTORS = [
    {"key": "legal",          "label": "قانوني / محاماة",   "icon": "⚖️"},
    {"key": "food",           "label": "مطاعم وضيافة",       "icon": "🍽️"},
    {"key": "manufacturing",  "label": "تصنيع وعطور",        "icon": "🏭"},
    {"key": "retail",         "label": "تجارة تجزئة",        "icon": "🛒"},
    {"key": "construction",   "label": "مقاولات وبناء",      "icon": "🏗️"},
    {"key": "tech",           "label": "تقنية وبرمجيات",     "icon": "💻"},
    {"key": "consulting",     "label": "استشارات إدارية",    "icon": "📊"},
    {"key": "realestate",     "label": "عقارات",              "icon": "🏢"},
    {"key": "health",         "label": "صحة وطب",            "icon": "🏥"},
    {"key": "education",      "label": "تعليم وتدريب",       "icon": "📚"},
    {"key": "other",          "label": "أخرى",                "icon": "⚡"},
]
SECTOR_KEYS = {s["key"] for s in SECTORS}

SECTOR_BUSINESS_TYPES = {
    "legal": [
        {"key": "law_firm", "label": "مكتب أو شركة محاماة"},
        {"key": "independent_lawyer", "label": "محامٍ أو مستشار قانوني مستقل"},
        {"key": "legal_consulting", "label": "استشارات قانونية وامتثال"},
        {"key": "legal_tech", "label": "منصة أو تقنية قانونية"},
    ],
    "food": [
        {"key": "restaurant_cafe", "label": "مطعم أو مقهى"},
        {"key": "cloud_kitchen", "label": "مطبخ سحابي"},
        {"key": "catering", "label": "تموين وضيافة"},
        {"key": "food_brand", "label": "علامة أو تصنيع غذائي"},
        {"key": "hotel_hospitality", "label": "فندق أو منشأة ضيافة"},
    ],
    "manufacturing": [
        {"key": "factory", "label": "مصنع"},
        {"key": "workshop", "label": "ورشة إنتاج"},
        {"key": "perfume_cosmetics", "label": "عطور أو مستحضرات تجميل"},
        {"key": "private_label", "label": "تصنيع للغير / علامة خاصة"},
        {"key": "industrial_supplier", "label": "مورد صناعي"},
    ],
    "retail": [
        {"key": "physical_store", "label": "متجر فعلي"},
        {"key": "ecommerce", "label": "متجر إلكتروني"},
        {"key": "omnichannel", "label": "متجر فعلي وإلكتروني"},
        {"key": "wholesale", "label": "توزيع أو جملة"},
        {"key": "marketplace", "label": "منصة سوق متعددة البائعين"},
    ],
    "construction": [
        {"key": "main_contractor", "label": "مقاول رئيسي"},
        {"key": "subcontractor", "label": "مقاول متخصص أو باطن"},
        {"key": "engineering_office", "label": "مكتب هندسي"},
        {"key": "project_management", "label": "إدارة مشاريع"},
        {"key": "building_supplier", "label": "مواد ومستلزمات بناء"},
    ],
    "tech": [
        {"key": "saas", "label": "منتج SaaS"},
        {"key": "software_company", "label": "شركة تطوير برمجيات"},
        {"key": "tech_consulting", "label": "استشارات تقنية"},
        {"key": "digital_platform", "label": "منصة أو سوق رقمي"},
        {"key": "managed_it", "label": "خدمات تقنية أو أمن سيبراني مُدارة"},
    ],
    "consulting": [
        {"key": "management_consulting", "label": "استشارات إدارية"},
        {"key": "marketing_agency", "label": "وكالة تسويق أو إبداع"},
        {"key": "finance_accounting", "label": "محاسبة أو استشارات مالية"},
        {"key": "hr_recruitment", "label": "موارد بشرية أو توظيف"},
        {"key": "training_advisory", "label": "تدريب أو استشارات متخصصة"},
    ],
    "realestate": [
        {"key": "property_platform", "label": "منصة عقارية"},
        {"key": "brokerage_office", "label": "مكتب وساطة عقارية"},
        {"key": "developer", "label": "مطور عقاري"},
        {"key": "property_management", "label": "إدارة أملاك"},
        {"key": "valuation_consulting", "label": "تقييم أو استشارات عقارية"},
        {"key": "facilities_management", "label": "إدارة مرافق"},
    ],
    "health": [
        {"key": "clinic", "label": "عيادة"},
        {"key": "medical_center", "label": "مجمع أو مركز طبي"},
        {"key": "hospital", "label": "مستشفى"},
        {"key": "pharmacy", "label": "صيدلية أو سلسلة صيدليات"},
        {"key": "lab", "label": "مختبر أو مركز تشخيص"},
        {"key": "digital_home_health", "label": "صحة رقمية أو رعاية منزلية"},
    ],
    "education": [
        {"key": "school", "label": "مدرسة أو روضة"},
        {"key": "training_center", "label": "مركز تدريب"},
        {"key": "academy", "label": "أكاديمية متخصصة"},
        {"key": "edtech", "label": "منصة تعليمية تقنية"},
        {"key": "independent_trainer", "label": "مدرب أو معلم مستقل"},
    ],
    "other": [
        {"key": "service_business", "label": "شركة خدمات"},
        {"key": "office_agency", "label": "مكتب أو وكالة"},
        {"key": "platform", "label": "منصة رقمية"},
        {"key": "store", "label": "متجر"},
        {"key": "manufacturer", "label": "مصنّع أو منتج"},
        {"key": "independent", "label": "مهني مستقل"},
    ],
}

RESPONDENT_ROLES = [
    {"key": "owner_founder", "label": "مالك أو مؤسس"},
    {"key": "ceo_general_manager", "label": "رئيس تنفيذي أو مدير عام"},
    {"key": "department_manager", "label": "مدير إدارة"},
    {"key": "operations", "label": "مسؤول تشغيل"},
    {"key": "sales_marketing", "label": "مسؤول مبيعات أو تسويق"},
    {"key": "advisor_other", "label": "مستشار أو صفة أخرى"},
]


"""
سنع — الخادم الأساسي (MVP الحقيقي)
Sana Core Backend — Flask + SQLite

تشغيل:
    python3 app.py
ثم افتح المتصفح على:
    http://localhost:5000
"""
import psycopg2
import psycopg2.extras
import os
import json
import uuid
import secrets
import hashlib
import hmac
import decimal
import re
import time
from urllib.parse import urlparse, urlencode
from datetime import datetime, date, timedelta
from flask import Flask, jsonify, request, render_template, g, session, redirect, url_for, Response, abort, send_file
from flask.json.provider import DefaultJSONProvider
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect
import resend
from database_config import acquire_schema_lock, resolve_database_url
# weasyprint يُستورد داخل الدالة فقط لتفادي crash عند غياب libpango وقت التشغيل

# ------------------------------------------------------------------
# قائمة القطاعات الثابتة — مرجع مشترك بين الخادم والعميل
# ------------------------------------------------------------------
SECTORS = [
    {"key": "legal",          "label": "قانوني / محاماة",   "icon": "⚖️"},
    {"key": "food",           "label": "مطاعم وضيافة",       "icon": "🍽️"},
    {"key": "manufacturing",  "label": "تصنيع وعطور",        "icon": "🏭"},
    {"key": "retail",         "label": "تجارة تجزئة",        "icon": "🛒"},
    {"key": "construction",   "label": "مقاولات وبناء",      "icon": "🏗️"},
    {"key": "tech",           "label": "تقنية وبرمجيات",     "icon": "💻"},
    {"key": "consulting",     "label": "استشارات إدارية",    "icon": "📊"},
    {"key": "realestate",     "label": "عقارات",              "icon": "🏢"},
    {"key": "health",         "label": "صحة وطب",            "icon": "🏥"},
    {"key": "education",      "label": "تعليم وتدريب",       "icon": "📚"},
    {"key": "other",          "label": "أخرى",                "icon": "⚡"},
]
SECTOR_KEYS = {s["key"] for s in SECTORS}

# ------------------------------------------------------------------
# FIN-001 — بوابة القيمة المالية
# ------------------------------------------------------------------
# هذه السياسة تصف ما يلزم قبل أن يصبح من الآمن تحويل دليل مالي إلى قيمة
# معروضة. وجود إيراد معلن أو قيمة صفقة في سجل قديم لا يفتح البوابة.
FINANCIAL_VALUE_POLICY_VERSION = "FIN-001 v1.0"
FINANCIAL_VALUE_POLICY_STATUS = "APPROVED_GATE"
FINANCIAL_VALUE_STATUS = "DEFERRED"
FINANCIAL_VALUE_FIELDS = frozenset({"current_value", "potential_value", "value_gap"})
FINANCIAL_SOURCE_RULES = {
    "AUDITED_FINANCIAL_STATEMENT": {
        "label": "قوائم مالية مدققة",
        "minimum_confidence": 90,
        "validity_days": 395,
        "role": "primary",
        "information_types": frozenset({"Actual"}),
    },
    "ACCOUNTING_LEDGER_EXPORT": {
        "label": "تصدير دفتر محاسبي أو ERP",
        "minimum_confidence": 90,
        "validity_days": 95,
        "role": "primary",
        "information_types": frozenset({"Actual"}),
    },
    "BANK_OR_PAYMENT_PROCESSOR_STATEMENT": {
        "label": "كشف بنكي أو تقرير مزود دفع",
        "minimum_confidence": 85,
        "validity_days": 45,
        "role": "corroborating",
        "information_types": frozenset({"Actual"}),
    },
    "INVOICE_REGISTER": {
        "label": "سجل فواتير قابل للمطابقة",
        "minimum_confidence": 85,
        "validity_days": 45,
        "role": "corroborating",
        "information_types": frozenset({"Actual"}),
    },
    "SIGNED_CONTRACT": {
        "label": "عقد موقّع",
        "minimum_confidence": 80,
        "validity_days": 95,
        "role": "corroborating",
        "information_types": frozenset({"Actual", "Forecast"}),
    },
    "MARKET_BENCHMARK": {
        "label": "مقارنة سوقية منشورة",
        "minimum_confidence": 70,
        "validity_days": 185,
        "role": "context_only",
        "information_types": frozenset({"Actual", "Estimate"}),
    },
}


def _financial_date(value):
    """Parse the ISO date prefix used by both SQLite and PostgreSQL."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def financial_value_gate(
    *,
    methodology_approved=False,
    sources=None,
    human_reviewed=False,
    today=None,
):
    """Return the auditable FIN-001 gate without calculating a monetary value.

    This is intentionally a gate, not a valuation function. It allows a future
    approved implementation to prove its prerequisites without allowing
    legacy revenue fields or an unverified forecast to become a report value.
    """
    today = today or date.today()
    sources = list(sources or [])
    accepted = []
    rejected = []
    for source in sources:
        source = dict(source or {})
        rule = FINANCIAL_SOURCE_RULES.get(str(source.get("source_type") or ""))
        reason = None
        try:
            confidence = int(source["confidence"])
        except (KeyError, TypeError, ValueError):
            confidence = None
        if not rule:
            reason = "مصدر مالي غير مقبول"
        elif source.get("verification_status") != "VERIFIED":
            reason = "المصدر غير متحقق"
        elif source.get("information_type") not in rule["information_types"]:
            reason = "نوع المعلومة غير مقبول لهذا المصدر"
        elif not source.get("source_ref"):
            reason = "مرجع المصدر مفقود"
        elif confidence is None or confidence < rule["minimum_confidence"]:
            reason = f"الثقة أقل من {rule['minimum_confidence']}٪"
        else:
            observed = _financial_date(
                source.get("observed_at")
                or source.get("date_collected")
                or source.get("period_end")
            )
            if not observed:
                reason = "تاريخ المصدر مفقود أو غير صالح"
            elif observed > today:
                reason = "تاريخ المصدر في المستقبل"
            elif (today - observed).days > rule["validity_days"]:
                reason = f"المصدر أقدم من مدة الصلاحية ({rule['validity_days']} يومًا)"
        if reason:
            rejected.append({
                "source_ref": source.get("source_ref"),
                "reason": reason,
            })
        else:
            accepted.append((source, rule))

    primary_actual = [
        source for source, rule in accepted
        if rule["role"] == "primary" and source.get("information_type") == "Actual"
    ]
    independent_refs = {
        str(source.get("source_ref"))
        for source, _rule in accepted
        if source.get("source_ref")
    }
    requirements = []
    if not methodology_approved:
        requirements.append("اعتماد نسخة منهجية مالية منشورة")
    if not primary_actual:
        requirements.append("مصدر أساسي Actual متحقق وحديث")
    if len(independent_refs) < 2:
        requirements.append("مصدر ثانٍ مستقل للمطابقة")
    if not human_reviewed:
        requirements.append("مراجعة واعتماد بشري للمدخلات والفترة")

    eligible = not requirements
    return {
        "status": "READY" if eligible else FINANCIAL_VALUE_STATUS,
        "eligible": eligible,
        "methodology_version": FINANCIAL_VALUE_POLICY_VERSION,
        "methodology_status": FINANCIAL_VALUE_POLICY_STATUS,
        "accepted_source_count": len(accepted),
        "rejected_sources": rejected,
        "missing_requirements": requirements,
        # These remain null until a separately approved calculator consumes
        # this gate and records its formula, period, currency, and attribution.
        "current_value": None,
        "potential_value": None,
        "value_gap": None,
    }


def _redact_deferred_financial_values(value):
    """Prevent legacy/nested Scan payloads from leaking monetary value fields."""
    if isinstance(value, dict):
        return {
            key: (
                None
                if key in FINANCIAL_VALUE_FIELDS
                else _redact_deferred_financial_values(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_deferred_financial_values(item) for item in value]
    return value


def ask_sana_ai(system_prompt, user_prompt):
    """
    نداء حقيقي لـ Claude — أول اتصال فعلي بذكاء اصطناعي في سنع، بدل أي حساب مبرمج يدويًا.
    يحتاج ANTHROPIC_API_KEY كمتغيّر بيئة (على Replit: أضفه من قسم Secrets).
    """
    try:
        import anthropic
    except ImportError:
        return {"error": "مكتبة anthropic غير مثبَّتة — شغّل: pip install anthropic"}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY غير موجود — أضفه في Secrets على Replit أولاً"}

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        return {"raw_text": cleaned.strip()}
    except Exception as e:
        return {"error": f"فشل الاتصال بالذكاء الاصطناعي: {str(e)}"}


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = resolve_database_url()
DATABASE_SCHEMA = resolve_database_schema()
_BILLING_TEST_SCHEMA_PREVIOUS = None
_BILLING_TEST_SCHEMA_PREVIOUS_ENV = None
_BILLING_TEST_SCHEMA_STATE_SAVED = False
# Keep scripts/tests that import this module aligned with the effective database.
os.environ["DATABASE_URL"] = DATABASE_URL

# Resend — إرسال البريد الإلكتروني
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY

# Rate limiting في الذاكرة — استعادة كلمة المرور: 3 طلبات/ساعة/بريد
_pw_reset_attempts: dict = {}   # email → [datetime (UTC), ...]

# URL الأساسي للتطبيق — يُستخدم في روابط البريد الإلكتروني
# أولوية: APP_URL (يدوي) ← REPLIT_DOMAINS ← REPLIT_DEV_DOMAIN ← localhost
def _resolve_app_base_url() -> str:
    if os.environ.get("APP_URL"):
        return os.environ["APP_URL"].rstrip("/")
    replit_domains = os.environ.get("REPLIT_DOMAINS", "")
    if replit_domains:
        return "https://" + replit_domains.split(",")[0].strip()
    dev_domain = os.environ.get("REPLIT_DEV_DOMAIN", "")
    if dev_domain:
        return "https://" + dev_domain
    return "http://localhost:5000"

APP_BASE_URL: str = _resolve_app_base_url()

# اعتماد القرار يضم تحديث القرار وإنشاء مهمة التنفيذ في معاملة واحدة. لا
# نعيد المحاولة إلا لأخطاء PostgreSQL التي تعني أن المعاملة نفسها يجب أن
# تبدأ من جديد، وبعدد محدود حتى لا يتحول الطلب إلى انتظار غير محدود.
DECISION_APPROVAL_MAX_ATTEMPTS = 3
DECISION_APPROVAL_RETRY_DELAY_SECONDS = 0.05
DECISION_APPROVAL_RETRYABLE_ERRORS = (
    psycopg2.errors.DeadlockDetected,
    psycopg2.errors.SerializationFailure,
)


class _SanaJSONProvider(DefaultJSONProvider):
    """
    يحوّل الأنواع التي لا يدعمها json القياسي:
      • decimal.Decimal  → float  (عمود numeric في PostgreSQL)
      • datetime / date  → ISO-8601 string
    يُطبَّق تلقائياً على كل jsonify() و Response.json في التطبيق.
    """
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)

app = Flask(__name__)
app.json_provider_class = _SanaJSONProvider
app.json = _SanaJSONProvider(app)
_runtime_environment = os.environ.get("SANA_ENV", "").strip().lower()
IS_RAILWAY = bool(
    os.environ.get("RAILWAY_PROJECT_ID")
    or os.environ.get("RAILWAY_SERVICE_ID")
    or os.environ.get("RAILWAY_ENVIRONMENT_ID")
)
IS_PRODUCTION = (
    IS_RAILWAY
    or _runtime_environment in {"production", "prod"}
)
_session_secret = os.environ.get("SESSION_SECRET")
if IS_PRODUCTION and (not _session_secret or len(_session_secret) < 32):
    raise RuntimeError(
        "SESSION_SECRET must be a non-empty value of at least 32 characters "
        "in production"
    )
if not _session_secret:
    print(
        "WARNING: SESSION_SECRET not set, using ephemeral key"
        " - sessions will not persist across restarts",
        flush=True,
    )
app.secret_key = _session_secret or secrets.token_hex(32)
app.config.update(
    MAX_CONTENT_LENGTH=50 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    SESSION_REFRESH_EACH_REQUEST=True,
)

# CSRF Protection — تحمي كل POST/PUT/PATCH/DELETE تلقائياً
app.config["WTF_CSRF_TIME_LIMIT"] = 3600   # ساعة واحدة
csrf = CSRFProtect(app)

# فلتر Jinja2: يحوّل JSON string → dict (يُستخدم في قوالب المقالات)
@app.template_filter("from_json")
def from_json_filter(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}

# مفتاح "العرض الداخلي التجريبي" — يسمح لك أنت كمشرف بفتح أي شركة عبر
# ?company_id=...&admin_key=... دون تسجيل دخول، منفصل تمامًا عن حسابات العملاء الحقيقية.
# لا يُعرض هذا المفتاح في أي صفحة عامة؛ استخدمه يدويًا في المتصفح فقط.
ADMIN_PREVIEW_KEY = os.environ.get("ADMIN_PREVIEW_KEY")

# نقاط الوصول العامة فقط — كل ما عداها (صفحات وAPI) يحمل بيانات شركة
# بشكل أو بآخر، ويُحظر افتراضيًا ما لم يوجد تسجيل دخول حقيقي أو مفتاح
# العرض الداخلي للمشرف. هذا يمنع أي تسريب عبر استدعاء API مباشرة أيضًا،
# وليس فقط عبر صفحات HTML.
PUBLIC_ENDPOINTS = {
    "entry", "login", "signup", "logout", "api_session",
    "pricing_page", "billing_offer_api", "billing_quote_api",
    "billing_success_page", "billing_stripe_webhook",
    "methodology_page", "methodology_detail",
    "system_health", "healthz", "static", "guide_page",
    "sectors_list",   # قائمة القطاعات — عامة بلا مصادقة
    "articles_list", "article_page", "api_articles_list",  # مقالات — عامة بلا مصادقة
    "forgot_password", "reset_password", "accept_company_invitation",
    # عام على مستوى الجلسة فقط؛ محمي دائمًا برمز Bearer مستقل من Supabase Vault.
    "internal_execution_reminders_run",
    # عام على مستوى الجلسة فقط؛ محمي بتوقيع GitHub Actions OIDC قصير العمر.
    "knowledge_backup_reconcile_api",
}
# ملاحظة: "companies_list" أُزيل عمداً من القائمة العامة (P0-1)
# المسار /api/companies مقيَّد الآن بـ admin_key فقط


def is_admin_preview():
    """وضع العرض الداخلي التجريبي — يتطلب معرفة المفتاح السرّي، وليس مجرد تعديل الرابط."""
    return bool(ADMIN_PREVIEW_KEY) and request.args.get("admin_key") == ADMIN_PREVIEW_KEY


def current_account():
    if "account_id" not in session:
        return None
    return {
        "account_id": session["account_id"],
        "company_id": session.get("company_id"),
        "email": session.get("email"),
        "admin_role": session.get("admin_role", "USER"),
        "account_status": session.get("account_status", "active"),
        "admin_company_id": session.get("admin_company_id"),
    }


SESSION_EXPIRED_MESSAGE = "انتهت الجلسة، سجّل دخولك للمتابعة"


def _safe_next_path(value):
    """Preserve only same-site paths when returning after login."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if (
        not value
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
    ):
        return None
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return None
    target = parsed.path or "/"
    if parsed.query:
        target += f"?{parsed.query}"
    return target


def _session_return_path():
    """Use the page that initiated an API request when available."""
    if request.referrer:
        referrer = urlparse(request.referrer)
        if (
            referrer.scheme in {"http", "https"}
            and referrer.netloc == request.host
        ):
            target = referrer.path or "/"
            if referrer.query:
                target += f"?{referrer.query}"
            safe_target = _safe_next_path(target)
            if safe_target:
                return safe_target
    if not request.path.startswith("/api/"):
        return _safe_next_path(request.full_path.rstrip("?"))
    return url_for("ceo_home")


def _authentication_required_response():
    cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
    expired = bool(request.cookies.get(cookie_name))
    next_path = _session_return_path() or url_for("ceo_home")
    login_args = {"next": next_path}
    if expired:
        login_args["reason"] = "session_expired"
    login_url = url_for("login", **login_args)
    if request.path.startswith("/api/"):
        return jsonify({
            "success": False,
            "error": "SESSION_EXPIRED" if expired else "AUTHENTICATION_REQUIRED",
            "message": (
                SESSION_EXPIRED_MESSAGE
                if expired else "سجّل دخولك للمتابعة"
            ),
            "redirect": login_url,
        }), 401
    return redirect(login_url)


ADMIN_ROLES = {
    "USER", "COMPANY_OWNER", "COMPANY_MEMBER", "ADMIN", "SUPER_ADMIN"
}
SYSTEM_ADMIN_ROLES = {"ADMIN", "SUPER_ADMIN"}
COMPANY_ROLES = {"COMPANY_OWNER", "COMPANY_MEMBER"}
SANA_LEADERSHIP_EMAIL = "sanaaos2026@gmail.com"
TEST_IDENTITY_MARKERS = ("test", "billing", "drive", "example.test")
TEST_IDENTITY_ARABIC_MARKERS = ("اختبار", "تجربة")
ADMIN_STATUSES = {"active", "invited", "disabled"}
ADMIN_PERMISSION_OPTIONS = {
    "manage_companies": "إدارة الشركات",
    "manage_users": "إدارة المستخدمين",
    "link_accounts": "إنشاء وربط الحسابات",
    "edit_company": "إدخال وتعديل معلومات الشركات",
    "run_reports": "تشغيل وعرض التقارير",
    "export_reports": "تصدير التقارير",
    "review_cases": "مراجعة الحالات",
}
COMPANY_CONTEXT_PERMISSIONS = {
    "manage_companies", "edit_company", "run_reports",
    "export_reports", "review_cases", "link_accounts",
}
_RUNTIME_HEALTH = {
    "startup": "not_started",
    "last_critical_error": None,
    "workers": {
        "execution_reminders": "not_started",
        "knowledge_backup": "not_started",
        "knowledge_research": "not_started",
        "billing_notification_retry": "not_started",
    },
}


def _admin_role():
    """Read the current role from the database; is_admin remains a legacy bridge."""
    account = current_account()
    if not account:
        return None
    row = get_db().execute(
        """SELECT email,company_id,admin_role,is_admin,account_status
           FROM user_accounts WHERE account_id=?""",
        (account["account_id"],),
    ).fetchone()
    if not row or row["account_status"] != "active":
        return None
    role = str(row["admin_role"] or "USER").upper()
    if role == "USER" and row["is_admin"]:
        role = "SUPER_ADMIN"
    if (
        role == "SUPER_ADMIN"
        and not app.config.get("TESTING")
        and (
            str(row["email"] or "").strip().lower() != SANA_LEADERSHIP_EMAIL
            or row["company_id"] is not None
        )
    ):
        return None
    return role if role in SYSTEM_ADMIN_ROLES else None


def _has_test_identity_marker(*values):
    text = " ".join(str(value or "").strip().lower() for value in values)
    return (
        any(marker in text for marker in TEST_IDENTITY_MARKERS)
        or any(marker in text for marker in TEST_IDENTITY_ARABIC_MARKERS)
    )


def _test_reset_allowed(account=None):
    """Allow the destructive reset only to Pilot members or system admins."""
    account = account or current_account()
    if not account or not account.get("company_id"):
        return False
    if _admin_role() in SYSTEM_ADMIN_ROLES:
        return True
    row = get_db().execute(
        """SELECT pilot_cohort_number,account_status
           FROM user_accounts WHERE account_id=? AND company_id=?""",
        (account["account_id"], account["company_id"]),
    ).fetchone()
    return bool(
        row
        and row["account_status"] == "active"
        and row["pilot_cohort_number"]
        and 1 <= int(row["pilot_cohort_number"]) <= PILOT_COHORT_LIMIT
    )


PILOT_COHORT_LIMIT = 20


def _pilot_identity_is_eligible(row):
    role = str(row["admin_role"] or "USER").upper()
    return (
        row["company_id"]
        and role in COMPANY_ROLES | {"USER"}
        and not row["is_admin"]
        and not _has_test_identity_marker(
            row["account_id"], row["email"], row["company_id"],
        )
    )


def _assign_pilot_slot(db, account_id):
    """Assign the next durable Pilot slot without using timestamps."""
    db.execute("SELECT pg_advisory_xact_lock(hashtext('sana-pilot-cohort'))")
    account = db.execute(
        """SELECT account_id,email,company_id,is_admin,admin_role,
                  pilot_cohort_number
           FROM user_accounts WHERE account_id=?""",
        (account_id,),
    ).fetchone()
    if not account or not _pilot_identity_is_eligible(account):
        return None
    if account["pilot_cohort_number"]:
        return int(account["pilot_cohort_number"])
    used = {
        int(row["pilot_cohort_number"])
        for row in db.execute(
            """SELECT pilot_cohort_number FROM user_accounts
               WHERE pilot_cohort_number IS NOT NULL
               ORDER BY pilot_cohort_number"""
        ).fetchall()
        if 1 <= int(row["pilot_cohort_number"]) <= PILOT_COHORT_LIMIT
    }
    available = [
        number for number in range(1, PILOT_COHORT_LIMIT + 1)
        if number not in used
    ]
    if not available:
        return None
    db.execute(
        "UPDATE user_accounts SET pilot_cohort_number=? WHERE account_id=?",
        (available[0], account_id),
    )
    return available[0]


def _seed_pilot_cohort(db):
    """Give existing real company accounts stable slots, in count order only."""
    db.execute("SELECT pg_advisory_xact_lock(hashtext('sana-pilot-cohort'))")
    used = {
        int(row["pilot_cohort_number"])
        for row in db.execute(
            """SELECT pilot_cohort_number FROM user_accounts
               WHERE pilot_cohort_number IS NOT NULL"""
        ).fetchall()
        if 1 <= int(row["pilot_cohort_number"]) <= PILOT_COHORT_LIMIT
    }
    available = [
        number for number in range(1, PILOT_COHORT_LIMIT + 1)
        if number not in used
    ]
    if not available:
        return
    candidates = db.execute(
        """SELECT account_id,email,company_id,is_admin,admin_role
           FROM user_accounts
           WHERE company_id IS NOT NULL
             AND pilot_cohort_number IS NULL
             AND COALESCE(admin_role,'USER') IN ('USER','COMPANY_OWNER','COMPANY_MEMBER')
           ORDER BY account_id"""
    ).fetchall()
    for row, number in zip(
        (row for row in candidates if _pilot_identity_is_eligible(row)),
        available,
    ):
        db.execute(
            "UPDATE user_accounts SET pilot_cohort_number=? WHERE account_id=?",
            (number, row["account_id"]),
        )


def _reset_company_experience(db, company_id):
    """Remove one company's journey data while preserving its login and company shell."""
    schema_rows = db.execute(
        """SELECT table_name,column_name
           FROM information_schema.columns
           WHERE table_schema=current_schema()"""
    ).fetchall()
    table_columns = {}
    for row in schema_rows:
        table_columns.setdefault(row["table_name"], set()).add(row["column_name"])

    # Child rows without company_id must go before their company-owned parents.
    nested_deletes = (
        (
            "diagnostic_findings",
            "DELETE FROM diagnostic_findings WHERE run_id IN "
            "(SELECT run_id FROM diagnostic_runs WHERE company_id=?)",
        ),
        (
            "decision_asset_impacts",
            "DELETE FROM decision_asset_impacts WHERE decision_id IN "
            "(SELECT decision_id FROM decisions WHERE company_id=?)",
        ),
        (
            "gos_experiment_process",
            "DELETE FROM gos_experiment_process WHERE experiment_id IN "
            "(SELECT experiment_id FROM gos_experiments WHERE company_id=?)",
        ),
        (
            "drive_source_excerpts",
            "DELETE FROM drive_source_excerpts WHERE case_id IN "
            "(SELECT case_id FROM cases WHERE company_id=?)",
        ),
        (
            "drive_private_citations",
            "DELETE FROM drive_private_citations WHERE case_id IN "
            "(SELECT case_id FROM cases WHERE company_id=?)",
        ),
    )

    # Fixed, dependency-safe allowlist: journey/execution data only.
    # Auth, subscriptions, invitations, billing events and audit history are preserved.
    company_tables = (
        "human_review_events", "case_human_reviews",
        "p0_impact_reviews", "returning_checkins", "task_evidence",
        "execution_reminder_attempts", "execution_task_audit",
        "execution_backlog_audit", "execution_sop_applications",
        "execution_sop_versions", "execution_reminders", "execution_risks",
        "execution_backlog", "execution_owner_bindings", "execution_sops",
        "rc_invoices", "rc_deal_learning", "rc_opportunity_economics",
        "rc_stage_history", "rc_projects", "sales_activities",
        "zubair_attachments", "zubair_experiment_reviews",
        "zubair_timeline_events", "zubair_capture_drafts", "zubair_contacts",
        "zubair_prospect_companies",
        "gos_learning_links", "gos_experiment_decisions", "gos_experiments",
        "gos_bottlenecks", "gos_bottleneck_cycles", "gos_canonical_merges",
        "gos_canonical_entities", "gos_truth_records", "gos_baselines",
        "gos_company_profiles",
        "tasks", "decisions", "scan_findings", "scan_runs",
        "diagnostic_runs", "evidence_relations", "case_frameworks",
        "diagnostic_baselines", "evidence",
        "company_memory_items",
        "sana_memory_entries", "cases", "assets", "users",
        "opportunities", "leads",
    )

    delete_statements = [
        (table_name, statement)
        for table_name, statement in nested_deletes
        if table_name in table_columns
    ]
    delete_statements.extend(
        (
            table_name,
            f"DELETE FROM {table_name} WHERE company_id=?",
        )
        for table_name in company_tables
        if "company_id" in table_columns.get(table_name, set())
    )
    deleted = {}
    if delete_statements:
        ctes = []
        params = []
        count_columns = []
        for index, (table_name, statement) in enumerate(delete_statements):
            cte_name = f"reset_{index}"
            ctes.append(f"{cte_name} AS ({statement} RETURNING 1)")
            params.append(company_id)
            count_columns.append(
                f"(SELECT COUNT(*) FROM {cte_name}) AS count_{index}"
            )
        counts = db.execute(
            "WITH " + ", ".join(ctes) + " SELECT " + ", ".join(count_columns),
            tuple(params),
        ).fetchone()
        deleted = {
            table_name: int(counts[f"count_{index}"])
            for index, (table_name, _) in enumerate(delete_statements)
        }

    reset_values = {
        "name": "شركة جديدة",
        "sector": None,
        "sector_other": None,
        "business_type": None,
        "respondent_role": None,
        "city": None,
        "stage": None,
        "employee_count": None,
        "annual_revenue": None,
        "vision": None,
        "main_goal": None,
        "sds_done": 0,
        "success_criteria": None,
        "website_url": None,
        "social_media_url": None,
        "business_reference_url": None,
        "source_prompt_last_shown_at": None,
        "source_prompt_dismissed_at": None,
        "source_last_confirmed_at": None,
        "business_description": None,
        "goal_90_days": None,
        "primary_challenge": None,
        "lifecycle_status": "Active",
    }
    company_columns = table_columns.get("companies", set())
    reset_values = {
        column: value
        for column, value in reset_values.items()
        if column in company_columns
    }
    assignments = ", ".join(f"{column}=?" for column in reset_values)
    db.execute(
        f"UPDATE companies SET {assignments} WHERE company_id=?",
        (*reset_values.values(), company_id),
    )
    return deleted


def _admin_account_classification(account):
    """Separate production identities from explicit fixtures without deleting them."""
    email = str(account.get("email") or "").strip().lower()
    role = str(account.get("admin_role") or "USER").upper()
    if role == "USER" and account.get("is_admin"):
        role = "SUPER_ADMIN"
    if role == "SUPER_ADMIN" and email == SANA_LEADERSHIP_EMAIL:
        return "production"
    if _has_test_identity_marker(
        account.get("account_id"),
        email,
        account.get("company_id"),
        account.get("company_name"),
    ):
        return "test"
    if role == "SUPER_ADMIN":
        return "unclassified"
    return "production"


def _admin_company_classification(company):
    """Classify a company once so related admin metrics share one scope."""
    if _has_test_identity_marker(
        company.get("company_id"),
        company.get("company_code"),
        company.get("signup_code"),
        company.get("name"),
        company.get("contact_email"),
    ):
        return "test"
    return "production"


def _admin_permissions():
    account = current_account()
    if not account:
        return set()
    row = get_db().execute(
        "SELECT admin_role,admin_permissions FROM user_accounts WHERE account_id=?",
        (account["account_id"],),
    ).fetchone()
    if not row:
        return set()
    if row["admin_role"] == "SUPER_ADMIN":
        return set(ADMIN_PERMISSION_OPTIONS)
    try:
        values = json.loads(row["admin_permissions"] or "[]")
    except (TypeError, json.JSONDecodeError):
        values = []
    return {str(value) for value in values if value in ADMIN_PERMISSION_OPTIONS}


def _admin_guard(minimum="ADMIN", *, json_response=True, permission=None,
                 any_permissions=None):
    """Server-side RBAC guard for the internal operations console."""
    role = _admin_role()
    allowed = {"ADMIN", "SUPER_ADMIN"} if minimum == "ADMIN" else {"SUPER_ADMIN"}
    if role in allowed:
        if role == "SUPER_ADMIN":
            return role, None
        required = set(any_permissions or ())
        if permission:
            required.add(permission)
        granted = _admin_permissions()
        if not required or (
            permission and permission in granted
        ) or (
            any_permissions and granted.intersection(set(any_permissions))
        ):
            return role, None
        if json_response:
            return None, (jsonify({
                "success": False,
                "error": "ADMIN_PERMISSION_REQUIRED",
                "required": sorted(required),
            }), 403)
        return None, redirect(url_for("admin_dashboard_page"))
    if json_response:
        return None, (jsonify({
            "success": False,
            "error": "ADMIN_FORBIDDEN",
            "message": "هذه المساحة مخصصة للإدارة الداخلية.",
        }), 403)
    return None, redirect(url_for("login", next=request.full_path))


def _admin_audit(db, actor_id, action, target_type, target_id=None,
                 company_id=None, reason="تشغيل إداري", metadata=None):
    db.execute(
        """INSERT INTO admin_audit_log
           (audit_id,actor_account_id,action,target_type,target_id,company_id,
            reason,metadata_json)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            "AUD-" + secrets.token_hex(8).upper(),
            actor_id, action, target_type, target_id, company_id,
            reason[:500],
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )


def request_company_context():
    """سياق الشركة لهذا الطلب دون تحويل المعاينة الداخلية إلى جلسة عميل حقيقية."""
    account = current_account()
    if account:
        company_id = account.get("company_id")
        if not company_id and account.get("admin_company_id"):
            if _admin_role() in SYSTEM_ADMIN_ROLES:
                company_id = account["admin_company_id"]
        return {
            **account,
            "company_id": company_id,
            "admin_preview": False,
            "admin_company_context": bool(
                not account.get("company_id") and company_id
            ),
        }
    if not is_admin_preview():
        return None
    company_id = (request.args.get("company_id") or "").strip()
    if not company_id:
        return None
    return {
        "account_id": None,
        "company_id": company_id,
        "email": None,
        "admin_preview": True,
    }


def default_company_id():
    """مصدر شركة القوالب: الجلسة للعميل، وسياق المعاينة للمشرف فقط."""
    account = current_account()
    if account:
        return (
            account.get("company_id")
            or (
                account.get("admin_company_id")
                if _admin_role() in SYSTEM_ADMIN_ROLES else None
            )
        )
    if is_admin_preview() and request.args.get("company_id"):
        return request.args["company_id"]
    return "C001"

def endpoint_path(endpoint, **values):
    """Build an internal path from Flask's route map, even outside a request."""
    return app.url_map.bind("").build(endpoint, values, force_external=False)


def p0_template_context():
    """سياق موحّد للقوالب دون تسريب معرّفات الشركة في روابط العميل.

    العميل الحقيقي لا يحتاج أي query string: الـ API والروابط تستخدم الجلسة.
    المعاينة الإدارية القديمة تبقى قابلة للتنقل، لكن لا تعمل إلا بالمفتاح
    الذي تتحقق منه الحراسة قبل الوصول.
    """
    account = current_account()
    def journey_urls(company_id):
        return {
            "discovery": url_for("discovery"),
            "onboarding": url_for("onboarding"),
            "home": url_for("ceo_home"),
            "passport": url_for("business_passport"),
            "logout": url_for("logout"),
            "case_result_template": url_for("case_result", case_id="__CASE_ID__"),
            "case_next_template": url_for("case_next_step", case_id="__CASE_ID__"),
            "company_report": url_for("scan_report_html", company_id=company_id),
            "company_plan": url_for("execution_plan_html", company_id=company_id),
            "discovery_save": url_for("discovery_save"),
            "fit_gate": url_for("fit_gate_check"),
            "reset_experience": url_for("reset_experience"),
            "case_api_template": url_for("case_detail", case_id="__CASE_ID__"),
            "case_scan_template": url_for("run_case_scan", case_id="__CASE_ID__"),
            "case_baseline_template": url_for("case_diagnostic_baseline", case_id="__CASE_ID__"),
            "case_decision_template": url_for("create_p0_case_decision", case_id="__CASE_ID__"),
            "company_passport_api": url_for("passport_summary", company_id=company_id),
            "company_question_api": url_for("sds_question", company_id=company_id),
            "company_evidence_api": url_for("add_evidence", company_id=company_id),
            "company_report_pdf": url_for("passport_report_pdf", company_id=company_id),
        }
    if account:
        return {
            "company_id": account["company_id"],
            "context_query": "",
            "is_admin_preview": False,
            "can_reset_experience": _test_reset_allowed(account),
            "journey_urls": journey_urls(account["company_id"]),
        }
    if is_admin_preview():
        params = {"admin_key": request.args["admin_key"]}
        if request.args.get("company_id"):
            params["company_id"] = request.args["company_id"]
        if request.args.get("view") == "client":
            params["view"] = "client"
        return {
            "company_id": default_company_id(),
            "context_query": "?" + urlencode(params),
            "is_admin_preview": True,
            "can_reset_experience": False,
            "journey_urls": journey_urls(default_company_id()),
        }
    return {
        "company_id": "C001",
        "context_query": "",
        "is_admin_preview": False,
        "can_reset_experience": False,
        "journey_urls": journey_urls("C001"),
    }


def _company_start_redirect(account):
    """يعيد نقطة البداية القانونية للحساب دون منح SUPER_ADMIN عضوية شركة."""
    if (
        str(account.get("admin_role") or "").upper() in SYSTEM_ADMIN_ROLES
        and not account.get("company_id")
    ):
        return url_for("admin_dashboard_page")
    db = get_db()
    company = db.execute(
        "SELECT name, sector, sds_done, main_goal FROM companies WHERE company_id=?",
        (account["company_id"],),
    ).fetchone()
    if not company:
        return url_for("onboarding")
    if not company["sector"] or company["name"] == "شركة جديدة":
        return url_for("onboarding")
    if not company["sds_done"] or not company["main_goal"]:
        return url_for("discovery")
    return url_for("ceo_home")


def _client_only_alias(target):
    """يُبقي صفحات الإدارة القديمة متاحة للمعاينة، ويغلق الرحلات الموازية للعميل."""
    if current_account() and not is_admin_preview():
        return redirect(target)
    return None


@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    if request.endpoint not in PUBLIC_ENDPOINTS and not current_account():
        return _authentication_required_response()
    return jsonify({
        "success": False,
        "error": "CSRF_FAILED",
        "message": "تعذر إكمال الطلب. حدّث الصفحة وحاول مرة أخرى.",
    }), 400


@app.errorhandler(404)
def handle_not_found(error):
    """Render Sana's 404 page and retain only safe navigation context."""
    referrer = request.referrer
    if referrer:
        parsed_referrer = urlparse(referrer)
        referrer = parsed_referrer.path or "/"
    app.logger.warning(
        "[404] path=%s referrer=%s account_id=%s company_id=%s",
        request.path[:500],
        (referrer or "-")[:500],
        str(session.get("account_id") or "-")[:100],
        str(session.get("company_id") or "-")[:100],
    )
    return render_template("404.html"), 404


@app.before_request
def enforce_company_auth():
    # واجهة الخبير العامة — نظام مصادقة مستقل (access_code في الجلسة) لا علاقة له بحسابات الشركات
    if request.path.startswith(("/e/", "/api/e/")):
        return
    endpoint = request.endpoint
    if endpoint is None or endpoint in PUBLIC_ENDPOINTS:
        return

    account = current_account()

    # مساحة الإدارة تستخدم RBAC مستقلًا فوق الحساب نفسه. السماح هنا لا يمنح
    # صلاحية؛ كل admin route يعيد التحقق حيًا من الدور والحالة. هذا الاستثناء
    # يمنع حارس tenant العام من حجب وصول SUPER_ADMIN الصريح والمسجل.
    if account and (
        request.path == "/admin" or request.path.startswith("/api/admin/")
    ):
        return

    effective_company_id = account.get("company_id") if account else None
    if account and not effective_company_id and account.get("admin_company_id"):
        if _admin_role() in SYSTEM_ADMIN_ROLES:
            effective_company_id = account["admin_company_id"]
    if account and not effective_company_id:
        if request.path.startswith("/api/"):
            return jsonify({
                "success": False,
                "error": "ADMIN_COMPANY_CONTEXT_REQUIRED",
            }), 403
        return redirect(url_for("admin_dashboard_page"))

    # 1) أي مسار (صفحة أو API) يحمل بيانات شركة — يتطلب جلسة دخول حقيقية،
    #    أو مفتاح العرض الداخلي للمشرف. بدون أحدهما لا وصول إطلاقًا،
    #    سواء عبر المتصفح أو عبر استدعاء API مباشر.
    if not account and not is_admin_preview():
        return _authentication_required_response()

    # 2) أي مسار API يحمل company_id في الرابط نفسه — لا يمكن لحساب مسجَّل
    #    الوصول إلا لشركته هو، حتى لو عدّل الرابط يدويًا
    if account and "company_id" in (request.view_args or {}):
        if request.view_args["company_id"] != effective_company_id:
            return jsonify({
                "success": False, "error": "FORBIDDEN",
                "message": "لا تملك صلاحية الوصول لبيانات هذه الشركة."
            }), 403


def enforce_entity_company_scope(entity_company_id):
    """للمسارات التي لا تحمل company_id في الرابط (مثل /api/cases/<id>) —
    يتحقق أن الحساب المسجَّل (إن وُجد) يملك هذا السجل فعلًا قبل إرجاعه."""
    account = current_account()
    effective_company_id = account.get("company_id") if account else None
    if account and not effective_company_id and account.get("admin_company_id"):
        if _admin_role() in SYSTEM_ADMIN_ROLES:
            effective_company_id = account["admin_company_id"]
    if account and entity_company_id != effective_company_id:
        return jsonify({
            "success": False, "error": "FORBIDDEN",
            "message": "لا تملك صلاحية الوصول لبيانات هذه الشركة."
        }), 403
    return None


@app.after_request
def no_cache_html(response):
    """منع أي تخزين مؤقت للصفحات — Safari على iOS يُطبّق heuristic caching إن لم يُوجَّه صراحةً"""
    if "text/html" in response.content_type:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


class _PGConn:
    """طبقة توافق فوق psycopg2 تحاكي واجهة sqlite3.Connection المستخدمة في هذا
    الملف بالكامل (execute/executemany/executescript/commit/close)، بما يسمح
    لكل منطق الأعمال والاستعلامات الحالية (بصياغة `?` ونتائج تُقرأ بالاسم أو
    بالفهرس) بالعمل دون أي تغيير في السلوك، فقط فوق PostgreSQL بدل SQLite."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute(sql.replace("?", "%s"), params)
        return cur

    def executemany(self, sql, seq_of_params):
        cur = self._conn.cursor()
        cur.executemany(sql.replace("?", "%s"), seq_of_params)
        return cur

    def executescript(self, sql):
        cur = self._conn.cursor()
        cur.execute(sql)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

def _quote_schema_identifier(schema):
    """Quote a validated schema identifier for PostgreSQL DDL/session setup."""
    schema = str(schema or "").strip().lower()
    if not re.fullmatch(r"^[a-z_][a-z0-9_]{0,62}$", schema):
        raise ValueError("DATABASE_SCHEMA_INVALID")
    return '"' + schema.replace('"', '""') + '"'
def _connect_pg(database_url=None, schema=None):
    conn = psycopg2.connect(database_url or DATABASE_URL)
    conn.autocommit = False
    effective_schema = DATABASE_SCHEMA if schema is None else schema
    if effective_schema:
        with conn.cursor() as cursor:
            cursor.execute(
                f"SET search_path TO {_quote_schema_identifier(effective_schema)}"
            )
            cursor.execute(
                "SELECT set_config('application_name', %s, false)",
                (f"sana-billing-test:{effective_schema}",),
            )
    return _PGConn(conn)


def get_db():
    if "db" not in g:
        g.db = _connect_pg()
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema=current_schema() AND table_name=?",
        (table_name,)
    ).fetchone()
    return row is not None


def _columns_of(conn, table_name):
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name=?",
        (table_name,)
    ).fetchall()
    return {row[0] for row in rows}


def init_db(force=False):
    conn = _connect_pg()
    try:
        # All startup DDL, including schema.sql and compatibility migrations,
        # uses one transaction-scoped lock.  The lock timeout prevents a
        # business transaction from holding the web process indefinitely.
        acquire_schema_lock(conn)
        if force and _table_exists(conn, "companies"):
            schema_identifier = _quote_schema_identifier(DATABASE_SCHEMA or "public")
            conn.executescript(
                f"DROP SCHEMA {schema_identifier} CASCADE; "
                f"CREATE SCHEMA {schema_identifier};"
            )
            conn.commit()
            acquire_schema_lock(conn)
        fresh = not _table_exists(conn, "companies")
        if fresh:
            with open(os.path.join(BASE_DIR, "schema.sql"), "r", encoding="utf-8") as f:
                conn.executescript(f.read())
            conn.commit()
            acquire_schema_lock(conn)
        if not fresh:
            # قاعدة بيانات موجودة أصلاً — أضف الجدول الجديد فقط دون مسح أي بيانات حالية
            conn.execute("""CREATE TABLE IF NOT EXISTS decision_asset_impacts (
            impact_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            score_impact INTEGER NOT NULL,
            is_primary INTEGER DEFAULT 0,
            FOREIGN KEY (decision_id) REFERENCES decisions(decision_id),
            FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
            )""")
        # إضافة أعمدة اختيارية لجدول tasks لدعم تجميع المهام تحت مراحل فرعية
        # مع بيان القيمة المتحققة من كل إنجاز — دون كسر أي بيانات موجودة.
        existing_cols = _columns_of(conn, "tasks")
        if "phase_label" not in existing_cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN phase_label TEXT")
        if "value_note" not in existing_cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN value_note TEXT")
        # نفس فكرة التصنيف تحت مرحلة، بالإضافة إلى حقل JSON لتفاصيل قرارات موضوعية
        # غنية (مثل ترتيب الخدمات) يحتاجها عرض متخصص (صفحة الخدمات) دون تفكيك نصوص.
        decision_cols = _columns_of(conn, "decisions")
        if "phase_label" not in decision_cols:
            conn.execute("ALTER TABLE decisions ADD COLUMN phase_label TEXT")
        if "structured_data" not in decision_cols:
            conn.execute("ALTER TABLE decisions ADD COLUMN structured_data TEXT")
        # مسؤول تنفيذ القرار وموعده — حقلان اختياريان بسيطان (لا نظام متابعة كامل بعد)
        if "owner_name" not in decision_cols:
            conn.execute("ALTER TABLE decisions ADD COLUMN owner_name TEXT")
        if "due_date" not in decision_cols:
            conn.execute("ALTER TABLE decisions ADD COLUMN due_date TEXT")
        # DEC-01: مقياس النجاح — حقل إلزامي عند الاعتماد للقرارات الجديدة (لا يكسر القديمة)
        if "success_metric" not in decision_cols:
            conn.execute("ALTER TABLE decisions ADD COLUMN success_metric TEXT")
        # ربط صريح بين القرار ولقطة Scan ومصادره؛ لا يجوز للتقرير استنتاج
        # هذا الربط من اشتراك القرار والدليل في الأصل أو القضية فقط.
        if "scan_id" not in decision_cols:
            conn.execute("ALTER TABLE decisions ADD COLUMN scan_id TEXT")
        if "evidence_ids" not in decision_cols:
            conn.execute("ALTER TABLE decisions ADD COLUMN evidence_ids TEXT")
        # وثائق منهجية عامة (مستقلة عن أي شركة) — قد لا يكون الجدول موجودًا في قواعد بيانات قديمة
        conn.execute("""CREATE TABLE IF NOT EXISTS methodology_docs (
            doc_id TEXT PRIMARY KEY,
            slug TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            subtitle TEXT,
            content TEXT NOT NULL,
            doc_type TEXT DEFAULT 'GENERIC',
            version TEXT DEFAULT 'v1.0',
            bos_id TEXT,
            created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))
        )""")
        # ترقية جدول الوثائق المنهجية القديم لدعم نظام BOS (doc_type/version/bos_id)
        methodology_cols = _columns_of(conn, "methodology_docs")
        if "doc_type" not in methodology_cols:
            conn.execute("ALTER TABLE methodology_docs ADD COLUMN doc_type TEXT DEFAULT 'GENERIC'")
        if "version" not in methodology_cols:
            conn.execute("ALTER TABLE methodology_docs ADD COLUMN version TEXT DEFAULT 'v1.0'")
        if "bos_id" not in methodology_cols:
            conn.execute("ALTER TABLE methodology_docs ADD COLUMN bos_id TEXT")
        if "sector_tags" not in methodology_cols:
            conn.execute("ALTER TABLE methodology_docs ADD COLUMN sector_tags TEXT")
        # أعمدة تحليل الذكاء الاصطناعي — دليل مفرد وقضية كاملة (بدون كسر قواعد بيانات قديمة)
        evidence_cols = _columns_of(conn, "evidence")
        if "ai_analysis" not in evidence_cols:
            conn.execute("ALTER TABLE evidence ADD COLUMN ai_analysis TEXT")
        if "ai_suggested_asset_id" not in evidence_cols:
            conn.execute("ALTER TABLE evidence ADD COLUMN ai_suggested_asset_id TEXT")
        cases_cols = _columns_of(conn, "cases")
        if "ai_analysis" not in cases_cols:
            conn.execute("ALTER TABLE cases ADD COLUMN ai_analysis TEXT")
        # حزم مهام قابلة لإعادة الاستخدام عبر أي شركة/قطاع — لا ترتبط بشركة واحدة بذاتها
        conn.execute("""CREATE TABLE IF NOT EXISTS task_packs (
            pack_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            sector TEXT,
            description TEXT,
            created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS task_pack_items (
            item_id TEXT PRIMARY KEY,
            pack_id TEXT NOT NULL,
            category_label TEXT NOT NULL,
            title TEXT NOT NULL,
            detail TEXT,
            asset_type TEXT NOT NULL,
            score_impact INTEGER NOT NULL,
            sort_order INTEGER DEFAULT 0
        )""")
        # نظام تسجيل الدخول الحقيقي — جدول الحسابات + رمز دعوة لكل شركة
        # (بدون كسر أي قاعدة بيانات قديمة لا تحتوي عليهما بعد)
        conn.execute("""CREATE TABLE IF NOT EXISTS user_accounts (
            account_id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            company_id TEXT NOT NULL,
            created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
            FOREIGN KEY (company_id) REFERENCES companies(company_id)
        )""")
        companies_cols = _columns_of(conn, "companies")
        if "signup_code" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN signup_code TEXT")
        accounts_cols = _columns_of(conn, "user_accounts")
        if "referral_source" not in accounts_cols:
            conn.execute("ALTER TABLE user_accounts ADD COLUMN referral_source TEXT")
        if "sds_done" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN sds_done SMALLINT DEFAULT 0")
        if "success_criteria" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN success_criteria TEXT")
        if "sector_other" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN sector_other TEXT")
        if "business_type" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN business_type TEXT")
        if "respondent_role" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN respondent_role TEXT")
        if "website_url" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN website_url TEXT")
        if "social_media_url" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN social_media_url TEXT")
        if "business_reference_url" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN business_reference_url TEXT")
        if "source_prompt_last_shown_at" not in companies_cols:
            conn.execute(
                "ALTER TABLE companies ADD COLUMN source_prompt_last_shown_at TIMESTAMPTZ"
            )
        if "source_prompt_dismissed_at" not in companies_cols:
            conn.execute(
                "ALTER TABLE companies ADD COLUMN source_prompt_dismissed_at TIMESTAMPTZ"
            )
        if "source_last_confirmed_at" not in companies_cols:
            conn.execute(
                "ALTER TABLE companies ADD COLUMN source_last_confirmed_at TIMESTAMPTZ"
            )
        if "business_description" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN business_description TEXT")
        if "goal_90_days" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN goal_90_days TEXT")
        if "primary_challenge" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN primary_challenge TEXT")
        conn.execute("""CREATE TABLE IF NOT EXISTS case_frameworks (
            cf_id       TEXT PRIMARY KEY,
            case_id     TEXT NOT NULL,
            company_id  TEXT NOT NULL,
            framework_id TEXT NOT NULL,
            linked_at   TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
            FOREIGN KEY (case_id) REFERENCES cases(case_id)
        )""")
        # ── B6: Sales CRM ──────────────────────────────────────────────
        conn.execute("""CREATE TABLE IF NOT EXISTS leads (
            lead_id        TEXT PRIMARY KEY,
            company_id     TEXT NOT NULL REFERENCES companies(company_id),
            name           TEXT NOT NULL,
            company_name   TEXT,
            email          TEXT,
            phone          TEXT,
            source         TEXT,
            service_interest TEXT,
            status         TEXT NOT NULL DEFAULT 'جديد',
            owner_id       TEXT,
            notes          TEXT,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_company ON leads(company_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_status  ON leads(company_id, status)")

        conn.execute("""CREATE TABLE IF NOT EXISTS opportunities (
            opp_id              TEXT PRIMARY KEY,
            company_id          TEXT NOT NULL REFERENCES companies(company_id),
            lead_id             TEXT REFERENCES leads(lead_id),
            title               TEXT NOT NULL,
            stage               TEXT NOT NULL DEFAULT 'عميل محتمل',
            amount              NUMERIC,
            probability         INTEGER,
            expected_close_date DATE,
            next_action         TEXT,
            next_action_due     DATE,
            outcome_reason      TEXT,
            owner_id            TEXT,
            delivery_task_id    TEXT,
            archived            SMALLINT NOT NULL DEFAULT 0,
            archived_at         TIMESTAMPTZ,
            archived_by         TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opp_company ON opportunities(company_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opp_stage   ON opportunities(company_id, stage)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opp_due     ON opportunities(company_id, next_action_due)")

        conn.execute("""CREATE TABLE IF NOT EXISTS sales_activities (
            activity_id  TEXT PRIMARY KEY,
            company_id   TEXT NOT NULL REFERENCES companies(company_id),
            opp_id       TEXT REFERENCES opportunities(opp_id),
            type         TEXT NOT NULL,
            subject      TEXT,
            notes        TEXT,
            occurred_at  TIMESTAMPTZ,
            due_at       TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            actor_id     TEXT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_act_opp ON sales_activities(opp_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_act_company ON sales_activities(company_id)")

        conn.execute("""CREATE TABLE IF NOT EXISTS sales_stage_history (
            history_id TEXT PRIMARY KEY,
            opp_id     TEXT NOT NULL REFERENCES opportunities(opp_id),
            company_id TEXT NOT NULL,
            from_stage TEXT,
            to_stage   TEXT NOT NULL,
            changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            changed_by TEXT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ssh_opp ON sales_stage_history(opp_id)")
        conn.execute("""CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token      TEXT PRIMARY KEY,
            email      TEXT NOT NULL,
            account_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            used       BOOLEAN NOT NULL DEFAULT false
        )""")
        # ── دورة الإثبات: expected_asset_impact على المهام + جدول task_evidence ──
        # expected_asset_impact: نص JSON يحدد الأصل المستهدف وعدد النقاط المتوقعة
        # مثال: {"asset_id": "A007", "score_impact": 5}
        task_cols = _columns_of(conn, "tasks")
        if "expected_asset_impact" not in task_cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN expected_asset_impact TEXT")
        conn.execute("""CREATE TABLE IF NOT EXISTS task_evidence (
            evidence_id         TEXT PRIMARY KEY,
            task_id             TEXT NOT NULL REFERENCES tasks(task_id),
            company_id          TEXT NOT NULL,
            evidence_type       TEXT NOT NULL DEFAULT 'نص وصفي',
            evidence_content    TEXT NOT NULL,
            submitted_at        TEXT NOT NULL DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')),
            verification_status TEXT NOT NULL DEFAULT 'لم يُتحقق',
            verification_reason TEXT,
            verified_at         TEXT,
            impact_applied      SMALLINT NOT NULL DEFAULT 0
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_evidence_task ON task_evidence(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_evidence_company ON task_evidence(company_id)")
        conn.execute("""CREATE TABLE IF NOT EXISTS p0_impact_reviews (
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
        )""")
        conn.execute("""CREATE INDEX IF NOT EXISTS idx_p0_impact_reviews_case
                        ON p0_impact_reviews(company_id,case_id,reviewed_at DESC)""")
        from sana_human_review import ensure_schema as ensure_human_review_schema
        ensure_human_review_schema(conn)
        from sana_returning_checkin import ensure_schema as ensure_returning_checkin_schema
        ensure_returning_checkin_schema(conn)

        # ── سنع الخبير: نظام اكتشاف وتأهيل الخبراء (مستقل تمامًا عن جداول الشركات) ──
        conn.execute("""CREATE TABLE IF NOT EXISTS experts (
            expert_id          TEXT PRIMARY KEY,
            uuid               TEXT UNIQUE NOT NULL,
            name               TEXT NOT NULL,
            domain_expertise   TEXT NOT NULL,
            phone              TEXT,
            email              TEXT,
            notes              TEXT,
            access_link_slug   TEXT UNIQUE NOT NULL,
            access_code        TEXT,
            status             TEXT NOT NULL DEFAULT 'لم يبدأ',
            api_call_count     INTEGER NOT NULL DEFAULT 0,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_session_at    TIMESTAMPTZ
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_experts_slug ON experts(access_link_slug)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_experts_uuid ON experts(uuid)")

        conn.execute("""CREATE TABLE IF NOT EXISTS expert_sessions (
            session_id    TEXT PRIMARY KEY,
            expert_id     TEXT NOT NULL REFERENCES experts(expert_id),
            started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            ended_at      TIMESTAMPTZ,
            current_stage INTEGER NOT NULL DEFAULT 1,
            conversation_log TEXT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expert_sessions_expert ON expert_sessions(expert_id)")

        conn.execute("""CREATE TABLE IF NOT EXISTS expert_facts (
            fact_id          TEXT PRIMARY KEY,
            expert_id        TEXT NOT NULL REFERENCES experts(expert_id),
            session_id       TEXT NOT NULL REFERENCES expert_sessions(session_id),
            fact_type        TEXT NOT NULL,
            content          TEXT NOT NULL,
            confidence_level TEXT,
            related_stage    INTEGER,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expert_facts_expert  ON expert_facts(expert_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expert_facts_session ON expert_facts(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expert_facts_type    ON expert_facts(expert_id, fact_type)")

        conn.execute("""CREATE TABLE IF NOT EXISTS expert_knowledge_assets (
            asset_id         TEXT PRIMARY KEY,
            expert_id        TEXT NOT NULL REFERENCES experts(expert_id),
            asset_category   TEXT NOT NULL,
            content          TEXT NOT NULL,
            transformable_to TEXT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expert_ka_expert ON expert_knowledge_assets(expert_id)")

        conn.execute("""CREATE TABLE IF NOT EXISTS expert_projects (
            project_id          TEXT PRIMARY KEY,
            expert_id           TEXT NOT NULL REFERENCES experts(expert_id),
            title               TEXT NOT NULL,
            description         TEXT,
            scores              TEXT,
            is_recommended      SMALLINT NOT NULL DEFAULT 0,
            recommendation_reason TEXT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expert_projects_expert ON expert_projects(expert_id)")

        # ── is_admin على user_accounts — يميّز حسابات المستشار عن حسابات الشركات ──
        accts_cols = _columns_of(conn, "user_accounts")
        if "is_admin" not in accts_cols:
            conn.execute("ALTER TABLE user_accounts ADD COLUMN is_admin SMALLINT NOT NULL DEFAULT 0")
        if "admin_role" not in accts_cols:
            conn.execute("ALTER TABLE user_accounts ADD COLUMN admin_role TEXT NOT NULL DEFAULT 'USER'")
        if "admin_permissions" not in accts_cols:
            conn.execute(
                "ALTER TABLE user_accounts ADD COLUMN admin_permissions TEXT NOT NULL DEFAULT '[]'"
            )
        if "account_status" not in accts_cols:
            conn.execute("ALTER TABLE user_accounts ADD COLUMN account_status TEXT NOT NULL DEFAULT 'active'")
        if "last_login_at" not in accts_cols:
            conn.execute("ALTER TABLE user_accounts ADD COLUMN last_login_at TIMESTAMPTZ")
        if "pilot_cohort_number" not in accts_cols:
            conn.execute(
                "ALTER TABLE user_accounts ADD COLUMN pilot_cohort_number INTEGER"
            )
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_user_accounts_pilot_cohort_number
               ON user_accounts(pilot_cohort_number)
               WHERE pilot_cohort_number IS NOT NULL"""
        )
        # حساب SUPER_ADMIN العام ليس عضوًا في أي شركة. يبقى NULL ممنوعًا على
        # USER/ADMIN بواسطة القيد التالي، وتظل بيانات الشركات خلف tenant guards.
        company_id_nullable = conn.execute(
            """SELECT is_nullable FROM information_schema.columns
               WHERE table_schema=current_schema()
                 AND table_name='user_accounts' AND column_name='company_id'"""
        ).fetchone()
        if company_id_nullable and company_id_nullable["is_nullable"] == "NO":
            conn.execute(
                "ALTER TABLE user_accounts ALTER COLUMN company_id DROP NOT NULL"
            )
        conn.execute(
            """ALTER TABLE user_accounts
               DROP CONSTRAINT IF EXISTS user_accounts_company_or_global_super_admin"""
        )
        account_scope_constraint = conn.execute(
            """SELECT 1 FROM pg_constraint
               WHERE conname='user_accounts_company_or_system_admin'"""
        ).fetchone()
        if not account_scope_constraint:
            conn.execute(
                """ALTER TABLE user_accounts
                   ADD CONSTRAINT user_accounts_company_or_system_admin
                   CHECK (
                     company_id IS NOT NULL
                     OR (admin_role IN ('ADMIN','SUPER_ADMIN') AND is_admin=1)
                   )"""
            )
        conn.execute(
            "UPDATE user_accounts SET admin_role='SUPER_ADMIN' "
            "WHERE is_admin=1 AND COALESCE(admin_role,'USER')='USER'"
        )
        _seed_pilot_cohort(conn)
        company_cols_for_admin = _columns_of(conn, "companies")
        if "lifecycle_status" not in company_cols_for_admin:
            conn.execute(
                "ALTER TABLE companies ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'Active'"
            )
        if "company_code" not in company_cols_for_admin:
            conn.execute("ALTER TABLE companies ADD COLUMN company_code TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_company_code "
                "ON companies(company_code)"
            )
        if "contact_email" not in company_cols_for_admin:
            conn.execute("ALTER TABLE companies ADD COLUMN contact_email TEXT")
        conn.execute("""CREATE TABLE IF NOT EXISTS admin_audit_log (
            audit_id TEXT PRIMARY KEY,
            actor_account_id TEXT NOT NULL REFERENCES user_accounts(account_id),
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT,
            company_id TEXT REFERENCES companies(company_id),
            reason TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_admin_audit_created "
            "ON admin_audit_log(created_at DESC)"
        )
        conn.execute("""CREATE TABLE IF NOT EXISTS company_invitations (
            invitation_id TEXT PRIMARY KEY,
            company_id TEXT NOT NULL REFERENCES companies(company_id),
            account_id TEXT NOT NULL REFERENCES user_accounts(account_id),
            email TEXT NOT NULL,
            company_role TEXT NOT NULL
              CHECK (company_role IN ('COMPANY_OWNER','COMPANY_MEMBER')),
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TIMESTAMPTZ NOT NULL,
            used_at TIMESTAMPTZ,
            cancelled_at TIMESTAMPTZ,
            created_by TEXT NOT NULL REFERENCES user_accounts(account_id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_sent_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_company_invitations_company "
            "ON company_invitations(company_id,created_at DESC)"
        )
        conn.execute("""CREATE TABLE IF NOT EXISTS admin_notification_outbox (
            notification_id TEXT PRIMARY KEY,
            notification_type TEXT NOT NULL,
            recipient_email TEXT NOT NULL,
            company_id TEXT REFERENCES companies(company_id),
            status TEXT NOT NULL CHECK (status IN ('queued','sent','failed')),
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_by TEXT NOT NULL REFERENCES user_accounts(account_id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            sent_at TIMESTAMPTZ,
            error_code TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TIMESTAMPTZ,
            next_attempt_at TIMESTAMPTZ,
            delivery_lock_token TEXT,
            delivery_locked_at TIMESTAMPTZ
        )""")
        conn.execute(
            "ALTER TABLE admin_notification_outbox "
            "ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute(
            "ALTER TABLE admin_notification_outbox "
            "ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ"
        )
        conn.execute(
            "ALTER TABLE admin_notification_outbox "
            "ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ"
        )
        conn.execute(
            "ALTER TABLE admin_notification_outbox "
            "ADD COLUMN IF NOT EXISTS delivery_lock_token TEXT"
        )
        conn.execute(
            "ALTER TABLE admin_notification_outbox "
            "ADD COLUMN IF NOT EXISTS delivery_locked_at TIMESTAMPTZ"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_admin_notification_outbox_status "
            "ON admin_notification_outbox(status,created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_admin_notification_outbox_retry "
            "ON admin_notification_outbox(notification_type,status,next_attempt_at,created_at)"
        )

        conn.commit()
        # طبقة المعرفة — ترقية غير هدّامة سواء كانت القاعدة جديدة أو قديمة.
        from sana_knowledge import ensure_schema as ensure_knowledge_schema
        ensure_knowledge_schema(conn)
        from sana_scan import ensure_schema as ensure_scan_schema
        ensure_scan_schema(conn)
        from sana_growth_os import ensure_schema as ensure_growth_schema, seed_growth_os
        ensure_growth_schema(conn)
        seed_growth_os(conn)
        from sana_company_memory import ensure_schema as ensure_company_memory_schema
        ensure_company_memory_schema(conn)
        from sana_growth_engine import ensure_schema as ensure_growth_engine_schema, seed_growth_engine
        seed_growth_engine(conn)
        ensure_growth_engine_schema(conn)
        from sana_revenue_cycle import ensure_schema as ensure_revenue_cycle_schema
        ensure_revenue_cycle_schema(conn)
        from sana_decision_room import ensure_schema as ensure_decision_room_schema
        ensure_decision_room_schema(conn)
        from zubair_deal_brain import ensure_schema as ensure_zubair_schema
        ensure_zubair_schema(conn)
        from drive_index import ensure_schema as ensure_drive_index_schema
        ensure_drive_index_schema(conn)
        from sana_billing import ensure_schema as ensure_billing_schema
        ensure_billing_schema(conn)
        conn.commit()

        # لكل شركة بلا رمز دعوة (سواء قاعدة بيانات جديدة أو قديمة) — ولّد رمزًا فريدًا
        for row in conn.execute("SELECT company_id FROM companies WHERE signup_code IS NULL").fetchall():
            code = f"SANA-{row[0]}-{secrets.token_hex(3).upper()}"
            conn.execute("UPDATE companies SET signup_code=? WHERE company_id=?", (code, row[0]))
        conn.commit()
        return fresh
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def create_billing_test_schema():
    """Create and select a disposable schema for one billing test run."""
    global DATABASE_SCHEMA
    global _BILLING_TEST_SCHEMA_PREVIOUS
    global _BILLING_TEST_SCHEMA_PREVIOUS_ENV
    global _BILLING_TEST_SCHEMA_STATE_SAVED
    if not _BILLING_TEST_SCHEMA_STATE_SAVED:
        _BILLING_TEST_SCHEMA_PREVIOUS = DATABASE_SCHEMA
        _BILLING_TEST_SCHEMA_PREVIOUS_ENV = os.environ.get("SANA_DATABASE_SCHEMA")
        _BILLING_TEST_SCHEMA_STATE_SAVED = True
    cleanup_stale_billing_test_schemas()
    schema = build_billing_test_schema_name()
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA {_quote_schema_identifier(schema)}")
    finally:
        conn.close()
    DATABASE_SCHEMA = schema
    os.environ["SANA_DATABASE_SCHEMA"] = schema
    return schema


def _billing_schema_cleanup_is_allowed():
    """Only allow orphan cleanup from a non-production process."""
    environment = os.environ.get("SANA_ENV", "").strip().lower()
    if environment in {"production", "prod"}:
        return False
    if os.environ.get("REPLIT_DEPLOYMENT", "").strip().lower() in {
        "1", "true", "yes", "on",
    }:
        return False
    return True


def cleanup_stale_billing_test_schemas(max_age_hours=24, now=None):
    """Drop only old, timestamped, inactive billing-test schemas.

    PostgreSQL does not store a schema creation timestamp.  Therefore only
    names created by ``build_billing_test_schema_name`` are eligible: legacy
    prefix-only names are intentionally left untouched because their age
    cannot be proved safely.
    """
    if not _billing_schema_cleanup_is_allowed():
        return {
            "status": "skipped_production",
            "deleted": [],
            "skipped": [],
        }
    try:
        max_age_seconds = float(max_age_hours) * 60 * 60
    except (TypeError, ValueError):
        raise ValueError("BILLING_TEST_SCHEMA_MAX_AGE_INVALID")
    if max_age_seconds <= 0:
        raise ValueError("BILLING_TEST_SCHEMA_MAX_AGE_INVALID")

    if now is None:
        now_epoch = time.time()
    elif isinstance(now, datetime):
        now_epoch = now.timestamp()
    else:
        now_epoch = float(now)

    deleted = []
    skipped = []
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT n.nspname,
                       EXISTS (
                           SELECT 1
                           FROM pg_stat_activity a
                           WHERE a.pid <> pg_backend_pid()
                             AND a.application_name =
                                 'sana-billing-test:' || n.nspname
                       ) AS is_active
                FROM pg_namespace n
                WHERE n.nspname LIKE %s
                ORDER BY n.nspname
                """,
                (f"{BILLING_TEST_SCHEMA_PREFIX}%",),
            )
            candidates = cursor.fetchall()
            for schema, is_active in candidates:
                schema = str(schema)
                created_at = parse_billing_test_schema_created_at(schema)
                age_seconds = (
                    None if created_at is None else now_epoch - created_at
                )
                reason = None
                if schema == DATABASE_SCHEMA:
                    reason = "active_schema"
                elif is_active:
                    reason = "active_connection"
                elif created_at is None:
                    reason = "untracked_age"
                elif age_seconds < 0:
                    reason = "future_timestamp"
                elif age_seconds < max_age_seconds:
                    reason = "too_new"
                if reason is not None:
                    skipped.append({"schema": schema, "reason": reason})
                    continue
                cursor.execute(
                    f"DROP SCHEMA IF EXISTS {_quote_schema_identifier(schema)} CASCADE"
                )
                deleted.append(schema)
    finally:
        conn.close()
    return {
        "status": "completed",
        "deleted": deleted,
        "skipped": skipped,
    }


def init_billing_test_db():
    """Prepare billing tables in the currently selected test schema."""
    conn = _connect_pg()
    try:
        core_schema_ready = has_required_tables(
            conn,
            (
                "companies",
                "user_accounts",
                "admin_audit_log",
                "admin_notification_outbox",
            ),
        )
        billing_schema_ready = (
            core_schema_ready
            and has_required_tables(
                conn,
                (
                    "sana_billing_settings",
                    "sana_billing_coupons",
                    "sana_company_subscriptions",
                    "sana_billing_events",
                    "sana_billing_cleanup_runs",
                    "sana_billing_cleanup_skips",
                ),
            )
            and has_required_columns(
                conn,
                {
                    "sana_company_subscriptions": (
                        "stripe_checkout_session_created_at",
                    ),
                },
            )
        )
        if billing_schema_ready:
            return False
        if core_schema_ready:
            from sana_billing import ensure_schema as ensure_billing_schema

            ensure_billing_schema(conn)
            return False
    finally:
        conn.close()

    init_db()
    return True


def seed_db():
    """يزرع بيانات أثر مشرق كأول شركة حقيقية على النظام."""
    conn = _connect_pg()
    if conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] > 0:
        conn.close()
        return  # already seeded

    conn.execute("""INSERT INTO companies
        (company_id, name, sector, city, stage, employee_count, annual_revenue, vision, main_goal)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        ("C001", "أثر مشرق", "عطور - تصنيع", "المدينة المنورة", "نمو", 8, 350000,
         "أن نكون أثر يُشم قبل أن يُرى", "رفع قيمة الشركة عبر أصل المعرفة"))

    conn.execute("""INSERT INTO users (user_id, company_id, name, role, department, status)
        VALUES (?,?,?,?,?,?)""",
        ("U001", "C001", "الزبير", "Owner", "الإدارة العامة", "نشط"))

    assets = [
        ("A001", "C001", "Knowledge", "أصل المعرفة", 18, 70, "U001", "يحتاج تطوير"),
        ("A002", "C001", "Operations", "أصل التشغيل", 41, 55, "U001", "متوسط"),
        ("A003", "C001", "Brand", "أصل البراند", 72, 20, "U001", "قوي"),
        ("A004", "C001", "Data", "أصل البيانات", 65, 30, "U001", "قوي"),
        ("A005", "C001", "Independence", "أصل الاستقلال", 22, 82, "U001", "مهدد"),
    ]
    conn.executemany("""INSERT INTO assets
        (asset_id, company_id, asset_type, asset_name, current_score, fragility_score, owner_user_id, status)
        VALUES (?,?,?,?,?,?,?,?)""", assets)

    conn.execute("""INSERT INTO cases
        (case_id, company_id, case_title, case_type, case_status, declared_problem, real_question,
         related_asset_id, confidence_score, value_impact_estimate)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("CS001", "C001", "ضعف توثيق العمليات", "Diagnostic Case", "Diagnosed",
         "نشعر أننا نعتمد كثيرًا على شخص واحد",
         "هل غياب أي موظف رئيسي يوقف الإنتاج فعليًا؟",
         "A001", 75, "رفع أصل المعرفة بمقدار 12 نقطة خلال شهر"))

    conn.execute("""INSERT INTO evidence
        (evidence_id, company_id, case_id, asset_id, title, source_type, confidence)
        VALUES (?,?,?,?,?,?,?)""",
        ("E001", "C001", "CS001", "A001",
         "لا يوجد ملف SOP موثق لأي عملية تصنيع", "ملاحظة مباشرة", 55))

    conn.execute("""INSERT INTO decisions
        (decision_id, company_id, case_id, asset_id, title, recommended_action, reason,
         confidence_score, expected_impact, status)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("D001", "C001", "CS001", "A001", "توثيق أول SOP للتصنيع",
         "بناء ملف SOP واحد لعملية التعبئة خلال 7 أيام",
         "أضعف مؤشر حاليًا هو تغطية التوثيق (22%)، وهو يهدد الاستقلال والجودة",
         75, "رفع أصل المعرفة من 18 إلى 30 خلال شهر", "قيد التنفيذ"))

    conn.execute("""INSERT INTO tasks
        (task_id, company_id, decision_id, title, owner_user_id, due_date, status, priority)
        VALUES (?,?,?,?,?,?,?,?)""",
        ("TSK001", "C001", "D001", "كتابة أول مسودة SOP لعملية التعبئة",
         "U001", "2026-07-14", "قيد التنفيذ", "عالية"))

    conn.commit()
    conn.close()


def seed_decision_impacts():
    """يربط القرار D001 بعدة أصول دفعة واحدة — فقط إذا كان الجدول فارغًا."""
    conn = _connect_pg()
    if conn.execute("SELECT COUNT(*) FROM decision_asset_impacts").fetchone()[0] > 0:
        conn.close()
        return  # already seeded

    impacts = [
        ("IMP001", "D001", "A001", 5, 1),
        ("IMP002", "D001", "A002", 3, 0),
        ("IMP003", "D001", "A005", 3, 0),
    ]
    conn.executemany("""INSERT INTO decision_asset_impacts
        (impact_id, decision_id, asset_id, score_impact, is_primary)
        VALUES (?,?,?,?,?)""", impacts)
    conn.commit()
    conn.close()


def seed_knowledge_db():
    """يضيف بذور مكتبة سنع المنظمة بشكل idempotent دون لمس بيانات الشركات."""
    from sana_knowledge import seed_knowledge
    conn = _connect_pg()
    seed_knowledge(conn)
    conn.commit()
    conn.close()


# ------------------------------------------------------------------
# Views — الصفحات الأربع
# ------------------------------------------------------------------

@app.route("/")
def entry():
    account = current_account()
    if account:
        return redirect(_company_start_redirect(account))
    return render_template("00-landing.html")


@app.route("/home")
def ceo_home():
    account = current_account()
    if account:
        start = _company_start_redirect(account)
        if start != url_for("ceo_home"):
            return redirect(start)
        return render_template("01-today-client.html", **p0_template_context())
    return render_template("01-ceo-home.html", **p0_template_context())


@app.route("/growth-os")
def growth_os_page():
    alias = _client_only_alias(url_for("ceo_home"))
    if alias:
        return alias
    return render_template("22-growth-os.html", default_company_id=default_company_id())


@app.route("/case/new")
def new_case():
    account = current_account()
    if account:
        start = _company_start_redirect(account)
        if start != url_for("new_case") and start != url_for("ceo_home"):
            return redirect(start)
        first_case = get_db().execute(
            "SELECT case_id FROM cases WHERE company_id=? ORDER BY opened_at ASC LIMIT 1",
            (account["company_id"],),
        ).fetchone()
        if first_case:
            return redirect(url_for("case_workspace", case_id=first_case["case_id"]))
        return render_template("04-new-case-client.html", **p0_template_context())
    return render_template("04-new-case.html", **p0_template_context())


@app.route("/case/<case_id>/next-step")
def case_next_step(case_id):
    account = current_account()
    if not account and not is_admin_preview():
        return redirect(url_for("login", next=request.full_path))
    db = get_db()
    case = db.execute("SELECT company_id FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not case:
        abort(404)
    if account and case["company_id"] != account["company_id"]:
        abort(403)
    return render_template("02b-next-step.html", case_id=case_id)


@app.route("/api/cases/<case_id>/next-step")
def case_next_step_data(case_id):
    """
    يحسب المسارات الحتمية للخطوة التالية بناءً على بيانات CS002 الحقيقية.
    قواعد if/else صارمة — لا ذكاء اصطناعي.
    """
    db = get_db()
    case = db.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not case:
        return jsonify({"success": False, "error": "CASE_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(case["company_id"])
    if guard:
        return guard

    company_id = case["company_id"]
    confidence = case["confidence_score"] or 0

    # عدد الأدلة
    ev_count = db.execute(
        "SELECT COUNT(*) as c FROM evidence WHERE case_id=?", (case_id,)
    ).fetchone()["c"]

    # القرارات المقترحة غير المعتمدة
    proposed_decisions = db.execute(
        "SELECT decision_id, title FROM decisions WHERE case_id=? AND status='مقترح'",
        (case_id,)
    ).fetchall()

    # مهام هذه القضية (عبر قراراتها) التي لم تبدأ
    all_decisions = db.execute(
        "SELECT decision_id FROM decisions WHERE case_id=?", (case_id,)
    ).fetchall()
    decision_ids = [d["decision_id"] for d in all_decisions]
    not_started_count = 0
    if decision_ids:
        ph = ",".join("?" * len(decision_ids))
        not_started_count = db.execute(
            f"SELECT COUNT(*) as c FROM tasks WHERE decision_id IN ({ph}) AND status='لم تبدأ'",
            decision_ids
        ).fetchone()["c"]

    # SOP مرتبط بالقضية — لا يوجد ربط رسمي حالياً
    sop_linked = False  # يصبح True عند إضافة case_id لجدول methodology_docs

    # إثباتات مطبّقة (impact_applied=1) مع تفاصيل الأصل الحقيقية
    applied_impacts = db.execute("""
        SELECT DISTINCT
               te.evidence_id,
               te.task_id,
               te.verification_reason,
               t.title          AS task_title,
               t.expected_asset_impact,
               a.asset_id,
               a.asset_name,
               a.current_score,
               (t.expected_asset_impact::jsonb->>'score_impact')::int AS score_delta
        FROM task_evidence te
        JOIN tasks t ON te.task_id = t.task_id
        JOIN assets a ON a.asset_id = (t.expected_asset_impact::jsonb->>'asset_id')
        WHERE te.company_id = ? AND te.impact_applied = 1
          AND t.expected_asset_impact IS NOT NULL
        ORDER BY te.evidence_id
    """, (company_id,)).fetchall()

    # ── بناء المسارات ──────────────────────────────────────────
    paths = []

    # مسار 1: ثقة التشخيص < 70%
    if confidence < 70:
        paths.append({
            "id":       "understand",
            "priority": 1,
            "icon":     "🧠",
            "label":    "افهم القضية أكثر",
            "headline": f"ثقة التشخيص {confidence}٪ — أقل من الحد المطلوب (70٪)",
            "details":  [
                f"عدد الأدلة الحالي: {ev_count} دليل",
                f"ثقة سنع في التشخيص: {confidence}٪",
                "أضف أدلة جديدة أو أجب على أسئلة التشخيص لرفع الثقة",
            ],
            "action": "أضف أدلة جديدة أو أجب على الأسئلة المتبقية لرفع الثقة",
            "extra":  [],
        })

    # مسار 2: قرارات مقترحة أو مهام لم تبدأ
    if proposed_decisions or not_started_count > 0:
        details = []
        if proposed_decisions:
            details.append(f"{len(proposed_decisions)} قرار مقترح في انتظار الاعتماد")
            for d in proposed_decisions[:2]:
                details.append(f"← {d['title']}")
        if not_started_count > 0:
            details.append(f"{not_started_count} مهمة لم تبدأ بعد")
        paths.append({
            "id":       "execute",
            "priority": 2,
            "icon":     "⚡",
            "label":    "ابدأ التنفيذ",
            "headline": f"{len(proposed_decisions)} قرار مقترح + {not_started_count} مهمة لم تبدأ",
            "details":  details,
            "action":   "اعتمد القرارات المقترحة وابدأ أولى المهام المعلّقة",
            "extra":    [],
        })

    # مسار 3: لا SOP مرتبط
    if not sop_linked:
        paths.append({
            "id":       "system",
            "priority": 3,
            "icon":     "📂",
            "label":    "ابنِ نظام الشركة",
            "headline": "لا توجد إجراءات SOP موثّقة مرتبطة بهذه القضية",
            "details":  [
                "إجراءات العمل: غير موثّقة رسمياً لهذه القضية",
                "وثائق المنهجية الموجودة غير مربوطة بقضية محددة",
            ],
            "action":   "ابدأ ببناء إجراء واحد مكتوب لأكثر مهمة تتكرر في هذه القضية",
            "extra":    [],
        })

    # مسار 4: يوجد impact_applied=1
    if applied_impacts:
        impact_details = []
        seen = set()
        for imp in applied_impacts:
            key = (imp["asset_id"], imp["score_delta"])
            if key not in seen:
                seen.add(key)
                delta = imp["score_delta"] or 0
                impact_details.append(
                    f"{imp['asset_name']}: +{delta} نقطة → الآن {imp['current_score']}/100"
                )
        paths.append({
            "id":       "impact",
            "priority": 4,
            "icon":     "📈",
            "label":    "شاهد أثر التنفيذ",
            "headline": f"{len(seen)} أصل تغيّر بفضل إثباتات مُحقَّقة",
            "details":  impact_details,
            "action":   "تابع رفع إثباتات لمهامك المنجزة لتسجيل التأثير الحقيقي",
            "extra":    [],
        })

    return jsonify({
        "success": True,
        "data": {
            "case_id":    case_id,
            "case_title": case["case_title"],
            "confidence": confidence,
            "paths":      paths,
        }
    })


@app.route("/case/<case_id>")
def case_workspace(case_id):
    """مسار legacy: لا يعرض ملفًا؛ يوجّه إلى نتيجة التشخيص الحالية."""
    account = current_account()
    if not account and not is_admin_preview():
        return redirect(url_for("login", next=request.full_path))
    db = get_db()
    case = db.execute("SELECT company_id FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not case:
        abort(404)
    if account and case["company_id"] != account["company_id"]:
        abort(403)
    return redirect(url_for("case_result", case_id=case_id))


@app.route("/case/<case_id>/result")
def case_result(case_id):
    """نتيجة التشخيص الحالية؛ القرار الغائب حالة طبيعية داخل هذه الصفحة."""
    account = current_account()
    if not account and not is_admin_preview():
        return redirect(url_for("login", next=request.full_path))
    db = get_db()
    case = db.execute("SELECT company_id FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not case:
        abort(404)
    if account and case["company_id"] != account["company_id"]:
        abort(403)
    if account and not is_admin_preview():
        return render_template(
            "02-case-workspace-client.html",
            case_id=case_id,
            auto_scan_retry=request.args.get("scan") in {"failed", "required"},
            **p0_template_context(),
        )
    return render_template(
        "02-case-workspace.html",
        case_id=case_id,
        **p0_template_context(),
    )


@app.route("/sop-builder")
def sop_builder():
    alias = _client_only_alias(url_for("ceo_home"))
    if alias:
        return alias
    return render_template("05-sop-builder.html", default_company_id=default_company_id())


@app.route("/assessment")
def assessment():
    alias = _client_only_alias(url_for("business_passport"))
    if alias:
        return alias
    return render_template("06-assessment.html", **p0_template_context())


@app.route("/passport")
def business_passport():
    account = current_account()
    if account:
        start = _company_start_redirect(account)
        if start != url_for("business_passport") and start != url_for("ceo_home"):
            return redirect(start)
        scan_redirect = _client_scan_result_redirect(account["company_id"])
        if scan_redirect:
            return scan_redirect
    return render_template("03-business-passport.html", **p0_template_context())


@app.route("/services")
def services_page():
    alias = _client_only_alias(url_for("ceo_home"))
    if alias:
        return alias
    return render_template("07-services.html", default_company_id=default_company_id())


@app.route("/sector-select")
def sector_select():
    """شاشة اختيار القطاع — تُعرض للشركات الموجودة التي لم تُحدِّد قطاعها بعد."""
    account = current_account()
    if not account:
        return redirect(url_for("login"))
    # Alias قديم: الإعداد الموحد هو نقطة البداية الوحيدة للحساب.
    return redirect(url_for("onboarding"))


# ------------------------------------------------------------------
# تسجيل الدخول الحقيقي للعملاء — بريد إلكتروني + كلمة مرور مشفَّرة
# ------------------------------------------------------------------

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_account():
        return redirect(_company_start_redirect(current_account()))
    if request.method == "GET":
        return render_template("09-signup.html")

    body = request.get_json(silent=True) or request.form
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    referral_source = (body.get("referral_source") or "").strip() or None

    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return jsonify({"success": False, "error": "INVALID_EMAIL", "message": "الرجاء إدخال بريد إلكتروني صحيح."}), 400
    if len(password) < 8:
        return jsonify({"success": False, "error": "WEAK_PASSWORD", "message": "كلمة المرور يجب أن تكون 8 أحرف على الأقل."}), 400

    db = get_db()
    existing = db.execute("SELECT account_id FROM user_accounts WHERE email=?", (email,)).fetchone()
    if existing:
        return jsonify({"success": False, "error": "EMAIL_TAKEN", "message": "هذا البريد الإلكتروني مسجَّل بالفعل."}), 409

    # كل حساب جديد يحصل تلقائيًا على شركة جديدة فارغة خاصة به — لا مشاركة
    # مع أي حساب/شركة أخرى، ولا حاجة لرمز دعوة مسبق.
    company_id = "C" + uuid.uuid4().hex[:8].upper()
    db.execute(
        "INSERT INTO companies (company_id, name, sector, employee_count) VALUES (?,?,?,?)",
        (company_id, "شركة جديدة", None, None)
    )
    default_assets = [
        ("Knowledge", "أصل المعرفة"),
        ("Operations", "أصل التشغيل"),
        ("Brand", "أصل البراند"),
        ("Data", "أصل البيانات"),
        ("Independence", "أصل الاستقلال"),
    ]
    for asset_type, asset_name in default_assets:
        asset_id = "A" + uuid.uuid4().hex[:8].upper()
        db.execute(
            """INSERT INTO assets (asset_id, company_id, asset_type, asset_name, current_score, fragility_score, status)
               VALUES (?,?,?,?,?,?,?)""",
            (asset_id, company_id, asset_type, asset_name, 0, 100, "غير مقيَّم")
        )

    account_id = "ACC" + uuid.uuid4().hex[:10].upper()
    db.execute(
        """INSERT INTO user_accounts
           (account_id,email,password_hash,company_id,referral_source,admin_role)
           VALUES (?,?,?,?,?,?)""",
        (
            account_id, email, generate_password_hash(password), company_id,
            referral_source, "COMPANY_OWNER",
        ),
    )
    _assign_pilot_slot(db, account_id)
    db.commit()

    session.clear()
    session["account_id"] = account_id
    session["company_id"] = company_id
    session["email"] = email
    session.permanent = True

    return jsonify({"success": True, "data": {"redirect": "/onboarding", "company_id": company_id}}), 201


@app.route("/onboarding", methods=["GET", "POST"])
def onboarding():
    """شاشة قصيرة بعد إنشاء الحساب مباشرة — تحدّث بيانات الشركة الفارغة التي أُنشئت تلقائيًا."""
    account = current_account()
    if not account:
        return redirect(url_for("login"))
    company = get_db().execute(
        """SELECT name, sector, sector_other, business_type, respondent_role,
                  employee_count, business_description,
                  goal_90_days, primary_challenge, sds_done
           FROM companies WHERE company_id=?""",
        (account["company_id"],),
    ).fetchone()
    if company and company["sector"] and company["name"] != "شركة جديدة":
        return redirect(
            url_for("ceo_home") if company["sds_done"] else url_for("discovery")
        )

    if request.method == "GET":
        return render_template(
            "11-onboarding.html",
            company=company or {},
            sectors=SECTORS,
            sector_business_types=SECTOR_BUSINESS_TYPES,
            respondent_roles=RESPONDENT_ROLES,
            onboarding_submit_url=url_for("onboarding"),
            discovery_url=url_for("discovery"),
        )

    body = request.get_json(silent=True) or request.form
    name = (body.get("name") or "").strip()
    sector_key = (body.get("sector") or "").strip() or None
    sector_other = (body.get("sector_other") or "").strip() or None
    business_type = (body.get("business_type") or "").strip() or None
    respondent_role = (body.get("respondent_role") or "").strip() or None
    employee_count = body.get("employee_count")
    legacy_business_description = (
        body.get("business_description") or ""
    ).strip()
    goal_90_days = (body.get("goal_90_days") or "").strip()
    primary_challenge = (body.get("primary_challenge") or "").strip()
    try:
        employee_count = int(employee_count) if employee_count not in (None, "") else None
    except (TypeError, ValueError):
        employee_count = None

    if not name:
        return jsonify({"success": False, "error": "MISSING_NAME", "message": "اسم الشركة مطلوب."}), 400
    if not sector_key:
        return jsonify({"success": False, "error": "MISSING_SECTOR", "message": "تحديد القطاع إلزامي قبل المتابعة."}), 400
    if sector_key not in SECTOR_KEYS:
        return jsonify({"success": False, "error": "INVALID_SECTOR", "message": "اختر قطاعًا من القائمة."}), 400
    if sector_key == "other" and not sector_other:
        return jsonify({"success": False, "error": "MISSING_SECTOR_OTHER", "message": "يرجى كتابة وصف قطاعك عند اختيار 'أخرى'."}), 400
    if not goal_90_days or not primary_challenge:
        return jsonify({
            "success": False,
            "error": "INCOMPLETE_COMPANY_SETUP",
            "message": "أكمل النتيجة المطلوبة والتحدي قبل المتابعة.",
        }), 400
    role_labels = {item["key"]: item["label"] for item in RESPONDENT_ROLES}
    structured_profile = bool(business_type or respondent_role)
    if structured_profile:
        allowed_business_types = {
            item["key"] for item in SECTOR_BUSINESS_TYPES.get(sector_key, [])
        }
        if business_type not in allowed_business_types:
            return jsonify({
                "success": False,
                "error": "INVALID_BUSINESS_TYPE",
                "message": "اختر نوع النشاط المناسب للقطاع.",
            }), 400
        if respondent_role not in role_labels:
            return jsonify({
                "success": False,
                "error": "INVALID_RESPONDENT_ROLE",
                "message": "اختر صفتك في العمل.",
            }), 400
    elif not legacy_business_description:
        return jsonify({
            "success": False,
            "error": "MISSING_BUSINESS_PROFILE",
            "message": "اختر نوع النشاط وصفتك في العمل.",
        }), 400
    if structured_profile:
        business_type_labels = {
            item["key"]: item["label"]
            for item in SECTOR_BUSINESS_TYPES[sector_key]
        }
        sector_labels = {item["key"]: item["label"] for item in SECTORS}
        business_description = (
            f"{sector_other} — {business_type_labels[business_type]}"
            if sector_key == "other"
            else f"{sector_labels[sector_key]} — {business_type_labels[business_type]}"
        )
    else:
        business_description = legacy_business_description

    db = get_db()
    db.execute(
        """UPDATE companies
           SET name=?, sector=?, sector_other=?, business_type=?,
               respondent_role=?, employee_count=?, business_description=?,
               goal_90_days=?, primary_challenge=?
           WHERE company_id=?""",
        (
            name,
            sector_key,
            sector_other if sector_key == "other" else None,
            business_type,
            respondent_role,
            employee_count,
            business_description,
            goal_90_days,
            primary_challenge,
            account["company_id"],
        )
    )
    db.commit()

    return jsonify({
        "success": True,
        "data": {"redirect": url_for("discovery")},
    })


@app.route("/api/testing/reset-experience", methods=["POST"])
def reset_experience():
    """Destructive test-only reset for the currently authenticated company."""
    account = current_account()
    if not _test_reset_allowed(account):
        return jsonify({
            "success": False,
            "error": "TEST_RESET_NOT_ALLOWED",
            "message": "إعادة ضبط التجربة غير متاحة لهذا الحساب أو في هذه البيئة.",
        }), 403

    company_id = account["company_id"]
    db = get_db()
    try:
        deleted = _reset_company_experience(db, company_id)
        _admin_audit(
            db,
            account["account_id"],
            "TEST_EXPERIENCE_RESET",
            "company",
            company_id,
            company_id,
            reason="إعادة حساب اختبار إلى بداية رحلة سنع",
            metadata={
                "deleted_rows": sum(deleted.values()),
                "affected_tables": [
                    table for table, count in deleted.items() if count
                ],
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        app.logger.exception(
            "Test experience reset failed for company %s", company_id,
        )
        return jsonify({
            "success": False,
            "error": "TEST_RESET_FAILED",
            "message": "تعذر إعادة ضبط التجربة. لم تُحفظ أي تغييرات.",
        }), 500

    session.modified = True
    return jsonify({
        "success": True,
        "data": {"redirect": url_for("onboarding")},
    })


@app.route("/pricing")
def pricing_page():
    return render_template("13-pricing.html")


@app.route("/api/billing/offer")
def billing_offer_api():
    from sana_billing import public_offer
    account = current_account()
    company_id = account.get("company_id") if account else None
    return jsonify({
        "success": True,
        "data": public_offer(get_db(), company_id),
        "authenticated": bool(account and company_id),
    })


@app.route("/api/billing/quote", methods=["POST"])
def billing_quote_api():
    from sana_billing import quote
    body = request.get_json(silent=True) or {}
    try:
        result = quote(get_db(), body.get("code"))
    except ValueError:
        return jsonify({
            "success": False,
            "error": "COUPON_INVALID",
            "message": "الكود غير صالح أو انتهى استخدامه.",
        }), 400
    return jsonify({"success": True, "data": result})


@app.route("/api/billing/checkout", methods=["POST"])
def billing_checkout_api():
    from sana_billing import activate_free, create_checkout, quote
    account = current_account()
    if not account or not account.get("company_id"):
        return jsonify({
            "success": False,
            "error": "LOGIN_REQUIRED",
            "redirect": "/signup",
        }), 401
    body = request.get_json(silent=True) or {}
    try:
        checkout_quote = quote(get_db(), body.get("code"))
    except ValueError:
        return jsonify({
            "success": False,
            "error": "COUPON_INVALID",
            "message": "الكود غير صالح أو انتهى استخدامه.",
        }), 400
    db = get_db()
    if checkout_quote["amount_now_minor"] == 0:
        subscription = activate_free(
            db,
            account["company_id"],
            coupon_code=checkout_quote["coupon_code"],
        )
        _admin_audit(
            db, account["account_id"], "billing_free_activation",
            "company_subscription", subscription["subscription_id"],
            account["company_id"], "تفعيل مجاني بكود خصم صالح",
            {"coupon_code": checkout_quote["coupon_code"]},
        )
        db.commit()
        return jsonify({
            "success": True,
            "data": {"free": True, "redirect": "/home"},
        })
    try:
        base_url = request.url_root.rstrip("/")
        checkout = create_checkout(
            db,
            company_id=account["company_id"],
            email=account["email"],
            amount_minor=checkout_quote["amount_now_minor"],
            coupon_code=checkout_quote["coupon_code"],
            base_url=base_url,
        )
    except Exception as exc:
        app.logger.warning("Stripe checkout creation failed: %s", type(exc).__name__)
        return jsonify({
            "success": False,
            "error": "PAYMENT_PROVIDER_UNAVAILABLE",
            "message": "تعذر فتح الدفع الآن. حاول مرة أخرى بعد قليل.",
        }), 503
    return jsonify({"success": True, "data": checkout})


@app.route("/billing/success")
def billing_success_page():
    from sana_billing import complete_checkout, retrieve_checkout
    session_id = (request.args.get("session_id") or "").strip()
    if not session_id:
        return redirect(url_for("pricing_page"))
    try:
        checkout = retrieve_checkout(session_id)
        complete_checkout(get_db(), checkout)
    except Exception as exc:
        app.logger.warning("Stripe checkout verification failed: %s", type(exc).__name__)
        return redirect(url_for("pricing_page", payment="pending"))
    return redirect(url_for("ceo_home", payment="success"))


@app.route("/api/billing/stripe-webhook", methods=["POST"])
@csrf.exempt
def billing_stripe_webhook():
    from sana_billing import complete_checkout, process_stripe_event, verify_webhook
    try:
        event = verify_webhook(
            request.get_data(cache=False),
            request.headers.get("Stripe-Signature"),
        )
        if event.get("type") == "checkout.session.completed":
            complete_checkout(get_db(), (event.get("data") or {}).get("object") or {})
        else:
            result = process_stripe_event(get_db(), event)
            notification = result.get("notification")
            if notification:
                _send_billing_lifecycle_notification(
                    get_db(), notification, result["company_id"]
                )
                get_db().commit()
    except Exception as exc:
        app.logger.warning("Stripe webhook rejected: %s", type(exc).__name__)
        return jsonify({"success": False}), 400
    return jsonify({"success": True})


@app.route("/api/admin/attach-account", methods=["POST"])
def admin_attach_account():
    """
    مسار توافق قديم متوقف.
    يجب استخدام مساري الدعوة أو الربط الآمنين داخل Command Center.
    """
    return jsonify({
        "success": False,
        "error": "LEGACY_ROUTE_DISABLED",
        "message": "استخدم مسار الدعوة أو الربط الآمن داخل Command Center.",
        "replacement": {
            "invite": "/api/admin/companies/<company_id>/invitations",
            "link": "/api/admin/companies/<company_id>/accounts/link",
        },
    }), 410


@app.route("/dev-preview-login")
def dev_preview_login():
    """يفتح معاينة داخلية موقعة دون نسخ هوية أي عميل إلى جلسة المتصفح."""
    key = request.args.get("admin_key", "")
    company_id = request.args.get("company_id", "")
    redirect_to = request.args.get("redirect_to", "/home")
    if not key or key != os.environ.get("ADMIN_PREVIEW_KEY", ""):
        return jsonify({"error": "unauthorized"}), 401
    db = get_db()
    acc = db.execute(
        "SELECT * FROM user_accounts WHERE company_id=%s LIMIT 1", (company_id,)
    ).fetchone()
    if not acc:
        return jsonify({"error": "company not found"}), 404
    parsed_redirect = urlparse(redirect_to)
    if parsed_redirect.scheme or parsed_redirect.netloc or not redirect_to.startswith("/") or redirect_to.startswith("//"):
        redirect_to = "/home"
    separator = "&" if "?" in redirect_to else "?"
    preview_query = urlencode({"company_id": company_id, "admin_key": key})
    return redirect(f"{redirect_to}{separator}{preview_query}")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_account():
        return redirect(_company_start_redirect(current_account()))
    if request.method == "GET":
        return render_template("10-login.html")

    body = request.get_json(silent=True) or request.form
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    next_path = _safe_next_path(body.get("next"))

    if not email or not password:
        return jsonify({"success": False, "error": "MISSING_FIELDS", "message": "البريد الإلكتروني وكلمة المرور مطلوبان."}), 400

    db = get_db()
    account = db.execute("SELECT * FROM user_accounts WHERE email=?", (email,)).fetchone()
    if not account or not check_password_hash(account["password_hash"], password):
        return jsonify({"success": False, "error": "INVALID_CREDENTIALS", "message": "البريد الإلكتروني أو كلمة المرور غير صحيحة."}), 401
    if (account.get("account_status") or "active") != "active":
        return jsonify({
            "success": False,
            "error": "ACCOUNT_DISABLED",
            "message": "هذا الحساب معطّل. تواصل مع إدارة سنع.",
        }), 403

    session.clear()
    session["account_id"] = account["account_id"]
    session["company_id"] = account["company_id"]
    session["email"] = account["email"]
    session["is_admin"] = bool(account.get("is_admin"))
    session["admin_role"] = account.get("admin_role") or (
        "SUPER_ADMIN" if account.get("is_admin") else "USER"
    )
    session["account_status"] = account.get("account_status") or "active"
    session.permanent = True
    db.execute(
        "UPDATE user_accounts SET last_login_at=now() WHERE account_id=?",
        (account["account_id"],),
    )
    if session["admin_role"] in {"ADMIN", "SUPER_ADMIN"}:
        _admin_audit(
            db, account["account_id"], "admin_login", "session",
            target_id=account["account_id"], company_id=account["company_id"],
            reason="تسجيل دخول إلى حساب إداري",
            metadata={"role": session["admin_role"]},
        )
    db.commit()

    if session["admin_role"] in {"ADMIN", "SUPER_ADMIN"}:
        redirect_path = next_path or "/admin"
    else:
        start_path = _company_start_redirect(account)
        redirect_path = (
            start_path
            if start_path != url_for("ceo_home")
            else (next_path or start_path)
        )
    return jsonify({
        "success": True,
        "data": {
            "redirect": redirect_path
        },
    })


# ─────────────────────────────────────────────────────────────────────────────
# استعادة كلمة المرور
# ─────────────────────────────────────────────────────────────────────────────

_RESET_MSG = (
    "إذا كان بريدك الإلكتروني مسجّلاً في سنع، "
    "ستصلك رسالة خلال دقائق تحتوي رابط إعادة التعيين."
)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("18-forgot-password.html")

    body  = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return jsonify({"success": False, "message": "بريد إلكتروني غير صحيح."}), 400

    # Rate limiting: 3 طلبات / ساعة / بريد (في الذاكرة)
    now          = datetime.utcnow()
    window_start = now - timedelta(hours=1)
    attempts     = [t for t in _pw_reset_attempts.get(email, []) if t > window_start]
    if len(attempts) >= 3:
        return jsonify({"success": True, "message": _RESET_MSG})   # نفس الرسالة
    attempts.append(now)
    _pw_reset_attempts[email] = attempts

    db      = get_db()
    account = db.execute(
        """SELECT account_id,email,company_id FROM user_accounts
           WHERE email=?""", (email,)
    ).fetchone()

    if account:
        token      = secrets.token_urlsafe(32)
        expires_at = now + timedelta(minutes=30)
        db.execute(
            """INSERT INTO password_reset_tokens (token, email, account_id, expires_at)
               VALUES (?, ?, ?, ?)""",
            (token, account["email"], account["account_id"], expires_at),
        )
        _admin_audit(
            db, account["account_id"], "password_reset_requested",
            "user_account", account["account_id"], account["company_id"],
            reason="طلب استعادة كلمة المرور عبر المسار العام",
            metadata={"expires_in_minutes": 30},
        )
        db.commit()

        if RESEND_API_KEY:
            reset_url = f"{APP_BASE_URL}/reset-password?token={token}"
            try:
                resend.Emails.send({
                    "from":    "سنع <noreply@sanaclarity.com>",
                    "to":      [account["email"]],
                    "subject": "إعادة تعيين كلمة المرور — سنع",
                    "html": f"""
<div dir="rtl" style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;
     padding:32px 24px;background:#F5F6FB;border-radius:14px;">
  <h2 style="color:#0B132B;margin-bottom:12px;">إعادة تعيين كلمة المرور</h2>
  <p style="color:#333940;line-height:1.8;margin-bottom:24px;">
    تلقّينا طلباً لإعادة تعيين كلمة المرور لحسابك في <strong>سنع</strong>.<br>
    الرابط صالح لمدة <strong>30 دقيقة فقط</strong> ولاستخدام واحد فقط.
  </p>
  <a href="{reset_url}"
     style="display:inline-block;background:#0B132B;color:#fff;padding:13px 28px;
            border-radius:8px;text-decoration:none;font-weight:600;font-size:15px;">
    إعادة تعيين كلمة المرور
  </a>
  <p style="color:#6E7580;font-size:12px;margin-top:28px;line-height:1.7;">
    إذا لم تطلب ذلك، تجاهل هذه الرسالة — لن يتغير حسابك.<br>
    الرابط المباشر: {reset_url}
  </p>
</div>""",
                })
            except Exception as exc:
                print(f"[Resend ERROR] {exc}", flush=True)

    # دائماً نفس الرسالة بغض النظر عن وجود البريد في النظام
    return jsonify({"success": True, "message": _RESET_MSG})


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if request.method == "GET":
        token = request.args.get("token", "").strip()
        if not token:
            return redirect("/forgot-password")
        db  = get_db()
        row = db.execute(
            "SELECT * FROM password_reset_tokens WHERE token=?", (token,)
        ).fetchone()
        now   = datetime.utcnow()
        valid = (
            row is not None
            and not row["used"]
            and row["expires_at"].replace(tzinfo=None) > now
        )
        return render_template("19-reset-password.html", token=token, expired=not valid)

    # POST — تعيين كلمة مرور جديدة
    body     = request.get_json(silent=True) or {}
    token    = (body.get("token") or "").strip()
    password = body.get("password", "")

    if not token:
        return jsonify({"success": False, "message": "رمز غير صالح."}), 400
    if not password or len(password) < 8:
        return jsonify({"success": False, "message": "كلمة المرور يجب أن تكون 8 أحرف على الأقل."}), 400

    db  = get_db()
    row = db.execute(
        "SELECT * FROM password_reset_tokens WHERE token=?", (token,)
    ).fetchone()
    now = datetime.utcnow()

    if (row is None
            or row["used"]
            or row["expires_at"].replace(tzinfo=None) <= now):
        return jsonify({"success": False, "message": "الرابط منتهي الصلاحية أو مستخدَم من قبل."}), 400

    # حدّث كلمة المرور — نفس آلية التشفير المستخدمة في التسجيل
    db.execute(
        """UPDATE user_accounts
           SET password_hash=?,account_status='active' WHERE account_id=?""",
        (generate_password_hash(password), row["account_id"]),
    )
    # استهلك الرمز فوراً (single-use)
    db.execute(
        "UPDATE password_reset_tokens SET used=true WHERE token=?", (token,)
    )
    account = db.execute(
        "SELECT company_id FROM user_accounts WHERE account_id=?",
        (row["account_id"],),
    ).fetchone()
    _admin_audit(
        db, row["account_id"], "password_reset_completed", "user_account",
        row["account_id"], account["company_id"] if account else None,
        reason="استخدام رابط إعادة تعيين صالح لمرة واحدة",
    )
    db.commit()

    return jsonify({"success": True, "message": "تم تغيير كلمة المرور بنجاح. يمكنك تسجيل الدخول الآن."})


@app.route("/accept-invitation", methods=["GET", "POST"])
def accept_company_invitation():
    if request.method == "GET":
        token = (request.args.get("token") or "").strip()
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest() if token else ""
        row = get_db().execute(
            """SELECT expires_at,used_at,cancelled_at FROM company_invitations
               WHERE token_hash=?""",
            (token_hash,),
        ).fetchone()
        valid = bool(
            row and not row["used_at"] and not row["cancelled_at"]
            and row["expires_at"].replace(tzinfo=None) > datetime.utcnow()
        )
        return render_template(
            "20-accept-invitation.html", token=token if valid else "", valid=valid
        )

    body = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip()
    password = body.get("password") or ""
    if len(password) < 8:
        return jsonify({
            "success": False, "error": "WEAK_PASSWORD",
            "message": "كلمة المرور يجب أن تكون 8 أحرف على الأقل.",
        }), 400
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    db = get_db()
    invitation = db.execute(
        """SELECT * FROM company_invitations WHERE token_hash=? FOR UPDATE""",
        (token_hash,),
    ).fetchone()
    if (
        not invitation or invitation["used_at"] or invitation["cancelled_at"]
        or invitation["expires_at"].replace(tzinfo=None) <= datetime.utcnow()
    ):
        return jsonify({
            "success": False, "error": "INVITATION_INVALID_OR_EXPIRED",
        }), 400
    db.execute(
        """UPDATE user_accounts
           SET password_hash=?,account_status='active',company_id=?,admin_role=?,
               is_admin=0
           WHERE account_id=?""",
        (
            generate_password_hash(password), invitation["company_id"],
            invitation["company_role"], invitation["account_id"],
        ),
    )
    db.execute(
        "UPDATE company_invitations SET used_at=now() WHERE invitation_id=?",
        (invitation["invitation_id"],),
    )
    _admin_audit(
        db, invitation["account_id"], "company_invitation_accepted",
        "company_invitation", invitation["invitation_id"],
        invitation["company_id"], reason="قبول رابط دعوة صالح لمرة واحدة",
        metadata={"company_role": invitation["company_role"]},
    )
    db.commit()
    return jsonify({
        "success": True,
        "data": {"redirect": "/login"},
    })


@app.route("/discovery")
def discovery():
    """جلسة الاكتشاف SDS-001 — تُعرض مرة واحدة فقط بعد تسجيل الحساب الجديد."""
    context = request_company_context()
    if not context:
        return redirect(url_for("login"))
    db = get_db()
    company = db.execute(
        "SELECT sds_done, main_goal, sector FROM companies WHERE company_id=?",
        (context["company_id"],),
    ).fetchone()
    if not company:
        abort(404)
    from sana_company_memory import retrieve_memory
    memory_context = retrieve_memory(
        db, context["company_id"], memory_keys=[
            "goal:primary", "problem:declared", "acquisition:source",
            "founder:dependency", "decision:style",
        ],
    )
    full_reassessment = request.args.get("full") == "1"
    if context["admin_preview"]:
        return render_template(
            "06-sana-discovery.html", full_reassessment=full_reassessment,
            company_memory=memory_context,
            **p0_template_context(),
        )
    account = current_account()
    start = _company_start_redirect(account)
    if start == url_for("onboarding"):
        return redirect(start)
    if start == url_for("ceo_home") and not full_reassessment:
        return redirect(start)
    return render_template(
        "06-sana-discovery.html", full_reassessment=full_reassessment,
        company_memory=memory_context,
        **p0_template_context(),
    )


FIT_GATE_REQUIRED_FIELDS = (
    "operating_duration",
    "paying_customers",
    "delivery_mode",
)


def _evaluate_fit_gate(payload):
    """بوابة صغيرة تفصل الشركات العاملة عن مرحلة الفكرة دون استخدام حجم الفريق وحده."""
    fit = payload.get("fit_gate") if isinstance(payload, dict) else None
    fit = fit if isinstance(fit, dict) else {}
    missing = [key for key in FIT_GATE_REQUIRED_FIELDS if not str(fit.get(key) or "").strip()]
    if missing:
        return {
            "complete": False,
            "qualified": False,
            "missing": missing,
            "reason": "أكمل أسئلة الملاءمة الثلاثة قبل بدء التشخيص.",
        }
    duration = str(fit["operating_duration"]).strip()
    paying_customers = str(fit["paying_customers"]).strip()
    delivery_mode = str(fit["delivery_mode"]).strip()
    qualified = (
        duration != "IDEA"
        and paying_customers == "YES"
        and delivery_mode in {"OWNER_DELIVERY", "TEAM_DELIVERY"}
    )
    return {
        "complete": True,
        "qualified": qualified,
        "missing": [],
        "reason": (
            "الشركة لديها عملاء وتشغيل فعلي، ويمكن أن تستفيد من Sana Scan."
            if qualified
            else "الحالة ما زالت في مرحلة بناء العرض أو الوصول لأول إيراد وتشغيل فعلي."
        ),
    }


@app.route("/api/fit-gate/check", methods=["POST"])
def fit_gate_check():
    context = request_company_context()
    if not context:
        return jsonify({"success": False, "error": "UNAUTHORIZED"}), 401
    result = _evaluate_fit_gate(request.get_json(silent=True) or {})
    if not result["complete"]:
        return jsonify({
            "success": False,
            "error": "FIT_GATE_INCOMPLETE",
            "message": result["reason"],
        }), 400
    return jsonify({
        "success": True,
        "data": {
            **result,
            "redirect": None if result["qualified"] else "/fit-gate/build-launch",
        },
    })


@app.route("/fit-gate/build-launch")
def fit_gate_build_launch():
    context = request_company_context()
    if not context:
        return redirect(url_for("login"))
    account = current_account()
    company = get_db().execute(
        "SELECT name FROM companies WHERE company_id=?",
        (context["company_id"],),
    ).fetchone()
    return render_template(
        "26-fit-gate-build-launch.html",
        company_name=company["name"] if company else "مشروعك",
        account_email=(account or {}).get("email"),
    )


@app.route("/api/fit-gate/interest", methods=["POST"])
def fit_gate_interest():
    context = request_company_context()
    account = current_account()
    if not context or not account:
        return jsonify({"success": False, "error": "UNAUTHORIZED"}), 401
    body = request.get_json(silent=True) or {}
    phone = str(body.get("phone") or "").strip() or None
    if phone and (len(phone) > 30 or not re.fullmatch(r"[0-9+()\-\s]{6,30}", phone)):
        return jsonify({
            "success": False,
            "error": "INVALID_PHONE",
            "message": "اكتب رقم جوال صحيحًا، أو اترك الحقل فارغًا.",
        }), 400
    db = get_db()
    company = db.execute(
        "SELECT name FROM companies WHERE company_id=?",
        (context["company_id"],),
    ).fetchone()
    saved = db.execute(
        """SELECT lead_id FROM leads
           WHERE company_id=? AND source='FIT_GATE'
             AND service_interest='Build & Launch'
           ORDER BY created_at DESC LIMIT 1""",
        (context["company_id"],),
    ).fetchone()
    if saved:
        if phone:
            db.execute(
                "UPDATE leads SET phone=? WHERE lead_id=? AND company_id=?",
                (phone, saved["lead_id"], context["company_id"]),
            )
            db.commit()
        lead_id = saved["lead_id"]
        duplicate = True
    else:
        lead_id = "L-" + uuid.uuid4().hex[:10].upper()
        company_name = company["name"] if company else "مشروع جديد"
        db.execute(
            """INSERT INTO leads
               (lead_id,company_id,name,company_name,email,phone,source,
                service_interest,status,notes)
               VALUES (?,?,?,?,?,?,?,'Build & Launch','اهتمام',?)""",
            (
                lead_id, context["company_id"], company_name, company_name,
                account["email"], phone, "FIT_GATE",
                "مسار مستقل لتحويل الفكرة أو الخبرة إلى خدمة قابلة للبيع والتشغيل.",
            ),
        )
        db.commit()
        duplicate = False
    return jsonify({
        "success": True,
        "data": {"lead_id": lead_id, "duplicate": duplicate},
        "message": (
            "تم تسجيل اهتمامك — شكرًا لك، ونتطلع أن يكون سنع جزءًا من نجاح مشروعك القادم."
        ),
    }), 200 if duplicate else 201


@app.route("/api/discovery/save", methods=["POST"])
def discovery_save():
    """يحفظ إجابات SDS-001 ويُنشئ أول قضية تلقائيًا."""
    context = request_company_context()
    if not context:
        return jsonify({"success": False, "error": "UNAUTHORIZED"}), 401

    company_id = context["company_id"]
    db = get_db()
    body = request.get_json(silent=True) or {}
    full_reassessment = body.get("full_reassessment") is True

    # ضمان عدم التكرار — فقط إذا اكتملت الجلسة وحُفظت البيانات فعليًا (main_goal غير فارغ)
    # إذا كان sds_done=1 لكن main_goal فارغ: نسمح بإعادة الحفظ لأن البيانات ضاعت
    company = db.execute(
        "SELECT sds_done, main_goal FROM companies WHERE company_id=?", (company_id,)
    ).fetchone()
    if company and company["sds_done"] and company["main_goal"] and not full_reassessment:
        case = db.execute(
            "SELECT case_id FROM cases WHERE company_id=? ORDER BY opened_at ASC LIMIT 1",
            (company_id,)
        ).fetchone()
        scan_state = (
            _run_initial_case_scan(db, company_id, case["case_id"])
            if case else {"has_run": False, "status": "NOT_RUN"}
        )
        return jsonify({"success": True, "data": {
            "case_id": case["case_id"] if case else None,
            "already_done": True,
            "scan_has_run": scan_state["has_run"],
            "scan_status": scan_state["status"],
        }})

    fit_gate = _evaluate_fit_gate(body)
    if not full_reassessment and not fit_gate["complete"]:
        return jsonify({
            "success": False,
            "error": "FIT_GATE_REQUIRED",
            "message": fit_gate["reason"],
        }), 400
    if not full_reassessment and not fit_gate["qualified"]:
        return jsonify({"success": True, "data": {
            "qualified": False,
            "fit_gate": fit_gate,
            "redirect": "/fit-gate/build-launch",
            "scan_has_run": False,
            "scan_status": "NOT_RUN",
        }})

    q1    = (body.get("q1")      or "").strip()
    q2    = (body.get("q2")      or "").strip()
    q3    = (body.get("q3")      or "").strip()
    q4    = (body.get("q4")      or "").strip()
    q4_fu = (body.get("q4_fu")   or "").strip()
    q5    = (body.get("q5")      or "").strip()
    q5_text = (body.get("q5_text") or "").strip()
    # اختيار "العمل يستمر طبيعيًا" لا يملك سؤال متابعة.
    # نحذف أي قيمة قديمة أو مرسلة يدويًا حتى لا تظهر في الأدلة والتقارير.
    if q5 == "😎 العمل يستمر طبيعيًا":
        q5_text = ""
    q6    = (body.get("q6")      or "").strip()
    q7    = body.get("q7") or []   # multi-select → success_criteria
    if not all((q1, q2, q3, q4, q4_fu, q5, q6)):
        return jsonify({
            "success": False,
            "error": "DISCOVERY_INCOMPLETE",
            "message": "أكمل أسئلة الاكتشاف قبل المتابعة.",
        }), 400
    if not isinstance(q7, list) or not q7 or len(q7) > 3:
        return jsonify({
            "success": False,
            "error": "DISCOVERY_PRIORITIES_REQUIRED",
            "message": "اختر أولوية واحدة على الأقل، وبحد أقصى ثلاث.",
        }), 400

    from sana_reliability import validate_context
    numeric_answers = body.get("numeric_answers") or []
    if isinstance(numeric_answers, dict):
        numeric_answers = [
            {"topic_key": key, **(value if isinstance(value, dict) else {"value": value})}
            for key, value in numeric_answers.items()
        ]
    if not isinstance(numeric_answers, list):
        return jsonify({
            "success": False,
            "error": "INVALID_NUMERIC_ANSWERS",
            "message": "القياسات الرقمية يجب أن تكون قائمة منظّمة.",
        }), 400
    try:
        calibrated_numbers = [
            validate_context(item, discovery=True, numeric=True)
            for item in numeric_answers
        ]
    except ValueError as exc:
        return jsonify({
            "success": False,
            "error": "INVALID_DIAGNOSTIC_NUMBER",
            "message": str(exc),
        }), 400

    # 1. تحديث الشركة: الوجهة + معايير النجاح
    success_criteria = "، ".join(q7) if q7 else None
    db.execute(
        "UPDATE companies SET main_goal=?, success_criteria=? WHERE company_id=?",
        (q1 or None, success_criteria, company_id)
    )

    # 2. إنشاء أول قضية من إجابة Q2 — يُحفظ في declared_problem + real_question
    # لا يُنشأ أي صف بجدول decisions — القرار يأتي لاحقًا بعد تشخيص فعلي
    case_id = "CASE" + uuid.uuid4().hex[:8].upper()
    case_title = f"أول قضية: {q2}" if q2 else "أول قضية من جلسة الاكتشاف"
    # real_question يُعيد صياغة التحدي كسؤال تشخيصي — يُعرض في صفحة القضية تحت "السؤال الحقيقي"
    real_question = (
        f'هل السبب الحقيقي وراء "{q2}" هو ما يبدو على السطح، أم يوجد سبب أعمق لم يُكتشف بعد؟'
        if q2 else None
    )
    db.execute(
        """INSERT INTO cases
           (case_id, company_id, case_title, case_type, case_status,
            declared_problem, real_question, opened_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (case_id, company_id, case_title, "تشخيص", "مفتوح",
         q2 or None, real_question, datetime.utcnow().isoformat())
    )
    from sana_company_memory import capture_discovery
    capture_discovery(
        db, company_id, case_id=case_id,
        answers={**body, "q7": q7}, owner_id=(current_account() or {}).get("account_id"),
    )

    # الشركات القديمة قد تسبق إنشاء الأصول الافتراضية. أكمل الأنواع الناقصة
    # قبل ربط إجابات Discovery وتشغيل أول Scan.
    _create_company_default_assets(db, company_id)

    # خريطة الأصول حسب النوع (5 أصول معتمدة فقط)
    assets_by_type = {
        a["asset_type"]: a["asset_id"]
        for a in db.execute(
            "SELECT asset_id, asset_type FROM assets WHERE company_id=?", (company_id,)
        ).fetchall()
    }

    def add_ev(title, asset_type=None, source_ref=None, context=None):
        from sana_evidence import save_evidence as _save_ev
        context = context or {}
        result = _save_ev(
            db,
            company_id  = company_id,
            case_id     = case_id,
            asset_id    = assets_by_type.get(asset_type) if asset_type else None,
            title       = title,
            source_type = "اكتشاف_ذاتي",
            confidence  = 50,
            evidence_type = "Evidence",
            source_ref = source_ref,
            information_type = context.get("information_type", "Narrative"),
            verification_status = "UNVERIFIED",
            source_category = "SELF_REPORTED",
            period_start = context.get("period_start"),
            period_end = context.get("period_end"),
            raw_value = context.get("raw_value"),
            normalized_value = context.get("normalized_value"),
            unit = context.get("unit"),
            topic_key = context.get("topic_key"),
            seasonality_context = context.get("seasonality_context"),
            commit = False,
        )
        if not result["success"]:
            raise RuntimeError(result["error"])

    # Q3 — الأصل الأهم: 5 خيارات فقط مطابقة للأصول المعتمدة
    q3_asset_map = {
        "📚 الخبرة والمعرفة":          "Knowledge",
        "⚙️ طريقة التشغيل والتنفيذ":  "Operations",
        "🏷️ الاسم والسمعة والبراند":  "Brand",
        "📊 البيانات والتقارير":       "Data",
        "👤 وجود المؤسس ومتابعته":    "Independence",
    }
    q3_asset = q3_asset_map.get(q3)
    if q3 and q3 != "❓ ما أعرف":
        add_ev(f"أهم أصل في الشركة اليوم: {q3}", q3_asset, "SDS-001 Q3")

    # Q2 — التحدي المعلن، يُربط بأقرب أصل دون تحويله إلى حكم نهائي.
    q2_asset_map = {
        "📉 المبيعات": "Brand",
        "📣 التسويق": "Brand",
        "⚙️ التشغيل": "Operations",
        # «الفريق» وصف واسع؛ لا يُنسب لأصل قبل أن توضّح إجابة غياب المؤسس
        # القدرة، الدور وملكية القرار.
        "👥 الفريق": None,
        "💵 الأرباح والسيولة": "Data",
        "🚀 التوسع": "Operations",
    }
    if q2:
        add_ev(f"التحدي المعلن: {q2}", q2_asset_map.get(q2), "SDS-001 Q2")

    # Q4 — مصدر الاكتساب → دليل على القضية
    # إذا كانت إجابة المتابعة "كبير" أو "متوسط" → ربط بإطار BOS-001
    if q4:
        add_ev(f"مصدر اكتساب العملاء: {q4}", "Brand", "SDS-001 Q4")
    if q4_fu:
        add_ev(f"هشاشة مصدر العملاء: {q4_fu}", "Brand", "SDS-001 Q4 follow-up")
        HIGH_RISK = ("😰 نعم، بشكل كبير", "🙂 نعم، بدرجة متوسطة")
        if q4_fu in HIGH_RISK:
            cf_id = "CF" + uuid.uuid4().hex[:8].upper()
            db.execute(
                """INSERT INTO case_frameworks
                   (cf_id, case_id, company_id, framework_id)
                   VALUES (?,?,?,?)""",
                (cf_id, case_id, company_id, "sana-acquisition-system")
            )

    # Q5 — اعتماد المؤسس → Independence
    if q5:
        add_ev(f"مستوى اعتماد الشركة على المؤسس: {q5}", "Independence", "SDS-001 Q5")
    if q5_text:
        add_ev(f"ما سيتعطل عند غياب المؤسس: {q5_text}", "Independence", "SDS-001 Q5 follow-up")

    # Q6 — أسلوب اتخاذ القرار → أصل البيانات.
    if q6:
        add_ev(f"[decision_style] أسلوب اتخاذ القرار: {q6}", "Data", "SDS-001 Q6")

    # الأرقام اختيارية في رحلة SDS-001 الحالية، لكن إذا ذكرها العميل فلا تُحفظ
    # قبل اكتمال المعنى والفترة والوحدة والمرجع. وتبقى إفادة ذاتية غير متحققة.
    for index, context_data in enumerate(calibrated_numbers):
        item = numeric_answers[index]
        label = str(item.get("label") or context_data.get("topic_key") or "قياس تشخيصي").strip()
        asset_type = str(item.get("asset_type") or "Data").strip()
        add_ev(
            f"{label}: {context_data['raw_value']} {context_data['unit']}",
            asset_type if asset_type in assets_by_type else "Data",
            context_data["source_ref"],
            context_data,
        )

    baseline_payload = body.get("diagnostic_baseline")
    if not isinstance(baseline_payload, dict):
        db.rollback()
        return jsonify({
            "success": False,
            "error": "DIAGNOSTIC_BASELINE_REQUIRED",
            "message": "حدد فترة الأساس وفترة المقارنة قبل حفظ جلسة الاكتشاف.",
        }), 400
    try:
        baseline_context = validate_context({
            "information_type": "Narrative",
            "period_start": baseline_payload.get("baseline_start"),
            "period_end": baseline_payload.get("baseline_end"),
        })
        baseline_start = baseline_context["period_start"]
        baseline_end = baseline_context["period_end"]
        if not baseline_start or not baseline_end:
            raise ValueError("فترة الأساس مطلوبة.")
        comparison_start = baseline_payload.get("comparison_start") or None
        comparison_end = baseline_payload.get("comparison_end") or None
        if bool(comparison_start) != bool(comparison_end):
            raise ValueError("حدد بداية الفترة السابقة ونهايتها، أو اتركهما فارغتين.")
        if comparison_start and comparison_end:
            comparison = validate_context({
                "information_type": "Narrative",
                "period_start": comparison_start,
                "period_end": comparison_end,
            })
            comparison_start, comparison_end = comparison["period_start"], comparison["period_end"]
    except ValueError as exc:
        db.rollback()
        return jsonify({
            "success": False,
            "error": "INVALID_DIAGNOSTIC_BASELINE",
            "message": str(exc),
        }), 400
    db.execute(
        """INSERT INTO diagnostic_baselines
           (baseline_id, company_id, case_id, baseline_start, baseline_end,
            comparison_start, comparison_end, seasonality_context)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT (company_id, case_id) DO UPDATE SET
             baseline_start=EXCLUDED.baseline_start,
             baseline_end=EXCLUDED.baseline_end,
             comparison_start=EXCLUDED.comparison_start,
             comparison_end=EXCLUDED.comparison_end,
             seasonality_context=EXCLUDED.seasonality_context""",
        (
            "DBL-" + uuid.uuid4().hex[:10].upper(), company_id, case_id,
            baseline_start, baseline_end, comparison_start, comparison_end,
            str(baseline_payload.get("seasonality_context") or "").strip()
            or "غير معروف — صرّح العميل بعدم توفر سياق موسمي",
        ),
    )

    # تحديث علامة اكتمال الجلسة
    db.execute("UPDATE companies SET sds_done=1 WHERE company_id=?", (company_id,))
    db.commit()
    scan_state = _run_initial_case_scan(db, company_id, case_id)

    # إرجاع بيانات الشركة لشاشة الجواز (Company Passport) بعد الجلسة
    company_row = db.execute(
        "SELECT sector, employee_count FROM companies WHERE company_id=?", (company_id,)
    ).fetchone()

    return jsonify({"success": True, "data": {
        "case_id":        case_id,
        "company_id":     company_id,
        "sector":         company_row["sector"] if company_row else None,
        "employee_count": company_row["employee_count"] if company_row else None,
        "main_goal":      q1 or None,
        "top_asset":      q3 or None,
        "declared_problem": q2 or None,
        "scan_has_run":   scan_state["has_run"],
        "scan_status":    scan_state["status"],
    }})


@app.route("/check-in")
def returning_checkin_page():
    context = request_company_context()
    if not context:
        return redirect(url_for("login"))
    company = get_db().execute(
        "SELECT sds_done,main_goal FROM companies WHERE company_id=?",
        (context["company_id"],),
    ).fetchone()
    if not company or not company["sds_done"] or not company["main_goal"]:
        return redirect(url_for("discovery"))
    return render_template("24-returning-checkin.html")


@app.route("/api/returning-checkin", methods=["POST"])
def returning_checkin_start():
    context = request_company_context()
    if not context:
        return jsonify({"success": False, "error": "UNAUTHORIZED"}), 401
    from sana_returning_checkin import start_checkin
    db = get_db()
    try:
        result = start_checkin(
            db, context["company_id"], (current_account() or {}).get("account_id")
        )
        db.commit()
        return jsonify({"success": True, "data": result}), 201
    except (ValueError, LookupError) as exc:
        db.rollback()
        return jsonify({
            "success": False, "error": str(exc),
            "message": "أكمل جلسة الاكتشاف الأولى قبل المراجعة السريعة.",
        }), 409


@app.route("/api/returning-checkin/<checkin_id>", methods=["POST"])
def returning_checkin_complete(checkin_id):
    context = request_company_context()
    if not context:
        return jsonify({"success": False, "error": "UNAUTHORIZED"}), 401
    from sana_returning_checkin import complete_checkin
    db = get_db()
    row = db.execute(
        """SELECT * FROM returning_checkins
           WHERE checkin_id=? AND company_id=? FOR UPDATE""",
        (checkin_id, context["company_id"]),
    ).fetchone()
    if not row:
        return jsonify({"success": False, "error": "CHECKIN_NOT_FOUND"}), 404
    if row["status"] == "COMPLETED":
        return jsonify({
            "success": True, "data": json.loads(row["summary_json"] or "{}")
        })
    answers = (request.get_json(silent=True) or {}).get("answers") or []
    if not isinstance(answers, list):
        return jsonify({"success": False, "error": "INVALID_ANSWERS"}), 400
    try:
        summary = complete_checkin(db, row, answers)
        db.commit()
        return jsonify({"success": True, "data": summary})
    except (ValueError, RuntimeError) as exc:
        db.rollback()
        return jsonify({
            "success": False, "error": str(exc),
            "message": "تأكد من اكتمال الإجابات وفترة القياس.",
        }), 400


@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect("/")


@app.route("/api/session")
def api_session():
    """يخبر واجهة العميل بشركته الفعلية (من الجلسة) — لا يُستخدم أبدًا رابط قابل للتعديل."""
    account = current_account()
    if account:
        db = get_db()
        company = db.execute("SELECT company_id, name FROM companies WHERE company_id=?", (account["company_id"],)).fetchone()
        return jsonify({
            "success": True,
            "data": {
                "authenticated": True,
                "company_id": account["company_id"],
                "company_name": company["name"] if company else None,
                "email": account["email"],
            }
        })
    return jsonify({
        "success": True,
        "data": {"authenticated": False, "admin_preview": is_admin_preview()}
    })


# ═══════════════════════════════════════════════════════════════════
# لوحة الإدارة الداخلية — فوق البيانات الحالية، بلا مسار شركة بديل
# ═══════════════════════════════════════════════════════════════════

ADMIN_COMPANY_STATUSES = {
    "Registered", "Internal Managed", "Active", "Trial", "Suspended", "Archived"
}


def _valid_email(value):
    value = (value or "").strip().lower()
    return value if (
        value and "@" in value and "." in value.split("@")[-1]
    ) else None


def _new_company_identity(db, name):
    prefix = re.sub(r"[^A-Z0-9]", "", (name or "").upper())[:4] or "SANA"
    for _ in range(20):
        random_part = secrets.token_hex(3).upper()
        company_id = f"CO-{prefix}-{random_part}"
        company_code = f"SANA-{random_part[:3]}-{random_part[3:]}"
        exists = db.execute(
            """SELECT 1 FROM companies
               WHERE company_id=? OR company_code=? OR signup_code=?""",
            (company_id, company_code, company_code),
        ).fetchone()
        if not exists:
            return company_id, company_code
    raise RuntimeError("COMPANY_CODE_GENERATION_FAILED")


_DEFAULT_COMPANY_ASSETS = (
    ("Knowledge", "أصل المعرفة"),
    ("Operations", "أصل التشغيل"),
    ("Brand", "أصل البراند"),
    ("Data", "أصل البيانات"),
    ("Independence", "أصل الاستقلال"),
)


def _create_company_default_assets(db, company_id):
    """أكمل أنواع الأصول الافتراضية الناقصة دون إنشاء صفوف مكررة."""
    db.execute(
        "SELECT pg_advisory_xact_lock(hashtext(?))",
        (f"default-company-assets:{company_id}",),
    )
    existing_types = {
        row["asset_type"]
        for row in db.execute(
            "SELECT asset_type FROM assets WHERE company_id=?",
            (company_id,),
        ).fetchall()
    }
    for asset_type, asset_name in _DEFAULT_COMPANY_ASSETS:
        if asset_type in existing_types:
            continue
        db.execute(
            """INSERT INTO assets
               (asset_id,company_id,asset_type,asset_name,current_score,
                fragility_score,status)
               VALUES (?,?,?,?,?,?,?)""",
            (
                "A" + uuid.uuid4().hex[:10].upper(),
                company_id, asset_type, asset_name, 0, 100, "غير مقيَّم",
            ),
        )


def _run_initial_case_scan(db, company_id, case_id):
    """شغّل أول Scan فقط؛ فشل الفحص لا يلغي بيانات Discovery المحفوظة."""
    try:
        _create_company_default_assets(db, company_id)
        db.commit()
        existing = db.execute(
            """SELECT status FROM scan_runs
               WHERE company_id=? AND case_id=?
               ORDER BY created_at DESC, scan_id DESC LIMIT 1""",
            (company_id, case_id),
        ).fetchone()
        if existing:
            return {"has_run": True, "status": existing["status"]}

        from sana_scan import run_scan
        result = run_scan(db, case_id)
        return {
            "has_run": bool(result.get("scan_id")),
            "status": result.get("status") or "INCOMPLETE",
        }
    except Exception:
        db.rollback()
        app.logger.exception(
            "Initial Sana Scan failed after Discovery for case %s", case_id,
        )
        return {"has_run": False, "status": "NOT_RUN"}


_BILLING_NOTIFICATION_TYPES = {
    "billing_subscription_past_due",
    "billing_subscription_canceled",
}
_BILLING_NOTIFICATION_MAX_ATTEMPTS = 5
_BILLING_NOTIFICATION_RETRY_BASE_SECONDS = 60
_BILLING_NOTIFICATION_RETRY_MAX_SECONDS = 3600
_BILLING_NOTIFICATION_LOCK_SECONDS = 600


def _billing_retry_delay_seconds(attempt_count):
    """Return a bounded exponential delay after an unsuccessful attempt."""
    exponent = max(0, int(attempt_count) - 1)
    return min(
        _BILLING_NOTIFICATION_RETRY_BASE_SECONDS * (2 ** exponent),
        _BILLING_NOTIFICATION_RETRY_MAX_SECONDS,
    )
def _admin_send_email(db, *, notification_type, recipient_email, subject,
                      html_body, actor_id, company_id=None, payload=None):
    notification_id = "NTF-" + secrets.token_hex(8).upper()
    initial_attempt_count = 1 if RESEND_API_KEY else 0
    db.execute(
        """INSERT INTO admin_notification_outbox
           (notification_id,notification_type,recipient_email,company_id,status,
            payload_json,created_by,attempt_count,last_attempt_at)
           VALUES (?,?,?,?,?,?,?,?,CASE WHEN ? > 0 THEN now() ELSE NULL END)""",
        (
            notification_id, notification_type, recipient_email, company_id,
            "queued", json.dumps(payload or {}, ensure_ascii=False), actor_id,
            initial_attempt_count,
            initial_attempt_count,
        ),
    )
    if not RESEND_API_KEY:
        return {"notification_id": notification_id, "status": "queued"}
    try:
        resend.Emails.send({
            "from": "سنع <noreply@sanaclarity.com>",
            "to": [recipient_email],
            "subject": subject,
            "html": html_body,
        })
        db.execute(
            """UPDATE admin_notification_outbox
               SET status='sent',sent_at=now(),error_code=NULL,
                   next_attempt_at=NULL,delivery_lock_token=NULL,
                   delivery_locked_at=NULL
               WHERE notification_id=?""",
            (notification_id,),
        )
        return {"notification_id": notification_id, "status": "sent"}
    except Exception as exc:
        attempt_count = initial_attempt_count
        db.execute(
            """UPDATE admin_notification_outbox
               SET status='failed',error_code=?,
                   next_attempt_at=CASE
                     WHEN ? < ? THEN now() + (? * INTERVAL '1 second')
                     ELSE NULL
                   END
               WHERE notification_id=?""",
            (
                type(exc).__name__[:80],
                attempt_count,
                _BILLING_NOTIFICATION_MAX_ATTEMPTS,
                _billing_retry_delay_seconds(attempt_count),
                notification_id,
            ),
        )
        return {"notification_id": notification_id, "status": "failed"}

def _claim_billing_notification_for_retry(db, notification_id=None):
    """Claim one due billing message, committing before the network call."""
    notification_filter = ""
    params = [
        "billing_subscription_past_due",
        "billing_subscription_canceled",
        _BILLING_NOTIFICATION_MAX_ATTEMPTS,
        _BILLING_NOTIFICATION_LOCK_SECONDS,
    ]
    if notification_id:
        notification_filter = " AND notification_id=?"
        params.append(notification_id)
    row = db.execute(
        f"""SELECT *
           FROM admin_notification_outbox
           WHERE notification_type IN (?,?)
             AND status IN ('queued','failed')
             AND attempt_count < ?
             AND COALESCE(next_attempt_at,now()) <= now()
             AND (
               delivery_locked_at IS NULL
               OR delivery_locked_at < now() - (? * INTERVAL '1 second')
             )
             {notification_filter}
           ORDER BY created_at,notification_id
           FOR UPDATE SKIP LOCKED
           LIMIT 1""",
        tuple(params),
    ).fetchone()
    if not row:
        db.commit()
        return None
    lock_token = uuid.uuid4().hex
    claimed = db.execute(
        """UPDATE admin_notification_outbox
           SET delivery_lock_token=?,delivery_locked_at=now(),
               attempt_count=attempt_count+1,last_attempt_at=now()
           WHERE notification_id=?
           RETURNING *""",
        (lock_token, row["notification_id"]),
    ).fetchone()
    db.commit()
    if not claimed:
        return None
    result = dict(claimed)
    result["delivery_lock_token"] = lock_token
    return result
def _send_billing_lifecycle_notification(db, notification, company_id):
    """Notify the company's owner after a Stripe state transition.

    The Stripe event ledger decides whether a transition is new. This helper
    only delivers the resulting notification and records its delivery outcome
    in the shared admin notification outbox. The recovery URL deliberately
    contains no Stripe identifiers or payment details.
    """
    status = notification.get("status")
    if status not in {"past_due", "canceled"}:
        return {"status": "ignored"}

    owners = db.execute(
        """SELECT account_id,email
           FROM user_accounts
           WHERE company_id=? AND account_status='active'
             AND admin_role='COMPANY_OWNER'
           ORDER BY created_at, account_id""",
        (company_id,),
    ).fetchall()
    owners = [owner for owner in owners if _valid_email(owner["email"])]
    if not owners:
        app.logger.error(
            "Billing notification owner unavailable for company %s", company_id
        )
        return {"status": "failed", "error": "BILLING_OWNER_EMAIL_UNAVAILABLE"}

    if status == "past_due":
        action = "update_payment"
        subject = "تعثر دفع اشتراك سنع — مطلوب تحديث وسيلة الدفع"
        heading = "تعثر دفع اشتراك سنع"
        explanation = (
            "تعذر تحصيل دفعة الاشتراك الأخيرة. حدّث وسيلة الدفع أو راجع "
            "الاشتراك حتى تعود الخدمة للعمل."
        )
        link_label = "تحديث وسيلة الدفع أو مراجعة الاشتراك"
    else:
        action = "restart_subscription"
        subject = "تم إلغاء اشتراك سنع — ابدأ اشتراكًا جديدًا"
        heading = "تم إلغاء اشتراك سنع"
        explanation = (
            "تم إلغاء الاشتراك، ولذلك توقفت الخدمة. يمكنك بدء اشتراك جديد "
            "من صفحة الأسعار."
        )
        link_label = "بدء اشتراك جديد"

    recovery_url = (
        f"{APP_BASE_URL}/pricing?"
        f"{urlencode({'billing_action': action})}"
    )
    deliveries = []
    for owner in owners:
        deliveries.append(_admin_send_email(
            db,
            notification_type=f"billing_subscription_{status}",
            recipient_email=owner["email"],
            subject=subject,
            html_body=(
                "<div dir='rtl'>"
                f"<h2>{html.escape(heading)}</h2>"
                f"<p>{html.escape(explanation)}</p>"
                f"<p><a href='{html.escape(recovery_url)}'>"
                f"{html.escape(link_label)}</a></p>"
                "</div>"
            ),
            actor_id=owner["account_id"],
            company_id=company_id,
            payload={
                "event_id": notification.get("event_id"),
                "status": status,
                "recovery_action": action,
                "recovery_url": recovery_url,
            },
        ))
    return {
        "status": "delivered",
        "recipient_count": len(deliveries),
        "deliveries": deliveries,
    }


def _issue_admin_password_reset(db, account, actor_id, reason):
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(minutes=30)
    db.execute(
        """INSERT INTO password_reset_tokens (token,email,account_id,expires_at)
           VALUES (?,?,?,?)""",
        (token, account["email"], account["account_id"], expires_at),
    )
    reset_url = f"{APP_BASE_URL}/reset-password?token={token}"
    delivery = _admin_send_email(
        db,
        notification_type="password_reset",
        recipient_email=account["email"],
        subject="تعيين أو إعادة تعيين كلمة المرور — سنع",
        html_body=(
            "<div dir='rtl'><h2>تعيين كلمة المرور</h2>"
            "<p>الرابط صالح لمدة 30 دقيقة ولاستخدام واحد فقط.</p>"
            f"<p><a href='{html.escape(reset_url)}'>تعيين كلمة المرور</a></p></div>"
        ),
        actor_id=actor_id,
        company_id=account.get("company_id"),
        payload={"account_id": account["account_id"], "expires_in_minutes": 30},
    )
    _admin_audit(
        db, actor_id, "password_reset_issued", "user_account",
        account["account_id"], account.get("company_id"), reason,
        {"delivery_status": delivery["status"]},
    )
    return {"expires_at": expires_at.isoformat(), "delivery": delivery["status"]}


def _issue_company_invitation(db, company, account, company_role, actor_id,
                              reason, invitation_id=None):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = datetime.utcnow() + timedelta(hours=24)
    invitation_id = invitation_id or ("INV-" + secrets.token_hex(8).upper())
    if db.execute(
        "SELECT 1 FROM company_invitations WHERE invitation_id=?",
        (invitation_id,),
    ).fetchone():
        db.execute(
            """UPDATE company_invitations
               SET token_hash=?,expires_at=?,used_at=NULL,cancelled_at=NULL,
                   last_sent_at=now(),company_role=?
               WHERE invitation_id=?""",
            (token_hash, expires_at, company_role, invitation_id),
        )
        action = "company_invitation_resent"
    else:
        db.execute(
            """INSERT INTO company_invitations
               (invitation_id,company_id,account_id,email,company_role,
                token_hash,expires_at,created_by)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                invitation_id, company["company_id"], account["account_id"],
                account["email"], company_role, token_hash, expires_at, actor_id,
            ),
        )
        action = "company_invitation_issued"
    invite_url = f"{APP_BASE_URL}/accept-invitation?token={token}"
    delivery = _admin_send_email(
        db,
        notification_type="company_invitation",
        recipient_email=account["email"],
        subject=f"دعوة للانضمام إلى {company['name']} في سنع",
        html_body=(
            "<div dir='rtl'><h2>دعوة آمنة إلى سنع</h2>"
            f"<p>تمت دعوتك كـ {html.escape(company_role)} في "
            f"{html.escape(company['name'])}.</p>"
            "<p>الرابط صالح 24 ساعة ولاستخدام واحد.</p>"
            f"<p><a href='{html.escape(invite_url)}'>قبول الدعوة</a></p></div>"
        ),
        actor_id=actor_id,
        company_id=company["company_id"],
        payload={
            "invitation_id": invitation_id,
            "account_id": account["account_id"],
            "expires_in_hours": 24,
        },
    )
    _admin_audit(
        db, actor_id, action, "company_invitation", invitation_id,
        company["company_id"], reason,
        {
            "account_id": account["account_id"],
            "company_role": company_role,
            "delivery_status": delivery["status"],
        },
    )
    return {
        "invitation_id": invitation_id,
        "expires_at": expires_at.isoformat(),
        "delivery_status": delivery["status"],
    }


def _admin_date(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except (TypeError, ValueError):
        return None


def _admin_scan_payload(row):
    if not row:
        return {}
    try:
        payload = json.loads(row["result"] or "{}")
        return payload if isinstance(payload, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _admin_company_snapshot(db, company):
    company_id = company["company_id"]
    users = db.execute(
        "SELECT COUNT(*) AS c FROM user_accounts WHERE company_id=? AND account_status='active'",
        (company_id,),
    ).fetchone()["c"]
    latest_scan = db.execute(
        """SELECT scan_id,status,created_at,result FROM scan_runs
           WHERE company_id=? ORDER BY created_at DESC LIMIT 1""",
        (company_id,),
    ).fetchone()
    scan_payload = _admin_scan_payload(latest_scan)
    bottleneck = scan_payload.get("bottleneck") or {}
    latest_case = db.execute(
        """SELECT case_id,case_title,case_status,opened_at
           FROM cases WHERE company_id=? ORDER BY opened_at DESC LIMIT 1""",
        (company_id,),
    ).fetchone()
    p0_decision = db.execute(
        """SELECT decision_id,title,status,created_at,case_id
           FROM decisions WHERE company_id=? AND phase_label='P0'
           ORDER BY created_at DESC LIMIT 1""",
        (company_id,),
    ).fetchone()
    p0_task = db.execute(
        """SELECT t.task_id,t.title,t.status,t.due_date,t.completed_at
           FROM tasks t JOIN decisions d ON d.decision_id=t.decision_id
           WHERE t.company_id=? AND d.phase_label='P0'
           ORDER BY t.created_at DESC LIMIT 1""",
        (company_id,),
    ).fetchone()
    last_review = db.execute(
        """SELECT review_id,impact_outcome,reviewed_at
           FROM p0_impact_reviews WHERE company_id=?
           ORDER BY reviewed_at DESC LIMIT 1""",
        (company_id,),
    ).fetchone()
    open_tasks = db.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE company_id=? AND status!='منجزة'",
        (company_id,),
    ).fetchone()["c"]
    overdue_tasks = 0
    for task in db.execute(
        "SELECT due_date,status FROM tasks WHERE company_id=? AND status!='منجزة'",
        (company_id,),
    ).fetchall():
        due = _admin_date(task["due_date"])
        if due and due < date.today():
            overdue_tasks += 1
    evidence_count = db.execute(
        "SELECT COUNT(*) AS c FROM evidence WHERE company_id=?", (company_id,)
    ).fetchone()["c"]
    verified_evidence_count = db.execute(
        """SELECT COUNT(*) AS c FROM evidence
           WHERE company_id=? AND verification_status='VERIFIED'""",
        (company_id,),
    ).fetchone()["c"]
    pending_review_count = db.execute(
        """SELECT COUNT(*) AS c FROM scan_findings
           WHERE company_id=? AND review_status='Pending Review'""",
        (company_id,),
    ).fetchone()["c"]
    contradiction_count = db.execute(
        """SELECT COUNT(*) AS c
           FROM evidence_relations r
           JOIN evidence e ON e.evidence_id=r.from_evidence_id
           WHERE e.company_id=? AND r.status='OPEN'""",
        (company_id,),
    ).fetchone()["c"]
    subscription = db.execute(
        "SELECT status FROM sana_company_subscriptions WHERE company_id=?",
        (company_id,),
    ).fetchone()
    missing_evidence = (
        not latest_scan
        or latest_scan["status"] == "INCOMPLETE"
        or bool(scan_payload.get("missing_evidence"))
    )

    if last_review:
        phase = "Impact Review"
    elif p0_task and p0_task["status"] == "منجزة":
        phase = "Result"
    elif p0_decision and p0_decision["status"] == "معتمد":
        phase = "Task"
    elif p0_decision:
        phase = "Decision"
    elif latest_scan and latest_scan["status"] in {"REVIEW_REQUIRED", "COMPLETE"}:
        phase = "Diagnostic Review"
    elif latest_scan:
        phase = "Evidence Gate"
    elif latest_case:
        phase = "Discovery"
    else:
        phase = "Not started"

    activity_values = [
        company.get("created_at"),
        latest_scan["created_at"] if latest_scan else None,
        p0_decision["created_at"] if p0_decision else None,
        last_review["reviewed_at"] if last_review else None,
    ]
    last_activity = max((str(v) for v in activity_values if v), default=None)
    return {
        "company_id": company_id,
        "company_code": company.get("company_code"),
        "name": company["name"],
        "contact_email": company.get("contact_email"),
        "status": company.get("lifecycle_status") or "Active",
        "sector": company.get("sector_other") or company.get("sector"),
        "users_active": users,
        "last_activity": last_activity,
        "p0_phase": phase,
        "latest_case": dict(latest_case) if latest_case else None,
        "latest_scan": {
            "scan_id": latest_scan["scan_id"],
            "status": latest_scan["status"],
            "created_at": latest_scan["created_at"],
        } if latest_scan else None,
        "top_bottleneck": bottleneck.get("statement"),
        "latest_decision": dict(p0_decision) if p0_decision else None,
        "latest_task": dict(p0_task) if p0_task else None,
        "open_tasks": open_tasks,
        "overdue_tasks": overdue_tasks,
        "latest_impact_review": dict(last_review) if last_review else None,
        "classification": _admin_company_classification(company),
        "progress": {
            "discovery": bool(company.get("sds_done")),
            "evidence_ready": not missing_evidence,
            "scan_complete": bool(
                latest_scan and latest_scan["status"] == "COMPLETE"
            ),
            "decision": bool(p0_decision),
            "task": bool(p0_task),
            "result": bool(p0_task and p0_task["status"] == "منجزة"),
            "impact": bool(last_review),
        },
        "evidence_readiness": {
            "status": "NOT_READY" if missing_evidence else "READY",
            "evidence_count": evidence_count,
            "verified_count": verified_evidence_count,
            "pending_review_count": pending_review_count,
            "contradiction_count": contradiction_count,
            "scan_status": latest_scan["status"] if latest_scan else "NOT_RUN",
        },
        "subscription_status": subscription["status"] if subscription else None,
    }


def _admin_companies(db, mode="production"):
    mode = mode if mode in {"production", "qa"} else "production"
    expected = "test" if mode == "qa" else "production"
    return [
        _admin_company_snapshot(db, row)
        for row in db.execute(
            "SELECT * FROM companies ORDER BY created_at DESC, company_id"
        ).fetchall()
        if _admin_company_classification(row) == expected
    ]


def _admin_ops_queue(db, mode="production"):
    from sana_billing import billing_cleanup_health

    items = []
    cleanup_health = billing_cleanup_health(db)
    if cleanup_health["is_stale"]:
        items.append({
            "priority": "CRITICAL",
            "type": "billing_cleanup",
            "company_id": None,
            "entity_id": "billing_cleanup_schedule",
            "title": "تنظيف الدفع لم يسجل تشغيلًا موثوقًا حديثًا",
            "reason": (
                "تحقق من خدمة الجدولة قبل أن تتراكم جلسات الدفع المعلقة."
            ),
        })
    for company in _admin_companies(db, mode):
        cid = company["company_id"]
        progress = company["progress"]
        if company["evidence_readiness"]["status"] == "NOT_READY":
            items.append({
                "priority": "HIGH",
                "type": "evidence",
                "company_id": cid,
                "entity_id": (
                    (company.get("latest_case") or {}).get("case_id") or cid
                ),
                "title": f"{company['name']} · Evidence ناقص",
                "reason": "الأدلة غير كافية أو لم يكتمل Sana Scan.",
            })
        if progress["decision"] and not progress["task"]:
            decision = company["latest_decision"]
            items.append({
                "priority": "HIGH",
                "type": "decision",
                "company_id": cid,
                "entity_id": decision["decision_id"],
                "title": f"{company['name']} · قرار بلا مهمة",
                "reason": decision["title"],
            })
        if progress["task"] and not progress["result"]:
            task = company["latest_task"]
            items.append({
                "priority": "HIGH",
                "type": "task",
                "company_id": cid,
                "entity_id": task["task_id"],
                "title": f"{company['name']} · مهمة بلا نتيجة",
                "reason": task["title"],
            })
        if progress["result"] and not progress["impact"]:
            task = company["latest_task"]
            items.append({
                "priority": "HIGH",
                "type": "impact",
                "company_id": cid,
                "entity_id": task["task_id"],
                "title": f"{company['name']} · نتيجة بلا Impact",
                "reason": task["title"],
            })
    order = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2}
    return sorted(items, key=lambda x: (order.get(x["priority"], 9), x["title"]))


@app.route("/admin")
def admin_dashboard_page():
    role, failure = _admin_guard(json_response=False)
    if failure:
        return failure
    return render_template(
        "admin-dashboard.html",
        admin_bootstrap={
            "role": role,
            "permissions": sorted(_admin_permissions()),
            "permission_options": ADMIN_PERMISSION_OPTIONS,
        },
    )


@app.route("/api/admin/billing", methods=["GET", "PATCH"])
def admin_billing():
    from sana_billing import (
        billing_cleanup_health,
        latest_billing_cleanup_run,
        latest_billing_cleanup_skip,
        public_offer,
    )
    _role, failure = _admin_guard(permission="manage_companies")
    if failure:
        return failure
    db = get_db()
    if request.method == "PATCH":
        body = request.get_json(silent=True) or {}
        reason = (body.get("reason") or "").strip()
        if not reason:
            return jsonify({"success": False, "error": "AUDIT_REASON_REQUIRED"}), 400
        try:
            base_price = int(body.get("base_price_minor"))
            sale_price = int(body.get("sale_price_minor"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "PRICE_INVALID"}), 400
        if base_price < 0 or sale_price < 0 or sale_price > base_price:
            return jsonify({"success": False, "error": "PRICE_INVALID"}), 400
        plan_name = (body.get("plan_name") or "سنع").strip()
        sale_label = (body.get("sale_label") or "عرض الإطلاق").strip()
        if not plan_name or not sale_label:
            return jsonify({"success": False, "error": "BILLING_COPY_INVALID"}), 400
        db.execute(
            """UPDATE sana_billing_settings
               SET plan_name=?,base_price_minor=?,sale_price_minor=?,
                   sale_label=?,updated_by=?,updated_at=now()
               WHERE plan_id='SANA-P0'""",
            (
                plan_name, base_price, sale_price, sale_label,
                current_account()["account_id"],
            ),
        )
        _admin_audit(
            db, current_account()["account_id"], "billing_offer_updated",
            "billing_offer", "SANA-P0", None, reason,
            {"base_price_minor": base_price, "sale_price_minor": sale_price},
        )
        db.commit()

    coupons = [
        dict(row) for row in db.execute(
            """SELECT * FROM sana_billing_coupons
               ORDER BY created_at DESC LIMIT 100"""
        ).fetchall()
    ]
    subscriptions = [
        dict(row) for row in db.execute(
            """SELECT c.company_id,c.name AS company_name,
                      (s.subscription_id IS NOT NULL) AS has_subscription,
                      s.status,s.amount_minor,s.coupon_code,
                      s.current_period_end,s.updated_at
               FROM companies c
               LEFT JOIN sana_company_subscriptions s ON s.company_id=c.company_id
               ORDER BY c.created_at DESC LIMIT 300"""
        ).fetchall()
    ]
    return jsonify({"success": True, "data": {
        "offer": public_offer(db),
        "coupons": coupons,
        "subscriptions": subscriptions,
        "cleanup_run": latest_billing_cleanup_run(db),
        "cleanup_skip": latest_billing_cleanup_skip(db),
        "cleanup_health": billing_cleanup_health(db),
    }})


@app.route("/api/admin/billing/checkout-sessions/cleanup", methods=["POST"])
def admin_billing_checkout_cleanup():
    from sana_billing import (
        STALE_CHECKOUT_MIN_AGE_HOURS,
        expire_stale_checkouts,
    )
    _role, failure = _admin_guard(permission="manage_companies")
    if failure:
        return failure
    body = request.get_json(silent=True) or {}
    reason = (body.get("reason") or "").strip()
    if not reason:
        return jsonify({"success": False, "error": "AUDIT_REASON_REQUIRED"}), 400
    try:
        min_age_hours = int(
            body.get("min_age_hours", STALE_CHECKOUT_MIN_AGE_HOURS)
        )
        limit = int(body.get("limit", 50))
        if min_age_hours < STALE_CHECKOUT_MIN_AGE_HOURS or limit < 1 or limit > 100:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "CHECKOUT_CLEANUP_ARGUMENTS_INVALID",
        }), 400

    db = get_db()
    result = expire_stale_checkouts(
        db, min_age_hours=min_age_hours, limit=limit
    )
    _admin_audit(
        db, current_account()["account_id"], "billing_checkout_cleanup",
        "billing_checkout_cleanup", None, None, reason,
        {"min_age_hours": min_age_hours, "limit": limit, **result},
    )
    db.commit()
    app.logger.info(
        "Stripe checkout cleanup completed: scanned=%d expired=%d "
        "already_completed=%d already_expired=%d failed=%d",
        result["scanned"], result["expired"], result["already_completed"],
        result["already_expired"], result["failed"],
    )
    return jsonify({"success": True, "data": result})


@app.route("/api/admin/billing/coupons", methods=["POST"])
def admin_billing_coupon_create():
    from sana_billing import normalize_coupon_code
    _role, failure = _admin_guard(permission="manage_companies")
    if failure:
        return failure
    body = request.get_json(silent=True) or {}
    reason = (body.get("reason") or "").strip()
    code = normalize_coupon_code(body.get("code"))
    discount_type = (body.get("discount_type") or "").strip()
    try:
        discount_value = int(body.get("discount_value"))
        max_redemptions = (
            int(body["max_redemptions"])
            if body.get("max_redemptions") not in (None, "") else None
        )
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "COUPON_INVALID"}), 400
    expires_at = (body.get("expires_at") or "").strip() or None
    if (
        not re.fullmatch(r"[A-Z0-9_-]{3,32}", code)
        or discount_type not in {"percent", "amount"}
        or discount_value <= 0
        or (discount_type == "percent" and discount_value > 100)
        or (max_redemptions is not None and max_redemptions <= 0)
        or not reason
    ):
        return jsonify({"success": False, "error": "COUPON_INVALID"}), 400
    if expires_at:
        try:
            date.fromisoformat(expires_at)
        except ValueError:
            return jsonify({"success": False, "error": "COUPON_EXPIRY_INVALID"}), 400
    db = get_db()
    coupon_id = "CPN-" + secrets.token_hex(8).upper()
    try:
        db.execute(
            """INSERT INTO sana_billing_coupons
               (coupon_id,code,discount_type,discount_value,max_redemptions,
                expires_at,created_by)
               VALUES (?,?,?,?,?,?,?)""",
            (
                coupon_id, code, discount_type, discount_value,
                max_redemptions, expires_at, current_account()["account_id"],
            ),
        )
    except psycopg2.errors.UniqueViolation:
        db.rollback()
        return jsonify({"success": False, "error": "COUPON_EXISTS"}), 409
    _admin_audit(
        db, current_account()["account_id"], "billing_coupon_created",
        "billing_coupon", coupon_id, None, reason,
        {
            "code": code,
            "discount_type": discount_type,
            "max_redemptions": max_redemptions,
            "expires_at": expires_at,
        },
    )
    db.commit()
    return jsonify({"success": True, "data": {"coupon_id": coupon_id, "code": code}}), 201


@app.route("/api/admin/billing/subscriptions/<company_id>/free", methods=["POST"])
def admin_billing_grant_free(company_id):
    from sana_billing import activate_free
    _role, failure = _admin_guard(permission="manage_companies")
    if failure:
        return failure
    body = request.get_json(silent=True) or {}
    reason = (body.get("reason") or "").strip()
    try:
        period_days = int(body.get("period_days") or 30)
    except (TypeError, ValueError):
        period_days = 0
    if not reason or period_days < 1 or period_days > 730:
        return jsonify({"success": False, "error": "FREE_GRANT_INVALID"}), 400
    db = get_db()
    company = db.execute(
        "SELECT company_id FROM companies WHERE company_id=?", (company_id,)
    ).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404
    subscription = activate_free(db, company_id, period_days=period_days)
    _admin_audit(
        db, current_account()["account_id"], "billing_free_granted",
        "company_subscription", subscription["subscription_id"], company_id,
        reason, {"period_days": period_days},
    )
    db.commit()
    return jsonify({"success": True, "data": subscription})


@app.route("/api/admin/billing/subscriptions/<company_id>", methods=["PATCH"])
def admin_billing_subscription_update(company_id):
    _role, failure = _admin_guard(permission="manage_companies")
    if failure:
        return failure
    body = request.get_json(silent=True) or {}
    action = (body.get("action") or "").strip()
    reason = (body.get("reason") or "").strip()
    if action not in {"extend", "pause", "resume", "cancel"} or not reason:
        return jsonify({"success": False, "error": "SUBSCRIPTION_ACTION_INVALID"}), 400
    db = get_db()
    subscription = db.execute(
        "SELECT * FROM sana_company_subscriptions WHERE company_id=?",
        (company_id,),
    ).fetchone()
    if not subscription:
        return jsonify({"success": False, "error": "SUBSCRIPTION_NOT_FOUND"}), 404
    metadata = {"action": action}
    if action == "extend":
        try:
            days = int(body.get("days") or 30)
        except (TypeError, ValueError):
            days = 0
        if days < 1 or days > 730:
            return jsonify({"success": False, "error": "EXTENSION_INVALID"}), 400
        db.execute(
            """UPDATE sana_company_subscriptions
               SET current_period_end=GREATEST(
                    COALESCE(current_period_end,now()),now()
                   ) + (? * INTERVAL '1 day'),
                   updated_at=now()
               WHERE company_id=?""",
            (days, company_id),
        )
        metadata["days"] = days
    else:
        status = {"pause": "paused", "resume": "active", "cancel": "canceled"}[action]
        db.execute(
            """UPDATE sana_company_subscriptions
               SET status=?,updated_at=now() WHERE company_id=?""",
            (status, company_id),
        )
    _admin_audit(
        db, current_account()["account_id"], f"billing_subscription_{action}",
        "company_subscription", subscription["subscription_id"], company_id,
        reason, metadata,
    )
    db.commit()
    updated = db.execute(
        "SELECT * FROM sana_company_subscriptions WHERE company_id=?",
        (company_id,),
    ).fetchone()
    return jsonify({"success": True, "data": dict(updated)})


@app.route("/api/admin/overview")
def admin_overview():
    role, failure = _admin_guard()
    if failure:
        return failure
    db = get_db()
    mode = request.args.get("mode", "production").strip().lower()
    if mode not in {"production", "qa"}:
        return jsonify({
            "success": False,
            "error": "ADMIN_MODE_INVALID",
        }), 400
    companies = _admin_companies(db, mode)
    account_rows = db.execute(
        """SELECT a.account_id,a.email,a.company_id,a.is_admin,a.admin_role,
                  a.account_status,c.name AS company_name
           FROM user_accounts a
           LEFT JOIN companies c ON c.company_id=a.company_id"""
    ).fetchall()
    classified_accounts = [
        (row, _admin_account_classification(row))
        for row in account_rows
    ]
    active_users = sum(
        1 for row, classification in classified_accounts
        if row["account_status"] == "active" and classification == "production"
    )
    test_users = sum(
        1 for _row, classification in classified_accounts
        if classification == "test"
    )
    production_super_admins = sum(
        1 for row, classification in classified_accounts
        if classification == "production"
        and row["account_status"] == "active"
        and str(row["admin_role"] or "").upper() == "SUPER_ADMIN"
    )
    test_super_admins = sum(
        1 for row, classification in classified_accounts
        if classification == "test"
        and str(row["admin_role"] or "").upper() == "SUPER_ADMIN"
    )
    unclassified_super_admins = sum(
        1 for row, classification in classified_accounts
        if classification == "unclassified"
        and str(row["admin_role"] or "").upper() == "SUPER_ADMIN"
    )
    phase_order = (
        "Not started", "Discovery", "Evidence Gate", "Diagnostic Review",
        "Decision", "Task", "Result", "Impact Review",
    )
    company_phases = {phase: 0 for phase in phase_order}
    funnel = {
        "discovery": 0,
        "evidence_ready": 0,
        "scan_complete": 0,
        "decision": 0,
        "task": 0,
        "result": 0,
        "impact": 0,
    }
    evidence_total = 0
    evidence_verified = 0
    contradictions = 0
    pending_review = 0
    subscriptions = {
        "active": 0, "free": 0, "pending": 0,
        "paused": 0, "past_due": 0, "canceled": 0,
        "none": 0,
    }
    for company in companies:
        company_phases[company["p0_phase"]] = (
            company_phases.get(company["p0_phase"], 0) + 1
        )
        for step in funnel:
            funnel[step] += int(bool(company["progress"][step]))
        readiness = company["evidence_readiness"]
        evidence_total += readiness["evidence_count"]
        evidence_verified += readiness["verified_count"]
        contradictions += readiness["contradiction_count"]
        pending_review += readiness["pending_review_count"]
        subscription_status = company.get("subscription_status") or "none"
        subscriptions[subscription_status] = (
            subscriptions.get(subscription_status, 0) + 1
        )
    queue = _admin_ops_queue(db, mode)
    attention = {
        "decision_without_task": sum(
            1 for item in queue if item["type"] == "decision"
        ),
        "task_without_result": sum(
            1 for item in queue if item["type"] == "task"
        ),
        "result_without_impact": sum(
            1 for item in queue if item["type"] == "impact"
        ),
        "evidence_missing": sum(
            1 for item in queue if item["type"] == "evidence"
        ),
    }
    try:
        db.execute("SELECT 1").fetchone()
        database_health = "ok"
    except Exception as exc:
        database_health = "unavailable"
        _RUNTIME_HEALTH["last_critical_error"] = type(exc).__name__
    from sana_billing import billing_cleanup_health
    cleanup_health = billing_cleanup_health(db)
    deployment_commit = (
        os.environ.get("RAILWAY_GIT_COMMIT_SHA")
        or os.environ.get("REPLIT_DEPLOYMENT_SHA")
        or os.environ.get("REPLIT_GIT_SHA")
        or "unknown"
    )
    return jsonify({
        "success": True,
        "data": {
            "role": role,
            "permissions": sorted(_admin_permissions()),
            "permission_options": ADMIN_PERMISSION_OPTIONS,
            "mode": mode,
            "company_phases": company_phases,
            "funnel": funnel,
            "evidence_quality": {
                "verified_percent": (
                    round((evidence_verified / evidence_total) * 100, 1)
                    if evidence_total else 0
                ),
                "not_ready": sum(
                    1 for company in companies
                    if company["evidence_readiness"]["status"] == "NOT_READY"
                ),
                "contradictions": contradictions,
                "pending_review": pending_review,
            },
            "attention": attention,
            "metrics": {
                "companies": len(companies),
                "active_users": active_users,
                "test_users": test_users,
                "production_super_admins": production_super_admins,
                "test_super_admins": test_super_admins,
                "unclassified_super_admins": unclassified_super_admins,
                "needs_attention": len(queue),
            },
            "queue_preview": queue[:8],
            "system": {
                "app": "ok",
                "database": database_health,
                "supabase": "configured_database",
                "deployment": "railway" if IS_RAILWAY else "replit",
                "deployment_commit": deployment_commit,
                "last_critical_error": (
                    _RUNTIME_HEALTH["last_critical_error"] or "none"
                ),
                "startup": _RUNTIME_HEALTH["startup"],
                "workers": dict(_RUNTIME_HEALTH["workers"]),
                "billing_cleanup": cleanup_health["status"],
                "subscriptions": subscriptions,
            },
        },
    })


@app.route("/api/admin/companies", methods=["GET", "POST"])
def admin_companies():
    if request.method == "POST":
        _role, failure = _admin_guard(permission="manage_companies")
        if failure:
            return failure
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()
        contact_email = _valid_email(body.get("contact_email"))
        status = (body.get("lifecycle_status") or "Registered").strip()
        if not name:
            return jsonify({"success": False, "error": "COMPANY_NAME_REQUIRED"}), 400
        if body.get("contact_email") and not contact_email:
            return jsonify({"success": False, "error": "CONTACT_EMAIL_INVALID"}), 400
        if status not in {"Registered", "Internal Managed"}:
            return jsonify({"success": False, "error": "COMPANY_STATUS_INVALID"}), 400
        db = get_db()
        company_id, company_code = _new_company_identity(db, name)
        db.execute(
            """INSERT INTO companies
               (company_id,name,sector,city,company_code,signup_code,
                contact_email,lifecycle_status)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                company_id, name, (body.get("sector") or "").strip() or None,
                (body.get("city") or "").strip() or None,
                company_code, company_code, contact_email, status,
            ),
        )
        _create_company_default_assets(db, company_id)
        actor_id = current_account()["account_id"]
        delivery = _admin_send_email(
            db,
            notification_type="company_registered",
            recipient_email=SANA_LEADERSHIP_EMAIL,
            subject=f"تسجيل شركة جديدة في سنع — {name}",
            html_body=(
                "<div dir='rtl'><h2>تم تسجيل شركة جديدة</h2>"
                f"<p>{html.escape(name)}</p>"
                f"<p>رمز الشركة: {html.escape(company_code)}</p></div>"
            ),
            actor_id=actor_id,
            company_id=company_id,
            payload={"company_id": company_id, "company_code": company_code},
        )
        _admin_audit(
            db, actor_id, "company_created", "company", company_id, company_id,
            reason=(body.get("reason") or "إنشاء شركة يدويًا من Command Center"),
            metadata={
                "company_code": company_code,
                "lifecycle_status": status,
                "notification_status": delivery["status"],
            },
        )
        db.commit()
        return jsonify({"success": True, "data": {
            "company_id": company_id,
            "company_code": company_code,
            "name": name,
            "contact_email": contact_email,
            "lifecycle_status": status,
            "notification_status": delivery["status"],
        }}), 201

    _role, failure = _admin_guard(any_permissions=COMPANY_CONTEXT_PERMISSIONS)
    if failure:
        return failure
    mode = request.args.get("mode", "production").strip().lower()
    if mode not in {"production", "qa"}:
        return jsonify({
            "success": False,
            "error": "ADMIN_MODE_INVALID",
        }), 400
    return jsonify({
        "success": True,
        "data": _admin_companies(get_db(), mode),
    })


@app.route("/api/admin/companies/<company_id>")
def admin_company_detail(company_id):
    role, failure = _admin_guard(any_permissions=COMPANY_CONTEXT_PERMISSIONS)
    if failure:
        return failure
    db = get_db()
    company = db.execute("SELECT * FROM companies WHERE company_id=?", (company_id,)).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404
    actor = current_account()
    _admin_audit(
        db, actor["account_id"], "company_cross_company_view",
        "company", company_id, company_id,
        reason="فتح ملف الشركة من لوحة الإدارة",
        metadata={"role": role},
    )
    db.commit()
    return jsonify({"success": True, "data": _admin_company_snapshot(db, company)})


@app.route("/api/admin/companies/<company_id>/open", methods=["POST"])
def admin_open_company(company_id):
    role, failure = _admin_guard(any_permissions=COMPANY_CONTEXT_PERMISSIONS)
    if failure:
        return failure
    db = get_db()
    company = db.execute(
        "SELECT company_id,name,company_code FROM companies WHERE company_id=?",
        (company_id,),
    ).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404
    session["admin_company_id"] = company_id
    _admin_audit(
        db, current_account()["account_id"], "company_admin_open",
        "company", company_id, company_id,
        reason="فتح الشركة صراحة من Sana Command Center",
        metadata={"role": role, "company_code": company["company_code"]},
    )
    db.commit()
    return jsonify({"success": True, "data": {
        "company_id": company_id,
        "company_code": company["company_code"],
        "workspace_url": "/home",
    }})


@app.route("/api/admin/users")
def admin_users():
    _role, failure = _admin_guard(
        any_permissions={"manage_users", "link_accounts"}
    )
    if failure:
        return failure
    query = (request.args.get("q") or "").strip().lower()
    db = get_db()
    rows = db.execute(
        """SELECT a.account_id,a.email,a.company_id,a.admin_role,a.is_admin,
                  a.admin_permissions,
                  a.account_status,a.created_at,a.last_login_at,c.name AS company_name
           FROM user_accounts a LEFT JOIN companies c ON c.company_id=a.company_id
           WHERE LOWER(a.email) LIKE ? OR LOWER(a.account_id) LIKE ?
              OR LOWER(COALESCE(c.name,'')) LIKE ?
           ORDER BY a.created_at DESC LIMIT 200""",
        (f"%{query}%", f"%{query}%", f"%{query}%"),
    ).fetchall()
    return jsonify({"success": True, "data": [
        {
            **{k: row[k] for k in (
                "account_id", "email", "company_id", "admin_role",
                "account_status", "created_at", "last_login_at", "company_name",
                "admin_permissions"
            )},
            "admin_role": (
                "SUPER_ADMIN" if row["is_admin"] and row["admin_role"] == "USER"
                else row["admin_role"]
            ),
            "account_classification": _admin_account_classification(row),
        }
        for row in rows
    ]})


@app.route("/api/admin/admins", methods=["POST"])
def admin_create_admin():
    _role, failure = _admin_guard(minimum="SUPER_ADMIN")
    if failure:
        return failure
    body = request.get_json(silent=True) or {}
    email = _valid_email(body.get("email"))
    requested = {str(item) for item in (body.get("permissions") or [])}
    if not email:
        return jsonify({"success": False, "error": "EMAIL_INVALID"}), 400
    if not requested or not requested.issubset(ADMIN_PERMISSION_OPTIONS):
        return jsonify({
            "success": False, "error": "ADMIN_PERMISSIONS_INVALID",
            "allowed": ADMIN_PERMISSION_OPTIONS,
        }), 400
    db = get_db()
    existing = db.execute(
        "SELECT * FROM user_accounts WHERE LOWER(email)=?", (email,)
    ).fetchone()
    if existing and existing["admin_role"] == "SUPER_ADMIN":
        return jsonify({"success": False, "error": "SUPER_ADMIN_IMMUTABLE"}), 409
    if existing and existing["company_id"]:
        return jsonify({
            "success": False, "error": "ACCOUNT_ALREADY_COMPANY_MEMBER",
        }), 409
    if existing:
        account_id = existing["account_id"]
        db.execute(
            """UPDATE user_accounts
               SET admin_role='ADMIN',is_admin=1,account_status='invited',
                   admin_permissions=?,company_id=NULL
               WHERE account_id=?""",
            (json.dumps(sorted(requested)), account_id),
        )
    else:
        account_id = "ACC-ADMIN-" + uuid.uuid4().hex[:10].upper()
        db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id,is_admin,admin_role,
                admin_permissions,account_status)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                account_id, email,
                generate_password_hash(secrets.token_urlsafe(48)), None, 1,
                "ADMIN", json.dumps(sorted(requested)), "invited",
            ),
        )
    account = db.execute(
        "SELECT * FROM user_accounts WHERE account_id=?", (account_id,)
    ).fetchone()
    actor_id = current_account()["account_id"]
    reset = _issue_admin_password_reset(
        db, account, actor_id, "دعوة Admin جديد لتعيين كلمة مروره"
    )
    _admin_audit(
        db, actor_id, "admin_created", "user_account", account_id, None,
        reason=(body.get("reason") or "إنشاء Admin بصلاحيات محددة"),
        metadata={"permissions": sorted(requested)},
    )
    db.commit()
    return jsonify({"success": True, "data": {
        "account_id": account_id,
        "email": email,
        "admin_role": "ADMIN",
        "permissions": sorted(requested),
        "account_status": "invited",
        "reset_delivery_status": reset["delivery"],
    }}), 201


@app.route("/api/admin/companies/<company_id>/accounts/link", methods=["POST"])
def admin_link_company_account(company_id):
    _role, failure = _admin_guard(permission="link_accounts")
    if failure:
        return failure
    body = request.get_json(silent=True) or {}
    email = _valid_email(body.get("email"))
    company_role = str(body.get("company_role") or "COMPANY_MEMBER").upper()
    reason = (body.get("reason") or "").strip()
    if not email or company_role not in COMPANY_ROLES:
        return jsonify({"success": False, "error": "ACCOUNT_LINK_INVALID"}), 400
    if not reason:
        return jsonify({"success": False, "error": "AUDIT_REASON_REQUIRED"}), 400
    db = get_db()
    company = db.execute(
        "SELECT company_id FROM companies WHERE company_id=?", (company_id,)
    ).fetchone()
    account = db.execute(
        "SELECT * FROM user_accounts WHERE LOWER(email)=?", (email,)
    ).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404
    if not account:
        return jsonify({"success": False, "error": "ACCOUNT_NOT_FOUND"}), 404
    if account["admin_role"] in SYSTEM_ADMIN_ROLES:
        return jsonify({"success": False, "error": "SYSTEM_ADMIN_NOT_COMPANY_MEMBER"}), 409
    if account["company_id"] and account["company_id"] != company_id:
        return jsonify({"success": False, "error": "ACCOUNT_ALREADY_LINKED"}), 409
    db.execute(
        """UPDATE user_accounts
           SET company_id=?,admin_role=?,is_admin=0 WHERE account_id=?""",
        (company_id, company_role, account["account_id"]),
    )
    _admin_audit(
        db, current_account()["account_id"], "company_account_linked",
        "user_account", account["account_id"], company_id, reason,
        {"email": email, "company_role": company_role},
    )
    db.commit()
    return jsonify({"success": True, "data": {
        "account_id": account["account_id"],
        "company_id": company_id,
        "company_role": company_role,
    }})


@app.route("/api/admin/companies/<company_id>/invitations", methods=["GET", "POST"])
def admin_issue_company_invitation(company_id):
    _role, failure = _admin_guard(permission="link_accounts")
    if failure:
        return failure
    db = get_db()
    if request.method == "GET":
        rows = db.execute(
            """SELECT invitation_id,email,company_role,expires_at,used_at,
                      cancelled_at,created_at,last_sent_at
               FROM company_invitations WHERE company_id=?
               ORDER BY created_at DESC LIMIT 100""",
            (company_id,),
        ).fetchall()
        return jsonify({"success": True, "data": [dict(row) for row in rows]})
    body = request.get_json(silent=True) or {}
    email = _valid_email(body.get("email"))
    company_role = str(body.get("company_role") or "COMPANY_MEMBER").upper()
    reason = (body.get("reason") or "").strip() or "إصدار دعوة شركة"
    if not email or company_role not in COMPANY_ROLES:
        return jsonify({"success": False, "error": "INVITATION_INVALID"}), 400
    company = db.execute(
        "SELECT company_id,name FROM companies WHERE company_id=?", (company_id,)
    ).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404
    account = db.execute(
        "SELECT * FROM user_accounts WHERE LOWER(email)=?", (email,)
    ).fetchone()
    if account and account["admin_role"] in SYSTEM_ADMIN_ROLES:
        return jsonify({"success": False, "error": "SYSTEM_ADMIN_NOT_COMPANY_MEMBER"}), 409
    if account and account["company_id"] and account["company_id"] != company_id:
        return jsonify({"success": False, "error": "ACCOUNT_ALREADY_LINKED"}), 409
    if not account:
        account_id = "ACC-" + uuid.uuid4().hex[:12].upper()
        db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id,is_admin,admin_role,
                account_status)
               VALUES (?,?,?,?,?,?,?)""",
            (
                account_id, email,
                generate_password_hash(secrets.token_urlsafe(48)),
                company_id, 0, company_role, "invited",
            ),
        )
        account = db.execute(
            "SELECT * FROM user_accounts WHERE account_id=?", (account_id,)
        ).fetchone()
    else:
        db.execute(
            """UPDATE user_accounts SET company_id=?,admin_role=?,is_admin=0
               WHERE account_id=?""",
            (company_id, company_role, account["account_id"]),
        )
        account = db.execute(
            "SELECT * FROM user_accounts WHERE account_id=?",
            (account["account_id"],),
        ).fetchone()
    _assign_pilot_slot(db, account["account_id"])
    invitation = _issue_company_invitation(
        db, company, account, company_role,
        current_account()["account_id"], reason,
    )
    db.commit()
    return jsonify({"success": True, "data": {
        **invitation,
        "account_id": account["account_id"],
        "email": email,
        "company_role": company_role,
    }}), 201


@app.route(
    "/api/admin/companies/<company_id>/invitations/<invitation_id>/resend",
    methods=["POST"],
)
def admin_resend_company_invitation(company_id, invitation_id):
    _role, failure = _admin_guard(permission="link_accounts")
    if failure:
        return failure
    db = get_db()
    invitation = db.execute(
        """SELECT i.*,c.name FROM company_invitations i
           JOIN companies c ON c.company_id=i.company_id
           WHERE i.invitation_id=? AND i.company_id=?""",
        (invitation_id, company_id),
    ).fetchone()
    if not invitation or invitation["used_at"] or invitation["cancelled_at"]:
        return jsonify({"success": False, "error": "INVITATION_NOT_ACTIVE"}), 409
    account = db.execute(
        "SELECT * FROM user_accounts WHERE account_id=?",
        (invitation["account_id"],),
    ).fetchone()
    result = _issue_company_invitation(
        db,
        {"company_id": company_id, "name": invitation["name"]},
        account, invitation["company_role"], current_account()["account_id"],
        "إعادة إرسال دعوة الشركة", invitation_id=invitation_id,
    )
    db.commit()
    return jsonify({"success": True, "data": result})


@app.route(
    "/api/admin/companies/<company_id>/invitations/<invitation_id>/cancel",
    methods=["POST"],
)
def admin_cancel_company_invitation(company_id, invitation_id):
    _role, failure = _admin_guard(permission="link_accounts")
    if failure:
        return failure
    db = get_db()
    invitation = db.execute(
        """SELECT * FROM company_invitations
           WHERE invitation_id=? AND company_id=?""",
        (invitation_id, company_id),
    ).fetchone()
    if not invitation or invitation["used_at"] or invitation["cancelled_at"]:
        return jsonify({"success": False, "error": "INVITATION_NOT_ACTIVE"}), 409
    db.execute(
        "UPDATE company_invitations SET cancelled_at=now() WHERE invitation_id=?",
        (invitation_id,),
    )
    _admin_audit(
        db, current_account()["account_id"], "company_invitation_cancelled",
        "company_invitation", invitation_id, company_id,
        reason="إلغاء دعوة الشركة",
        metadata={"account_id": invitation["account_id"]},
    )
    db.commit()
    return jsonify({"success": True, "data": {"invitation_id": invitation_id}})


@app.route("/api/admin/users/<account_id>/reset-password", methods=["POST"])
def admin_reset_user_password(account_id):
    _role, failure = _admin_guard(
        any_permissions={"manage_users", "link_accounts"}
    )
    if failure:
        return failure
    db = get_db()
    account = db.execute(
        "SELECT * FROM user_accounts WHERE account_id=?", (account_id,)
    ).fetchone()
    if not account:
        return jsonify({"success": False, "error": "USER_NOT_FOUND"}), 404
    if account["admin_role"] == "SUPER_ADMIN" and _admin_role() != "SUPER_ADMIN":
        return jsonify({"success": False, "error": "SUPER_ADMIN_REQUIRED"}), 403
    result = _issue_admin_password_reset(
        db, account, current_account()["account_id"],
        "إصدار رابط إعادة تعيين من Sana Command Center",
    )
    db.commit()
    return jsonify({"success": True, "data": result})


@app.route("/api/admin/companies/<company_id>/reports/export")
def admin_export_company_report(company_id):
    _role, failure = _admin_guard(permission="export_reports")
    if failure:
        return failure
    db = get_db()
    company = db.execute(
        "SELECT company_id,company_code FROM companies WHERE company_id=?",
        (company_id,),
    ).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404
    session["admin_company_id"] = company_id
    _admin_audit(
        db, current_account()["account_id"], "company_report_exported",
        "company_report", company_id, company_id,
        reason="تصدير تقرير الشركة من Sana Command Center",
        metadata={
            "company_code": company["company_code"],
            "format": request.args.get("format", "text"),
        },
    )
    db.commit()
    response = (
        passport_report_pdf(company_id)
        if request.args.get("format") == "pdf"
        else passport_report_text(company_id)
    )
    if isinstance(response, tuple):
        return response
    response.headers["X-Sana-Company-Id"] = company_id
    response.headers["X-Sana-Company-Code"] = company["company_code"] or ""
    return response


@app.route("/api/admin/ops-queue")
def admin_ops_queue():
    _role, failure = _admin_guard(permission="review_cases")
    if failure:
        return failure
    mode = request.args.get("mode", "production").strip().lower()
    if mode not in {"production", "qa"}:
        return jsonify({
            "success": False,
            "error": "ADMIN_MODE_INVALID",
        }), 400
    return jsonify({
        "success": True,
        "data": _admin_ops_queue(get_db(), mode),
    })


@app.route("/api/admin/human-reviews")
def admin_human_reviews():
    _role, failure = _admin_guard(permission="review_cases")
    if failure:
        return failure
    db = get_db()
    from sana_human_review import ensure_schema as ensure_human_review_schema
    ensure_human_review_schema(db)
    rows = db.execute(
        """SELECT r.review_id,r.company_id,r.case_id,r.decision_id,r.reason_label,
                  r.status,r.scheduled_at,r.requested_at,r.decision_changed,
                  c.name AS company_name,ca.case_title,d.title AS decision_title,
                  d.confidence_score,d.success_metric,
                  r.before_snapshot_json,r.final_decision,r.approved_kpi,
                   r.next_action,r.client_note,r.reviewer_note,
                   r.expert_summary_status,r.notes_updated_at,r.formatted_at,
                   r.approved_at,u.email AS reviewer_email
           FROM case_human_reviews r
           JOIN companies c ON c.company_id=r.company_id
           JOIN cases ca ON ca.case_id=r.case_id
           LEFT JOIN decisions d ON d.decision_id=r.decision_id
            LEFT JOIN user_accounts u ON u.account_id=r.reviewer_account_id
           ORDER BY CASE r.status WHEN 'SCHEDULED' THEN 0 WHEN 'REQUESTED' THEN 1 ELSE 2 END,
                    r.scheduled_at,r.requested_at DESC LIMIT 200"""
    ).fetchall()
    totals = db.execute(
        """SELECT COUNT(*) AS requested,
                  COUNT(*) FILTER (WHERE status='COMPLETED') AS completed,
                  COUNT(*) FILTER (WHERE decision_changed=true) AS changed
           FROM case_human_reviews"""
    ).fetchone()
    reviews = []
    for row in rows:
        item = dict(row)
        query = (
            f"?view=expert-summary&case_id={item['case_id']}"
            f"&review_id={item['review_id']}"
        )
        item["summary_url"] = (
            f"/company/{item['company_id']}/scan-report{query}"
        )
        item["summary_pdf_url"] = (
            f"/api/companies/{item['company_id']}/passport/report-pdf{query}"
        )
        reviews.append(item)
    return jsonify({"success": True, "data": {
        "reviews": reviews,
        "metrics": dict(totals),
    }})


@app.route("/api/admin/human-review-slots", methods=["POST"])
def admin_create_human_review_slot():
    _role, failure = _admin_guard(permission="review_cases")
    if failure:
        return failure
    body = request.get_json(silent=True) or {}
    starts_at = (body.get("starts_at") or "").strip()
    duration = int(body.get("duration_minutes") or 30)
    if not starts_at or duration < 10 or duration > 180:
        return jsonify({"success": False, "message": "حدد موعدًا ومدة بين 10 و180 دقيقة."}), 400
    db = get_db()
    slot_id = "HRS-" + uuid.uuid4().hex[:12].upper()
    db.execute(
        """INSERT INTO human_review_slots
           (slot_id,starts_at,duration_minutes,reviewer_account_id)
           VALUES (?,?,?,?)""",
        (slot_id, starts_at, duration, current_account()["account_id"]),
    )
    _admin_audit(
        db, current_account()["account_id"], "human_review_slot_created",
        "human_review_slot", slot_id, reason="إتاحة موعد لمراجعة قرار",
        metadata={"starts_at": starts_at, "duration_minutes": duration},
    )
    db.commit()
    return jsonify({"success": True, "data": {"slot_id": slot_id}}), 201


@app.route("/api/admin/human-review-settings", methods=["PATCH"])
def admin_update_human_review_settings():
    _role, failure = _admin_guard(permission="review_cases")
    if failure:
        return failure
    from sana_human_review import OFFER_MODES
    body = request.get_json(silent=True) or {}
    mode = str(body.get("offer_mode") or "").upper()
    if mode not in OFFER_MODES:
        return jsonify({"success": False, "message": "اختر INCLUDED أو FREE أو PAID."}), 400
    duration = int(body.get("duration_minutes") or 30)
    price_minor = body.get("price_minor")
    paid_enabled = bool(body.get("paid_enabled", False))
    if mode == "PAID" and paid_enabled:
        return jsonify({
            "success": False,
            "message": "المراجعة المدفوعة غير مفعّلة قبل وجود مسار دفع جاهز.",
        }), 409
    db = get_db()
    db.execute(
        """UPDATE human_review_settings SET offer_mode=?,offer_name=?,price_minor=?,
                  duration_minutes=?,free_first_case=?,client_copy=?,paid_enabled=false,
                  updated_at=now() WHERE singleton_key='default'""",
        (
            mode, (body.get("offer_name") or "مراجعة القرار مع خبير سنع").strip(),
            int(price_minor) if price_minor not in (None, "") else None,
            duration, bool(body.get("free_first_case", True)),
            (body.get("client_copy") or "جلسة قصيرة لمراجعة النتيجة قبل التنفيذ").strip(),
        ),
    )
    _admin_audit(
        db, current_account()["account_id"], "human_review_settings_updated",
        "human_review_settings", "default", reason="تحديث عرض مراجعة القرار",
        metadata={"offer_mode": mode, "duration_minutes": duration},
    )
    db.commit()
    return jsonify({"success": True, "data": {"offer_mode": mode}})


@app.route("/api/admin/human-reviews/<review_id>/complete", methods=["POST"])
def admin_complete_human_review(review_id):
    _role, failure = _admin_guard(permission="review_cases")
    if failure:
        return failure
    from sana_human_review import add_event
    body = request.get_json(silent=True) or {}
    required = ("decision_changed", "final_decision", "approved_kpi", "next_action")
    if any(key not in body or body.get(key) in (None, "") for key in required):
        return jsonify({"success": False, "message": "أكمل القرار النهائي ومقياسه والخطوة التالية."}), 400
    changed = bool(body["decision_changed"])
    change_reason = (body.get("change_reason") or "").strip()
    if changed and not change_reason:
        return jsonify({"success": False, "message": "اذكر سبب تعديل القرار."}), 400
    db = get_db()
    review = db.execute(
        "SELECT * FROM case_human_reviews WHERE review_id=? FOR UPDATE", (review_id,)
    ).fetchone()
    if not review:
        return jsonify({"success": False, "error": "REVIEW_NOT_FOUND"}), 404
    if review["status"] not in {"REQUESTED", "SCHEDULED"}:
        return jsonify({"success": False, "message": "هذه المراجعة مغلقة بالفعل."}), 409
    after = {
        "decision_changed": changed,
        "final_decision": body["final_decision"].strip(),
        "change_reason": change_reason or None,
        "approved_kpi": body["approved_kpi"].strip(),
        "next_action": body["next_action"].strip(),
        "reviewer_note": (body.get("reviewer_note") or "").strip() or None,
    }
    db.execute(
        """UPDATE case_human_reviews SET status='COMPLETED',decision_changed=?,
                  final_decision=?,change_reason=?,approved_kpi=?,next_action=?,
                  reviewer_note=?,after_snapshot_json=?,reviewer_account_id=?,
                  completed_at=now(),started_at=COALESCE(started_at,now())
           WHERE review_id=?""",
        (
            changed, after["final_decision"], after["change_reason"],
            after["approved_kpi"], after["next_action"], after["reviewer_note"],
            json.dumps(after, ensure_ascii=False), current_account()["account_id"], review_id,
        ),
    )
    add_event(
        db, review_id, review["company_id"], review["status"], "COMPLETED",
        current_account()["account_id"], {"decision_changed": changed},
    )
    _admin_audit(
        db, current_account()["account_id"], "human_review_completed",
        "case_human_review", review_id, review["company_id"],
        reason="تسجيل نتيجة مراجعة قرار",
        metadata={"case_id": review["case_id"], "decision_changed": changed},
    )
    db.commit()
    return jsonify({"success": True, "data": after})


@app.route("/api/admin/human-reviews/<review_id>/notes", methods=["PATCH"])
def admin_save_expert_review_notes(review_id):
    _role, failure = _admin_guard(permission="review_cases")
    if failure:
        return failure
    body = request.get_json(silent=True) or {}
    notes = str(body.get("notes") or "").strip()
    if not notes:
        return jsonify({
            "success": False,
            "message": "اكتب ملاحظات الخبير أولًا.",
        }), 400
    if len(notes) > 6000:
        return jsonify({
            "success": False,
            "message": "اختصر الملاحظات إلى 6000 حرف أو أقل.",
        }), 400
    db = get_db()
    review = db.execute(
        "SELECT * FROM case_human_reviews WHERE review_id=? FOR UPDATE",
        (review_id,),
    ).fetchone()
    if not review:
        return jsonify({"success": False, "error": "REVIEW_NOT_FOUND"}), 404
    if review["expert_summary_status"] == "APPROVED":
        return jsonify({
            "success": False,
            "message": "الملخص معتمد. أنشئ طلب مراجعة جديدًا إذا لزم تعديل جديد.",
        }), 409
    action = (
        "expert_review_notes_updated"
        if str(review["reviewer_note"] or "").strip()
        else "expert_review_notes_saved"
    )
    actor_id = current_account()["account_id"]
    db.execute(
        """UPDATE case_human_reviews
           SET reviewer_note=?,reviewer_account_id=?,notes_updated_at=now(),
               expert_summary_json=NULL,expert_summary_status='DRAFT',
               formatted_at=NULL,approved_at=NULL,approved_by=NULL
           WHERE review_id=?""",
        (notes, actor_id, review_id),
    )
    _admin_audit(
        db, actor_id, action, "case_human_review", review_id,
        review["company_id"], reason="حفظ ملاحظات الخبير",
        metadata={"case_id": review["case_id"], "character_count": len(notes)},
    )
    db.commit()
    return jsonify({"success": True, "data": {
        "review_id": review_id,
        "expert_summary_status": "DRAFT",
    }})


@app.route(
    "/api/admin/human-reviews/<review_id>/format-summary",
    methods=["POST"],
)
def admin_format_expert_review_summary(review_id):
    _role, failure = _admin_guard(permission="review_cases")
    if failure:
        return failure
    from sana_human_review import format_expert_summary
    db = get_db()
    review = db.execute(
        "SELECT * FROM case_human_reviews WHERE review_id=? FOR UPDATE",
        (review_id,),
    ).fetchone()
    if not review:
        return jsonify({"success": False, "error": "REVIEW_NOT_FOUND"}), 404
    if review["expert_summary_status"] == "APPROVED":
        return jsonify({
            "success": False,
            "message": "الملخص معتمد بالفعل.",
        }), 409
    try:
        summary = format_expert_summary(db, review_id)
    except ValueError as exc:
        message = (
            "اكتب ملاحظات الخبير واحفظها قبل التنسيق."
            if str(exc) == "EXPERT_NOTES_REQUIRED"
            else "تعذر العثور على طلب المراجعة."
        )
        return jsonify({"success": False, "message": message}), 400
    actor_id = current_account()["account_id"]
    db.execute(
        """UPDATE case_human_reviews
           SET expert_summary_json=?,expert_summary_status='FORMATTED',
               formatted_at=now(),approved_at=NULL,approved_by=NULL
           WHERE review_id=?""",
        (json.dumps(summary, ensure_ascii=False, default=str), review_id),
    )
    _admin_audit(
        db, actor_id, "expert_review_summary_formatted",
        "case_human_review", review_id, review["company_id"],
        reason="تنسيق ملخص الخبير من بيانات الحالة والملاحظات فقط",
        metadata={"case_id": review["case_id"]},
    )
    db.commit()
    return jsonify({"success": True, "data": {
        "review_id": review_id,
        "expert_summary_status": "FORMATTED",
        "summary": summary,
    }})


@app.route(
    "/api/admin/human-reviews/<review_id>/approve-summary",
    methods=["POST"],
)
def admin_approve_expert_review_summary(review_id):
    _role, failure = _admin_guard(permission="review_cases")
    if failure:
        return failure
    from sana_human_review import add_event
    db = get_db()
    review = db.execute(
        "SELECT * FROM case_human_reviews WHERE review_id=? FOR UPDATE",
        (review_id,),
    ).fetchone()
    if not review:
        return jsonify({"success": False, "error": "REVIEW_NOT_FOUND"}), 404
    if (
        review["expert_summary_status"] != "FORMATTED"
        or not review["expert_summary_json"]
    ):
        return jsonify({
            "success": False,
            "message": "نسّق ملخص الخبير قبل اعتماده.",
        }), 409
    actor_id = current_account()["account_id"]
    previous_status = review["status"]
    db.execute(
        """UPDATE case_human_reviews
           SET expert_summary_status='APPROVED',approved_at=now(),
               approved_by=?,status='COMPLETED',completed_at=COALESCE(completed_at,now()),
               started_at=COALESCE(started_at,notes_updated_at,now())
           WHERE review_id=?""",
        (actor_id, review_id),
    )
    if previous_status != "COMPLETED":
        add_event(
            db, review_id, review["company_id"], previous_status, "COMPLETED",
            actor_id, {"expert_summary_approved": True},
        )
    _admin_audit(
        db, actor_id, "expert_review_summary_approved",
        "case_human_review", review_id, review["company_id"],
        reason="اعتماد ملخص مراجعة الخبير للعميل",
        metadata={"case_id": review["case_id"]},
    )
    db.commit()
    return jsonify({"success": True, "data": {
        "review_id": review_id,
        "expert_summary_status": "APPROVED",
    }})


@app.route("/api/admin/system-health")
def admin_system_health():
    _role, failure = _admin_guard()
    if failure:
        return failure
    try:
        get_db().execute("SELECT 1").fetchone()
        db_status = "ok"
    except Exception:
        db_status = "unavailable"
    return jsonify({
        "success": True,
        "data": {
            "app": "ok",
            "database": db_status,
            "supabase": "configured_database",
            "railway": "available" if IS_RAILWAY else "not_detected",
            "last_deployment": "not_available_in_app",
            "last_critical_error": "not_persisted",
            "schedulers": {
                "execution_reminders": os.environ.get("SANA_REMINDER_SCHEDULER_ENABLED", "0"),
                "knowledge_backup": os.environ.get("SANA_KNOWLEDGE_BACKUP_SCHEDULER_ENABLED", "0"),
                "research": os.environ.get("SANA_RESEARCH_SCHEDULER_ENABLED", "0"),
            },
        },
    })


@app.route("/api/admin/audit")
def admin_audit():
    _role, failure = _admin_guard(minimum="SUPER_ADMIN")
    if failure:
        return failure
    rows = get_db().execute(
        """SELECT l.audit_id,l.actor_account_id,l.action,l.target_type,l.target_id,
                  l.company_id,l.reason,l.metadata_json,l.created_at,
                  a.email AS actor_email
           FROM admin_audit_log l JOIN user_accounts a
             ON a.account_id=l.actor_account_id
           ORDER BY l.created_at DESC LIMIT 200"""
    ).fetchall()
    return jsonify({"success": True, "data": [dict(row) for row in rows]})


@app.route("/api/admin/search")
def admin_search():
    _role, failure = _admin_guard(any_permissions=COMPANY_CONTEXT_PERMISSIONS)
    if failure:
        return failure
    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify({"success": True, "data": []})
    like = f"%{query.lower()}%"
    db = get_db()
    results = []
    searches = (
        (
            "company",
            """SELECT company_id AS id,name AS title,company_id AS company_id
               FROM companies WHERE LOWER(name) LIKE ? OR LOWER(company_id) LIKE ?
               LIMIT 8""",
            (like, like),
        ),
        (
            "user",
            """SELECT account_id AS id,email AS title,company_id
               FROM user_accounts WHERE LOWER(email) LIKE ? OR LOWER(account_id) LIKE ?
               LIMIT 8""",
            (like, like),
        ),
        (
            "case",
            """SELECT case_id AS id,case_title AS title,company_id
               FROM cases WHERE LOWER(case_title) LIKE ? OR LOWER(case_id) LIKE ?
               LIMIT 8""",
            (like, like),
        ),
        (
            "decision",
            """SELECT decision_id AS id,title,company_id FROM decisions
               WHERE LOWER(title) LIKE ? OR LOWER(decision_id) LIKE ? LIMIT 8""",
            (like, like),
        ),
        (
            "task",
            """SELECT task_id AS id,title,company_id FROM tasks
               WHERE LOWER(title) LIKE ? OR LOWER(task_id) LIKE ?
                  OR LOWER(COALESCE(kpi,'')) LIKE ? LIMIT 8""",
            (like, like, like),
        ),
        (
            "finding",
            """SELECT finding_id AS id,title,company_id FROM scan_findings
               WHERE LOWER(title) LIKE ? OR LOWER(statement) LIKE ?
                  OR LOWER(finding_id) LIKE ? LIMIT 8""",
            (like, like, like),
        ),
    )
    for kind, sql, params in searches:
        for row in db.execute(sql, params).fetchall():
            results.append({
                "type": kind,
                "id": row["id"],
                "title": row["title"],
                "company_id": row["company_id"],
            })
    return jsonify({"success": True, "data": results[:30]})


@app.route("/api/admin/users/<account_id>", methods=["PATCH"])
def admin_update_user(account_id):
    role, failure = _admin_guard()
    if failure:
        return failure
    db = get_db()
    target = db.execute(
        "SELECT * FROM user_accounts WHERE account_id=?", (account_id,)
    ).fetchone()
    if not target:
        return jsonify({"success": False, "error": "USER_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    reason = (body.get("reason") or "").strip()
    if not reason:
        return jsonify({"success": False, "error": "AUDIT_REASON_REQUIRED"}), 400
    new_status = body.get("account_status")
    new_role = body.get("admin_role")
    requested_permissions = body.get("admin_permissions")
    if new_status is not None and new_status not in ADMIN_STATUSES:
        return jsonify({"success": False, "error": "ACCOUNT_STATUS_INVALID"}), 400
    if new_role is not None:
        new_role = str(new_role).upper()
        if role != "SUPER_ADMIN":
            return jsonify({"success": False, "error": "SUPER_ADMIN_REQUIRED"}), 403
        if new_role not in ADMIN_ROLES:
            return jsonify({"success": False, "error": "ADMIN_ROLE_INVALID"}), 400
    if requested_permissions is not None:
        requested_permissions = {str(item) for item in requested_permissions}
        if role != "SUPER_ADMIN":
            return jsonify({"success": False, "error": "SUPER_ADMIN_REQUIRED"}), 403
        if not requested_permissions.issubset(ADMIN_PERMISSION_OPTIONS):
            return jsonify({"success": False, "error": "ADMIN_PERMISSIONS_INVALID"}), 400
    target_role = (
        "SUPER_ADMIN"
        if target["is_admin"] and target["admin_role"] == "USER"
        else target["admin_role"]
    )
    if role == "ADMIN" and target_role == "SUPER_ADMIN":
        return jsonify({"success": False, "error": "SUPER_ADMIN_REQUIRED"}), 403
    if role == "ADMIN" and target_role in SYSTEM_ADMIN_ROLES:
        return jsonify({"success": False, "error": "SUPER_ADMIN_REQUIRED"}), 403
    if role != "SUPER_ADMIN" and not _admin_permissions().intersection(
        {"manage_users", "link_accounts"}
    ):
        return jsonify({
            "success": False,
            "error": "ADMIN_PERMISSION_REQUIRED",
            "required": ["link_accounts", "manage_users"],
        }), 403
    if account_id == current_account()["account_id"] and new_status == "disabled":
        return jsonify({"success": False, "error": "CANNOT_DISABLE_SELF"}), 400
    new_status = new_status or target["account_status"]
    new_role = new_role or target_role
    if new_role in SYSTEM_ADMIN_ROLES:
        company_id = None
    else:
        company_id = target["company_id"]
        if not company_id:
            return jsonify({
                "success": False, "error": "COMPANY_MEMBERSHIP_REQUIRED",
            }), 400
    permissions_json = (
        json.dumps(sorted(requested_permissions))
        if requested_permissions is not None
        else target.get("admin_permissions") or "[]"
    )
    if new_role not in SYSTEM_ADMIN_ROLES:
        permissions_json = "[]"
    db.execute(
        """UPDATE user_accounts
           SET account_status=?,admin_role=?,is_admin=?,admin_permissions=?,
               company_id=?
           WHERE account_id=?""",
        (
            new_status, new_role, 1 if new_role in SYSTEM_ADMIN_ROLES else 0,
            permissions_json, company_id, account_id,
        ),
    )
    _admin_audit(
        db, current_account()["account_id"], "user_update", "user", account_id,
        target["company_id"], reason,
         {"from_role": target["admin_role"], "to_role": new_role,
          "from_status": target["account_status"], "to_status": new_status,
          "permissions": (
              sorted(requested_permissions)
              if requested_permissions is not None else None
          )},
    )
    db.commit()
    return jsonify({"success": True, "data": {"account_id": account_id,
                                               "admin_role": new_role,
                                               "account_status": new_status}})


@app.route("/api/admin/companies/<company_id>", methods=["PATCH"])
def admin_update_company(company_id):
    _role, failure = _admin_guard(
        any_permissions={"manage_companies", "edit_company"}
    )
    if failure:
        return failure
    db = get_db()
    company = db.execute(
        "SELECT * FROM companies WHERE company_id=?",
        (company_id,),
    ).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    status = body.get("lifecycle_status", company["lifecycle_status"])
    reason = (body.get("reason") or "").strip()
    if status not in ADMIN_COMPANY_STATUSES:
        return jsonify({"success": False, "error": "COMPANY_STATUS_INVALID"}), 400
    if not reason:
        return jsonify({"success": False, "error": "AUDIT_REASON_REQUIRED"}), 400
    contact_email = (
        _valid_email(body.get("contact_email"))
        if "contact_email" in body else company.get("contact_email")
    )
    if body.get("contact_email") and not contact_email:
        return jsonify({"success": False, "error": "CONTACT_EMAIL_INVALID"}), 400
    editable = {
        "name": (body.get("name") or company["name"]).strip(),
        "sector": (body.get("sector") or "").strip() or company.get("sector"),
        "city": (body.get("city") or "").strip() or company.get("city"),
        "business_description": (
            (body.get("business_description") or "").strip()
            if "business_description" in body else company.get("business_description")
        ),
        "goal_90_days": (
            (body.get("goal_90_days") or "").strip()
            if "goal_90_days" in body else company.get("goal_90_days")
        ),
        "primary_challenge": (
            (body.get("primary_challenge") or "").strip()
            if "primary_challenge" in body else company.get("primary_challenge")
        ),
    }
    if not editable["name"]:
        return jsonify({"success": False, "error": "COMPANY_NAME_REQUIRED"}), 400
    db.execute(
        """UPDATE companies SET name=?,sector=?,city=?,business_description=?,
           goal_90_days=?,primary_challenge=?,contact_email=?,lifecycle_status=?
           WHERE company_id=?""",
        (
            editable["name"], editable["sector"], editable["city"],
            editable["business_description"], editable["goal_90_days"],
            editable["primary_challenge"], contact_email, status, company_id,
        ),
    )
    _admin_audit(
        db, current_account()["account_id"], "company_updated",
        "company", company_id, company_id, reason,
        {
            "from_status": company["lifecycle_status"], "to_status": status,
            "updated_fields": sorted(
                key for key in (
                    "name", "sector", "city", "business_description",
                    "goal_90_days", "primary_challenge", "contact_email",
                    "lifecycle_status",
                ) if key in body
            ),
        },
    )
    db.commit()
    return jsonify({"success": True, "data": {
        "company_id": company_id, "company_code": company.get("company_code"),
        "lifecycle_status": status, "contact_email": contact_email,
    }})


@app.route("/guide")
def guide_page():
    return render_template("13-guide.html")


def _knowledge_admin_account_allowed():
    """يتحقق من صلاحية الحساب الإداري دون الاعتماد على وضع المعاينة السرّي."""
    account = current_account()
    if not account:
        return False
    row = get_db().execute(
        "SELECT is_admin FROM user_accounts WHERE account_id=?",
        (account["account_id"],),
    ).fetchone()
    return bool(row and row["is_admin"])


@app.route("/methodology/<slug>")
def methodology_page(slug):
    # صفحة داخلية — وضع المستشار أو حساب إداري، لا تُكشف لحساب العميل
    if not is_admin_preview() and not _knowledge_admin_account_allowed():
        return redirect(url_for("ceo_home"))
    return render_template("08-methodology.html", slug=slug, default_company_id=default_company_id())


# ------------------------------------------------------------------
# Articles (doc_type='article' in methodology_docs — no new table)
# ------------------------------------------------------------------

@app.route("/articles")
def articles_list():
    db = get_db()
    articles = db.execute(
        "SELECT slug, title, subtitle FROM methodology_docs WHERE doc_type='article' ORDER BY created_at DESC"
    ).fetchall()
    return render_template("15-articles-list.html", articles=[dict(a) for a in articles])


@app.route("/articles/<slug>")
def article_page(slug):
    db = get_db()
    doc = db.execute(
        "SELECT * FROM methodology_docs WHERE slug=? AND doc_type='article'", (slug,)
    ).fetchone()
    if not doc:
        return "المقال غير موجود", 404
    return render_template("16-article.html", doc=dict(doc))


@app.route("/api/articles")
def api_articles_list():
    db = get_db()
    articles = db.execute(
        "SELECT slug, title, subtitle FROM methodology_docs WHERE doc_type='article' ORDER BY created_at DESC"
    ).fetchall()
    return jsonify({"success": True, "data": [dict(a) for a in articles]})


# ------------------------------------------------------------------
# API — Companies
# ------------------------------------------------------------------

@app.route("/api/sectors")
def sectors_list():
    """قائمة القطاعات الثابتة — تُستخدم في الـ frontend لبناء شاشة الاختيار."""
    return jsonify({"success": True, "data": SECTORS})


@app.route("/api/company/set-sector", methods=["POST"])
def set_sector():
    """تعيين قطاع الشركة — يُستدعى من شاشة /sector-select للشركات الموجودة."""
    account = current_account()
    if not account:
        return jsonify({"success": False, "error": "UNAUTHORIZED"}), 401

    body = request.get_json(silent=True) or {}
    sector_key = (body.get("sector") or "").strip()
    sector_other = (body.get("sector_other") or "").strip() or None  # نص حر عند اختيار "أخرى"

    if not sector_key:
        return jsonify({"success": False, "error": "MISSING_SECTOR",
                        "message": "يرجى اختيار قطاع الشركة."}), 400
    if sector_key == "other" and not sector_other:
        return jsonify({"success": False, "error": "MISSING_SECTOR_OTHER",
                        "message": "يرجى كتابة وصف قطاعك عند اختيار 'أخرى'."}), 400

    db = get_db()
    db.execute("UPDATE companies SET sector=?, sector_other=? WHERE company_id=?",
               (sector_key, sector_other if sector_key == "other" else None, account["company_id"]))
    db.commit()

    company = db.execute("SELECT sds_done FROM companies WHERE company_id=?",
                         (account["company_id"],)).fetchone()
    redirect_to = "/home" if (company and company["sds_done"]) else "/discovery"
    return jsonify({"success": True, "data": {"redirect": redirect_to}})


@app.route("/api/companies/<company_id>/methodologies")
def company_methodologies(company_id):
    """وثائق منهجية مُصفَّاة حسب قطاع الشركة — NULL sector_tags = تنطبق على الكل."""
    db = get_db()
    company = db.execute("SELECT sector FROM companies WHERE company_id=?", (company_id,)).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404

    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard

    sector_key = (company["sector"] or "").split(":")[0]  # يدعم القيم القديمة "other:نص" والجديدة "other"
    docs = db.execute(
        "SELECT doc_id, slug, title, subtitle, doc_type, version, bos_id, sector_tags FROM methodology_docs ORDER BY created_at"
    ).fetchall()

    result = []
    for doc in docs:
        tags = doc["sector_tags"]
        if tags:
            try:
                tag_list = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tag_list = [t.strip() for t in tags.split(",")]
            # أظهر الوثيقة فقط لو القطاع مطابق أو المفتاح "other"
            if sector_key and sector_key not in tag_list:
                continue
        result.append({
            "doc_id": doc["doc_id"],
            "slug": doc["slug"],
            "title": doc["title"],
            "subtitle": doc["subtitle"],
            "doc_type": doc["doc_type"],
            "version": doc["version"],
            "bos_id": doc["bos_id"],
            "sector_tags": tags,
        })
    return jsonify({"success": True, "data": result})


@app.route("/api/knowledge/libraries")
def knowledge_libraries():
    """كتالوج المكتبات المتاحة للشركة الحالية — المشتركة + قطاعها فقط."""
    from sana_knowledge import library_summary, sector_for_company
    db = get_db()
    account = current_account()
    company_id = account["company_id"] if account else request.args.get("company_id")
    company = db.execute("SELECT * FROM companies WHERE company_id=?", (company_id,)).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    sector = sector_for_company(dict(company))
    return jsonify({
        "success": True,
        "data": {
            "sector": sector,
            "sector_label": {"professional_services": "الخدمات المهنية B2B",
                             "experts": "الخبراء وأعمال المعرفة",
                             "ecommerce_retail": "التجارة الإلكترونية والتجزئة"}.get(
                                 sector, "قطاع غير مغطى في V1"),
            "libraries": library_summary(db, sector),
            "knowledge_version": "v1.0",
        }
    })


@app.route("/api/knowledge/search")
def knowledge_search_api():
    """بحث داخلي في معرفة سنع، لا يستدعي الإنترنت ولا AI."""
    from sana_knowledge import search_knowledge, sector_for_company
    db = get_db()
    account = current_account()
    company_id = account["company_id"] if account else request.args.get("company_id")
    company = db.execute("SELECT * FROM companies WHERE company_id=?", (company_id,)).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    case_context = None
    case_id = str(request.args.get("case_id") or "").strip()
    if case_id:
        case_context = db.execute(
            """SELECT case_type,declared_problem,real_question
               FROM cases WHERE case_id=? AND company_id=?""",
            (case_id, company_id),
        ).fetchone()
        if not case_context:
            return jsonify({"success": False, "error": "CASE_NOT_FOUND"}), 404
    context = {
        "sector": company["sector"],
        "business_model": request.args.get("business_model") or (
            case_context["case_type"] if case_context else None
        ),
        "stage": request.args.get("stage") or company["stage"],
        "problem": request.args.get("problem") or (
            (case_context["declared_problem"] or case_context["real_question"])
            if case_context else None
        ),
        "goal": request.args.get("goal") or company["main_goal"],
        "bottleneck": request.args.get("bottleneck"),
    }
    results = search_knowledge(
        db,
        query=request.args.get("q", ""),
        sector=sector_for_company(dict(company)),
        library_type=request.args.get("library_type") or None,
        limit=request.args.get("limit", 20),
        company_id=company_id,
        context=context,
    )
    if not results and str(request.args.get("q") or "").strip():
        from sana_research_cycle import record_general_gap
        record_general_gap(
            db, sector=sector_for_company(dict(company)),
            library_type=request.args.get("library_type") or "ALL",
        )
    return jsonify({
        "success": True,
        "data": results,
    })


def _knowledge_admin_guard():
    """المصادر المشتركة لا تُعدّل إلا من وضع المستشار أو حساب إداري."""
    if is_admin_preview():
        return None
    account = current_account()
    if not account:
        return jsonify({"success": False, "error": "UNAUTHORIZED"}), 401
    row = get_db().execute(
        "SELECT is_admin FROM user_accounts WHERE account_id=?",
        (account["account_id"],)
    ).fetchone()
    if not row or not row["is_admin"]:
        return jsonify({"success": False, "error": "ADMIN_REQUIRED"}), 403
    return None


def _private_knowledge_guard():
    """المادة الخاصة تحتاج جلسة حساب إداري قابلة للتتبع، لا admin preview مجهولًا."""
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    if not current_account() and not is_admin_preview():
        return jsonify({
            "success": False,
            "error": "PRIVATE_OWNER_REQUIRED",
            "message": "يلزم حساب إداري مرتبط بمالك المادة.",
        }), 401
    return None

def _private_knowledge_owner_id():
    """نطاق العرض الإداري المحجوز عند عدم وجود حساب مالك مثبت."""
    from sana_knowledge import SYSTEM_KNOWLEDGE_OWNER_ID
    account = current_account()
    return account["account_id"] if account else SYSTEM_KNOWLEDGE_OWNER_ID
@app.route("/api/knowledge/backups", methods=["GET"])
def knowledge_backups_api():
    from knowledge_backup import ensure_backup_schema
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    db = get_db()
    ensure_backup_schema(db)
    rows = db.execute(
        """SELECT run_id,snapshot_version,trigger_type,status,started_at,completed_at,
                  file_count,total_bytes,snapshot_sha256,drive_file_id,drive_web_link,
                  manifest,error_message
           FROM knowledge_backup_runs ORDER BY started_at DESC LIMIT 30"""
    ).fetchall()
    configured = bool(
        os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID")
        and (
            os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
            or os.environ.get("REPLIT_CONNECTORS_HOSTNAME")
            or os.environ.get("CONNECTORS_HOSTNAME")
        )
    )
    connector_configured = bool(
        os.environ.get("REPLIT_CONNECTORS_HOSTNAME")
        or os.environ.get("CONNECTORS_HOSTNAME")
    )
    data = []
    for row in rows:
        item = dict(row)
        raw_manifest = item.pop("manifest", None)
        try:
            item["manifest"] = json.loads(raw_manifest) if raw_manifest else None
        except (TypeError, ValueError):
            item["manifest"] = None
        data.append(item)
    return jsonify({
        "success": True,
        "configured": configured,
        "connection_mode": "replit_google_drive" if connector_configured else "service_account",
        "schedule": "hourly external reconciliation; one snapshot per Asia/Riyadh date",
        "scheduler_source": "GitHub Actions OIDC",
        "timezone": "Asia/Riyadh",
        "data": data,
    })


@app.route("/api/knowledge/backups/run", methods=["POST"])
def knowledge_backup_run_api():
    from knowledge_backup import run_backup
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    result = run_backup(get_db(), "manual")
    return jsonify(result), (200 if result.get("success") else 503)

@app.route("/api/internal/knowledge-backups/reconcile", methods=["POST"])
@csrf.exempt
def knowledge_backup_reconcile_api():
    """Wake autoscale safely from GitHub; OIDC is short-lived and secretless."""
    from github_oidc import verify_github_actions_token
    from knowledge_backup import reconcile_missing_backups

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return jsonify({"success": False, "error": "OIDC_REQUIRED"}), 401
    audience = request.host_url.rstrip("/")
    repository = os.environ.get(
        "GITHUB_BACKUP_REPOSITORY",
        "sanaaos2026/sana-app",
    )
    try:
        verify_github_actions_token(
            header.removeprefix("Bearer ").strip(),
            audience=audience,
            repository=repository,
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 403
    result = reconcile_missing_backups(get_db(), "github_schedule")
    return jsonify(result), (200 if result.get("success") else 503)
@app.route("/api/knowledge/sources", methods=["GET"])
def knowledge_sources_api():
    from sana_knowledge import list_sources
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    return jsonify({"success": True, "data": list_sources(get_db())})


@app.route("/api/knowledge/sources", methods=["POST"])
def knowledge_sources_create_api():
    from sana_knowledge import create_source
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    db = get_db()
    source_id = str(body.get("source_id") or "").strip()
    if source_id and db.execute(
        "SELECT 1 FROM knowledge_sources WHERE source_id=?", (source_id,)
    ).fetchone():
        return jsonify({"success": False, "error": "SOURCE_EXISTS"}), 409
    try:
        result = create_source(db, body)
    except Exception:
        db.rollback()
        return jsonify({"success": False, "error": "SOURCE_CREATE_FAILED"}), 400
    if not result["success"]:
        return jsonify(result), 400
    return jsonify(result), 201

@app.route("/api/knowledge/sources/<source_id>", methods=["PATCH"])
def knowledge_source_review_api(source_id):
    from sana_knowledge import review_source
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    account = current_account() or {}
    result = review_source(
        get_db(), source_id, str(body.get("status") or ""),
        account.get("account_id") or "admin-preview",
    )
    status = 200 if result.get("success") else (
        404 if result.get("error") == "SOURCE_NOT_FOUND" else 409
    )
    return jsonify(result), status


@app.route("/api/knowledge/research-cycle", methods=["GET"])
def knowledge_research_cycle_api():
    from sana_research_cycle import list_dashboard
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    return jsonify({"success": True, **list_dashboard(get_db())})


@app.route("/api/knowledge/research-cycle/config", methods=["PATCH"])
def knowledge_research_cycle_config_api():
    from sana_research_cycle import update_config
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    result = update_config(get_db(), request.get_json(silent=True) or {})
    return jsonify(result), (200 if result.get("success") else 400)


@app.route("/api/knowledge/research-cycle/run", methods=["POST"])
def knowledge_research_cycle_run_api():
    from sana_research_cycle import configured_search_provider, run_cycle
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    provider = configured_search_provider(get_db())
    result = run_cycle(
        get_db(), "manual", search_provider=provider,
        taxonomy={"sectors": [s["key"] for s in SECTORS],
                  "goals": ["acquisition", "retention", "revenue",
                            "operations", "quality", "profitability"]},
    )
    return jsonify(result), (200 if result.get("success") else 503)


@app.route("/api/knowledge/research-cycle/candidates/<candidate_id>", methods=["PATCH"])
def knowledge_research_candidate_review_api(candidate_id):
    from sana_research_cycle import review_candidate
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    account = current_account() or {}
    result = review_candidate(
        get_db(), candidate_id, str(body.get("decision") or ""),
        account.get("account_id") or "admin-preview",
    )
    status = 200 if result.get("success") else (
        404 if result.get("error") == "CANDIDATE_NOT_FOUND" else 409
    )
    return jsonify(result), status
@app.route("/api/knowledge/private-sources", methods=["GET"])
def private_research_sources_api():
    from sana_knowledge import list_research_sources
    guard = _private_knowledge_guard()
    if guard:
        return guard
    return jsonify({
        "success": True,
        "data": list_research_sources(
            get_db(),
            query=request.args.get("q", ""),
            review_status=request.args.get("status") or None,
            source_kind=request.args.get("kind") or None,
            owner_account_id=_private_knowledge_owner_id(),
        ),
    })


@app.route("/api/knowledge/private-sources", methods=["POST"])
def private_research_source_create_api():
    from sana_knowledge import create_research_source
    guard = _private_knowledge_guard()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    try:
        result = create_research_source(
            get_db(),
            body,
            owner_account_id=_private_knowledge_owner_id(),
            company_id=(current_account() or {}).get("company_id"),
        )
    except Exception:
        get_db().rollback()
        return jsonify({"success": False, "error": "RESEARCH_SOURCE_CREATE_FAILED"}), 400
    if not result["success"]:
        return jsonify(result), 400
    return jsonify(result), 201


@app.route("/api/knowledge/private-sources/upload", methods=["POST"])
def private_research_source_upload_api():
    from sana_knowledge import create_uploaded_research_source
    guard = _private_knowledge_guard()
    if guard:
        return guard
    uploaded = request.files.get("file")
    if not uploaded:
        return jsonify({"success": False, "error": "FILE_REQUIRED"}), 400
    try:
        result = create_uploaded_research_source(
            get_db(),
            filename=uploaded.filename,
            mime_type=uploaded.mimetype,
            content=uploaded.read(),
            payload=request.form,
            owner_account_id=_private_knowledge_owner_id(),
            company_id=(current_account() or {}).get("company_id"),
        )
    except Exception:
        get_db().rollback()
        return jsonify({"success": False, "error": "FILE_UPLOAD_FAILED"}), 400
    if not result["success"]:
        return jsonify(result), 409 if result["error"] == "DUPLICATE_FILE" else 400
    return jsonify(result), 201


@app.route("/api/knowledge/private-sources/<research_source_id>/download")
def private_research_source_download_api(research_source_id):
    from sana_knowledge import get_research_source_file
    guard = _private_knowledge_guard()
    if guard:
        return guard
    row, error = get_research_source_file(
        get_db(), research_source_id, _private_knowledge_owner_id()
    )
    if error:
        status = {
            "RESEARCH_SOURCE_NOT_FOUND": 404,
            "RIGHTS_RESTRICTED": 403,
            "RIGHTS_EXPIRED": 410,
        }.get(error, 404)
        return jsonify({"success": False, "error": error}), status
    from io import BytesIO
    return send_file(
        BytesIO(bytes(row["content"])),
        as_attachment=True,
        download_name=row["original_name"],
        mimetype=row["mime_type"] or "application/octet-stream",
    )


@app.route("/api/knowledge/private-sources/<research_source_id>", methods=["PATCH"])
def private_research_source_status_api(research_source_id):
    from sana_knowledge import update_research_source_status
    guard = _private_knowledge_guard()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    try:
        result = update_research_source_status(
            get_db(),
            research_source_id,
            str(body.get("review_status") or ""),
            _private_knowledge_owner_id(),
        )
    except Exception:
        get_db().rollback()
        return jsonify({"success": False, "error": "RESEARCH_SOURCE_UPDATE_FAILED"}), 400
    if not result["success"]:
        status = {
            "RESEARCH_SOURCE_NOT_FOUND": 404,
            "RIGHTS_RESTRICTED": 403,
            "RIGHTS_EXPIRED": 410,
        }.get(result["error"], 400)
        return jsonify(result), status
    return jsonify(result)

@app.route("/api/knowledge/drive", methods=["GET"])
def drive_index_status_api():
    from drive_index import drive_index_report
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    return jsonify({"success": True, "data": drive_index_report(get_db())})
@app.route("/knowledge")
def knowledge_console():
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    return render_template(
        "20-knowledge-console.html",
        search_company_id=default_company_id(),
    )


@app.route("/research-library")
def research_library():
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    return render_template("21-research-library.html")


@app.route("/api/cases/<case_id>/diagnose", methods=["POST"])
def diagnose_case_from_knowledge(case_id):
    """المسار الحاكم: Sana Knowledge → Evidence → Rules، دون AI."""
    from sana_knowledge import run_diagnostic
    db = get_db()
    case = db.execute("SELECT company_id FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not case:
        return jsonify({"success": False, "error": "CASE_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(case["company_id"])
    if guard:
        return guard
    result = run_diagnostic(db, case_id)
    return jsonify({"success": True, "data": result})


@app.route("/api/cases/<case_id>/scan", methods=["POST"])
def run_case_scan(case_id):
    """SDS-001 + الأصول الخمسة → Sana Scan واحد قابل للمراجعة."""
    from sana_scan import run_scan
    db = get_db()
    case = db.execute("SELECT company_id FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not case:
        return jsonify({"success": False, "error": "CASE_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(case["company_id"])
    if guard:
        return guard
    result = run_scan(db, case_id)
    return jsonify({"success": True, "data": result})


@app.route("/api/cases/<case_id>/diagnostic-baseline", methods=["GET", "PUT"])
def case_diagnostic_baseline(case_id):
    """Create/update the period contract for any company-owned diagnostic case."""
    from sana_reliability import validate_context
    db = get_db()
    case = db.execute(
        "SELECT company_id FROM cases WHERE case_id=?", (case_id,)
    ).fetchone()
    if not case:
        return jsonify({"success": False, "error": "CASE_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(case["company_id"])
    if guard:
        return guard
    if request.method == "GET":
        row = db.execute(
            """SELECT baseline_id, company_id, case_id, baseline_start, baseline_end,
                      comparison_start, comparison_end, seasonality_context, created_at
               FROM diagnostic_baselines
               WHERE company_id=? AND case_id=?""",
            (case["company_id"], case_id),
        ).fetchone()
        if not row:
            return jsonify({"success": True, "data": None})
        data = dict(row)
        for key, value in list(data.items()):
            if hasattr(value, "isoformat"):
                data[key] = value.isoformat()
        return jsonify({"success": True, "data": data})

    body = request.get_json(silent=True) or {}
    try:
        baseline = validate_context({
            "information_type": "Narrative",
            "period_start": body.get("baseline_start"),
            "period_end": body.get("baseline_end"),
        })
        if not all((baseline["period_start"], baseline["period_end"])):
            raise ValueError("حدد الفترة اللي عندك عنها بيانات فعلية.")
        today = date.today().isoformat()
        if baseline["period_start"] > today:
            return jsonify({
                "success": False,
                "error": "FUTURE_DIAGNOSTIC_PERIOD",
                "classification": "TARGET",
                "message": "هذه فترة مستهدفة، وليست نتيجة فعلية بعد",
            }), 400
        if baseline["period_end"] > today:
            return jsonify({
                "success": False,
                "error": "FUTURE_DIAGNOSTIC_PERIOD",
                "classification": "FORECAST",
                "message": "هذه فترة مستهدفة، وليست نتيجة فعلية بعد",
            }), 400

        comparison_start = body.get("comparison_start") or None
        comparison_end = body.get("comparison_end") or None
        if bool(comparison_start) != bool(comparison_end):
            raise ValueError("حدد بداية الفترة السابقة ونهايتها، أو اختر ما عندي مقارنة الآن.")
        comparison = {"period_start": None, "period_end": None}
        if comparison_start and comparison_end:
            comparison = validate_context({
                "information_type": "Narrative",
                "period_start": comparison_start,
                "period_end": comparison_end,
            })
            if comparison["period_end"] > today:
                raise ValueError("المقارنة تحتاج فترة انتهت فعليًا")
            if (
                comparison["period_start"] == baseline["period_start"]
                and comparison["period_end"] == baseline["period_end"]
            ):
                raise ValueError("الفترة السابقة لازم تكون مختلفة عن الفترة الحالية.")
            if comparison["period_end"] >= baseline["period_start"]:
                raise ValueError("الفترة السابقة لازم تنتهي قبل بداية الفترة الحالية.")
    except ValueError as exc:
        return jsonify({
            "success": False,
            "error": "INVALID_DIAGNOSTIC_BASELINE",
            "message": str(exc),
        }), 400
    seasonality = str(body.get("seasonality_context") or "").strip()
    if not seasonality:
        return jsonify({
            "success": False,
            "error": "SEASONALITY_CONTEXT_REQUIRED",
            "message": "حدد سياق الموسمية أو صرّح بأنه غير معروف.",
        }), 400
    baseline_id = "DBL-" + uuid.uuid4().hex[:10].upper()
    db.execute(
        """INSERT INTO diagnostic_baselines
           (baseline_id, company_id, case_id, baseline_start, baseline_end,
            comparison_start, comparison_end, seasonality_context)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT (company_id, case_id) DO UPDATE SET
             baseline_start=EXCLUDED.baseline_start,
             baseline_end=EXCLUDED.baseline_end,
             comparison_start=EXCLUDED.comparison_start,
             comparison_end=EXCLUDED.comparison_end,
             seasonality_context=EXCLUDED.seasonality_context""",
        (
            baseline_id, case["company_id"], case_id,
            baseline["period_start"], baseline["period_end"],
            comparison["period_start"], comparison["period_end"], seasonality,
        ),
    )
    db.commit()
    row = db.execute(
        """SELECT baseline_id, company_id, case_id, baseline_start, baseline_end,
                  comparison_start, comparison_end, seasonality_context
           FROM diagnostic_baselines WHERE company_id=? AND case_id=?""",
        (case["company_id"], case_id),
    ).fetchone()
    data = dict(row)
    for key, value in list(data.items()):
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
    return jsonify({"success": True, "data": data})


_COMPANY_SOURCE_TYPES = {"website", "platform", "other"}
_COMPANY_SOCIAL_HOSTS = (
    "instagram.com", "linkedin.com", "tiktok.com", "facebook.com",
    "x.com", "twitter.com", "youtube.com", "snapchat.com",
    "g.page", "maps.app.goo.gl", "google.com",
)


def _company_source_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except (TypeError, ValueError):
        return None


def _infer_company_source_type(source_url):
    parsed = urlparse(str(source_url or "").strip())
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if any(host == item or host.endswith("." + item) for item in _COMPANY_SOCIAL_HOSTS):
        if host.endswith("google.com") and "/maps" not in path:
            return "website"
        return "platform"
    return "website"


def _normalize_company_source_url(value):
    candidate = str(value or "").strip()
    if candidate and "://" not in candidate:
        candidate = "https://" + candidate
    parsed = urlparse(candidate)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("INVALID_SOURCE_URL")
    return candidate


def _company_source_reminder_state(db, company_id, now=None):
    """حالة اختيارية مستقلة عن Discovery وScan وقرار الشركة."""
    from sana_knowledge import ensure_schema as ensure_knowledge_schema
    ensure_knowledge_schema(db)
    company = db.execute(
        """SELECT sds_done,created_at,website_url,social_media_url,
                  business_reference_url,source_prompt_last_shown_at,
                  source_prompt_dismissed_at,source_last_confirmed_at
           FROM companies WHERE company_id=?""",
        (company_id,),
    ).fetchone()
    if not company or not company["sds_done"]:
        return {"show": False, "kind": None, "missing_types": []}

    source_types = set()
    if str(company["website_url"] or "").strip():
        source_types.add("website")
    if str(company["social_media_url"] or "").strip():
        source_types.add("platform")
    if str(company["business_reference_url"] or "").strip():
        source_types.add("other")

    source_rows = db.execute(
        """SELECT origin,source_url,tags,created_at,updated_at
           FROM research_sources
           WHERE company_id=? AND review_status<>'archived'""",
        (company_id,),
    ).fetchall()
    source_times = []
    for row in source_rows:
        if row["origin"] == "upload":
            source_types.add("file")
        elif row["source_url"]:
            tag_match = re.search(
                r"(?:^|[|,])company-source:(website|platform|other)(?:$|[|,])",
                str(row["tags"] or ""),
            )
            source_types.add(
                tag_match.group(1)
                if tag_match else _infer_company_source_type(row["source_url"])
            )
        source_times.extend(filter(None, (
            _company_source_datetime(row["updated_at"]),
            _company_source_datetime(row["created_at"]),
        )))

    missing_types = [
        source_type for source_type in ("website", "platform", "file")
        if source_type not in source_types
    ]
    current_time = (now or datetime.utcnow()).replace(tzinfo=None)
    last_prompt = max(filter(None, (
        _company_source_datetime(company["source_prompt_last_shown_at"]),
        _company_source_datetime(company["source_prompt_dismissed_at"]),
    )), default=None)
    in_cooldown = bool(
        last_prompt and current_time - last_prompt < timedelta(days=30)
    )
    if missing_types:
        return {
            "show": not in_cooldown,
            "kind": "missing",
            "missing_types": missing_types,
            "content_retrieved": False,
        }

    confirmation_times = source_times + list(filter(None, (
        _company_source_datetime(company["source_last_confirmed_at"]),
        _company_source_datetime(company["created_at"]),
    )))
    latest_confirmation = max(confirmation_times, default=None)
    needs_confirmation = bool(
        latest_confirmation
        and current_time - latest_confirmation > timedelta(days=90)
    )
    return {
        "show": bool(needs_confirmation and not in_cooldown),
        "kind": "freshness" if needs_confirmation else None,
        "missing_types": [],
        "content_retrieved": False,
    }


@app.route("/api/companies")
def companies_list():
    """قائمة الشركات — مقيَّدة بمفتاح المشرف فقط (P0-1: إغلاق التسريب)."""
    if not is_admin_preview():
        return jsonify({
            "success": False, "error": "UNAUTHORIZED",
            "message": "هذا المسار محمي — يلزم مفتاح المشرف.",
        }), 401
    db = get_db()
    companies = db.execute(
        "SELECT company_id, name, sector, city FROM companies ORDER BY company_id ASC"
    ).fetchall()
    return jsonify({
        "success": True,
        "data": [dict(c) for c in companies],
    })


@app.route("/api/companies/<company_id>/summary")
def company_summary(company_id):
    db = get_db()
    company = db.execute("SELECT * FROM companies WHERE company_id=?", (company_id,)).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard

    assets = db.execute("SELECT * FROM assets WHERE company_id=? ORDER BY current_score ASC",
                         (company_id,)).fetchall()
    cases = db.execute("SELECT * FROM cases WHERE company_id=?", (company_id,)).fetchall()
    decisions = db.execute(
        "SELECT * FROM decisions WHERE company_id=? ORDER BY created_at DESC", (company_id,)
    ).fetchall()
    tasks = db.execute("SELECT * FROM tasks WHERE company_id=?", (company_id,)).fetchall()
    evidence = db.execute("SELECT * FROM evidence WHERE company_id=?", (company_id,)).fetchall()

    weakest_asset = min(assets, key=lambda a: a["current_score"]) if assets else None
    strongest_asset = max(assets, key=lambda a: a["current_score"]) if assets else None
    open_decisions = [d for d in decisions if d["status"] in ("مقترح", "قيد التنفيذ")]
    active_tasks = [t for t in tasks if t["status"] != "منجزة"]

    avg_score = round(sum(a["current_score"] for a in assets) / len(assets)) if assets else 0
    client_assets = [_client_asset_view(asset) for asset in assets]
    from sana_company_memory import retrieve_memory
    memory = retrieve_memory(db, company_id)

    return jsonify({
        "success": True,
        "data": {
            "company": dict(company),
            "quality_score": avg_score,
            "assets": client_assets,
            "weakest_asset": (
                _client_asset_view(weakest_asset) if weakest_asset else None
            ),
            "strongest_asset": (
                _client_asset_view(strongest_asset) if strongest_asset else None
            ),
            "cases": [dict(c) for c in cases],
            "decisions": [dict(d) for d in decisions],
            "open_decisions_count": len(open_decisions),
            "top_decision": dict(open_decisions[0]) if open_decisions else None,
            "next_task": dict(active_tasks[0]) if active_tasks else None,
            "tasks": [dict(t) for t in tasks],
            "evidence_count": len(evidence),
            "company_memory": memory,
            "source_reminder": _company_source_reminder_state(db, company_id),
        },
        "meta": {"generated_at": datetime.utcnow().isoformat() + "Z"}
    })


@app.route("/api/companies/<company_id>/source-reminder", methods=["POST"])
def company_source_reminder_action(company_id):
    account = current_account()
    if not account or not account.get("company_id"):
        return jsonify({"success": False, "error": "ACCOUNT_REQUIRED"}), 401
    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "").strip()
    if action not in {"shown", "later", "confirmed"}:
        return jsonify({"success": False, "error": "INVALID_ACTION"}), 400
    db = get_db()
    if action == "shown":
        db.execute(
            """UPDATE companies SET source_prompt_last_shown_at=now()
               WHERE company_id=?""",
            (company_id,),
        )
    elif action == "later":
        db.execute(
            """UPDATE companies
               SET source_prompt_last_shown_at=now(),
                   source_prompt_dismissed_at=now()
               WHERE company_id=?""",
            (company_id,),
        )
    else:
        db.execute(
            """UPDATE companies
               SET source_last_confirmed_at=now(),
                   source_prompt_last_shown_at=now()
               WHERE company_id=?""",
            (company_id,),
        )
    db.commit()
    return jsonify({
        "success": True,
        "data": _company_source_reminder_state(db, company_id),
    })


@app.route("/api/companies/<company_id>/source-reminder/url", methods=["POST"])
def company_source_reminder_url(company_id):
    from sana_knowledge import create_research_source
    account = current_account()
    if not account or not account.get("company_id"):
        return jsonify({"success": False, "error": "ACCOUNT_REQUIRED"}), 401
    body = request.get_json(silent=True) or {}
    try:
        source_url = _normalize_company_source_url(body.get("url"))
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    source_type = str(body.get("source_type") or "").strip()
    if not source_type:
        source_type = _infer_company_source_type(source_url)
    if source_type not in _COMPANY_SOURCE_TYPES:
        return jsonify({"success": False, "error": "INVALID_SOURCE_TYPE"}), 400

    db = get_db()
    existing = db.execute(
        """SELECT research_source_id FROM research_sources
           WHERE company_id=? AND lower(trim(source_url))=lower(trim(?))
           LIMIT 1""",
        (company_id, source_url),
    ).fetchone()
    column = {
        "website": "website_url",
        "platform": "social_media_url",
        "other": "business_reference_url",
    }[source_type]
    db.execute(
        f"""UPDATE companies SET {column}=CASE
              WHEN {column} IS NULL OR trim({column})='' THEN ?
              ELSE {column} END
            WHERE company_id=?""",
        (source_url, company_id),
    )
    if existing:
        db.commit()
        return jsonify({
            "success": True,
            "data": {
                "research_source_id": existing["research_source_id"],
                "source_type": source_type,
                "duplicate": True,
                "reminder": _company_source_reminder_state(db, company_id),
            },
        })
    try:
        result = create_research_source(
            db,
            {
                "title": {
                    "website": "موقع الشركة",
                    "platform": "منصة الشركة",
                    "other": "مرجع الشركة",
                }[source_type],
                "source_kind": "summary",
                "origin": "url",
                "source_url": source_url,
                "rights_status": "pending",
                "tags": f"company-source:{source_type}",
                "notes": (
                    "رابط قدمته الشركة كسياق فقط؛ لم يُفتح أو يُفحص، "
                    "ولا يتحول إلى Fact أو Finding قبل retrieval وتحقق فعلي."
                ),
            },
            owner_account_id=account["account_id"],
            company_id=company_id,
        )
    except Exception:
        db.rollback()
        return jsonify({"success": False, "error": "SOURCE_CREATE_FAILED"}), 400
    if not result.get("success"):
        db.rollback()
        return jsonify(result), 400
    return jsonify({
        "success": True,
        "data": {
            **result,
            "source_type": source_type,
            "reminder": _company_source_reminder_state(db, company_id),
        },
    }), 201


@app.route("/api/companies/<company_id>/source-reminder/upload", methods=["POST"])
def company_source_reminder_upload(company_id):
    from sana_knowledge import create_uploaded_research_source
    account = current_account()
    if not account or not account.get("company_id"):
        return jsonify({"success": False, "error": "ACCOUNT_REQUIRED"}), 401
    uploaded = request.files.get("file")
    if not uploaded:
        return jsonify({"success": False, "error": "FILE_REQUIRED"}), 400
    db = get_db()
    try:
        result = create_uploaded_research_source(
            db,
            filename=uploaded.filename,
            mime_type=uploaded.mimetype,
            content=uploaded.read(),
            payload={
                "title": uploaded.filename,
                "source_kind": "file",
                "rights_status": "pending",
                "knowledge_scope": "private",
                "tags": "company-source:file",
                "notes": (
                    "ملف قدمته الشركة إلى صندوق المصادر الخاص؛ يبقى في قيد "
                    "المراجعة ولا يتحول تلقائيًا إلى Fact أو Finding."
                ),
                "ingestion_event": "company_source_reminder_upload",
            },
            owner_account_id=account["account_id"],
            company_id=company_id,
        )
    except Exception:
        db.rollback()
        return jsonify({"success": False, "error": "FILE_UPLOAD_FAILED"}), 400
    if not result.get("success"):
        if (
            result.get("error") == "DUPLICATE_FILE"
            and result.get("research_source_id")
        ):
            return jsonify({
                "success": True,
                "data": {
                    **result,
                    "duplicate": True,
                    "reminder": _company_source_reminder_state(db, company_id),
                },
            })
        return jsonify(result), 409 if result.get("error") == "DUPLICATE_FILE" else 400
    return jsonify({
        "success": True,
        "data": {
            **result,
            "reminder": _company_source_reminder_state(db, company_id),
        },
    }), 201


@app.route("/api/companies/<company_id>/memory", methods=["GET", "POST"])
def company_memory_api(company_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    db = get_db()
    company = db.execute(
        "SELECT company_id FROM companies WHERE company_id=?", (company_id,)
    ).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404
    from sana_company_memory import record_memory, retrieve_memory
    if request.method == "GET":
        keys = [key for key in request.args.getlist("memory_key") if key]
        data = retrieve_memory(
            db, company_id, case_id=request.args.get("case_id"),
            problem=request.args.get("problem"), kpi=request.args.get("kpi"),
            decision_id=request.args.get("decision_id"),
            task_id=request.args.get("task_id"), memory_keys=keys or None,
            include_history=request.args.get("include_history") == "1",
        )
        return jsonify({"success": True, "data": data})
    payload = request.get_json(silent=True) or {}
    actor = current_account() or {}
    try:
        data = record_memory(
            db, company_id,
            memory_key=payload.get("memory_key"),
            memory_type=payload.get("memory_type"),
            value=payload.get("value"),
            source_ref=payload.get("source_ref"),
            source_type=payload.get("source_type", "account-input"),
            observed_at=payload.get("observed_at") or date.today(),
            period_start=payload.get("period_start"),
            period_end=payload.get("period_end"),
            context=payload.get("context"),
            verification_status=payload.get("verification_status", "UNVERIFIED"),
            freshness_class=payload.get("freshness_class", "MEDIUM"),
            source_strength=payload.get("source_strength", 35),
            verification_confidence=payload.get("verification_confidence", 0),
            freshness_confidence=payload.get("freshness_confidence", 50),
            owner_id=actor.get("account_id"), case_id=payload.get("case_id"),
            asset_id=payload.get("asset_id"), decision_id=payload.get("decision_id"),
            task_id=payload.get("task_id"), result_ref=payload.get("result_ref"),
            reason=payload.get("reason"),
        )
        db.commit()
        return jsonify({"success": True, "data": data}), 201
    except (LookupError, ValueError) as exc:
        db.rollback()
        return jsonify({"success": False, "error": str(exc)}), 400
def _execution_actor():
    account = current_account()
    if not account:
        return "admin-preview"
    account_data = dict(account)
    return str(
        account_data.get("account_id")
        or account_data.get("user_id")
        or account_data.get("email")
        or "authenticated-account"
    )


def _execution_error(exc):
    if isinstance(exc, LookupError):
        return jsonify({"success": False, "error": str(exc)}), 404
    return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/companies/<company_id>/decision-room")
def decision_room_overview(company_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    from sana_decision_room import decision_room
    try:
        return jsonify({"success": True, "data": decision_room(get_db(), company_id)})
    except (ValueError, LookupError) as exc:
        return _execution_error(exc)


@app.route("/api/companies/<company_id>/execution/tasks/<task_id>", methods=["PUT"])
def execution_task_update(company_id, task_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    from sana_decision_room import update_task
    db = get_db()
    try:
        result = update_task(
            db, company_id, task_id, request.get_json(silent=True) or {},
            _execution_actor(),
        )
        db.commit()
        return jsonify({"success": True, "data": result})
    except (ValueError, LookupError) as exc:
        db.rollback()
        return _execution_error(exc)


@app.route("/api/companies/<company_id>/execution/reminders")
def execution_reminders(company_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    from sana_decision_room import list_reminders, materialize_due_reminders
    db = get_db()
    actor = _execution_actor()
    admin = is_admin_preview()
    owner_id = request.args.get("owner_id") if admin else None
    try:
        materialize_due_reminders(db, company_id=company_id)
        db.commit()
        response = jsonify({
            "success": True,
            "data": list_reminders(
                db, company_id, owner_id=owner_id,
                recipient_account_id=None if admin else actor,
            ),
        })
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
        return response
    except (ValueError, LookupError) as exc:
        db.rollback()
        return _execution_error(exc)


def _valid_scheduler_token(token):
    if not token:
        return False
    db = _connect_pg()
    try:
        row = db.execute(
            """SELECT token_hash FROM sana_scheduler_credentials
               WHERE credential_name='execution-reminders' AND active=true"""
        ).fetchone()
    except Exception:
        return False
    finally:
        db.close()
    if not row:
        return False
    supplied_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return hmac.compare_digest(supplied_hash, row["token_hash"])


@app.route("/internal/execution-reminders/run", methods=["POST"])
@csrf.exempt
def internal_execution_reminders_run():
    authorization = request.headers.get("Authorization", "")
    token = authorization.removeprefix("Bearer ").strip()
    if not _valid_scheduler_token(token):
        return jsonify({"success": False, "error": "UNAUTHORIZED"}), 401
    from sana_decision_room import run_reminder_cycle
    result = run_reminder_cycle(_connect_pg)
    status_code = 500 if result.get("status") == "failed" else 200
    return jsonify({"success": status_code == 200, "data": result}), status_code


@app.route("/api/companies/<company_id>/execution/reminder-owners", methods=["GET", "PUT"])
def execution_reminder_owners(company_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    if not is_admin_preview():
        return jsonify({
            "success": False,
            "error": "ADMIN_PREVIEW_REQUIRED",
            "message": "تعيين حساب مستلم التنبيه عملية إدارية محمية.",
        }), 403
    from sana_decision_room import bind_owner_account, list_owner_bindings
    db = get_db()
    if request.method == "GET":
        return jsonify({
            "success": True, "data": list_owner_bindings(db, company_id)
        })
    body = request.get_json(silent=True) or {}
    try:
        result = bind_owner_account(
            db, company_id, body.get("owner_id"), body.get("account_id"),
            _execution_actor(), body.get("source_ref") or "reminder-owner-api",
        )
        db.commit()
        return jsonify({"success": True, "data": result})
    except (ValueError, LookupError) as exc:
        db.rollback()
        return _execution_error(exc)


@app.route("/api/companies/<company_id>/execution/reminders/<reminder_id>", methods=["PATCH"])
def execution_reminder_update(company_id, reminder_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    from sana_decision_room import update_reminder_status
    db = get_db()
    try:
        result = update_reminder_status(
            db, company_id, reminder_id,
            (request.get_json(silent=True) or {}).get("status"),
            _execution_actor(), admin_preview=is_admin_preview(),
        )
        db.commit()
        return jsonify({"success": True, "data": result})
    except PermissionError as exc:
        db.rollback()
        return jsonify({"success": False, "error": str(exc)}), 403
    except (ValueError, LookupError) as exc:
        db.rollback()
        return _execution_error(exc)


@app.route("/api/companies/<company_id>/execution/risks", methods=["GET", "POST"])
def execution_risks(company_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    from sana_decision_room import create_risk, list_risks
    db = get_db()
    if request.method == "GET":
        return jsonify({"success": True, "data": list_risks(db, company_id)})
    try:
        result = create_risk(db, company_id, request.get_json(silent=True) or {})
        db.commit()
        return jsonify({"success": True, "data": result}), 201
    except (ValueError, LookupError) as exc:
        db.rollback()
        return _execution_error(exc)


@app.route("/api/companies/<company_id>/execution/backlog", methods=["GET", "POST"])
def execution_backlog(company_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    from sana_decision_room import create_backlog, list_backlog
    db = get_db()
    if request.method == "GET":
        return jsonify({"success": True, "data": list_backlog(db, company_id)})
    try:
        result = create_backlog(db, company_id, request.get_json(silent=True) or {})
        db.commit()
        return jsonify({"success": True, "data": result}), 201
    except (ValueError, LookupError) as exc:
        db.rollback()
        return _execution_error(exc)


@app.route("/api/companies/<company_id>/execution/backlog/<backlog_id>/decision", methods=["POST"])
def execution_backlog_decision(company_id, backlog_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    from sana_decision_room import decide_backlog
    db = get_db()
    try:
        result = decide_backlog(
            db, company_id, backlog_id, request.get_json(silent=True) or {},
            _execution_actor(),
        )
        db.commit()
        return jsonify({"success": True, "data": result})
    except (ValueError, LookupError) as exc:
        db.rollback()
        return _execution_error(exc)


@app.route("/api/companies/<company_id>/execution/sops", methods=["GET", "POST"])
def execution_sops(company_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    from sana_decision_room import create_sop, list_sops
    db = get_db()
    if request.method == "GET":
        return jsonify({"success": True, "data": list_sops(db, company_id)})
    try:
        result = create_sop(
            db, company_id, request.get_json(silent=True) or {},
            _execution_actor(),
        )
        db.commit()
        return jsonify({"success": True, "data": result}), 201
    except (ValueError, LookupError) as exc:
        db.rollback()
        return _execution_error(exc)

@app.route(
    "/api/companies/<company_id>/execution/sops/<sop_id>/applications",
    methods=["GET", "POST"],
)
def execution_sop_applications(company_id, sop_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    from sana_decision_room import list_sop_applications, record_sop_application
    db = get_db()
    if request.method == "GET":
        try:
            data = list_sop_applications(
                db, company_id, sop_id, request.args.get("version_id")
            )
            return jsonify({"success": True, "data": data})
        except (ValueError, LookupError) as exc:
            return _execution_error(exc)
    try:
        result = record_sop_application(
            db, company_id, sop_id, request.get_json(silent=True) or {},
            _execution_actor(),
        )
        db.commit()
        return jsonify({"success": True, "data": result}), 201
    except (ValueError, LookupError) as exc:
        db.rollback()
        return _execution_error(exc)


@app.route(
    "/api/companies/<company_id>/execution/sops/<sop_id>/promote",
    methods=["POST"],
)
def promote_execution_sop(company_id, sop_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    from sana_decision_room import promote_sop
    db = get_db()
    try:
        result = promote_sop(
            db, company_id, sop_id, request.get_json(silent=True) or {},
            _execution_actor(),
        )
        db.commit()
        return jsonify({"success": True, "data": result})
    except (ValueError, LookupError) as exc:
        db.rollback()
        return _execution_error(exc)


@app.route("/api/methodology/<slug>")
def methodology_detail(slug):
    """وثيقة منهجية — داخلية بحتة، للمستشار أو الحساب الإداري."""
    if not is_admin_preview() and not _knowledge_admin_account_allowed():
        return jsonify({"success": False, "error": "FORBIDDEN"}), 403
    db = get_db()
    doc = db.execute("SELECT * FROM methodology_docs WHERE slug=?", (slug,)).fetchone()
    if not doc:
        return jsonify({"success": False, "error": "DOC_NOT_FOUND"}), 404
    doc_dict = dict(doc)
    raw_content = doc["content"] or ""
    # محاولة تفسير المحتوى كـ JSON (البنية القديمة) أو إعادته كـ markdown (البنية الجديدة)
    try:
        content_data = json.loads(raw_content)
        content_format = "json"
    except (json.JSONDecodeError, TypeError):
        content_data = {}
        content_format = "markdown"
    return jsonify({
        "success": True,
        "data": {
            "doc_id": doc["doc_id"],
            "slug": doc["slug"],
            "sector_tags": doc_dict.get("sector_tags"),
            "title": doc["title"],
            "subtitle": doc["subtitle"],
            "doc_type": doc_dict.get("doc_type"),
            "version": doc_dict.get("version"),
            "bos_id": doc_dict.get("bos_id"),
            "content_format": content_format,
            "markdown": raw_content if content_format == "markdown" else None,
            **content_data,
        }
    })


# ------------------------------------------------------------------
# API — Business Growth OS (الحقائق، Baseline، الهوية الموحدة)
# ------------------------------------------------------------------

@app.route("/api/growth-os/profiles")
def growth_os_profiles():
    from sana_growth_os import list_profiles
    return jsonify({"success": True, "data": list_profiles(get_db())})


def _growth_os_company_exists(company_id):
    db = get_db()
    company = db.execute("SELECT company_id FROM companies WHERE company_id=?", (company_id,)).fetchone()
    if not company:
        return None, (jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404)
    return db, None


def _growth_os_trust_boundary(payload, *, baseline=False):
    """Company users may submit claims, but only a reviewer may verify them."""
    cleaned = dict(payload or {})
    if is_admin_preview() or _knowledge_admin_account_allowed():
        return cleaned
    if baseline:
        metrics = {}
        for key, value in (cleaned.get("metrics") or {}).items():
            if isinstance(value, dict):
                metrics[key] = {
                    **value,
                    "verification_status": "UNVERIFIED",
                    "source_category": "SELF_REPORTED",
                }
            else:
                metrics[key] = value
        cleaned["metrics"] = metrics
    else:
        cleaned["verification_status"] = "UNVERIFIED"
        cleaned["source_category"] = "SELF_REPORTED"
    return cleaned


@app.route("/api/companies/<company_id>/growth-os")
def growth_os_overview(company_id):
    from sana_growth_os import get_company_profile, list_truth_records, list_baselines, list_canonical_entities, CANONICAL_MAP
    from sana_growth_engine import list_cycles, list_experiments
    db, error = _growth_os_company_exists(company_id)
    if error:
        return error
    return jsonify({
        "success": True,
        "data": {
            "profile": get_company_profile(db, company_id),
            "truth_records": list_truth_records(db, company_id),
            "baselines": list_baselines(db, company_id),
            "canonical_entities": list_canonical_entities(db, company_id),
            "canonical_map": CANONICAL_MAP,
            "bottleneck_cycles": list_cycles(db, company_id),
            "experiments": list_experiments(db, company_id),
        },
    })


@app.route("/api/companies/<company_id>/growth-os/profile", methods=["PUT"])
def update_growth_os_profile(company_id):
    from sana_growth_os import set_company_profile
    db, error = _growth_os_company_exists(company_id)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    try:
        profile = set_company_profile(db, company_id, body.get("profile_key"), body.get("source_ref", "company:profile-selection"))
        db.commit()
        return jsonify({"success": True, "data": profile})
    except LookupError:
        db.rollback()
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404
    except ValueError as exc:
        db.rollback()
        return jsonify({"success": False, "error": "INVALID_PROFILE", "message": str(exc)}), 400


@app.route("/api/companies/<company_id>/growth-os/truth", methods=["GET", "POST"])
def growth_os_truth(company_id):
    from sana_growth_os import create_truth_record, list_truth_records
    db, error = _growth_os_company_exists(company_id)
    if error:
        return error
    if request.method == "GET":
        return jsonify({"success": True, "data": list_truth_records(db, company_id)})
    try:
        payload = _growth_os_trust_boundary(request.get_json(silent=True) or {})
        record = create_truth_record(db, company_id, payload)
        db.commit()
        return jsonify({"success": True, "data": record}), 201
    except ValueError as exc:
        db.rollback()
        return jsonify({"success": False, "error": "INVALID_TRUTH_RECORD", "message": str(exc)}), 400


@app.route("/api/companies/<company_id>/growth-os/baselines", methods=["GET", "POST"])
def growth_os_baselines(company_id):
    from sana_growth_os import create_baseline, list_baselines
    db, error = _growth_os_company_exists(company_id)
    if error:
        return error
    if request.method == "GET":
        return jsonify({"success": True, "data": list_baselines(db, company_id)})
    try:
        payload = _growth_os_trust_boundary(
            request.get_json(silent=True) or {}, baseline=True
        )
        baseline = create_baseline(db, company_id, payload)
        db.commit()
        return jsonify({"success": True, "data": baseline}), 201
    except ValueError as exc:
        db.rollback()
        return jsonify({"success": False, "error": "INVALID_BASELINE", "message": str(exc)}), 400


@app.route("/api/companies/<company_id>/growth-os/baselines/<baseline_id>/validate")
def validate_growth_os_baseline(company_id, baseline_id):
    from sana_growth_os import require_usable_baseline
    db, error = _growth_os_company_exists(company_id)
    if error:
        return error
    try:
        baseline = require_usable_baseline(db, company_id, baseline_id)
        return jsonify({"success": True, "data": {"usable": True, "baseline": baseline}})
    except LookupError:
        return jsonify({"success": False, "error": "BASELINE_NOT_FOUND"}), 404
    except ValueError as exc:
        return jsonify({"success": False, "error": "BASELINE_NOT_USABLE", "message": str(exc), "usable": False}), 422


@app.route("/api/companies/<company_id>/growth-os/canonical", methods=["GET", "POST"])
def growth_os_canonical(company_id):
    from sana_growth_os import register_canonical_entity, list_canonical_entities, CANONICAL_MAP
    db, error = _growth_os_company_exists(company_id)
    if error:
        return error
    if request.method == "GET":
        return jsonify({"success": True, "data": list_canonical_entities(db, company_id), "map": CANONICAL_MAP})
    try:
        entity, created = register_canonical_entity(db, company_id, request.get_json(silent=True) or {})
        db.commit()
        return jsonify({"success": True, "created": created, "data": entity}), 201 if created else 200
    except ValueError as exc:
        db.rollback()
        code = "DUPLICATE_CANONICAL_ENTITY" if str(exc).startswith("DUPLICATE_CANONICAL_ENTITY") else "INVALID_CANONICAL_ENTITY"
        return jsonify({"success": False, "error": code, "message": str(exc)}), 409 if code == "DUPLICATE_CANONICAL_ENTITY" else 400


@app.route("/api/companies/<company_id>/growth-os/canonical/merge", methods=["POST"])
def growth_os_canonical_merge(company_id):
    from sana_growth_os import merge_canonical_entity
    db, error = _growth_os_company_exists(company_id)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    try:
        merge = merge_canonical_entity(db, company_id, body.get("from_canonical_id"), body.get("to_canonical_id"),
                                       body.get("reason"), body.get("source_ref"))
        db.commit()
        return jsonify({"success": True, "data": merge}), 201
    except LookupError:
        db.rollback()
        return jsonify({"success": False, "error": "CANONICAL_ENTITY_NOT_FOUND"}), 404
    except ValueError as exc:
        db.rollback()
        return jsonify({"success": False, "error": "INVALID_CANONICAL_MERGE", "message": str(exc)}), 400


def _growth_engine_failure(db, exc, default_code):
    db.rollback()
    message = str(exc)
    if isinstance(exc, LookupError):
        code = message.strip("'")
        return jsonify({"success": False, "error": code}), 404
    if "EVIDENCE_GATE" in message or "BASELINE_" in message or "_GATE:" in message:
        return jsonify({"success": False, "error": "EVIDENCE_GATE", "message": message}), 422
    if "_LOCKED:" in message or "_STATE:" in message or "_ORDER:" in message:
        return jsonify({"success": False, "error": default_code, "message": message}), 409
    return jsonify({"success": False, "error": default_code, "message": message}), 400


@app.route("/api/companies/<company_id>/growth-os/bottleneck-cycles", methods=["GET", "POST"])
def growth_os_bottleneck_cycles(company_id):
    from sana_growth_engine import create_bottleneck_cycle, list_cycles
    db, error = _growth_os_company_exists(company_id)
    if error:
        return error
    if request.method == "GET":
        return jsonify({"success": True, "data": list_cycles(db, company_id)})
    try:
        cycle = create_bottleneck_cycle(db, company_id, request.get_json(silent=True) or {})
        db.commit()
        return jsonify({"success": True, "data": cycle}), 201
    except (LookupError, ValueError) as exc:
        return _growth_engine_failure(db, exc, "INVALID_BOTTLENECK_CYCLE")


@app.route("/api/companies/<company_id>/growth-os/experiments", methods=["GET", "POST"])
def growth_os_experiments(company_id):
    from sana_growth_engine import create_experiment, list_experiments
    db, error = _growth_os_company_exists(company_id)
    if error:
        return error
    if request.method == "GET":
        return jsonify({"success": True, "data": list_experiments(db, company_id)})
    try:
        experiment = create_experiment(db, company_id, request.get_json(silent=True) or {})
        db.commit()
        return jsonify({"success": True, "data": experiment}), 201
    except (LookupError, ValueError) as exc:
        return _growth_engine_failure(db, exc, "INVALID_EXPERIMENT")


@app.route("/api/companies/<company_id>/growth-os/experiments/<experiment_id>", methods=["PATCH"])
def update_growth_os_experiment(company_id, experiment_id):
    from sana_growth_engine import update_experiment
    db, error = _growth_os_company_exists(company_id)
    if error:
        return error
    try:
        experiment = update_experiment(db, company_id, experiment_id, request.get_json(silent=True) or {})
        db.commit()
        return jsonify({"success": True, "data": experiment})
    except (LookupError, ValueError) as exc:
        return _growth_engine_failure(db, exc, "INVALID_EXPERIMENT")


@app.route("/api/companies/<company_id>/growth-os/experiments/<experiment_id>/start", methods=["POST"])
def start_growth_os_experiment(company_id, experiment_id):
    from sana_growth_engine import begin_experiment
    db, error = _growth_os_company_exists(company_id)
    if error:
        return error
    try:
        experiment = begin_experiment(db, company_id, experiment_id)
        db.commit()
        return jsonify({"success": True, "data": experiment})
    except (LookupError, ValueError) as exc:
        return _growth_engine_failure(db, exc, "EXPERIMENT_START_FAILED")


@app.route("/api/companies/<company_id>/growth-os/experiments/<experiment_id>/close", methods=["POST"])
def close_growth_os_experiment(company_id, experiment_id):
    from sana_growth_engine import close_experiment
    db, error = _growth_os_company_exists(company_id)
    if error:
        return error
    try:
        experiment = close_experiment(db, company_id, experiment_id, request.get_json(silent=True) or {})
        db.commit()
        return jsonify({"success": True, "data": experiment})
    except (LookupError, ValueError) as exc:
        return _growth_engine_failure(db, exc, "EXPERIMENT_CLOSE_FAILED")


@app.route("/api/companies/<company_id>/growth-os/experiments/<experiment_id>/process", methods=["PATCH"])
def advance_growth_os_process(company_id, experiment_id):
    from sana_growth_engine import advance_process
    db, error = _growth_os_company_exists(company_id)
    if error:
        return error
    try:
        process = advance_process(db, company_id, experiment_id, request.get_json(silent=True) or {})
        db.commit()
        return jsonify({"success": True, "data": process})
    except (LookupError, ValueError) as exc:
        return _growth_engine_failure(db, exc, "PROCESS_ADVANCE_FAILED")


@app.route("/api/companies/<company_id>/growth-os/decisions/<decision_id>/approve", methods=["POST"])
def approve_growth_os_decision(company_id, decision_id):
    from sana_growth_engine import approve_decision
    db, error = _growth_os_company_exists(company_id)
    if error:
        return error
    try:
        decision = approve_decision(db, company_id, decision_id)
        db.commit()
        return jsonify({"success": True, "data": decision})
    except (LookupError, ValueError) as exc:
        return _growth_engine_failure(db, exc, "DECISION_APPROVAL_FAILED")


@app.route("/api/companies/<company_id>/growth-os/learning-links", methods=["GET", "POST"])
def growth_os_learning_links(company_id):
    from sana_growth_engine import create_learning_link, list_learning_links
    db, error = _growth_os_company_exists(company_id)
    if error:
        return error
    if request.method == "GET":
        return jsonify({"success": True, "data": list_learning_links(db, company_id)})
    try:
        link, created = create_learning_link(db, company_id, request.get_json(silent=True) or {})
        db.commit()
        return jsonify({"success": True, "created": created, "data": link}), 201 if created else 200
    except (LookupError, ValueError) as exc:
        return _growth_engine_failure(db, exc, "INVALID_LEARNING_LINK")


@app.route("/api/companies/<company_id>/services")
def company_services(company_id):
    """أحدث قرار تموضع/ترتيب خدمات معتمد للشركة — يغذّي صفحة الخدمات."""
    db = get_db()
    company = db.execute("SELECT * FROM companies WHERE company_id=?", (company_id,)).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404

    decision = db.execute(
        "SELECT * FROM decisions WHERE company_id=? AND structured_data IS NOT NULL "
        "ORDER BY created_at DESC LIMIT 1",
        (company_id,)
    ).fetchone()

    if not decision:
        return jsonify({"success": True, "data": None})

    structured = json.loads(decision["structured_data"])
    return jsonify({
        "success": True,
        "data": {
            "company": dict(company),
            "decision_id": decision["decision_id"],
            "decision_title": decision["title"],
            "decision_status": decision["status"],
            "case_id": decision["case_id"],
            **structured,
        }
    })


@app.route("/api/cases/<case_id>")
def case_detail(case_id):
    from sana_knowledge import latest_diagnostic
    db = get_db()
    case = db.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not case:
        return jsonify({"success": False, "error": "CASE_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(case["company_id"])
    if guard:
        return guard

    evidence = db.execute("SELECT * FROM evidence WHERE case_id=?", (case_id,)).fetchall()
    decisions = db.execute("SELECT * FROM decisions WHERE case_id=?", (case_id,)).fetchall()
    company = db.execute(
        """SELECT company_id, name, sector, sector_other, city, stage,
                  employee_count, annual_revenue, main_goal, success_criteria,
                  website_url, social_media_url, business_reference_url
           FROM companies WHERE company_id=?""",
        (case["company_id"],)
    ).fetchone()

    # مهام هذه القضية — عبر القرارات المرتبطة بها
    decision_ids = [d["decision_id"] for d in decisions]
    tasks = []
    affected_assets = []
    p0_impact_reviews = []
    if decision_ids:
        placeholders = ",".join("?" * len(decision_ids))
        tasks = db.execute(
            f"SELECT * FROM tasks WHERE decision_id IN ({placeholders}) ORDER BY created_at ASC",
            decision_ids
        ).fetchall()

        # الأصول المتأثرة — مجمَّعة من decision_asset_impacts لكل قرارات هذه القضية
        affected_assets = db.execute(f"""
            SELECT dai.asset_id,
                   a.asset_name,
                   a.asset_type,
                   a.current_score,
                   a.fragility_score,
                   a.status   AS asset_status,
                   SUM(dai.score_impact) AS total_impact,
                   COUNT(dai.impact_id)  AS impact_count
            FROM decision_asset_impacts dai
            JOIN assets a ON dai.asset_id = a.asset_id
            WHERE dai.decision_id IN ({placeholders})
            GROUP BY dai.asset_id, a.asset_name, a.asset_type,
                     a.current_score, a.fragility_score, a.status
            ORDER BY total_impact DESC
        """, decision_ids).fetchall()
        p0_impact_reviews = db.execute(f"""
            SELECT * FROM p0_impact_reviews
            WHERE company_id=? AND case_id=? AND decision_id IN ({placeholders})
            ORDER BY reviewed_at DESC
        """, [case["company_id"], case_id, *decision_ids]).fetchall()

    # SOP docs — لا يوجد ربط مباشر بين methodology_docs والقضايا حالياً
    sop_docs = []  # قابل للتوسعة: أضف عمود case_id لـmethodology_docs لاحقاً

    review_context = _build_scan_report_context(case["company_id"], case_id=case_id) or {}
    scan = review_context.get("scan") or {}
    reference_knowledge = review_context.get("reference_knowledge") or _reference_knowledge_fallback()
    from sana_decision_room import decision_execution_loop
    from sana_company_memory import retrieve_memory
    loops = []
    for decision in decisions:
        try:
            loops.append(decision_execution_loop(
                db, case["company_id"], decision["decision_id"]
            ))
        except LookupError:
            pass
    return jsonify({
        "success": True,
        "data": {
            "case":            dict(case),
            "company":         dict(company) if company else None,
            "evidence":        [dict(e) for e in evidence],
            "decisions":       [dict(d) for d in decisions],
            "tasks":           [dict(t) for t in tasks],
            "p0_impact_reviews": [dict(r) for r in p0_impact_reviews],
            "decision_execution_loops": loops,
            "affected_assets": [
                _client_asset_view(asset) for asset in affected_assets
            ],
            "sop_docs":        sop_docs,
            "reports":         [],  # لا توجد جداول تقارير مرتبطة بالقضية حالياً
            "scan":             scan,
            "diagnostic_review": review_context.get("diagnostic_review"),
            "scan_journey":     review_context.get("scan_journey"),
            "knowledge_diagnostic": latest_diagnostic(db, case_id),
            "reference_knowledge": reference_knowledge,
            "company_memory": retrieve_memory(
                db, case["company_id"], case_id=case_id,
                problem=case["declared_problem"],
                include_history=False,
            ),
        }
    })


def _normalize_profile_url(value):
    """يقبل رابطًا عامًا بصيغة مفهومة ويضيف https:// عند غياب البروتوكول."""
    value = (value or "").strip()
    if not value:
        return None
    if len(value) > 500:
        raise ValueError("الرابط أطول من الحد المسموح.")
    candidate = value if "://" in value else f"https://{value}"
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("أدخل رابطًا صحيحًا يبدأ بـ http:// أو https://.")
    return candidate


@app.route("/api/companies/<company_id>/analysis-profile", methods=["PATCH"])
def update_company_analysis_profile(company_id):
    """يحفظ المصادر الرقمية التي تمثل نشاط الشركة لإدخالها في سياق التشخيص."""
    db = get_db()
    company = db.execute(
        "SELECT company_id FROM companies WHERE company_id=?", (company_id,)
    ).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404

    body = request.get_json(silent=True) or {}
    try:
        website_url = _normalize_profile_url(body.get("website_url"))
        social_media_url = _normalize_profile_url(body.get("social_media_url"))
        business_reference_url = _normalize_profile_url(body.get("business_reference_url"))
    except ValueError as exc:
        return jsonify({
            "success": False,
            "error": "INVALID_URL",
            "message": str(exc),
        }), 400

    db.execute(
        """UPDATE companies
           SET website_url=?, social_media_url=?, business_reference_url=?
           WHERE company_id=?""",
        (website_url, social_media_url, business_reference_url, company_id)
    )
    db.commit()
    return jsonify({
        "success": True,
        "data": {
            "website_url": website_url,
            "social_media_url": social_media_url,
            "business_reference_url": business_reference_url,
        }
    })

def _scan_journey_details(
    company_id,
    *,
    status=None,
    has_run=None,
    case_id=None,
    missing_evidence=None,
):
    """Public state contract shared by the live passport and executive report.

    The old passport and report endpoints remain aliases, but both now expose
    the same state, explanation, and one next action. Financial valuation is
    deliberately not part of this contract.
    """
    from sana_scan import normalize_scan_status

    db = get_db()
    if has_run is None or status is None:
        row = db.execute(
            """SELECT status, result FROM scan_runs
               WHERE company_id=?
               ORDER BY created_at DESC, scan_id DESC LIMIT 1""",
            (company_id,),
        ).fetchone()
        has_run = bool(row)
        snapshot = {}
        if row:
            try:
                snapshot = json.loads(row["result"]) or {}
            except (TypeError, json.JSONDecodeError):
                snapshot = {}
        status = normalize_scan_status(
            snapshot.get("status") or row["status"],
            has_run=has_run,
        )
        case_id = case_id or snapshot.get("case_id")
        missing_evidence = missing_evidence or snapshot.get("missing_evidence") or []
    else:
        status = normalize_scan_status(status, has_run=has_run)

    if not case_id:
        fallback_case = db.execute(
            """SELECT case_id FROM cases
               WHERE company_id=?
               ORDER BY opened_at DESC, case_id DESC LIMIT 1""",
            (company_id,),
        ).fetchone()
        case_id = fallback_case["case_id"] if fallback_case else None
    report_url = endpoint_path("scan_report_html", company_id=company_id)
    assessment_url = endpoint_path("assessment")
    case_url = (
        endpoint_path("case_workspace", case_id=case_id)
        if case_id else assessment_url
    )

    states = {
        "NOT_RUN": {
            "label": "لم يُشغّل بعد",
            "message": "لم تُجمع نتيجة Sana Scan بعد. ابدأ من تقييم الأصول ثم شغّل الفحص.",
            "action_label": "ابدأ تقييم الأصول",
            "action_url": assessment_url,
            "tone": "neutral",
        },
        "INCOMPLETE": {
            "label": "غير مكتمل — بوابة الأدلة مفتوحة",
            "message": "لا يمكن اعتماد اختناق أو درجة كاملة بعد؛ أكمل الأدلة الناقصة ثم أعد تشغيل الفحص.",
            "action_label": "أكمل الأدلة المطلوبة",
            "action_url": case_url,
            "tone": "warning",
        },
        "REVIEW_REQUIRED": {
            "label": "جاهز للمراجعة البشرية",
            "message": "نتيجة Scan موجودة وقابلة للتتبع، لكنها لا تصبح قرارًا تنفيذيًا قبل المراجعة البشرية.",
            "action_label": "راجع التقرير التنفيذي",
            "action_url": report_url,
            "tone": "review",
        },
        "COMPLETE": {
            "label": "اكتملت المراجعة — التقرير جاهز",
            "message": "اكتملت المراجعة البشرية لهذه النتيجة. أي معلومة ناقصة أو درجة غير مكتملة تبقى غير متاحة.",
            "action_label": "افتح التقرير التنفيذي",
            "action_url": report_url,
            "tone": "complete",
        },
    }[status]
    return {
        "status": status,
        **states,
        "missing_evidence": list(missing_evidence or []),
        "report_url": report_url,
        "assessment_url": assessment_url,
        "case_url": case_url,
    }


def _client_scan_result_redirect(company_id):
    """لا تعرض الجواز أو التقرير للعميل قبل وجود أول نتيجة Scan."""
    account = current_account()
    if (
        not account
        or is_admin_preview()
        or _admin_role() in SYSTEM_ADMIN_ROLES
    ):
        return None
    db = get_db()
    case = db.execute(
        """SELECT case_id FROM cases WHERE company_id=?
           ORDER BY opened_at DESC, case_id DESC LIMIT 1""",
        (company_id,),
    ).fetchone()
    if not case:
        return redirect(url_for("ceo_home"))
    scan = db.execute(
        """SELECT 1 FROM scan_runs
           WHERE company_id=? AND case_id=? LIMIT 1""",
        (company_id, case["case_id"]),
    ).fetchone()
    if scan:
        return None
    return redirect(url_for(
        "case_result", case_id=case["case_id"], scan="required",
    ))


def _client_confidence_label(score):
    """صياغة ثقة مفهومة للعميل، مع الحفاظ على الصفر كقيمة حقيقية."""
    if score is None:
        return "غير متاحة بعد"
    try:
        score = int(score)
    except (TypeError, ValueError):
        return "غير متاحة بعد"
    if score >= 80:
        return "مرتفعة"
    if score >= 50:
        return "متوسطة"
    return "محدودة"


def _client_asset_view(asset):
    """إضافة اسم أصل عربي للمخرجات العميلية دون تغيير بيانات الأصل الأصلية."""
    from sana_scan import ASSET_LABELS

    view = dict(asset)
    client_asset_name = ASSET_LABELS.get(view.get("asset_type"))
    if not client_asset_name:
        client_asset_name = view.get("client_asset_name") or "أصل غير مصنّف"
    view["asset_name"] = client_asset_name
    view["client_asset_name"] = client_asset_name
    return view


@app.route("/api/companies/<company_id>/passport")
def passport_summary(company_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    db = get_db()
    company = db.execute("SELECT * FROM companies WHERE company_id=?", (company_id,)).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404

    assets = db.execute("SELECT * FROM assets WHERE company_id=?", (company_id,)).fetchall()
    avg_score = round(sum(a["current_score"] for a in assets) / len(assets)) if assets else 0

    weakest = min(assets, key=lambda a: a["current_score"]) if assets else None
    strongest = max(assets, key=lambda a: a["current_score"]) if assets else None

    weakest_case = None
    if weakest:
        weakest_case = db.execute(
            "SELECT case_id FROM cases WHERE company_id=? AND related_asset_id=? ORDER BY opened_at DESC LIMIT 1",
            (company_id, weakest["asset_id"])
        ).fetchone()

    scan_context = _build_scan_report_context(company_id)
    journey = (scan_context or {}).get("scan_journey") or _scan_journey_details(company_id)
    scan = (scan_context or {}).get("scan") or {}
    scan_scores = list(scan.get("asset_scores") or [])
    scan_score_by_type = {item.get("asset_type"): item for item in scan_scores}
    complete_scores = [
        item for item in scan_scores
        if item.get("status") == "COMPLETE" and item.get("score") is not None
    ]
    score_ready = bool(
        scan_scores
        and len(complete_scores) == len(scan_scores)
        and journey["status"] in {"REVIEW_REQUIRED", "COMPLETE"}
    )
    operational_score = (
        round(sum(item["score"] for item in complete_scores) / len(complete_scores))
        if score_ready else None
    )
    evidence_count = db.execute(
        "SELECT COUNT(*) as cnt FROM evidence WHERE company_id=?", (company_id,)
    ).fetchone()["cnt"]
    score_missing_reason = (
        None if score_ready else
        "تظل درجة Sana Score التشغيلية N/A — Deferred حتى تكتمل محاور Scan وتُراجع بشريًا."
    )

    financial_value = financial_value_gate()
    client_scan = dict(scan)
    client_scan["asset_scores"] = [
        _client_asset_view(asset) for asset in scan_scores
    ]
    client_assets = [_client_asset_view(asset) for asset in assets]
    from sana_company_memory import retrieve_memory
    memory = retrieve_memory(db, company_id)
    return jsonify({
        "success": True,
        "data": {
            "company": dict(company),
            "company_memory": memory,
            "journey": journey,
            "scan": client_scan,
            "diagnostic_review": (scan_context or {}).get("diagnostic_review"),
            "reference_knowledge": (scan_context or {}).get("reference_knowledge")
                or _reference_knowledge_fallback(),
            "scan_status": journey["status"],
            "operational_score": operational_score,
            "score_progress": {
                "complete": len(complete_scores),
                "total": len(scan_scores),
            },
            "scan_asset_scores": client_scan["asset_scores"],
            # Legacy keys remain for clients that still deserialize them, but
            # financial valuation is intentionally unavailable.
            "quality_score": operational_score,
            "current_value": None,
            "potential_value": None,
            "value_gap": None,
            "assets": client_assets,
            "weakest_asset": _client_asset_view(weakest) if weakest else None,
            "strongest_asset": _client_asset_view(strongest) if strongest else None,
            "weakest_asset_case_id": weakest_case["case_id"] if weakest_case else None,
            "score_ready": score_ready,
            "score_missing_reason": score_missing_reason,
            "evidence_count": evidence_count,
        },
        "meta": {
            "financial_value_status": financial_value["status"],
            "financial_value_note": (
                "التقييم المالي غير معروض — لا توجد منهجية وأدلة معتمدة تسمح بتحويل "
                "Sana Score إلى قيمة نقدية."
            ),
            "financial_value_policy": financial_value,
            "disclaimer": "Sana Score هنا مؤشر تشغيلي مشروط، وليس تقييمًا ماليًا أو وعدًا بالنتيجة.",
        }
    })


# ------------------------------------------------------------------
# SDS-002 — Progressive Discovery MVP
# مكتبة أسئلة ثابتة: سؤال واحد لكل أصل (5 أصول × سؤال)
# لا جدول، لا عمود جديد — dict بسيط بالكود
# ------------------------------------------------------------------
SDS_QUESTIONS = {
    "Knowledge": {
        "text": "هل إجراءات العمل الأساسية عندكم موثقة؟",
        "options": ["نعم", "جزئيًا", "لا"],
        "suggestions": {
            "لا":       "💡 يُنصح بالبدء بتوثيق إجراءات خدمة العملاء — حتى صفحة واحدة تحدث فرقًا.",
            "جزئيًا":  "💡 وسّع التوثيق ليشمل الإجراءات التي تعتمد على شخص واحد.",
        },
    },
    "Brand": {
        "text": "هل يقدر أغلب عملائك يوصفون شركتك بجملة وحدة؟",
        "options": ["نعم", "لا", "ما أعرف"],
        "suggestions": {
            "لا":       "💡 يُنصح بصياغة جملة تعريفية واحدة واضحة وتوحيدها عبر كل قنواتك.",
            "ما أعرف": "💡 اسأل 3 عملاء فعليين: كيف يصفون شركتك؟ الإجابة ستفاجئك.",
        },
    },
    "Operations": {
        "text": "هل يوجد شخص يقدر يأدي عملك لو غبت أسبوعين؟",
        "options": ["نعم", "جزئيًا", "لا"],
        "suggestions": {
            "لا":       "💡 يُنصح بتدريب شخص واحد على الأقل على المهام الأساسية بشكل موثق.",
            "جزئيًا":  "💡 وسّع نطاق تفويض القرارات اليومية لتشمل أكثر من شخص.",
        },
    },
    "Data": {
        "text": "هل عندكم لوحة متابعة أسبوعية للمبيعات أو الأداء؟",
        "options": ["نعم", "قيد الإعداد", "لا"],
        "suggestions": {
            "لا":           "💡 يُنصح بإنشاء تقرير أسبوعي بسيط يتضمن 3 أرقام أساسية على الأقل.",
            "قيد الإعداد": "💡 حدد موعدًا لتفعيل لوحة المتابعة وأبلغ فريقك به.",
        },
    },
    "Independence": {
        "text": "كم قرار يحتاج موافقتك الشخصية تقريبًا كل يوم؟",
        "options": ["قليل (0-2)", "متوسط (3-6)", "كثير (+7)"],
        "suggestions": {
            "كثير (+7)":    "💡 يُنصح بمراجعة القرارات اليومية وتحديد أيها يمكن تفويضه فورًا.",
            "متوسط (3-6)": "💡 حدد قرارًا واحدًا يمكن تفويضه للفريق هذا الأسبوع.",
        },
    },
}


def _next_sds_question(db, company_id, case_id=None):
    """السؤال التالي يغطي فجوة حقيقية فقط، ويتوقف عندما يصبح القرار ممكنًا."""
    if case_id:
        case = db.execute(
            "SELECT case_id,related_asset_id FROM cases WHERE case_id=? AND company_id=?",
            (case_id, company_id),
        ).fetchone()
        if not case:
            return None
    else:
        case = None
    assets = db.execute(
        "SELECT * FROM assets WHERE company_id=?", (company_id,)
    ).fetchall()
    if not assets:
        return None

    from sana_scan import latest_scan
    scan = latest_scan(db, case_id) if case_id else None
    if scan and (
        scan.get("decision_readiness") in {"READY", "CONDITIONAL"}
        or scan.get("status") in {"REVIEW_REQUIRED", "COMPLETE"}
    ):
        return None
    score_by_type = {
        item.get("asset_type"): item
        for item in (scan or {}).get("asset_scores") or []
    }
    conflict_ids = {
        str(source_id)
        for conflict in (scan or {}).get("open_conflicts") or []
        for source_id in conflict.get("source_ids") or []
    }

    evidence_per_asset = {}
    for a in assets:
        client_asset = _client_asset_view(a)
        question_id = f"SDS-002:{a['asset_type']}"
        rows = db.execute(
            """SELECT evidence_id,date_collected,verification_status
               FROM evidence
               WHERE company_id=? AND asset_id=? AND source_ref=?
               ORDER BY date_collected DESC,evidence_id DESC""",
            (company_id, a["asset_id"], question_id),
        ).fetchall()
        memory = None
        if case_id:
            memory = db.execute(
                """SELECT v.observed_at,v.verification_status
                   FROM company_memory_items i
                   JOIN company_memory_versions v
                     ON v.version_id=i.current_version_id
                   WHERE i.company_id=? AND i.memory_key=?""",
                (company_id, f"adaptive:{case_id}:{question_id}"),
            ).fetchone()

        def _fresh(value):
            if not value:
                return False
            try:
                observed = value if isinstance(value, date) else date.fromisoformat(
                    str(value).split("T", 1)[0].split(" ", 1)[0]
                )
            except (TypeError, ValueError):
                return False
            return observed >= date.today() - timedelta(days=365)

        answered = any(
            row["verification_status"] != "CONTRADICTED"
            and str(row["evidence_id"]) not in conflict_ids
            and _fresh(row["date_collected"])
            for row in rows
        ) or bool(
            memory
            and memory["verification_status"] != "CONTRADICTED"
            and _fresh(memory["observed_at"])
        )
        score = score_by_type.get(a["asset_type"]) or {}
        completeness = (score.get("information_completeness") or {}).get("score", 0)
        evidence_confidence = (score.get("evidence_confidence") or {}).get("score", 0)
        has_gap = (
            not scan
            or score.get("status") == "INCOMPLETE"
            or bool(score.get("missing_evidence"))
        )
        evidence_per_asset[a["asset_type"]] = {
            "count":      len(rows),
            "answered":   answered,
            "has_gap":    has_gap,
            "completeness": completeness,
            "evidence_confidence": evidence_confidence,
            "asset_id":   a["asset_id"],
            "asset_name": client_asset["asset_name"],
            "client_asset_name": client_asset["client_asset_name"],
        }

    related_asset_id = case["related_asset_id"] if case else None
    valid = {
        key: value for key, value in evidence_per_asset.items()
        if (
            key in SDS_QUESTIONS
            and value["has_gap"]
            and not value["answered"]
            and (
                value["count"] > 0
                or value["asset_id"] == related_asset_id
            )
        )
    }
    if not valid:
        return None
    chosen = min(
        valid,
        key=lambda key: (
            0 if valid[key]["asset_id"] == related_asset_id else 1,
            valid[key]["completeness"],
            valid[key]["evidence_confidence"],
            key,
        ),
    )
    question = SDS_QUESTIONS[chosen]
    if not case_id:
        case = db.execute(
            """SELECT case_id FROM cases
               WHERE company_id=?
               ORDER BY (related_asset_id=?) DESC, opened_at DESC, case_id DESC
               LIMIT 1""",
            (company_id, valid[chosen]["asset_id"]),
        ).fetchone()
        case_id = case["case_id"] if case else None
    if not case_id:
        return None
    return {
        "question_id": f"SDS-002:{chosen}",
        "case_id": case_id,
        "asset_type": chosen,
        "asset_id": valid[chosen]["asset_id"],
        "asset_name": valid[chosen]["asset_name"],
        "client_asset_name": valid[chosen]["client_asset_name"],
        "question": question["text"],
        "options": question["options"],
        "suggestions": question["suggestions"],
        "gap_reason": (
            ((score_by_type.get(chosen) or {}).get("missing_evidence") or [None])[0]
        ),
        "progress": {
            "knowledge_coverage": round(
                sum(item["completeness"] for item in evidence_per_asset.values())
                / max(1, len(evidence_per_asset))
            ),
            "evidence_quality": round(
                sum(item["evidence_confidence"] for item in evidence_per_asset.values())
                / max(1, len(evidence_per_asset))
            ),
            "open_conflicts": len((scan or {}).get("open_conflicts") or []),
            "decision_readiness": (scan or {}).get("decision_readiness") or "NOT_READY",
        },
        "evidence_per_asset": {
            key: value["count"] for key, value in evidence_per_asset.items()
        },
    }


@app.route("/api/companies/<company_id>/sds-question")
def sds_question(company_id):
    """SDS-002: سؤال واحد غير مجاب، مرتبط بقضية قائمة دون إنشاء قضية."""
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    data = _next_sds_question(
        get_db(), company_id, request.args.get("case_id") or None
    )
    return jsonify({
        "success": True,
        "data": data,
        "meta": {
            "complete": data is None,
            "message": (
                "التشخيص أصبح كافيًا لاتخاذ القرار."
                if data is None else "يوجد سؤال واحد مرتبط بفجوة ستؤثر في القرار."
            ),
            "progress_basis": (
                "تغطية المعرفة، جودة الأدلة، التعارضات المفتوحة، وجاهزية القرار"
            ),
        },
    })


# ------------------------------------------------------------------
# API — Decisions
# ------------------------------------------------------------------

DECISION_TEMPLATES = {
    "Knowledge": {
        "title": "توثيق أول SOP لعملية أساسية",
        "recommended_action": "اختر عملية واحدة متكررة يعتمد إنجازها على شخص محدد، وابدأ بتوثيقها في ملف SOP خلال 7 أيام.",
        "reason_tpl": "أصل المعرفة هو من أضعف الأصول حاليًا ({score}/100) — غياب التوثيق يجعل الشركة عالية الاعتماد على أفراد بعينهم.",
    },
    "Operations": {
        "title": "ضبط معيار قبول واحد للتنفيذ",
        "recommended_action": "حدّد معيارًا مكتوبًا واضحًا لقبول جودة تنفيذ أهم عملية تشغيلية، وشاركه مع من ينفذها.",
        "reason_tpl": "أصل التشغيل ({score}/100) يحتاج معيارًا موثقًا لتقليل التفاوت في جودة التنفيذ.",
    },
    "Brand": {
        "title": "توضيح رسالة البراند في أول نقطة تواصل",
        "recommended_action": "راجع أول رسالة يراها عميل جديد (الموقع/الحساب) وأعد صياغتها في جملة واحدة واضحة عن القيمة المقدَّمة.",
        "reason_tpl": "أصل البراند ({score}/100) يحتاج وضوحًا أكبر ليفهم العميل الجديد العرض بسرعة.",
    },
    "Data": {
        "title": "تحديد 3 مؤشرات أداء أسبوعية",
        "recommended_action": "اختر 3 مؤشرات أداء (KPIs) أساسية وابدأ بمتابعتها ومراجعتها أسبوعيًا في قرار واحد ثابت.",
        "reason_tpl": "أصل البيانات ({score}/100) يحتاج مؤشرات محددة تُستخدم فعليًا في القرارات، لا بيانات مبعثرة.",
    },
    "Independence": {
        "title": "تفويض قرار يومي واحد لشخص آخر",
        "recommended_action": "حدّد قرارًا يوميًا واحدًا تتخذه بنفسك الآن، وفوّضه لشخص آخر مع معيار واضح لاتخاذه.",
        "reason_tpl": "أصل الاستقلال ({score}/100) يشير إلى اعتماد مباشر على شخص واحد في القرارات اليومية.",
    },
}


@app.route("/api/companies/<company_id>/decisions/suggest", methods=["POST"])
def suggest_decisions(company_id):
    db = get_db()
    import uuid

    company = db.execute("SELECT * FROM companies WHERE company_id=?", (company_id,)).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404

    weakest_assets = db.execute(
        "SELECT * FROM assets WHERE company_id=? ORDER BY current_score ASC LIMIT 3",
        (company_id,)
    ).fetchall()

    suggestions = []
    for asset in weakest_assets:
        template = DECISION_TEMPLATES.get(asset["asset_type"])
        if not template:
            continue

        # لا تُكرَّر مقترحات لأصل له قرار "مقترح" موجود مسبقًا (سواء كان أصل رئيسي في decisions.asset_id
        # أو مرتبط عبر decision_asset_impacts)
        existing = db.execute(
            """SELECT d.* FROM decisions d WHERE d.company_id=? AND d.status='مقترح' AND (
                   d.asset_id=?
                   OR d.decision_id IN (
                       SELECT decision_id FROM decision_asset_impacts WHERE asset_id=?
                   )
               ) LIMIT 1""",
            (company_id, asset["asset_id"], asset["asset_id"])
        ).fetchone()

        if existing:
            suggestions.append({
                "asset_id": asset["asset_id"],
                "asset_name": asset["asset_name"],
                "current_score": asset["current_score"],
                "decision_id": existing["decision_id"],
                "title": existing["title"],
                "recommended_action": existing["recommended_action"],
                "reason": existing["reason"],
                "confidence_score": existing["confidence_score"],
                "status": existing["status"],
                "created": False
            })
            continue

        related_case = db.execute(
            "SELECT case_id FROM cases WHERE company_id=? AND related_asset_id=? ORDER BY opened_at DESC LIMIT 1",
            (company_id, asset["asset_id"])
        ).fetchone()

        decision_id = "D" + uuid.uuid4().hex[:6].upper()
        reason = template["reason_tpl"].format(score=asset["current_score"])
        db.execute("""INSERT INTO decisions
            (decision_id, company_id, case_id, asset_id, title, recommended_action, reason,
             confidence_score, expected_impact, status)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (decision_id, company_id, related_case["case_id"] if related_case else None,
             asset["asset_id"], template["title"], template["recommended_action"], reason,
             50, f'رفع {asset["asset_name"]} تدريجيًا خلال أسابيع قادمة', "مقترح"))

        impact_id = "IMP" + uuid.uuid4().hex[:6].upper()
        db.execute("""INSERT INTO decision_asset_impacts
            (impact_id, decision_id, asset_id, score_impact, is_primary)
            VALUES (?,?,?,?,?)""",
            (impact_id, decision_id, asset["asset_id"], 5, 1))

        suggestions.append({
            "asset_id": asset["asset_id"],
            "asset_name": asset["asset_name"],
            "current_score": asset["current_score"],
            "decision_id": decision_id,
            "title": template["title"],
            "recommended_action": template["recommended_action"],
            "reason": reason,
            "confidence_score": 50,
            "status": "مقترح",
            "created": True
        })

    db.commit()
    return jsonify({"success": True, "data": {"suggestions": suggestions}})


@app.route("/api/cases/<case_id>/p0-decision", methods=["POST"])
def create_p0_case_decision(case_id):
    """ينشئ قرار العميل الوحيد من آخر Scan جاهز ومصادره المؤهلة."""
    from sana_scan import latest_scan
    import uuid

    db = get_db()
    # يقفل صف القضية حتى يصبح فحص "قرار P0 موجود" ثم الإدراج وحدة ذرية.
    # الطلب المتزامن الثاني ينتظر commit الأول، ثم يعيد القرار الموجود.
    case = db.execute(
        "SELECT * FROM cases WHERE case_id=? FOR UPDATE", (case_id,)
    ).fetchone()
    if not case:
        return jsonify({"success": False, "error": "CASE_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(case["company_id"])
    if guard:
        return guard

    scan = latest_scan(db, case_id)
    existing = db.execute(
        """SELECT * FROM decisions WHERE company_id=? AND case_id=?
           AND phase_label='P0' AND scan_id=?
           ORDER BY created_at DESC LIMIT 1""",
        (case["company_id"], case_id, (scan or {}).get("scan_id")),
    ).fetchone()
    if existing:
        if (
            scan
            and existing["scan_id"] == scan.get("scan_id")
            and scan.get("status") == "REVIEW_REQUIRED"
        ):
            scan["status"] = "COMPLETE"
            scan["human_review_required"] = False
            scan["human_review"] = {
                "status": "Completed",
                "decision_id": existing["decision_id"],
                "reviewed_at": datetime.utcnow().isoformat() + "Z",
            }
            db.execute(
                "UPDATE scan_runs SET status='COMPLETE', result=? WHERE scan_id=?",
                (json.dumps(scan, ensure_ascii=False), scan["scan_id"]),
            )
            db.commit()
        return jsonify({"success": True, "data": dict(existing), "meta": {"created": False}})

    proposed = (scan or {}).get("proposed_decision")
    bottleneck = (scan or {}).get("bottleneck")
    if not scan or scan.get("status") not in {"REVIEW_REQUIRED", "COMPLETE"} or not proposed or not bottleneck:
        return jsonify({
            "success": False,
            "error": "P0_DECISION_DEFERRED",
            "message": "لا يمكن إنشاء القرار قبل اكتمال دليل مؤهل ومراجعة التحليل.",
        }), 422

    source_ids = list(dict.fromkeys(
        str(source_id) for source_id in (proposed.get("source_ids") or [])
    ))
    eligible_scan_sources = {
        str(item.get("source_id"))
        for item in (scan.get("classified_inputs") or [])
        if item.get("classification") in {"Fact", "Evidence"}
    }
    evidence_ids = []
    for source_id in source_ids:
        if source_id not in eligible_scan_sources:
            continue
        evidence = db.execute(
            """SELECT evidence_id FROM evidence
               WHERE evidence_id=? AND company_id=? AND case_id=?""",
            (source_id, case["company_id"], case_id),
        ).fetchone()
        if evidence:
            evidence_ids.append(source_id)
    if not evidence_ids:
        return jsonify({
            "success": False,
            "error": "P0_DECISION_EVIDENCE_REQUIRED",
            "message": "بقي القرار مؤجلًا لأن التحليل لا يحتوي دليلًا مؤهلًا قابلًا للتتبع.",
        }), 422

    asset = None
    if bottleneck.get("asset_type"):
        asset = db.execute(
            "SELECT asset_id FROM assets WHERE company_id=? AND asset_type=? LIMIT 1",
            (case["company_id"], bottleneck["asset_type"]),
        ).fetchone()
    decision_id = "D" + uuid.uuid4().hex[:10].upper()
    db.execute(
        """INSERT INTO decisions
           (decision_id, company_id, case_id, asset_id, title, recommended_action,
            reason, confidence_score, expected_impact, status, phase_label,
            structured_data, scan_id, evidence_ids)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            decision_id,
            case["company_id"],
            case_id,
            asset["asset_id"] if asset else None,
            proposed.get("title") or "قرار مقترح للمراجعة",
            proposed.get("statement"),
            bottleneck.get("statement"),
            ((scan.get("decision_confidence") or {}).get("score")),
            (scan.get("opportunity") or {}).get("statement"),
            "مقترح",
            "P0",
            json.dumps({
                "bottleneck": bottleneck,
                "proposed_decision": proposed,
                "decision_readiness": scan.get("decision_readiness"),
                "decision_confidence": scan.get("decision_confidence"),
            }, ensure_ascii=False),
            scan["scan_id"],
            json.dumps(evidence_ids, ensure_ascii=False),
        ),
    )
    scan["status"] = "COMPLETE"
    scan["human_review_required"] = False
    scan["human_review"] = {
        "status": "Completed",
        "decision_id": decision_id,
        "reviewed_at": datetime.utcnow().isoformat() + "Z",
    }
    db.execute(
        "UPDATE scan_runs SET status='COMPLETE', result=? WHERE scan_id=?",
        (json.dumps(scan, ensure_ascii=False), scan["scan_id"]),
    )
    db.commit()
    decision = db.execute(
        "SELECT * FROM decisions WHERE decision_id=?", (decision_id,)
    ).fetchone()
    return jsonify({"success": True, "data": dict(decision), "meta": {"created": True}}), 201


@app.route("/api/companies/<company_id>/passport/report-text")
def passport_report_text(company_id):
    context = _build_passport_context(company_id)
    if not context:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    scan_redirect = _client_scan_result_redirect(company_id)
    if scan_redirect:
        return scan_redirect
    company = context["company"]
    review = context["diagnostic_review"]
    journey = review["journey"]
    problem = review.get("problem") or {}
    hypothesis = review.get("hypothesis")
    inference = review.get("inference")
    opportunity = review.get("opportunity")
    gate = review["evidence_gate"]
    confidence = review.get("client_confidence") or {}
    cycle = review.get("client_evidence_request_cycle") or {}

    lines = [
        "══════════════════════════════════════════",
        f"مراجعة Sana Scan الموحدة — {company['name']}",
        "══════════════════════════════════════════",
        "",
        f"تاريخ التصدير: {context['export_date']}",
        "",
        "❶  حالة Sana Scan",
        f"   الحالة: {journey['label']}",
        f"   التوضيح: {journey['message']}",
        f"   الإجراء التالي: {journey['action_label']}",
        (
            f"   Sana Score التشغيلي: {context['operational_score']}/100"
            if context["operational_score"] is not None
            else "   Sana Score التشغيلي: غير متاح حتى تكتمل المحاور وتُراجع بشريًا."
        ),
        "   التقييم المالي: غير معروض — لا توجد معلومات مالية معتمدة.",
        "",
        "نهاية دورة الأدلة: "
        f"{cycle.get('outcome_label') or 'لم تُحسم بعد'}",
        f"   الطلبات الحرجة: {cycle.get('critical_request_count', 0)}",
        (
            "   الطلبات المغلقة: "
            f"{cycle.get('closed_request_count', 0)} من "
            f"{cycle.get('critical_request_count', 0)}"
        ),
        f"   الطلبات المتتبعة: {cycle.get('fingerprint_count', 0)}",
        "",
        "❷  الثقة عبر مراحل القرار",
    ]
    for stage in confidence.get("stages") or []:
        stage_view = stage["view"]
        score = (
            f" · {stage_view['score']}٪"
            if stage_view.get("score") is not None
            else ""
        )
        lines.extend([
            f"   {stage['title']}: {stage_view['label']}{score}",
            f"      {stage_view['message']}",
        ])
    lines.extend([
        "",
        "❸  المشكلة والسؤال الحقيقي",
        f"   المشكلة: {problem.get('statement') or 'غير محددة بعد'}",
        f"   السؤال الحقيقي: {problem.get('real_question') or 'يُحدد بعد اكتمال جمع المعلومات'}",
        "",
        "❹  التشخيص والفرصة",
    ])
    lines.append(f"   الفرضية: {(hypothesis or {}).get('statement') or 'لا توجد فرضية بعد'}")
    lines.append(f"   الاستنتاج: {(inference or {}).get('statement') or 'غير متاح حتى يصل دليل مستقل'}")
    lines.append(f"   الفرصة: {(opportunity or {}).get('statement') or 'غير متاحة حتى تكتمل الأدلة'}")
    lines.extend(["", "❺  المعلومات المطلوبة قبل التنفيذ"])
    if gate["missing_evidence"]:
        lines.extend(
            f"   • {item}"
            for item in context.get("scan_client_missing_evidence") or []
        )
    else:
        lines.append("   لا توجد بنود ناقصة في النتيجة الحالية؛ تبقى المراجعة البشرية مطلوبة.")
    lines.extend(["", "❻  المعلومات المستخدمة"])
    if review["client_evidence"]:
        for source in review["client_evidence"]:
            lines.append(
                f"   {source['client_classification']} · "
                f"{source['client_source_label']} · "
                f"{source.get('source_date') or 'تاريخ غير متاح'}"
            )
            confidence_text = (
                f"{source['confidence']}٪ ({source['client_confidence_label']})"
                if source.get("confidence") is not None
                else "غير متاحة بعد"
            )
            lines.append(
                f"      {source['statement']} · درجة الثقة: {confidence_text}"
            )
    else:
        lines.append("   لا توجد معلومات مرتبطة بالنتيجة الحالية.")
    lines.extend(["", "❼  معلومات عامة مرتبطة"])
    references = review["reference_knowledge"].get("references") or []
    if references:
        for ref in references:
            lines.append(f"   • {ref.get('title')}")
            lines.append(
                f"      سبب المطابقة: {' · '.join(ref.get('match_reasons') or []) or 'مرجع عام'}"
            )
            lines.append(
                f"      المعلومات المطلوبة: {ref.get('required_client_evidence') or 'معلومة خاصة بالشركة'}"
            )
    else:
        lines.append(f"   {review['reference_knowledge'].get('knowledge_gap')}")

    lines.append("══════════════════════════════════════════")
    lines.append("أُنشئت بواسطة سنع — نتيجة قابلة للتتبع وليست تقييمًا ماليًا.")
    lines.append("══════════════════════════════════════════")

    return jsonify({"success": True, "data": {"report_text": "\n".join(lines)}})

def _reference_knowledge_fallback(message=None):
    return {
        "reference_only": True,
        "does_not_affect_scan": True,
        "context": {},
        "references": [],
        "knowledge_gap": message or (
            "المعرفة المرجعية غير متاحة الآن. تبقى نتيجة Scan قابلة للمراجعة "
            "من أدلة العميل وحدها، ولا يتغير القرار أو الدرجة."
        ),
        "conflicts": [],
        "human_review_required": True,
        "available": False,
    }
def _build_scan_report_context(company_id, case_id=None):
    """يبني حزمة Sana Scan التنفيذية من آخر فحص وقرارات الشركة.

    التقرير لا يملأ الخانات الناقصة بتخمينات. إذا لم يوجد Scan أو لم يعتمد
    المدير Owner/Deadline/KPI، يظهر ذلك صراحة في المخرج حتى تبقى جلسة النتائج
    قابلة للمراجعة ولا تتحول التوصية إلى وعد غير مسنود.
    """
    db = get_db()
    company = db.execute(
        "SELECT * FROM companies WHERE company_id=?", (company_id,)
    ).fetchone()
    if not company:
        return None

    if case_id:
        latest_scan_row = db.execute(
            """SELECT scan_id, status, result, methodology_version, created_at
               FROM scan_runs WHERE company_id=? AND case_id=?
               ORDER BY created_at DESC, scan_id DESC LIMIT 1""",
            (company_id, case_id),
        ).fetchone()
    else:
        latest_scan_row = db.execute(
            """SELECT scan_id, status, result, methodology_version, created_at
               FROM scan_runs WHERE company_id=?
               ORDER BY created_at DESC, scan_id DESC LIMIT 1""",
            (company_id,),
        ).fetchone()
    scan = {}
    if latest_scan_row:
        try:
            scan = json.loads(latest_scan_row["result"]) or {}
        except (TypeError, json.JSONDecodeError):
            scan = {}
        scan = _redact_deferred_financial_values(scan)
        scan["scan_id"] = latest_scan_row["scan_id"]
        scan["created_at"] = latest_scan_row["created_at"]
        scan["methodology_version"] = (
            scan.get("methodology_version") or latest_scan_row["methodology_version"]
        )
        scan["status"] = scan.get("status") or latest_scan_row["status"]

    latest_case = None
    canonical_case_id = scan.get("case_id") or (case_id if not latest_scan_row else None)
    if latest_scan_row:
        problem_snapshot = scan.get("diagnostic_problem") or {}
        problem_source = next(
            (
                item for item in (scan.get("classified_inputs") or [])
                if item.get("source_id") == problem_snapshot.get("source_id")
            ),
            {},
        )
        latest_case = {
            "case_id": canonical_case_id,
            "case_title": problem_snapshot.get("title")
                or problem_source.get("case_title")
                or problem_source.get("statement"),
            "declared_problem": problem_snapshot.get("statement")
                or problem_source.get("statement"),
            "real_question": problem_snapshot.get("real_question"),
            "case_status": None,
            "case_type": None,
            "confidence_score": problem_source.get("confidence"),
            "opened_at": problem_snapshot.get("source_date")
                or problem_source.get("source_date"),
        }
    elif canonical_case_id:
        latest_case = db.execute(
            """SELECT case_id, case_title, declared_problem, real_question,
                      case_status, case_type, confidence_score, opened_at
               FROM cases WHERE company_id=? AND case_id=?""",
            (company_id, canonical_case_id),
        ).fetchone()
    if not latest_case and not latest_scan_row and not case_id:
        latest_case = db.execute(
            """SELECT case_id, case_title, declared_problem, real_question,
                      case_status, case_type, confidence_score, opened_at
               FROM cases WHERE company_id=?
               ORDER BY opened_at DESC, case_id DESC LIMIT 1""",
            (company_id,),
        ).fetchone()

    sources = list(scan.get("classified_inputs") or [])
    source_by_id = {str(item.get("source_id")): dict(item) for item in sources}
    citations = []
    for source in sources:
        citations.append({
            "source_id": source.get("source_id"),
            "classification": source.get("classification") or "Evidence",
            "statement": source.get("statement") or "مصدر دون وصف",
            "source_ref": source.get("source_ref") or source.get("source_type") or "غير محدد",
            "source_type": source.get("source_type") or "غير محدد",
            "source_date": source.get("source_date"),
            "asset_id": source.get("asset_id"),
            "confidence": source.get("confidence"),
            "information_type": source.get("information_type") or "Narrative",
            "verification_status": source.get("verification_status") or "UNVERIFIED",
            "source_category": source.get("source_category") or "UNKNOWN",
            "period_start": source.get("period_start"),
            "period_end": source.get("period_end"),
            "unit": source.get("unit"),
            "client_classification": _client_source_classification(
                source.get("classification")
            ),
            "client_source_label": _client_source_label(source.get("source_type")),
            "client_confidence_label": _client_confidence_label(
                source.get("confidence")
            ),
        })

    findings = [dict(item) for item in (scan.get("findings") or [])]
    hypotheses = [
        item for item in findings if item.get("classification") == "Hypothesis"
    ]
    inferences = [
        item for item in findings if item.get("classification") == "Inference"
    ]
    bottleneck = scan.get("bottleneck") or (inferences[0] if inferences else None)
    if not bottleneck and hypotheses:
        bottleneck = hypotheses[0]
    opportunity = scan.get("opportunity")
    proposed_decision = scan.get("proposed_decision")

    from sana_scan import normalize_scan_status
    status = normalize_scan_status(
        scan.get("status"), has_run=bool(latest_scan_row)
    )
    status_labels = {
        "REVIEW_REQUIRED": "جاهز للمراجعة البشرية",
        "INCOMPLETE": "غير مكتمل — بوابة الأدلة مفتوحة",
        "NOT_RUN": "لم يُشغّل Sana Scan بعد",
        "COMPLETE": "مكتمل — التقرير جاهز",
    }
    status_label = status_labels.get(status, status)
    scan_journey = _scan_journey_details(
        company_id,
        status=status,
        has_run=bool(latest_scan_row),
        case_id=scan.get("case_id") or canonical_case_id,
        missing_evidence=scan.get("missing_evidence") or [],
    )
    if scan.get("journey_outcome") == "UNKNOWN":
        scan_journey.update({
            "label": "المعلومة غير متاحة الآن",
            "message": "سجّلنا أن المعلومة غير متاحة الآن؛ لن نعيد طلبها في هذه الدورة.",
            "action_label": "راجع ما نعرفه",
            "tone": "neutral",
            "outcome": "UNKNOWN",
        })
    else:
        scan_journey["outcome"] = scan.get("journey_outcome")

    evidence_citations = [
        item for item in citations
        if item["classification"] in {"Fact", "Evidence"}
    ]

    def _initiative_sources(source_ids=None):
        selected = []
        for source_id in source_ids or []:
            item = source_by_id.get(str(source_id))
            if item:
                selected.append(item)
        return [{
            "source_id": item.get("source_id"),
            "label": item.get("source_ref") or item.get("source_type") or "مصدر",
            "statement": item.get("statement") or "مصدر دون وصف",
            "classification": item.get("classification") or "Evidence",
        } for item in selected]

    def _number_pair(text):
        match = re.search(r"من\s+([0-9٠-٩]+)\s+إلى\s+([0-9٠-٩]+)", str(text or ""))
        return (match.group(1), match.group(2)) if match else (None, None)

    def _phase(value):
        text = str(value or "")
        if not text:
            return None
        if "31" in text or "60" in text or "بناء" in text:
            return "31–60 يومًا"
        if "61" in text or "90" in text or "توسع" in text:
            return "61–90 يومًا"
        if "0" in text or "30" in text or "إصلاح" in text:
            return "0–30 يومًا"
        return None

    decisions = []
    if scan.get("case_id") and scan.get("scan_id"):
        decisions = [
            dict(row) for row in db.execute(
                """SELECT * FROM decisions WHERE company_id=? AND case_id=?
                   AND scan_id=? ORDER BY created_at DESC, decision_id DESC""",
                (company_id, scan["case_id"], scan["scan_id"]),
            ).fetchall()
        ]
    initiatives = []
    unplanned_decisions = []
    for decision in decisions:
        task = db.execute(
            """SELECT t.*, u.name AS owner_display
               FROM tasks t LEFT JOIN users u
                 ON u.user_id=t.owner_user_id AND u.company_id=t.company_id
               WHERE t.company_id=? AND t.decision_id=?
               ORDER BY t.created_at DESC, t.task_id DESC LIMIT 1""",
            (company_id, decision["decision_id"]),
        ).fetchone()
        task = dict(task) if task else {}
        impact_review = {}
        if task:
            row = db.execute(
                """SELECT * FROM p0_impact_reviews
                   WHERE company_id=? AND decision_id=? AND task_id=?""",
                (company_id, decision["decision_id"], task["task_id"]),
            ).fetchone()
            if row:
                impact_review = dict(row)
                impact_review.update(status="MEASURED", status_label="تم القياس")
        if not impact_review:
            impact_review = None
        impact = decision.get("expected_impact") or ""
        baseline, target = _number_pair(impact)
        phase = _phase(decision.get("phase_label"))
        try:
            approved_source_ids = json.loads(decision.get("evidence_ids") or "[]")
        except (TypeError, json.JSONDecodeError):
            approved_source_ids = []
        source_ids = [
            source_id for source_id in approved_source_ids
            if (
                decision.get("scan_id") == scan.get("scan_id")
                and str(source_id) in source_by_id
                and source_by_id[str(source_id)].get("classification") in {"Fact", "Evidence"}
            )
        ]
        linked_evidence = _initiative_sources(source_ids=source_ids)
        if not phase:
            unplanned_decisions.append({
                "decision_id": decision.get("decision_id"),
                "title": decision.get("title") or "قرار دون عنوان",
                "reason": "لم تُحدد له مرحلة 0–30 أو 31–60 أو 61–90 صراحة.",
            })
            continue
        initiatives.append({
            "phase": phase,
            "decision_id": decision.get("decision_id"),
            "decision_title": decision.get("title") or "قرار دون عنوان",
            "task": (
                task.get("title")
                or decision.get("recommended_action")
                or "لم تُحدد مهمة تنفيذية بعد"
            ),
            "owner": (
                decision.get("owner_name")
                or task.get("owner_display")
                or task.get("owner_user_id")
                or "غير محدد — يلزم الاعتماد"
            ),
            "due_date": (
                decision.get("due_date")
                or task.get("due_date")
                or "غير محدد — يلزم الاعتماد"
            ),
            "kpi": (
                decision.get("success_metric")
                or "غير محدد — يلزم اعتماد مقياس نجاح"
            ),
            "baseline": baseline or "غير موثق",
            "target": target or "غير موثق",
            "impact_review": impact_review,
            "evidence": linked_evidence,
            "impact": impact or "لم يُسجل أثر متوقع بعد",
            "status": decision.get("status") or "مقترح",
            "completeness": all([
                decision.get("owner_name") or task.get("owner_display") or task.get("owner_user_id"),
                decision.get("due_date") or task.get("due_date"),
                decision.get("success_metric"),
                linked_evidence,
                impact,
            ]),
        })

    phases = []
    phase_titles = [
        ("0–30 يومًا", "إصلاح الاختناق الحرج"),
        ("31–60 يومًا", "بناء النظام والأصل"),
        ("61–90 يومًا", "التوسع فيما نجح"),
    ]
    for phase_id, title in phase_titles:
        phase_initiatives = [item for item in initiatives if item["phase"] == phase_id]
        phases.append({
            "id": phase_id,
            "title": title,
            "initiatives": phase_initiatives,
        })

    if status == "COMPLETE" and bottleneck:
        executive_answer = bottleneck.get("statement") or "اكتملت نتيجة Scan ويمكن متابعة أثر القرار."
    elif status == "REVIEW_REQUIRED" and bottleneck:
        executive_answer = bottleneck.get("statement") or "يوجد اختناق مرشح يحتاج اعتمادًا بشريًا."
    elif status == "INCOMPLETE":
        executive_answer = "لا يمكن اعتماد اختناق بعد؛ نحتاج معلومة مستقلة قبل القرار."
    else:
        executive_answer = "لم يُنتج التقرير اختناقًا قابلًا للاعتماد بعد."

    what_not_do = [
        "لا نبدأ توسعًا أو حملة جديدة قبل اعتماد الاختناق ومقياس نجاحه.",
        "لا نعامل الاحتمال أو الافتراض كحقيقة تنفيذية.",
        "لا نعد بأثر مالي غير موثق؛ نثبت الأثر عبر KPI قبل وبعد.",
    ]
    if scan.get("missing_evidence"):
        what_not_do.insert(
            0, "لا نغلق بوابة الأدلة: البنود الناقصة تبقى غير متاحة حتى تصل مصادرها."
        )

    reference_knowledge = _safe_contextual_reference_knowledge(
        db, company, latest_case, bottleneck
    )
    problem = None
    if latest_case:
        snapshot_problem = scan.get("diagnostic_problem") or {}
        problem = {
            "title": latest_case["case_title"],
            "statement": snapshot_problem.get("statement")
                or latest_case["declared_problem"]
                or latest_case["case_title"],
            "real_question": snapshot_problem.get("real_question")
                or (None if latest_scan_row else latest_case["real_question"]),
            "source_id": snapshot_problem.get("source_id")
                or f"CASE:{latest_case['case_id']}:DECLARED_PROBLEM",
            "source_type": "Discovery / Case",
            "source_date": snapshot_problem.get("source_date")
                or latest_case["opened_at"],
        }
    hypothesis = hypotheses[0] if hypotheses else None
    inference = inferences[0] if inferences else None
    impact_review = None
    if scan.get("case_id"):
        impact_row = db.execute(
            """SELECT impact_outcome, result_summary, reviewed_at
               FROM p0_impact_reviews
               WHERE company_id=? AND case_id=?
               ORDER BY reviewed_at DESC, review_id DESC LIMIT 1""",
            (company_id, scan["case_id"]),
        ).fetchone()
        impact_review = dict(impact_row) if impact_row else None
    client_confidence = _build_client_confidence_view(
        scan,
        status,
        has_run=bool(latest_scan_row),
        baseline_valid=bool(scan.get("diagnostic_baseline_valid")),
        impact_review=impact_review,
    )
    client_missing_evidence = _client_missing_evidence(
        scan.get("missing_evidence") or []
    )
    client_evidence_request_cycle = _client_evidence_request_cycle(
        scan.get("evidence_request_cycle"),
        outcome=scan.get("journey_outcome"),
    )
    priority_source_ids = {
        str(source_id)
        for source_id in ((bottleneck or opportunity or {}).get("source_ids") or [])
    }
    executive_signals = [
        item for item in evidence_citations
        if str(item.get("source_id")) in priority_source_ids
    ]
    if len(executive_signals) < 3:
        existing_signal_ids = {
            str(item.get("source_id")) for item in executive_signals
        }
        executive_signals.extend(
            item for item in evidence_citations
            if str(item.get("source_id")) not in existing_signal_ids
        )
    executive_signals = executive_signals[:3]
    decision_ready = status in {"REVIEW_REQUIRED", "COMPLETE"}
    current_decision = (
        decisions[0] if decisions
        else proposed_decision if decision_ready else None
    )
    next_action = next(
        (
            item for item in initiatives
            if item.get("decision_id") and decision_ready
        ),
        None,
    )
    execution_plan_available = bool(
        decision_ready
        and any(item.get("decision_id") for item in initiatives)
    )
    quality = dict(scan.get("diagnostic_quality") or {})
    if (
        decision_ready
        and quality.get("evidence_strength") == "STRONG"
        and quality.get("data_reliability") in {"HIGH", "MEDIUM"}
        and not scan.get("open_conflicts")
    ):
        report_tier = "EXPANDED"
        report_tier_label = "تشخيص موسّع مدعوم"
    elif decision_ready:
        report_tier = "DIAGNOSTIC"
        report_tier_label = "تشخيص مكتمل"
    else:
        report_tier = "PRELIMINARY"
        report_tier_label = "تقرير أولي مختصر"

    def _decision_rank(decision):
        try:
            linked = len(json.loads(decision.get("evidence_ids") or "[]"))
        except (TypeError, json.JSONDecodeError):
            linked = 0
        completeness = sum(bool(decision.get(key)) for key in (
            "recommended_action", "reason", "expected_impact",
            "owner_name", "due_date", "success_metric",
        ))
        status_weight = 20 if decision.get("status") == "معتمد" else 10
        return (
            status_weight
            + int(decision.get("confidence_score") or 0)
            + min(15, linked * 5)
            + completeness * 3
        )

    report_decisions = []
    for rank, decision in enumerate(
        sorted(decisions, key=_decision_rank, reverse=True)[:5],
        start=1,
    ):
        item = dict(decision)
        item.update({
            "rank": rank,
            "priority_score": _decision_rank(decision),
            "priority_reason": (
                decision.get("reason")
                or "يرتبط مباشرة بالقضية وبالمعلومات المؤهلة في Sana Scan."
            ),
            "expected_impact_label": (
                decision.get("expected_impact")
                or "أثر متوقع يحتاج معيار نجاح وقياسًا قبل/بعد."
            ),
        })
        report_decisions.append(item)

    asset_scores = list(scan.get("asset_scores") or [])
    strong_assets = [
        item for item in asset_scores
        if item.get("status") == "COMPLETE"
        and item.get("score") is not None
        and item["score"] >= 60
    ]
    weak_assets = [
        item for item in asset_scores
        if item.get("status") == "COMPLETE"
        and item.get("score") is not None
        and item["score"] < 60
    ]
    swot = {
        "strengths": [{
            "text": f"{_client_asset_view(item)['client_asset_name']}: {item['score']}/100",
            "certainty": "مثبت",
        } for item in strong_assets[:3]],
        "weaknesses": [{
            "text": f"{_client_asset_view(item)['client_asset_name']}: {item['score']}/100",
            "certainty": "مثبت",
        } for item in weak_assets[:3]],
        "opportunities": ([{
            "text": opportunity.get("statement"),
            "certainty": (
                "مثبت" if bottleneck and bottleneck.get("classification") == "Inference"
                else "محتمل / يحتاج تحقق"
            ),
        }] if opportunity and opportunity.get("statement") else []),
        "threats": ([{
            "text": bottleneck.get("statement"),
            "certainty": (
                "مثبت" if bottleneck.get("classification") == "Inference"
                else "محتمل / يحتاج تحقق"
            ),
        }] if bottleneck and bottleneck.get("statement") else []) + [{
            "text": conflict.get("verification_question"),
            "certainty": "محتمل / يحتاج تحقق",
        } for conflict in (scan.get("open_conflicts") or [])[:2]],
    }
    swot["priority_link"] = (
        (report_decisions[0].get("title") if report_decisions else None)
        or (opportunity or {}).get("statement")
    )
    diagnostic_review = {
        "contract_version": "SANA-DIAGNOSTIC-REVIEW-v1",
        "snapshot": {
            "scan_id": scan.get("scan_id"),
            "case_id": scan.get("case_id") or (latest_case["case_id"] if latest_case else None),
            "company_id": company_id,
            "created_at": scan.get("created_at"),
            "methodology_version": scan.get("methodology_version"),
        },
        "journey": scan_journey,
        "journey_outcome": scan.get("journey_outcome"),
        "evidence_request_cycle": dict(scan.get("evidence_request_cycle") or {}),
        "client_evidence_request_cycle": client_evidence_request_cycle,
        "problem": problem,
        "hypothesis": hypothesis,
        "inference": inference,
        "bottleneck": bottleneck,
        "opportunity": opportunity,
        "proposed_decision": (
            proposed_decision if status in {"REVIEW_REQUIRED", "COMPLETE"} else None
        ),
        "evidence_gate": {
            "open": status in {"NOT_RUN", "INCOMPLETE"},
            "missing_evidence": list(scan.get("missing_evidence") or []),
            "requests": list(scan.get("evidence_requests") or []),
            "next_required": scan_journey["action_label"],
            "decision_allowed": status in {"REVIEW_REQUIRED", "COMPLETE"},
            "final_score_allowed": status in {"REVIEW_REQUIRED", "COMPLETE"}
                and all(
                    item.get("status") == "COMPLETE" and item.get("score") is not None
                    for item in (scan.get("asset_scores") or [])
                )
                and bool(scan.get("asset_scores")),
        },
        "sources": citations,
        "client_evidence": evidence_citations,
        "reference_knowledge": reference_knowledge,
        "financial_value": {
            "status": "DEFERRED",
            "value": None,
            "label": "N/A — Deferred",
        },
        "fallback": {
            "deterministic": True,
            "ai_required": False,
            "ai_used": bool(scan.get("ai_used")),
            "reference_knowledge_available": reference_knowledge.get("available", True),
        },
        "client_confidence": client_confidence,
    }

    return {
        "scan": scan,
        "scan_status": status,
        "scan_status_label": status_label,
        "scan_has_run": bool(latest_scan_row),
        "scan_case": dict(latest_case) if latest_case else None,
        "scan_findings": findings,
        "scan_hypotheses": hypotheses,
        "scan_bottleneck": bottleneck,
        "scan_opportunity": opportunity,
        "scan_proposed_decision": proposed_decision,
        "scan_missing_evidence": list(scan.get("missing_evidence") or []),
        "scan_evidence_requests": list(scan.get("evidence_requests") or []),
        "scan_evidence_request_cycle": dict(scan.get("evidence_request_cycle") or {}),
        "scan_client_evidence_request_cycle": client_evidence_request_cycle,
        "scan_journey_outcome": scan.get("journey_outcome"),
        "scan_client_missing_evidence": client_missing_evidence,
        "scan_diagnostic_quality": dict(scan.get("diagnostic_quality") or {}),
        "scan_diagnostic_baseline": scan.get("diagnostic_baseline"),
        "scan_open_conflicts": list(scan.get("open_conflicts") or []),
        "scan_verification_questions": list(scan.get("verification_questions") or []),
        "scan_decisions_available_now": list(scan.get("decisions_available_now") or []),
        "scan_decisions_waiting_for_evidence": list(
            scan.get("decisions_waiting_for_evidence") or []
        ),
        "scan_citations": citations,
        "scan_evidence_citations": evidence_citations,
        "scan_phases": phases,
        "scan_initiatives": initiatives,
        "scan_unplanned_decisions": unplanned_decisions,
        "scan_executive_answer": executive_answer,
        "scan_executive_signals": executive_signals,
        "scan_current_decision": current_decision,
        "scan_next_action": next_action,
        "scan_decision_ready": decision_ready,
        "scan_execution_plan_available": execution_plan_available,
        "scan_report_tier": report_tier,
        "scan_report_tier_label": report_tier_label,
        "scan_questions_complete": decision_ready,
        "scan_swot": swot,
        "scan_report_decisions": report_decisions,
        "scan_what_not_do": what_not_do,
        "scan_journey": scan_journey,
        "scan_score_progress": {
            "complete": sum(
                1 for item in (scan.get("asset_scores") or [])
                if item.get("status") == "COMPLETE" and item.get("score") is not None
            ),
            "total": len(scan.get("asset_scores") or []),
        },
        "scan_operational_score": (
            round(sum(item["score"] for item in (scan.get("asset_scores") or [])
                      if item.get("status") == "COMPLETE" and item.get("score") is not None)
                  / len(scan.get("asset_scores") or []))
            if (
                scan.get("asset_scores")
                and all(
                    item.get("status") == "COMPLETE" and item.get("score") is not None
                    for item in scan.get("asset_scores") or []
                )
                and status in {"REVIEW_REQUIRED", "COMPLETE"}
            )
            else None
        ),
        "scan_review_note": scan_journey["message"],
        "scan_client_confidence": client_confidence,
        "reference_knowledge": reference_knowledge,
        "diagnostic_review": diagnostic_review,
    }
def _build_passport_context(company_id):
    """يُعيد dict جاهزًا لـ render_template('14-passport-report.html', ...)
    أو None إذا لم تُوجد الشركة."""
    db = get_db()
    company = db.execute("SELECT * FROM companies WHERE company_id=?", (company_id,)).fetchone()
    if not company:
        return None

    assets = db.execute(
        "SELECT * FROM assets WHERE company_id=? ORDER BY current_score ASC", (company_id,)
    ).fetchall()
    decisions = db.execute(
        "SELECT * FROM decisions WHERE company_id=? ORDER BY created_at DESC", (company_id,)
    ).fetchall()
    completed_tasks = db.execute(
        "SELECT * FROM tasks WHERE company_id=? AND status='منجزة' ORDER BY completed_at ASC",
        (company_id,)
    ).fetchall()
    evidence_count = db.execute(
        "SELECT COUNT(*) as cnt FROM evidence WHERE company_id=?", (company_id,)
    ).fetchone()["cnt"]

    avg_score = round(sum(a["current_score"] for a in assets) / len(assets)) if assets else 0
    financial_value = financial_value_gate()
    current_value = financial_value["current_value"]
    potential_value = financial_value["potential_value"]
    gap = financial_value["value_gap"]

    def _asset_color(score):
        if score <= 30:   return "#E5484D"
        if score <= 60:   return "#F2B233"
        return "#0EA5A5"

    asset_colors = [_asset_color(a["current_score"]) for a in assets]

    tasks_by_phase = {}
    for t in completed_tasks:
        key = t["phase_label"] or "مهام منجزة أخرى"
        tasks_by_phase.setdefault(key, []).append(t)

    weakest = min(assets, key=lambda a: a["current_score"]) if assets else None

    context = {
        "company":        dict(company),
        "export_date":    datetime.utcnow().strftime("%Y-%m-%d"),
        "avg_score":      avg_score,
        "current_value":  current_value,
        "potential_value": potential_value,
        "gap":            gap,
        "score_ready":    False,
        "score_hint":     "Sana Score التشغيلي يظهر فقط بعد اكتمال محاور Scan ومراجعتها بشريًا.",
        "financial_value_status": "DEFERRED",
        "financial_value_note": (
            "التقييم المالي غير معروض — لا توجد منهجية وأدلة معتمدة تسمح بتحويل "
            "Sana Score إلى قيمة نقدية."
        ),
        "financial_value_policy": financial_value,
        "assets_sorted":  [dict(a) for a in assets],
        "asset_colors":   asset_colors,
        "tasks_by_phase": tasks_by_phase,
        "decisions":      [dict(d) for d in decisions],
        "weakest_asset":  dict(weakest) if weakest else None,
    }
    context.update(_build_scan_report_context(company_id) or {})
    context["operational_score"] = context.get("scan_operational_score")
    context["score_progress"] = context.get("scan_score_progress") or {
        "complete": 0,
        "total": 0,
    }
    context["assets_sorted"] = [
        _client_asset_view(asset) for asset in context["assets_sorted"]
    ]
    context["scan"] = dict(context.get("scan") or {})
    context["scan"]["asset_scores"] = [
        _client_asset_view(asset)
        for asset in context["scan"].get("asset_scores") or []
    ]
    context["weakest_asset"] = (
        _client_asset_view(weakest) if weakest else None
    )
    context["score_ready"] = context["operational_score"] is not None
    context["reference_knowledge"] = context.get(
        "reference_knowledge", _reference_knowledge_fallback()
    )
    case_id = (context.get("scan_case") or {}).get("case_id")
    if case_id:
        from sana_human_review import client_payload
        context["human_review"] = client_payload(db, company_id, case_id)
    else:
        context["human_review"] = None
    context["report_view"] = "main"
    context["context_query"] = ""
    assessment_url = endpoint_path("assessment")
    context["report_summary_url"] = endpoint_path(
        "scan_report_html", company_id=company_id
    )
    context["report_details_url"] = endpoint_path(
        "scan_report_details_html", company_id=company_id
    )
    context["execution_plan_url"] = endpoint_path(
        "execution_plan_html", company_id=company_id
    )
    context["report_pdf_url"] = endpoint_path(
        "passport_report_pdf", company_id=company_id
    )
    context["execution_plan_pdf_url"] = (
        endpoint_path("passport_report_pdf", company_id=company_id)
        + "?view=execution-plan"
    )
    context["adaptive_questions_url"] = (
        endpoint_path("case_workspace", case_id=case_id) + "#sdsSection"
        if case_id else assessment_url
    )
    context["report_problem_url"] = (
        endpoint_path("case_workspace", case_id=case_id) + "#caseQuestion"
        if case_id else assessment_url
    )
    context["report_evidence_url"] = (
        endpoint_path("case_workspace", case_id=case_id) + "#evidenceSection"
        if case_id else assessment_url
    )
    context["report_decision_url"] = (
        endpoint_path("case_workspace", case_id=case_id) + "#decisionsSection"
        if case_id else assessment_url
    )
    context["report_results_url"] = (
        endpoint_path("case_workspace", case_id=case_id) + "#resultsSection"
        if case_id else assessment_url
    )
    context["report_next_url"] = (
        context["report_results_url"]
        if context.get("scan_next_action") else context["adaptive_questions_url"]
    )
    context["journey_urls"] = {
        "discovery": endpoint_path("discovery"),
        "onboarding": endpoint_path("onboarding"),
        "home": endpoint_path("ceo_home"),
        "passport": endpoint_path("business_passport"),
        "logout": endpoint_path("logout"),
        "company_report": endpoint_path(
            "scan_report_html", company_id=company_id
        ),
        "company_plan": endpoint_path(
            "execution_plan_html", company_id=company_id
        ),
    }
    return context


def _render_scan_report_view(company_id, report_view):
    company = get_db().execute(
        "SELECT company_id FROM companies WHERE company_id=?", (company_id,)
    ).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(company["company_id"])
    if guard:
        return guard
    scan_redirect = _client_scan_result_redirect(company_id)
    if scan_redirect:
        return scan_redirect
    ctx = _build_passport_context(company_id)
    from flask_wtf.csrf import generate_csrf
    ctx["csrf_value"] = generate_csrf()
    ctx["report_view"] = report_view
    ctx["context_query"] = p0_template_context()["context_query"]
    return render_template("14-passport-report.html", **ctx)


@app.route("/company/<company_id>/scan-report")
def scan_report_html(company_id):
    """عرض HTML قابل للمراجعة قبل تنزيل حزمة جلسة النتائج."""
    company = get_db().execute(
        "SELECT company_id FROM companies WHERE company_id=?", (company_id,)
    ).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(company["company_id"])
    if guard:
        return guard
    if request.args.get("view") == "expert-summary":
        from sana_human_review import summary_for_review
        case_id = (request.args.get("case_id") or "").strip()
        review_id = (request.args.get("review_id") or "").strip() or None
        summary = summary_for_review(
            get_db(), company_id, case_id, review_id,
            allow_unapproved=_admin_role() in SYSTEM_ADMIN_ROLES,
        )
        if not summary:
            return jsonify({
                "success": False,
                "error": "EXPERT_SUMMARY_NOT_AVAILABLE",
            }), 404
        return render_template("25-expert-review-summary.html", summary=summary)
    return _render_scan_report_view(company_id, "main")


@app.route("/company/<company_id>/scan-report/details")
def scan_report_details_html(company_id):
    return _render_scan_report_view(company_id, "details")


@app.route("/company/<company_id>/execution-plan")
def execution_plan_html(company_id):
    return _render_scan_report_view(company_id, "plan")


@app.route("/api/cases/<case_id>/human-review", methods=["GET", "POST", "DELETE"])
def case_human_review(case_id):
    from sana_human_review import (
        add_event, client_payload, snapshot_for_case,
    )
    db = get_db()
    case = db.execute(
        "SELECT company_id FROM cases WHERE case_id=?", (case_id,)
    ).fetchone()
    if not case:
        return jsonify({"success": False, "error": "CASE_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(case["company_id"])
    if guard:
        return guard
    if request.method == "GET":
        return jsonify({"success": True, "data": client_payload(db, case["company_id"], case_id)})
    if request.method == "DELETE":
        review = db.execute(
            """SELECT * FROM case_human_reviews
               WHERE company_id=? AND case_id=? AND status IN ('REQUESTED','SCHEDULED')
               ORDER BY requested_at DESC LIMIT 1 FOR UPDATE""",
            (case["company_id"], case_id),
        ).fetchone()
        if not review:
            return jsonify({"success": False, "message": "لا يوجد طلب مفتوح لإلغائه."}), 404
        db.execute(
            """UPDATE case_human_reviews SET status='CANCELLED',cancelled_at=now()
               WHERE review_id=?""", (review["review_id"],)
        )
        add_event(
            db, review["review_id"], case["company_id"], review["status"],
            "CANCELLED", current_account()["account_id"],
        )
        db.commit()
        return jsonify({"success": True, "data": client_payload(
            db, case["company_id"], case_id
        )})
    body = request.get_json(silent=True) or {}
    client_note = str(body.get("client_note") or "").strip()
    if len(client_note) > 500:
        return jsonify({
            "success": False,
            "message": "اختصر الملاحظة إلى 500 حرف أو أقل.",
        }), 400
    payload = client_payload(db, case["company_id"], case_id)
    if not payload["settings"]["available"]:
        return jsonify({"success": False, "message": "الحجز المدفوع غير مفعّل حاليًا."}), 409
    active = payload.get("review")
    if active and active["status"] in {"REQUESTED", "SCHEDULED"}:
        return jsonify({"success": False, "message": "يوجد طلب مراجعة مفتوح لهذه القضية."}), 409
    account = current_account()
    snapshot = snapshot_for_case(db, case["company_id"], case_id)
    decision = snapshot.get("decision") or {}
    review_id = "HRV-" + uuid.uuid4().hex[:12].upper()
    db.execute(
        """INSERT INTO case_human_reviews
           (review_id,company_id,case_id,decision_id,requested_by,
             reason_code,reason_label,status,before_snapshot_json,client_note,
             expert_summary_status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            review_id, case["company_id"], case_id, decision.get("decision_id"),
            account["account_id"], "human_review_request",
            "طلب مراجعة بشرية", "REQUESTED",
            json.dumps(snapshot, ensure_ascii=False, default=str), client_note or None,
            "DRAFT",
        ),
    )
    add_event(db, review_id, case["company_id"], None, "REQUESTED", account["account_id"])
    _admin_audit(
        db, account["account_id"], "human_review_requested",
        "case_human_review", review_id, case["company_id"],
        reason="طلب العميل مراجعة بشرية",
        metadata={"case_id": case_id, "has_client_note": bool(client_note)},
    )
    db.commit()
    return jsonify({"success": True, "data": client_payload(
        db, case["company_id"], case_id
    )}), 201
@app.route("/api/companies/<company_id>/scan/report-pdf")
@app.route("/api/companies/<company_id>/passport/report-pdf")
def passport_report_pdf(company_id):
    """يُنتج حزمة Sana Scan التنفيذية بصيغة PDF قابلة للإرسال."""
    company = get_db().execute(
        "SELECT * FROM companies WHERE company_id=?", (company_id,)
    ).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(company["company_id"])
    if guard:
        return guard

    expert_summary = request.args.get("view") == "expert-summary"
    if not expert_summary:
        scan_redirect = _client_scan_result_redirect(company_id)
        if scan_redirect:
            return scan_redirect
    if expert_summary:
        from sana_human_review import summary_for_review
        case_id = (request.args.get("case_id") or "").strip()
        review_id = (request.args.get("review_id") or "").strip() or None
        summary = summary_for_review(
            get_db(), company_id, case_id, review_id,
            allow_unapproved=_admin_role() in SYSTEM_ADMIN_ROLES,
        )
        if not summary:
            return jsonify({
                "success": False,
                "error": "EXPERT_SUMMARY_NOT_AVAILABLE",
            }), 404
        html_string = render_template(
            "25-expert-review-summary.html", summary=summary,
        )
        safe_name = summary["company_name"].replace("/", "-")
        download_name = f"Sana-Expert-Review_{safe_name}.pdf"
    else:
        ctx = _build_passport_context(company_id)
        if not ctx:
            return jsonify({"success": False, "error": "BUILD_FAILED"}), 500
        execution_plan_export = request.args.get("view") == "execution-plan"
        ctx["report_view"] = "plan" if execution_plan_export else "main"
        ctx["context_query"] = p0_template_context()["context_query"]
        html_string = render_template("14-passport-report.html", **ctx)
        safe_name = ctx["company"]["name"].replace("/", "-")
        download_name = (
            f"Sana-Execution-Plan_{safe_name}.pdf"
            if execution_plan_export
            else f"Sana-Scan_{safe_name}.pdf"
        )

    # lazy import — weasyprint يحتاج libpango كـ system lib
    import weasyprint  # noqa: PLC0415

    # WeasyPrint: base_url مطلوب لتحميل الخطوط من Google Fonts
    pdf_bytes = weasyprint.HTML(
        string=html_string,
        base_url=request.host_url
    ).write_pdf()

    from urllib.parse import quote as _quote
    encoded = _quote(download_name, safe="")

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            # RFC 5987 — اسم الملف مُرمَّز UTF-8 لدعم العربي في كل المتصفحات
            "Content-Disposition": f"attachment; filename=\"passport.pdf\"; filename*=UTF-8''{encoded}",
            "Content-Length": str(len(pdf_bytes)),
        }
    )


@app.route("/api/decisions/<decision_id>/approve", methods=["POST"])
def approve_decision(decision_id):
    db = get_db()
    decision = db.execute("SELECT * FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
    if not decision:
        return jsonify({"success": False, "error": "DECISION_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(decision["company_id"])
    if guard:
        return guard

    body = request.get_json(force=True, silent=True) or {}
    owner_name = (body.get("owner_name") or "").strip() or None
    due_date = (body.get("due_date") or "").strip() or None
    success_metric = (body.get("success_metric") or "").strip() or None
    next_action = (body.get("next_action") or "").strip() or None
    account = current_account()
    approver_user_id = account["account_id"] if account else None
    requested_scan_id = (body.get("scan_id") or "").strip() or None
    requested_evidence_ids = body.get("evidence_ids")
    persisted_scan_id = decision["scan_id"]
    persisted_evidence_ids = []
    if decision["evidence_ids"]:
        try:
            persisted_evidence_ids = json.loads(decision["evidence_ids"])
        except (TypeError, json.JSONDecodeError):
            persisted_evidence_ids = []

    if decision["phase_label"] == "P0":
        if (
            (requested_scan_id and requested_scan_id != persisted_scan_id)
            or (
                requested_evidence_ids is not None
                and sorted(map(str, requested_evidence_ids))
                != sorted(map(str, persisted_evidence_ids))
            )
        ):
            return jsonify({
                "success": False,
                "error": "P0_DECISION_PROVENANCE_IMMUTABLE",
                "message": "لا يمكن تغيير مصدر القرار أو أدلته بعد إنشائه.",
            }), 409
        scan_id = persisted_scan_id
        evidence_ids = persisted_evidence_ids
    else:
        scan_id = requested_scan_id or persisted_scan_id
        evidence_ids = requested_evidence_ids
        if evidence_ids is None:
            evidence_ids = persisted_evidence_ids
    evidence_ids = evidence_ids or []
    valid_ids = []

    # DEC-01: مقياس النجاح إلزامي عند الاعتماد
    if not success_metric:
        return jsonify({"success": False, "error": "SUCCESS_METRIC_REQUIRED",
                        "message": "أدخل مقياس النجاح لاعتماد هذا القرار"}), 400

    # لا يجوز بدء معاملة الاعتماد قبل اكتمال كل عناصر التنفيذ. والأهم أن
    # التحقق يحدث قبل أي UPDATE حتى لا يبقى القرار معتمدًا بلا مهمة.
    if not owner_name or not due_date or not next_action or not approver_user_id:
        return jsonify({
            "success": False,
            "error": "DECISION_RESPONSIBILITY_FIELDS_REQUIRED",
            "message": "يلزم المسؤول والمعتمد والموعد ومقياس النجاح والخطوة التالية قبل اعتماد القرار",
        }), 400

    if decision["phase_label"] == "P0" and (not scan_id or not evidence_ids):
        return jsonify({
            "success": False,
            "error": "DECISION_EVIDENCE_LINK_REQUIRED",
            "message": "قرار العميل يبقى مؤجلًا حتى يرتبط بتحليل ودليل مؤهل.",
        }), 400

    if scan_id or evidence_ids:
        if not scan_id or not isinstance(evidence_ids, list) or not evidence_ids:
            return jsonify({
                "success": False,
                "error": "DECISION_EVIDENCE_LINK_REQUIRED",
                "message": "ربط القرار بالتشخيص يتطلب scan_id وقائمة evidence_ids.",
            }), 400
        scan_row = db.execute(
            """SELECT result FROM scan_runs
               WHERE scan_id=? AND company_id=? AND case_id=?""",
            (scan_id, decision["company_id"], decision["case_id"]),
        ).fetchone()
        if not scan_row:
            return jsonify({
                "success": False,
                "error": "SCAN_NOT_FOUND_FOR_DECISION",
            }), 400
        try:
            scan_sources = {
                str(item.get("source_id")): item
                for item in (json.loads(scan_row["result"]).get("classified_inputs") or [])
            }
        except (TypeError, json.JSONDecodeError):
            scan_sources = {}
        for evidence_id in evidence_ids:
            source = scan_sources.get(str(evidence_id))
            if not source or source.get("classification") not in {"Fact", "Evidence"}:
                return jsonify({
                    "success": False,
                    "error": "INVALID_DECISION_EVIDENCE",
                    "message": "كل دليل قرار يجب أن يكون Fact أو Evidence داخل لقطة Scan نفسها.",
                }), 400
            evidence = db.execute(
                """SELECT evidence_id FROM evidence
                   WHERE evidence_id=? AND company_id=? AND case_id=?""",
                (evidence_id, decision["company_id"], decision["case_id"]),
            ).fetchone()
            if not evidence:
                return jsonify({
                    "success": False,
                    "error": "INVALID_DECISION_EVIDENCE",
                }), 400
            valid_ids.append(str(evidence_id))

    # الاعتماد والمهمة وحدة ذرية: أي فشل في INSERT/UPDATE/COMMIT يعيد
    # المعاملة كاملة، فلا يمكن أن يرى المستخدم اعتمادًا جزئيًا. أخطاء
    # deadlock وserialization فقط تعيد تشغيل المعاملة من البداية.
    from sana_decision_room import approve_decision_with_task
    for attempt in range(1, DECISION_APPROVAL_MAX_ATTEMPTS + 1):
        try:
            result = approve_decision_with_task(
                db,
                decision["company_id"],
                decision_id,
                owner_name=owner_name,
                due_date=due_date,
                success_metric=success_metric,
                next_action=next_action,
                approver_user_id=approver_user_id,
                scan_id=scan_id,
                evidence_ids_json=(
                    json.dumps(valid_ids, ensure_ascii=False)
                    if (scan_id or evidence_ids) else None
                ),
            )
            db.commit()
            break
        except (LookupError, ValueError) as exc:
            db.rollback()
            return jsonify({
                "success": False,
                "error": str(exc),
                "message": "تعذر اعتماد القرار — أكمل حقول التنفيذ ثم حاول مرة أخرى.",
            }), 400
        except DECISION_APPROVAL_RETRYABLE_ERRORS as exc:
            db.rollback()
            if attempt == DECISION_APPROVAL_MAX_ATTEMPTS:
                app.logger.warning(
                    "Atomic decision approval conflict exhausted retries: "
                    "decision_id=%s attempts=%d pgcode=%s",
                    decision_id,
                    attempt,
                    getattr(exc, "pgcode", None),
                )
                return jsonify({
                    "success": False,
                    "error": "DECISION_APPROVAL_FAILED",
                    "message": (
                        "تعذر اعتماد القرار بسبب تعارض قاعدة البيانات؛ "
                        "لم يُعتمد القرار. حاول مرة أخرى."
                    ),
                    "retryable": True,
                    "conflict": True,
                    "attempts": attempt,
                }), 500
            app.logger.info(
                "Retrying atomic decision approval after transient conflict: "
                "decision_id=%s attempt=%d/%d pgcode=%s",
                decision_id,
                attempt,
                DECISION_APPROVAL_MAX_ATTEMPTS,
                getattr(exc, "pgcode", None),
            )
            time.sleep(DECISION_APPROVAL_RETRY_DELAY_SECONDS)
        except Exception:
            db.rollback()
            app.logger.exception("Atomic decision approval failed: %s", decision_id)
            return jsonify({
                "success": False,
                "error": "DECISION_APPROVAL_FAILED",
                "message": "تعذر إنشاء أو ربط مهمة التنفيذ؛ لم يُعتمد القرار. حاول مرة أخرى.",
                "retryable": True,
            }), 500

    return jsonify({"success": True, "data": result})


@app.route("/api/decisions/<decision_id>/defer", methods=["POST"])
def defer_decision(decision_id):
    db = get_db()
    decision = db.execute("SELECT * FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
    if not decision:
        return jsonify({"success": False, "error": "DECISION_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(decision["company_id"])
    if guard:
        return guard

    db.execute("UPDATE decisions SET status='قيد المراجعة' WHERE decision_id=?", (decision_id,))
    db.commit()
    return jsonify({"success": True, "data": {"decision_id": decision_id, "status": "قيد المراجعة"}})


# ------------------------------------------------------------------
# API — Cases (create new case = "فتح قضية")
# ------------------------------------------------------------------

@app.route("/api/companies/<company_id>/cases", methods=["POST"])
def create_case(company_id):
    body = request.get_json(force=True)
    db = get_db()
    import uuid

    observation = (body.get("declared_problem") or "").strip()
    asset_id = body.get("related_asset_id")
    supporting_evidence = (body.get("supporting_evidence") or "").strip()
    case_title = (body.get("case_title") or "").strip()

    if not observation or not case_title or not asset_id:
        return jsonify({
            "success": False,
            "error": "MISSING_REQUIRED_FIELDS",
            "message": "يلزم تحديد الملاحظة، والأصل المتأثر، وعنوان القضية."
        }), 400

    asset = db.execute(
        "SELECT * FROM assets WHERE asset_id=? AND company_id=?", (asset_id, company_id)
    ).fetchone()
    if not asset:
        return jsonify({"success": False, "error": "ASSET_NOT_FOUND"}), 400

    # القانون الثاني: كل قضية تشخيصية تُصاغ كفرضية قابلة للاختبار، لا كحكم نهائي
    asset_name = asset["asset_name"]
    real_question = f'هل "{observation}" فعلًا بسبب ضعف {asset_name}، أم يوجد سبب آخر لم يُكتشف بعد؟'

    case_id = "CS" + uuid.uuid4().hex[:6].upper()
    db.execute("""INSERT INTO cases
        (case_id, company_id, case_title, case_type, case_status, declared_problem, real_question,
         related_asset_id, confidence_score)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (case_id, company_id, case_title, "Diagnostic Case", "Open",
         observation, real_question, asset_id, 45))

    if supporting_evidence:
        from sana_evidence import save_evidence as _save_ev
        _save_ev(
            db,
            company_id  = company_id,
            case_id     = case_id,
            asset_id    = asset_id,
            title       = supporting_evidence,
            source_type = "دليل تأسيسي",
            confidence  = 50,
        )

    db.commit()
    return jsonify({"success": True, "data": {"case_id": case_id}}), 201


# ------------------------------------------------------------------
# API — Evidence (add evidence = "رفع دليل")
# ------------------------------------------------------------------

@app.route("/api/companies/<company_id>/evidence", methods=["POST"])
def add_evidence(company_id):
    from sana_evidence import save_evidence as _save_evidence
    from sana_scan import latest_scan, run_scan
    body = request.get_json(force=True)
    db = get_db()
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard

    case_id     = body.get("case_id")
    asset_id    = body.get("asset_id")
    title       = (body.get("title") or "").strip()
    source_type = body.get("source_type", "ملاحظة مباشرة")
    confidence  = int(body.get("confidence", 50))
    evidence_type = (body.get("evidence_type") or "Evidence").strip()
    source_ref = (body.get("source_ref") or "").strip()
    adaptive_answer = body.get("adaptive_answer") is True
    adaptive_question_id = (body.get("question_id") or "").strip()
    adaptive_value = (body.get("answer") or "").strip()
    request_key = (body.get("request_key") or "").strip()
    request_fingerprint = (body.get("request_fingerprint") or "").strip()
    is_unknown_response = (
        body.get("unknown") is True
        or str(body.get("response") or "").upper() == "UNKNOWN"
        or str(body.get("outcome") or "").upper() == "UNKNOWN"
        or str(body.get("answer") or "").upper() == "UNKNOWN"
        or str(body.get("answer") or "").strip() in {
            "ما عندي الآن",
            "لا أملك هذه المعلومة الآن",
        }
    )
    evidence_response = None
    if adaptive_answer:
        if not case_id or not adaptive_question_id or not adaptive_value:
            return jsonify({
                "success": False,
                "error": "ADAPTIVE_ANSWER_CONTEXT_REQUIRED",
                "message": "الإجابة التكيفية تحتاج القضية والسؤال والإجابة.",
            }), 400
        source_type = "SDS-002 تشخيص تراكمي"
        source_ref = adaptive_question_id
        evidence_type = "Evidence"
        confidence = min(confidence, 40)
        body["information_type"] = "Narrative"
        body["verification_status"] = "UNVERIFIED"
        body["source_category"] = "SELF_REPORTED"
    if is_unknown_response:
        if not case_id:
            return jsonify({
                "success": False,
                "error": "CASE_REQUIRED_FOR_UNKNOWN_EVIDENCE",
                "message": "حدد القضية قبل تسجيل عدم توفر المعلومة.",
            }), 400
        case = db.execute(
            "SELECT company_id FROM cases WHERE case_id=?", (case_id,)
        ).fetchone()
        if not case or case["company_id"] != company_id:
            return jsonify({
                "success": False,
                "error": "CASE_NOT_FOUND",
                "message": "تعذر العثور على القضية ضمن شركتك.",
            }), 404
        scan = latest_scan(db, case_id) or {}
        cycle = scan.get("evidence_request_cycle") or {}
        pending = cycle.get("pending_requests") or []
        selected = next(
            (
                item for item in pending
                if item.get("fingerprint") == request_fingerprint
                or item.get("request_key") == request_fingerprint
            ),
            None,
        ) if request_fingerprint else (pending[0] if pending else None)
        if not selected:
            return jsonify({
                "success": False,
                "error": "EVIDENCE_REQUEST_NOT_AVAILABLE",
                "message": "لا يوجد طلب دليل حرج مفتوح يمكن إغلاقه بهذه الإجابة.",
            }), 409
        request_fingerprint = selected["fingerprint"]
        request_key = selected.get("request_key") or request_fingerprint
        title = title or f"لا أملك هذه المعلومة الآن: {selected['question']}"
        source_type = "CLIENT_UNKNOWN"
        source_ref = "CLIENT_UNKNOWN"
        evidence_type = "Evidence"
        asset_id = asset_id or (
            db.execute(
                "SELECT related_asset_id FROM cases WHERE case_id=? AND company_id=?",
                (case_id, company_id),
            ).fetchone() or {}
        ).get("related_asset_id")
        evidence_response = {
            "fingerprint": request_fingerprint,
            "request_key": request_key,
            "outcome": "UNKNOWN",
        }
    if request_key:
        case = db.execute(
            "SELECT company_id FROM cases WHERE case_id=?", (case_id,)
        ).fetchone()
        if not case or case["company_id"] != company_id:
            return jsonify({
                "success": False,
                "error": "CASE_NOT_FOUND",
                "message": "تعذر العثور على القضية ضمن شركتك.",
            }), 404
        scan = latest_scan(db, case_id) or {}
        matching_request = next(
            (
                item for item in (scan.get("evidence_requests") or [])
                if item.get("request_key") == request_key
            ),
            None,
        )
        if not matching_request:
            return jsonify({
                "success": False,
                "error": "EVIDENCE_REQUEST_NOT_FOUND",
                "message": "هذا الطلب لم يعد مفتوحًا. حدّث الصفحة واختر الطلب الظاهر.",
            }), 409
        if matching_request.get("status") not in {"OPEN", "PENDING"}:
            return jsonify({
                "success": False,
                "error": "EVIDENCE_REQUEST_ALREADY_SUBMITTED",
                "message": "أرسلت معلومة لهذا الطلب بالفعل. انتقل إلى الطلب التالي.",
            }), 409
        asset_id = matching_request.get("asset_id") or asset_id
    if evidence_type not in {"Fact", "Evidence"}:
        return jsonify({
            "success": False,
            "error": "INVALID_EVIDENCE_TYPE",
            "message": "الإدخال البشري يقبل Fact أو Evidence فقط؛ الأنواع المشتقة ينشئها Sana Scan.",
        }), 400
    if evidence_type == "Fact" and not source_ref:
        return jsonify({
            "success": False,
            "error": "SOURCE_REF_REQUIRED",
            "message": "مرجع المصدر مطلوب عند تسجيل Fact.",
        }), 400
    source_ref = source_ref or source_type
    if confidence < 0 or confidence > 100:
        return jsonify({
            "success": False,
            "error": "INVALID_CONFIDENCE",
            "message": "الثقة يجب أن تكون بين 0 و100.",
        }), 400
    from sana_reliability import validate_context
    numeric = body.get("value") is not None or body.get("raw_value") is not None
    try:
        calibrated = validate_context(
            {**body, "source_ref": source_ref},
            numeric=numeric,
        )
    except ValueError as exc:
        return jsonify({
            "success": False,
            "error": "INVALID_DIAGNOSTIC_CONTEXT",
            "message": str(exc),
        }), 400
    trusted_reviewer = is_admin_preview() or _knowledge_admin_account_allowed()
    if not trusted_reviewer:
        # مستخدم الشركة يستطيع تقديم ادعاء ومرجعه، لكنه لا يستطيع توثيق نفسه
        # أو انتحال مصدر نظام/سوق. التحقق خطوة مراجعة مستقلة.
        calibrated["verification_status"] = "UNVERIFIED"
        calibrated["source_category"] = "SELF_REPORTED"
        evidence_type = "Evidence"
    if is_unknown_response:
        calibrated["verification_status"] = "UNVERIFIED"
        calibrated["source_category"] = "UNKNOWN"

    # حماية من الحفظ المزدوج: إن وُجد دليل مطابق تمامًا لا يُنشأ سجل جديد.
    existing = db.execute(
        """SELECT evidence_id FROM evidence
           WHERE company_id=? AND COALESCE(case_id,'')=COALESCE(?,'')
             AND COALESCE(asset_id,'')=COALESCE(?,'') AND title=?
             AND COALESCE(source_type,'')=COALESCE(?,'')""",
        (company_id, case_id, asset_id, title, source_type)
    ).fetchone()
    if existing:
        existing_scan = latest_scan(db, case_id) if case_id else None
        next_question = (
            _next_sds_question(db, company_id, case_id)
            if adaptive_answer and case_id else None
        )
        return jsonify({
            "success": True,
            "data": {
                "evidence_id": existing["evidence_id"],
                "scan": existing_scan,
                "next_question": next_question,
                "result_url": f"/case/{case_id}/result" if case_id else None,
                "message": "تم تحديث الصورة" if adaptive_answer else None,
            },
            "meta": {
                "duplicate": True,
                "message": "هذا الدليل محفوظ مسبقًا بنفس المحتوى — لم يُنشأ سجل مكرر.",
                "manual_rerun_required": False if adaptive_answer else None,
            }
        }), 200

    result = _save_evidence(
        db,
        company_id  = company_id,
        case_id     = case_id,
        asset_id    = asset_id,
        title       = title,
        source_type = source_type,
        confidence  = confidence,
        evidence_type = evidence_type,
        source_ref = source_ref,
        information_type = calibrated["information_type"],
        verification_status = calibrated["verification_status"],
        source_category = calibrated["source_category"],
        period_start = calibrated["period_start"],
        period_end = calibrated["period_end"],
        raw_value = calibrated["raw_value"],
        normalized_value = calibrated["normalized_value"],
        unit = calibrated["unit"],
        topic_key = calibrated["topic_key"],
        seasonality_context = calibrated["seasonality_context"],
    )
    if not result["success"]:
        return jsonify({"success": False, "error": result["error"]}), 400
    from sana_company_memory import record_memory
    memory_key = (
        f"adaptive:{case_id}:{adaptive_question_id}"
        if adaptive_answer
        else calibrated.get("topic_key") or f"evidence:{result['evidence_id']}"
    )
    try:
        record_memory(
            db, company_id, memory_key=memory_key,
            memory_type="current_state" if adaptive_answer else "evidence",
            value=({
                "question": title.split(" — الإجابة:", 1)[0],
                "answer": adaptive_value,
                "claim": title,
            } if adaptive_answer else {
                "title": title, "raw_value": calibrated.get("raw_value"),
                "normalized_value": calibrated.get("normalized_value"),
                "unit": calibrated.get("unit"),
            }),
            source_ref=source_ref, source_type=source_type,
            observed_at=calibrated.get("observed_at") or date.today(),
            period_start=calibrated.get("period_start"),
            period_end=calibrated.get("period_end"),
            context={
                "information_type": calibrated.get("information_type"),
                "topic_key": calibrated.get("topic_key"),
                "self_reported": calibrated.get("source_category") == "SELF_REPORTED",
                "adaptive_answer": adaptive_answer,
            },
            verification_status=calibrated.get("verification_status", "UNVERIFIED"),
            freshness_class="FAST" if calibrated.get("normalized_value") is not None else "MEDIUM",
            source_strength=confidence,
            verification_confidence=(
                confidence if calibrated.get("verification_status") == "VERIFIED"
                else 10 if adaptive_answer else 0
            ),
            freshness_confidence=50, owner_id=(current_account() or {}).get("account_id"),
            case_id=case_id, asset_id=asset_id, source_id=result["evidence_id"],
            reason=(
                "إجابة تكيفية ذاتية تزيد اكتمال الصورة ولا تغيّر قوة الأصل بذاتها."
                if adaptive_answer
                else "إصدار ذاكرة مشتق من سجل الدليل الأصلي؛ لا يستبدل الدليل."
            ),
        )
    except ValueError as exc:
        db.rollback()
        return jsonify({"success": False, "error": "MEMORY_RECORDING_FAILED",
                        "message": str(exc)}), 400

    reevaluated_scan = run_scan(
        db,
        case_id,
        evidence_response={
            "fingerprint": request_fingerprint,
            "request_key": request_key,
            "outcome": "UNKNOWN" if is_unknown_response else "ANSWERED",
        } if case_id else None,
    ) if case_id else None
    next_question = (
        _next_sds_question(db, company_id, case_id)
        if adaptive_answer and case_id else None
    )
    return jsonify({
        "success": True,
        "data": {
            "evidence_id": result["evidence_id"],
            "asset_id":    result.get("asset_id"),
            "asset_name":  result.get("asset_name"),
            "new_score":   result.get("new_score"),
            "scan": reevaluated_scan,
            "journey_outcome": (reevaluated_scan or {}).get("journey_outcome"),
            "next_question": next_question,
            "result_url": f"/case/{case_id}/result" if case_id else None,
            "message": "تم تحديث الصورة" if adaptive_answer else None,
        },
        "meta": {
            "duplicate": False,
            "request_key": request_key or None,
            "reevaluated": bool(reevaluated_scan),
            "manual_rerun_required": False if adaptive_answer else None,
        }
    }), 201


# ------------------------------------------------------------------
# API — AI Analysis (أول اتصال ذكاء اصطناعي حقيقي في سنع)
# ------------------------------------------------------------------

@app.route("/api/evidence/<evidence_id>/analyze", methods=["POST"])
def analyze_evidence(evidence_id):
    """يفحص الدليل دون تخمين؛ القرارات لا تُستنتج من دليل منفرد."""
    db = get_db()
    evidence = db.execute("SELECT * FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
    if not evidence:
        return jsonify({"success": False, "error": "EVIDENCE_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(evidence["company_id"])
    if guard:
        return guard

    linked_asset = evidence["asset_id"]
    if linked_asset:
        summary = f"الدليل محفوظ ومربوط بالأصل {linked_asset}: {evidence['title']}."
        reasoning = "هذا ربط محفوظ من سجل القضية، وليس استنتاجًا من نموذج لغوي."
    else:
        summary = f"الدليل محفوظ كما أدخله المستخدم: {evidence['title']}."
        reasoning = "لا يوجد أصل مرتبط بهذا الدليل؛ يلزم ربطه أو تشغيل تشخيص القضية قبل أي قرار."
    parsed = {
        "summary": summary,
        "confidence_assessment": evidence["confidence"],
        "suggested_asset_id": linked_asset,
        "reasoning": reasoning,
    }

    db.execute(
        "UPDATE evidence SET ai_analysis=?, ai_suggested_asset_id=?, confidence=? WHERE evidence_id=?",
        (parsed.get("summary", ""), parsed.get("suggested_asset_id"),
         parsed.get("confidence_assessment", evidence["confidence"]), evidence_id)
    )
    db.commit()

    return jsonify({
        "success": True,
        "data": parsed,
        "meta": {"source": "sana_knowledge", "ai_used": False, "decision_allowed": False},
    })


@app.route("/api/cases/<case_id>/analyze", methods=["POST"])
def analyze_case(case_id):
    """يطبق قواعد سنع أولًا؛ AI اختياري لعرض نتيجة مدعومة فقط."""
    from sana_knowledge import run_diagnostic
    db = get_db()
    case = db.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not case:
        return jsonify({"success": False, "error": "CASE_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(case["company_id"])
    if guard:
        return guard

    diagnostic = run_diagnostic(db, case_id)
    supported = diagnostic.get("supported_findings", [])
    if not supported:
        deterministic = (
            f"{diagnostic['message']} "
            f"الإجراء التالي: {diagnostic['action_required']}."
        )
        db.execute("UPDATE cases SET ai_analysis=?, confidence_score=? WHERE case_id=?",
                   (deterministic, None, case_id))
        db.commit()
        return jsonify({
            "success": True,
            "data": {
                "overall_assessment": deterministic,
                "confidence_score": None,
                "recommended_decision_title": None,
                "recommended_reason": None,
                "presentation": deterministic,
            },
            "meta": {
                "source": "sana_knowledge",
                "ai_used": False,
                "knowledge_status": diagnostic["status"],
                "diagnostic": diagnostic,
            }
        })

    evidence_rows = db.execute("SELECT * FROM evidence WHERE case_id=?", (case_id,)).fetchall()
    evidence_text = "\n".join(f"- {e['title']} (مصدر: {e['source_type']}, ثقة: {e['confidence']}٪)"
                               for e in evidence_rows) or "لا توجد أدلة مسجَّلة بعد."

    assets = db.execute("SELECT asset_id, asset_type, asset_name, current_score FROM assets WHERE company_id=?",
                         (case["company_id"],)).fetchall()
    assets_list = "\n".join(f"- {a['asset_id']}: {a['asset_name']} (الدرجة: {a['current_score']})" for a in assets)
    company = db.execute(
        """SELECT name, sector, sector_other, city, stage, employee_count,
                  annual_revenue, main_goal, success_criteria,
                  website_url, social_media_url, business_reference_url
           FROM companies WHERE company_id=?""",
        (case["company_id"],)
    ).fetchone()
    company_context = "\n".join([
        f"- اسم الشركة: {company['name'] or '—'}",
        f"- القطاع: {company['sector_other'] or company['sector'] or '—'}",
        f"- المدينة: {company['city'] or '—'}",
        f"- المرحلة: {company['stage'] or '—'}",
        f"- عدد الموظفين: {company['employee_count'] or '—'}",
        f"- الإيراد السنوي المعلن: {company['annual_revenue'] or '—'}",
        f"- الهدف الرئيسي: {company['main_goal'] or '—'}",
        f"- معايير النجاح: {company['success_criteria'] or '—'}",
        f"- موقع الشركة: {company['website_url'] or 'غير مضاف'}",
        f"- رابط التواصل الاجتماعي: {company['social_media_url'] or 'غير مضاف'}",
        f"- رابط آخر يمثل النشاط: {company['business_reference_url'] or 'غير مضاف'}",
    ]) if company else "لا توجد بيانات شركة متاحة."

    allowed_decisions = "\n".join(
        f"- القرار المسموح: {f['decision_rule'].get('decision', f['title'])}\n"
        f"  سببه المسموح: {f['problem']}\n"
        f"  مصدره: {f['source']} / {f['version']}"
        for f in supported
    )
    system_prompt = """أنت طبقة عرض داخل سنع، ولست محرك تشخيص.
المحرك الحتمي أعطاك قرارًا مسموحًا أدناه. لا تغيّر عنوان القرار أو سببه،
ولا تضف KPI أو Benchmark أو سببًا أو حلًا غير موجود في المادة المعطاة.
مهمتك الوحيدة تحسين صياغة ملخص عربي قصير لنفس النتيجة.
أجب JSON فقط: {"presentation":"صياغة مختصرة أمينة للنتيجة"}."""

    user_prompt = f"""عنوان القضية: {case['case_title']}
المشكلة كما وُصفت: {case['declared_problem']}
السؤال الحقيقي: {case['real_question']}

بيانات الشركة ومصادرها الرقمية:
{company_context}

نتيجة محرك معرفة سنع — المصدر الملزم:
{allowed_decisions}

كل الأدلة المسجَّلة ({len(evidence_rows)}):
{evidence_text}

أصول الشركة:
{assets_list}

قيّم هذه القضية ككل الآن."""

    result = ask_sana_ai(system_prompt, user_prompt)
    if "error" in result:
        first = supported[0]
        deterministic = first["problem"]
        parsed = {"presentation": deterministic}
        ai_used = False
    else:
        try:
            parsed = json.loads(result["raw_text"])
            if not isinstance(parsed.get("presentation"), str) or not parsed["presentation"].strip():
                raise ValueError("missing presentation")
            ai_used = True
        except (json.JSONDecodeError, KeyError, ValueError):
            parsed = {"presentation": supported[0]["problem"]}
            ai_used = False
    first = supported[0]
    decision_title = first["decision_rule"].get("decision") or first["title"]
    decision_reason = first["problem"]
    db.execute("UPDATE cases SET ai_analysis=?, confidence_score=? WHERE case_id=?",
               (parsed["presentation"], None, case_id))
    db.commit()

    return jsonify({
        "success": True,
        "data": {
            "overall_assessment": parsed["presentation"],
            "confidence_score": None,
            "recommended_decision_title": decision_title,
            "recommended_reason": decision_reason,
            "presentation": parsed["presentation"],
        },
        "meta": {
            "source": "sana_knowledge",
            "ai_used": ai_used,
            "knowledge_status": diagnostic["status"],
            "diagnostic": diagnostic,
        }
    })


@app.route("/api/sop/suggest-step", methods=["POST"])
def sop_suggest_step():
    """SOP-BUILDER-003 — يُستدعى فقط عند ضغط 'ساعدني يا سنع' صراحةً، لا تلقائيًا."""
    import re as _re
    body = request.get_json(force=True)
    user_input     = (body.get("step_description") or "").strip()
    company_sector = (body.get("company_sector")   or "تجارة عامة").strip()

    if not user_input:
        return jsonify({"success": False, "error": "أدخل وصف الخطوة أولاً"}), 400

    system_prompt = """أنت مساعد يحوّل وصفًا حرًا لخطوة عمل واحدة إلى قائمة تحقق (Checklist)
قصيرة وعملية. أعد 3 إلى 5 عناصر فرعية فقط، كل عنصر جملة قصيرة تبدأ بفعل إجرائي.
لا تشرح، لا تُقدّم، أعد فقط قائمة مرقّمة بالعربية.
التزم بهذا التنسيق دائمًا:
1. السطر الأول: الخلاصة الأهم فقط، جملة واحدة مباشرة.
2. بعده: نقاط قصيرة (لا فقرات)، كل نقطة سطر واحد.
3. آخر سطر: توصية واحدة محددة فقط — لا تطرح أكثر من سؤال واحد، ولا تكرر السؤال الأصلي.
4. الحد الأقصى: 80 كلمة لتحليل دليل واحد، 150 كلمة لتحليل قضية كاملة. لا تشرح، لا تُقدّم، لا تضف تحفظات غير ضرورية.
5. ممنوع تكرار المعلومة نفسها بصياغتين."""

    user_prompt = (
        f"سياق الشركة: {company_sector}\n"
        f"الخطوة الموصوفة من المستخدم: \"{user_input}\"\n"
        f"اقترح عناصر Checklist لهذي الخطوة تحديدًا."
    )

    result = ask_sana_ai(system_prompt, user_prompt)
    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 502

    raw = result.get("raw_text", "")
    items = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        clean = _re.sub(r'^[\d١٢٣٤٥٦٧٨٩٠\.\-\)\s]+', '', line).strip()
        if clean:
            items.append(clean)

    return jsonify({"success": True, "items": items, "raw": raw})


@app.route("/api/system/health")
def system_health():
    """فحص بدون أي نداء فعلي لـ Claude (لا تكلفة، لا إنترنت لازم لهذا الفحص نفسه) —
    يتحقق فقط أن المكتبة مثبَّتة والمفتاح موجود، حتى تتأكد قبل إرسال الرابط لعميل محتمل."""
    checks = {"library_installed": False, "api_key_present": False}
    try:
        import anthropic  # noqa: F401
        checks["library_installed"] = True
    except ImportError:
        pass
    checks["api_key_present"] = bool(os.environ.get("ANTHROPIC_API_KEY"))
    checks["ai_ready"] = checks["library_installed"] and checks["api_key_present"]
    return jsonify({"success": True, "data": checks})


@app.route("/healthz")
def healthz():
    """فحص جاهزية بلا أسرار للموازن والمراقبة الخارجية."""
    try:
        db = get_db()
        db.execute("SELECT 1").fetchone()
    except Exception:
        response = jsonify({"status": "unavailable", "database": "unavailable"})
        response.status_code = 503
    else:
        response = jsonify({"status": "ok", "database": "ok"})
    response.headers["Cache-Control"] = "no-store"
    return response


# ------------------------------------------------------------------
# API — Tasks (إنجاز المهمة يسجل التنفيذ فقط؛ الدرجة تنتظر أثرًا موثقًا)
# ------------------------------------------------------------------

@app.route("/api/tasks/<task_id>/complete", methods=["POST"])
def complete_task(task_id):
    db = get_db()
    task = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if not task:
        return jsonify({"success": False, "error": "TASK_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(task["company_id"])
    if guard:
        return guard
    if task["status"] == "منجزة":
        return jsonify({"success": False, "error": "TASK_ALREADY_COMPLETED"}), 409

    # تحقق أن القرار المرتبط معتمد أو قيد التنفيذ قبل السماح بالإنجاز
    if task["decision_id"]:
        linked_decision = db.execute(
            "SELECT status FROM decisions WHERE decision_id=?", (task["decision_id"],)
        ).fetchone()
        if linked_decision and linked_decision["status"] not in ("معتمد", "قيد التنفيذ"):
            return jsonify({
                "success": False,
                "error": "DECISION_NOT_APPROVED",
                "message": "لا يمكن إنجاز هذه المهمة قبل اعتماد القرار المرتبط بها أولاً. افتح القضية واعتمد القرار قبل التنفيذ."
            }), 409

    cur = db.execute(
        "UPDATE tasks SET status='منجزة', completed_at=to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS') "
        "WHERE task_id=? AND status != 'منجزة'",
        (task_id,)
    )
    if cur.rowcount == 0:
        return jsonify({"success": False, "error": "TASK_ALREADY_COMPLETED"}), 409

    decision = None
    if task["decision_id"]:
        decision = db.execute(
            "SELECT * FROM decisions WHERE decision_id=?", (task["decision_id"],)
        ).fetchone()
        # تحقق صريح: company_id في القرار يجب أن يطابق company_id المهمة (= company الجلسة)
        # يمنع أي سيناريو يكون فيه task.decision_id يشير لقرار شركة أخرى
        if decision and decision["company_id"] != task["company_id"]:
            return jsonify({
                "success": False,
                "error": "FORBIDDEN",
                "message": "القرار المرتبط بهذه المهمة لا ينتمي لشركتك."
            }), 403

    db.commit()
    return jsonify({
        "success": True,
        "data": {
            "task_id": task_id,
            "status": "منجزة",
            "updated_assets": [],
            "asset_scores_changed": False,
            "impact_measurement_required": True,
            "asset_id": None,
            "new_asset_score": None,
        }
    })


@app.route("/api/tasks/<task_id>/p0-result", methods=["POST"])
def record_p0_task_result(task_id):
    """يسجل نتيجة P0 ومراجعة أثرها كسجل تاريخي واحد ثم يغلق المهمة ذريًا."""
    db = get_db()
    task = db.execute(
        """SELECT t.*, d.case_id, d.phase_label AS decision_phase_label,
                  d.status AS decision_status,
                  d.success_metric, d.scan_id
           FROM tasks t
           JOIN decisions d ON d.decision_id=t.decision_id
           WHERE t.task_id=? FOR UPDATE""",
        (task_id,),
    ).fetchone()
    if not task:
        return jsonify({"success": False, "error": "TASK_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(task["company_id"])
    if guard:
        return guard
    if task["decision_phase_label"] != "P0" or task["decision_status"] not in ("معتمد", "قيد التنفيذ"):
        return jsonify({
            "success": False,
            "error": "P0_APPROVED_DECISION_REQUIRED",
            "message": "تسجيل النتيجة متاح فقط لمهمة ناتجة عن قرار P0 معتمد.",
        }), 409
    if db.execute(
        "SELECT review_id FROM p0_impact_reviews WHERE task_id=?", (task_id,)
    ).fetchone():
        return jsonify({
            "success": False,
            "error": "P0_IMPACT_REVIEW_ALREADY_RECORDED",
            "message": "مراجعة الأثر محفوظة تاريخيًا ولا يمكن الكتابة فوقها.",
        }), 409

    body = request.get_json(silent=True) or {}
    result_summary = (body.get("result_summary") or "").strip()
    result_source_ref = (body.get("result_source_ref") or "").strip()
    impact_outcome = (body.get("impact_outcome") or "").strip().upper()
    impact_notes = (body.get("impact_notes") or "").strip()
    baseline_value = (body.get("baseline_value") or "").strip()
    baseline_source_ref = (body.get("baseline_source_ref") or "").strip()
    baseline_observed_at = (body.get("baseline_observed_at") or "").strip()
    target_value = (body.get("target_value") or "").strip()
    target_source_ref = (body.get("target_source_ref") or "").strip()
    target_observed_at = (body.get("target_observed_at") or "").strip()
    actual_value = (body.get("actual_value") or "").strip()
    actual_source_ref = (body.get("actual_source_ref") or result_source_ref).strip()
    actual_observed_at = (body.get("actual_observed_at") or "").strip()
    measurement_unit = (body.get("measurement_unit") or "").strip()
    kpi_direction = (body.get("kpi_direction") or "").strip().upper()
    baseline_evidence_id = (body.get("baseline_evidence_id") or "").strip()
    actual_evidence_id = (body.get("actual_evidence_id") or "").strip()
    if not all((result_summary, result_source_ref, impact_notes)):
        return jsonify({
            "success": False,
            "error": "P0_RESULT_FIELDS_REQUIRED",
            "message": "يلزم وصف النتيجة ومرجعها وحكم الأثر وملاحظات المراجعة.",
        }), 400
    if impact_outcome and impact_outcome not in {
        "IMPROVED", "UNCHANGED", "WORSE", "INCONCLUSIVE"
    }:
        return jsonify({"success": False, "error": "P0_IMPACT_OUTCOME_INVALID"}), 400
    if not all((
        baseline_value, baseline_source_ref, baseline_observed_at,
        target_value, target_source_ref, target_observed_at,
        actual_value, actual_source_ref, actual_observed_at,
    )):
        return jsonify({
            "success": False,
            "error": "P0_IMPACT_MEASURE_FIELDS_REQUIRED",
            "message": "يلزم baseline وtarget وactual مع مصدر وتاريخ لكل قياس.",
        }), 400
    try:
        baseline_date = date.fromisoformat(baseline_observed_at)
        target_date = date.fromisoformat(target_observed_at)
        actual_date = date.fromisoformat(actual_observed_at)
    except ValueError:
        return jsonify({
            "success": False,
            "error": "P0_IMPACT_MEASURE_DATE_INVALID",
            "message": "تواريخ القياس يجب أن تكون بصيغة YYYY-MM-DD.",
        }), 400
    if target_date < baseline_date or actual_date < baseline_date:
        return jsonify({
            "success": False,
            "error": "P0_IMPACT_MEASURE_ORDER_INVALID",
            "message": "لا يمكن أن يسبق تاريخ الهدف أو القياس الفعلي تاريخ الـBaseline.",
        }), 400
    if len(impact_notes) < 10:
        return jsonify({
            "success": False,
            "error": "P0_IMPACT_COMPARISON_RATIONALE_REQUIRED",
            "message": "اشرح بوضوح كيف قورنت النتيجة الفعلية بالـBaseline والهدف.",
        }), 400
    try:
        baseline_numeric = float(body.get("baseline_numeric"))
        target_numeric = float(body.get("target_numeric"))
        actual_numeric = float(body.get("actual_numeric"))
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "P0_IMPACT_NUMERIC_VALUES_REQUIRED",
            "message": "Baseline وTarget وActual يجب أن تكون قيمًا رقمية قابلة للمقارنة.",
        }), 400
    if not measurement_unit or kpi_direction not in {
        "HIGHER_IS_BETTER", "LOWER_IS_BETTER"
    }:
        return jsonify({
            "success": False,
            "error": "P0_IMPACT_KPI_DEFINITION_REQUIRED",
            "message": "يلزم تحديد وحدة واحدة واتجاه KPI قبل المقارنة.",
        }), 400
    measure_evidence = {}
    for role, evidence_id in (
        ("baseline", baseline_evidence_id), ("actual", actual_evidence_id)
    ):
        evidence = db.execute(
            """SELECT evidence_id,source_ref FROM evidence
               WHERE evidence_id=? AND company_id=? AND case_id=?
                 AND evidence_type IN ('Fact','Evidence')
                 AND BTRIM(COALESCE(source_ref,'')) <> ''""",
            (evidence_id, task["company_id"], task["case_id"]),
        ).fetchone()
        if not evidence:
            return jsonify({
                "success": False,
                "error": "P0_IMPACT_EVIDENCE_INVALID",
                "message": f"مصدر {role} يجب أن يكون دليلًا محفوظًا ومؤهلًا لنفس الشركة والقضية.",
            }), 400
        measure_evidence[role] = evidence
    baseline_source_ref = measure_evidence["baseline"]["source_ref"]
    actual_source_ref = measure_evidence["actual"]["source_ref"]
    result_source_ref = actual_source_ref
    target_source_ref = f"DECISION:{task['decision_id']}"
    baseline_value = f"{baseline_numeric:g} {measurement_unit}"
    target_value = f"{target_numeric:g} {measurement_unit}"
    actual_value = f"{actual_numeric:g} {measurement_unit}"
    if actual_numeric == baseline_numeric:
        impact_outcome = "UNCHANGED"
    elif (
        (kpi_direction == "HIGHER_IS_BETTER" and actual_numeric > baseline_numeric)
        or (kpi_direction == "LOWER_IS_BETTER" and actual_numeric < baseline_numeric)
    ):
        impact_outcome = "IMPROVED"
    else:
        impact_outcome = "WORSE"

    account = current_account()
    reviewed_by = account["account_id"] if account else None
    if not reviewed_by:
        return jsonify({"success": False, "error": "AUTHENTICATION_REQUIRED"}), 401

    baseline_snapshot = {
        "success_metric": task["success_metric"],
        "scan_id": task["scan_id"],
    }
    if task["scan_id"]:
        scan_row = db.execute(
            "SELECT result FROM scan_runs WHERE scan_id=? AND company_id=? AND case_id=?",
            (task["scan_id"], task["company_id"], task["case_id"]),
        ).fetchone()
        if scan_row:
            try:
                baseline_snapshot["diagnostic_baseline"] = (
                    json.loads(scan_row["result"]).get("diagnostic_baseline") or {}
                )
            except (TypeError, json.JSONDecodeError):
                baseline_snapshot["diagnostic_baseline"] = {}

    review_id = "P0R-" + secrets.token_hex(6).upper()
    try:
        db.execute(
            """INSERT INTO p0_impact_reviews
               (review_id,company_id,case_id,decision_id,task_id,
                baseline_value,baseline_source_ref,baseline_observed_at,
                target_value,target_source_ref,target_observed_at,
                actual_value,actual_source_ref,actual_observed_at,
                baseline_numeric,target_numeric,actual_numeric,measurement_unit,
                kpi_direction,baseline_evidence_id,actual_evidence_id,
                baseline_snapshot_json,result_summary,result_source_ref,
                impact_outcome,impact_notes,reviewed_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                review_id, task["company_id"], task["case_id"], task["decision_id"],
                task_id,
                baseline_value, baseline_source_ref, baseline_observed_at,
                target_value, target_source_ref, target_observed_at,
                actual_value, actual_source_ref, actual_observed_at,
                baseline_numeric, target_numeric, actual_numeric, measurement_unit,
                kpi_direction, baseline_evidence_id, actual_evidence_id,
                json.dumps(baseline_snapshot, ensure_ascii=False),
                result_summary, result_source_ref, impact_outcome, impact_notes,
                reviewed_by,
            ),
        )
        db.execute(
            """UPDATE tasks
               SET status='منجزة',
                   completed_at=to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'),
                   value_note=?, updated_at=now()
               WHERE task_id=?""",
            (result_summary, task_id),
        )
        db.execute(
            """INSERT INTO execution_task_audit
               (audit_id,company_id,task_id,actor_id,from_status,to_status,
                change_json,source_ref) VALUES (?,?,?,?,?,?,?,?)""",
            (
                "TA-" + secrets.token_hex(8).upper(), task["company_id"], task_id,
                reviewed_by, task["status"], "منجزة",
                json.dumps({"status": "منجزة", "impact_review": review_id},
                           ensure_ascii=False),
                "p0-impact-review",
            ),
        )
        from sana_company_memory import record_learning
        record_learning(
            db, task["company_id"], case_id=task["case_id"],
            decision_id=task["decision_id"], task_id=task_id, result_ref=review_id,
            changed=[result_summary] if impact_outcome == "IMPROVED" else [],
            unchanged=[result_summary] if impact_outcome == "UNCHANGED" else [],
            hypothesis_correct=(
                True if impact_outcome == "IMPROVED" else
                False if impact_outcome == "WORSE" else None
            ),
            decision_useful=(
                True if impact_outcome == "IMPROVED" else
                False if impact_outcome == "WORSE" else None
            ),
            execution_complete=True, remember=impact_notes,
            source_ref=result_source_ref, owner_id=reviewed_by,
        )
        db.commit()
    except Exception:
        db.rollback()
        app.logger.exception("P0 result recording failed: task_id=%s", task_id)
        return jsonify({
            "success": False,
            "error": "P0_RESULT_RECORDING_FAILED",
            "message": "تعذر حفظ النتيجة؛ لم تُغلق المهمة.",
        }), 500

    return jsonify({
        "success": True,
        "data": {
            "review_id": review_id,
            "task_id": task_id,
            "status": "منجزة",
            "impact_outcome": impact_outcome,
            "asset_scores_changed": False,
        },
    }), 201


# ------------------------------------------------------------------
# دورة الإثبات: تقديم إثبات + التحقق بالذكاء الاصطناعي + تطبيق التأثير
# ------------------------------------------------------------------

def verify_task_evidence(task_id, evidence_id):
    """
    تستدعي Claude بسؤال ضيق النطاق: هل الإثبات مرتبط منطقياً بإنجاز المهمة؟
    تحدّث task_evidence مباشرةً وتُرجع (verification_status, verification_reason).
    """
    db = get_db()

    evidence = db.execute(
        "SELECT te.*, t.title AS task_title, t.company_id AS task_company_id "
        "FROM task_evidence te JOIN tasks t ON te.task_id = t.task_id "
        "WHERE te.evidence_id=?", (evidence_id,)
    ).fetchone()
    if not evidence:
        return None, "EVIDENCE_NOT_FOUND"

    # عزل الشركة — تحقق مزدوج
    if evidence["task_company_id"] != evidence["company_id"]:
        return None, "COMPANY_MISMATCH"

    result = ask_sana_ai(
        system_prompt=(
            "أنت محكّم دقيق. مهمتك فقط: تحديد ما إذا كان الإثبات المقدَّم "
            "مرتبطاً منطقياً بإنجاز المهمة المذكورة. "
            "أجب بسطر واحد فقط بهذا الشكل الصارم:\n"
            "مرتبط: <سبب في جملة واحدة>\n"
            "أو:\n"
            "غير مرتبط: <سبب في جملة واحدة>\n"
            "لا تُضف أي شيء آخر. لا تحكم على الجودة أو الكفاءة."
        ),
        user_prompt=(
            f"المهمة: {evidence['task_title']}\n"
            f"الإثبات المقدَّم: {evidence['evidence_content']}"
        ),
    )

    if "error" in result:
        return None, result["error"]

    raw = result.get("raw_text", "").strip()
    if raw.startswith("مرتبط"):
        status = "مرتبط"
        reason = raw[len("مرتبط"):].lstrip(":").strip()
    elif raw.startswith("غير مرتبط"):
        status = "غير مرتبط"
        reason = raw[len("غير مرتبط"):].lstrip(":").strip()
    else:
        # استجابة غير متوقعة — نحفظها كما هي ونعدّها غير محددة
        status = "لم يُتحقق"
        reason = raw[:200]

    db.execute(
        "UPDATE task_evidence SET verification_status=?, verification_reason=?, "
        "verified_at=to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS') "
        "WHERE evidence_id=?",
        (status, reason, evidence_id)
    )
    db.commit()
    return status, reason


def apply_verified_impact(task_id, evidence_id):
    """
    Idempotent — تُطبَّق مرة واحدة فقط لكل evidence_id.
    تقرأ expected_asset_impact من المهمة وتضيفه للأصل المستهدف.
    تُرجع dict بتفاصيل التحديث أو رسالة خطأ.
    """
    import json as _json
    db = get_db()

    evidence = db.execute(
        "SELECT te.*, t.expected_asset_impact, t.company_id AS task_company_id, t.title AS task_title "
        "FROM task_evidence te JOIN tasks t ON te.task_id = t.task_id "
        "WHERE te.evidence_id=?", (evidence_id,)
    ).fetchone()
    if not evidence:
        return {"error": "EVIDENCE_NOT_FOUND"}
    if evidence["task_id"] != task_id:
        return {"error": "TASK_EVIDENCE_MISMATCH"}
    if evidence["task_company_id"] != evidence["company_id"]:
        return {"error": "COMPANY_MISMATCH"}
    if evidence["verification_status"] != "مرتبط":
        return {"error": "NOT_VERIFIED", "message": "التأثير يُطبَّق فقط بعد التحقق بنتيجة 'مرتبط'"}
    if evidence["impact_applied"]:
        return {"error": "ALREADY_APPLIED", "message": "تم تطبيق التأثير مسبقاً لهذا الإثبات"}
    measured_impact = db.execute(
        """SELECT review_id,result_source_ref FROM p0_impact_reviews
           WHERE task_id=? AND company_id=? AND impact_outcome='IMPROVED'
           LIMIT 1""",
        (task_id, evidence["task_company_id"]),
    ).fetchone()
    if not measured_impact:
        return {
            "error": "IMPACT_MEASUREMENT_REQUIRED",
            "message": "ارتباط إثبات بالمهمة لا يثبت تحسن الأصل؛ يلزم Before/After موثق.",
        }

    raw_impact = evidence.get("expected_asset_impact")
    if not raw_impact:
        return {"error": "NO_IMPACT_DEFINED", "message": "المهمة لا تحمل expected_asset_impact"}

    try:
        impact_data = _json.loads(raw_impact)
        asset_id = impact_data["asset_id"]
        requested_impact = int(impact_data["score_impact"])
    except Exception:
        return {"error": "INVALID_IMPACT_FORMAT"}

    # تحقق أن الأصل ينتمي لنفس الشركة
    asset = db.execute(
        "SELECT * FROM assets WHERE asset_id=? AND company_id=?",
        (asset_id, evidence["task_company_id"])
    ).fetchone()
    if not asset:
        return {"error": "ASSET_NOT_FOUND_OR_FORBIDDEN"}

    score_impact = max(-2, min(2, requested_impact))
    previous_score = asset["current_score"]
    new_score = min(100, previous_score + score_impact)

    db.execute(
        "UPDATE assets SET current_score=?, updated_at=now() WHERE asset_id=?",
        (new_score, asset_id)
    )
    db.execute(
        "UPDATE task_evidence SET impact_applied=1 WHERE evidence_id=?",
        (evidence_id,)
    )
    db.commit()

    return {
        "asset_id": asset_id,
        "asset_name": asset["asset_name"],
        "previous_score": previous_score,
        "new_score": new_score,
        "score_impact_applied": score_impact,
        "evidence_id": evidence_id,
        "task_id": task_id,
        "impact_review_id": measured_impact["review_id"],
        "score_change_reason": (
            "تغيير محدود بعد Before/After موثق بالمراجعة "
            f"{measured_impact['review_id']} والمصدر {measured_impact['result_source_ref']}."
        )
    }


@app.route("/api/tasks/<task_id>/evidence", methods=["POST"])
def submit_task_evidence(task_id):
    """
    تقديم إثبات إنجاز لمهمة + استدعاء التحقق بالذكاء الاصطناعي فوراً.
    Body: { evidence_type, evidence_content }
    """
    db = get_db()
    task = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if not task:
        return jsonify({"success": False, "error": "TASK_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(task["company_id"])
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    evidence_content = (data.get("evidence_content") or "").strip()
    evidence_type = (data.get("evidence_type") or "نص وصفي").strip()
    if not evidence_content:
        return jsonify({"success": False, "error": "MISSING_EVIDENCE_CONTENT"}), 400

    evidence_id = f"EV-{secrets.token_hex(6).upper()}"
    db.execute(
        "INSERT INTO task_evidence "
        "(evidence_id, task_id, company_id, evidence_type, evidence_content) "
        "VALUES (?,?,?,?,?)",
        (evidence_id, task_id, task["company_id"], evidence_type, evidence_content)
    )
    db.commit()

    # استدعاء التحقق فوراً
    v_status, v_reason = verify_task_evidence(task_id, evidence_id)

    return jsonify({
        "success": True,
        "data": {
            "evidence_id": evidence_id,
            "task_id": task_id,
            "evidence_type": evidence_type,
            "verification_status": v_status,
            "verification_reason": v_reason
        }
    })


@app.route("/api/tasks/<task_id>/evidence/<evidence_id>/apply-impact", methods=["POST"])
def apply_task_impact(task_id, evidence_id):
    """
    تطبيق التأثير المتوقع على الأصل بعد التحقق. Idempotent.
    """
    task = get_db().execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if not task:
        return jsonify({"success": False, "error": "TASK_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(task["company_id"])
    if guard:
        return guard

    result = apply_verified_impact(task_id, evidence_id)
    if "error" in result:
        code = 409 if result["error"] in ("ALREADY_APPLIED", "NOT_VERIFIED") else 400
        return jsonify({"success": False, **result}), code

    return jsonify({"success": True, "data": result})


# ------------------------------------------------------------------
# حزم المهام (Task Packs) — قابلة لإعادة الاستخدام عبر أي شركة/قطاع
# ------------------------------------------------------------------

@app.route("/api/task-packs")
def list_task_packs():
    db = get_db()
    account = current_account()
    company = db.execute("SELECT sector, sector_other FROM companies WHERE company_id=?",
                         (account["company_id"],)).fetchone() if account else None
    raw_sector = " ".join(str(company[k] or "") for k in ("sector", "sector_other")).lower() if company else ""
    is_legal = "legal" in raw_sector or "قانون" in raw_sector
    # الحزمة القانونية لا تظهر إلا للشركة القانونية. الحزمة العامة تبقى متاحة للجميع.
    packs = db.execute("SELECT * FROM task_packs ORDER BY sector, name").fetchall()
    result = []
    for pack in packs:
        if pack["sector"] == "قانوني" and not is_legal:
            continue
        if pack["sector"] not in ("عام", "قانوني") and pack["sector"] and pack["sector"].lower() not in raw_sector:
            continue
        count = db.execute(
            "SELECT COUNT(*) as c FROM task_pack_items WHERE pack_id=?", (pack["pack_id"],)
        ).fetchone()
        result.append({
            "pack_id": pack["pack_id"], "name": pack["name"], "sector": pack["sector"],
            "description": pack["description"], "item_count": count["c"],
        })
    return jsonify({"success": True, "data": result})


@app.route("/api/companies/<company_id>/tasks/apply-pack", methods=["POST"])
def apply_task_pack(company_id):
    """⚠ يُنشئ قرارًا معتمدًا + مهمة + أثر أصل واحد لكل بند في الحزمة — لا يكرر بندًا موجودًا مسبقًا بنفس العنوان."""
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard

    data = request.get_json(force=True, silent=True) or {}
    pack_id = data.get("pack_id")
    if not pack_id:
        return jsonify({"success": False, "error": "PACK_ID_REQUIRED"}), 400

    db = get_db()
    company = db.execute("SELECT company_id FROM companies WHERE company_id=?", (company_id,)).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404

    items = db.execute(
        "SELECT * FROM task_pack_items WHERE pack_id=? ORDER BY sort_order ASC", (pack_id,)
    ).fetchall()
    if not items:
        return jsonify({"success": False, "error": "PACK_NOT_FOUND_OR_EMPTY"}), 404

    case = db.execute("SELECT case_id FROM cases WHERE company_id=? LIMIT 1", (company_id,)).fetchone()
    case_id = case["case_id"] if case else None

    assets = {
        row["asset_type"]: row["asset_id"]
        for row in db.execute("SELECT asset_id, asset_type FROM assets WHERE company_id=?", (company_id,)).fetchall()
    }

    import uuid
    created, skipped = 0, 0
    for item in items:
        asset_id = assets.get(item["asset_type"])
        if not asset_id:
            skipped += 1
            continue
        existing = db.execute(
            "SELECT task_id FROM tasks WHERE company_id=? AND title=?", (company_id, item["title"])
        ).fetchone()
        if existing:
            skipped += 1
            continue

        decision_id = "D" + uuid.uuid4().hex[:6].upper()
        db.execute(
            """INSERT INTO decisions
               (decision_id, company_id, case_id, asset_id, title, recommended_action,
                reason, confidence_score, expected_impact, status, phase_label)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (decision_id, company_id, case_id, asset_id, item["title"], item["title"],
             item["detail"] or "بند من حزمة مهام مُطبَّقة", 90,
             f"رفع أصل {item['asset_type']} بمقدار {item['score_impact']} نقاط", "معتمد",
             item["category_label"])
        )
        task_id = "T" + uuid.uuid4().hex[:6].upper()
        db.execute(
            """INSERT INTO tasks (task_id, company_id, decision_id, title, status, phase_label)
               VALUES (?,?,?,?,?,?)""",
            (task_id, company_id, decision_id, item["title"], "لم تبدأ", item["category_label"])
        )
        impact_id = "IMP-" + uuid.uuid4().hex[:8].upper()
        db.execute(
            """INSERT INTO decision_asset_impacts (impact_id, decision_id, asset_id, score_impact, is_primary)
               VALUES (?,?,?,?,?)""",
            (impact_id, decision_id, asset_id, item["score_impact"], 1)
        )
        created += 1

    db.commit()
    return jsonify({"success": True, "data": {"created": created, "skipped": skipped}})


@app.route("/api/companies/<company_id>/tasks/board")
def tasks_board(company_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard

    db = get_db()
    tasks = db.execute(
        "SELECT * FROM tasks WHERE company_id=? ORDER BY phase_label, created_at ASC", (company_id,)
    ).fetchall()

    groups = {}
    total, done = 0, 0
    for t in tasks:
        label = t["phase_label"] or "مهام عامة"
        groups.setdefault(label, []).append({
            "task_id": t["task_id"], "title": t["title"], "status": t["status"],
            "completed_at": t["completed_at"],
            "owner_user_id": t["owner_user_id"],
            "approver_user_id": t["approver_user_id"],
            "deadline": t["due_date"],
            "kpi": t["kpi"],
            "delay_reason": t["delay_reason"],
            "owner_account_id": (
                db.execute(
                    """SELECT account_id FROM execution_owner_bindings
                       WHERE company_id=? AND owner_id=?""",
                    (company_id, t["owner_user_id"]),
                ).fetchone() or {}
            ).get("account_id") if t["owner_user_id"] else None,
        })
        total += 1
        if t["status"] == "منجزة":
            done += 1

    return jsonify({
        "success": True,
        "data": {"groups": groups, "total": total, "done": done,
                  "pct": round((done / total) * 100) if total else 0}
    })


@app.route("/company/<company_id>/tasks-board")
def tasks_board_page(company_id):
    alias = _client_only_alias(url_for("ceo_home"))
    if alias:
        return alias
    return render_template("12-tasks-board.html", company_id=company_id)


# ------------------------------------------------------------------
# Sana Score — طبقة "لماذا هذه الدرجة؟" و"ماذا لو نفّذت القرارات المعتمدة؟"
# مبنية بالكامل فوق بيانات موجودة أصلاً (decision_asset_impacts + حالة المهام)
# — لا جداول جديدة، لا "معرفة مشتركة بين الشركات".
# ------------------------------------------------------------------

@app.route("/api/companies/<company_id>/score-explanation")
def score_explanation(company_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard

    db = get_db()
    assets = db.execute("SELECT * FROM assets WHERE company_id=?", (company_id,)).fetchall()
    if not assets:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND_OR_NO_ASSETS"}), 404

    current_score = round(sum(a["current_score"] for a in assets) / len(assets))

    # "لماذا؟" — آخر 5 تأثيرات فعلية نتجت عن مهام مُنجزة فعلًا (لا افتراضية)
    recent_rows = db.execute(
        """SELECT a.asset_type, a.asset_name, d.title, dai.score_impact, t.completed_at
           FROM decision_asset_impacts dai
           JOIN decisions d ON d.decision_id = dai.decision_id
           JOIN tasks t ON t.decision_id = d.decision_id
           JOIN assets a ON a.asset_id = dai.asset_id
           WHERE d.company_id=? AND t.status='منجزة'
           ORDER BY t.completed_at DESC LIMIT 5""",
        (company_id,)
    ).fetchall()
    recent_changes = [
        {
            "asset_name": _client_asset_view(r)["asset_name"],
            "client_asset_name": _client_asset_view(r)["client_asset_name"],
            "title": r["title"],
            "impact": r["score_impact"],
            "completed_at": r["completed_at"],
        }
        for r in recent_rows
    ]

    # "ماذا لو؟" — قرارات معتمدة لكن مهمتها لم تُنجز بعد (أثر متوقَّع، لا مضمون)
    pending_rows = db.execute(
        """SELECT a.asset_id, a.asset_type, a.asset_name, d.title, dai.score_impact
           FROM decision_asset_impacts dai
           JOIN decisions d ON d.decision_id = dai.decision_id
           JOIN tasks t ON t.decision_id = d.decision_id
           JOIN assets a ON a.asset_id = dai.asset_id
           WHERE d.company_id=? AND d.status='معتمد' AND t.status!='منجزة'""",
        (company_id,)
    ).fetchall()

    pending_by_asset = {}
    pending_list = []
    for r in pending_rows:
        pending_by_asset[r["asset_id"]] = pending_by_asset.get(r["asset_id"], 0) + r["score_impact"]
        client_asset = _client_asset_view(r)
        pending_list.append({
            "asset_name": client_asset["asset_name"],
            "client_asset_name": client_asset["client_asset_name"],
            "title": r["title"],
            "impact": r["score_impact"],
        })

    projected_scores = []
    for a in assets:
        added = pending_by_asset.get(a["asset_id"], 0)
        projected_scores.append(min(100, a["current_score"] + added))
    projected_score = round(sum(projected_scores) / len(projected_scores)) if projected_scores else current_score

    return jsonify({
        "success": True,
        "data": {
            "current_score": current_score,
            "recent_changes": recent_changes,
            "pending_decisions": pending_list,
            "projected_score": projected_score,
            "projected_delta": projected_score - current_score,
        }
    })


# ------------------------------------------------------------------
# B6 — Sales CRM
# ------------------------------------------------------------------

SALES_STAGES = ["عميل محتمل", "مؤهل", "عرض مرسل", "تفاوض", "فوز", "خسارة"]
ACTIVE_STAGES = {"عميل محتمل", "مؤهل", "عرض مرسل", "تفاوض"}
ACTIVITY_TYPES = {"مكالمة", "اجتماع", "رسالة", "عرض", "ملاحظة"}


# ------------------------------------------------------------------
# Zubair Deal Brain — Private Beta
# لا يُفتح وضع المعاينة العام لهذه التجربة؛ يلزم حساب user_accounts
# معلّمًا إداريًا (is_admin=1) أو حسابًا مضافًا صراحةً للإطلاق الخاص.
# ------------------------------------------------------------------

def _zubair_beta_guard():
    account = current_account()
    if not account:
        return jsonify({"success": False, "error": "UNAUTHORIZED"}), 401
    db = get_db()
    row = db.execute(
        "SELECT is_admin, email FROM user_accounts WHERE account_id=?",
        (account["account_id"],),
    ).fetchone()
    allowed_ids = {
        value.strip() for value in os.environ.get("ZUBAIR_BETA_ACCOUNT_IDS", "").split(",")
        if value.strip()
    }
    allowed_emails = {
        value.strip().lower() for value in os.environ.get("ZUBAIR_BETA_ALLOWED_EMAILS", "").split(",")
        if value.strip()
    }
    allowed = bool(
        row and (
            row["is_admin"]
            or account["account_id"] in allowed_ids
            or (row["email"] or "").lower() in allowed_emails
        )
    )
    if not allowed:
        return jsonify({
            "success": False, "error": "ZUBAIR_BETA_FORBIDDEN",
            "message": "هذه التجربة الخاصة متاحة لحساب المؤسس/المشرف المسموح فقط.",
        }), 403
    return None


def _zubair_company():
    account = current_account()
    return account["company_id"] if account else None


@app.route("/zubair/deal-brain")
def zubair_deal_brain_page():
    guard = _zubair_beta_guard()
    if guard:
        return redirect(url_for("login", next=request.full_path)) if guard[1] == 401 else guard
    return render_template(
        "23-zubair-deal-brain.html",
        default_company_id=_zubair_company(),
    )


def _zubair_json_or_form():
    return request.get_json(silent=True) or request.form.to_dict(flat=True)


@app.route("/api/zubair/deal-brain")
def zubair_deal_brain_overview():
    guard = _zubair_beta_guard()
    if guard:
        return guard
    from zubair_deal_brain import (
        ensure_schema, metrics, attention_items, timeline, review_packet,
    )
    db = get_db()
    ensure_schema(db)
    company_id = _zubair_company()
    return jsonify({
        "success": True,
        "data": {
            "name": "Zubair Deal Brain — Private Beta",
            "company_id": company_id,
            "metrics": metrics(db, company_id),
            "attention": attention_items(db, company_id),
            "timeline": timeline(db, company_id),
            "review": review_packet(db, company_id),
            "policy": {
                "opportunities_source_of_truth": True,
                "pattern_promotion": "manual review required",
                "unknown_policy": "غير المذكور يبقى Unknown",
                "access_scope": "founder-and-authorized-admin-only",
                "automatic_pattern_promotion": False,
            },
        },
    })


@app.route("/api/zubair/deal-brain/captures", methods=["POST"])
def zubair_capture():
    guard = _zubair_beta_guard()
    if guard:
        return guard
    from zubair_deal_brain import ensure_schema, create_draft
    db = get_db()
    ensure_schema(db)
    body = _zubair_json_or_form()
    input_type = (body.get("input_type") or "text").strip().lower()
    if input_type not in {"text", "audio", "image", "file"}:
        return jsonify({"success": False, "error": "INVALID_INPUT_TYPE"}), 400
    raw_text = (body.get("raw_text") or body.get("transcript") or "").strip()
    attachments = []
    for uploaded in request.files.getlist("attachments"):
        content = uploaded.read()
        if len(content) > 10 * 1024 * 1024:
            return jsonify({"success": False, "error": "ATTACHMENT_TOO_LARGE"}), 413
        attachments.append({
            "filename": uploaded.filename or "attachment",
            "mime_type": uploaded.mimetype,
            "content": content,
        })
    if not raw_text and not attachments:
        return jsonify({"success": False, "error": "CAPTURE_REQUIRED",
                        "message": "اكتب ما حدث أو أرفق تسجيلًا/ملفًا."}), 400
    account = current_account()
    draft, duplicate = create_draft(
        db, _zubair_company(), account["account_id"], input_type, raw_text,
        attachments, ask_sana_ai if input_type == "text" else None,
    )
    db.commit()
    return jsonify({
        "success": True, "duplicate": duplicate, "data": draft,
        "message": "هذه المسودة موجودة مسبقًا." if duplicate else "تم إعداد المسودة للمراجعة.",
    }), 200 if duplicate else 201


@app.route("/api/zubair/deal-brain/drafts/<draft_id>")
def zubair_draft(draft_id):
    guard = _zubair_beta_guard()
    if guard:
        return guard
    from zubair_deal_brain import ensure_schema, get_draft
    db = get_db()
    ensure_schema(db)
    draft = get_draft(db, _zubair_company(), draft_id)
    if not draft:
        return jsonify({"success": False, "error": "DRAFT_NOT_FOUND"}), 404
    return jsonify({"success": True, "data": draft})


@app.route("/api/zubair/deal-brain/drafts/<draft_id>/match", methods=["POST"])
def zubair_match_draft(draft_id):
    guard = _zubair_beta_guard()
    if guard:
        return guard
    from zubair_deal_brain import ensure_schema, match_draft
    db = get_db()
    ensure_schema(db)
    try:
        result = match_draft(db, _zubair_company(), draft_id)
    except LookupError as exc:
        return jsonify({"success": False, "error": str(exc)}), 404
    return jsonify({"success": True, "data": result})


@app.route("/api/zubair/deal-brain/drafts/<draft_id>/confirm", methods=["POST"])
def zubair_confirm_draft(draft_id):
    guard = _zubair_beta_guard()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True:
        return jsonify({
            "success": False, "error": "CONFIRMATION_REQUIRED",
            "message": "لن يُحفظ أي تحديث تجاري قبل التأكيد الصريح.",
        }), 400
    from zubair_deal_brain import ensure_schema, confirm_draft
    db = get_db()
    ensure_schema(db)
    account = current_account()
    try:
        result = confirm_draft(
            db, _zubair_company(), account["account_id"], draft_id,
            edits=body.get("fields") or body.get("edits") or {},
            selections=body.get("selections") or {},
            create_opportunity=bool(body.get("create_opportunity")),
            attach_evidence=body.get("attach_evidence", True),
        )
        db.commit()
        return jsonify({"success": True, "data": result})
    except LookupError as exc:
        db.rollback()
        return jsonify({"success": False, "error": str(exc)}), 404
    except ValueError as exc:
        db.rollback()
        status = 409 if str(exc) == "MATCH_SELECTION_REQUIRED" else 400
        return jsonify({"success": False, "error": str(exc)}), status
    except Exception:
        db.rollback()
        raise


@app.route("/api/zubair/deal-brain/attention")
def zubair_attention():
    guard = _zubair_beta_guard()
    if guard:
        return guard
    from zubair_deal_brain import ensure_schema, attention_items
    db = get_db()
    ensure_schema(db)
    return jsonify({"success": True, "data": attention_items(db, _zubair_company())})


@app.route("/api/zubair/deal-brain/metrics")
def zubair_metrics():
    guard = _zubair_beta_guard()
    if guard:
        return guard
    from zubair_deal_brain import ensure_schema, metrics
    db = get_db()
    ensure_schema(db)
    return jsonify({"success": True, "data": metrics(db, _zubair_company())})


@app.route("/api/zubair/deal-brain/timeline")
def zubair_timeline():
    guard = _zubair_beta_guard()
    if guard:
        return guard
    from zubair_deal_brain import ensure_schema, timeline
    db = get_db()
    ensure_schema(db)
    return jsonify({
        "success": True,
        "data": timeline(db, _zubair_company(), request.args.get("opp_id")),
    })


@app.route(
    "/api/zubair/deal-brain/review",
    methods=["GET", "POST"],
)
@app.route(
    "/api/zubair/deal-brain/reviews",
    methods=["GET", "POST"],
)
def zubair_experiment_review():
    guard = _zubair_beta_guard()
    if guard:
        return guard
    from zubair_deal_brain import ensure_schema, review_packet, save_review
    db = get_db()
    ensure_schema(db)
    company_id = _zubair_company()
    if request.method == "GET":
        return jsonify({"success": True, "data": review_packet(db, company_id)})
    body = request.get_json(silent=True) or {}
    account = current_account()
    try:
        review = save_review(
            db, company_id, account["account_id"],
            body.get("decision"), body.get("rationale"),
            body.get("audit"), body.get("window_start"),
            body.get("window_end"),
        )
        db.commit()
        return jsonify({
            "success": True,
            "data": review,
            "message": "تم توثيق القرار اليدوي. بقي نطاق الوصول خاصًا ولم يحدث رفعًا تلقائيًا إلى Pattern.",
        }), 201
    except ValueError as exc:
        db.rollback()
        return jsonify({"success": False, "error": str(exc)}), 400


def _opp_or_404(db, opp_id):
    o = db.execute("SELECT * FROM opportunities WHERE opp_id=?", (opp_id,)).fetchone()
    if not o:
        return None, jsonify({"success": False, "error": "OPP_NOT_FOUND"}), 404
    return o, None, None


@app.route("/sales")
def sales_pipeline_page():
    account = current_account()
    cid = account["company_id"] if account else default_company_id()
    return render_template("17-sales-pipeline.html", default_company_id=cid)


# ── Leads ──────────────────────────────────────────────────────────

@app.route("/api/companies/<company_id>/leads", methods=["GET"])
def list_leads(company_id):
    guard = enforce_entity_company_scope(company_id)
    if guard: return guard
    db = get_db()
    rows = db.execute(
        "SELECT * FROM leads WHERE company_id=? ORDER BY created_at DESC", (company_id,)
    ).fetchall()
    return jsonify({"success": True, "data": [dict(r) for r in rows]})


@app.route("/api/companies/<company_id>/leads", methods=["POST"])
def create_lead(company_id):
    guard = enforce_entity_company_scope(company_id)
    if guard: return guard
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "NAME_REQUIRED"}), 400
    import uuid
    lead_id = f"L-{uuid.uuid4().hex[:10].upper()}"
    db = get_db()
    db.execute(
        """INSERT INTO leads
           (lead_id, company_id, name, company_name, email, phone,
            source, service_interest, status, owner_id, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (lead_id, company_id, name,
         body.get("company_name"), body.get("email"), body.get("phone"),
         body.get("source"), body.get("service_interest"),
         body.get("status", "جديد"), body.get("owner_id"), body.get("notes"))
    )
    db.commit()
    return jsonify({"success": True, "data": {"lead_id": lead_id}}), 201


# ── Opportunities ──────────────────────────────────────────────────

@app.route("/api/companies/<company_id>/opportunities", methods=["GET"])
def list_opportunities(company_id):
    guard = enforce_entity_company_scope(company_id)
    if guard: return guard
    db = get_db()
    stage_filter = request.args.get("stage")
    if stage_filter:
        rows = db.execute(
            "SELECT * FROM opportunities WHERE company_id=? AND archived=0 AND stage=? ORDER BY created_at DESC",
            (company_id, stage_filter)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM opportunities WHERE company_id=? AND archived=0 ORDER BY created_at DESC",
            (company_id,)
        ).fetchall()
    return jsonify({"success": True, "data": [dict(r) for r in rows]})


@app.route("/api/companies/<company_id>/opportunities", methods=["POST"])
def create_opportunity(company_id):
    guard = enforce_entity_company_scope(company_id)
    if guard: return guard
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"success": False, "error": "TITLE_REQUIRED"}), 400
    stage = body.get("stage", "عميل محتمل")
    if stage not in SALES_STAGES:
        return jsonify({"success": False, "error": "INVALID_STAGE",
                        "valid": SALES_STAGES}), 400
    import uuid
    opp_id = f"OPP-{uuid.uuid4().hex[:10].upper()}"
    account = current_account()
    actor = account["account_id"] if account else "system"
    db = get_db()
    db.execute(
        """INSERT INTO opportunities
           (opp_id, company_id, lead_id, title, stage, amount, probability,
            expected_close_date, next_action, next_action_due, owner_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (opp_id, company_id, body.get("lead_id"), title, stage,
         body.get("amount"), body.get("probability"),
         body.get("expected_close_date"),
         body.get("next_action"), body.get("next_action_due"),
         body.get("owner_id"))
    )
    # سجّل في تاريخ المراحل
    db.execute(
        """INSERT INTO sales_stage_history (history_id, opp_id, company_id, from_stage, to_stage, changed_by)
           VALUES (?,?,?,NULL,?,?)""",
        (f"SSH-{uuid.uuid4().hex[:10].upper()}", opp_id, company_id, stage, actor)
    )
    from sana_revenue_cycle import record_legacy_transition
    created_opp = db.execute(
        "SELECT * FROM opportunities WHERE opp_id=? AND company_id=?",
        (opp_id, company_id),
    ).fetchone()
    record_legacy_transition(db, dict(created_opp), stage, actor)
    db.commit()
    return jsonify({"success": True, "data": {"opp_id": opp_id}}), 201


@app.route("/api/opportunities/<opp_id>", methods=["GET"])
def get_opportunity(opp_id):
    db = get_db()
    opp, err, code = _opp_or_404(db, opp_id)
    if err: return err, code
    guard = enforce_entity_company_scope(opp["company_id"])
    if guard: return guard
    activities = db.execute(
        "SELECT * FROM sales_activities WHERE opp_id=? ORDER BY created_at DESC", (opp_id,)
    ).fetchall()
    history = db.execute(
        "SELECT * FROM sales_stage_history WHERE opp_id=? ORDER BY changed_at", (opp_id,)
    ).fetchall()
    from sana_revenue_cycle import opportunity_context
    return jsonify({
        "success": True,
        "data": {**dict(opp),
                 "activities": [dict(a) for a in activities],
                 "stage_history": [dict(h) for h in history],
                 "revenue_cycle": opportunity_context(
                     db, opp["company_id"], opp_id
                 )}
    })


@app.route("/api/opportunities/<opp_id>/stage", methods=["PATCH"])
def move_stage(opp_id):
    db = get_db()
    opp, err, code = _opp_or_404(db, opp_id)
    if err: return err, code
    guard = enforce_entity_company_scope(opp["company_id"])
    if guard: return guard

    body = request.get_json(silent=True) or {}
    new_stage = (body.get("stage") or "").strip()
    if new_stage not in SALES_STAGES:
        return jsonify({"success": False, "error": "INVALID_STAGE", "valid": SALES_STAGES}), 400

    # SALES-05: الخسارة تتطلب سببًا
    if new_stage == "خسارة":
        reason = (body.get("outcome_reason") or "").strip()
        if not reason:
            return jsonify({"success": False, "error": "OUTCOME_REASON_REQUIRED",
                            "message": "الخسارة تتطلب سبب إلزامي"}), 400

    import uuid
    account = current_account()
    actor = account["account_id"] if account else "system"
    old_stage = opp["stage"]

    db.execute(
        """UPDATE opportunities
           SET stage=?, outcome_reason=?, updated_at=now()
           WHERE opp_id=?""",
        (new_stage,
         body.get("outcome_reason") if new_stage in ("فوز", "خسارة") else opp["outcome_reason"],
         opp_id)
    )
    # SALES-02: سجّل انتقال المرحلة
    db.execute(
        """INSERT INTO sales_stage_history (history_id, opp_id, company_id, from_stage, to_stage, changed_by)
           VALUES (?,?,?,?,?,?)""",
        (f"SSH-{uuid.uuid4().hex[:10].upper()}", opp_id, opp["company_id"], old_stage, new_stage, actor)
    )
    # SALES-04: الفوز ينشئ مهمة تسليم مرة واحدة فقط (Idempotent)
    if new_stage == "فوز" and not opp["delivery_task_id"]:
        task_id = f"TSK-DEL-{uuid.uuid4().hex[:8].upper()}"
        db.execute(
            """INSERT INTO tasks (task_id, company_id, title, status, priority)
               VALUES (?,?,?,?,?)""",
            (task_id, opp["company_id"],
             f"بدء تسليم: {opp['title']}", "لم تبدأ", "عالية")
        )
        db.execute("UPDATE opportunities SET delivery_task_id=? WHERE opp_id=?", (task_id, opp_id))
    from sana_revenue_cycle import record_legacy_transition
    record_legacy_transition(
        db, dict(opp), new_stage, actor,
        reason=body.get("outcome_reason"),
    )
    db.commit()
    return jsonify({"success": True, "data": {"opp_id": opp_id, "stage": new_stage}})


# ── Activities ────────────────────────────────────────────────────

@app.route("/api/opportunities/<opp_id>/activities", methods=["POST"])
def add_activity(opp_id):
    db = get_db()
    opp, err, code = _opp_or_404(db, opp_id)
    if err: return err, code
    guard = enforce_entity_company_scope(opp["company_id"])
    if guard: return guard
    body = request.get_json(silent=True) or {}
    act_type = (body.get("type") or "").strip()
    if act_type not in ACTIVITY_TYPES:
        return jsonify({"success": False, "error": "INVALID_TYPE",
                        "valid": list(ACTIVITY_TYPES)}), 400
    import uuid
    account = current_account()
    act_id = f"ACT-{uuid.uuid4().hex[:10].upper()}"
    db.execute(
        """INSERT INTO sales_activities
           (activity_id, company_id, opp_id, type, subject, notes,
            occurred_at, due_at, completed_at, actor_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (act_id, opp["company_id"], opp_id, act_type,
         body.get("subject"), body.get("notes"),
         body.get("occurred_at"), body.get("due_at"), body.get("completed_at"),
         account["account_id"] if account else body.get("actor_id"))
    )
    db.commit()
    return jsonify({"success": True, "data": {"activity_id": act_id}}), 201


# ── Archive (SALES-07) ────────────────────────────────────────────

@app.route("/api/opportunities/<opp_id>/next-action", methods=["PATCH"])
def update_next_action(opp_id):
    db = get_db()
    opp, err, code = _opp_or_404(db, opp_id)
    if err: return err, code
    guard = enforce_entity_company_scope(opp["company_id"])
    if guard: return guard
    body = request.get_json(silent=True) or {}
    db.execute(
        "UPDATE opportunities SET next_action=?, next_action_due=?, updated_at=now() WHERE opp_id=?",
        (body.get("next_action") or None, body.get("next_action_due") or None, opp_id)
    )
    db.commit()
    return jsonify({"success": True})


@app.route("/api/opportunities/<opp_id>/archive", methods=["POST"])
def archive_opportunity(opp_id):
    """SALES-07: حذف/أرشفة يحتاج صلاحية وسجل تدقيق"""
    db = get_db()
    opp, err, code = _opp_or_404(db, opp_id)
    if err: return err, code
    guard = enforce_entity_company_scope(opp["company_id"])
    if guard: return guard
    account = current_account()
    if not account and not is_admin_preview():
        return jsonify({"success": False, "error": "UNAUTHORIZED"}), 401
    actor = account["account_id"] if account else "admin"
    import uuid
    db.execute(
        "UPDATE opportunities SET archived=1, archived_at=now(), archived_by=? WHERE opp_id=?",
        (actor, opp_id)
    )
    # سجّل في تاريخ المراحل كحدث أرشفة
    db.execute(
        """INSERT INTO sales_stage_history (history_id, opp_id, company_id, from_stage, to_stage, changed_by)
           VALUES (?,?,?,?,'مؤرشفة',?)""",
        (f"SSH-{uuid.uuid4().hex[:10].upper()}", opp_id, opp["company_id"], opp["stage"], actor)
    )
    db.commit()
    return jsonify({"success": True, "data": {"opp_id": opp_id, "archived": True}})


# ── Metrics ───────────────────────────────────────────────────────

@app.route("/api/companies/<company_id>/sales/metrics")
def sales_metrics(company_id):
    guard = enforce_entity_company_scope(company_id)
    if guard: return guard
    db = get_db()

    all_opps = db.execute(
        "SELECT * FROM opportunities WHERE company_id=? AND archived=0", (company_id,)
    ).fetchall()

    pipeline_value = sum(decimal.Decimal(o["amount"] or 0) for o in all_opps if o["stage"] in ACTIVE_STAGES)
    weighted_value = sum(
        decimal.Decimal(o["amount"] or 0) * (decimal.Decimal(o["probability"] or 50) / 100)
        for o in all_opps if o["stage"] in ACTIVE_STAGES
    )
    # SALES-01: فرص بلا إجراء تالٍ أو تاريخ
    no_action = [
        dict(o) for o in all_opps
        if o["stage"] in ACTIVE_STAGES and (not o["next_action"] or not o["next_action_due"])
    ]
    wins = [o for o in all_opps if o["stage"] == "فوز"]
    losses = [o for o in all_opps if o["stage"] == "خسارة"]
    total_closed = len(wins) + len(losses)
    conversion_rate = round(len(wins) / total_closed * 100) if total_closed else None

    # أسباب الخسارة
    loss_reasons: dict = {}
    for o in losses:
        r = o["outcome_reason"] or "غير محدد"
        loss_reasons[r] = loss_reasons.get(r, 0) + 1

    # مصادر العملاء من جدول leads
    source_rows = db.execute(
        """SELECT l.source, COUNT(o.opp_id) AS opps, COUNT(CASE WHEN o.stage='فوز' THEN 1 END) AS wins,
                  SUM(CASE WHEN o.stage='فوز' THEN o.amount ELSE 0 END) AS won_value
           FROM leads l
           LEFT JOIN opportunities o ON o.lead_id=l.lead_id AND o.company_id=l.company_id
           WHERE l.company_id=?
           GROUP BY l.source""",
        (company_id,)
    ).fetchall()
    sources = [dict(r) for r in source_rows]

    return jsonify({
        "success": True,
        "data": {
            "pipeline_value": pipeline_value,
            "weighted_value": round(weighted_value),
            "no_action_count": len(no_action),
            "no_action_opps": no_action,
            "conversion_rate": conversion_rate,
            "avg_deal_value": float(sum(decimal.Decimal(o["amount"] or 0) for o in wins) / len(wins)) if wins else None,
            "loss_reasons": loss_reasons,
            "sources": sources,
            "stage_counts": {s: sum(1 for o in all_opps if o["stage"] == s) for s in SALES_STAGES},
        }
    })


@app.route("/api/companies/<company_id>/revenue-cycle")
def revenue_cycle_report(company_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    from sana_revenue_cycle import report
    filters = {
        key: request.args.get(key)
        for key in ("owner", "service", "channel", "sector", "stage")
    }
    return jsonify({
        "success": True,
        "data": report(get_db(), company_id, filters),
    })


@app.route("/api/companies/<company_id>/revenue-cycle/stages")
def revenue_cycle_stages(company_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    from sana_revenue_cycle import stage_catalog
    return jsonify({"success": True, "data": stage_catalog(get_db())})


@app.route("/api/opportunities/<opp_id>/revenue-stage", methods=["PATCH"])
def move_revenue_stage(opp_id):
    db = get_db()
    opp, err, code = _opp_or_404(db, opp_id)
    if err:
        return err, code
    guard = enforce_entity_company_scope(opp["company_id"])
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    from sana_revenue_cycle import record_transition
    try:
        result = record_transition(
            db,
            company_id=opp["company_id"],
            opp_id=opp_id,
            to_stage_id=str(body.get("stage_id") or "").strip(),
            owner_id=(current_account() or {}).get("account_id") or "system",
            reason=body.get("reason"),
            source_ref=body.get("source_ref") or "manual",
        )
        db.commit()
        return jsonify({"success": True, "data": result})
    except ValueError as exc:
        db.rollback()
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/opportunities/<opp_id>/economics", methods=["PUT"])
def opportunity_economics(opp_id):
    db = get_db()
    opp, err, code = _opp_or_404(db, opp_id)
    if err:
        return err, code
    guard = enforce_entity_company_scope(opp["company_id"])
    if guard:
        return guard
    from sana_revenue_cycle import upsert_economics
    try:
        result = upsert_economics(
            db, opp["company_id"], opp_id,
            request.get_json(silent=True) or {},
        )
        db.commit()
        return jsonify({"success": True, "data": result})
    except ValueError as exc:
        db.rollback()
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/opportunities/<opp_id>/invoices", methods=["POST"])
def opportunity_invoice_create(opp_id):
    db = get_db()
    opp, err, code = _opp_or_404(db, opp_id)
    if err:
        return err, code
    guard = enforce_entity_company_scope(opp["company_id"])
    if guard:
        return guard
    from sana_revenue_cycle import create_invoice
    try:
        result = create_invoice(
            db, opp["company_id"], opp_id,
            request.get_json(silent=True) or {},
        )
        db.commit()
        return jsonify({"success": True, "data": result}), 201
    except Exception as exc:
        db.rollback()
        status = 409 if "unique" in str(exc).lower() else 400
        return jsonify({"success": False, "error": str(exc)}), status


@app.route("/api/invoices/<invoice_id>/payment", methods=["PATCH"])
def invoice_payment(invoice_id):
    db = get_db()
    row = db.execute(
        "SELECT company_id FROM rc_invoices WHERE invoice_id=?",
        (invoice_id,),
    ).fetchone()
    if not row:
        return jsonify({"success": False, "error": "INVOICE_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(row["company_id"])
    if guard:
        return guard
    from sana_revenue_cycle import record_payment
    try:
        result = record_payment(
            db, row["company_id"], invoice_id,
            request.get_json(silent=True) or {},
        )
        db.commit()
        return jsonify({"success": True, "data": result})
    except ValueError as exc:
        db.rollback()
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/opportunities/<opp_id>/learning", methods=["PUT"])
def opportunity_learning(opp_id):
    db = get_db()
    opp, err, code = _opp_or_404(db, opp_id)
    if err:
        return err, code
    guard = enforce_entity_company_scope(opp["company_id"])
    if guard:
        return guard
    from sana_revenue_cycle import upsert_learning
    try:
        result = upsert_learning(
            db, opp["company_id"], opp_id,
            request.get_json(silent=True) or {},
        )
        db.commit()
        return jsonify({"success": True, "data": result})
    except ValueError as exc:
        db.rollback()
        return jsonify({"success": False, "error": str(exc)}), 400


# ═══════════════════════════════════════════════════════════════════
# سنع الخبير — الجزء 3: واجهة الخبير العامة + محادثة Claude
# ═══════════════════════════════════════════════════════════════════

# ── System Prompt (سيُستبدل بالنص الكامل عند استلامه من المستشار) ──
EXPERT_SYSTEM_PROMPT = """\
أنت سنع الخبير، مستشار استراتيجي متخصص في استخراج المعرفة، اكتشاف الفرص، تصميم نماذج الأعمال، وتحويل خبرات المتخصصين إلى مشاريع وأنظمة تشغيل قابلة للتكرار والتوسع.

مهمتك ليست اقتراح فكرة سريعة، بل اكتشاف القيمة الحقيقية داخل خبرة المستخدم، اختبارها بالأدلة، ثم تحويلها تدريجيًا إلى مشروع واضح ونظام قابل للتنفيذ.

## قواعد الحوار
1. ابدأ بالتعريف بالمستخدم وهدفه من الجلسة.
2. اطرح سؤالًا واحدًا فقط في كل رسالة.
3. اجعل السؤال التالي مبنيًا على الإجابة السابقة، لا على تسلسل جامد.
4. لا تقبل الإجابات العامة؛ اطلب أمثلة وأرقامًا وأدلة عند الحاجة.
5. فرّق دائمًا بين: الحقيقة، الرأي، الافتراض، الرغبة، الدليل.
6. إذا ظهر تناقض، توقف وناقشه قبل الاستمرار.
7. لا تنتقل بين المراحل قبل تلخيص ما تم اكتشافه وأخذ اعتماد المستخدم.
8. لا تقدم مشروعًا لمجرد أنه جذاب؛ اختبر ملاءمته للخبير والسوق والقدرة على التنفيذ.
9. اجعل الحوار طبيعيًا، مشجعًا، عميقًا، وغير شبيه بالاستبيانات.
10. لا تطرح أكثر من ثلاثة خيارات جاهزة إلا عند الحاجة، واترك خيارًا مفتوحًا للمستخدم.

## بعد كل إجابة
حلل الإجابة داخليًا واستخرج: الحقائق المؤكدة، الأدلة المتاحة، الافتراضات غير المثبتة، الفرص المحتملة، المخاطر والتناقضات، القرارات التي يمكن اتخاذها، السؤال الأعلى قيمة الذي يجب طرحه بعد ذلك.

اعرض للمستخدم ملخصًا مختصرًا عند وجود اكتشاف مهم بالصيغة التالية:

ثم اطرح سؤالًا واحدًا.

---

## المرحلة الأولى: اكتشاف الخبير
اكتشف: المجال الذي يتقنه فعليًا، مدة ونوع خبرته، الأعمال التي يجيدها أكثر من غيره، النتائج التي حققها، المشكلات التي يلجأ الناس إليه لحلها، القرارات التي يعرف كيف يتخذها، الأخطاء التي يستطيع اكتشافها مبكرًا، طريقته الخاصة في العمل، ما يحبه وما لا يرغب في ممارسته، الوقت والمال والعلاقات والأدوات المتاحة له.

مخرجات المرحلة: هوية الخبير، نقاط القوة الحقيقية، رأس المال المعرفي، الخبرة القابلة للتحويل إلى قيمة، الميزة الشخصية والمهنية، القيود التشغيلية.

اطلب اعتماد المستخدم قبل الانتقال.

## المرحلة الثانية: اكتشاف القيمة والسوق
اكتشف: من يحتاج هذه الخبرة أكثر؟ ما المشكلة التي يعاني منها؟ ما أثر المشكلة ماليًا أو تشغيليًا أو نفسيًا؟ كيف يحلها حاليًا؟ لماذا الحلول الحالية غير كافية؟ هل يدفع العميل مقابل حلها؟ من صاحب قرار الشراء؟ ما سرعة ظهور النتيجة؟ ما الأدلة المتوفرة على وجود الطلب؟

مخرجات المرحلة: العميل المثالي، المستخدم النهائي، المشتري الفعلي، المشكلة الأعلى قيمة، النتيجة التي يمكن بيعها، حجم القيمة المتوقعة، درجة ثبوت الحاجة.

اطلب اعتماد المستخدم قبل الانتقال.

## المرحلة الثالثة: توليد واختيار المشروع
استخرج خمسة مشاريع مختلفة على الأقل: خدمة استشارية، خدمة تنفيذية، منتج رقمي، برنامج تدريبي، منصة، أداة ذكاء اصطناعي، نظام تقييم أو تشخيص، اشتراك معرفي، ترخيص منهجية، نموذج هجين.

قيّم كل مشروع من 10 وفق: ملاءمته للخبير، وضوح المشكلة، توفر الطلب، سرعة الوصول للإيراد، سهولة البدء، تكلفة التأسيس، هامش الربح، قوة التميز، قابلية التكرار، قابلية التوسع، قابلية الأتمتة، سهولة إثبات النتيجة، المخاطر، اعتماد المشروع على الخبير شخصيًا.

لا تختَر المشروع صاحب المجموع الأعلى بصورة آلية، بل فسّر المفاضلات.

مخرجات المرحلة: المشاريع الخمسة، بطاقة تقييم كل مشروع، المشروع الأسرع للإيراد، المشروع الأقوى استراتيجيًا، المشروع الأنسب للبداية، المشروع المرشح النهائي وأسباب ترشيحه، ما يجب إثباته قبل الاستثمار الكامل.

## المرحلة الرابعة: تصميم نموذج المشروع
حدد: وعد المشروع، النتيجة التي يبيعها، العميل المثالي، المنتج الأول، العرض التجاري، طريقة التسعير، رحلة العميل، قنوات الوصول، نموذج الإيرادات، الموارد المطلوبة، الشركاء المحتملين، نقطة التعادل، تجربة البداية الصغيرة.

مخرجات المرحلة: نموذج عمل مختصر، عرض أولي قابل للبيع، تجربة سوق منخفضة المخاطر، معايير النجاح أو الإيقاف.

## المرحلة الخامسة: بناء نظام التشغيل
حوّل المشروع إلى نظام يشتمل على: المدخلات، العمليات، نقاط القرار، الأدلة المطلوبة، المخرجات، المسؤوليات، الإجراءات القياسية (SOPs)، قاعدة المعرفة، رحلة العميل، مراقبة الجودة، مؤشرات الأداء، الأدوات التقنية، الأتمتة الممكنة، وكلاء الذكاء الاصطناعي، التقارير، آلية التعلم والتحسين.

وضح ما ينفذه الإنسان، وما تنفذه الأتمتة، وما يحتاج قرار الخبير.

## المرحلة السادسة: استخراج الأصول المعرفية
استخرج من الخبير: المبادئ، القواعد، طرق التشخيص، معايير التقييم، أشجار القرار، النماذج، القوائم، الأسئلة، الأخطاء الشائعة، الإشارات المبكرة، الاستثناءات، المصطلحات، القصص، دراسات الحالة، الاختصارات، خطوات التنفيذ، الخبرة الضمنية التي يمارسها دون أن يشرحها.

اربط كل أصل معرفي بإمكانية تحويله إلى: خدمة، منتج رقمي، أداة، وكيل ذكاء اصطناعي، محتوى، دورة، دليل، نموذج جاهز، SOP، نظام تقييم، أصل قابل للترخيص أو الاشتراك.

## المرحلة السابعة: خطة الانطلاق
أنشئ خطة عملية تشمل: ما الذي يُبنى الآن؟ ما الذي يؤجل؟ أول عميل يجب استهدافه، أول عرض يتم بيعه، أول تجربة إثبات، خطة أول 7 أيام، خطة أول 30 يومًا، مؤشرات النجاح، قرارات الاستمرار أو التعديل أو الإيقاف.

---

## سجل قرارات سنع
بعد كل مرحلة أنشئ سجلًا: الحقيقة | الدليل | القرار | درجة الثقة
درجات الثقة: مؤكد (دليل مباشر) · مرجح (مؤشرات قوية) · افتراض (يحتاج اختبارًا) · غير محسوم (لا معلومات كافية)

## القاعدة الحاكمة
لا تجمع معلومات لمجرد الجمع. يجب أن يقلل كل سؤال مقدار الغموض، أو يكشف فرصة، أو يختبر افتراضًا، أو يقود إلى قرار قابل للتنفيذ.

---

## ملحق تقني (للنظام فقط — لا يُعرض على الخبير مطلقًا)
في نهاية كل ردّ، بعد كامل النص الظاهر للخبير، أضف دائمًا السطرين المخفيين التاليين بالضبط بهذه الصيغة (يُحذفان تلقائيًا قبل عرض الرد على الخبير، ولا تذكرهما أو تشِر إليهما في النص الظاهر):

1. حقائق هذا الرد فقط (لا تكرر ما استُخرج في ردود سابقة). إن لم يظهر أي عنصر جديد أخرج مصفوفة فارغة:
<!-- FACTS_JSON: [{"type": "حقيقة|دليل|افتراض|فرصة|خطر|قرار", "content": "نص مختصر وواضح لهذا العنصر", "confidence": "مؤكد|مرجح|افتراض|غير محسوم"}] -->
ضع "confidence" فقط للعناصر من نوع "حقيقة" أو "قرار"، واحذف هذا الحقل تمامًا لبقية الأنواع.

2. فقط في الرد الذي تلخّص فيه مرحلة كاملة وتطلب اعتماد المستخدم صراحةً قبل الانتقال، أضف سطرًا برقم المرحلة المكتملة (وإلا لا تُخرج هذا السطر إطلاقًا):
<!-- STAGE_CHECKPOINT: N -->

3. فقط في الرد الذي تعرض فيه المشاريع الخمسة المقترحة كاملة ببطاقة تقييمها (نهاية المرحلة الثالثة) — مرة واحدة فقط، لا تكرره في أي رد لاحق:
<!-- PROJECTS_JSON: [{"title": "اسم المشروع", "description": "وصف مختصر للمشروع", "scores": {"ملاءمته للخبير": 0, "وضوح المشكلة": 0, "توفر الطلب": 0, "سرعة الوصول للإيراد": 0, "سهولة البدء": 0, "تكلفة التأسيس": 0, "هامش الربح": 0, "قوة التميز": 0, "قابلية التكرار": 0, "قابلية التوسع": 0, "قابلية الأتمتة": 0, "سهولة إثبات النتيجة": 0, "المخاطر": 0, "اعتماد المشروع على الخبير شخصيًا": 0}, "is_recommended": false, "recommendation_reason": ""}] -->
ضع أرقامًا فعلية من 0 إلى 10 لكل معيار من المعايير الأربعة عشر بأسمائها بالضبط كما وردت. يجب أن تحتوي المصفوفة خمسة عناصر بالضبط، وعنصر واحد فقط منها "is_recommended": true مع "recommendation_reason" مملوء بمبرر الترشيح الفعلي؛ اترك "recommendation_reason" فارغًا "" للأربعة الأخرى.

4. أصول معرفية هذا الرد فقط (خلال المرحلة السادسة تحديدًا)، بلا تكرار لما استُخرج في ردود سابقة. إن لم يظهر أصل جديد أخرج مصفوفة فارغة:
<!-- KNOWLEDGE_ASSETS_JSON: [{"category": "مبدأ|قاعدة|طريقة تشخيص|معيار تقييم|شجرة قرار|نموذج|قائمة|سؤال|خطأ شائع|إشارة مبكرة|استثناء|مصطلح|قصة|دراسة حالة|اختصار|خطوة تنفيذ", "content": "نص الأصل المعرفي بوضوح وتحديد", "transformable_to": ["خدمة", "منتج رقمي"]}] -->
اختر "category" من القائمة أعلاه فقط، و"transformable_to" مصفوفة نصوص من: خدمة، منتج رقمي، أداة، وكيل ذكاء اصطناعي، محتوى، دورة، دليل، نموذج جاهز، SOP، نظام تقييم، أصل قابل للترخيص أو الاشتراك.
"""

# ── Rate limiting (ذاكرة: slug+IP → {count, locked_until}) ──────────
import threading as _threading
_expert_attempts: dict       = {}   # "slug:ip" → {count, locked_until}
_expert_attempts_lock        = _threading.Lock()
_EXPERT_MAX_ATTEMPTS         = 5
_EXPERT_LOCKOUT_MIN          = 15


def _exp_attempt_key(slug: str) -> str:
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown").split(",")[0].strip()
    return f"{slug}:{ip}"


def _exp_check_lock(slug: str):
    """None → مسموح، float → ثوانٍ متبقية في الحظر."""
    key = _exp_attempt_key(slug)
    with _expert_attempts_lock:
        rec = _expert_attempts.get(key)
        if not rec:
            return None
        lu = rec.get("locked_until")
        if lu:
            if datetime.utcnow() < lu:
                return (lu - datetime.utcnow()).total_seconds()
            del _expert_attempts[key]   # انتهى الحظر — امسح
        return None


def _exp_record_fail(slug: str):
    """سجّل محاولة فاشلة. يُعيد (attempts_left, is_locked)."""
    key = _exp_attempt_key(slug)
    with _expert_attempts_lock:
        rec = _expert_attempts.setdefault(key, {"count": 0, "locked_until": None})
        rec["count"] += 1
        if rec["count"] >= _EXPERT_MAX_ATTEMPTS:
            rec["locked_until"] = datetime.utcnow() + timedelta(minutes=_EXPERT_LOCKOUT_MIN)
            return 0, True
        return _EXPERT_MAX_ATTEMPTS - rec["count"], False


def _exp_clear_lock(slug: str):
    with _expert_attempts_lock:
        _expert_attempts.pop(_exp_attempt_key(slug), None)


def _exp_verified(slug: str) -> bool:
    """تحقق أن الخبير أدخل الرمز الصحيح في هذه الجلسة."""
    return session.get(f"exp_v_{slug}") is True


def _exp_get_or_create_session(db, expert_id: str) -> str:
    """احصل على آخر جلسة مفتوحة أو أنشئ واحدة جديدة، وارجع session_id."""
    sess_key = f"exp_sid_{expert_id}"
    sid = session.get(sess_key)
    if sid:
        row = db.execute("SELECT session_id FROM expert_sessions WHERE session_id=? AND expert_id=?",
                         (sid, expert_id)).fetchone()
        if row:
            return sid
    # جلسة جديدة
    sid = "SES-" + uuid.uuid4().hex[:12].upper()
    db.execute("""
        INSERT INTO expert_sessions (session_id, expert_id, current_stage, conversation_log)
        VALUES (?,?,?,?)
    """, (sid, expert_id, 1, json.dumps([])))
    db.execute("UPDATE experts SET last_session_at=now() WHERE expert_id=?", (expert_id,))
    db.commit()
    session[sess_key] = sid
    return sid


def _exp_counters(db, expert_id: str) -> dict:
    """عدّادات حقيقية من DB — لا أرقام مصطنعة."""
    rows = db.execute("""
        SELECT fact_type, COUNT(*) as n FROM expert_facts
        WHERE expert_id=? GROUP BY fact_type
    """, (expert_id,)).fetchall()
    by_type = {r["fact_type"]: r["n"] for r in rows}
    return {
        "حقائق":     by_type.get("حقيقة",  0),
        "أدلة":      by_type.get("دليل",   0),
        "فرص":       by_type.get("فرصة",   0),
        "قرارات":    by_type.get("قرار",   0),
        "افتراضات":  by_type.get("افتراض", 0),
        "أصول":      db.execute("SELECT COUNT(*) FROM expert_knowledge_assets WHERE expert_id=?",
                                (expert_id,)).fetchone()[0],
    }


def _exp_readiness(db, expert_id: str) -> list:
    """قائمة تحقق 'جاهزية بناء النظام' — 7 عناصر بشرط صريح لكل منها."""
    facts_stages = {r["related_stage"] for r in db.execute(
        "SELECT DISTINCT related_stage FROM expert_facts WHERE expert_id=?", (expert_id,)
    ).fetchall() if r["related_stage"]}

    has_decisions = db.execute(
        "SELECT COUNT(*) FROM expert_facts WHERE expert_id=? AND fact_type='قرار'",
        (expert_id,)
    ).fetchone()[0] > 0

    has_evidence = db.execute(
        "SELECT COUNT(*) FROM expert_facts WHERE expert_id=? AND fact_type='دليل'",
        (expert_id,)
    ).fetchone()[0] > 0

    has_projects = db.execute(
        "SELECT COUNT(*) FROM expert_projects WHERE expert_id=?", (expert_id,)
    ).fetchone()[0] > 0

    return [
        {"label": "هوية الخبير",       "done": 1 in facts_stages},
        {"label": "الخبرة الجوهرية",   "done": 2 in facts_stages},
        {"label": "العميل المثالي",    "done": 3 in facts_stages},
        {"label": "المشكلة والحل",     "done": 4 in facts_stages},
        {"label": "نموذج الإيرادات",   "done": 5 in facts_stages},
        {"label": "نظام التشغيل",      "done": 6 in facts_stages},
        {"label": "مشروع محدد معتمد",  "done": has_projects},
    ]


# ── صفحة الخبير ──────────────────────────────────────────────────

@app.route("/e/<slug>")
def expert_page(slug):
    db = get_db()
    exp = db.execute("SELECT expert_id, name, domain_expertise, status FROM experts WHERE access_link_slug=?",
                     (slug,)).fetchone()
    if not exp:
        abort(404)
    return render_template("expert-interface.html",
                           slug=slug, expert_name=exp["name"],
                           expert_domain=exp["domain_expertise"])


@csrf.exempt
@app.route("/api/e/<slug>/verify", methods=["POST"])
def expert_verify(slug):
    db  = get_db()
    exp = db.execute("SELECT expert_id, access_code FROM experts WHERE access_link_slug=?",
                     (slug,)).fetchone()
    if not exp:
        return jsonify({"success": False, "error": "not_found"}), 404

    # تحقق من الحظر
    remaining = _exp_check_lock(slug)
    if remaining is not None:
        mins = int(remaining // 60) + 1
        return jsonify({"success": False, "error": "locked",
                        "message": f"تم تجاوز الحد المسموح. حاول بعد {mins} دقيقة."}), 429

    code = (request.get_json(silent=True) or {}).get("code", "").strip()
    if code != (exp["access_code"] or ""):
        left, locked = _exp_record_fail(slug)
        if locked:
            return jsonify({"success": False, "error": "locked",
                            "message": f"تم إيقاف الدخول {_EXPERT_LOCKOUT_MIN} دقيقة بعد {_EXPERT_MAX_ATTEMPTS} محاولات خاطئة."}), 429
        return jsonify({"success": False, "error": "wrong_code",
                        "message": f"رمز خاطئ. {left} محاولة متبقية."}), 401

    _exp_clear_lock(slug)
    session[f"exp_v_{slug}"] = True
    return jsonify({"success": True})


@app.route("/api/e/<slug>/status")
def expert_status(slug):
    if not _exp_verified(slug):
        return jsonify({"success": False, "error": "unverified"}), 401
    db  = get_db()
    exp = db.execute("""
        SELECT e.expert_id, e.name, e.domain_expertise, e.status, e.api_call_count,
               COALESCE((SELECT es.current_stage FROM expert_sessions es
                          WHERE es.expert_id=e.expert_id ORDER BY es.started_at DESC LIMIT 1), 0)
               AS current_stage
        FROM experts e WHERE e.access_link_slug=?
    """, (slug,)).fetchone()
    if not exp:
        return jsonify({"success": False, "error": "not_found"}), 404
    return jsonify({
        "success":     True,
        "expert":      dict(exp),
        "counters":    _exp_counters(db, exp["expert_id"]),
        "readiness":   _exp_readiness(db, exp["expert_id"]),
    })


@csrf.exempt
def _exp_extract_facts_json(raw: str, db, expert_id: str, sid: str, stage: int) -> list:
    """يستخرج كتلة FACTS_JSON المخفية من رد Claude ويحفظها في expert_facts. يُرجع قائمة العناصر المُستخرجة."""
    import re as _re
    extracted_facts = []
    facts_match = _re.search(r"<!--\s*FACTS_JSON:\s*(\[.*?\])\s*-->", raw, _re.DOTALL)
    if facts_match:
        try:
            items = json.loads(facts_match.group(1))
            for item in items:
                ftype = item.get("type", "حقيقة")
                conf  = item.get("confidence") if ftype in ("حقيقة", "قرار") else None
                fid   = "FACT-" + uuid.uuid4().hex[:10].upper()
                db.execute("""
                    INSERT INTO expert_facts
                        (fact_id, expert_id, session_id, fact_type, content, confidence_level, related_stage)
                    VALUES (?,?,?,?,?,?,?)
                """, (fid, expert_id, sid,
                      ftype, item.get("content", ""), conf, stage))
                extracted_facts.append({"type": ftype, "content": item.get("content", "")})
        except Exception:
            pass
    return extracted_facts


def _exp_extract_projects_json(raw: str, db, expert_id: str) -> list:
    """يستخرج كتلة PROJECTS_JSON المخفية (نهاية المرحلة الثالثة، مرة واحدة فقط) ويحفظها في expert_projects.
    محمية بحارس idempotency: لا تُدرج مشاريع جديدة إذا كانت موجودة أصلاً لهذا الخبير. يُرجع قائمة العناصر المُستخرجة."""
    import re as _re
    extracted_projects = []
    proj_match = _re.search(r"<!--\s*PROJECTS_JSON:\s*(\[.*?\])\s*-->", raw, _re.DOTALL)
    if proj_match:
        try:
            already = db.execute(
                "SELECT COUNT(*) FROM expert_projects WHERE expert_id=?", (expert_id,)
            ).fetchone()[0]
            if already == 0:
                items = json.loads(proj_match.group(1))
                for item in items:
                    pid = "PROJ-" + uuid.uuid4().hex[:10].upper()
                    db.execute("""
                        INSERT INTO expert_projects
                            (project_id, expert_id, title, description, scores, is_recommended, recommendation_reason)
                        VALUES (?,?,?,?,?,?,?)
                    """, (pid, expert_id, item.get("title", ""), item.get("description", ""),
                          json.dumps(item.get("scores", {}), ensure_ascii=False),
                          1 if item.get("is_recommended") else 0,
                          item.get("recommendation_reason") or None))
                    extracted_projects.append({"title": item.get("title", ""),
                                                "is_recommended": bool(item.get("is_recommended"))})
        except Exception:
            pass
    return extracted_projects


def _exp_extract_ka_json(raw: str, db, expert_id: str) -> list:
    """يستخرج كتلة KNOWLEDGE_ASSETS_JSON المخفية (تراكمية، المرحلة السادسة) ويحفظها في expert_knowledge_assets.
    محمية بحارس تكرار على مستوى المحتوى (لا يُدرج نفس الأصل مرتين لنفس الخبير). يُرجع قائمة العناصر المُستخرجة."""
    import re as _re
    extracted_ka = []
    ka_match = _re.search(r"<!--\s*KNOWLEDGE_ASSETS_JSON:\s*(\[.*?\])\s*-->", raw, _re.DOTALL)
    if ka_match:
        try:
            items = json.loads(ka_match.group(1))
            for item in items:
                content = item.get("content", "")
                if not content:
                    continue
                dup = db.execute(
                    "SELECT COUNT(*) FROM expert_knowledge_assets WHERE expert_id=? AND content=?",
                    (expert_id, content)
                ).fetchone()[0]
                if dup:
                    continue
                aid = "KA-" + uuid.uuid4().hex[:10].upper()
                db.execute("""
                    INSERT INTO expert_knowledge_assets (asset_id, expert_id, asset_category, content, transformable_to)
                    VALUES (?,?,?,?,?)
                """, (aid, expert_id, item.get("category", ""), content,
                      json.dumps(item.get("transformable_to", []), ensure_ascii=False)))
                extracted_ka.append({"category": item.get("category", ""), "content": content})
        except Exception:
            pass
    return extracted_ka


@app.route("/api/e/<slug>/chat", methods=["POST"])
def expert_chat(slug):
    if not _exp_verified(slug):
        return jsonify({"success": False, "error": "unverified"}), 401

    db  = get_db()
    exp = db.execute("SELECT expert_id, status, api_call_count FROM experts WHERE access_link_slug=?",
                     (slug,)).fetchone()
    if not exp:
        return jsonify({"success": False, "error": "not_found"}), 404

    body        = request.get_json(silent=True) or {}
    user_text   = (body.get("message") or "").strip()
    if not user_text:
        return jsonify({"success": False, "error": "رسالة فارغة"}), 400

    expert_id  = exp["expert_id"]
    sid        = _exp_get_or_create_session(db, expert_id)

    # تحميل سجل المحادثة
    sess_row   = db.execute("SELECT conversation_log, current_stage FROM expert_sessions WHERE session_id=?",
                            (sid,)).fetchone()
    history    = json.loads(sess_row["conversation_log"] or "[]")
    stage      = sess_row["current_stage"]

    # أضف رسالة المستخدم
    history.append({"role": "user", "content": user_text})

    # ── استدعاء Claude ──────────────────────────────────────────
    try:
        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp   = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=8192,
            system=EXPERT_SYSTEM_PROMPT,
            messages=history,
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:
        return jsonify({"success": False, "error": f"خطأ في الاتصال بـClaude: {str(e)}"}), 500

    # ── استخراج الكتل المخفية من ردّ Claude ─────────────────────
    import re as _re
    extracted_facts    = _exp_extract_facts_json(raw, db, expert_id, sid, stage)
    extracted_projects = _exp_extract_projects_json(raw, db, expert_id)
    extracted_ka       = _exp_extract_ka_json(raw, db, expert_id)

    checkpoint_stage = None
    cp_match = _re.search(r"<!--\s*STAGE_CHECKPOINT:\s*(\d+)\s*-->", raw)
    if cp_match:
        checkpoint_stage = int(cp_match.group(1))

    # حذف الكتل المخفية من النص المعروض للخبير
    display_text = _re.sub(r"<!--.*?-->", "", raw, flags=_re.DOTALL).strip()

    # أضف ردّ المساعد للسجل (النص الكامل مع الكتل — للحفاظ على السياق الكامل لـClaude)
    history.append({"role": "assistant", "content": raw})

    # حدّث DB
    db.execute("UPDATE expert_sessions SET conversation_log=?, current_stage=? WHERE session_id=?",
               (json.dumps(history, ensure_ascii=False), stage, sid))
    db.execute("UPDATE experts SET api_call_count=api_call_count+1, last_session_at=now() WHERE expert_id=?",
               (expert_id,))
    db.commit()

    return jsonify({
        "success":        True,
        "reply":          display_text,
        "session_id":     sid,
        "stage":          stage,
        "checkpoint":     checkpoint_stage,
        "extracted":      extracted_facts,
        "extracted_projects": extracted_projects,
        "extracted_knowledge_assets": extracted_ka,
        "counters":       _exp_counters(db, expert_id),
    })


@csrf.exempt
@app.route("/api/e/<slug>/checkpoint", methods=["POST"])
def expert_checkpoint(slug):
    """يُعالج استجابة الخبير عند نقطة التوقف بين المراحل."""
    if not _exp_verified(slug):
        return jsonify({"success": False, "error": "unverified"}), 401
    db   = get_db()
    exp  = db.execute("SELECT expert_id FROM experts WHERE access_link_slug=?", (slug,)).fetchone()
    if not exp:
        return jsonify({"success": False, "error": "not_found"}), 404

    body     = request.get_json(silent=True) or {}
    decision = body.get("decision", "")   # "approve" | "revise" | "add_evidence" | "inaccurate"
    expert_id = exp["expert_id"]
    sid = session.get(f"exp_sid_{expert_id}")

    if decision == "approve" and sid:
        sess = db.execute("SELECT current_stage FROM expert_sessions WHERE session_id=?", (sid,)).fetchone()
        new_stage = min((sess["current_stage"] or 1) + 1, 7)
        db.execute("UPDATE expert_sessions SET current_stage=? WHERE session_id=?", (new_stage, sid))
        db.commit()
        return jsonify({"success": True, "new_stage": new_stage})

    return jsonify({"success": True, "new_stage": None})


@app.route("/api/e/<slug>/facts")
def expert_facts_api(slug):
    if not _exp_verified(slug):
        return jsonify({"success": False, "error": "unverified"}), 401
    db  = get_db()
    exp = db.execute("SELECT expert_id FROM experts WHERE access_link_slug=?", (slug,)).fetchone()
    if not exp:
        return jsonify({"success": False, "error": "not_found"}), 404
    rows = db.execute("""
        SELECT fact_id, fact_type, content, confidence_level, related_stage,
               to_char(created_at AT TIME ZONE 'Asia/Riyadh','YYYY-MM-DD HH24:MI') as created_fmt
        FROM expert_facts WHERE expert_id=? ORDER BY created_at
    """, (exp["expert_id"],)).fetchall()
    by_type = {}
    for r in rows:
        t = r["fact_type"]
        by_type.setdefault(t, []).append(dict(r))
    return jsonify({"success": True, "by_type": by_type,
                    "total": len(rows)})


@app.route("/api/e/<slug>/knowledge-assets")
def expert_ka_api(slug):
    if not _exp_verified(slug):
        return jsonify({"success": False, "error": "unverified"}), 401
    db  = get_db()
    exp = db.execute("SELECT expert_id FROM experts WHERE access_link_slug=?", (slug,)).fetchone()
    if not exp:
        return jsonify({"success": False, "error": "not_found"}), 404
    rows = db.execute("""
        SELECT asset_id, asset_category, content, transformable_to
        FROM expert_knowledge_assets WHERE expert_id=? ORDER BY asset_id
    """, (exp["expert_id"],)).fetchall()
    return jsonify({"success": True, "assets": [dict(r) for r in rows]})


@app.route("/api/e/<slug>/projects")
def expert_projects_api(slug):
    if not _exp_verified(slug):
        return jsonify({"success": False, "error": "unverified"}), 401
    db  = get_db()
    exp = db.execute("SELECT expert_id FROM experts WHERE access_link_slug=?", (slug,)).fetchone()
    if not exp:
        return jsonify({"success": False, "error": "not_found"}), 404
    rows = db.execute("""
        SELECT project_id, title, description, scores, is_recommended, recommendation_reason
        FROM expert_projects WHERE expert_id=? ORDER BY is_recommended DESC, project_id
    """, (exp["expert_id"],)).fetchall()
    return jsonify({"success": True, "projects": [dict(r) for r in rows]})


@app.route("/api/e/<slug>/report")
def expert_report_api(slug):
    if not _exp_verified(slug):
        return jsonify({"success": False, "error": "unverified"}), 401
    db  = get_db()
    exp = db.execute("SELECT * FROM experts WHERE access_link_slug=?", (slug,)).fetchone()
    if not exp:
        return jsonify({"success": False, "error": "not_found"}), 404
    expert_id = exp["expert_id"]
    facts_rows = db.execute("""
        SELECT fact_type, content, confidence_level, related_stage
        FROM expert_facts WHERE expert_id=? ORDER BY created_at
    """, (expert_id,)).fetchall()
    ka_rows = db.execute("""
        SELECT asset_category, content, transformable_to
        FROM expert_knowledge_assets WHERE expert_id=? ORDER BY asset_id
    """, (expert_id,)).fetchall()
    proj_rows = db.execute("""
        SELECT title, description, scores, is_recommended, recommendation_reason
        FROM expert_projects WHERE expert_id=? ORDER BY is_recommended DESC
    """, (expert_id,)).fetchall()
    sess = db.execute("""
        SELECT current_stage FROM expert_sessions WHERE expert_id=?
        ORDER BY started_at DESC LIMIT 1
    """, (expert_id,)).fetchone()
    return jsonify({
        "success":  True,
        "expert":   dict(exp),
        "stage":    sess["current_stage"] if sess else 0,
        "facts":    [dict(r) for r in facts_rows],
        "knowledge_assets": [dict(r) for r in ka_rows],
        "projects": [dict(r) for r in proj_rows],
        "counters": _exp_counters(db, expert_id),
        "readiness": _exp_readiness(db, expert_id),
    })


# ═══════════════════════════════════════════════════════════════════
# سنع الخبير — لوحة تحكم إدارة الخبراء (الجزء 2)
# ═══════════════════════════════════════════════════════════════════

def is_expert_admin():
    """صلاحية إدارة الخبراء: مفتاح المشرف الداخلي أو حساب مُعلَّم بـis_admin=1."""
    if is_admin_preview():
        return True
    return bool(session.get("is_admin"))


@app.route("/admin/experts")
def admin_experts_page():
    if not is_expert_admin():
        return redirect(url_for("login", next=request.full_path))
    return render_template("admin-experts.html")


@app.route("/api/experts", methods=["GET"])
def list_experts():
    if not is_expert_admin():
        return jsonify({"success": False, "error": "FORBIDDEN"}), 403
    db = get_db()
    rows = db.execute("""
        SELECT
            e.expert_id, e.name, e.domain_expertise, e.status,
            e.access_link_slug, e.access_code, e.api_call_count,
            to_char(e.created_at AT TIME ZONE 'Asia/Riyadh', 'YYYY-MM-DD') AS created_date,
            to_char(e.last_session_at AT TIME ZONE 'Asia/Riyadh', 'YYYY-MM-DD') AS last_session_date,
            COALESCE((
                SELECT es.current_stage FROM expert_sessions es
                WHERE es.expert_id = e.expert_id
                ORDER BY es.started_at DESC LIMIT 1
            ), 0) AS current_stage,
            (SELECT COUNT(*) FROM expert_knowledge_assets ka WHERE ka.expert_id = e.expert_id) AS knowledge_count,
            (SELECT COUNT(*) FROM expert_sessions   s  WHERE s.expert_id  = e.expert_id) AS session_count,
            (SELECT COUNT(*) FROM expert_facts      f  WHERE f.expert_id  = e.expert_id) AS fact_count
        FROM experts e
        ORDER BY e.created_at DESC
    """).fetchall()
    return jsonify({"success": True, "experts": [dict(r) for r in rows]})


@csrf.exempt   # يُستدعى بـ admin_key أو من واجهة المستشار — الواجهة ترسل الـCSRF عبر meta-interceptor
@app.route("/api/experts", methods=["POST"])
def create_expert():
    if not is_expert_admin():
        return jsonify({"success": False, "error": "FORBIDDEN"}), 403
    body = request.get_json(silent=True) or {}
    name   = (body.get("name") or "").strip()
    domain = (body.get("domain_expertise") or "").strip()
    if not name or not domain:
        return jsonify({"success": False, "error": "الاسم والمجال مطلوبان"}), 400

    db = get_db()
    # توليد expert_id تسلسلي فريد (EXP-101, EXP-102, ...)
    existing_ids = {r[0] for r in db.execute("SELECT expert_id FROM experts").fetchall()}
    n = 101
    while f"EXP-{n}" in existing_ids:
        n += 1
    expert_id = f"EXP-{n}"
    slug      = f"exp-{n}"
    uid       = str(uuid.uuid4())
    code      = f"{secrets.randbelow(1_000_000):06d}"

    db.execute("""
        INSERT INTO experts
            (expert_id, uuid, name, domain_expertise, phone, email,
             notes, access_link_slug, access_code, status, api_call_count)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (expert_id, uid, name,
          domain,
          (body.get("phone") or "").strip() or None,
          (body.get("email") or "").strip() or None,
          (body.get("notes") or "").strip() or None,
          slug, code, "لم يبدأ", 0))
    db.commit()

    base = request.host_url.rstrip("/")
    return jsonify({
        "success":    True,
        "expert_id":  expert_id,
        "slug":       slug,
        "access_code": code,
        "access_link": f"{base}/e/{slug}",
    }), 201


@app.route("/api/experts/<expert_id>", methods=["GET"])
def get_expert(expert_id):
    if not is_expert_admin():
        return jsonify({"success": False, "error": "FORBIDDEN"}), 403
    db = get_db()
    row = db.execute("""
        SELECT e.*,
            to_char(e.created_at AT TIME ZONE 'Asia/Riyadh', 'YYYY-MM-DD HH24:MI') AS created_fmt,
            to_char(e.last_session_at AT TIME ZONE 'Asia/Riyadh', 'YYYY-MM-DD HH24:MI') AS last_session_fmt,
            COALESCE((
                SELECT es.current_stage FROM expert_sessions es
                WHERE es.expert_id = e.expert_id
                ORDER BY es.started_at DESC LIMIT 1
            ), 0) AS current_stage,
            (SELECT COUNT(*) FROM expert_knowledge_assets WHERE expert_id=e.expert_id) AS knowledge_count,
            (SELECT COUNT(*) FROM expert_facts WHERE expert_id=e.expert_id AND fact_type='حقيقة') AS fact_count,
            (SELECT COUNT(*) FROM expert_facts WHERE expert_id=e.expert_id AND fact_type='دليل')  AS evidence_count,
            (SELECT COUNT(*) FROM expert_facts WHERE expert_id=e.expert_id AND fact_type='فرصة') AS opp_count,
            (SELECT COUNT(*) FROM expert_facts WHERE expert_id=e.expert_id AND fact_type='قرار') AS decision_count,
            (SELECT COUNT(*) FROM expert_facts WHERE expert_id=e.expert_id AND fact_type='افتراض') AS assumption_count,
            (SELECT COUNT(*) FROM expert_projects WHERE expert_id=e.expert_id) AS project_count
        FROM experts e WHERE e.expert_id=?
    """, (expert_id,)).fetchone()
    if not row:
        return jsonify({"success": False, "error": "not found"}), 404
    base = request.host_url.rstrip("/")
    data = dict(row)
    data["access_link"] = f"{base}/e/{row['access_link_slug']}"
    return jsonify({"success": True, "expert": data})

def _initialize_and_start_schedulers():
    """هيّئ قاعدة البيانات مرة واحدة دون حجب فتح منفذ الويب."""
    _RUNTIME_HEALTH["startup"] = "initializing"
    try:
        retry_count = int(os.environ.get("SANA_SCHEMA_INIT_RETRIES", "10"))
    except (TypeError, ValueError):
        retry_count = 10
    retry_count = max(1, min(retry_count, 12))
    retryable_errors = (
        psycopg2.errors.DeadlockDetected,
        psycopg2.errors.LockNotAvailable,
        psycopg2.errors.SerializationFailure,
    )
    for attempt in range(retry_count):
        try:
            init_db()
            break
        except retryable_errors as exc:
            if attempt + 1 == retry_count:
                print(
                    f"[startup] database initialization failed after "
                    f"{retry_count} attempts: {exc}",
                    flush=True,
                )
                _RUNTIME_HEALTH["startup"] = "failed"
                _RUNTIME_HEALTH["last_critical_error"] = type(exc).__name__
                return
            delay = min(2 ** attempt, 5)
            print(
                f"[startup] schema is busy; retrying in {delay}s "
                f"({attempt + 1}/{retry_count - 1})",
                flush=True,
            )
            time.sleep(delay)
        except Exception as exc:
            print(f"[startup] database initialization failed: {exc}", flush=True)
            _RUNTIME_HEALTH["startup"] = "failed"
            _RUNTIME_HEALTH["last_critical_error"] = type(exc).__name__
            return

    try:
        seed_db()
        seed_decision_impacts()
        seed_knowledge_db()
    except Exception as exc:
        print(f"[startup] database seeding failed: {exc}", flush=True)
        _RUNTIME_HEALTH["last_critical_error"] = type(exc).__name__
    else:
        _RUNTIME_HEALTH["startup"] = "ok"

    # Railway's production web service is web-only. Scheduler ownership must
    # live in one explicit external runner, never in every Gunicorn worker.
    if os.environ.get("SANA_ENV", "").strip().lower() in {"production", "prod"}:
        for worker in _RUNTIME_HEALTH["workers"]:
            _RUNTIME_HEALTH["workers"][worker] = "external_required"
        print("[schedulers] disabled in production web runtime", flush=True)
        return

    reminder_scheduler_enabled = os.environ.get(
        "ENABLE_EXECUTION_REMINDER_SCHEDULER", "1"
    ) == "1"
    if reminder_scheduler_enabled:
        from sana_decision_room import start_reminder_scheduler
        start_reminder_scheduler(_connect_pg)
        _RUNTIME_HEALTH["workers"]["execution_reminders"] = "running"
        print("[execution-reminders] scheduler: every 15 minutes", flush=True)

    scheduler_enabled = os.environ.get(
        "ENABLE_KNOWLEDGE_BACKUP_SCHEDULER", "1"
    ) == "1"
    if scheduler_enabled:
        from knowledge_backup import start_daily_scheduler
        start_daily_scheduler(_connect_pg)
        _RUNTIME_HEALTH["workers"]["knowledge_backup"] = "running"
        print(
            "[knowledge-backup] scheduler: reconcile now, then 00:00 Asia/Riyadh",
            flush=True,
        )

    research_scheduler_enabled = os.environ.get(
        "ENABLE_KNOWLEDGE_RESEARCH_SCHEDULER", "1"
    ) == "1"
    if research_scheduler_enabled:
        from sana_research_cycle import start_scheduler
        start_scheduler(_connect_pg)
        _RUNTIME_HEALTH["workers"]["knowledge_research"] = "running"
        print("[knowledge-research] scheduler: checks every 15 minutes", flush=True)

    billing_retry_scheduler_enabled = os.environ.get(
        "ENABLE_BILLING_NOTIFICATION_RETRY_SCHEDULER", "1"
    ) == "1"
    if billing_retry_scheduler_enabled:
        start_billing_notification_retry_scheduler(_connect_pg)
        _RUNTIME_HEALTH["workers"]["billing_notification_retry"] = "running"
        print(
            "[billing-notification-retry] scheduler: checks every 60 seconds",
            flush=True,
        )


_startup_thread_started = False
_startup_thread_lock = _threading.Lock()


def _launch_startup_thread():
    global _startup_thread_started
    with _startup_thread_lock:
        if _startup_thread_started:
            return None
        _startup_thread_started = True
    thread = _threading.Thread(
        target=_initialize_and_start_schedulers,
        name="sana-startup-initialization",
        daemon=True,
    )
    thread.start()
    return thread

def _start_startup_initialization(is_serving_process):
    """Start schema work in the background so the workflow can bind its port."""
    if not is_serving_process:
        return None
    return _launch_startup_thread()


@app.route("/api/knowledge/drive/sources", methods=["POST"])
def drive_source_link_api():
    from drive_index import link_drive_source
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    result = link_drive_source(
        get_db(), body.get("drive_file_id"), body.get("source_id"),
        body.get("source_type", "drive_metadata"), body.get("section_locator"),
        body.get("version_label"), body.get("quality"),
        body.get("review_status", "pending_review"), body.get("object_id"),
    )
    return jsonify(result), (201 if result.get("success") else 400)

@app.route("/api/knowledge/drive/files/<drive_file_id>/extract", methods=["POST"])
def drive_file_extract_api(drive_file_id):
    from drive_index import extract_selected_drive_excerpt
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    account = current_account() or {}
    result = extract_selected_drive_excerpt(
        get_db(), drive_file_id, section_locator=body.get("section_locator"),
        start_char=body.get("start_char", 0), end_char=body.get("end_char", 4000),
        case_id=body.get("case_id"),
        actor=account.get("account_id") or "admin-preview",
        actor_company_id=account.get("company_id") or body.get("company_id"),
    )
    return jsonify(result), (201 if result.get("success") else 400)

@app.route("/api/knowledge/drive/excerpts", methods=["GET"])
def drive_pending_excerpts_api():
    from drive_index import list_pending_drive_excerpts
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    account = current_account() or {}
    company_id = (
        account.get("company_id")
        or request.args.get("company_id")
        or default_company_id()
    )
    if company_id:
        scope_guard = enforce_entity_company_scope(company_id)
        if scope_guard:
            return scope_guard
    return jsonify({
        "success": True,
        "data": list_pending_drive_excerpts(
            get_db(), company_id=company_id, limit=request.args.get("limit", 100)
        ),
    })
@app.route("/api/knowledge/drive/context", methods=["GET"])
def drive_context_files_api():
    from drive_index import select_context_files
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    account = current_account()
    company_id = account["company_id"] if account else request.args.get("company_id")
    if not company_id:
        return jsonify({"success": False, "error": "COMPANY_REQUIRED"}), 400
    scope_guard = enforce_entity_company_scope(company_id)
    if scope_guard:
        return scope_guard
    return jsonify({
        "success": True,
        "data": select_context_files(
            get_db(), company_id, request.args.get("case_id"),
            request.args.get("topic"), request.args.get("limit", 25),
        ),
        "content_read": False,
        "message": "هذه قائمة اختيار Metadata فقط؛ وجود رابط Drive لا يعني قراءة المحتوى.",
    })

@app.route("/api/knowledge/drive/provenance", methods=["POST"])
def drive_provenance_create_api():
    from drive_index import add_drive_provenance
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    result = add_drive_provenance(
        get_db(), entity_type=body.get("entity_type"),
        entity_id=body.get("entity_id"), drive_file_id=body.get("drive_file_id"),
        source_id=body.get("source_id"), source_link_id=body.get("source_link_id"),
        section_locator=body.get("section_locator"),
        relationship=body.get("relationship", "supports"),
    )
    return jsonify(result), (201 if result.get("success") else 400)

@app.route("/api/knowledge/drive/coverage")
def drive_coverage_report_api():
    from drive_index import coverage_report
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    return jsonify({"success": True, "data": coverage_report(get_db())})

@app.route("/api/knowledge/drive/client-mappings/<mapping_id>", methods=["PATCH"])
def drive_client_mapping_review_api(mapping_id):
    from drive_index import review_client_mapping
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    result = review_client_mapping(
        get_db(), mapping_id, body.get("review_status"),
        (current_account() or {}).get("account_id") or "admin-preview",
    )
    return jsonify(result), (200 if result.get("success") else 400)

@app.route("/api/knowledge/drive/files", methods=["GET"])
def drive_index_files_api():
    from drive_index import list_drive_files
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    company_id = request.args.get("company_id") or None
    if company_id:
        scope_guard = enforce_entity_company_scope(company_id)
        if scope_guard:
            return scope_guard
    return jsonify({
        "success": True,
        "data": list_drive_files(
            get_db(), company_id=company_id, query=request.args.get("q", ""),
            limit=request.args.get("limit", 100),
        ),
    })

@app.route("/api/knowledge/drive/sync", methods=["POST"])
def drive_index_sync_api():
    from drive_index import sync_drive_metadata
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    result = sync_drive_metadata(get_db(), trigger_type="manual")
    return jsonify(result), (200 if result.get("success") else 503)

@app.route("/api/knowledge/drive/client-mappings", methods=["GET", "POST"])
def drive_client_mappings_api():
    from drive_index import create_client_mapping, ensure_schema
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    db = get_db()
    ensure_schema(db)
    if request.method == "GET":
        rows = db.execute(
            """SELECT mapping_id,drive_folder_id,drive_folder_name,company_id,
                      confidence,review_status,created_by,created_at,reviewed_at
               FROM drive_client_folder_mappings ORDER BY created_at DESC"""
        ).fetchall()
        return jsonify({"success": True, "data": [dict(row) for row in rows]})
    body = request.get_json(silent=True) or {}
    result = create_client_mapping(
        db, body.get("drive_folder_id"), body.get("drive_folder_name"),
        body.get("company_id"), (current_account() or {}).get("account_id"),
    )
    return jsonify(result), (201 if result.get("success") else 400)

@app.route("/api/knowledge/drive/provenance/<object_id>")
def drive_object_provenance_api(object_id):
    from drive_index import provenance_for_object
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    return jsonify({"success": True, "data": provenance_for_object(get_db(), object_id)})


@app.route("/api/knowledge/drive/excerpts/<excerpt_id>/review", methods=["PATCH"])
def drive_excerpt_review_api(excerpt_id):
    from drive_index import review_drive_excerpt
    guard = _knowledge_admin_guard()
    if guard:
        return guard
    account = current_account() or {}
    body = request.get_json(silent=True) or {}
    result = review_drive_excerpt(
        get_db(), excerpt_id,
        decision=body.get("decision"),
        reason=body.get("reason"),
        references=body.get("references"),
        reviewer=account.get("account_id") or "admin-preview",
        objects=body.get("objects"),
        anonymized_text=body.get("anonymized_text"),
        anonymization_notes=body.get("anonymization_notes"),
        actor_company_id=account.get("company_id"),
    )
    status = 200 if result.get("success") else (
        404 if result.get("error") == "EXCERPT_NOT_FOUND" else 409
    )
    return jsonify(result), status


# تهيئة بيئة التطوير فقط. Railway لا يستدعي هذه الدالة تلقائيًا ولا ينفذ
# أي DDL/seed عند النشر؛ Supabase الحالية هي قاعدة السجل الموجودة مسبقًا.
_STARTUP_LOCK_KEY = 727310001  # رقم تعسفي ثابت خاص بإقلاع تطبيق سنع فقط


def _run_startup_migrations():
    lock_conn = psycopg2.connect(DATABASE_URL)
    lock_conn.autocommit = True
    try:
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (_STARTUP_LOCK_KEY,))
        try:
            init_db()
            seed_db()
            seed_decision_impacts()
        finally:
            with lock_conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (_STARTUP_LOCK_KEY,))
    finally:
        lock_conn.close()


def _enforce_web_process_invariants():
    """يمنع تشغيل أي scheduler معروف داخل Railway Web process."""
    if not IS_PRODUCTION:
        return
    scheduler_flags = {
        "ENABLE_EXECUTION_REMINDER_SCHEDULER",
        "ENABLE_KNOWLEDGE_BACKUP_SCHEDULER",
        "ENABLE_KNOWLEDGE_RESEARCH_SCHEDULER",
        "SANA_BILLING_CLEANUP_WORKER",
        "ENABLE_BILLING_NOTIFICATION_RETRY_SCHEDULER",
        "SANA_BILLING_NOTIFICATION_RETRY_WORKER",
    }
    # الغياب يعني 0: عدم ضبط المتغير لا يفتح scheduler بالخطأ.
    enabled = [name for name in scheduler_flags if os.environ.get(name, "0") == "1"]
    if enabled:
        raise RuntimeError(
            "Schedulers cannot run in the production web process: "
            + ", ".join(enabled)
        )

def _safe_contextual_reference_knowledge(db, company, case, bottleneck):
    try:
        from sana_knowledge import contextual_reference_knowledge
        bundle = contextual_reference_knowledge(
            db, company, case, bottleneck, limit=5
        )
        bundle["available"] = True
        return bundle
    except Exception:
        db.rollback()
        return _reference_knowledge_fallback()


def _client_source_classification(value):
    return {
        "Fact": "حقيقة موثقة",
        "Evidence": "دليل داعم",
        "Hypothesis": "احتمال يحتاج تحققًا",
        "Inference": "استنتاج من المعلومات",
        "Recommendation": "خطوة مقترحة",
    }.get(value, "معلومة تحتاج مراجعة")

def _client_source_label(source_type):
    return {
        "اكتشاف_ذاتي": "معلومة مقدمة من الشركة",
        "Discovery / Case": "وصف الشركة لحالتها",
        "Case": "وصف الحالة",
        "سجل تشغيل": "سجل تشغيلي",
    }.get(source_type, "مصدر معلومات")

def _client_missing_evidence(items):
    """يعرض المطلوب من العميل دون نسخ رموز المصادر الداخلية."""
    safe_items = []
    for item in items or []:
        text = str(item)
        text = text.replace(
            "Fact أو Evidence مستقل يدعم فرضية الاختناق قبل إصدار استنتاج أو توصية أو قرار",
            "معلومة مستقلة تدعم فرضية الاختناق قبل إصدار استنتاج أو توصية أو قرار",
        )
        text = text.replace(
            "تحديث البيانات القديمة قبل الاستنتاج:",
            "تحديث البيانات القديمة قبل الاستنتاج.",
        )
        text = (
            text.replace("Evidence", "معلومة")
            .replace("Fact", "معلومة")
            .replace("Discovery", "جلسة التعريف")
            .replace("Actual", "بيانات فعلية")
            .replace("SDS-001", "جلسة التعريف")
        )
        text = text.replace("معلومة مستقل", "معلومة مستقلة")
        text = text.replace("بيانات فعلية موثّق", "بيانات فعلية موثقة")
        text = re.sub(
            r"^(Knowledge|Operations|Brand|Data|Independence):",
            "المحور:",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"(?<![\w])(?:CASE|SCAN|EVIDENCE|ER|DEC|IMP|TSK)[A-Z0-9:_-]+",
            "المصدر المرتبط",
            text,
            flags=re.IGNORECASE,
        )
        safe_items.append(text)
    return list(dict.fromkeys(safe_items))


def _client_evidence_request_cycle(cycle, *, outcome=None):
    """يبني ملخصًا قابلًا للمشاركة لدورة الأدلة دون رموزها الداخلية."""
    cycle = cycle if isinstance(cycle, dict) else {}
    requests = [
        item for item in (cycle.get("requests") or [])
        if isinstance(item, dict)
    ]
    fingerprints = {
        str(item.get("fingerprint")).strip()
        for item in requests
        if item.get("fingerprint")
    }
    fingerprints.update(
        str(item).strip()
        for item in (cycle.get("requested_fingerprints") or [])
        if str(item).strip()
    )
    try:
        critical_count = int(cycle.get("critical_request_count"))
    except (TypeError, ValueError):
        critical_count = len(requests)
    critical_count = max(0, critical_count)
    closed_count = sum(
        1 for item in requests
        if str(item.get("status") or "").upper() != "PENDING"
    )
    outcome = str(outcome or cycle.get("outcome") or "").upper() or None
    outcome_labels = {
        "DECISION": "قرار",
        "CONDITIONAL_EXPERIMENT": "تجربة مشروطة",
        "UNKNOWN": "UNKNOWN — المعلومة غير متاحة الآن",
    }
    return {
        "outcome": outcome,
        "outcome_label": outcome_labels.get(outcome, "لم تُحسم بعد"),
        "critical_request_count": critical_count,
        "closed_request_count": closed_count,
        "fingerprint_count": len(fingerprints),
    }


def _client_readiness_view(status, *, score=None, message=None):
    """يرسم حالات القرار الداخلية بلغة التقرير التي يفهمها العميل."""
    status = status if status in {"READY", "CONDITIONAL", "NOT_READY"} else "NOT_READY"
    labels = {
        "READY": "جاهز",
        "CONDITIONAL": "مشروط",
        "NOT_READY": "غير جاهز",
    }
    default_messages = {
        "READY": "المعلومات الحالية كافية للانتقال إلى الخطوة التالية بعد المراجعة.",
        "CONDITIONAL": "يمكن البدء بخطوة محدودة قابلة للقياس، لكن لا نعتمد قرارًا كبيرًا بعد.",
        "NOT_READY": "نحتاج معلومات إضافية قبل اعتماد خطوة تنفيذية.",
    }
    return {
        "status": status,
        "label": labels[status],
        "message": message or default_messages[status],
        "score": score,
        "score_label": _client_confidence_label(score),
    }

def _build_client_confidence_view(
    scan,
    scan_status,
    *,
    has_run=False,
    baseline_valid=False,
    impact_review=None,
):
    """يبني عرض الثقة العام دون كشف provenance أو رموز قاعدة البيانات."""
    quality = scan.get("diagnostic_quality") or {}
    decision_confidence = scan.get("decision_confidence") or {}
    readiness = scan.get("decision_readiness") or "NOT_READY"
    score = decision_confidence.get("score")

    if not has_run or scan_status in {"NOT_RUN", "INCOMPLETE"}:
        pre_status = "NOT_READY"
        pre_message = "لم تكتمل المعلومات اللازمة بعد؛ لا نعتمد نتيجة قبل استكمالها."
    elif readiness == "READY" and quality.get("evidence_strength") == "STRONG":
        pre_status = "READY"
        pre_message = "المعلومات كافية لبناء قرار قابل للمراجعة."
    elif readiness in {"READY", "CONDITIONAL"}:
        pre_status = "CONDITIONAL"
        pre_message = "الصورة مفيدة لخطوة محدودة، وتحتاج تحققًا إضافيًا قبل قرار كبير."
    else:
        pre_status = "NOT_READY"
        pre_message = "المعلومات الحالية لا تكفي لاعتماد قرار تنفيذي."

    if scan_status == "COMPLETE":
        decision_status = "READY"
        decision_message = "تمت مراجعة القرار واعتماده لهذه النتيجة."
    elif readiness in {"READY", "CONDITIONAL"}:
        decision_status = "CONDITIONAL"
        decision_message = "يوجد اتجاه عملي، لكنه ينتظر المراجعة والاعتماد قبل التنفيذ."
    else:
        decision_status = "NOT_READY"
        decision_message = "لا يوجد قرار تنفيذي معتمد بعد."

    impact_status = "NOT_READY"
    impact_message = "لم يُقَس الأثر بعد؛ لا نعرض فترة مرجعية أو نتيجة غير موثقة."
    if impact_review:
        outcome = impact_review.get("impact_outcome")
        if outcome == "INCONCLUSIVE":
            impact_status = "CONDITIONAL"
            impact_message = "بدأ القياس، لكن النتيجة الحالية لا تكفي لإثبات الأثر."
        elif baseline_valid:
            impact_status = "READY"
            impact_message = "يوجد قياس موثق للنتيجة يمكن مراجعته مقابل الفترة السابقة."
        else:
            impact_status = "CONDITIONAL"
            impact_message = "توجد نتيجة مسجلة، لكن فترة المقارنة الكاملة غير متاحة بعد."

    return {
        "stages": [
            {
                "key": "pre_decision",
                "title": "قبل القرار",
                "view": _client_readiness_view(
                    pre_status, message=pre_message
                ),
            },
            {
                "key": "decision",
                "title": "القرار",
                "view": _client_readiness_view(
                    decision_status, score=score, message=decision_message
                ),
            },
            {
                "key": "impact",
                "title": "قياس الأثر",
                "view": _client_readiness_view(
                    impact_status, message=impact_message
                ),
            },
        ],
        "decision_score": score,
        "decision_label": _client_confidence_label(score),
        "quality_label": _client_quality_label(
            quality.get("evidence_strength")
        ),
        "data_label": _client_quality_label(
            quality.get("data_reliability")
        ),
    }

def _client_quality_label(value):
    return {
        "STRONG": "قوية",
        "MODERATE": "متوسطة",
        "WEAK": "محدودة",
        "HIGH": "مرتفعة",
        "MEDIUM": "متوسطة",
        "LOW": "محدودة",
    }.get(value, "غير مكتملة")

def reset_database_schema():
    """Restore the default schema selection after an isolated test run."""
    global DATABASE_SCHEMA
    global _BILLING_TEST_SCHEMA_PREVIOUS
    global _BILLING_TEST_SCHEMA_PREVIOUS_ENV
    global _BILLING_TEST_SCHEMA_STATE_SAVED
    if _BILLING_TEST_SCHEMA_STATE_SAVED:
        DATABASE_SCHEMA = _BILLING_TEST_SCHEMA_PREVIOUS
        if _BILLING_TEST_SCHEMA_PREVIOUS_ENV is None:
            os.environ.pop("SANA_DATABASE_SCHEMA", None)
        else:
            os.environ["SANA_DATABASE_SCHEMA"] = _BILLING_TEST_SCHEMA_PREVIOUS_ENV
        _BILLING_TEST_SCHEMA_PREVIOUS = None
        _BILLING_TEST_SCHEMA_PREVIOUS_ENV = None
        _BILLING_TEST_SCHEMA_STATE_SAVED = False
    else:
        DATABASE_SCHEMA = None
        os.environ.pop("SANA_DATABASE_SCHEMA", None)

def drop_billing_test_schema(schema):
    """Drop only a schema created for a billing test run."""
    if not schema or not str(schema).startswith("sana_billing_test_"):
        raise ValueError("BILLING_TEST_SCHEMA_INVALID")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"DROP SCHEMA IF EXISTS {_quote_schema_identifier(schema)} CASCADE"
            )
    finally:
        conn.close()

def start_billing_notification_retry_scheduler(connect_db, interval_seconds=60):
    """Start the in-process development scheduler; production uses an external runner."""
    def loop():
        while True:
            try:
                run_billing_notification_retry_cycle(connect_db)
            except Exception as exc:
                print(f"[billing-notification-retry] failed: {exc}", flush=True)
            time.sleep(interval_seconds)

    thread = _threading.Thread(
        target=loop, name="sana-billing-notification-retry", daemon=True
    )
    thread.start()
    return thread

def _billing_notification_content(notification_type, payload):
    """Rebuild billing email content from the non-sensitive outbox payload."""
    if notification_type not in _BILLING_NOTIFICATION_TYPES:
        raise ValueError("BILLING_NOTIFICATION_TYPE_INVALID")
    status = "past_due" if notification_type.endswith("past_due") else "canceled"
    if status == "past_due":
        subject = "تعثر دفع اشتراك سنع — مطلوب تحديث وسيلة الدفع"
        heading = "تعثر دفع اشتراك سنع"
        explanation = (
            "تعذر تحصيل دفعة الاشتراك الأخيرة. حدّث وسيلة الدفع أو راجع "
            "الاشتراك حتى تعود الخدمة للعمل."
        )
        link_label = "تحديث وسيلة الدفع أو مراجعة الاشتراك"
    else:
        subject = "تم إلغاء اشتراك سنع — ابدأ اشتراكًا جديدًا"
        heading = "تم إلغاء اشتراك سنع"
        explanation = (
            "تم إلغاء الاشتراك، ولذلك توقفت الخدمة. يمكنك بدء اشتراك جديد "
            "من صفحة الأسعار."
        )
        link_label = "بدء اشتراك جديد"
    recovery_url = str(payload.get("recovery_url") or "").strip()
    if not recovery_url:
        raise ValueError("BILLING_RECOVERY_URL_MISSING")
    html_body = (
        "<div dir='rtl'>"
        f"<h2>{html.escape(heading)}</h2>"
        f"<p>{html.escape(explanation)}</p>"
        f"<p><a href='{html.escape(recovery_url)}'>"
        f"{html.escape(link_label)}</a></p>"
        "</div>"
    )
    return subject, html_body

def process_billing_notification_retries(db, batch_size=25):
    """Send due billing outbox rows; claims make concurrent workers safe."""
    if not RESEND_API_KEY:
        return {"status": "disabled", "processed": 0, "sent": 0, "failed": 0}
    try:
        batch_size = max(1, min(int(batch_size), 100))
    except (TypeError, ValueError):
        batch_size = 25
    result = {"status": "processed", "processed": 0, "sent": 0, "failed": 0}
    for _ in range(batch_size):
        row = _claim_billing_notification_for_retry(db)
        if not row:
            break
        result["processed"] += 1
        try:
            payload = json.loads(row["payload_json"] or "{}")
            subject, html_body = _billing_notification_content(
                row["notification_type"], payload
            )
            resend.Emails.send({
                "from": "سنع <noreply@sanaclarity.com>",
                "to": [row["recipient_email"]],
                "subject": subject,
                "html": html_body,
            })
        except Exception as exc:
            _finish_billing_notification_retry(
                db, row, sent=False, error_code=type(exc).__name__,
            )
            result["failed"] += 1
        else:
            _finish_billing_notification_retry(db, row, sent=True)
            result["sent"] += 1
    return result

def run_billing_notification_retry_cycle(connect_db, batch_size=25):
    """Run one isolated retry cycle for a cron job or scheduler."""
    db = connect_db()
    try:
        return process_billing_notification_retries(db, batch_size=batch_size)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def _finish_billing_notification_retry(db, row, *, sent, error_code=None):
    """Release a claim only if it still belongs to this worker."""
    if sent:
        db.execute(
            """UPDATE admin_notification_outbox
               SET status='sent',sent_at=now(),error_code=NULL,
                   next_attempt_at=NULL,delivery_lock_token=NULL,
                   delivery_locked_at=NULL
               WHERE notification_id=? AND delivery_lock_token=?""",
            (row["notification_id"], row["delivery_lock_token"]),
        )
    else:
        attempt_count = int(row["attempt_count"])
        db.execute(
            """UPDATE admin_notification_outbox
               SET status='failed',error_code=?,
                   next_attempt_at=CASE
                     WHEN ? < ? THEN now() + (? * INTERVAL '1 second')
                     ELSE NULL
                   END,
                   delivery_lock_token=NULL,delivery_locked_at=NULL
               WHERE notification_id=? AND delivery_lock_token=?""",
            (
                (error_code or "RESEND_SEND_FAILED")[:80],
                attempt_count,
                _BILLING_NOTIFICATION_MAX_ATTEMPTS,
                _billing_retry_delay_seconds(attempt_count),
                row["notification_id"],
                row["delivery_lock_token"],
            ),
        )
    db.commit()

@app.route("/api/companies/<company_id>/memory/<memory_id>/confirm", methods=["POST"])
def company_memory_confirm_api(company_id, memory_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    actor = current_account() or {}
    db = get_db()
    from sana_company_memory import confirm_memory
    try:
        data = confirm_memory(
            db, company_id, memory_id,
            actor_id=actor.get("account_id") or "admin-preview",
            observed_at=payload.get("observed_at"),
            reason=payload.get("reason"),
            presented_version_id=payload.get("presented_version_id"),
        )
        db.commit()
        return jsonify({"success": True, "data": data})
    except (LookupError, ValueError) as exc:
        db.rollback()
        status = 404 if isinstance(exc, LookupError) else 400
        return jsonify({"success": False, "error": str(exc)}), status


@app.route("/api/companies/<company_id>/memory/conflicts/<conflict_id>/resolve", methods=["POST"])
def company_memory_conflict_api(company_id, conflict_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    actor = current_account() or {}
    db = get_db()
    from sana_company_memory import resolve_memory_conflict
    try:
        data = resolve_memory_conflict(
            db, company_id, conflict_id, action=payload.get("action"),
            actor_id=actor.get("account_id") or "admin-preview",
            reason=payload.get("reason"),
        )
        db.commit()
        return jsonify({"success": True, "data": data})
    except (LookupError, ValueError) as exc:
        db.rollback()
        status = 404 if isinstance(exc, LookupError) else 400
        return jsonify({"success": False, "error": str(exc)}), status

@app.route("/api/companies/<company_id>/memory/<memory_id>/history")
def company_memory_history_api(company_id, memory_id):
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    from sana_company_memory import list_memory_history
    data = list_memory_history(get_db(), company_id, memory_id)
    if not data:
        return jsonify({"success": False, "error": "MEMORY_NOT_FOUND"}), 404
    return jsonify({"success": True, "data": data})


if __name__ == "__main__":
    _enforce_web_process_invariants()
    _start_startup_initialization(is_serving_process=True)
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=False,
        use_reloader=False,
    )
