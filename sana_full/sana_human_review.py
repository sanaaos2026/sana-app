"""V1 human review requests for a case decision.

The review record is append-only with respect to the original decision:
before_snapshot_json is never overwritten, while the reviewer outcome lives in
after_snapshot_json and status history is stored separately.
"""
import json
import uuid
from datetime import date


REVIEW_REASONS = {
    "confirm_decision": "أريد التأكد من القرار",
    "explain_result": "أحتاج تفسير النتيجة",
    "plan_execution": "أريد ترتيب التنفيذ",
    "additional_information": "لدي معلومات إضافية",
    "other_problem": "أريد مناقشة مشكلة أخرى",
}
REVIEW_STATUSES = {"REQUESTED", "SCHEDULED", "COMPLETED", "CANCELLED"}
OFFER_MODES = {"INCLUDED", "FREE", "PAID"}
EXPERT_SUMMARY_STATUSES = {"DRAFT", "FORMATTED", "APPROVED"}


def ensure_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS human_review_settings (
        singleton_key TEXT PRIMARY KEY DEFAULT 'default'
          CHECK (singleton_key='default'),
        offer_mode TEXT NOT NULL DEFAULT 'FREE'
          CHECK (offer_mode IN ('INCLUDED','FREE','PAID')),
        offer_name TEXT NOT NULL DEFAULT 'مراجعة القرار مع خبير سنع',
        price_minor INTEGER,
        duration_minutes INTEGER NOT NULL DEFAULT 30 CHECK (duration_minutes > 0),
        free_first_case BOOLEAN NOT NULL DEFAULT true,
        client_copy TEXT NOT NULL DEFAULT 'جلسة قصيرة لمراجعة النتيجة قبل التنفيذ',
        paid_enabled BOOLEAN NOT NULL DEFAULT false,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""INSERT INTO human_review_settings(singleton_key)
                  VALUES ('default') ON CONFLICT (singleton_key) DO NOTHING""")
    db.execute("""CREATE TABLE IF NOT EXISTS human_review_slots (
        slot_id TEXT PRIMARY KEY,
        starts_at TIMESTAMPTZ NOT NULL,
        duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
        reviewer_account_id TEXT REFERENCES user_accounts(account_id),
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS case_human_reviews (
        review_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        case_id TEXT NOT NULL REFERENCES cases(case_id),
        decision_id TEXT REFERENCES decisions(decision_id),
        requested_by TEXT NOT NULL REFERENCES user_accounts(account_id),
        reviewer_account_id TEXT REFERENCES user_accounts(account_id),
        reason_code TEXT NOT NULL,
        reason_label TEXT NOT NULL,
        status TEXT NOT NULL CHECK
          (status IN ('REQUESTED','SCHEDULED','COMPLETED','CANCELLED')),
        slot_id TEXT REFERENCES human_review_slots(slot_id),
        scheduled_at TIMESTAMPTZ,
        before_snapshot_json TEXT NOT NULL,
        after_snapshot_json TEXT,
        decision_changed BOOLEAN,
        final_decision TEXT,
        change_reason TEXT,
        approved_kpi TEXT,
        next_action TEXT,
        client_note TEXT,
        reviewer_note TEXT,
        expert_summary_json TEXT,
        expert_summary_status TEXT NOT NULL DEFAULT 'DRAFT',
        notes_updated_at TIMESTAMPTZ,
        formatted_at TIMESTAMPTZ,
        approved_at TIMESTAMPTZ,
        approved_by TEXT REFERENCES user_accounts(account_id),
        requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        cancelled_at TIMESTAMPTZ
    )""")
    for statement in (
        "ALTER TABLE case_human_reviews ADD COLUMN IF NOT EXISTS client_note TEXT",
        "ALTER TABLE case_human_reviews ADD COLUMN IF NOT EXISTS expert_summary_json TEXT",
        "ALTER TABLE case_human_reviews ADD COLUMN IF NOT EXISTS "
        "expert_summary_status TEXT NOT NULL DEFAULT 'DRAFT'",
        "ALTER TABLE case_human_reviews ADD COLUMN IF NOT EXISTS notes_updated_at TIMESTAMPTZ",
        "ALTER TABLE case_human_reviews ADD COLUMN IF NOT EXISTS formatted_at TIMESTAMPTZ",
        "ALTER TABLE case_human_reviews ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ",
        "ALTER TABLE case_human_reviews ADD COLUMN IF NOT EXISTS "
        "approved_by TEXT REFERENCES user_accounts(account_id)",
    ):
        db.execute(statement)
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_human_review_active_slot
                  ON case_human_reviews(slot_id)
                  WHERE status IN ('REQUESTED','SCHEDULED')""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_human_reviews_case
                  ON case_human_reviews(company_id,case_id,requested_at DESC)""")
    db.execute("""CREATE TABLE IF NOT EXISTS human_review_events (
        event_id TEXT PRIMARY KEY,
        review_id TEXT NOT NULL REFERENCES case_human_reviews(review_id),
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        from_status TEXT,
        to_status TEXT NOT NULL,
        actor_account_id TEXT REFERENCES user_accounts(account_id),
        details_json TEXT NOT NULL DEFAULT '{}',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")


def settings(db):
    return dict(db.execute(
        "SELECT * FROM human_review_settings WHERE singleton_key='default'"
    ).fetchone())


def latest_for_case(db, company_id, case_id):
    row = db.execute(
        """SELECT r.*,s.starts_at,s.duration_minutes,c.name AS company_name,
                  ca.case_title,d.title AS decision_title
           FROM case_human_reviews r
           LEFT JOIN human_review_slots s ON s.slot_id=r.slot_id
           JOIN companies c ON c.company_id=r.company_id
           JOIN cases ca ON ca.case_id=r.case_id
           LEFT JOIN decisions d ON d.decision_id=r.decision_id
           WHERE r.company_id=? AND r.case_id=?
           ORDER BY r.requested_at DESC,r.review_id DESC LIMIT 1""",
        (company_id, case_id),
    ).fetchone()
    return dict(row) if row else None


def available_slots(db):
    return [
        dict(row) for row in db.execute(
            """SELECT s.slot_id,s.starts_at,s.duration_minutes
               FROM human_review_slots s
               WHERE s.is_active=true AND s.starts_at > now()
                 AND NOT EXISTS (
                   SELECT 1 FROM case_human_reviews r
                   WHERE r.slot_id=s.slot_id
                     AND r.status IN ('REQUESTED','SCHEDULED')
                 )
               ORDER BY s.starts_at ASC LIMIT 30"""
        ).fetchall()
    ]


def snapshot_for_case(db, company_id, case_id):
    decision = db.execute(
        """SELECT decision_id,title,recommended_action,reason,confidence_score,
                  success_metric,status,scan_id,created_at
           FROM decisions WHERE company_id=? AND case_id=?
           ORDER BY created_at DESC,decision_id DESC LIMIT 1""",
        (company_id, case_id),
    ).fetchone()
    scan = db.execute(
        """SELECT scan_id,status,result FROM scan_runs
           WHERE company_id=? AND case_id=?
           ORDER BY created_at DESC,scan_id DESC LIMIT 1""",
        (company_id, case_id),
    ).fetchone()
    scan_result = {}
    if scan:
        try:
            scan_result = json.loads(scan["result"] or "{}")
        except (TypeError, json.JSONDecodeError):
            scan_result = {}
    return {
        "decision": dict(decision) if decision else None,
        "scan": {
            "scan_id": scan["scan_id"] if scan else None,
            "status": scan["status"] if scan else None,
            "decision_readiness": scan_result.get("decision_readiness"),
            "decision_confidence": scan_result.get("decision_confidence"),
            "missing_evidence": scan_result.get("missing_evidence") or [],
            "proposed_decision": scan_result.get("proposed_decision"),
            "diagnostic_baseline": scan_result.get("diagnostic_baseline"),
        },
    }


def add_event(db, review_id, company_id, from_status, to_status, actor_id, details=None):
    if to_status not in REVIEW_STATUSES:
        raise ValueError("INVALID_REVIEW_STATUS")
    db.execute(
        """INSERT INTO human_review_events
           (event_id,review_id,company_id,from_status,to_status,actor_account_id,details_json)
           VALUES (?,?,?,?,?,?,?)""",
        (
            "HRE-" + uuid.uuid4().hex[:12].upper(), review_id, company_id,
            from_status, to_status, actor_id,
            json.dumps(details or {}, ensure_ascii=False),
        ),
    )


def client_payload(db, company_id, case_id):
    config = settings(db)
    review = latest_for_case(db, company_id, case_id)
    snapshot = snapshot_for_case(db, company_id, case_id)
    critical = bool(
        snapshot["scan"].get("decision_readiness") == "CONDITIONAL"
        or snapshot["scan"].get("missing_evidence")
    )
    client_review = None
    if review:
        client_review = {
            key: review.get(key) for key in (
                "review_id", "company_id", "case_id", "decision_id",
                "reason_label", "status", "scheduled_at", "requested_at",
                "completed_at", "final_decision", "approved_kpi", "next_action",
                "client_note", "expert_summary_status", "approved_at",
            )
        }
        if review.get("expert_summary_status") == "APPROVED":
            query = (
                f"?view=expert-summary&case_id={case_id}"
                f"&review_id={review['review_id']}"
            )
            client_review["summary_url"] = (
                f"/company/{company_id}/scan-report{query}"
            )
            client_review["summary_pdf_url"] = (
                f"/api/companies/{company_id}/passport/report-pdf{query}"
            )
    return {
        "settings": {
            "offer_mode": config["offer_mode"],
            "offer_name": config["offer_name"],
            "price_minor": config["price_minor"],
            "duration_minutes": config["duration_minutes"],
            "free_first_case": config["free_first_case"],
            "client_copy": config["client_copy"],
            "available": not (
                config["offer_mode"] == "PAID" and not config["paid_enabled"]
            ),
        },
        "review": client_review,
        "slots": [] if review and review["status"] in {"REQUESTED", "SCHEDULED"} else available_slots(db),
        "needs_emphasis": critical,
        "reasons": REVIEW_REASONS,
    }


def _json_object(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _display_text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in (
            "statement", "conflict_note", "title", "label", "question",
            "reason", "message",
        ):
            text = str(value.get(key) or "").strip()
            if text:
                return text
    return ""


def format_expert_summary(db, review_id):
    """Build a bounded summary from persisted case data and human notes only."""
    review = db.execute(
        """SELECT r.*,c.name AS company_name,ca.case_title,ca.declared_problem,
                  ca.real_question,d.title AS decision_title,
                  d.recommended_action,d.reason AS decision_reason,
                  u.email AS reviewer_email
           FROM case_human_reviews r
           JOIN companies c ON c.company_id=r.company_id
           JOIN cases ca ON ca.case_id=r.case_id
           LEFT JOIN decisions d ON d.decision_id=r.decision_id
           LEFT JOIN user_accounts u ON u.account_id=r.reviewer_account_id
           WHERE r.review_id=?""",
        (review_id,),
    ).fetchone()
    if not review:
        raise ValueError("REVIEW_NOT_FOUND")
    notes = str(review["reviewer_note"] or "").strip()
    if not notes:
        raise ValueError("EXPERT_NOTES_REQUIRED")

    scan_row = db.execute(
        """SELECT result FROM scan_runs
           WHERE company_id=? AND case_id=?
           ORDER BY created_at DESC,scan_id DESC LIMIT 1""",
        (review["company_id"], review["case_id"]),
    ).fetchone()
    scan = _json_object(scan_row["result"] if scan_row else None)

    evidence_rows = db.execute(
        """SELECT evidence_id,title,source_ref,source_type,verification_status,
                  confidence,date_collected
           FROM evidence WHERE company_id=? AND case_id=?
           ORDER BY date_collected DESC,evidence_id DESC LIMIT 12""",
        (review["company_id"], review["case_id"]),
    ).fetchall()
    evidence = [{
        "evidence_id": row["evidence_id"],
        "title": row["title"],
        "source_ref": row["source_ref"] or row["source_type"] or "دون مصدر محدد",
        "verification_status": row["verification_status"] or "UNVERIFIED",
        "confidence": row["confidence"],
        "date_collected": row["date_collected"],
    } for row in evidence_rows]

    relation_rows = db.execute(
        """SELECT a.title AS from_title,b.title AS to_title,
                  r.verification_question
           FROM evidence_relations r
           JOIN evidence a ON a.evidence_id=r.from_evidence_id
           JOIN evidence b ON b.evidence_id=r.to_evidence_id
           WHERE r.company_id=? AND r.case_id=?
             AND r.relation_type='CONTRADICTS' AND r.status='OPEN'
           ORDER BY r.created_at DESC LIMIT 12""",
        (review["company_id"], review["case_id"]),
    ).fetchall()
    conflicts = [
        "يتعارض «{}» مع «{}»{}".format(
            row["from_title"], row["to_title"],
            (
                f" — {row['verification_question']}"
                if row["verification_question"] else ""
            ),
        )
        for row in relation_rows
    ]
    for item in scan.get("open_conflicts") or []:
        text = _display_text(item)
        if text and text not in conflicts:
            conflicts.append(text)

    missing = []
    for item in scan.get("missing_evidence") or []:
        text = _display_text(item)
        if text and text not in missing:
            missing.append(text)
    missing.append(
        "ملاحظات الخبير تبقى Expert Observation / Human Review، وأي رقم أو "
        "ادعاء فيها يحتاج مصدرًا مباشرًا قبل اعتباره حقيقة."
    )

    bottleneck = _display_text(scan.get("bottleneck"))
    proposed = _display_text(scan.get("proposed_decision"))
    task = None
    if review["decision_id"]:
        task = db.execute(
            """SELECT title,status,due_date,kpi FROM tasks
               WHERE company_id=? AND decision_id=?
               ORDER BY created_at DESC,task_id DESC LIMIT 1""",
            (review["company_id"], review["decision_id"]),
        ).fetchone()

    current_situation = (
        review["real_question"]
        or review["declared_problem"]
        or review["case_title"]
    )
    recommendation = (
        review["final_decision"]
        or review["decision_title"]
        or proposed
        or "لم تُسجّل توصية عملية في الحالة حتى الآن."
    )
    next_step = (
        task["title"] if task
        else review["recommended_action"]
        or "لم تُسجّل خطوة تالية في الحالة حتى الآن."
    )
    return {
        "title": "ملخص مراجعة الخبير",
        "review_id": review["review_id"],
        "company_id": review["company_id"],
        "case_id": review["case_id"],
        "company_name": review["company_name"],
        "review_date": date.today().isoformat(),
        "reviewer": review["reviewer_email"] or "مراجع سنع",
        "classification": "Expert Observation / Human Review",
        "current_situation": current_situation,
        "expert_observations": notes,
        "evidence": evidence,
        "needs_confirmation": missing,
        "conflicts": conflicts,
        "priority": bottleneck or "لم تُسجّل أولوية صريحة في الحالة حتى الآن.",
        "recommendation": recommendation,
        "next_step": next_step,
        "trust_note": (
            "هذا الملخص يعيد تنظيم ملاحظات بشرية وبيانات محفوظة في الحالة فقط. "
            "لا يحوّل ملاحظة الخبير إلى Fact، ولا يضيف سببًا جذريًا أو KPI أو نتيجة."
        ),
    }


def summary_for_review(
    db, company_id, case_id, review_id=None, *, allow_unapproved=False
):
    params = [company_id, case_id]
    review_filter = ""
    if review_id:
        review_filter = " AND review_id=?"
        params.append(review_id)
    approval_filter = "" if allow_unapproved else (
        " AND expert_summary_status='APPROVED'"
    )
    row = db.execute(
        f"""SELECT review_id,expert_summary_json,expert_summary_status,
                   formatted_at,approved_at
            FROM case_human_reviews
            WHERE company_id=? AND case_id=?{review_filter}
              AND expert_summary_json IS NOT NULL{approval_filter}
            ORDER BY requested_at DESC,review_id DESC LIMIT 1""",
        params,
    ).fetchone()
    if not row:
        return None
    summary = _json_object(row["expert_summary_json"])
    if not summary:
        return None
    summary["summary_status"] = row["expert_summary_status"]
    summary["formatted_at"] = row["formatted_at"]
    summary["approved_at"] = row["approved_at"]
    return summary