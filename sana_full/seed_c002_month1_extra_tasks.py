"""
سكربت إضافة 3 إنجازات حقيقية حصلت اليوم لمكتب المحامي (C002)، خارج قائمة المهام الأصلية الـ20:
- استخراج شريحة جوال مخصصة للمكتب
- تخصيص جوال مخصص لأعمال المكتب (WhatsApp Business)
- عرض خطة الـ90 يومًا على العميل واعتمادها رسميًا

بخلاف سكربت المهام الأصلي، هذي المهام تُضاف "مُنجزة فورًا" (لا بانتظار ضغط زر لاحقًا)،
لأنها حصلت فعليًا اليوم، ويُطبَّق أثرها على الأصل المناسب مباشرة.

تشغيل: python3 seed_c002_month1_extra_tasks.py
"""
import uuid
from datetime import datetime

from app import get_db, close_db, app

COMPANY_ID = "C002"

# (العنوان، الأصل المستهدف، مقدار الأثر)
EXTRA_TASKS = [
    ("استخراج شريحة جوال مخصصة للمكتب", "Operations", 2),
    ("تخصيص جوال مخصص لأعمال المكتب (WhatsApp Business)", "Operations", 2),
    ("عرض خطة الـ90 يومًا على العميل واعتمادها رسميًا", "Brand", 2),
]


def run():
    with app.app_context():
        db = get_db()

        company = db.execute(
            "SELECT company_id FROM companies WHERE company_id=?", (COMPANY_ID,)
        ).fetchone()
        if not company:
            print("خطأ: الشركة C002 غير موجودة. شغّل seed_c002.py أولاً.")
            return

        case = db.execute(
            "SELECT case_id FROM cases WHERE company_id=? LIMIT 1", (COMPANY_ID,)
        ).fetchone()
        case_id = case["case_id"] if case else None

        assets = {
            row["asset_type"]: row["asset_id"]
            for row in db.execute(
                "SELECT asset_id, asset_type FROM assets WHERE company_id=?", (COMPANY_ID,)
            ).fetchall()
        }

        existing = db.execute(
            "SELECT task_id FROM tasks WHERE company_id=? AND title=?",
            (COMPANY_ID, EXTRA_TASKS[0][0]),
        ).fetchone()
        if existing:
            print("هذه الإنجازات مضافة بالفعل — لن تُكرَّر.")
            return

        for title, asset_type, impact in EXTRA_TASKS:
            asset_id = assets.get(asset_type)
            if not asset_id:
                print(f"تحذير: لم يُعثر على أصل {asset_type} — تخطّي '{title}'")
                continue

            decision_id = "D" + uuid.uuid4().hex[:6].upper()
            db.execute(
                """INSERT INTO decisions
                   (decision_id, company_id, case_id, asset_id, title, recommended_action,
                    reason, confidence_score, expected_impact, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    decision_id, COMPANY_ID, case_id, asset_id, title, title,
                    "إنجاز فعلي حصل خارج قائمة مهام الشهر الأول الأصلية — مُوثَّق بتاريخه الحقيقي",
                    95, f"رفع أصل {asset_type} بمقدار {impact} نقاط", "معتمد",
                ),
            )

            task_id = "T" + uuid.uuid4().hex[:6].upper()
            db.execute(
                """INSERT INTO tasks (task_id, company_id, decision_id, title, status, completed_at)
                   VALUES (?,?,?,?,?,to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))""",
                (task_id, COMPANY_ID, decision_id, title, "منجزة"),
            )

            impact_id = "IMP-" + uuid.uuid4().hex[:8].upper()
            db.execute(
                """INSERT INTO decision_asset_impacts (impact_id, decision_id, asset_id, score_impact, is_primary)
                   VALUES (?,?,?,?,?)""",
                (impact_id, decision_id, asset_id, impact, 1),
            )

            asset = db.execute("SELECT current_score FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
            new_score = min(100, asset["current_score"] + impact)
            db.execute("UPDATE assets SET current_score=? WHERE asset_id=?", (new_score, asset_id))
            print(f"✓ {title} — {asset_type}: {asset['current_score']} → {new_score}")

        db.commit()
        print("تم توثيق الإنجازات الثلاثة كمُنجزة بتاريخ اليوم، وانعكس أثرها في الأصول فورًا.")


if __name__ == "__main__":
    run()
