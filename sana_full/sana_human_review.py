"""V1 human review requests for a case decision.

The review record is append-only with respect to the original decision:
before_snapshot_json is never overwritten, while the reviewer outcome lives in
after_snapshot_json and status history is stored separately.
"""
import json
import uuid


REVIEW_REASONS = {
    "confirm_decision": "أريد التأكد من القرار",
    "explain_result": "أحتاج تفسير النتيجة",
    "plan_execution": "أريد ترتيب التنفيذ",
    "additional_information": "لدي معلومات إضافية",
    "other_problem": "أريد مناقشة مشكلة أخرى",
}
REVIEW_STATUSES = {"REQUESTED", "SCHEDULED", "COMPLETED", "CANCELLED"}
OFFER_MODES = {"INCLUDED", "FREE", "PAID"}


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
        reviewer_note TEXT,
        requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        cancelled_at TIMESTAMPTZ
    )""")
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
        "review": review,
        "slots": [] if review and review["status"] in {"REQUESTED", "SCHEDULED"} else available_slots(db),
        "needs_emphasis": critical,
        "reasons": REVIEW_REASONS,
    }