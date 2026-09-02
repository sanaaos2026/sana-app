"""إدخال الوثيقتين المنقحتين المحددتين في المهمة إلى صندوق المراجعة."""
from pathlib import Path

from app import _connect_pg
from sana_knowledge import (
    SYSTEM_KNOWLEDGE_OWNER_ID,
    annotate_curated_source,
    create_uploaded_research_source,
    record_candidate_reconciliation,
)


ROOT = Path(__file__).resolve().parent.parent
ATTACHMENTS = ROOT / "attached_assets"

DOCUMENTS = (
    {
        "prefix": "نظام_تطوير_المشاريع_الخدمية_B2B_مستخرج_من_حالة_أراك",
        "title": "نظام تطوير المشاريع الخدمية وB2B — مستخرج من حالة أراك",
        "summary": "منطق تشغيل عام مرشح للتسوية مع المرجعين canonical القائمين، دون نشر أمثلة أو أرقام حالة أراك.",
        "tags": "internal,b2b,operating-logic,growth,reconciliation,candidate",
        "notes": "مرشح للمقارنة فقط؛ لا ينشئ FRAMEWORK موازيًا ولا يُعتمد تلقائيًا.",
        "source_profile": "b2b_operating_logic",
        "knowledge_scope": "shared_candidate",
        "sensitivity": "internal",
        "storage_destination": "سجل المعرفة + صندوق تسوية خاص",
        "framework_ref": "FRAMEWORK-GENERAL-SERVICE-B2B-RULES;FRAMEWORK-B2B-SERVICE-OS",
        "entity_scope": None,
    },
    {
        "prefix": "قاعدة_معرفة_موحدة_مجمع_أراك_طابا_الطبي",
        "title": "قاعدة معرفة موحدة — مجمع أراك طابا الطبي",
        "summary": "مادة حالة خاصة مرتبطة بكيان أراك؛ تفصل الحقائق المؤكدة والتاريخيات والأدلة والمقترحات والاستنتاجات والتعارضات.",
        "tags": "internal,private-case,health,arak,needs-verification",
        "notes": "مادة إدارية خاصة فقط؛ لا تُنشر في المكتبة المشتركة ولا تُستخدم لتشخيص شركة أخرى.",
        "source_profile": "arak_private_case",
        "knowledge_scope": "private_case",
        "sensitivity": "confidential",
        "storage_destination": "صندوق مادة حالة أراك الخاص",
        "framework_ref": None,
        "entity_scope": "مجمع أراك طابا الطبي — المدينة المنورة (كيان مذكور في الوثيقة، دون ربط بشركة سنع)",
    },
)


def _find(prefix):
    matches = sorted(ATTACHMENTS.glob(prefix + "*.docx"))
    if len(matches) != 1:
        raise RuntimeError(f"EXPECTED_ONE_ATTACHMENT:{prefix}:{len(matches)}")
    return matches[0]


def run():
    db = _connect_pg()
    try:
        owner = db.execute(
            "SELECT account_id FROM user_accounts WHERE is_admin=1 ORDER BY account_id LIMIT 1"
        ).fetchone()
        # لا يوجد في قاعدة البيانات الحالية حساب شركة مثبت لأراك. عند غياب
        # حساب إداري حقيقي نستخدم نطاقًا إداريًا محجوزًا لا يمكن لأي عميل تسجيل
        # الدخول به، بدل نسب المادة إلى شركة أو مستخدم عشوائي.
        owner_account_id = owner["account_id"] if owner else SYSTEM_KNOWLEDGE_OWNER_ID
        results = []
        for document in DOCUMENTS:
            path = _find(document["prefix"])
            result = create_uploaded_research_source(
                db,
                filename=path.name,
                mime_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                content=path.read_bytes(),
                payload={
                    "title": document["title"],
                    "source_kind": "file",
                    "language": "ar",
                    "summary": document["summary"],
                    "notes": document["notes"],
                    "tags": document["tags"],
                    "rights_status": "pending",
                    "source_profile": document["source_profile"],
                    "knowledge_scope": document["knowledge_scope"],
                    "sensitivity": document["sensitivity"],
                    "storage_destination": document["storage_destination"],
                    "framework_ref": document["framework_ref"],
                    "ingestion_event": "task-19-curated-knowledge-intake",
                    "entity_scope": document["entity_scope"],
                },
                owner_account_id=owner_account_id,
            )
            if result.get("error") == "DUPLICATE_FILE":
                if not result.get("research_source_id"):
                    existing_scope = db.execute(
                        """SELECT rs.research_source_id, rs.owner_account_id,
                                  COALESCE(ua.is_admin, 0) AS owner_is_admin
                           FROM research_source_files rf
                           JOIN research_sources rs ON rs.research_source_id=rf.research_source_id
                           LEFT JOIN user_accounts ua ON ua.account_id=rs.owner_account_id
                           WHERE rf.original_name=?""",
                        (path.name,),
                    ).fetchone()
                    if (
                        existing_scope
                        and (
                            existing_scope["owner_account_id"] == SYSTEM_KNOWLEDGE_OWNER_ID
                            or existing_scope["owner_is_admin"]
                        )
                    ):
                        result["research_source_id"] = existing_scope["research_source_id"]
                    else:
                        raise RuntimeError("DUPLICATE_FILE_OUTSIDE_ADMIN_SCOPE")
                source_id = result["research_source_id"]
                # النسخ الأولى من هذا المسار كانت تضع يوم الإدخال في حقل
                # تاريخ الوثيقة. صححه عند إعادة التشغيل دون المساس بحالة
                # المراجعة أو ببصمة الملف.
                db.execute(
                    """UPDATE research_sources
                       SET document_date=NULL, publication_year=NULL
                       WHERE research_source_id=?""",
                    (source_id,)
                )
                existing = db.execute(
                    "SELECT extracted_text, content_hash FROM research_source_files WHERE research_source_id=?",
                    (source_id,),
                ).fetchone()
                if existing and existing["extracted_text"]:
                    annotate_curated_source(
                        db, source_id, document["source_profile"], document["entity_scope"]
                    )
                    if document["source_profile"] == "b2b_operating_logic":
                        record_candidate_reconciliation(
                            db, source_id, existing["extracted_text"], existing["content_hash"]
                        )
                    db.commit()
                result = {
                    "success": True,
                    "duplicate": True,
                    "research_source_id": source_id,
                }
            results.append((path.name, result))
        return results
    finally:
        db.close()


if __name__ == "__main__":
    for name, result in run():
        print(name, result)