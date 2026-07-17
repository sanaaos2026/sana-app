"""
seed_article_founder_dependency.py
بذر أول مقال: "لماذا تعتمد شركتك على المؤسس؟"
doc_type='article' في جدول methodology_docs الموجود — لا جدول جديد.
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ARTICLE_SLUG = "founder-dependency"

CONTENT = {
    "problem": (
        "شركتك لا تعمل بدونك. كل قرار يمر عليك، كل عميل يريدك أنت، "
        "وكل ما حاولت تأخذ إجازة — حدث شيء. هذا ليس نجاحًا، هذا خطر."
    ),
    "why": (
        "الاعتماد على المؤسس ينشأ من ثلاثة أسباب متشابكة: "
        "<strong>أولاً:</strong> غياب التوثيق — العمليات في رأسك لا في أنظمة. "
        "<strong>ثانيًا:</strong> ضعف الثقة — الفريق أو العملاء لا يثقون بأحد غيرك لأنك لم تبنِ هذه الثقة تدريجيًا. "
        "<strong>ثالثًا:</strong> غياب الصلاحيات الواضحة — لا أحد يعرف حدود قراره بدقة فيرفع كل شيء لك."
    ),
    "diagnose": [
        "كم قراراً مرّ عليك الأسبوع الماضي كان يمكن لشخص آخر اتخاذه؟",
        "لو غبت أسبوعاً كاملاً — ماذا سيتوقف؟",
        "هل عملاؤك يطلبونك أنت بالاسم، أم يثقون بفريقك؟",
        "هل لديك SOP موثقة لأكثر من 80% من العمليات المتكررة؟",
        "هل الفريق يعرف ماذا يفعل عند غيابك بدون أن يسألك؟",
    ],
    "solution": (
        "الحل ليس \"تفويض\" — الحل هو بناء <strong>نظام استقلالية تدريجي</strong>: "
        "توثيق، ثم تدريب، ثم تفويض، ثم قياس. "
        "نظام سنع لاكتساب العملاء (<a href='/methodology/sana-acquisition-system'>BOS-001</a>) "
        "يحتوي على قسم كامل عن كيفية بناء هذا الاستقلال وقياس أثره على قيمة الشركة."
    ),
    "steps": [
        "حدّد الـ 10 قرارات الأكثر تكراراً التي تمر عليك — اكتبها.",
        "صنّفها: أي منها يمكن توثيقه كـ SOP خلال ساعة واحدة؟",
        "اختر شخصاً واحداً وعلّمه SOP واحدة — راقب، لا تتدخل.",
        "قِس: كم قراراً مررت عليه الأسبوع التالي؟ هل انخفض العدد؟",
        "كرّر حتى تصل لـ 80% من القرارات اليومية لا تحتاجك.",
        "وثّق الاستقلالية في جواز الشركة — ستؤثر مباشرة على قيمة الشركة السوقية.",
    ],
    "checklist": [
        "لديّ قائمة بكل القرارات التي تمر عليي أسبوعياً",
        "أكثر من 60% من العمليات المتكررة موثقة كـ SOP",
        "هناك شخص واحد على الأقل يمكنه تمثيلي أمام العملاء",
        "لو غبت أسبوعاً — الفريق يعرف ماذا يفعل",
        "درجة الاستقلالية في جواز الشركة أعلى من 50%",
    ],
    "related": [
        {"url": "/methodology/sana-acquisition-system", "label": "نظام سنع لاكتساب العملاء (BOS-001) — الإطار الكامل"},
        {"url": "/articles", "label": "← جميع المقالات"},
    ],
}

def seed():
    import importlib.util, pathlib
    # استدعاء get_db من app.py
    spec = importlib.util.spec_from_file_location("app", pathlib.Path(__file__).parent / "app.py")
    app_mod = importlib.util.load_from_spec = None

    # نستخدم psycopg2 مباشرة
    import psycopg2, psycopg2.extras

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set"); sys.exit(1)

    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    content_json = json.dumps(CONTENT, ensure_ascii=False)
    doc_id = "ART-001"

    cur.execute("SELECT doc_id FROM methodology_docs WHERE slug=%s", (ARTICLE_SLUG,))
    existing = cur.fetchone()

    if existing:
        cur.execute(
            """UPDATE methodology_docs
               SET title=%s, subtitle=%s, content=%s, doc_type='article', version='v1.0'
               WHERE slug=%s""",
            ("لماذا تعتمد شركتك على المؤسس؟",
             "التشخيص الحقيقي لظاهرة Founder Dependency وكيف تخرج منها خطوة بخطوة",
             content_json, ARTICLE_SLUG)
        )
        print(f"✔ تم تحديث المقال slug={ARTICLE_SLUG}")
    else:
        cur.execute(
            """INSERT INTO methodology_docs
               (doc_id, slug, title, subtitle, content, doc_type, version)
               VALUES (%s,%s,%s,%s,%s,'article','v1.0')""",
            (doc_id, ARTICLE_SLUG,
             "لماذا تعتمد شركتك على المؤسس؟",
             "التشخيص الحقيقي لظاهرة Founder Dependency وكيف تخرج منها خطوة بخطوة",
             content_json)
        )
        print(f"✔ تم إنشاء المقال slug={ARTICLE_SLUG} doc_id={doc_id}")

    conn.commit()
    cur.close()
    conn.close()
    print("✔ اكتمل البذر بنجاح")

if __name__ == "__main__":
    seed()
