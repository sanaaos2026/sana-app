"""
سكربت ربط مهام الشهر الأول (مكتب المحامي) بنظام سنع الحقيقي:
لكل مهمة من الـ21 مهمة: قرار خاص بها (معتمد مسبقًا، لأنها خطة متفق عليها فعليًا مع العميل)
+ مهمة تنفيذية واحدة + تأثير على الأصل المناسب.

عند إنجاز أي مهمة من نفس مسار التطبيق العادي (زر "إنجاز" في القضية)،
يرتفع الأصل المرتبط تلقائيًا بنفس آلية سنع القائمة، ويظهر الأثر فورًا في جواز الشركة.

تشغيل: python3 seed_c002_month1_tasks.py
"""
import uuid
from datetime import datetime

# استخدم نفس طبقة الاتصال بقاعدة البيانات الحالية في المشروع (PostgreSQL عبر app.py)
from app import get_db, close_db, app

COMPANY_ID = "C002"

# كل مهمة: (العنوان، الأصل المستهدف، مقدار الأثر)
TASKS = [
    # التمركز والهوية → أصل البراند (7 مهام × +2)
    ("تحديد العملاء المستهدفين", "Brand", 2),
    ("اعتماد الخدمات الخمس الرئيسية", "Brand", 2),
    ("كتابة الرسالة التسويقية", "Brand", 2),
    ("صياغة القيمة المضافة", "Brand", 2),
    ("اعتماد الهوية البصرية", "Brand", 2),
    ("اعتماد شخصية العلامة التجارية", "Brand", 2),
    ("تحديد أسلوب ونبرة التواصل", "Brand", 2),
    # ملفات PDF الاحترافية → أصل البراند (3 مهام × +3)
    ("الملف التعريفي للمكتب (Company Profile)", "Brand", 3),
    ("ملف خدمات الشركات", "Brand", 3),
    ("ملف المستثمرين والتجار", "Brand", 3),
    # نظام التشغيل → أصل التشغيل (6 مهام × 2-3)
    ("إنشاء CRM", "Operations", 3),
    ("إعداد Pipeline", "Operations", 2),
    ("إعداد لوحة مؤشرات الأداء", "Operations", 3),
    ("إعداد الردود الجاهزة", "Operations", 2),
    ("إعداد نماذج الاستشارة", "Operations", 2),
    ("إعداد آلية متابعة العملاء", "Operations", 3),
    # تجهيز المحتوى → أصل المعرفة (4 مهام × +3)
    ("إعداد خطة محتوى لمدة 90 يومًا", "Knowledge", 3),
    ("كتابة السكربتات", "Knowledge", 3),
    ("تصميم القوالب", "Knowledge", 3),
    ("تصوير أول دفعة من الفيديوهات", "Knowledge", 3),
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

        # تحقق من عدم التكرار: لا نضيف مهام الشهر الأول مرتين
        existing = db.execute(
            "SELECT task_id FROM tasks WHERE company_id=? AND title=?",
            (COMPANY_ID, TASKS[0][0]),
        ).fetchone()
        if existing:
            print("مهام الشهر الأول مضافة بالفعل — لن تُكرَّر.")
            return

        created = 0
        for title, asset_type, impact in TASKS:
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
                    "بند من خطة الـ90 يومًا المتفق عليها مع العميل — الشهر الأول",
                    90, f"رفع أصل {asset_type} بمقدار {impact} نقاط عند الإنجاز", "معتمد",
                ),
            )

            task_id = "T" + uuid.uuid4().hex[:6].upper()
            db.execute(
                """INSERT INTO tasks (task_id, company_id, decision_id, title, status)
                   VALUES (?,?,?,?,?)""",
                (task_id, COMPANY_ID, decision_id, title, "لم تبدأ"),
            )

            impact_id = "IMP-" + uuid.uuid4().hex[:8].upper()
            db.execute(
                """INSERT INTO decision_asset_impacts (impact_id, decision_id, asset_id, score_impact, is_primary)
                   VALUES (?,?,?,?,?)""",
                (impact_id, decision_id, asset_id, impact, 1),
            )
            created += 1

        db.commit()
        print(f"تم إنشاء {created} مهمة من أصل {len(TASKS)}، كل واحدة مرتبطة بقرار معتمد وأثر على الأصل المناسب.")
        print("أي مهمة تُنجَز الآن من داخل القضية (CS002) سترفع الأصل المرتبط تلقائيًا وتنعكس في جواز الشركة.")


if __name__ == "__main__":
    run()
