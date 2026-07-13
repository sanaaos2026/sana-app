"""
سكربت لمرة واحدة: يرفع وثيقة "نظام سنع لجلب العملاء" من v1.0 إلى BOS-001
الموسّع v2.0 (Sana Acquisition System) — نفس slug، محتوى موسّع بالكامل.

تشغيل: python3 upgrade_methodology_bos001.py
آمن لإعادة التشغيل: يحدّث الصف بمطابقة slug إن وجد، أو يُدرجه إن لم يوجد.
"""
import sqlite3
import os
import uuid
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "sana.db")

SLUG = "sana-acquisition-system"

SANA_ACQUISITION_SYSTEM = {
    "bos_id": "BOS-001",
    "slug": SLUG,
    "title": "نظام سنع لاكتساب العملاء",
    "subtitle": "Sana Acquisition System (SAS)",
    "version": "v2.0",
    "purpose": "تحويل التسويق والمبيعات والتواجد الرقمي إلى نظام واحد يقود العميل من الاكتشاف حتى الولاء، بدلاً من إدارات منفصلة.",
    "principle": "كل نقطة اتصال يجب أن تقرب العميل من قرار الشراء. لا نبني \"قسم تسويق\" ولا \"قسم مبيعات\" كوحدتين منفصلتين — بل نظام اكتساب عملاء واحد متكامل.",
    "clarity_rule": "أي نشاط لا يخدم انتقال العميل إلى المرحلة التالية فهو هدر.",

    "rule_groups": [
        {"title": "القواعد التأسيسية العشر", "rules": [
            "لا تبدأ بالنشر، ابدأ بالوجهة: من العميل؟ ماذا يريد؟ لماذا يشتري؟ لماذا يرفض؟",
            "لا تسوّق للجميع — كلما ضاقت الفئة المستهدفة ارتفع معدل التحويل.",
            "لا تبع الخدمة، بع النتيجة: زيادة مبيعات، توفير وقت، تقليل خسائر، راحة، وضوح.",
            "ابنِ الثقة قبل طلب الشراء — كل محتوى يقوم بأحد ثلاثة أدوار: تثقيف، إثبات، أو طمأنة.",
            "كل محتوى يقود إلى خطوة واحدة فقط: حجز، رسالة، زيارة، تحميل، أو اشتراك.",
            "الإعلان يضاعف الموجود ولا يصنع النجاح — عرض ضعيف + إعلان = فشل أسرع.",
            "لا تعتمد على قناة واحدة — العميل يجب أن يراك أكثر من مرة عبر مسار متعدد القنوات.",
            "سرعة الاستجابة جزء من التسويق — كل دقيقة تأخير تنقص احتمالية الإغلاق.",
            "كل عميل محتمل يدخل النظام (CRM، متابعة، تصنيف، قياس) — لا \"أرسل واتساب وانتهى\".",
            "ما لا يُقاس لا يمكن تطويره — قياس أسبوعي: الزوار ← العملاء المحتملون ← الاجتماعات ← العروض ← المبيعات ← الإيرادات.",
        ]},
        {"title": "قواعد التواجد الرقمي", "rules": [
            "كل منصة لها وظيفة واضحة: TikTok للاكتشاف، Instagram لبناء الثقة، LinkedIn لبناء السلطة، YouTube للتعليم، Google لمن يبحث ويقارن، الموقع للتحويل، WhatsApp للإغلاق.",
            "كل صفحة لها هدف واحد فقط (CTA واحد).",
            "الموقع مندوب مبيعات يعمل 24 ساعة، وليس تعريفا بالشركة.",
            "الهوية موحدة بالكامل: الشعار، اللغة، الألوان، الرسائل، الوعود.",
            "كل نقطة اتصال تجيب عن سؤال واحد: لماذا أثق بك؟",
        ]},
        {"title": "قواعد المبيعات", "rules": [
            "شخّص ثم بع.",
            "الأسئلة أهم من العرض.",
            "الاعتراضات تعني نقص وضوح، لا رفضًا نهائيًا.",
            "كل عرض يوضح بالترتيب: المشكلة ← التكلفة ← الحل ← النتيجة ← الخطوة التالية.",
            "المتابعة ليست إزعاجًا، بل جزء من الخدمة.",
        ]},
    ],

    "rule_categories": [
        {"name": "قاعدة الوجهة", "rule_numbers": [1]},
        {"name": "قاعدة النتيجة", "rule_numbers": [3]},
        {"name": "قاعدة الثقة", "rule_numbers": [4, 15]},
        {"name": "قاعدة القناة", "rule_numbers": [7, 11]},
        {"name": "قاعدة القياس", "rule_numbers": [10]},
        {"name": "قاعدة الوضوح", "rule_numbers": [2, 5, 6, 8, 9, 12, 13, 14, 16, 17, 18, 19, 20]},
    ],

    "stages": [
        {"name": "يرى", "question": "كيف يكتشفنا العميل؟", "goal": "صناعة الوعي وأول تعريف بالعلامة أمام جمهور جديد.",
         "kpis": "Reach، الانطباعات، عدد الزيارات الجديدة من مصادر غير مباشرة.",
         "assets": "أصل المحتوى، أصل البراند.",
         "decisions": "أي منصة نستثمر فيها هذا الشهر؟",
         "content": "محتوى اكتشاف خفيف على TikTok وInstagram — بلا طلب شراء مباشر.",
         "evidence": "عدد المشاهدات، نسبة الوصول لجمهور جديد.",
         "mistakes": "النشر بلا استهداف؛ قياس الإعجابات بدل الوصول الفعلي."},
        {"name": "يثق", "question": "لماذا يختارنا؟", "goal": "بناء ثقة كافية لينتقل العميل من المعرفة إلى الجدية.",
         "kpis": "Trust Score، معدل التفاعل العميق، عدد التقييمات الإيجابية.",
         "assets": "أصل البراند، أصل السمعة، أصل المحتوى، أصل الخبرة، أصل التقييمات.",
         "decisions": "هل نحتاج شهادات أو دراسات حالة إضافية؟",
         "content": "محتوى إثبات (نتائج، شهادات) وتثقيف عميق.",
         "evidence": "عدد الرسائل التي تسأل عن تفاصيل أعمق، معدل مشاهدة الصفحة كاملة.",
         "mistakes": "الاعتماد على الوعود بدل الإثبات؛ إهمال الرد على التقييمات السلبية."},
        {"name": "يتواصل", "question": "كيف يبدأ العلاقة؟", "goal": "تحويل الاهتمام إلى Lead فعلي.",
         "kpis": "معدل تحويل الزائر إلى Lead، عدد الرسائل أو الحجوزات الجديدة.",
         "assets": "أصل الموقع (CTA)، أصل قنوات التواصل المباشر.",
         "decisions": "أي CTA يحقق أعلى تحويل؟",
         "content": "صفحة هبوط بعرض واحد وزر إجراء واحد.",
         "evidence": "عدد النقرات على CTA، زمن الاستجابة الأول.",
         "mistakes": "أكثر من CTA بصفحة واحدة؛ بطء الرد على أول تواصل."},
        {"name": "يشتري", "question": "كيف نغلق الصفقة؟", "goal": "تحويل الـLead المؤهَل إلى عميل فعلي.",
         "kpis": "معدل إغلاق الصفقات، متوسط قيمة الصفقة.",
         "assets": "أصل فريق المبيعات، أصل العرض.",
         "decisions": "هل العرض يوضح المشكلة ← التكلفة ← الحل ← النتيجة بترتيب واضح؟",
         "content": "عرض مخصص مبني على تشخيص فعلي.",
         "evidence": "الاعتراضات المتكررة الموثّقة، نسبة العروض المقبولة.",
         "mistakes": "البيع قبل التشخيص؛ معاملة الاعتراض كرفض نهائي."},
        {"name": "يبقى ويُوصي", "question": "كيف نزيد القيمة؟", "goal": "تحويل العميل إلى إيراد متكرر ومصدر إحالات.",
         "kpis": "Retention Rate، Referral Rate، Customer Lifetime Value.",
         "assets": "أصل خدمة ما بعد البيع، أصل برنامج الإحالة.",
         "decisions": "متى نطلب الإحالة؟ كيف نكافئ العميل الدائم؟",
         "content": "متابعة دورية مجدولة، برنامج ولاء واضح.",
         "evidence": "عدد العملاء المتكررين، عدد الإحالات الفعلية.",
         "mistakes": "التوقف عن التواصل بعد البيع؛ عدم طلب الإحالة صراحة."},
    ],

    "engines": [
        {"name": "Content Engine", "description": "إنتاج المحتوى عبر مراحل الدورة كاملة."},
        {"name": "SEO Engine", "description": "الظهور لمن يبحث فعليًا عن الحل."},
        {"name": "Social Engine", "description": "الحضور والتفاعل عبر منصات التواصل."},
        {"name": "Referral Engine", "description": "تحويل العملاء الحاليين إلى مصدر عملاء جدد."},
        {"name": "Partnership Engine", "description": "شراكات توسّع الوصول دون إعلان مدفوع."},
        {"name": "Paid Ads Engine", "description": "تضخيم ما يعمل فعليًا، لا تعويض ضعف العرض."},
        {"name": "CRM Engine", "description": "تتبّع كل عميل محتمل من أول تواصل حتى الإغلاق."},
        {"name": "Follow-up Engine", "description": "متابعة منظمة بلا اعتماد على الذاكرة الفردية."},
        {"name": "Reputation Engine", "description": "إدارة التقييمات والسمعة الرقمية باستمرار."},
    ],

    "metrics_chain": ["Reach", "Trust Score", "Lead Conversion", "Sales Conversion", "Retention", "Referral Rate", "Customer Lifetime Value"],

    "ai_roles": [
        "اقتراح المحتوى — أفكار مبنية على ما نجح فعليًا في مراحل سابقة.",
        "تحليل الصفحة — تقييم وضوح CTA ومطابقته لقاعدة هدف واحد لكل صفحة.",
        "تقييم العرض — التحقق من ترتيب المشكلة ← التكلفة ← الحل ← النتيجة.",
        "كتابة الرسائل — صياغة متابعات ومراسلات متسقة مع الهوية.",
        "متابعة العملاء — تذكير تلقائي بمواعيد المتابعة المستحقة.",
        "تحليل الاعتراضات — تصنيف الاعتراضات المتكررة وربطها بفجوة وضوح.",
        "توقع الإغلاق — تقدير احتمالية إغلاق كل صفقة بناء على الأدلة المتوفرة.",
    ],

    "sops": [
        {"id": "SOP-001", "title": "كيف نبني حملة Awareness"},
        {"id": "SOP-002", "title": "كيف نحوّل زائرًا إلى Lead"},
        {"id": "SOP-003", "title": "كيف نرفع معدل الإغلاق"},
        {"id": "SOP-004", "title": "كيف نبني برنامج إحالات"},
    ],

    "linkage_chain": ["Sana OS", "Assets", "Knowledge Graph", "Decision Engine", "Reports", "AI", "Content System", "CRM", "Business Events"],

    "bos_map": [
        {"id": "BOS-001", "name": "Sana Acquisition System", "status": "معتمدة ✅"},
        {"id": "BOS-002", "name": "Sana Sales System", "status": "مخطط لها"},
        {"id": "BOS-003", "name": "Sana Marketing System", "status": "مخطط لها"},
        {"id": "BOS-004", "name": "Sana Customer Success System", "status": "مخطط لها"},
        {"id": "BOS-005", "name": "Sana Growth System", "status": "مخطط لها"},
        {"id": "BOS-006", "name": "Sana Strategy System", "status": "مخطط لها"},
        {"id": "BOS-007", "name": "Sana Operations System", "status": "مخطط لها"},
        {"id": "BOS-008", "name": "Sana Finance System", "status": "مخطط لها"},
        {"id": "BOS-009", "name": "Sana HR System", "status": "مخطط لها"},
        {"id": "BOS-010", "name": "Sana Innovation System", "status": "مخطط لها"},
    ],
}


def main():
    db = sqlite3.connect(DB_PATH)

    # تأكد من وجود الجدول وأعمدة BOS الجديدة (نفس آلية الترقية المستخدمة في app.py)
    db.execute("""CREATE TABLE IF NOT EXISTS methodology_docs (
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
    cols = {row[1] for row in db.execute("PRAGMA table_info(methodology_docs)").fetchall()}
    if "doc_type" not in cols:
        db.execute("ALTER TABLE methodology_docs ADD COLUMN doc_type TEXT DEFAULT 'GENERIC'")
    if "version" not in cols:
        db.execute("ALTER TABLE methodology_docs ADD COLUMN version TEXT DEFAULT 'v1.0'")
    if "bos_id" not in cols:
        db.execute("ALTER TABLE methodology_docs ADD COLUMN bos_id TEXT")

    fw = SANA_ACQUISITION_SYSTEM
    content_json = json.dumps(
        {k: v for k, v in fw.items() if k not in ("bos_id", "slug", "title", "subtitle", "version")},
        ensure_ascii=False,
    )

    existing = db.execute("SELECT doc_id FROM methodology_docs WHERE slug=?", (fw["slug"],)).fetchone()
    if existing:
        db.execute(
            """UPDATE methodology_docs
               SET title=?, subtitle=?, content=?, doc_type='BOS', version=?, bos_id=?
               WHERE slug=?""",
            (fw["title"], fw["subtitle"], content_json, fw["version"], fw["bos_id"], fw["slug"]),
        )
        print(f"تم تحديث {fw['slug']} إلى {fw['bos_id']} {fw['version']}.")
    else:
        db.execute(
            """INSERT INTO methodology_docs
               (doc_id, slug, title, subtitle, content, doc_type, version, bos_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("DOC" + uuid.uuid4().hex[:6].upper(), fw["slug"], fw["title"], fw["subtitle"],
             content_json, "BOS", fw["version"], fw["bos_id"]),
        )
        print(f"تم إدراج {fw['slug']} كـ {fw['bos_id']} {fw['version']}.")

    db.commit()
    db.close()


if __name__ == "__main__":
    main()
