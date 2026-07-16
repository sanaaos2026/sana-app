"""
sana_evidence.py — الدالة الموحّدة الوحيدة لحفظ الأدلة (Evidence) في سنع.

تُستدعى من:
  - add_evidence() route — كل الـ frontend يمر منه
  - add_ev() داخل جلسة SDS discovery
  - أي ميزة قادمة تحتاج حفظ دليل

لا تحتوي منطق HTTP — فقط التحقق والحفظ والإرجاع.
"""

import uuid

# _PGConn في app.py يحوّل ? → %s تلقائيًا،
# لكن كتابة %s مباشرة تعمل كذلك (لا يؤثر عليها الـ replace).
PLACEHOLDER = "%s"


def asset_exists(db, company_id: str, asset_id: str) -> bool:
    """تحقق أن الأصل موجود فعليًا لهذي الشركة."""
    row = db.execute(
        f"SELECT 1 FROM assets WHERE company_id={PLACEHOLDER} AND asset_id={PLACEHOLDER}",
        (company_id, asset_id),
    ).fetchone()
    return row is not None


def save_evidence(
    db,
    *,
    company_id: str,
    asset_id: str | None,
    title: str,
    source_type: str = "sana_discovery",
    confidence: int = 50,
    case_id: str | None = None,
    tag: str | None = None,
) -> dict:
    """
    الدالة الوحيدة المعتمدة لحفظ أي دليل بسنع — من أي ميزة.

    المعاملات:
        db          : كائن الاتصال (get_db())
        company_id  : معرّف الشركة
        asset_id    : معرّف الأصل — اختياري (None مقبول لأدلة بلا أصل محدد)
        title       : نص الدليل
        source_type : مصدر الإدخال (افتراضي: sana_discovery)
        confidence  : درجة الثقة 0–100 (افتراضي: 50)
        case_id     : معرّف القضية — اختياري (None مقبول)
        tag         : وسم اختياري يُضاف كبادئة للعنوان (مثال: "sop_protects")
                      إن لم يُعطَ: العنوان يُحفظ كما هو بلا تغيير.

    يُرجع:
        {"success": True,  "evidence_id": "EXXXXXXXX", "error": None}
        {"success": False, "evidence_id": None,        "error": "سبب الفشل"}
    """
    # 1) تحقق الأصل موجود — فقط إن أُعطي asset_id
    if asset_id and not asset_exists(db, company_id, asset_id):
        return {
            "success": False,
            "evidence_id": None,
            "error": (
                f"asset_id غير موجود: '{asset_id}' لشركة '{company_id}' — "
                f"تأكد المعرّف مطابق تمامًا لما هو مخزَّن في جدول assets."
            ),
        }

    # 2) تحقق القضية موجودة وتخص نفس الشركة — فقط إن أُعطي case_id
    if case_id:
        case_row = db.execute(
            f"SELECT 1 FROM cases WHERE case_id={PLACEHOLDER} AND company_id={PLACEHOLDER}",
            (case_id, company_id),
        ).fetchone()
        if not case_row:
            return {
                "success": False,
                "evidence_id": None,
                "error": f"case_id غير موجود أو لا يخص هذي الشركة: '{case_id}'",
            }

    # 3) تجهيز العنوان (بادئة tag اختيارية)
    final_title = f"[{tag}] {title}" if tag else title

    # 4) الحفظ الفعلي
    evidence_id = "E" + uuid.uuid4().hex[:8].upper()
    try:
        db.execute(
            f"""INSERT INTO evidence
                    (evidence_id, company_id, case_id, asset_id,
                     title, source_type, confidence)
                VALUES
                    ({PLACEHOLDER},{PLACEHOLDER},{PLACEHOLDER},{PLACEHOLDER},
                     {PLACEHOLDER},{PLACEHOLDER},{PLACEHOLDER})""",
            (evidence_id, company_id, case_id, asset_id,
             final_title, source_type, confidence),
        )

        # 5) تحديث درجة الأصل (+2، بحد أقصى 100) — رقم حقيقي من القاعدة
        new_score = None
        asset_name = None
        if asset_id:
            db.execute(
                f"""UPDATE assets
                    SET current_score = CASE
                        WHEN current_score + 2 > 100 THEN 100
                        ELSE current_score + 2
                    END
                    WHERE asset_id={PLACEHOLDER} AND company_id={PLACEHOLDER}""",
                (asset_id, company_id),
            )
            row = db.execute(
                f"SELECT current_score, asset_name FROM assets WHERE asset_id={PLACEHOLDER}",
                (asset_id,),
            ).fetchone()
            if row:
                new_score  = row[0]
                asset_name = row[1]

        db.commit()
        return {
            "success": True,
            "evidence_id": evidence_id,
            "error": None,
            "asset_id": asset_id,
            "asset_name": asset_name,
            "new_score": new_score,
        }
    except Exception as exc:
        # لا فشل صامت أبدًا — الخطأ الحقيقي يصل للمستدعي
        return {"success": False, "evidence_id": None, "error": str(exc)}
