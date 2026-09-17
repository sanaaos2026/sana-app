"""Internal editorial governance for Sana public content.

This module keeps the public knowledge layer separate from proprietary Sana IP.
It is intentionally internal: no public route exposes the backlog, screening
markers, or Sana's private diagnostic mechanics.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta

CONTENT_GOVERNANCE_VERSION = "v1.0"
CONTENT_POLICY_SOURCE_ID = "SRC-SANA-CONTENT-GOVERNANCE"
CONTENT_POLICY_OBJECT_ID = "CONTENT-POLICY-IP-FIREWALL"
CONTENT_BACKLOG_OBJECT_ID = "CONTENT-BACKLOG-SERVICE-100"

PUBLIC_KNOWLEDGE_RULE = (
    "المحتوى يكشف للعميل مشكلته، ولا يكشف له محرك سنع الذي اكتشفها"
)

PUBLIC_LAYER_ALLOWED = (
    "المشكلة التي يعيشها صاحب الشركة",
    "الأعراض والآثار المالية والتشغيلية",
    "الأخطاء الشائعة",
    "أسئلة عامة تساعد المدير على التفكير",
    "مؤشرات عامة معروفة مثل CAC وLTV وROAS وConversion Rate",
    "أمثلة عامة لا تكشف طريقة سنع الداخلية",
    "النتيجة التي ينبغي أن تسعى الشركة للوصول إليها",
)

PRIVATE_SANA_IP = (
    "ترتيب الفحص والتشخيص الداخلي",
    "الأوزان وScoring والمعادلات الخاصة",
    "الخوارزميات وبوابات القرار وقواعد الانتقال",
    "Decision Trees والمحركات وتسلسلها",
    "طريقة استخراج الأولويات وربط الأدلة بالقرار",
    "Prompts وSOPs والنماذج التشغيلية الداخلية",
    "كيفية تحويل بيانات الحالة إلى قرار أو معرفة داخلية",
)

EDITORIAL_REVIEW_QUESTION = (
    "لو جمع منافس أفضل 100 مقال نشرناها، هل يستطيع إعادة بناء طريقة عمل "
    "Sana Scan أو أحد محركات سنع؟ إذا كانت الإجابة نعم أو ربما، يعاد تحرير المحتوى."
)

# High-risk markers only. The goal is to catch accidental IP leakage, not to
# ban normal business vocabulary. Sana Scan itself is a public product name.
PRIVATE_IP_MARKERS = (
    "Evidence Gate",
    "Decision Gate",
    "Knowledge OS",
    "Growth OS",
    "Revenue OS",
    "SCOS-001",
    "SEAF-001",
    "SDS-001",
    "SDS-002",
    "BOS-001",
    "بوابة القرار",
    "بوابة الأدلة",
    "أوزان التشخيص",
    "وزن التشخيص",
    "خوارزمية سنع",
    "خوارزمية التشخيص",
    "محرك التشخيص",
    "تسلسل المحركات",
    "قواعد الانتقال",
    "شجرة القرار الداخلية",
    "المعادلة الداخلية",
    "برومبت سنع",
    "Prompt سنع",
)

_INTERNAL_CODE_PATTERN = re.compile(r"\b(?:S[A-Z]{1,6}|BOS|SCOS|SEAF|SDS)-\d{3}\b", re.I)


def _topic(number, category, title, angle=""):
    return {
        "number": number,
        "category": category,
        "title": title,
        "angle": angle,
        "visibility": "internal_backlog",
        "publication_state": "planned",
    }


SAFE_ARTICLE_BACKLOG = [
    _topic(1, "مشاكل نمو الشركات", "لماذا تتوقف بعض الشركات عن النمو رغم زيادة الجهد؟", "أعراض توقف النمو وأين يبدأ صاحب الشركة بالنظر"),
    _topic(2, "مشاكل نمو الشركات", "شركتك تعمل كثيرًا لكن النتائج لا تتحسن: ما الذي يحدث؟", "الفرق بين كثرة النشاط وتحسن النتائج"),
    _topic(3, "مشاكل نمو الشركات", "7 علامات تقول إن شركتك تحتاج إلى مراجعة شاملة", "إشارات واضحة يفهمها المدير"),
    _topic(4, "مشاكل نمو الشركات", "لماذا لا تكون المشكلة دائمًا حيث تبدو؟", "أمثلة عامة تبين أن المشكلة الظاهرة قد يكون خلفها سبب آخر"),
    _topic(5, "مشاكل نمو الشركات", "كيف تعرف أن شركتك تعالج الأعراض بدل المشكلة؟", "أمثلة وأسئلة للتفكير دون كشف منهج التشخيص"),
    _topic(6, "مشاكل نمو الشركات", "متى يكون بطء النمو طبيعيًا ومتى يصبح إنذارًا؟"),
    _topic(7, "مشاكل نمو الشركات", "لماذا تنجح بعض الشركات في النمو ثم تتوقف فجأة؟"),
    _topic(8, "مشاكل نمو الشركات", "كثرة المشكلات أم مشكلة واحدة تؤثر في البقية؟"),
    _topic(9, "مشاكل نمو الشركات", "ما الذي يمنع الشركة الجيدة من أن تصبح شركة قوية؟"),
    _topic(10, "مشاكل نمو الشركات", "هل شركتك بحاجة إلى مزيد من العمل أم إلى وضوح أكبر؟"),

    _topic(11, "القرار والإدارة", "لماذا تتخذ الشركات قرارات كثيرة ولا ترى نتائج؟"),
    _topic(12, "القرار والإدارة", "كيف يعرف المدير التنفيذي ما الذي يستحق اهتمامه الآن؟"),
    _topic(13, "القرار والإدارة", "متى تصبح كثرة الأولويات عدوًا للنمو؟"),
    _topic(14, "القرار والإدارة", "لماذا تحتاج الإدارة إلى أرقام أقل ولكن أهم؟"),
    _topic(15, "القرار والإدارة", "كيف تفرق بين القرار المبني على البيانات والقرار المبني على الانطباع؟"),
    _topic(16, "القرار والإدارة", "لماذا لا تكفي التقارير لاتخاذ قرارات أفضل؟"),
    _topic(17, "القرار والإدارة", "ما الفرق بين المعلومة المفيدة والمعلومة التي تغير القرار؟"),
    _topic(18, "القرار والإدارة", "كيف تعرف أن شركتك تقيس الأشياء الخطأ؟"),
    _topic(19, "القرار والإدارة", "متى يجب على الإدارة التوقف عن مبادرة لا تحقق أثرًا؟"),
    _topic(20, "القرار والإدارة", "كيف تبني ثقافة قرارات أكثر وضوحًا داخل الشركة؟"),

    _topic(21, "المبيعات والإيرادات", "لماذا لا ترتفع الإيرادات رغم وجود فرص مبيعات كثيرة؟"),
    _topic(22, "المبيعات والإيرادات", "زيادة المبيعات أم زيادة الربحية: أيهما أهم؟"),
    _topic(23, "المبيعات والإيرادات", "لماذا المزيد من العملاء المحتملين ليس دائمًا الحل؟"),
    _topic(24, "المبيعات والإيرادات", "كيف تعرف أن شركتك تخسر فرصًا بيعية جيدة؟"),
    _topic(25, "المبيعات والإيرادات", "من العميل المحتمل إلى الإيراد: أين تضيع الفرص؟"),
    _topic(26, "المبيعات والإيرادات", "لماذا بعض الشركات تبيع أكثر لكنها تكسب أقل؟"),
    _topic(27, "المبيعات والإيرادات", "متى يصبح الخصم عدوًا للربحية؟"),
    _topic(28, "المبيعات والإيرادات", "كيف يؤثر متوسط قيمة الصفقة على نمو الشركة؟"),
    _topic(29, "المبيعات والإيرادات", "لماذا يجب أن يهتم صاحب الشركة بالتحصيل بقدر اهتمامه بالمبيعات؟"),
    _topic(30, "المبيعات والإيرادات", "ما الفرق بين الإيراد على الورق والنقد الحقيقي؟"),

    _topic(31, "التسويق", "لماذا لا تحل زيادة ميزانية الإعلانات مشكلة المبيعات دائمًا؟"),
    _topic(32, "التسويق", "كيف تعرف أن المشكلة في التسويق أم في مكان آخر؟"),
    _topic(33, "التسويق", "ROAS مرتفع ولكن الأرباح ضعيفة: كيف يحدث ذلك؟"),
    _topic(34, "التسويق", "لماذا تجلب بعض الحملات عملاء كثيرين دون نمو حقيقي؟"),
    _topic(35, "التسويق", "CAC: متى تصبح تكلفة اكتساب العميل خطرًا؟"),
    _topic(36, "التسويق", "هل تحتاج شركتك إلى قناة تسويقية جديدة فعلًا؟"),
    _topic(37, "التسويق", "لماذا الانتشار ليس دليلًا على نجاح التسويق؟"),
    _topic(38, "التسويق", "كيف تعرف أن رسالتك التسويقية غير واضحة؟"),
    _topic(39, "التسويق", "المحتوى أم الإعلانات: أين يجب أن تستثمر شركتك؟"),
    _topic(40, "التسويق", "لماذا تفشل حملات جيدة بسبب مشكلة داخل الشركة؟"),

    _topic(41, "العملاء ورحلة العميل", "رحلة العميل: أين تخسر الشركات عملاءها دون أن تلاحظ؟"),
    _topic(42, "العملاء ورحلة العميل", "لماذا يهتم العميل ثم يختفي؟"),
    _topic(43, "العملاء ورحلة العميل", "كم تكلف شركتك الاستجابة البطيئة للعملاء؟"),
    _topic(44, "العملاء ورحلة العميل", "لماذا لا يعود بعض العملاء بعد أول شراء أو عقد؟"),
    _topic(45, "العملاء ورحلة العميل", "كيف تعرف أن تجربة العميل تؤثر في الإيرادات؟"),
    _topic(46, "العملاء ورحلة العميل", "LTV: لماذا قيمة العميل أكبر من الصفقة الأولى؟"),
    _topic(47, "العملاء ورحلة العميل", "لماذا العميل القديم قد يكون فرصة نمو أفضل من العميل الجديد؟"),
    _topic(48, "العملاء ورحلة العميل", "كيف تعيد تنشيط العملاء السابقين دون إغراقهم بالخصومات؟"),
    _topic(49, "العملاء ورحلة العميل", "متى يكون Upsell مفيدًا للعميل وليس مجرد محاولة بيع؟"),
    _topic(50, "العملاء ورحلة العميل", "كيف تتحول تجربة العميل الجيدة إلى إحالات ومبيعات جديدة؟"),

    _topic(51, "المبيعات B2B والصفقات", "لماذا تستغرق بعض صفقات B2B أشهرًا دون قرار؟"),
    _topic(52, "المبيعات B2B والصفقات", "كيف تعرف أن فرصة المبيعات جادة؟"),
    _topic(53, "المبيعات B2B والصفقات", "لماذا تضيع فرق المبيعات وقتها مع العملاء غير المناسبين؟"),
    _topic(54, "المبيعات B2B والصفقات", "ما الذي يجعل صاحب القرار يقول نعم؟"),
    _topic(55, "المبيعات B2B والصفقات", "لماذا لا يكفي إرسال عرض سعر لإغلاق الصفقة؟"),
    _topic(56, "المبيعات B2B والصفقات", "أرسلت العرض ولم يرد العميل: ما الأسباب المحتملة؟"),
    _topic(57, "المبيعات B2B والصفقات", "لماذا يقول العميل نحتاج نفكر؟"),
    _topic(58, "المبيعات B2B والصفقات", "كيف تتعامل مع اعتراض السعر دون أن تبدأ بالخصم؟"),
    _topic(59, "المبيعات B2B والصفقات", "منافسك أرخص: هل هذه مشكلة فعلًا؟"),
    _topic(60, "المبيعات B2B والصفقات", "لماذا تفشل صفقات جيدة في المراحل الأخيرة؟"),

    _topic(61, "التشغيل", "كيف تعرف أن التشغيل أصبح عائقًا أمام نمو الشركة؟"),
    _topic(62, "التشغيل", "مبيعات أكثر، مشاكل أكثر: لماذا يحدث ذلك؟"),
    _topic(63, "التشغيل", "متى تكون الشركة غير جاهزة للتوسع؟"),
    _topic(64, "التشغيل", "لماذا لا يجب أن تسوق أكثر مما يستطيع فريقك تقديمه؟"),
    _topic(65, "التشغيل", "كيف تؤثر الفوضى التشغيلية في رضا العملاء والإيرادات؟"),
    _topic(66, "التشغيل", "لماذا تتكرر الأخطاء نفسها داخل بعض الشركات؟"),
    _topic(67, "التشغيل", "متى تحتاج العملية إلى SOP؟"),
    _topic(68, "التشغيل", "كيف تعرف أن فريقك يعتمد على الأشخاص أكثر من النظام؟"),
    _topic(69, "التشغيل", "لماذا تصبح جودة الخدمة غير مستقرة مع نمو الشركة؟"),
    _topic(70, "التشغيل", "كيف تحافظ على جودة التنفيذ أثناء التوسع؟"),

    _topic(71, "صاحب الشركة والمؤسس", "هل أنت أكبر نقطة قوة في شركتك أم أكبر اختناق؟"),
    _topic(72, "صاحب الشركة والمؤسس", "متى يصبح اعتماد الشركة على المؤسس خطرًا؟"),
    _topic(73, "صاحب الشركة والمؤسس", "لماذا يتخذ المؤسس عشرات القرارات التي يمكن للفريق اتخاذها؟"),
    _topic(74, "صاحب الشركة والمؤسس", "كيف تعرف أن شركتك لا تستطيع النمو بدونك؟"),
    _topic(75, "صاحب الشركة والمؤسس", "من الخبرة الشخصية إلى المعرفة المؤسسية: لماذا يهم ذلك؟"),
    _topic(76, "صاحب الشركة والمؤسس", "لماذا لا يكفي توظيف أشخاص أكثر لحل اعتماد الشركة على المؤسس؟"),
    _topic(77, "صاحب الشركة والمؤسس", "كيف تحافظ الشركة على خبرتها عندما يغادر موظف مهم؟"),
    _topic(78, "صاحب الشركة والمؤسس", "متى يكون التفويض ناجحًا ومتى يكون مجرد نقل للمشكلة؟"),
    _topic(79, "صاحب الشركة والمؤسس", "كيف تقيس مدى استقلال شركتك عنك؟"),
    _topic(80, "صاحب الشركة والمؤسس", "كيف ينتقل صاحب الشركة من تشغيل كل شيء إلى قيادة النمو؟"),

    _topic(81, "الذكاء الاصطناعي والبيانات", "الذكاء الاصطناعي للشركات: أين يحقق قيمة فعلية؟"),
    _topic(82, "الذكاء الاصطناعي والبيانات", "متى يكون AI حلًا ومتى يكون مجرد أداة جديدة؟"),
    _topic(83, "الذكاء الاصطناعي والبيانات", "لماذا لا يجب أن يتخذ الذكاء الاصطناعي قراراتك التجارية وحده؟"),
    _topic(84, "الذكاء الاصطناعي والبيانات", "كيف تستخدم AI دون أن تحول الأخطاء إلى قرارات أسرع؟"),
    _topic(85, "الذكاء الاصطناعي والبيانات", "بيانات كثيرة وقرارات ضعيفة: أين المشكلة؟"),
    _topic(86, "الذكاء الاصطناعي والبيانات", "كيف تعرف أن بيانات شركتك موثوقة؟"),
    _topic(87, "الذكاء الاصطناعي والبيانات", "لماذا مصدر الرقم مهم بقدر الرقم نفسه؟"),
    _topic(88, "الذكاء الاصطناعي والبيانات", "متى تكون الشركة جاهزة للأتمتة؟"),
    _topic(89, "الذكاء الاصطناعي والبيانات", "لماذا أتمتة عملية سيئة قد تجعل المشكلة أكبر؟"),
    _topic(90, "الذكاء الاصطناعي والبيانات", "كيف تساعد التقنية الإدارة دون أن تزيد التعقيد؟"),

    _topic(91, "النمو المستدام وسلطة سنع الفكرية", "لماذا النمو ليس كثرة المبادرات؟"),
    _topic(92, "النمو المستدام وسلطة سنع الفكرية", "كيف تعرف أن شركتك جاهزة للمرحلة التالية من النمو؟"),
    _topic(93, "النمو المستدام وسلطة سنع الفكرية", "متى تتوسع ومتى تتوقف وتراجع؟"),
    _topic(94, "النمو المستدام وسلطة سنع الفكرية", "لماذا يجب إثبات النجاح قبل تكراره؟"),
    _topic(95, "النمو المستدام وسلطة سنع الفكرية", "كيف تعرف أن النمو الذي تحققه قابل للاستمرار؟"),
    _topic(96, "النمو المستدام وسلطة سنع الفكرية", "ما الذي يجعل شركة خدمية قابلة للتوسع؟"),
    _topic(97, "النمو المستدام وسلطة سنع الفكرية", "لماذا لا تعني زيادة الإيرادات أن الشركة أصبحت أقوى؟"),
    _topic(98, "النمو المستدام وسلطة سنع الفكرية", "من الفوضى إلى الوضوح: ماذا يتغير عندما تعرف الشركة أولوياتها؟"),
    _topic(99, "النمو المستدام وسلطة سنع الفكرية", "10 أسئلة يجب أن يسألها صاحب الشركة قبل خطة العام القادم"),
    _topic(100, "النمو المستدام وسلطة سنع الفكرية", "لماذا يبدأ النمو الحقيقي بالوضوح؟", "مقال Brand Manifesto يربط المعرفة بوعد سنع: وضوح يصنع النمو"),
]


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _public_text(article):
    parts = [article.get("title", ""), article.get("subtitle", ""), article.get("keyword", "")]
    content = article.get("content") or {}
    for value in content.values():
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        else:
            parts.append(str(value or ""))
    return "\n".join(parts)


def validate_public_article(article):
    """Fail closed when a public article contains an obvious private-IP marker."""
    text = _public_text(article)
    violations = [marker for marker in PRIVATE_IP_MARKERS if marker.lower() in text.lower()]
    codes = sorted(set(_INTERNAL_CODE_PATTERN.findall(text)))
    if codes:
        violations.extend(codes)
    if violations:
        raise ValueError(
            f"Public article '{article.get('slug')}' violates Sana IP Firewall: "
            + ", ".join(sorted(set(violations)))
        )
    return True


def validate_public_catalog(articles):
    for article in articles:
        validate_public_article(article)
    return True


def _policy_payload():
    return {
        "governing_rule": PUBLIC_KNOWLEDGE_RULE,
        "public_layer_allowed": list(PUBLIC_LAYER_ALLOWED),
        "private_sana_ip": list(PRIVATE_SANA_IP),
        "review_question": EDITORIAL_REVIEW_QUESTION,
        "publishing_flow": [
            "Backlog",
            "Draft",
            "IP Review",
            "SEO Review",
            "Approved Public",
            "Published",
            "Knowledge Mirror",
        ],
        "knowledge_rule": (
            "المحتوى المنشور يعود إلى Knowledge OS كمرجع CONTENT_REFERENCE للتفسير وصناعة المحتوى فقط، "
            "ولا يصبح Evidence أو Benchmark أو قاعدة تشخيص لحالة عميل."
        ),
        "seo_rule": (
            "الأولوية لنية البحث ولغة صاحب الشركة، مع منع Keyword Cannibalization عبر مراجعة المقالات المنشورة قبل اعتماد موضوع جديد."
        ),
    }


def seed_content_governance(db):
    """Store the editorial firewall and the 100-topic roadmap as internal-only knowledge."""
    today = date.today().isoformat()
    review_due = (date.today() + timedelta(days=365)).isoformat()
    policy = _policy_payload()
    backlog_payload = {
        "purpose": "خريطة تحريرية آمنة للشركات الخدمية. ليست 100 صفحة منشورة تلقائيًا.",
        "publication_rule": "كل موضوع يمر بفلتر IP ثم SEO ويقارن بالمحتوى المنشور قبل الكتابة لتجنب التكرار وتضارب الكلمات.",
        "topics": SAFE_ARTICLE_BACKLOG,
    }
    canonical = _json({"policy": policy, "backlog": backlog_payload})
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    db.execute(
        """INSERT INTO knowledge_sources
           (source_id,title,source_type,publisher,jurisdiction,rights_status,license_note,
            retrieved_at,review_due_at,status,content_fingerprint,document_date,
            version_label,reviewed_at,trust_level,material_type,author_identity,methodology_note)
           VALUES (?,?, 'internal','Sana','SA','owned',?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT (source_id) DO UPDATE SET
             title=EXCLUDED.title,
             rights_status='owned',
             license_note=EXCLUDED.license_note,
             review_due_at=EXCLUDED.review_due_at,
             status='approved',
             content_fingerprint=EXCLUDED.content_fingerprint,
             document_date=EXCLUDED.document_date,
             version_label=EXCLUDED.version_label,
             reviewed_at=EXCLUDED.reviewed_at,
             trust_level=EXCLUDED.trust_level,
             material_type=EXCLUDED.material_type,
             author_identity=EXCLUDED.author_identity,
             methodology_note=EXCLUDED.methodology_note,
             updated_at=to_char(now() AT TIME ZONE 'utc','YYYY-MM-DD HH24:MI:SS')""",
        (
            CONTENT_POLICY_SOURCE_ID,
            "سياسة سنع للمحتوى العام وحماية الملكية الفكرية",
            "سياسة داخلية مملوكة لسنع ولا تنشر للعامة.",
            today,
            review_due,
            "approved",
            fingerprint,
            today,
            CONTENT_GOVERNANCE_VERSION,
            today,
            "high",
            "internal_editorial_governance",
            "Sana",
            "تفصل المعرفة العامة القابلة للنشر عن آليات سنع التشخيصية والتشغيلية الخاصة.",
        ),
    )
    db.execute(
        """INSERT INTO knowledge_versions
           (version_id,source_id,version_label,content_hash,published_at,reviewed_at,reviewer,status)
           VALUES (?,?,?,?,?,?,?,'approved')
           ON CONFLICT (source_id,version_label) DO UPDATE SET
             content_hash=EXCLUDED.content_hash,
             reviewed_at=EXCLUDED.reviewed_at,
             reviewer=EXCLUDED.reviewer,
             status='approved'""",
        (
            f"{CONTENT_POLICY_SOURCE_ID}:{CONTENT_GOVERNANCE_VERSION}",
            CONTENT_POLICY_SOURCE_ID,
            CONTENT_GOVERNANCE_VERSION,
            fingerprint,
            today,
            today,
            "Sana editorial",
        ),
    )

    common = {
        "source": "سنع — حوكمة المحتوى الداخلية",
        "source_id": CONTENT_POLICY_SOURCE_ID,
        "evidence_quality": "سياسة تشغيل داخلية؛ ليست Evidence عن عميل.",
        "confidence_level": "High",
        "knowledge_level": "L1",
        "version": CONTENT_GOVERNANCE_VERSION,
        "last_reviewed": today,
        "status": "approved",
    }

    db.execute(
        """INSERT INTO knowledge_objects
           (object_id,library_type,category,sector,title,problem,recommendation,sop,
            source,source_id,evidence_quality,confidence_level,applicable_when,
            do_not_apply_when,knowledge_level,version,last_reviewed,status,
            source_excerpt,original_summary,domains,sector_tags,business_model_tags,
            problem_tags,goal_tags,bottleneck_tags)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT (object_id) DO UPDATE SET
             title=EXCLUDED.title,
             problem=EXCLUDED.problem,
             recommendation=EXCLUDED.recommendation,
             sop=EXCLUDED.sop,
             evidence_quality=EXCLUDED.evidence_quality,
             applicable_when=EXCLUDED.applicable_when,
             do_not_apply_when=EXCLUDED.do_not_apply_when,
             version=EXCLUDED.version,
             last_reviewed=EXCLUDED.last_reviewed,
             status='approved',
             source_excerpt=EXCLUDED.source_excerpt,
             original_summary=EXCLUDED.original_summary,
             domains=EXCLUDED.domains,
             sector_tags=EXCLUDED.sector_tags,
             business_model_tags=EXCLUDED.business_model_tags,
             problem_tags=EXCLUDED.problem_tags,
             goal_tags=EXCLUDED.goal_tags,
             bottleneck_tags=EXCLUDED.bottleneck_tags,
             updated_at=to_char(now() AT TIME ZONE 'utc','YYYY-MM-DD HH24:MI:SS')""",
        (
            CONTENT_POLICY_OBJECT_ID,
            "CONTENT_POLICY",
            "CONTENT_GOVERNANCE",
            "internal_content",
            "SANA IP Firewall — سياسة النشر وحماية الملكية الفكرية",
            "منع تسرب آلية سنع الداخلية أثناء تقديم محتوى عام عالي القيمة.",
            _json(policy),
            _json(policy["publishing_flow"]),
            common["source"], common["source_id"], common["evidence_quality"], common["confidence_level"],
            "عند كتابة أو مراجعة مقال أو فيديو أو صفحة عامة أو مقابلة أو مادة تسويقية لسنع.",
            "لا تستخدم هذه السياسة كقاعدة تشخيص أو كدليل على شركة عميل.",
            common["knowledge_level"], common["version"], common["last_reviewed"], common["status"],
            PUBLIC_KNOWLEDGE_RULE,
            "نفصح عن المشكلة والقيمة والنتيجة، ونحجب طريقة سنع الداخلية للوصول إلى التشخيص والقرار.",
            _json(["content", "brand", "seo", "knowledge-governance"]),
            _json(["internal-content"]),
            _json(["service-business", "b2b-services"]),
            _json(["ip-leakage", "content-governance"]),
            _json(["public-authority", "safe-publishing"]),
            _json(["editorial-review"]),
        ),
    )

    db.execute(
        """INSERT INTO knowledge_objects
           (object_id,library_type,category,sector,title,problem,recommendation,
            source,source_id,evidence_quality,confidence_level,applicable_when,
            do_not_apply_when,knowledge_level,version,last_reviewed,status,
            source_excerpt,original_summary,domains,sector_tags,business_model_tags,
            problem_tags,goal_tags,bottleneck_tags)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT (object_id) DO UPDATE SET
             title=EXCLUDED.title,
             problem=EXCLUDED.problem,
             recommendation=EXCLUDED.recommendation,
             evidence_quality=EXCLUDED.evidence_quality,
             applicable_when=EXCLUDED.applicable_when,
             do_not_apply_when=EXCLUDED.do_not_apply_when,
             version=EXCLUDED.version,
             last_reviewed=EXCLUDED.last_reviewed,
             status='approved',
             source_excerpt=EXCLUDED.source_excerpt,
             original_summary=EXCLUDED.original_summary,
             domains=EXCLUDED.domains,
             sector_tags=EXCLUDED.sector_tags,
             business_model_tags=EXCLUDED.business_model_tags,
             problem_tags=EXCLUDED.problem_tags,
             goal_tags=EXCLUDED.goal_tags,
             bottleneck_tags=EXCLUDED.bottleneck_tags,
             updated_at=to_char(now() AT TIME ZONE 'utc','YYYY-MM-DD HH24:MI:SS')""",
        (
            CONTENT_BACKLOG_OBJECT_ID,
            "CONTENT_BACKLOG",
            "CONTENT_STRATEGY",
            "internal_content",
            "خريطة 100 مقال آمن للشركات الخدمية",
            "الحاجة إلى بناء سلطة معرفية قابلة للبحث دون كشف آليات Sana IP.",
            _json(backlog_payload),
            common["source"], common["source_id"], common["evidence_quality"], common["confidence_level"],
            "عند اختيار موضوع جديد للمقالات أو الفيديو أو المحتوى التعليمي العام.",
            "لا تنشر الخريطة نفسها ولا تحول كل عنوان تلقائيًا إلى صفحة قبل مراجعة التداخل والكلمة المفتاحية.",
            common["knowledge_level"], common["version"], common["last_reviewed"], common["status"],
            "100 موضوع موزعة على النمو والقرار والمبيعات والتسويق والعملاء والصفقات والتشغيل والمؤسس والAI والنمو المستدام.",
            "خريطة موضوعات عامة آمنة تقود إلى القيمة والنتيجة، مع منع كشف طريقة سنع الداخلية.",
            _json(["content", "seo", "service-business"]),
            _json(["service-business", "b2b-services"]),
            _json(["services", "expert-led"]),
            _json(["growth", "sales", "operations", "customer", "ai"]),
            _json(["organic-demand", "thought-leadership", "ai-discoverability"]),
            _json(["content-backlog"]),
        ),
    )
    db.commit()
    return {"policy": 1, "backlog_topics": len(SAFE_ARTICLE_BACKLOG)}
