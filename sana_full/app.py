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
import decimal
from datetime import datetime, date, timedelta
from flask import Flask, jsonify, request, render_template, g, session, redirect, url_for, Response, abort
from flask.json.provider import DefaultJSONProvider
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect
import resend
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
DATABASE_URL = os.environ["DATABASE_URL"]

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
_session_secret = os.environ.get("SESSION_SECRET")
if not _session_secret:
    print(
        "WARNING: SESSION_SECRET not set, using ephemeral key"
        " - sessions will not persist across restarts",
        flush=True,
    )
app.secret_key = _session_secret or secrets.token_hex(32)

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
    "system_health", "static", "guide_page",
    "sectors_list",   # قائمة القطاعات — عامة بلا مصادقة
    "articles_list", "article_page", "api_articles_list",  # مقالات — عامة بلا مصادقة
    "sales_pipeline_page",  # B6: صفحة خط المبيعات — تتطلب جلسة، لكن تُعرض دون redirect loop
    "forgot_password", "reset_password",  # استعادة كلمة المرور — عامة بالضرورة
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


def default_company_id():
    """الشركة الافتراضية لعرض صفحات الواجهة — تُقرأ من جلسة الحساب الحقيقي المسجَّل
    إن وُجدت، وإلا (عرض داخلي للمشرف بلا حساب) تبقى C001 كما كانت دائمًا. هذا يسمح
    لقوالب HTML بأخذ قيمة صحيحة حتى بدون ?company_id= في الرابط، مع إبقاء السلوك
    القديم لروابط C001/C002 الصريحة يعمل كما هو دون أي تغيير."""
    account = current_account()
    return account["company_id"] if account else "C001"


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
    if force and _table_exists(conn, "companies"):
        conn.executescript("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    fresh = not _table_exists(conn, "companies")
    if fresh:
        with open(os.path.join(BASE_DIR, "schema.sql"), "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()
    else:
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
    # لكل شركة بلا رمز دعوة (سواء قاعدة بيانات جديدة أو قديمة) — ولّد رمزًا فريدًا
    for row in conn.execute("SELECT company_id FROM companies WHERE signup_code IS NULL").fetchall():
        code = f"SANA-{row[0]}-{secrets.token_hex(3).upper()}"
        conn.execute("UPDATE companies SET signup_code=? WHERE company_id=?", (code, row[0]))
    conn.commit()
    conn.close()
    return fresh


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


# ------------------------------------------------------------------
# Views — الصفحات الأربع
# ------------------------------------------------------------------

@app.route("/")
def entry():
    return render_template("00-landing.html")


@app.route("/home")
def ceo_home():
    account = current_account()
    if account:
        db = get_db()
        company = db.execute(
            "SELECT sector FROM companies WHERE company_id=?", (account["company_id"],)
        ).fetchone()
        if company and not company["sector"]:
            return redirect(url_for("sector_select"))
    return render_template("01-ceo-home.html", default_company_id=default_company_id())


@app.route("/case/new")
def new_case():
    return render_template("04-new-case.html", default_company_id=default_company_id())


@app.route("/case/<case_id>/next-step")
def case_next_step(case_id):
    account = current_account()
    if not account and not is_admin_preview():
        return redirect(url_for("login", next=request.full_path))
    ENABLED_CASES = {"CS002"}
    if case_id not in ENABLED_CASES:
        abort(404)
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
    # 1. يجب أن يكون المستخدم مسجّلاً (أو وضع العرض الداخلي)
    account = current_account()
    if not account and not is_admin_preview():
        return redirect(url_for("login", next=request.full_path))

    # 2. قيد مرحلي — مفعَّل حالياً لـCS002 فقط؛ لتوسعته لاحقاً: أزل هذه الكتلة فقط
    ENABLED_CASES = {"CS002"}
    if case_id not in ENABLED_CASES:
        abort(404)

    # 3. التحقق من أن القضية موجودة وتخص الشركة الصحيحة
    db = get_db()
    case = db.execute("SELECT company_id FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not case:
        abort(404)
    if account and case["company_id"] != account["company_id"]:
        abort(403)

    return render_template("02-case-workspace.html", case_id=case_id)


@app.route("/sop-builder")
def sop_builder():
    return render_template("05-sop-builder.html", default_company_id=default_company_id())


@app.route("/assessment")
def assessment():
    return render_template("06-assessment.html", default_company_id=default_company_id())


@app.route("/passport")
def business_passport():
    return render_template("03-business-passport.html", default_company_id=default_company_id())


@app.route("/services")
def services_page():
    return render_template("07-services.html", default_company_id=default_company_id())


@app.route("/sector-select")
def sector_select():
    """شاشة اختيار القطاع — تُعرض للشركات الموجودة التي لم تُحدِّد قطاعها بعد."""
    account = current_account()
    if not account:
        return redirect(url_for("login"))
    db = get_db()
    company = db.execute(
        "SELECT sector, sds_done FROM companies WHERE company_id=?", (account["company_id"],)
    ).fetchone()
    # لو القطاع موجود فعلاً → وجّه حسب حالة SDS
    if company and company["sector"]:
        return redirect(url_for("ceo_home") if company["sds_done"] else url_for("discovery"))
    return render_template("14-sector-select.html")


# ------------------------------------------------------------------
# تسجيل الدخول الحقيقي للعملاء — بريد إلكتروني + كلمة مرور مشفَّرة
# ------------------------------------------------------------------

@app.route("/signup", methods=["GET", "POST"])
def signup():
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

    if request.method == "GET":
        return render_template("11-onboarding.html")

    body = request.get_json(silent=True) or request.form
    name = (body.get("name") or "").strip()
    sector_key = (body.get("sector") or "").strip() or None
    sector_other = (body.get("sector_other") or "").strip() or None
    employee_count = body.get("employee_count")
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

    db = get_db()
    db.execute(
        "UPDATE companies SET name=?, sector=?, sector_other=?, employee_count=? WHERE company_id=?",
        (name, sector_key, sector_other if sector_key == "other" else None, employee_count, account["company_id"])
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
    """مسار مؤقت للتطوير — يسجّل دخول شركة معينة عبر admin_key ويحوّل للصفحة المطلوبة."""
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
    session["account_id"] = acc["account_id"]
    session["company_id"] = acc["company_id"]
    session["email"] = acc["email"]
    return redirect(redirect_to)


@app.route("/login", methods=["GET", "POST"])
def login():
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

    # تحقق من القطاع وحالة SDS لتحديد الوجهة
    company = db.execute(
        "SELECT sds_done, main_goal, sector FROM companies WHERE company_id=?", (account["company_id"],)
    ).fetchone()
    if company and not company["sector"]:
        redirect_to = "/sector-select"
    elif company and company["sds_done"] and company["main_goal"]:
        # sds_done=1 AND main_goal محفوظ → جلسة مكتملة فعلاً
        redirect_to = "/home"
    else:
        # sds_done=0 أو main_goal فارغ (جلسة ناقصة) → أكمل Discovery
        redirect_to = "/discovery"
    return jsonify({"success": True, "data": {"redirect": redirect_to}})


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
    # مسار معاينة للمطوّر (admin_key فقط — يُعيّن جلسة مؤقتة للشركة المحددة)
    admin_key = request.args.get("admin_key", "")
    company_id_param = request.args.get("company_id", "")
    if admin_key and admin_key == os.environ.get("ADMIN_PREVIEW_KEY", ""):
        if company_id_param:
            db = get_db()
            acc = db.execute(
                "SELECT * FROM user_accounts WHERE company_id=?", (company_id_param,)
            ).fetchone()
            if acc:
                session["account_id"] = acc["account_id"]
                session["company_id"] = acc["company_id"]
                session["email"]      = acc["email"]
                return render_template("06-sana-discovery.html")

    account = current_account()
    if not account:
        return redirect(url_for("login"))
    db = get_db()
    company = db.execute(
        "SELECT sds_done, main_goal, sector FROM companies WHERE company_id=?", (account["company_id"],)
    ).fetchone()
    if company and not company["sector"]:
        return redirect(url_for("sector_select"))
    # يُعيد لـ/home فقط إذا اكتملت الجلسة فعلاً (sds_done=1 AND main_goal محفوظ)
    # إذا main_goal فارغ رغم sds_done=1 → السماح بإعادة تقديم الجلسة
    if company and company["sds_done"] and company["main_goal"]:
        return redirect(url_for("ceo_home"))
    return render_template("06-sana-discovery.html")


@app.route("/api/discovery/save", methods=["POST"])
def discovery_save():
    """يحفظ إجابات SDS-001 ويُنشئ أول قضية تلقائيًا."""
    account = current_account()
    if not account:
        return jsonify({"success": False, "error": "UNAUTHORIZED"}), 401

    company_id = account["company_id"]
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
    q6    = (body.get("q6")      or "").strip()
    q7    = body.get("q7") or []   # multi-select → success_criteria

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

    def add_ev(title, asset_type=None):
        from sana_evidence import save_evidence as _save_ev
        _save_ev(
            db,
            company_id  = company_id,
            case_id     = case_id,
            asset_id    = assets_by_type.get(asset_type) if asset_type else None,
            title       = title,
            source_type = "اكتشاف_ذاتي",
            confidence  = 50,
        )

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
        add_ev(f"أهم أصل في الشركة اليوم: {q3}", q3_asset)

    # Q4 — مصدر الاكتساب → دليل على القضية
    # إذا كانت إجابة المتابعة "كبير" أو "متوسط" → ربط بإطار BOS-001
    if q4:
        add_ev(f"مصدر اكتساب العملاء: {q4}", "Operations")
    if q4_fu:
        add_ev(f"هشاشة مصدر العملاء: {q4_fu}", "Operations")
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
        add_ev(f"مستوى اعتماد الشركة على المؤسس: {q5}", "Independence")
    if q5_text:
        add_ev(f"ما سيتعطل عند غياب المؤسس: {q5_text}", "Independence")

    # Q6 — أسلوب اتخاذ القرار → evidence عام بوسم decision_style (بلا أصل محدد)
    if q6:
        add_ev(f"[decision_style] أسلوب اتخاذ القرار: {q6}")

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


@app.route("/methodology/<slug>")
def methodology_page(slug):
    # صفحة داخلية — فريق سنع فقط، لا يُكشف وجودها لحساب العميل
    if not is_admin_preview():
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
            "tasks": [dict(t) for t in tasks],
            "evidence_count": len(evidence),
        },
        "meta": {"generated_at": datetime.utcnow().isoformat() + "Z"}
    })


# ------------------------------------------------------------------
# API — Case Detail (شاشة القضية الكاملة)
# ------------------------------------------------------------------

@app.route("/api/methodology/<slug>")
def methodology_detail(slug):
    """وثيقة منهجية — داخلية بحتة، فريق سنع فقط."""
    if not is_admin_preview():
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
    db = get_db()
    case = db.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not case:
        return jsonify({"success": False, "error": "CASE_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(case["company_id"])
    if guard:
        return guard

    evidence = db.execute("SELECT * FROM evidence WHERE case_id=?", (case_id,)).fetchall()
    decisions = db.execute("SELECT * FROM decisions WHERE case_id=?", (case_id,)).fetchall()

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

    return jsonify({
        "success": True,
        "data": {
            "case":            dict(case),
            "evidence":        [dict(e) for e in evidence],
            "decisions":       [dict(d) for d in decisions],
            "tasks":           [dict(t) for t in tasks],
            "affected_assets": [dict(a) for a in affected_assets],
            "sop_docs":        sop_docs,
            "reports":         [],  # لا توجد جداول تقارير مرتبطة بالقضية حالياً
        }
    })


# ------------------------------------------------------------------
# API — Business Passport (ملخص القيمة — معادلة مبسّطة مؤقتة)
# ------------------------------------------------------------------

@app.route("/api/companies/<company_id>/passport")
def passport_summary(company_id):
    db = get_db()
    company = db.execute("SELECT * FROM companies WHERE company_id=?", (company_id,)).fetchone()
    if not company:
        return jsonify({"success": False, "error": "COMPANY_NOT_FOUND"}), 404

    assets = db.execute("SELECT * FROM assets WHERE company_id=?", (company_id,)).fetchall()
    avg_score = round(sum(a["current_score"] for a in assets) / len(assets)) if assets else 0

    # ⚠ معادلة مبسّطة مؤقتة لأغراض العرض فقط — وليست محرك SVS الحقيقي.
    # السبب: بناء SQS/SVS الكامل (10 محاور + معادلة ضربية) خطوة لاحقة متعمَّدة
    # بعد أن يثبت هذا الهيكل الأساسي قيمته أولاً — راجع سجل القرارات في المحادثة.
    base = (company["annual_revenue"] or 0) * 2.5
    quality_multiplier = avg_score / 100
    current_value = round(base * quality_multiplier)
    potential_value = round(base * min((avg_score + 35) / 100, 1.1))

    weakest = min(assets, key=lambda a: a["current_score"]) if assets else None
    strongest = max(assets, key=lambda a: a["current_score"]) if assets else None

    weakest_case = None
    if weakest:
        weakest_case = db.execute(
            "SELECT case_id FROM cases WHERE company_id=? AND related_asset_id=? ORDER BY opened_at DESC LIMIT 1",
            (company_id, weakest["asset_id"])
        ).fetchone()

    # SCORE-03: لا تُعرض الدرجة إلا بعد توفر دليل واحد + تقييم سريع مكتمل لأصل واحد على الأقل
    evidence_count = db.execute(
        "SELECT COUNT(*) as cnt FROM evidence WHERE company_id=?", (company_id,)
    ).fetchone()["cnt"]
    assessed_assets = [a for a in assets if (a["current_score"] or 0) > 0]
    score_ready = evidence_count >= 1 and len(assessed_assets) >= 1
    if not score_ready:
        missing = []
        if evidence_count < 1:
            missing.append("دليل واحد على الأقل")
        if len(assessed_assets) < 1:
            missing.append("تقييم سريع مكتمل لأصل واحد")
        score_missing_reason = "لاستعراض درجة الجودة، أضف " + " و".join(missing) + "."
    else:
        score_missing_reason = None

    return jsonify({
        "success": True,
        "data": {
            "company": dict(company),
            "quality_score": avg_score,
            "current_value": current_value,
            "potential_value": potential_value,
            "value_gap": potential_value - current_value,
            "assets": [dict(a) for a in assets],
            "weakest_asset": dict(weakest) if weakest else None,
            "strongest_asset": dict(strongest) if strongest else None,
            "weakest_asset_case_id": weakest_case["case_id"] if weakest_case else None,
            "score_ready": score_ready,
            "score_missing_reason": score_missing_reason,
        },
        "meta": {
            "disclaimer": "تقدير مبسّط للعرض فقط — ليس محرك SVS الرسمي المُوثَّق في المعمارية"
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

    avg_score = round(sum(a["current_score"] for a in assets) / len(assets)) if assets else 0
    base = (company["annual_revenue"] or 0) * 2.5
    quality_multiplier = avg_score / 100
    current_value = round(base * quality_multiplier)
    potential_value = round(base * min((avg_score + 35) / 100, 1.1))
    gap = potential_value - current_value

    lines = [
        "══════════════════════════════════════════",
        f"جواز سفر الشركة — {company['name']}",
        "══════════════════════════════════════════",
        "",
        f"تاريخ التصدير: {datetime.utcnow().strftime('%Y-%m-%d')}",
        "",
        "❶  القيمة",
        f"   القيمة الحالية: {current_value:,.0f}",
        f"   القيمة الممكنة: {potential_value:,.0f}",
        f"   الفجوة: {gap:,.0f}",
        f"   درجة الجودة: {avg_score}/100",
        "",
        "❷  خريطة الأصول (من الأضعف للأقوى)",
    ]
    for a in assets:
        lines.append(f"   {a['asset_name']}: {a['current_score']}/100")

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


# ------------------------------------------------------------------
# دالة مساعدة: بيانات جواز الشركة المجمَّعة (تُستخدم في HTML و PDF)
# ------------------------------------------------------------------
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
    base = (company["annual_revenue"] or 0) * 2.5
    current_value  = round(base * avg_score / 100)
    potential_value = round(base * min((avg_score + 35) / 100, 1.1))
    gap = potential_value - current_value

    assessed_assets = [a for a in assets if (a["current_score"] or 0) > 0]
    score_ready = evidence_count >= 1 and len(assessed_assets) >= 1

    if avg_score <= 30:
        score_hint = "🌱 نقطة بداية طبيعية — الدرجة ترتفع مع كل دليل تضيفه وقرار تنفّذه، لا مع مرور الوقت وحده."
    elif avg_score <= 60:
        score_hint = "📈 أساس جيد — هناك فجوة قابلة للتحسين مع كل قرار منجز."
    else:
        score_hint = "🏆 شركة متقدمة — حافظ على الزخم بقرارات منتظمة ومدعومة بالأدلة."

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

    return {
        "company":        dict(company),
        "export_date":    datetime.utcnow().strftime("%Y-%m-%d"),
        "avg_score":      avg_score,
        "current_value":  current_value,
        "potential_value": potential_value,
        "gap":            gap,
        "score_ready":    score_ready,
        "score_hint":     score_hint,
        "assets_sorted":  [dict(a) for a in assets],
        "asset_colors":   asset_colors,
        "tasks_by_phase": tasks_by_phase,
        "decisions":      [dict(d) for d in decisions],
        "weakest_asset":  dict(weakest) if weakest else None,
    }


@app.route("/api/companies/<company_id>/passport/report-pdf")
def passport_report_pdf(company_id):
    """يُنتج PDF جواز الشركة من القالب HTML ويُعيده كملف قابل للتنزيل."""
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
    encoded = _quote(f"جواز_{safe_name}.pdf", safe="")

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

    # DEC-01: مقياس النجاح إلزامي عند الاعتماد
    if not success_metric:
        return jsonify({"success": False, "error": "SUCCESS_METRIC_REQUIRED",
                        "message": "أدخل مقياس النجاح لاعتماد هذا القرار"}), 400

    db.execute(
        "UPDATE decisions SET status='معتمد', owner_name=?, due_date=?, success_metric=? WHERE decision_id=?",
        (owner_name, due_date, success_metric, decision_id)
    )

    # القانون الأول: أي قرار معتمد يجب أن ينتج عنه مهمة تنفيذية فعلية
    existing_task = db.execute(
        "SELECT * FROM tasks WHERE decision_id=?", (decision_id,)
    ).fetchone()
    if not existing_task:
        db.execute("""INSERT INTO tasks (task_id, company_id, decision_id, title, status, priority)
            VALUES (?,?,?,?,?,?)""",
            (f"TSK-{decision_id}", decision["company_id"], decision_id,
             f"تنفيذ: {decision['title']}", "لم تبدأ", "عالية"))

    db.commit()
    return jsonify({"success": True, "data": {"decision_id": decision_id, "status": "معتمد"}})


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
    db = get_db()
    evidence = db.execute("SELECT * FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
    if not evidence:
        return jsonify({"success": False, "error": "EVIDENCE_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(evidence["company_id"])
    if guard:
        return guard

    case = None
    if evidence["case_id"]:
        case = db.execute("SELECT * FROM cases WHERE case_id=?", (evidence["case_id"],)).fetchone()

    assets = db.execute("SELECT asset_id, asset_type, asset_name, current_score FROM assets WHERE company_id=?",
                         (evidence["company_id"],)).fetchall()
    assets_list = "\n".join(f"- {a['asset_id']}: {a['asset_name']} ({a['asset_type']}, الدرجة الحالية: {a['current_score']})"
                             for a in assets)

    system_prompt = """أنت محرك تشخيص داخل نظام سنع (Sana) لرفع قيمة الشركات الصغيرة والمتوسطة.
مهمتك: قراءة دليل واحد (ملاحظة كتبها صاحب الشركة) وتحليله بموضوعية.
لا تجامل، ولا تفترض أكثر مما يقوله النص فعليًا.
أجب بصيغة JSON فقط، بلا أي نص قبله أو بعده، بهذا الشكل بالضبط:
{"summary": "ملخص من جملة واحدة لما يكشفه الدليل", "confidence_assessment": رقم من 0 إلى 100 يعكس قوة هذا الدليل بمفرده, "suggested_asset_id": "معرّف الأصل الأكثر ارتباطًا من القائمة المعطاة أو null إن لم يكن واضحًا", "reasoning": "سبب مختصر لماذا هذا الأصل تحديدًا"}
التزم بهذا التنسيق دائمًا:
1. السطر الأول: الخلاصة الأهم فقط، جملة واحدة مباشرة.
2. بعده: نقاط قصيرة (لا فقرات)، كل نقطة سطر واحد.
3. آخر سطر: توصية واحدة محددة فقط — لا تطرح أكثر من سؤال واحد، ولا تكرر السؤال الأصلي.
4. الحد الأقصى: 80 كلمة لتحليل دليل واحد، 150 كلمة لتحليل قضية كاملة. لا تشرح، لا تُقدّم، لا تضف تحفظات غير ضرورية.
5. ممنوع تكرار المعلومة نفسها بصياغتين."""

    user_prompt = f"""القضية: {case['case_title'] if case else 'غير مرتبطة بقضية'}
السؤال الحقيقي المطروح: {case['real_question'] if case else '—'}

الدليل المطلوب تحليله:
"{evidence['title']}"
(نوع المصدر: {evidence['source_type']}، الثقة المعلَنة عند الإضافة: {evidence['confidence']}٪)

أصول الشركة المتاحة:
{assets_list}

حلّل هذا الدليل تحديدًا."""

    result = ask_sana_ai(system_prompt, user_prompt)
    if "error" in result:
        return jsonify({"success": False, "error": "AI_ERROR", "message": result["error"]}), 502

    try:
        parsed = json.loads(result["raw_text"])
    except (json.JSONDecodeError, KeyError):
        return jsonify({"success": False, "error": "AI_PARSE_ERROR",
                         "message": "تعذّر فهم رد الذكاء الاصطناعي", "raw": result.get("raw_text", "")}), 502

    db.execute(
        "UPDATE evidence SET ai_analysis=?, ai_suggested_asset_id=?, confidence=? WHERE evidence_id=?",
        (parsed.get("summary", ""), parsed.get("suggested_asset_id"),
         parsed.get("confidence_assessment", evidence["confidence"]), evidence_id)
    )
    db.commit()

    return jsonify({"success": True, "data": parsed})


@app.route("/api/cases/<case_id>/analyze", methods=["POST"])
def analyze_case(case_id):
    """حوار فكري: يقرأ كل أدلة القضية معًا ويقيّم السؤال الحقيقي ككل، لا دليلًا واحدًا بمعزل."""
    db = get_db()
    case = db.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not case:
        return jsonify({"success": False, "error": "CASE_NOT_FOUND"}), 404
    guard = enforce_entity_company_scope(case["company_id"])
    if guard:
        return guard

    evidence_rows = db.execute("SELECT * FROM evidence WHERE case_id=?", (case_id,)).fetchall()
    evidence_text = "\n".join(f"- {e['title']} (مصدر: {e['source_type']}, ثقة: {e['confidence']}٪)"
                               for e in evidence_rows) or "لا توجد أدلة مسجَّلة بعد."

    assets = db.execute("SELECT asset_id, asset_type, asset_name, current_score FROM assets WHERE company_id=?",
                         (case["company_id"],)).fetchall()
    assets_list = "\n".join(f"- {a['asset_id']}: {a['asset_name']} (الدرجة: {a['current_score']})" for a in assets)

    system_prompt = """أنت محرك تشخيص داخل سنع. تقرأ كل الأدلة المسجَّلة لقضية واحدة معًا، لا كل دليل بمعزل.
قاعدة أساسية: لا تصدر حكمًا نهائيًا إن كانت الأدلة قليلة أو متضاربة — قل ذلك صراحة.
أجب بصيغة JSON فقط بهذا الشكل بالضبط:
{"overall_assessment": "تقييمك الكامل للسؤال الحقيقي بناءً على كل الأدلة مجتمعة، فقرة واحدة", "confidence_score": رقم 0-100, "recommended_decision_title": "عنوان قرار مقترح واحد قابل للتنفيذ", "recommended_reason": "سبب هذا القرار تحديدًا"}
التزم بهذا التنسيق دائمًا:
1. السطر الأول: الخلاصة الأهم فقط، جملة واحدة مباشرة.
2. بعده: نقاط قصيرة (لا فقرات)، كل نقطة سطر واحد.
3. آخر سطر: توصية واحدة محددة فقط — لا تطرح أكثر من سؤال واحد، ولا تكرر السؤال الأصلي.
4. الحد الأقصى: 80 كلمة لتحليل دليل واحد، 150 كلمة لتحليل قضية كاملة. لا تشرح، لا تُقدّم، لا تضف تحفظات غير ضرورية.
5. ممنوع تكرار المعلومة نفسها بصياغتين."""

    user_prompt = f"""عنوان القضية: {case['case_title']}
المشكلة كما وُصفت: {case['declared_problem']}
السؤال الحقيقي: {case['real_question']}

كل الأدلة المسجَّلة ({len(evidence_rows)}):
{evidence_text}

أصول الشركة:
{assets_list}

قيّم هذه القضية ككل الآن."""

    result = ask_sana_ai(system_prompt, user_prompt)
    if "error" in result:
        return jsonify({"success": False, "error": "AI_ERROR", "message": result["error"]}), 502

    try:
        parsed = json.loads(result["raw_text"])
    except (json.JSONDecodeError, KeyError):
        return jsonify({"success": False, "error": "AI_PARSE_ERROR",
                         "message": "تعذّر فهم رد الذكاء الاصطناعي", "raw": result.get("raw_text", "")}), 502

    db.execute("UPDATE cases SET ai_analysis=?, confidence_score=? WHERE case_id=?",
               (parsed.get("overall_assessment", ""), parsed.get("confidence_score", case["confidence_score"]), case_id))
    db.commit()

    return jsonify({"success": True, "data": parsed})


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
    packs = db.execute("SELECT * FROM task_packs ORDER BY sector, name").fetchall()
    result = []
    for pack in packs:
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


# ==================================================================
# B6 — Sales CRM
# ==================================================================

SALES_STAGES = ["عميل محتمل", "مؤهل", "عرض مرسل", "تفاوض", "فوز", "خسارة"]
ACTIVE_STAGES = {"عميل محتمل", "مؤهل", "عرض مرسل", "تفاوض"}
ACTIVITY_TYPES = {"مكالمة", "اجتماع", "رسالة", "عرض", "ملاحظة"}


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
    return jsonify({
        "success": True,
        "data": {**dict(opp),
                 "activities": [dict(a) for a in activities],
                 "stage_history": [dict(h) for h in history]}
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


# ═══════════════════════════════════════════════════════════════════
# سنع الخبير — الجزء 3: واجهة الخبير العامة + محادثة Claude
# ═══════════════════════════════════════════════════════════════════

# ── System Prompt (سيُستبدل بالنص الكامل عند استلامه من المستشار) ──
EXPERT_SYSTEM_PROMPT = """\
أنت "سنع"، مساعد استشاري متخصص في استخراج المعرفة من الخبراء وتحويلها إلى مشاريع.
ساعد الخبير في التعريف بنفسه وخبرته. اسأل أسئلة محددة وعميقة.
[PLACEHOLDER — سيُستبدل بالبرومبت الكامل السبع مراحل]
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
            max_tokens=2000,
            system=EXPERT_SYSTEM_PROMPT,
            messages=history,
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:
        return jsonify({"success": False, "error": f"خطأ في الاتصال بـClaude: {str(e)}"}), 500

    # ── استخراج الحقائق من ردّ Claude ───────────────────────────
    import re as _re
    extracted_facts = []
    checkpoint_stage = None

    # بحث عن كتلة FACTS_JSON مخفية
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

    # بحث عن إشارة إكمال مرحلة
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


if __name__ == "__main__":
    fresh = init_db()
    seed_db()
    seed_decision_impacts()
    print("=" * 60)
    print("سنع — الخادم يعمل الآن")
    print("افتح المتصفح على: http://localhost:5000")
    print("=" * 60)
    port = int(os.environ.get("PORT", 5000))
    is_dev = os.environ.get("REPLIT_DEPLOYMENT") != "1"
    app.run(debug=is_dev, use_reloader=is_dev, host="0.0.0.0", port=port)
