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
from datetime import datetime
from flask import Flask, jsonify, request, render_template, g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "sana.db")

app = Flask(__name__)


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


# ------------------------------------------------------------------
# Views — الصفحات الأربع
# ------------------------------------------------------------------

@app.route("/")
def entry():
    return render_template("00-entry.html")


@app.route("/home")
def ceo_home():
    return render_template("01-ceo-home.html")


@app.route("/case/<case_id>")
def case_workspace(case_id):
    return render_template("02-case-workspace.html", case_id=case_id)


@app.route("/passport")
def business_passport():
    return render_template("03-business-passport.html")


# ------------------------------------------------------------------
# API — Companies
# ------------------------------------------------------------------

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

@app.route("/api/cases/<case_id>")
def case_detail(case_id):
    db = get_db()
    case = db.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not case:
        return jsonify({"success": False, "error": "CASE_NOT_FOUND"}), 404

    evidence = db.execute("SELECT * FROM evidence WHERE case_id=?", (case_id,)).fetchall()
    decisions = db.execute("SELECT * FROM decisions WHERE case_id=?", (case_id,)).fetchall()

    return jsonify({
        "success": True,
        "data": {
            "case": dict(case),
            "evidence": [dict(e) for e in evidence],
            "decisions": [dict(d) for d in decisions],
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
        },
        "meta": {
            "disclaimer": "تقدير مبسّط للعرض فقط — ليس محرك SVS الرسمي المُوثَّق في المعمارية"
        }
    })


# ------------------------------------------------------------------
# API — Decisions
# ------------------------------------------------------------------

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
    case_id = "CS" + uuid.uuid4().hex[:6].upper()
    db.execute("""INSERT INTO cases (case_id, company_id, case_title, declared_problem, real_question, case_status)
        VALUES (?,?,?,?,?, 'Open')""",
        (case_id, company_id, body.get("case_title", "قضية جديدة"),
         body.get("declared_problem", ""), body.get("real_question", "")))
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
    evidence_id = "E" + uuid.uuid4().hex[:6].upper()
    db.execute("""INSERT INTO evidence (evidence_id, company_id, case_id, asset_id, title, source_type, confidence)
        VALUES (?,?,?,?,?,?,?)""",
        (evidence_id, company_id, body.get("case_id"), body.get("asset_id"),
         body.get("title", ""), body.get("source_type", "ملاحظة مباشرة"), body.get("confidence", 50)))
    db.commit()
    return jsonify({"success": True, "data": {"evidence_id": evidence_id}}), 201


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

    cur = db.execute(
        "UPDATE tasks SET status='منجزة', completed_at=datetime('now') "
        "WHERE task_id=? AND status != 'منجزة'",
        (task_id,)
    )
    if cur.rowcount == 0:
        return jsonify({"success": False, "error": "TASK_ALREADY_COMPLETED"}), 409

    new_score = None
    decision = None
    if task["decision_id"]:
        decision = db.execute(
            "SELECT * FROM decisions WHERE decision_id=?", (task["decision_id"],)
        ).fetchone()

    if decision and decision["asset_id"]:
        asset = db.execute(
            "SELECT * FROM assets WHERE asset_id=?", (decision["asset_id"],)
        ).fetchone()
        if asset:
            new_score = min(100, asset["current_score"] + 5)
            db.execute(
                "UPDATE assets SET current_score=? WHERE asset_id=?",
                (new_score, asset["asset_id"])
            )

    db.commit()
    return jsonify({
        "success": True,
        "data": {
            "task_id": task_id,
            "status": "منجزة",
            "asset_id": decision["asset_id"] if decision else None,
            "new_asset_score": new_score
        }
    })


if __name__ == "__main__":
    fresh = init_db()
    seed_db()
    print("=" * 60)
    print("سنع — الخادم يعمل الآن")
    print("افتح المتصفح على: http://localhost:5000")
    print("=" * 60)
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
