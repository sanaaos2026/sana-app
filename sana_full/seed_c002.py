"""
سكربت لمرة واحدة: يضيف شركة "مكتب المحامي محمد جابر الشهري" (C002) إلى قاعدة بيانات سنع
تحت تصنيف "مسار الإنقاذ السريع" (Rescue Track)، مع أصولها الخمسة، قضيتها التشخيصية،
أدلتها التأسيسية، قرار خطة الإنقاذ، ومهام المراحل الست.

تشغيل: python3 seed_c002.py
آمن لإعادة التشغيل: يتحقق من عدم وجود C002 مسبقًا قبل الإدراج.
"""
import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "sana.db")

COMPANY_ID = "C002"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM companies WHERE company_id=?", (COMPANY_ID,))
    if cur.fetchone()[0] > 0:
        print(f"{COMPANY_ID} موجودة مسبقًا — لن يُعاد الإدراج.")
        conn.close()
        return

    # 1) الشركة
    cur.execute("""INSERT INTO companies
        (company_id, name, sector, city, stage, employee_count, annual_revenue, vision, main_goal)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (COMPANY_ID,
         "مكتب المحامي محمد جابر الشهري",
         "خدمات قانونية (B2B - شركات)",
         "حي الشاطئ، جدة — فرع ثانٍ: المدينة المنورة",
         "مسار الإنقاذ السريع",
         None,
         0,
         "مكتب فخم بموقع مميز — أصل غير مستغل بانتظار تموضع واضح وعرض خدمات محدد لعملاء الشركات",
         "الوصول إلى أول 10 عملاء من الشركات وتحقيق 200,000 ريال أتعاب خلال 60 يومًا"))

    # 2) المالك
    cur.execute("""INSERT INTO users (user_id, company_id, name, role, department, status)
        VALUES (?,?,?,?,?,?)""",
        ("U002", COMPANY_ID, "محمد جابر الشهري", "Owner", "الإدارة العامة", "نشط"))

    # 3) الأصول الخمسة
    assets = [
        ("A006", COMPANY_ID, "Knowledge", "أصل المعرفة", 25, 60, "U002", "يحتاج تطوير"),
        ("A007", COMPANY_ID, "Operations", "أصل التشغيل", 12, 85, "U002", "بلا نظام لجلب العملاء"),
        ("A008", COMPANY_ID, "Brand", "أصل البراند", 20, 75, "U002", "غير مستغل — لا تموضع ولا عرض واضح"),
        ("A009", COMPANY_ID, "Data", "أصل البيانات", 8, 90, "U002", "شبه معدوم — صفر عملاء حتى الآن"),
        ("A010", COMPANY_ID, "Independence", "أصل الاستقلال", 30, 70, "U002", "معتمد كليًا على المالك"),
    ]
    cur.executemany("""INSERT INTO assets
        (asset_id, company_id, asset_type, asset_name, current_score, fragility_score, owner_user_id, status)
        VALUES (?,?,?,?,?,?,?,?)""", assets)

    # 4) القضية التشخيصية — مصنّفة مسار الإنقاذ السريع
    cur.execute("""INSERT INTO cases
        (case_id, company_id, case_title, case_type, case_status, declared_problem, real_question,
         related_asset_id, confidence_score, value_impact_estimate)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("CS002", COMPANY_ID,
         "قضية الإنقاذ السريع — غياب التموضع ونظام جلب العملاء",
         "مسار الإنقاذ السريع",
         "قيد التنفيذ",
         "صرفنا أكثر من 500,000 ريال حتى الآن، والمبيعات لا تزال صفرًا",
         "هل السبب الحقيقي هو غياب التموضع والعرض ونظام جلب العملاء — وليس نقص الميزانية أو ضعف الخدمة القانونية نفسها؟",
         "A008", 70,
         "الوصول إلى 200,000 ريال أتعاب خلال 60 يومًا عبر أول 10 عملاء شركات"))

    # 5) الأدلة التأسيسية
    evidence = [
        ("E100", COMPANY_ID, "CS002", "A010", "رقم الترخيص: 421592 (وزارة العدل)", "بيانات مؤسس", 90),
        ("E101", COMPANY_ID, "CS002", "A007", "الميزانية التسويقية المتاحة حاليًا: 1,000 ريال فقط", "بيانات مؤسس", 90),
        ("E102", COMPANY_ID, "CS002", "A009", "إجمالي الصرف حتى الآن يتجاوز 500,000 ريال بدون أي مبيعات فعلية", "بيانات مؤسس", 90),
        ("E103", COMPANY_ID, "CS002", "A008", "أصل غير مستغل: مكتب فخم بموقع مميز في حي الشاطئ بجدة، مع فرع ثانٍ في المدينة المنورة", "ملاحظة مباشرة", 80),
        ("E104", COMPANY_ID, "CS002", "A008", "لا يوجد تموضع واضح للمكتب في سوق الخدمات القانونية B2B، ولا عرض خدمات محدد، ولا نظام منهجي لجلب عملاء الشركات", "ملاحظة مباشرة", 80),
    ]
    cur.executemany("""INSERT INTO evidence
        (evidence_id, company_id, case_id, asset_id, title, source_type, confidence)
        VALUES (?,?,?,?,?,?,?)""", evidence)

    # 6) قرار خطة الإنقاذ الشاملة
    cur.execute("""INSERT INTO decisions
        (decision_id, company_id, case_id, asset_id, title, recommended_action, reason,
         confidence_score, expected_impact, status)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("D100", COMPANY_ID, "CS002", "A008",
         "تنفيذ خطة الإنقاذ السريع الشاملة (Rescue Track)",
         "تنفيذ ست مراحل متسلسلة خلال 60 يومًا: التشخيص، إعادة التموضع، بناء العروض الأربعة، الإنتاج، الهجوم (تواصل مباشر يومي)، ثم القياس والتحويل",
         "الشركة تصرف بلا عائد (أكثر من 500,000 ريال صرف مقابل صفر مبيعات) بسبب غياب التموضع والعرض ونظام جلب العملاء — وليس بسبب نقص الميزانية أو ضعف الخدمة",
         65,
         "الانتقال من صفر مبيعات إلى 200,000 ريال أتعاب خلال 60 يومًا عبر أول 10 عملاء شركات",
         "قيد التنفيذ"))

    cur.execute("""INSERT INTO decision_asset_impacts
        (impact_id, decision_id, asset_id, score_impact, is_primary)
        VALUES (?,?,?,?,?)""", ("IMP100", "D100", "A008", 6, 1))
    cur.executemany("""INSERT INTO decision_asset_impacts
        (impact_id, decision_id, asset_id, score_impact, is_primary)
        VALUES (?,?,?,?,?)""", [
        ("IMP101", "D100", "A007", 4, 0),
        ("IMP102", "D100", "A009", 3, 0),
    ])

    # 7) مهام المراحل الست — Case Workspace
    tasks = [
        ("TSK100", COMPANY_ID, "D100", "المرحلة 1 — التشخيص", "U002", "2026-07-13", "منجزة", "عالية"),
        ("TSK101", COMPANY_ID, "D100", "المرحلة 2 — إعادة التموضع (7 أيام)", "U002", "2026-07-20", "قيد التنفيذ", "عالية"),
        ("TSK102", COMPANY_ID, "D100", "المرحلة 3 — بناء العروض الأربعة", "U002", "2026-07-27", "لم تبدأ", "عالية"),
        ("TSK103", COMPANY_ID, "D100", "المرحلة 4 — الإنتاج (محتوى + هوية رقمية)", "U002", "2026-08-03", "لم تبدأ", "متوسطة"),
        ("TSK104", COMPANY_ID, "D100", "المرحلة 5 — الهجوم (تواصل مباشر يومي مع 20 شركة)", "U002", "2026-08-17", "لم تبدأ", "عالية"),
        ("TSK105", COMPANY_ID, "D100", "المرحلة 6 — القياس والتحويل", "U002", "2026-09-11", "لم تبدأ", "متوسطة"),
    ]
    cur.executemany("""INSERT INTO tasks
        (task_id, company_id, decision_id, title, owner_user_id, due_date, status, priority)
        VALUES (?,?,?,?,?,?,?,?)""", tasks)

    # وضع المهمة الأولى كمنجزة فعليًا (completed_at)
    cur.execute("UPDATE tasks SET completed_at=datetime('now') WHERE task_id='TSK100'")

    conn.commit()
    conn.close()
    print(f"تمت إضافة {COMPANY_ID} بنجاح.")


if __name__ == "__main__":
    main()
