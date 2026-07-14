"""
سكربت إضافة دليل موثَّق لقضية مكتب المحامي (CS002)
يوثّق حالة تنفيذ خطة الـ90 يومًا حتى تاريخه: ما أُنجز فعليًا (التخطيط والتوثيق)
مقابل ما لم يبدأ بعد (التنفيذ الفعلي).

يستخدم نفس طبقة الاتصال بقاعدة البيانات المستخدمة في app.py (get_db عبر
سياق تطبيق Flask) بدل فتح اتصال SQLite منفصل، لأن المشروع أصبح يعمل على
PostgreSQL.

تشغيل: python3 seed_c002_progress_update.py
"""
import uuid

from app import app, get_db, close_db

EVIDENCE_TITLE = "حالة تنفيذ خطة الـ90 يومًا — تحديث"

EVIDENCE_BODY = """[تحديث موثَّق يدويًا — لا تحليل ذكاء اصطناعي]

ما تم إنجازه فعليًا:
- توثيق الرؤية الكاملة للمكتب وتحويلها إلى نظام عمل واضح
- تحديد الأهداف الرقمية الدقيقة لكل محور (المبيعات، التواجد الرقمي، المحتوى) خلال 90 يومًا
- توثيق منهجية "سنع" لجلب العملاء (المراحل الخمس) وربطها بخدمات المكتب تحديدًا
- إعداد صفحة داخلية احترافية تعرض الخطة كاملة كمرجع مشترك

الخطوات القادمة (لم تبدأ بعد):
- الموقع الإلكتروني الرسمي للمكتب
- الملفات التعريفية الثلاثة بصيغة PDF
- إنشاء وتوحيد حسابات التواصل الاجتماعي
- نظام CRM لإدارة العملاء المحتملين
- إنتاج أول دفعة من المحتوى (تصاميم وفيديوهات)

الخلاصة: مرحلة التخطيط والتوثيق مكتملة. التنفيذ الفعلي لم يبدأ بعد."""


def run():
    db = get_db()

    company = db.execute(
        "SELECT company_id FROM companies WHERE company_id='C002'"
    ).fetchone()
    if not company:
        print("خطأ: الشركة C002 غير موجودة في هذه القاعدة. تأكد من تشغيل هذا على النسخة الصحيحة.")
        return

    case = db.execute(
        "SELECT case_id FROM cases WHERE company_id='C002' LIMIT 1"
    ).fetchone()
    case_id = case["case_id"] if case else None

    # تحقق من عدم التكرار: لا نضيف نفس التحديث مرتين
    existing = db.execute(
        "SELECT evidence_id FROM evidence WHERE company_id='C002' AND title=?",
        (EVIDENCE_TITLE,)
    ).fetchone()
    if existing:
        print(f"هذا الدليل موجود بالفعل ({existing['evidence_id']}) — لن يُكرَّر.")
        return

    evidence_id = "E" + uuid.uuid4().hex[:7].upper()
    db.execute(
        """INSERT INTO evidence
           (evidence_id, company_id, case_id, asset_id, title, source_type, confidence, ai_analysis)
           VALUES (?,?,?,?,?,?,?,?)""",
        (evidence_id, "C002", case_id, None, EVIDENCE_TITLE, "تحديث داخلي موثَّق", 90, EVIDENCE_BODY)
    )
    db.commit()
    print(f"تمت الإضافة بنجاح: {evidence_id} إلى القضية {case_id}")


if __name__ == "__main__":
    with app.app_context():
        try:
            run()
        finally:
            close_db()
