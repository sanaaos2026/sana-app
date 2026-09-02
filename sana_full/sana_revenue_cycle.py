"""دورة الإيراد والتعلّم التجاري فوق سجل opportunities القائم."""
import json
import hashlib
import re
import uuid
from datetime import date
from decimal import Decimal

from database_config import acquire_schema_lock


VERSION = "v1.0"
NA_DEFERRED = "N/A — Deferred"
STAGES = (
    ("lead", "Lead", "عميل محتمل"),
    ("qualification", "Qualification", "تأهيل"),
    ("discovery", "Discovery", "اكتشاف"),
    ("opportunity", "Opportunity", "فرصة"),
    ("proposal", "Proposal", "عرض"),
    ("negotiation", "Negotiation", "تفاوض"),
    ("won", "Won", "فوز"),
    ("lost", "Lost", "خسارة"),
    ("delivery", "Delivery", "تنفيذ"),
    ("collection", "Collection", "تحصيل"),
    ("retention_upsell", "Retention/Upsell", "احتفاظ/توسع"),
)
STAGE_IDS = tuple(item[0] for item in STAGES)
STAGE_LABELS = {item[0]: item[2] for item in STAGES}
LEGACY_TO_STAGE = {
    "عميل محتمل": "lead",
    "مؤهل": "qualification",
    "عرض مرسل": "proposal",
    "تفاوض": "negotiation",
    "فوز": "won",
    "خسارة": "lost",
}
STAGE_TO_LEGACY = {value: key for key, value in LEGACY_TO_STAGE.items()}
CLASSIFICATIONS = {"Fact", "Assumption", "Forecast", "Conflict"}


def _id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _text(value):
    return str(value or "").strip() or None


def _money(value, field, allow_none=True):
    if value in (None, "") and allow_none:
        return None
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field}_INVALID") from exc
    if result < 0:
        raise ValueError(f"{field}_INVALID")
    return result


def _date(value, field, allow_none=True):
    raw = _text(value)
    if not raw and allow_none:
        return None
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_INVALID") from exc


def ensure_schema(db):
    acquire_schema_lock(db)
    opp_columns = {
        row["column_name"] for row in db.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name='opportunities'"""
        ).fetchall()
    }
    for name, definition in (
        ("revenue_stage_id", "TEXT"),
        ("service_name", "TEXT"),
        ("channel_name", "TEXT"),
        ("sector_name", "TEXT"),
    ):
        if name not in opp_columns:
            db.execute(f"ALTER TABLE opportunities ADD COLUMN {name} {definition}")
    db.execute("""CREATE TABLE IF NOT EXISTS rc_stage_profiles (
        profile_id TEXT PRIMARY KEY,
        project_type TEXT UNIQUE NOT NULL,
        stages_json TEXT NOT NULL,
        version TEXT NOT NULL,
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS rc_stage_history (
        transition_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        opp_id TEXT NOT NULL REFERENCES opportunities(opp_id) ON DELETE CASCADE,
        from_stage_id TEXT,
        to_stage_id TEXT NOT NULL,
        owner_id TEXT,
        reason TEXT,
        source_ref TEXT NOT NULL,
        transitioned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (to_stage_id IN ('lead','qualification','discovery','opportunity',
          'proposal','negotiation','won','lost','delivery','collection','retention_upsell'))
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_rc_history_company
                  ON rc_stage_history(company_id, transitioned_at)""")
    db.execute("""CREATE TABLE IF NOT EXISTS rc_opportunity_economics (
        opp_id TEXT PRIMARY KEY REFERENCES opportunities(opp_id) ON DELETE CASCADE,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        customer_problem TEXT,
        customer_language TEXT,
        decision_maker TEXT,
        objection TEXT,
        winning_offer TEXT,
        outcome_reason TEXT,
        contract_value NUMERIC,
        recognized_revenue NUMERIC,
        delivery_cost NUMERIC,
        acquisition_cost NUMERIC,
        closed_at TIMESTAMPTZ,
        source_ref TEXT NOT NULL,
        observed_at DATE NOT NULL,
        classification TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (classification IN ('Fact','Assumption','Forecast','Conflict'))
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS rc_projects (
        project_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        opp_id TEXT NOT NULL UNIQUE REFERENCES opportunities(opp_id),
        delivery_task_id TEXT REFERENCES tasks(task_id),
        status TEXT NOT NULL DEFAULT 'open',
        source_ref TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (status IN ('open','delivering','completed','cancelled'))
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS rc_invoices (
        invoice_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        opp_id TEXT NOT NULL REFERENCES opportunities(opp_id),
        external_invoice_id TEXT,
        amount_due NUMERIC NOT NULL,
        amount_paid NUMERIC NOT NULL DEFAULT 0,
        issued_at DATE NOT NULL,
        due_at DATE NOT NULL,
        status TEXT NOT NULL DEFAULT 'issued',
        source_ref TEXT NOT NULL,
        classification TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (amount_due >= 0 AND amount_paid >= 0 AND amount_paid <= amount_due),
        CHECK (status IN ('issued','partially_paid','paid','overdue','void')),
        CHECK (classification IN ('Fact','Assumption','Forecast','Conflict')),
        UNIQUE(company_id, external_invoice_id)
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_rc_invoice_company
                  ON rc_invoices(company_id, due_at, status)""")
    db.execute("""CREATE TABLE IF NOT EXISTS rc_deal_learning (
        learning_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        opp_id TEXT NOT NULL UNIQUE REFERENCES opportunities(opp_id),
        outcome TEXT NOT NULL,
        outcome_reason TEXT NOT NULL,
        objection TEXT,
        customer_problem TEXT,
        customer_language TEXT,
        winning_offer TEXT,
        sector TEXT,
        decision_maker TEXT,
        evidence_ids_json TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        review_status TEXT NOT NULL DEFAULT 'draft',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (outcome IN ('won','lost')),
        CHECK (review_status IN ('draft','approved','rejected'))
    )""")
    db.execute(
        """INSERT INTO rc_stage_profiles
           (profile_id, project_type, stages_json, version)
           VALUES ('RC-STAGES-B2B','b2b_services',?,?,?)
           ON CONFLICT (project_type) DO NOTHING""".replace("?,?,?", "?,?"),
        (json.dumps(STAGE_IDS, ensure_ascii=False), VERSION),
    )
    for legacy, stage_id in LEGACY_TO_STAGE.items():
        db.execute(
            """UPDATE opportunities SET revenue_stage_id=?
               WHERE revenue_stage_id IS NULL AND stage=?""",
            (stage_id, legacy),
        )


def stage_catalog(db, project_type="b2b_services"):
    ensure_schema(db)
    row = db.execute(
        """SELECT stages_json, version FROM rc_stage_profiles
           WHERE project_type=? AND is_active=true""",
        (project_type,),
    ).fetchone()
    stage_ids = json.loads(row["stages_json"]) if row else list(STAGE_IDS)
    return {
        "project_type": project_type,
        "version": row["version"] if row else VERSION,
        "stages": [
            {"id": stage_id, "label": STAGE_LABELS[stage_id],
             "legacy_stage": STAGE_TO_LEGACY.get(stage_id)}
            for stage_id in stage_ids
        ],
        "legacy_mapping": LEGACY_TO_STAGE,
    }


def record_transition(db, *, company_id, opp_id, to_stage_id, owner_id,
                      reason=None, source_ref=None, transitioned_at=None):
    ensure_schema(db)
    if to_stage_id not in STAGE_IDS:
        raise ValueError("INVALID_REVENUE_STAGE")
    opp = db.execute(
        "SELECT * FROM opportunities WHERE opp_id=? AND company_id=? FOR UPDATE",
        (opp_id, company_id),
    ).fetchone()
    if not opp:
        raise ValueError("OPP_NOT_FOUND")
    from_stage = opp["revenue_stage_id"]
    if to_stage_id == "lost" and not _text(reason):
        raise ValueError("OUTCOME_REASON_REQUIRED")
    if to_stage_id == from_stage:
        return {"transitioned": False, "stage_id": to_stage_id}
    legacy = STAGE_TO_LEGACY.get(to_stage_id)
    if legacy:
        db.execute(
            """UPDATE opportunities SET revenue_stage_id=?, stage=?,
               outcome_reason=CASE WHEN ? IN ('won','lost') THEN ? ELSE outcome_reason END,
               updated_at=now() WHERE opp_id=? AND company_id=?""",
            (to_stage_id, legacy, to_stage_id, _text(reason), opp_id, company_id),
        )
    else:
        db.execute(
            """UPDATE opportunities SET revenue_stage_id=?, updated_at=now()
               WHERE opp_id=? AND company_id=?""",
            (to_stage_id, opp_id, company_id),
        )
    db.execute(
        """INSERT INTO rc_stage_history
           (transition_id, company_id, opp_id, from_stage_id, to_stage_id,
            owner_id, reason, source_ref, transitioned_at)
           VALUES (?,?,?,?,?,?,?,?,COALESCE(?,now()))""",
        (_id("RCT"), company_id, opp_id, from_stage, to_stage_id,
         _text(owner_id), _text(reason), _text(source_ref) or "manual",
         transitioned_at),
    )
    if to_stage_id == "won":
        _ensure_project(db, dict(opp))
    _ensure_canonical(db, company_id, "opportunity", "opportunities", opp_id, opp_id)
    return {"transitioned": True, "stage_id": to_stage_id}


def record_legacy_transition(db, opp, legacy_stage, owner_id, reason=None):
    return record_transition(
        db, company_id=opp["company_id"], opp_id=opp["opp_id"],
        to_stage_id=LEGACY_TO_STAGE[legacy_stage], owner_id=owner_id,
        reason=reason, source_ref="sales_stage_history",
    )


def _ensure_project(db, opp):
    current = db.execute(
        "SELECT delivery_task_id FROM opportunities WHERE opp_id=? AND company_id=?",
        (opp["opp_id"], opp["company_id"]),
    ).fetchone()
    existing = db.execute(
        "SELECT project_id FROM rc_projects WHERE opp_id=? AND company_id=?",
        (opp["opp_id"], opp["company_id"]),
    ).fetchone()
    if existing:
        return existing["project_id"]
    delivery_task_id = current["delivery_task_id"] if current else None
    if not delivery_task_id:
        delivery_task_id = _id("TSK-DEL")
        db.execute(
            """INSERT INTO tasks (task_id,company_id,title,status,priority)
               VALUES (?,?,?,?,?)""",
            (delivery_task_id, opp["company_id"],
             f"بدء تسليم: {opp['title']}", "لم تبدأ", "عالية"),
        )
        db.execute(
            """UPDATE opportunities SET delivery_task_id=?
               WHERE opp_id=? AND company_id=? AND delivery_task_id IS NULL""",
            (delivery_task_id, opp["opp_id"], opp["company_id"]),
        )
    project_id = _id("RCP")
    db.execute(
        """INSERT INTO rc_projects
           (project_id, company_id, opp_id, delivery_task_id, source_ref)
           VALUES (?,?,?,?,?) ON CONFLICT (opp_id) DO NOTHING""",
        (project_id, opp["company_id"], opp["opp_id"],
         delivery_task_id, "opportunity.won"),
    )
    _ensure_canonical(
        db, opp["company_id"], "project", "rc_projects", project_id,
        f"opportunity:{opp['opp_id']}",
    )
    return project_id


def _ensure_canonical(db, company_id, entity_type, source_table, source_id,
                      identity_key, display_name=None):
    governance = db.execute(
        """SELECT governance_id FROM gos_governance_registry
           WHERE record_type='canonical_entity'"""
    ).fetchone()
    if not governance:
        return
    digest = hashlib.sha256(
        f"{company_id}|{entity_type}|{identity_key}".encode("utf-8")
    ).hexdigest()[:18].upper()
    db.execute(
        """INSERT INTO gos_canonical_entities
           (canonical_id,company_id,entity_type,source_table,source_id,
            identity_key,display_name,governance_id)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT (company_id,entity_type,identity_key) DO NOTHING""",
        (f"CAN-{digest}", company_id, entity_type, source_table, source_id,
         identity_key, display_name, governance["governance_id"]),
    )


def upsert_economics(db, company_id, opp_id, payload):
    ensure_schema(db)
    opp = db.execute(
        "SELECT 1 FROM opportunities WHERE company_id=? AND opp_id=?",
        (company_id, opp_id),
    ).fetchone()
    if not opp:
        raise ValueError("OPP_NOT_FOUND")
    source_ref = _text(payload.get("source_ref"))
    observed_at = _date(payload.get("observed_at"), "OBSERVED_AT", False)
    classification = _text(payload.get("classification"))
    if not source_ref:
        raise ValueError("SOURCE_REF_REQUIRED")
    if classification not in CLASSIFICATIONS:
        raise ValueError("CLASSIFICATION_INVALID")
    values = (
        _text(payload.get("customer_problem")),
        _text(payload.get("customer_language")),
        _text(payload.get("decision_maker")),
        _text(payload.get("objection")),
        _text(payload.get("winning_offer")),
        _text(payload.get("outcome_reason")),
        _money(payload.get("contract_value"), "CONTRACT_VALUE"),
        _money(payload.get("recognized_revenue"), "RECOGNIZED_REVENUE"),
        _money(payload.get("delivery_cost"), "DELIVERY_COST"),
        _money(payload.get("acquisition_cost"), "ACQUISITION_COST"),
        _text(payload.get("closed_at")),
        source_ref, observed_at, classification,
    )
    db.execute(
        """INSERT INTO rc_opportunity_economics
           (opp_id,company_id,customer_problem,customer_language,decision_maker,
            objection,winning_offer,outcome_reason,contract_value,recognized_revenue,
            delivery_cost,acquisition_cost,closed_at,source_ref,observed_at,classification)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT (opp_id) DO UPDATE SET
            customer_problem=EXCLUDED.customer_problem,
            customer_language=EXCLUDED.customer_language,
            decision_maker=EXCLUDED.decision_maker,
            objection=EXCLUDED.objection,winning_offer=EXCLUDED.winning_offer,
            outcome_reason=EXCLUDED.outcome_reason,
            contract_value=EXCLUDED.contract_value,
            recognized_revenue=EXCLUDED.recognized_revenue,
            delivery_cost=EXCLUDED.delivery_cost,
            acquisition_cost=EXCLUDED.acquisition_cost,
            closed_at=EXCLUDED.closed_at,source_ref=EXCLUDED.source_ref,
            observed_at=EXCLUDED.observed_at,classification=EXCLUDED.classification,
            updated_at=now()
           WHERE rc_opportunity_economics.company_id=EXCLUDED.company_id""",
        (opp_id, company_id, *values),
    )
    db.execute(
        """UPDATE opportunities SET service_name=COALESCE(?,service_name),
           channel_name=COALESCE(?,channel_name),sector_name=COALESCE(?,sector_name)
           WHERE opp_id=? AND company_id=?""",
        (_text(payload.get("service")), _text(payload.get("channel")),
         _text(payload.get("sector")), opp_id, company_id),
    )
    return {"opp_id": opp_id}


def create_invoice(db, company_id, opp_id, payload):
    ensure_schema(db)
    if not db.execute(
        "SELECT 1 FROM opportunities WHERE company_id=? AND opp_id=?",
        (company_id, opp_id),
    ).fetchone():
        raise ValueError("OPP_NOT_FOUND")
    source_ref = _text(payload.get("source_ref"))
    classification = _text(payload.get("classification"))
    if not source_ref:
        raise ValueError("SOURCE_REF_REQUIRED")
    if classification not in CLASSIFICATIONS:
        raise ValueError("CLASSIFICATION_INVALID")
    invoice_id = _id("INV")
    external_id = _text(payload.get("external_invoice_id"))
    if not external_id:
        raise ValueError("EXTERNAL_INVOICE_ID_REQUIRED")
    db.execute(
        """INSERT INTO rc_invoices
           (invoice_id,company_id,opp_id,external_invoice_id,amount_due,amount_paid,
            issued_at,due_at,status,source_ref,classification)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (invoice_id, company_id, opp_id, external_id,
         _money(payload.get("amount_due"), "AMOUNT_DUE", False),
         _money(payload.get("amount_paid") or 0, "AMOUNT_PAID", False),
         _date(payload.get("issued_at"), "ISSUED_AT", False),
         _date(payload.get("due_at"), "DUE_AT", False),
         "issued", source_ref, classification),
    )
    refresh_invoice_statuses(db, company_id)
    _ensure_canonical(
        db, company_id, "invoice", "rc_invoices", invoice_id,
        external_id or invoice_id,
    )
    return {"invoice_id": invoice_id}


def record_payment(db, company_id, invoice_id, payload):
    ensure_schema(db)
    row = db.execute(
        "SELECT * FROM rc_invoices WHERE invoice_id=? AND company_id=?",
        (invoice_id, company_id),
    ).fetchone()
    if not row:
        raise ValueError("INVOICE_NOT_FOUND")
    paid = _money(payload.get("amount_paid"), "AMOUNT_PAID", False)
    if paid > Decimal(row["amount_due"]):
        raise ValueError("AMOUNT_PAID_EXCEEDS_DUE")
    source_ref = _text(payload.get("source_ref"))
    if not source_ref:
        raise ValueError("SOURCE_REF_REQUIRED")
    status = "paid" if paid == Decimal(row["amount_due"]) else "partially_paid"
    db.execute(
        """UPDATE rc_invoices SET amount_paid=?,status=?,source_ref=?,updated_at=now()
           WHERE invoice_id=? AND company_id=?""",
        (paid, status, source_ref, invoice_id, company_id),
    )
    return {"invoice_id": invoice_id, "status": status}


def refresh_invoice_statuses(db, company_id):
    db.execute(
        """UPDATE rc_invoices SET status='overdue',updated_at=now()
           WHERE company_id=? AND status IN ('issued','partially_paid')
             AND due_at<CURRENT_DATE AND amount_paid<amount_due""",
        (company_id,),
    )


def upsert_learning(db, company_id, opp_id, payload):
    ensure_schema(db)
    opp = db.execute(
        "SELECT stage,revenue_stage_id FROM opportunities WHERE company_id=? AND opp_id=?",
        (company_id, opp_id),
    ).fetchone()
    if not opp:
        raise ValueError("OPP_NOT_FOUND")
    outcome = _text(payload.get("outcome")) or opp["revenue_stage_id"]
    if outcome not in {"won", "lost"}:
        raise ValueError("CLOSED_OUTCOME_REQUIRED")
    reason = _text(payload.get("outcome_reason"))
    evidence_ids = payload.get("evidence_ids") or []
    source_ref = _text(payload.get("source_ref"))
    if not reason:
        raise ValueError("OUTCOME_REASON_REQUIRED")
    if not source_ref or not evidence_ids:
        raise ValueError("EVIDENCE_AND_SOURCE_REQUIRED")
    learning_id = _id("RCL")
    db.execute(
        """INSERT INTO rc_deal_learning
           (learning_id,company_id,opp_id,outcome,outcome_reason,objection,
            customer_problem,customer_language,winning_offer,sector,decision_maker,
            evidence_ids_json,source_ref,review_status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT (opp_id) DO UPDATE SET
            outcome=EXCLUDED.outcome,outcome_reason=EXCLUDED.outcome_reason,
            objection=EXCLUDED.objection,customer_problem=EXCLUDED.customer_problem,
            customer_language=EXCLUDED.customer_language,
            winning_offer=EXCLUDED.winning_offer,sector=EXCLUDED.sector,
            decision_maker=EXCLUDED.decision_maker,
            evidence_ids_json=EXCLUDED.evidence_ids_json,
            source_ref=EXCLUDED.source_ref,review_status=EXCLUDED.review_status,
            updated_at=now()
           WHERE rc_deal_learning.company_id=EXCLUDED.company_id""",
        (learning_id, company_id, opp_id, outcome, reason,
         _text(payload.get("objection")), _text(payload.get("customer_problem")),
         _text(payload.get("customer_language")), _text(payload.get("winning_offer")),
         _text(payload.get("sector")), _text(payload.get("decision_maker")),
         json.dumps(evidence_ids, ensure_ascii=False), source_ref,
         "approved" if payload.get("review_status") == "approved" else "draft"),
    )
    return {"opp_id": opp_id}


def opportunity_context(db, company_id, opp_id):
    ensure_schema(db)
    economics = db.execute(
        "SELECT * FROM rc_opportunity_economics WHERE company_id=? AND opp_id=?",
        (company_id, opp_id),
    ).fetchone()
    invoices = db.execute(
        "SELECT * FROM rc_invoices WHERE company_id=? AND opp_id=? ORDER BY issued_at",
        (company_id, opp_id),
    ).fetchall()
    learning = db.execute(
        "SELECT * FROM rc_deal_learning WHERE company_id=? AND opp_id=?",
        (company_id, opp_id),
    ).fetchone()
    return {
        "economics": dict(economics) if economics else None,
        "invoices": [dict(row) for row in invoices],
        "learning": dict(learning) if learning else None,
    }


def report(db, company_id, filters=None):
    ensure_schema(db)
    filters = filters or {}
    conditions = ["o.company_id=?", "o.archived=0"]
    params = [company_id]
    for field, column in (
        ("owner", "o.owner_id"), ("service", "o.service_name"),
        ("channel", "COALESCE(o.channel_name,l.source)"),
        ("sector", "o.sector_name"), ("stage", "o.revenue_stage_id"),
    ):
        value = _text(filters.get(field))
        if value:
            conditions.append(f"{column}=?")
            params.append(value)
    rows = db.execute(
        f"""SELECT o.*,l.name AS customer_name,l.company_name,
                   COALESCE(o.channel_name,l.source) AS effective_channel,
                   e.recognized_revenue,e.delivery_cost,e.acquisition_cost,
                   e.contract_value,e.closed_at,e.classification,e.source_ref
            FROM opportunities o LEFT JOIN leads l ON l.lead_id=o.lead_id
            LEFT JOIN rc_opportunity_economics e ON e.opp_id=o.opp_id
            WHERE {' AND '.join(conditions)} ORDER BY o.created_at""",
        params,
    ).fetchall()
    opportunities = [dict(row) for row in rows]
    counts = {stage: 0 for stage in STAGE_IDS}
    values = {stage: Decimal("0") for stage in STAGE_IDS}
    for row in opportunities:
        stage = row["revenue_stage_id"] or LEGACY_TO_STAGE.get(row["stage"], "lead")
        counts[stage] += 1
        values[stage] += Decimal(row["amount"] or 0)
    leakage = []
    ordered = [stage for stage in STAGE_IDS if stage != "lost"]
    for current, nxt in zip(ordered, ordered[1:]):
        current_count, next_count = counts[current], counts[nxt]
        leakage.append({
            "from_stage": current, "to_stage": nxt,
            "from_count": current_count, "to_count": next_count,
            "leaked_count": max(current_count - next_count, 0),
            "leaked_value": float(max(values[current] - values[nxt], 0)),
            "conversion_rate": (
                round(next_count / current_count * 100, 1) if current_count else None
            ),
        })
    facts = [
        row for row in opportunities
        if row.get("classification") == "Fact" and row.get("source_ref")
    ]
    revenue = sum(Decimal(row["recognized_revenue"] or 0) for row in facts)
    delivery_cost = sum(Decimal(row["delivery_cost"] or 0) for row in facts)
    cac_total = sum(Decimal(row["acquisition_cost"] or 0) for row in facts)
    closed = [row for row in opportunities if row.get("closed_at")]
    invoices = db.execute(
        """SELECT amount_due,amount_paid,status,due_at,classification,source_ref
           FROM rc_invoices WHERE company_id=? AND status<>'void'""",
        (company_id,),
    ).fetchall()
    fact_invoices = [
        row for row in invoices
        if row["classification"] == "Fact" and row["source_ref"]
    ]
    due = sum(Decimal(row["amount_due"] or 0) for row in fact_invoices)
    paid = sum(Decimal(row["amount_paid"] or 0) for row in fact_invoices)
    wins = [row for row in opportunities if row["revenue_stage_id"] in
            {"won", "delivery", "collection", "retention_upsell"}]
    cycle_days = []
    for row in closed:
        try:
            cycle_days.append(
                (row["closed_at"].date() - row["created_at"].date()).days
            )
        except AttributeError:
            pass
    metrics = {
        "revenue": float(revenue) if facts else None,
        "gross_margin": (
            round(float((revenue - delivery_cost) / revenue * 100), 1)
            if facts and revenue else None
        ),
        "cac": (
            round(float(cac_total / len(wins)), 2)
            if facts and wins else None
        ),
        "cost_of_delivery": float(delivery_cost) if facts else None,
        "collection_rate": round(float(paid / due * 100), 1) if due else None,
        "actual_profit": float(revenue - delivery_cost - cac_total) if facts else None,
        "average_contract_value": (
            round(float(sum(Decimal(r["amount"] or 0) for r in wins) / len(wins)), 2)
            if wins else None
        ),
        "sales_cycle": round(sum(cycle_days) / len(cycle_days), 1) if cycle_days else None,
        "retention": (
            round(counts["retention_upsell"] / len(wins) * 100, 1) if wins else None
        ),
        "source_policy": "Fact + source_ref only; otherwise N/A — Deferred",
    }
    learning_rows = db.execute(
        """SELECT l.*,o.service_name,o.channel_name
           FROM rc_deal_learning l JOIN opportunities o ON o.opp_id=l.opp_id
           WHERE l.company_id=? AND l.review_status='approved'
           ORDER BY l.updated_at DESC""",
        (company_id,),
    ).fetchall()
    return {
        "stage_catalog": stage_catalog(db),
        "opportunities": opportunities,
        "stage_counts": counts,
        "stage_values": {key: float(value) for key, value in values.items()},
        "leakage": leakage,
        "metrics": metrics,
        "overdue_invoices": sum(
            1 for row in invoices
            if row["status"] == "overdue"
            or (row["status"] in {"issued", "partially_paid"}
                and row["due_at"] < date.today()
                and Decimal(row["amount_paid"]) < Decimal(row["amount_due"]))
        ),
        "approved_learning": [dict(row) for row in learning_rows],
    }