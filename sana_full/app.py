"""
سنع — الخادم الأساسي (MVP الحقيقي)
Sana Core Backend — Flask + SQLite

تشغيل:
    python3 app.py
ثم افتح المتصفح على:
    http://localhost:5000
"""
import sqlite3
import os
import json
from datetime import datetime
from flask import Flask, jsonify, request, render_template, g


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
DB_PATH = os.path.join(BASE_DIR, "sana.db")

app = Flask(__name__)


@app.after_request
def no_cache_html(response):
    """منع أي تخزين مؤقت للصفحات — Safari على iOS يُطبّق heuristic caching إن لم يُوجَّه صراحةً"""
    if "text/html" in response.content_type:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(force=False):
    if force and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    fresh = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
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
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        if "phase_label" not in existing_cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN phase_label TEXT")
        if "value_note" not in existing_cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN value_note TEXT")
        # نفس فكرة التصنيف تحت مرحلة، بالإضافة إلى حقل JSON لتفاصيل قرارات موضوعية
        # غنية (مثل ترتيب الخدمات) يحتاجها عرض متخصص (صفحة الخدمات) دون تفكيك نصوص.
        decision_cols = {row[1] for row in conn.execute("PRAGMA table_info(decisions)").fetchall()}
        if "phase_label" not in decision_cols:
            conn.execute("ALTER TABLE decisions ADD COLUMN phase_label TEXT")
        if "structured_data" not in decision_cols:
            conn.execute("ALTER TABLE decisions ADD COLUMN structured_data TEXT")
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
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        # ترقية جدول الوثائق المنهجية القديم لدعم نظام BOS (doc_type/version/bos_id)
        methodology_cols = {row[1] for row in conn.execute("PRAGMA table_info(methodology_docs)").fetchall()}
        if "doc_type" not in methodology_cols:
            conn.execute("ALTER TABLE methodology_docs ADD COLUMN doc_type TEXT DEFAULT 'GENERIC'")
        if "version" not in methodology_cols:
            conn.execute("ALTER TABLE methodology_docs ADD COLUMN version TEXT DEFAULT 'v1.0'")
        if "bos_id" not in methodology_cols:
            conn.execute("ALTER TABLE methodology_docs ADD COLUMN bos_id TEXT")
        # أعمدة تحليل الذكاء الاصطناعي — دليل مفرد وقضية كاملة (بدون كسر قواعد بيانات قديمة)
        evidence_cols = {row[1] for row in conn.execute("PRAGMA table_info(evidence)").fetchall()}
        if "ai_analysis" not in evidence_cols:
            conn.execute("ALTER TABLE evidence ADD COLUMN ai_analysis TEXT")
        if "ai_suggested_asset_id" not in evidence_cols:
            conn.execute("ALTER TABLE evidence ADD COLUMN ai_suggested_asset_id TEXT")
        cases_cols = {row[1] for row in conn.execute("PRAGMA table_info(cases)").fetchall()}
        if "ai_analysis" not in cases_cols:
            conn.execute("ALTER TABLE cases ADD COLUMN ai_analysis TEXT")
        conn.commit()
    conn.close()
    return fresh


def seed_db():
    """يزرع بيانات أثر مشرق كأول شركة حقيقية على النظام."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM companies")
    if cur.fetchone()[0] > 0:
        conn.close()
        return  # already seeded

    cur.execute("""INSERT INTO companies
        (company_id, name, sector, city, stage, employee_count, annual_revenue, vision, main_goal)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        ("C001", "أثر مشرق", "عطور - تصنيع", "المدينة المنورة", "نمو", 8, 350000,
         "أن نكون أثر يُشم قبل أن يُرى", "رفع قيمة الشركة عبر أصل المعرفة"))

    cur.execute("""INSERT INTO users (user_id, company_id, name, role, department, status)
        VALUES (?,?,?,?,?,?)""",
        ("U001", "C001", "الزبير", "Owner", "الإدارة العامة", "نشط"))

    assets = [
        ("A001", "C001", "Knowledge", "أصل المعرفة", 18, 70, "U001", "يحتاج تطوير"),
        ("A002", "C001", "Operations", "أصل التشغيل", 41, 55, "U001", "متوسط"),
        ("A003", "C001", "Brand", "أصل البراند", 72, 20, "U001", "قوي"),
        ("A004", "C001", "Data", "أصل البيانات", 65, 30, "U001", "قوي"),
        ("A005", "C001", "Independence", "أصل الاستقلال", 22, 82, "U001", "مهدد"),
    ]
    cur.executemany("""INSERT INTO assets
        (asset_id, company_id, asset_type, asset_name, current_score, fragility_score, owner_user_id, status)
        VALUES (?,?,?,?,?,?,?,?)""", assets)

    cur.execute("""INSERT INTO cases
        (case_id, company_id, case_title, case_type, case_status, declared_problem, real_question,
         related_asset_id, confidence_score, value_impact_estimate)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("CS001", "C001", "ضعف توثيق العمليات", "Diagnostic Case", "Diagnosed",
         "نشعر أننا نعتمد كثيرًا على شخص واحد",
         "هل غياب أي موظف رئيسي يوقف الإنتاج فعليًا؟",
         "A001", 75, "رفع أصل المعرفة بمقدار 12 نقطة خلال شهر"))

    cur.execute("""INSERT INTO evidence
        (evidence_id, company_id, case_id, asset_id, title, source_type, confidence)
        VALUES (?,?,?,?,?,?,?)""",
        ("E001", "C001", "CS001", "A001",
         "لا يوجد ملف SOP موثق لأي عملية تصنيع", "ملاحظة مباشرة", 55))

    cur.execute("""INSERT INTO decisions
        (decision_id, company_id, case_id, asset_id, title, recommended_action, reason,
         confidence_score, expected_impact, status)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("D001", "C001", "CS001", "A001", "توثيق أول SOP للتصنيع",
         "بناء ملف SOP واحد لعملية التعبئة خلال 7 أيام",
         "أضعف مؤشر حاليًا هو تغطية التوثيق (22%)، وهو يهدد الاستقلال والجودة",
         75, "رفع أصل المعرفة من 18 إلى 30 خلال شهر", "قيد التنفيذ"))

    cur.execute("""INSERT INTO tasks
        (task_id, company_id, decision_id, title, owner_user_id, due_date, status, priority)
        VALUES (?,?,?,?,?,?,?,?)""",
        ("TSK001", "C001", "D001", "كتابة أول مسودة SOP لعملية التعبئة",
         "U001", "2026-07-14", "قيد التنفيذ", "عالية"))

    conn.commit()
    conn.close()


def seed_decision_impacts():
    """يربط القرار D001 بعدة أصول دفعة واحدة — فقط إذا كان الجدول فارغًا."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM decision_asset_impacts")
    if cur.fetchone()[0] > 0:
        conn.close()
        return  # already seeded

    impacts = [
        ("IMP001", "D001", "A001", 5, 1),
        ("IMP002", "D001", "A002", 3, 0),
        ("IMP003", "D001", "A005", 3, 0),
    ]
    cur.executemany("""INSERT INTO decision_asset_impacts
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
    return render_template("01-ceo-home.html")


@app.route("/case/new")
def new_case():
    return render_template("04-new-case.html")


@app.route("/case/<case_id>")
def case_workspace(case_id):
    return render_template("02-case-workspace.html", case_id=case_id)


@app.route("/sop-builder")
def sop_builder():
    return render_template("05-sop-builder.html")


@app.route("/assessment")
def assessment():
    return render_template("06-assessment.html")


@app.route("/passport")
def business_passport():
    return render_template("03-business-passport.html")


@app.route("/services")
def services_page():
    return render_template("07-services.html")


@app.route("/methodology/<slug>")
def methodology_page(slug):
    return render_template("08-methodology.html", slug=slug)


# ------------------------------------------------------------------
# API — Companies
# ------------------------------------------------------------------

@app.route("/api/companies")
def companies_list():
    """قائمة كل الشركات المسجلة — تُستخدم في صفحة الدخول (بوابة صاحب سنع/العميل)."""
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
    """وثيقة منهجية عامة — مستقلة عن أي شركة، مرجع يمكن ربطه من أي Case Workspace."""
    db = get_db()
    doc = db.execute("SELECT * FROM methodology_docs WHERE slug=?", (slug,)).fetchone()
    if not doc:
        return jsonify({"success": False, "error": "DOC_NOT_FOUND"}), 404
    doc_dict = dict(doc)
    return jsonify({
        "success": True,
        "data": {
            "doc_id": doc["doc_id"],
            "slug": doc["slug"],
            "title": doc["title"],
            "subtitle": doc["subtitle"],
            "doc_type": doc_dict.get("doc_type"),
            "version": doc_dict.get("version"),
            "bos_id": doc_dict.get("bos_id"),
            **json.loads(doc["content"]),
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

    evidence = db.execute("SELECT * FROM evidence WHERE case_id=?", (case_id,)).fetchall()
    decisions = db.execute("SELECT * FROM decisions WHERE case_id=?", (case_id,)).fetchall()

    # مهام هذه القضية — عبر القرارات المرتبطة بها (tasks.decision_id -> decisions.case_id)
    decision_ids = [d["decision_id"] for d in decisions]
    tasks = []
    if decision_ids:
        placeholders = ",".join("?" * len(decision_ids))
        tasks = db.execute(
            f"SELECT * FROM tasks WHERE decision_id IN ({placeholders}) ORDER BY created_at ASC",
            decision_ids
        ).fetchall()

    return jsonify({
        "success": True,
        "data": {
            "case": dict(case),
            "evidence": [dict(e) for e in evidence],
            "decisions": [dict(d) for d in decisions],
            "tasks": [dict(t) for t in tasks],
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
        },
        "meta": {
            "disclaimer": "تقدير مبسّط للعرض فقط — ليس محرك SVS الرسمي المُوثَّق في المعمارية"
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


@app.route("/api/decisions/<decision_id>/approve", methods=["POST"])
def approve_decision(decision_id):
    db = get_db()
    decision = db.execute("SELECT * FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
    if not decision:
        return jsonify({"success": False, "error": "DECISION_NOT_FOUND"}), 404

    db.execute("UPDATE decisions SET status='معتمد' WHERE decision_id=?", (decision_id,))

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
        evidence_id = "E" + uuid.uuid4().hex[:6].upper()
        db.execute("""INSERT INTO evidence
            (evidence_id, company_id, case_id, asset_id, title, source_type, confidence)
            VALUES (?,?,?,?,?,?,?)""",
            (evidence_id, company_id, case_id, asset_id,
             supporting_evidence, "دليل تأسيسي", 50))

    db.commit()
    return jsonify({"success": True, "data": {"case_id": case_id}}), 201


# ------------------------------------------------------------------
# API — Evidence (add evidence = "رفع دليل")
# ------------------------------------------------------------------

@app.route("/api/companies/<company_id>/evidence", methods=["POST"])
def add_evidence(company_id):
    body = request.get_json(force=True)
    db = get_db()
    import uuid

    case_id = body.get("case_id")
    asset_id = body.get("asset_id")
    title = (body.get("title") or "").strip()
    source_type = body.get("source_type", "ملاحظة مباشرة")
    confidence = body.get("confidence", 50)

    # حماية من الحفظ المزدوج والتكرار: إن وُجد دليل مطابق تمامًا في المحتوى
    # (نفس الشركة + القضية + الأصل + العنوان + نوع المصدر) لا يُنشأ سجل جديد،
    # بل يُعاد نفس السجل الموجود مع علامة duplicate=true.
    existing = db.execute(
        """SELECT evidence_id FROM evidence
           WHERE company_id=? AND IFNULL(case_id,'')=IFNULL(?,'')
             AND IFNULL(asset_id,'')=IFNULL(?,'') AND title=?
             AND IFNULL(source_type,'')=IFNULL(?,'')""",
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

    evidence_id = "E" + uuid.uuid4().hex[:6].upper()
    db.execute("""INSERT INTO evidence (evidence_id, company_id, case_id, asset_id, title, source_type, confidence)
        VALUES (?,?,?,?,?,?,?)""",
        (evidence_id, company_id, case_id, asset_id, title, source_type, confidence))
    db.commit()
    return jsonify({
        "success": True,
        "data": {"evidence_id": evidence_id},
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
{"summary": "ملخص من جملة واحدة لما يكشفه الدليل", "confidence_assessment": رقم من 0 إلى 100 يعكس قوة هذا الدليل بمفرده, "suggested_asset_id": "معرّف الأصل الأكثر ارتباطًا من القائمة المعطاة أو null إن لم يكن واضحًا", "reasoning": "سبب مختصر لماذا هذا الأصل تحديدًا"}"""

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

    evidence_rows = db.execute("SELECT * FROM evidence WHERE case_id=?", (case_id,)).fetchall()
    evidence_text = "\n".join(f"- {e['title']} (مصدر: {e['source_type']}, ثقة: {e['confidence']}٪)"
                               for e in evidence_rows) or "لا توجد أدلة مسجَّلة بعد."

    assets = db.execute("SELECT asset_id, asset_type, asset_name, current_score FROM assets WHERE company_id=?",
                         (case["company_id"],)).fetchall()
    assets_list = "\n".join(f"- {a['asset_id']}: {a['asset_name']} (الدرجة: {a['current_score']})" for a in assets)

    system_prompt = """أنت محرك تشخيص داخل سنع. تقرأ كل الأدلة المسجَّلة لقضية واحدة معًا، لا كل دليل بمعزل.
قاعدة أساسية: لا تصدر حكمًا نهائيًا إن كانت الأدلة قليلة أو متضاربة — قل ذلك صراحة.
أجب بصيغة JSON فقط بهذا الشكل بالضبط:
{"overall_assessment": "تقييمك الكامل للسؤال الحقيقي بناءً على كل الأدلة مجتمعة، فقرة واحدة", "confidence_score": رقم 0-100, "recommended_decision_title": "عنوان قرار مقترح واحد قابل للتنفيذ", "recommended_reason": "سبب هذا القرار تحديدًا"}"""

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
        "UPDATE tasks SET status='منجزة', completed_at=datetime('now') "
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


if __name__ == "__main__":
    fresh = init_db()
    seed_db()
    seed_decision_impacts()
    print("=" * 60)
    print("سنع — الخادم يعمل الآن")
    print("افتح المتصفح على: http://localhost:5000")
    print("=" * 60)
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
