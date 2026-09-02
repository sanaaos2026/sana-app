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
from urllib.parse import urlencode, urlparse
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
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

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
    "methodology_page", "methodology_detail",
    "system_health", "healthz", "static", "guide_page",
    "sectors_list",   # قائمة القطاعات — عامة بلا مصادقة
    "articles_list", "article_page", "api_articles_list",  # مقالات — عامة بلا مصادقة
    "forgot_password", "reset_password",  # استعادة كلمة المرور — عامة بالضرورة
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
    return {"account_id": session["account_id"], "company_id": session["company_id"], "email": session.get("email")}


def request_company_context():
    """سياق الشركة لهذا الطلب دون تحويل المعاينة الداخلية إلى جلسة عميل حقيقية."""
    account = current_account()
    if account:
        return {**account, "admin_preview": False}
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
        return account["company_id"]
    if is_admin_preview() and request.args.get("company_id"):
        return request.args["company_id"]
    return "C001"

def p0_template_context():
    """سياق موحّد للقوالب دون تسريب معرّفات الشركة في روابط العميل.

    العميل الحقيقي لا يحتاج أي query string: الـ API والروابط تستخدم الجلسة.
    المعاينة الإدارية القديمة تبقى قابلة للتنقل، لكن لا تعمل إلا بالمفتاح
    الذي تتحقق منه الحراسة قبل الوصول.
    """
    account = current_account()
    if account:
        return {
            "company_id": account["company_id"],
            "context_query": "",
            "is_admin_preview": False,
        }
    if is_admin_preview():
        params = {"admin_key": request.args["admin_key"]}
        if request.args.get("company_id"):
            params["company_id"] = request.args["company_id"]
        return {
            "company_id": default_company_id(),
            "context_query": "?" + urlencode(params),
            "is_admin_preview": True,
        }
    return {
        "company_id": "C001",
        "context_query": "",
        "is_admin_preview": False,
    }


def _company_start_redirect(account):
    """يعيد نقطة البداية القانونية لحساب عميل واحد."""
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


@app.before_request
def enforce_company_auth():
    # واجهة الخبير العامة — نظام مصادقة مستقل (access_code في الجلسة) لا علاقة له بحسابات الشركات
    if request.path.startswith(("/e/", "/api/e/")):
        return
    endpoint = request.endpoint
    if endpoint is None or endpoint in PUBLIC_ENDPOINTS:
        return

    account = current_account()

    # 1) أي مسار (صفحة أو API) يحمل بيانات شركة — يتطلب جلسة دخول حقيقية،
    #    أو مفتاح العرض الداخلي للمشرف. بدون أحدهما لا وصول إطلاقًا،
    #    سواء عبر المتصفح أو عبر استدعاء API مباشر.
    if not account and not is_admin_preview():
        if request.path.startswith("/api/"):
            return jsonify({
                "success": False, "error": "UNAUTHORIZED",
                "message": "يلزم تسجيل الدخول للوصول لهذه البيانات."
            }), 401
        return redirect(url_for("login", next=request.full_path))

    # 2) أي مسار API يحمل company_id في الرابط نفسه — لا يمكن لحساب مسجَّل
    #    الوصول إلا لشركته هو، حتى لو عدّل الرابط يدويًا
    if account and "company_id" in (request.view_args or {}):
        if request.view_args["company_id"] != account["company_id"]:
            return jsonify({
                "success": False, "error": "FORBIDDEN",
                "message": "لا تملك صلاحية الوصول لبيانات هذه الشركة."
            }), 403


def enforce_entity_company_scope(entity_company_id):
    """للمسارات التي لا تحمل company_id في الرابط (مثل /api/cases/<id>) —
    يتحقق أن الحساب المسجَّل (إن وُجد) يملك هذا السجل فعلًا قبل إرجاعه."""
    account = current_account()
    if account and entity_company_id != account["company_id"]:
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
        self._schema_lock_acquired = False

    def rollback(self):
        self._conn.rollback()
        self._schema_lock_acquired = False

    def close(self):
        self._conn.close()


def _connect_pg():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
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
        "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=?",
        (table_name,)
    ).fetchone()
    return row is not None


def _columns_of(conn, table_name):
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?",
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
            conn.executescript("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
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
        if "website_url" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN website_url TEXT")
        if "social_media_url" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN social_media_url TEXT")
        if "business_reference_url" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN business_reference_url TEXT")
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

        conn.commit()
        # طبقة المعرفة — ترقية غير هدّامة سواء كانت القاعدة جديدة أو قديمة.
        from sana_knowledge import ensure_schema as ensure_knowledge_schema
        ensure_knowledge_schema(conn)
        from sana_scan import ensure_schema as ensure_scan_schema
        ensure_scan_schema(conn)
        from sana_growth_os import ensure_schema as ensure_growth_schema, seed_growth_os
        ensure_growth_schema(conn)
        seed_growth_os(conn)
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
    if account and not is_admin_preview():
        return redirect(url_for("case_workspace", case_id=case_id))
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
    # 1. يجب أن يكون المستخدم مسجّلاً (أو وضع العرض الداخلي)
    account = current_account()
    if not account and not is_admin_preview():
        return redirect(url_for("login", next=request.full_path))

    # 2. التحقق من أن القضية موجودة وتخص الشركة الصحيحة.
    # القضايا الجديدة الناتجة من Discovery مسموحة؛ لا تقييد بمعرّف Demo ثابت.
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
        "INSERT INTO user_accounts (account_id, email, password_hash, company_id, referral_source) VALUES (?,?,?,?,?)",
        (account_id, email, generate_password_hash(password), company_id, referral_source)
    )
    db.commit()

    session.clear()
    session["account_id"] = account_id
    session["company_id"] = company_id
    session["email"] = email

    return jsonify({"success": True, "data": {"redirect": "/onboarding", "company_id": company_id}}), 201


@app.route("/onboarding", methods=["GET", "POST"])
def onboarding():
    """شاشة قصيرة بعد إنشاء الحساب مباشرة — تحدّث بيانات الشركة الفارغة التي أُنشئت تلقائيًا."""
    account = current_account()
    if not account:
        return redirect(url_for("login"))
    company = get_db().execute(
        """SELECT name, sector, sector_other, employee_count, business_description,
                  goal_90_days, primary_challenge, sds_done
           FROM companies WHERE company_id=?""",
        (account["company_id"],),
    ).fetchone()
    if company and company["sector"] and company["name"] != "شركة جديدة":
        return redirect(
            url_for("ceo_home") if company["sds_done"] else url_for("discovery")
        )

    if request.method == "GET":
        return render_template("11-onboarding.html", company=company or {})

    body = request.get_json(silent=True) or request.form
    name = (body.get("name") or "").strip()
    sector_key = (body.get("sector") or "").strip() or None
    sector_other = (body.get("sector_other") or "").strip() or None
    employee_count = body.get("employee_count")
    business_description = (body.get("business_description") or "").strip()
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
    if sector_key == "other" and not sector_other:
        return jsonify({"success": False, "error": "MISSING_SECTOR_OTHER", "message": "يرجى كتابة وصف قطاعك عند اختيار 'أخرى'."}), 400
    if not business_description or not goal_90_days or not primary_challenge:
        return jsonify({
            "success": False,
            "error": "INCOMPLETE_COMPANY_SETUP",
            "message": "أكمل وصف النشاط والهدف والتحدي قبل المتابعة.",
        }), 400

    db = get_db()
    db.execute(
        """UPDATE companies
           SET name=?, sector=?, sector_other=?, employee_count=?,
               business_description=?, goal_90_days=?, primary_challenge=?
           WHERE company_id=?""",
        (
            name,
            sector_key,
            sector_other if sector_key == "other" else None,
            employee_count,
            business_description,
            goal_90_days,
            primary_challenge,
            account["company_id"],
        )
    )
    db.commit()

    return jsonify({"success": True, "data": {"redirect": "/discovery"}})


@csrf.exempt   # يُستدعى خارجياً بـ admin_key عبر curl/scripts — لا browser session
@app.route("/api/admin/attach-account", methods=["POST"])
def admin_attach_account():
    """
    إنشاء حساب دخول جديد مرتبط بشركة موجودة — محمي بـ admin_key.
    Body JSON: { admin_key, email, password, company_id }
    """
    body = request.get_json(silent=True) or {}
    if not body.get("admin_key") or body["admin_key"] != os.environ.get("ADMIN_PREVIEW_KEY", ""):
        return jsonify({"success": False, "error": "UNAUTHORIZED"}), 401

    email    = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    cid      = (body.get("company_id") or "").strip()

    if not email or not password or not cid:
        return jsonify({"success": False, "error": "MISSING_FIELDS"}), 400

    db = get_db()

    # الشركة يجب أن تكون موجودة مسبقًا
    co = db.execute("SELECT company_id FROM companies WHERE company_id=%s", (cid,)).fetchone()
    if not co:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404

    # إذا الإيميل موجود، ارجع خطأ واضح
    ex = db.execute("SELECT account_id FROM user_accounts WHERE email=%s", (email,)).fetchone()
    if ex:
        return jsonify({"success": False, "error": "EMAIL_TAKEN",
                        "account_id": ex["account_id"]}), 409

    account_id = "ACC" + uuid.uuid4().hex[:10].upper()
    db.execute(
        "INSERT INTO user_accounts (account_id, email, password_hash, company_id) VALUES (%s,%s,%s,%s)",
        (account_id, email, generate_password_hash(password), cid)
    )
    db.commit()
    return jsonify({"success": True, "data": {"account_id": account_id, "company_id": cid}})


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

    if not email or not password:
        return jsonify({"success": False, "error": "MISSING_FIELDS", "message": "البريد الإلكتروني وكلمة المرور مطلوبان."}), 400

    db = get_db()
    account = db.execute("SELECT * FROM user_accounts WHERE email=?", (email,)).fetchone()
    if not account or not check_password_hash(account["password_hash"], password):
        return jsonify({"success": False, "error": "INVALID_CREDENTIALS", "message": "البريد الإلكتروني أو كلمة المرور غير صحيحة."}), 401

    session.clear()
    session["account_id"] = account["account_id"]
    session["company_id"] = account["company_id"]
    session["email"] = account["email"]
    session["is_admin"] = bool(account.get("is_admin"))

    return jsonify({
        "success": True,
        "data": {"redirect": _company_start_redirect(account)},
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
        "SELECT account_id, email FROM user_accounts WHERE email=?", (email,)
    ).fetchone()

    if account:
        token      = secrets.token_urlsafe(32)
        expires_at = now + timedelta(minutes=30)
        db.execute(
            """INSERT INTO password_reset_tokens (token, email, account_id, expires_at)
               VALUES (?, ?, ?, ?)""",
            (token, account["email"], account["account_id"], expires_at),
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
        "UPDATE user_accounts SET password_hash=? WHERE account_id=?",
        (generate_password_hash(password), row["account_id"]),
    )
    # استهلك الرمز فوراً (single-use)
    db.execute(
        "UPDATE password_reset_tokens SET used=true WHERE token=?", (token,)
    )
    db.commit()

    return jsonify({"success": True, "message": "تم تغيير كلمة المرور بنجاح. يمكنك تسجيل الدخول الآن."})


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
    if context["admin_preview"]:
        return render_template("06-sana-discovery.html")
    account = current_account()
    start = _company_start_redirect(account)
    if start == url_for("onboarding"):
        return redirect(start)
    if start == url_for("ceo_home"):
        return redirect(start)
    return render_template("06-sana-discovery.html")


@app.route("/api/discovery/save", methods=["POST"])
def discovery_save():
    """يحفظ إجابات SDS-001 ويُنشئ أول قضية تلقائيًا."""
    context = request_company_context()
    if not context:
        return jsonify({"success": False, "error": "UNAUTHORIZED"}), 401

    company_id = context["company_id"]
    db = get_db()

    # ضمان عدم التكرار — فقط إذا اكتملت الجلسة وحُفظت البيانات فعليًا (main_goal غير فارغ)
    # إذا كان sds_done=1 لكن main_goal فارغ: نسمح بإعادة الحفظ لأن البيانات ضاعت
    company = db.execute(
        "SELECT sds_done, main_goal FROM companies WHERE company_id=?", (company_id,)
    ).fetchone()
    if company and company["sds_done"] and company["main_goal"]:
        case = db.execute(
            "SELECT case_id FROM cases WHERE company_id=? ORDER BY opened_at ASC LIMIT 1",
            (company_id,)
        ).fetchone()
        return jsonify({"success": True, "data": {
            "case_id": case["case_id"] if case else None,
            "already_done": True
        }})

    body = request.get_json(silent=True) or {}
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
        "👥 الفريق": "Independence",
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
        if not comparison_start or not comparison_end:
            raise ValueError("فترة المقارنة مطلوبة حتى لا تُقرأ الأرقام خارج سياقها.")
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
    }})


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
        comparison = validate_context({
            "information_type": "Narrative",
            "period_start": body.get("comparison_start"),
            "period_end": body.get("comparison_end"),
        })
        if not all((
            baseline["period_start"], baseline["period_end"],
            comparison["period_start"], comparison["period_end"],
        )):
            raise ValueError("فترتا الأساس والمقارنة مطلوبتان.")
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

    return jsonify({
        "success": True,
        "data": {
            "company": dict(company),
            "quality_score": avg_score,
            "assets": [dict(a) for a in assets],
            "weakest_asset": dict(weakest_asset) if weakest_asset else None,
            "strongest_asset": dict(strongest_asset) if strongest_asset else None,
            "cases": [dict(c) for c in cases],
            "decisions": [dict(d) for d in decisions],
            "open_decisions_count": len(open_decisions),
            "top_decision": dict(open_decisions[0]) if open_decisions else None,
            "next_task": dict(active_tasks[0]) if active_tasks else None,
            "tasks": [dict(t) for t in tasks],
            "evidence_count": len(evidence),
        },
        "meta": {"generated_at": datetime.utcnow().isoformat() + "Z"}
    })


# ------------------------------------------------------------------
# غرفة القرار والتنفيذ
# ------------------------------------------------------------------

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
    from sana_knowledge import contextual_reference_knowledge, latest_diagnostic
    from sana_scan import latest_scan
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

    # SOP docs — لا يوجد ربط مباشر بين methodology_docs والقضايا حالياً
    sop_docs = []  # قابل للتوسعة: أضف عمود case_id لـmethodology_docs لاحقاً

    scan = latest_scan(db, case_id)
    reference_knowledge = contextual_reference_knowledge(
        db, company, case, (scan or {}).get("bottleneck"), limit=5
    )
    return jsonify({
        "success": True,
        "data": {
            "case":            dict(case),
            "company":         dict(company) if company else None,
            "evidence":        [dict(e) for e in evidence],
            "decisions":       [dict(d) for d in decisions],
            "tasks":           [dict(t) for t in tasks],
            "affected_assets": [dict(a) for a in affected_assets],
            "sop_docs":        sop_docs,
            "reports":         [],  # لا توجد جداول تقارير مرتبطة بالقضية حالياً
            "scan":             scan,
            "knowledge_diagnostic": latest_diagnostic(db, case_id),
            "reference_knowledge": reference_knowledge,
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

    fallback_case = db.execute(
        """SELECT case_id FROM cases
           WHERE company_id=?
           ORDER BY opened_at DESC, case_id DESC LIMIT 1""",
        (company_id,),
    ).fetchone()
    case_id = case_id or (fallback_case["case_id"] if fallback_case else None)
    report_url = f"/company/{company_id}/scan-report"
    assessment_url = "/assessment"
    case_url = f"/case/{case_id}" if case_id else assessment_url

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
            "label": "مكتمل — التقرير جاهز",
            "message": "اكتملت مدخلات Scan واعتماد مخرجاته. استخدم التقرير التنفيذي لمتابعة الأثر.",
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

    return jsonify({
        "success": True,
        "data": {
            "company": dict(company),
            "journey": journey,
            "scan": scan,
            "scan_status": journey["status"],
            "operational_score": operational_score,
            "score_progress": {
                "complete": len(complete_scores),
                "total": len(scan_scores),
            },
            "scan_asset_scores": scan_scores,
            # Legacy keys remain for clients that still deserialize them, but
            # financial valuation is intentionally unavailable.
            "quality_score": operational_score,
            "current_value": None,
            "potential_value": None,
            "value_gap": None,
            "assets": [dict(a) for a in assets],
            "weakest_asset": dict(weakest) if weakest else None,
            "strongest_asset": dict(strongest) if strongest else None,
            "weakest_asset_case_id": weakest_case["case_id"] if weakest_case else None,
            "score_ready": score_ready,
            "score_missing_reason": score_missing_reason,
            "evidence_count": evidence_count,
        },
        "meta": {
            "financial_value_status": "DEFERRED",
            "financial_value_note": (
                "التقييم المالي غير معروض — لا توجد منهجية وأدلة معتمدة تسمح بتحويل "
                "Sana Score إلى قيمة نقدية."
            ),
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


@app.route("/api/companies/<company_id>/sds-question")
def sds_question(company_id):
    """SDS-002 MVP: يختار الأصل الأقل أدلة ويعيد سؤاله — بلا جدول جديد."""
    guard = enforce_entity_company_scope(company_id)
    if guard:
        return guard
    db = get_db()
    assets = db.execute(
        "SELECT * FROM assets WHERE company_id=?", (company_id,)
    ).fetchall()
    if not assets:
        return jsonify({"success": False, "error": "NO_ASSETS"}), 404

    # COUNT مباشر من evidence لكل أصل — بلا عمود confidence جديد
    evidence_per_asset = {}
    for a in assets:
        cnt = db.execute(
            "SELECT COUNT(*) as cnt FROM evidence WHERE company_id=? AND asset_id=?",
            (company_id, a["asset_id"])
        ).fetchone()["cnt"]
        evidence_per_asset[a["asset_type"]] = {
            "count":      cnt,
            "asset_id":   a["asset_id"],
            "asset_name": a["asset_name"],
        }

    # اختر الأصل صاحب أقل عدد أدلة — عشوائيًا عند التساوي
    import random
    valid = {k: v for k, v in evidence_per_asset.items() if k in SDS_QUESTIONS}
    if not valid:
        return jsonify({"success": False, "error": "NO_QUESTION"}), 404
    min_count  = min(v["count"] for v in valid.values())
    candidates = [k for k, v in valid.items() if v["count"] == min_count]
    chosen     = random.choice(candidates)
    q          = SDS_QUESTIONS[chosen]

    return jsonify({
        "success": True,
        "data": {
            "asset_type":       chosen,
            "asset_id":         evidence_per_asset[chosen]["asset_id"],
            "asset_name":       evidence_per_asset[chosen]["asset_name"],
            "question":         q["text"],
            "options":          q["options"],
            "suggestions":      q["suggestions"],
            "evidence_per_asset": {k: v["count"] for k, v in evidence_per_asset.items()},
        }
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

    existing = db.execute(
        """SELECT * FROM decisions WHERE company_id=? AND case_id=?
           AND phase_label='P0' ORDER BY created_at DESC LIMIT 1""",
        (case["company_id"], case_id),
    ).fetchone()
    if existing:
        return jsonify({"success": True, "data": dict(existing), "meta": {"created": False}})

    scan = latest_scan(db, case_id)
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
            70,
            (scan.get("opportunity") or {}).get("statement"),
            "مقترح",
            "P0",
            json.dumps({
                "bottleneck": bottleneck,
                "proposed_decision": proposed,
            }, ensure_ascii=False),
            scan["scan_id"],
            json.dumps(evidence_ids, ensure_ascii=False),
        ),
    )
    db.commit()
    decision = db.execute(
        "SELECT * FROM decisions WHERE decision_id=?", (decision_id,)
    ).fetchone()
    return jsonify({"success": True, "data": dict(decision), "meta": {"created": True}}), 201


@app.route("/api/companies/<company_id>/passport/report-text")
def passport_report_text(company_id):
    db = get_db()
    company = db.execute("SELECT * FROM companies WHERE company_id=?", (company_id,)).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404

    assets = db.execute("SELECT * FROM assets WHERE company_id=? ORDER BY current_score ASC",
                         (company_id,)).fetchall()
    decisions = db.execute(
        "SELECT * FROM decisions WHERE company_id=? ORDER BY created_at DESC", (company_id,)
    ).fetchall()
    completed_tasks = db.execute(
        "SELECT * FROM tasks WHERE company_id=? AND status='منجزة' ORDER BY completed_at ASC",
        (company_id,)
    ).fetchall()

    journey = _scan_journey_details(company_id)

    lines = [
        "══════════════════════════════════════════",
        f"ملف Sana الموحد — {company['name']}",
        "══════════════════════════════════════════",
        "",
        f"تاريخ التصدير: {datetime.utcnow().strftime('%Y-%m-%d')}",
        "",
        "❶  حالة Sana Scan",
        f"   الحالة: {journey['status']} — {journey['label']}",
        f"   الإجراء التالي: {journey['action_label']}",
        "   Sana Score التشغيلي: N/A — Deferred حتى تكتمل المحاور وتُراجع بشريًا.",
        "   التقييم المالي: N/A — Deferred — لا توجد منهجية وأدلة مالية معتمدة.",
        "",
        "❷  خريطة الأصول — مؤشرات المصدر فقط",
    ]
    for a in assets:
        lines.append(f"   {a['asset_name']}: راجع نتيجة Scan ومصادرها")

    lines.append("")
    lines.append("❸  الإنجازات المنجزة")
    if completed_tasks:
        by_phase = {}
        for t in completed_tasks:
            key = t["phase_label"] or "مهام منجزة أخرى"
            by_phase.setdefault(key, []).append(t)
        for phase, phase_tasks in by_phase.items():
            lines.append(f"   ▸ {phase}")
            for t in phase_tasks:
                lines.append(f"      ✓ {t['title']}")
                if t["value_note"]:
                    lines.append(f"        القيمة: {t['value_note']}")
        lines.append("")
    else:
        lines.append("   لا توجد إنجازات مسجَّلة بعد.")
        lines.append("")

    lines.append("❹  القرارات")
    if decisions:
        for d in decisions:
            lines.append(f"   [{d['status']}] {d['title']}")
            if d["recommended_action"]:
                lines.append(f"      الإجراء الموصى به: {d['recommended_action']}")
            if d["reason"]:
                lines.append(f"      السبب: {d['reason']}")
            if d["confidence_score"] is not None:
                lines.append(f"      الثقة: {d['confidence_score']}٪")
            lines.append("")
    else:
        lines.append("   لا توجد قرارات مسجَّلة بعد.")
        lines.append("")

    # ❺ التموضع النهائي وترتيب الخدمات — من أحدث قرار معتمد يحمل structured_data
    positioning = next((d for d in decisions if d["structured_data"]), None)
    if positioning:
        sd = json.loads(positioning["structured_data"])
        lines.append("❺  التموضع النهائي وترتيب الخدمات")
        lines.append(f"   [{positioning['status']}] {positioning['title']}")
        lines.append("")
        lines.append("   الخدمات الرئيسية (Core):")
        for s in sd.get("core_services", []):
            flag = f" {s['flag']} ⭐" if s.get("flag") else ""
            lines.append(f"      • {s['title']}{flag}")
        lines.append("")
        lines.append("   الخدمات الداعمة (Supporting):")
        for s in sd.get("supporting_services", []):
            lines.append(f"      • {s['title']}")
        lines.append("")
        lines.append("   الخدمات المتخصصة (Specialized — بالطلب فقط):")
        for s in sd.get("specialized_services", []):
            lines.append(f"      • {s['title']}")
        if sd.get("excluded_note"):
            lines.append("")
            lines.append(f"   ملاحظة: {sd['excluded_note']}")
        if sd.get("marketing_message"):
            lines.append("")
            lines.append(f"   الرسالة التسويقية: {sd['marketing_message']}")
            for b in sd.get("marketing_bullets", []):
                lines.append(f"      {b}")
        lines.append("")

    lines.append("══════════════════════════════════════════")
    lines.append("أُنشئت بواسطة سنع — ليست معادلة SVS الرسمية، تقدير مبسّط للعرض فقط")
    lines.append("══════════════════════════════════════════")

    return jsonify({"success": True, "data": {"report_text": "\n".join(lines)}})

def _build_scan_report_context(company_id):
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
        scan["scan_id"] = latest_scan_row["scan_id"]
        scan["created_at"] = latest_scan_row["created_at"]
        scan["methodology_version"] = (
            scan.get("methodology_version") or latest_scan_row["methodology_version"]
        )
        scan["status"] = scan.get("status") or latest_scan_row["status"]

    latest_case = None
    if scan.get("case_id"):
        latest_case = db.execute(
            """SELECT case_id, case_title, declared_problem, real_question,
                      case_status, confidence_score
               FROM cases WHERE company_id=? AND case_id=?""",
            (company_id, scan["case_id"]),
        ).fetchone()
    if not latest_case:
        latest_case = db.execute(
            """SELECT case_id, case_title, declared_problem, real_question,
                      case_status, confidence_score
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
            "asset_id": source.get("asset_id"),
            "confidence": source.get("confidence"),
            "information_type": source.get("information_type") or "Narrative",
            "verification_status": source.get("verification_status") or "UNVERIFIED",
            "source_category": source.get("source_category") or "UNKNOWN",
            "period_start": source.get("period_start"),
            "period_end": source.get("period_end"),
            "unit": source.get("unit"),
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
        case_id=scan.get("case_id"),
        missing_evidence=scan.get("missing_evidence") or [],
    )

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
    if scan.get("case_id"):
        decisions = [
            dict(row) for row in db.execute(
                """SELECT * FROM decisions WHERE company_id=? AND case_id=?
                   ORDER BY created_at DESC, decision_id DESC""",
                (company_id, scan["case_id"]),
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

    if not initiatives and proposed_decision:
        proposed_sources = proposed_decision.get("source_ids") or []
        initiatives.append({
            "phase": "0–30 يومًا",
            "decision_id": None,
            "decision_title": "قرار مقترح للمراجعة البشرية",
            "task": proposed_decision.get("statement") or "جمع الدليل واعتماد القرار",
            "owner": "غير محدد — يلزم الاعتماد",
            "due_date": "غير محدد — يلزم الاعتماد",
            "kpi": "غير محدد — يلزم اعتماد مقياس نجاح",
            "baseline": "غير موثق",
            "target": "غير موثق",
            "evidence": _initiative_sources(source_ids=proposed_sources),
            "impact": opportunity.get("statement") if opportunity else "غير موثق",
            "status": "مقترح — مراجعة بشرية مطلوبة",
            "completeness": False,
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
        executive_answer = "لا يمكن اعتماد اختناق بعد؛ نحتاج Fact أو Evidence مستقلًا قبل القرار."
    else:
        executive_answer = "لم يُنتج التقرير اختناقًا قابلًا للاعتماد بعد."

    what_not_do = [
        "لا نبدأ توسعًا أو حملة جديدة قبل اعتماد الاختناق ومقياس نجاحه.",
        "لا نعامل Hypothesis أو Assumption كحقيقة تنفيذية.",
        "لا نعد بأثر مالي غير موثق؛ نثبت الأثر عبر KPI قبل وبعد.",
    ]
    if scan.get("missing_evidence"):
        what_not_do.insert(
            0, "لا نغلق بوابة الأدلة: البنود الناقصة أدناه تظل N/A — Deferred حتى تصل مصادرها."
        )

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
    # لا توجد منهجية مالية معتمدة؛ تبقى خانات التقييم المالي القديمة None حتى لا
    # تتحول معادلة العرض السابقة إلى تقييم نقدي مضلل.
    current_value = potential_value = gap = None

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
    context["score_ready"] = context["operational_score"] is not None
    from sana_knowledge import contextual_reference_knowledge
    context["reference_knowledge"] = contextual_reference_knowledge(
        db,
        company,
        context.get("scan_case"),
        context.get("scan_bottleneck"),
        limit=5,
    )
    return context

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
    ctx = _build_passport_context(company_id)
    return render_template("14-passport-report.html", **ctx)
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

    ctx = _build_passport_context(company_id)
    if not ctx:
        return jsonify({"success": False, "error": "BUILD_FAILED"}), 500

    html_string = render_template("14-passport-report.html", **ctx)

    # lazy import — weasyprint يحتاج libpango كـ system lib
    import weasyprint  # noqa: PLC0415

    # WeasyPrint: base_url مطلوب لتحميل الخطوط من Google Fonts
    pdf_bytes = weasyprint.HTML(
        string=html_string,
        base_url=request.host_url
    ).write_pdf()

    from urllib.parse import quote as _quote
    safe_name = ctx["company"]["name"].replace("/", "-")
    encoded = _quote(f"Sana-Scan_{safe_name}.pdf", safe="")

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
    body = request.get_json(force=True)
    db = get_db()

    case_id     = body.get("case_id")
    asset_id    = body.get("asset_id")
    title       = (body.get("title") or "").strip()
    source_type = body.get("source_type", "ملاحظة مباشرة")
    confidence  = int(body.get("confidence", 50))
    evidence_type = (body.get("evidence_type") or "Evidence").strip()
    source_ref = (body.get("source_ref") or "").strip()
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

    # حماية من الحفظ المزدوج: إن وُجد دليل مطابق تمامًا لا يُنشأ سجل جديد.
    existing = db.execute(
        """SELECT evidence_id FROM evidence
           WHERE company_id=? AND COALESCE(case_id,'')=COALESCE(?,'')
             AND COALESCE(asset_id,'')=COALESCE(?,'') AND title=?
             AND COALESCE(source_type,'')=COALESCE(?,'')""",
        (company_id, case_id, asset_id, title, source_type)
    ).fetchone()
    if existing:
        return jsonify({
            "success": True,
            "data": {"evidence_id": existing["evidence_id"]},
            "meta": {
                "duplicate": True,
                "message": "هذا الدليل محفوظ مسبقًا بنفس المحتوى — لم يُنشأ سجل مكرر."
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

    return jsonify({
        "success": True,
        "data": {
            "evidence_id": result["evidence_id"],
            "asset_id":    result.get("asset_id"),
            "asset_name":  result.get("asset_name"),
            "new_score":   result.get("new_score"),
        },
        "meta": {"duplicate": False}
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
# API — Tasks (إنجاز المهمة = ترفع الأصل المرتبط بالقرار تلقائيًا)
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

    updated_assets = []
    if decision:
        impacts = db.execute(
            "SELECT * FROM decision_asset_impacts WHERE decision_id=? "
            "ORDER BY is_primary DESC, impact_id ASC",
            (decision["decision_id"],)
        ).fetchall()

        if impacts:
            for impact in impacts:
                asset = db.execute(
                    "SELECT * FROM assets WHERE asset_id=?", (impact["asset_id"],)
                ).fetchone()
                if not asset:
                    continue
                new_score = min(100, asset["current_score"] + impact["score_impact"])
                db.execute(
                    "UPDATE assets SET current_score=? WHERE asset_id=?",
                    (new_score, asset["asset_id"])
                )
                updated_assets.append({
                    "asset_id": asset["asset_id"],
                    "asset_name": asset["asset_name"],
                    "previous_score": asset["current_score"],
                    "new_score": new_score,
                    "is_primary": bool(impact["is_primary"])
                })
        elif decision["asset_id"]:
            # قرارات قديمة بلا سجلات تأثير — تحافظ على السلوك السابق (أصل واحد فقط)
            asset = db.execute(
                "SELECT * FROM assets WHERE asset_id=?", (decision["asset_id"],)
            ).fetchone()
            if asset:
                new_score = min(100, asset["current_score"] + 5)
                db.execute(
                    "UPDATE assets SET current_score=? WHERE asset_id=?",
                    (new_score, asset["asset_id"])
                )
                updated_assets.append({
                    "asset_id": asset["asset_id"],
                    "asset_name": asset["asset_name"],
                    "previous_score": asset["current_score"],
                    "new_score": new_score,
                    "is_primary": True
                })

    db.commit()
    return jsonify({
        "success": True,
        "data": {
            "task_id": task_id,
            "status": "منجزة",
            "updated_assets": updated_assets,
            # حقول متوافقة مع النسخة السابقة (أصل واحد) لتجنّب كسر أي مستهلك قديم
            "asset_id": updated_assets[0]["asset_id"] if updated_assets else None,
            "new_asset_score": updated_assets[0]["new_score"] if updated_assets else None
        }
    })


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

    raw_impact = evidence.get("expected_asset_impact")
    if not raw_impact:
        return {"error": "NO_IMPACT_DEFINED", "message": "المهمة لا تحمل expected_asset_impact"}

    try:
        impact_data = _json.loads(raw_impact)
        asset_id = impact_data["asset_id"]
        score_impact = int(impact_data["score_impact"])
    except Exception:
        return {"error": "INVALID_IMPACT_FORMAT"}

    # تحقق أن الأصل ينتمي لنفس الشركة
    asset = db.execute(
        "SELECT * FROM assets WHERE asset_id=? AND company_id=?",
        (asset_id, evidence["task_company_id"])
    ).fetchone()
    if not asset:
        return {"error": "ASSET_NOT_FOUND_OR_FORBIDDEN"}

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
        "task_id": task_id
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
        {"asset_name": r["asset_name"], "title": r["title"],
         "impact": r["score_impact"], "completed_at": r["completed_at"]}
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
        pending_list.append({"asset_name": r["asset_name"], "title": r["title"], "impact": r["score_impact"]})

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
            return

    try:
        seed_db()
        seed_decision_impacts()
        seed_knowledge_db()
    except Exception as exc:
        print(f"[startup] database seeding failed: {exc}", flush=True)

    # Railway's production web service is web-only. Scheduler ownership must
    # live in one explicit external runner, never in every Gunicorn worker.
    if os.environ.get("SANA_ENV", "").strip().lower() in {"production", "prod"}:
        print("[schedulers] disabled in production web runtime", flush=True)
        return

    reminder_scheduler_enabled = os.environ.get(
        "ENABLE_EXECUTION_REMINDER_SCHEDULER", "1"
    ) == "1"
    if reminder_scheduler_enabled:
        from sana_decision_room import start_reminder_scheduler
        start_reminder_scheduler(_connect_pg)
        print("[execution-reminders] scheduler: every 15 minutes", flush=True)

    scheduler_enabled = os.environ.get(
        "ENABLE_KNOWLEDGE_BACKUP_SCHEDULER", "1"
    ) == "1"
    if scheduler_enabled:
        from knowledge_backup import start_daily_scheduler
        start_daily_scheduler(_connect_pg)
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
        print("[knowledge-research] scheduler: checks every 15 minutes", flush=True)


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
    }
    # الغياب يعني 0: عدم ضبط المتغير لا يفتح scheduler بالخطأ.
    enabled = [name for name in scheduler_flags if os.environ.get(name, "0") == "1"]
    if enabled:
        raise RuntimeError(
            "Schedulers cannot run in the production web process: "
            + ", ".join(enabled)
        )
_enforce_web_process_invariants()

if __name__ == "__main__":
    print("=" * 60)
    print("سنع — الخادم يعمل الآن")
    print("افتح المتصفح على: http://localhost:5000")
    print("=" * 60)
    port = int(os.environ.get("PORT", 5000))
    is_dev = (
        os.environ.get("REPLIT_DEPLOYMENT") != "1"
        and os.environ.get("SANA_ENV", "").strip().lower() not in {"production", "prod"}
    )
    is_serving_process = not is_dev or os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    if os.environ.get("SANA_ENV", "").strip().lower() not in {"production", "prod"}:
        _start_startup_initialization(is_serving_process)
    app.run(debug=is_dev, use_reloader=is_dev, host="0.0.0.0", port=port)
