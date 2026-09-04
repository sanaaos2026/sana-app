"""غرفة القرار والتنفيذ — تجميع حتمي وطبقة مسؤولية غير هدامة."""
import hashlib
import json
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from database_config import acquire_schema_lock


NA = "N/A — Deferred"
TASK_STATUSES = {"لم تبدأ", "قيد التنفيذ", "متوقفة", "منجزة"}
RISK_STATUSES = {"open", "mitigating", "accepted", "closed"}
BACKLOG_STATUSES = {"backlog", "approved", "deferred", "stopped"}
SOP_MATURITY = {"Manual", "Standardized", "Automatable"}
SOP_KPI_DIRECTIONS = {"higher_is_better", "lower_is_better"}
TASK_REQUIRED_FIELDS = (
    ("owner_user_id", "Owner"),
    ("approver_user_id", "Approver"),
    ("due_date", "Deadline"),
    ("kpi", "KPI"),
)
IMPACT_OUTCOMES = {"IMPROVED", "UNCHANGED", "WORSE", "INCONCLUSIVE"}


def _id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _text(value, field=None):
    result = str(value or "").strip()
    if field and not result:
        raise ValueError(f"{field}_REQUIRED")
    return result or None


def _int(value, field, minimum=0, maximum=100):
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_INVALID") from exc
    if result < minimum or result > maximum:
        raise ValueError(f"{field}_INVALID")
    return result


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _loads(value, default=None):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default

def _task_missing_fields(task):
    """Return the responsibility fields that are blank, including empty strings."""
    return [
        label for field, label in TASK_REQUIRED_FIELDS
        if not str(task.get(field) or "").strip()
    ]
def ensure_schema(db):
    acquire_schema_lock(db)
    columns = {
        row[0] for row in db.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name='tasks'"""
        ).fetchall()
    }
    for name, definition in (
        ("approver_user_id", "TEXT"),
        ("kpi", "TEXT"),
        ("delay_reason", "TEXT"),
        ("experiment_id", "TEXT"),
        ("evidence_ids_json", "TEXT"),
        ("updated_at", "TIMESTAMPTZ DEFAULT now()"),
    ):
        if name not in columns:
            db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")

    db.execute("""CREATE TABLE IF NOT EXISTS execution_task_audit (
        audit_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        actor_id TEXT NOT NULL,
        from_status TEXT,
        to_status TEXT NOT NULL,
        change_json TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_task_audit_company
                  ON execution_task_audit(company_id, changed_at DESC)""")
    db.execute("""CREATE TABLE IF NOT EXISTS p0_impact_reviews (
        review_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        case_id TEXT NOT NULL REFERENCES cases(case_id),
        decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
        task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
        baseline_snapshot_json TEXT NOT NULL,
        baseline_value TEXT,
        baseline_source_ref TEXT,
        baseline_observed_at DATE,
        target_value TEXT,
        target_source_ref TEXT,
        target_observed_at DATE,
        actual_value TEXT,
        actual_source_ref TEXT,
        actual_observed_at DATE,
        result_summary TEXT NOT NULL,
        result_source_ref TEXT NOT NULL,
        impact_outcome TEXT NOT NULL
          CHECK (impact_outcome IN ('IMPROVED','UNCHANGED','WORSE','INCONCLUSIVE')),
        impact_notes TEXT NOT NULL,
        reviewed_by TEXT NOT NULL,
        reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    impact_columns = {
        row[0] for row in db.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name='p0_impact_reviews'"""
        ).fetchall()
    }
    for name, definition in (
        ("baseline_value", "TEXT"),
        ("baseline_source_ref", "TEXT"),
        ("baseline_observed_at", "DATE"),
        ("target_value", "TEXT"),
        ("target_source_ref", "TEXT"),
        ("target_observed_at", "DATE"),
        ("actual_value", "TEXT"),
        ("actual_source_ref", "TEXT"),
        ("actual_observed_at", "DATE"),
        ("baseline_numeric", "NUMERIC"),
        ("target_numeric", "NUMERIC"),
        ("actual_numeric", "NUMERIC"),
        ("measurement_unit", "TEXT"),
        ("kpi_direction", "TEXT"),
        ("baseline_evidence_id", "TEXT"),
        ("actual_evidence_id", "TEXT"),
    ):
        if name not in impact_columns:
            db.execute(f"ALTER TABLE p0_impact_reviews ADD COLUMN {name} {definition}")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_p0_impact_reviews_case
                  ON p0_impact_reviews(company_id,case_id,reviewed_at DESC)""")
    db.execute("""CREATE TABLE IF NOT EXISTS execution_risks (
        risk_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        evidence_ids_json TEXT NOT NULL,
        impact TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        probability INTEGER NOT NULL CHECK (probability BETWEEN 1 AND 5),
        severity INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 5),
        mitigation_plan TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('open','mitigating','accepted','closed')),
        source_ref TEXT NOT NULL,
        review_due_at DATE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_risks_company
                  ON execution_risks(company_id, status, probability, severity)""")
    db.execute("""CREATE TABLE IF NOT EXISTS execution_backlog (
        backlog_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        expected_impact INTEGER NOT NULL CHECK (expected_impact BETWEEN 1 AND 5),
        ease INTEGER NOT NULL CHECK (ease BETWEEN 1 AND 5),
        speed INTEGER NOT NULL CHECK (speed BETWEEN 1 AND 5),
        evidence_strength INTEGER NOT NULL CHECK (evidence_strength BETWEEN 1 AND 5),
        profitability INTEGER NOT NULL CHECK (profitability BETWEEN 1 AND 5),
        risk INTEGER NOT NULL CHECK (risk BETWEEN 1 AND 5),
        score NUMERIC NOT NULL,
        score_explanation TEXT NOT NULL,
        evidence_ids_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'backlog'
          CHECK (status IN ('backlog','approved','deferred','stopped')),
        decision_reason TEXT,
        owner_id TEXT,
        source_ref TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_backlog_company
                  ON execution_backlog(company_id, status, score DESC)""")
    db.execute("""CREATE TABLE IF NOT EXISTS execution_backlog_audit (
        audit_id TEXT PRIMARY KEY,
        backlog_id TEXT NOT NULL REFERENCES execution_backlog(backlog_id) ON DELETE CASCADE,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        actor_id TEXT NOT NULL,
        from_status TEXT,
        to_status TEXT NOT NULL,
        reason TEXT NOT NULL,
        changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS execution_sops (
        sop_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        title TEXT NOT NULL,
        owner_role TEXT NOT NULL,
        maturity TEXT NOT NULL DEFAULT 'Manual'
          CHECK (maturity IN ('Manual','Standardized','Automatable')),
        experiment_id TEXT REFERENCES gos_experiments(experiment_id),
        source_ref TEXT NOT NULL,
        active_version_id TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS execution_sop_versions (
        version_id TEXT PRIMARY KEY,
        sop_id TEXT NOT NULL REFERENCES execution_sops(sop_id) ON DELETE CASCADE,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        version_number INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('draft','approved','deprecated')),
        steps_json TEXT NOT NULL,
        checklist_json TEXT NOT NULL,
        sla TEXT NOT NULL,
        kpi TEXT NOT NULL,
        reusable_template TEXT NOT NULL,
        evidence_ids_json TEXT NOT NULL,
        result_json TEXT NOT NULL,
        repetitions INTEGER NOT NULL DEFAULT 0 CHECK (repetitions >= 0),
        source_ref TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        approved_by TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        approved_at TIMESTAMPTZ,
        UNIQUE(sop_id, version_number),
        UNIQUE(sop_id, content_hash)
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_sops_company
                  ON execution_sops(company_id, maturity, updated_at DESC)""")
    db.execute("""CREATE TABLE IF NOT EXISTS execution_sop_applications (
        application_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        sop_id TEXT NOT NULL REFERENCES execution_sops(sop_id) ON DELETE CASCADE,
        version_id TEXT NOT NULL REFERENCES execution_sop_versions(version_id) ON DELETE CASCADE,
        application_ref TEXT NOT NULL,
        kpi TEXT NOT NULL REFERENCES gos_metric_definitions(metric_key),
        direction TEXT NOT NULL
          CHECK (direction IN ('higher_is_better','lower_is_better')),
        baseline_id TEXT NOT NULL REFERENCES gos_baselines(baseline_id),
        baseline_value NUMERIC NOT NULL,
        baseline_source_ref TEXT NOT NULL,
        result_value NUMERIC NOT NULL,
        result_source_ref TEXT NOT NULL,
        result_observed_at DATE NOT NULL,
        unit TEXT NOT NULL,
        delta NUMERIC NOT NULL,
        comparison_status TEXT NOT NULL
          CHECK (comparison_status IN ('improved','not_improved','unchanged')),
        result_evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
        evidence_ids_json TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        recorded_by TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_sop_applications_company
                  ON execution_sop_applications(company_id,sop_id,version_id,created_at DESC)""")
    application_columns = {
        row[0] for row in db.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public'
                 AND table_name='execution_sop_applications'"""
        ).fetchall()
    }
    if "result_evidence_id" not in application_columns:
        db.execute(
            """ALTER TABLE execution_sop_applications
               ADD COLUMN result_evidence_id TEXT REFERENCES evidence(evidence_id)"""
        )
    if "application_ref" not in application_columns:
        db.execute(
            "ALTER TABLE execution_sop_applications ADD COLUMN application_ref TEXT"
        )
    db.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_sop_application_result_evidence
           ON execution_sop_applications(company_id,result_evidence_id)
           WHERE result_evidence_id IS NOT NULL"""
    )
    db.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_sop_application_execution
           ON execution_sop_applications(company_id,version_id,application_ref)
           WHERE application_ref IS NOT NULL"""
    )
    db.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_sop_application_outcome
           ON execution_sop_applications
           (company_id,version_id,baseline_id,kpi,result_value,result_source_ref,
            result_observed_at)
           WHERE application_ref IS NOT NULL"""
    )
    db.execute("""CREATE TABLE IF NOT EXISTS execution_reminders (
        reminder_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        owner_id TEXT NOT NULL,
        recipient_account_id TEXT REFERENCES user_accounts(account_id),
        entity_type TEXT NOT NULL CHECK (entity_type IN ('task','risk')),
        entity_id TEXT NOT NULL,
        reminder_kind TEXT NOT NULL CHECK (reminder_kind IN ('upcoming','overdue')),
        due_date DATE NOT NULL,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'unread'
          CHECK (status IN ('unread','read','dismissed')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        read_at TIMESTAMPTZ,
        dismissed_at TIMESTAMPTZ,
        UNIQUE(company_id,entity_type,entity_id,reminder_kind,due_date)
    )""")
    reminder_columns = {
        row[0] for row in db.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name='execution_reminders'"""
        ).fetchall()
    }
    if "recipient_account_id" not in reminder_columns:
        db.execute(
            """ALTER TABLE execution_reminders
               ADD COLUMN recipient_account_id TEXT REFERENCES user_accounts(account_id)"""
        )
    db.execute("""CREATE INDEX IF NOT EXISTS idx_reminders_owner
                  ON execution_reminders(company_id,owner_id,status,created_at DESC)""")
    db.execute("""CREATE TABLE IF NOT EXISTS execution_reminder_attempts (
        attempt_id TEXT PRIMARY KEY,
        reminder_id TEXT NOT NULL REFERENCES execution_reminders(reminder_id) ON DELETE CASCADE,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        owner_id TEXT NOT NULL,
        channel TEXT NOT NULL CHECK (channel='internal'),
        outcome TEXT NOT NULL CHECK (outcome IN ('delivered','failed')),
        detail TEXT,
        attempted_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS execution_reminder_runs (
        run_id TEXT PRIMARY KEY,
        started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at TIMESTAMPTZ,
        status TEXT NOT NULL CHECK (status IN ('running','completed','failed')),
        scanned_count INTEGER NOT NULL DEFAULT 0,
        created_count INTEGER NOT NULL DEFAULT 0,
        recovered_count INTEGER NOT NULL DEFAULT 0,
        detail TEXT
    )""")
    run_columns = {
        row[0] for row in db.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name='execution_reminder_runs'"""
        ).fetchall()
    }
    if "recovered_count" not in run_columns:
        db.execute(
            """ALTER TABLE execution_reminder_runs
               ADD COLUMN recovered_count INTEGER NOT NULL DEFAULT 0"""
        )
    db.execute("""CREATE TABLE IF NOT EXISTS execution_owner_bindings (
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        owner_id TEXT NOT NULL,
        account_id TEXT NOT NULL REFERENCES user_accounts(account_id),
        source_ref TEXT NOT NULL,
        bound_by TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(company_id,owner_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS sana_scheduler_credentials (
        credential_name TEXT PRIMARY KEY,
        token_hash TEXT NOT NULL,
        active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")


def _validate_evidence(db, company_id, ids):
    ids = list(dict.fromkeys(ids or []))
    if not ids:
        raise ValueError("EVIDENCE_REQUIRED")
    for evidence_id in ids:
        if not db.execute(
            "SELECT 1 FROM evidence WHERE evidence_id=? AND company_id=?",
            (evidence_id, company_id),
        ).fetchone():
            raise ValueError("EVIDENCE_NOT_FOUND_OR_FORBIDDEN")
    return ids


def update_task(db, company_id, task_id, payload, actor_id):
    task = db.execute(
        "SELECT * FROM tasks WHERE task_id=? AND company_id=? FOR UPDATE",
        (task_id, company_id),
    ).fetchone()
    if not task:
        raise LookupError("TASK_NOT_FOUND")
    owner = _text(payload.get("owner_user_id"))
    approver = _text(payload.get("approver_user_id"))
    deadline = _text(payload.get("deadline") or payload.get("due_date"))
    kpi = _text(payload.get("kpi"))
    status = _text(payload.get("status")) or task["status"]
    delay_reason = _text(payload.get("delay_reason"))
    if status not in TASK_STATUSES:
        raise ValueError("TASK_STATUS_INVALID")
    if not all((owner, approver, deadline, kpi)):
        raise ValueError("TASK_RESPONSIBILITY_FIELDS_REQUIRED")
    if status == "متوقفة" and not delay_reason:
        raise ValueError("DELAY_REASON_REQUIRED")
    evidence_ids = payload.get("evidence_ids")
    if evidence_ids is not None:
        evidence_ids = _validate_evidence(db, company_id, evidence_ids)
    else:
        evidence_ids = _loads(task["evidence_ids_json"], [])
    experiment_id = _text(payload.get("experiment_id"))
    if experiment_id and not db.execute(
        "SELECT 1 FROM gos_experiments WHERE experiment_id=? AND company_id=?",
        (experiment_id, company_id),
    ).fetchone():
        raise ValueError("EXPERIMENT_NOT_FOUND_OR_FORBIDDEN")
    changes = {
        "owner_user_id": owner, "approver_user_id": approver,
        "deadline": deadline, "kpi": kpi, "status": status,
        "delay_reason": delay_reason, "experiment_id": experiment_id,
        "evidence_ids": evidence_ids,
    }
    db.execute(
        """UPDATE tasks SET owner_user_id=?,approver_user_id=?,due_date=?,kpi=?,
           status=?,delay_reason=?,experiment_id=?,evidence_ids_json=?,updated_at=now()
           WHERE task_id=? AND company_id=?""",
        (owner, approver, deadline, kpi, status, delay_reason, experiment_id,
         _json(evidence_ids), task_id, company_id),
    )
    db.execute(
        """INSERT INTO execution_task_audit
           (audit_id,task_id,company_id,actor_id,from_status,to_status,
            change_json,source_ref) VALUES (?,?,?,?,?,?,?,?)""",
        (_id("TA"), task_id, company_id, actor_id, task["status"], status,
         _json(changes), _text(payload.get("source_ref")) or "tasks-board"),
    )
    return changes


def decision_execution_loop(db, company_id, decision_id):
    """Return the one canonical decision → task → impact view for every surface."""
    decision = db.execute(
        """SELECT d.*, c.case_title, a.asset_name
           FROM decisions d
           LEFT JOIN cases c ON c.case_id=d.case_id AND c.company_id=d.company_id
           LEFT JOIN assets a ON a.asset_id=d.asset_id AND a.company_id=d.company_id
           WHERE d.decision_id=? AND d.company_id=?""",
        (decision_id, company_id),
    ).fetchone()
    if not decision:
        raise LookupError("DECISION_NOT_FOUND")
    task = db.execute(
        """SELECT * FROM tasks WHERE decision_id=? AND company_id=?
           ORDER BY created_at ASC,task_id ASC LIMIT 1""",
        (decision_id, company_id),
    ).fetchone()
    review = None
    if task:
        review = db.execute(
            """SELECT * FROM p0_impact_reviews
               WHERE company_id=? AND decision_id=? AND task_id=?""",
            (company_id, decision_id, task["task_id"]),
        ).fetchone()
    impact = dict(review) if review else {
        "status": "WAITING_FOR_MEASUREMENT",
        "status_label": "بانتظار القياس",
        "baseline_value": None,
        "baseline_source_ref": None,
        "baseline_observed_at": None,
        "target_value": None,
        "target_source_ref": None,
        "target_observed_at": None,
        "actual_value": None,
        "actual_source_ref": None,
        "actual_observed_at": None,
        "impact_outcome": None,
    }
    if review:
        impact["status"] = "MEASURED"
        impact["status_label"] = "تم القياس"
    return {
        "decision": dict(decision),
        "task": dict(task) if task else None,
        "impact_review": impact,
    }

def approve_decision_with_task(
    db,
    company_id,
    decision_id,
    *,
    owner_name,
    due_date,
    success_metric,
    next_action,
    approver_user_id,
    scan_id=None,
    evidence_ids_json=None,
):
    """اعتماد القرار وربطه بمهمة تنفيذية داخل المعاملة نفسها.

    لا تُنفّذ هذه الدالة commit؛ يستدعيها مسار HTTP ثم يعتمد المعاملة كاملة.
    قفل القرار يمنع طلبَي اعتماد متزامنين من إنشاء مهمتين، كما أن إعادة
    المحاولة تعيد استخدام المهمة المرتبطة وتحدّث حقول مسؤوليتها بدل إنشاء سجل
    تنفيذ مكرر.
    """
    required = {
        "owner_name": owner_name,
        "due_date": due_date,
        "success_metric": success_metric,
        "next_action": next_action,
        "approver_user_id": approver_user_id,
    }
    if not all(str(value or "").strip() for value in required.values()):
        raise ValueError("DECISION_RESPONSIBILITY_FIELDS_REQUIRED")

    decision = db.execute(
        "SELECT * FROM decisions WHERE decision_id=? AND company_id=? FOR UPDATE",
        (decision_id, company_id),
    ).fetchone()
    if not decision:
        raise LookupError("DECISION_NOT_FOUND")

    existing_task = db.execute(
        """SELECT * FROM tasks
           WHERE decision_id=? AND company_id=?
           ORDER BY created_at ASC, task_id ASC
           LIMIT 1
           FOR UPDATE""",
        (decision_id, company_id),
    ).fetchone()

    if existing_task:
        db.execute(
            """UPDATE tasks
               SET title=?, owner_user_id=?, approver_user_id=?, due_date=?, kpi=?,
                   updated_at=now()
               WHERE task_id=? AND company_id=?""",
            (
                next_action,
                owner_name,
                approver_user_id,
                due_date,
                success_metric,
                existing_task["task_id"],
                company_id,
            ),
        )
        task_id = existing_task["task_id"]
    else:
        task_id = f"TSK-{decision_id}"
        db.execute(
            """INSERT INTO tasks
               (task_id, company_id, decision_id, title, status, priority,
                owner_user_id, approver_user_id, due_date, kpi)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id,
                company_id,
                decision_id,
                next_action,
                "لم تبدأ",
                "عالية",
                owner_name,
                approver_user_id,
                due_date,
                success_metric,
            ),
        )

    db.execute(
        """UPDATE decisions
           SET status='معتمد', owner_name=?, due_date=?, success_metric=?,
               scan_id=COALESCE(?,scan_id),
               evidence_ids=COALESCE(?,evidence_ids)
           WHERE decision_id=? AND company_id=?""",
        (
            owner_name,
            due_date,
            success_metric,
            scan_id,
            evidence_ids_json,
            decision_id,
            company_id,
        ),
    )
    from sana_company_memory import record_memory
    record_memory(
        db, company_id, memory_key=f"decision:{decision_id}",
        memory_type="decision",
        value={
            "decision_id": decision_id,
            "title": decision["title"],
            "next_action": next_action,
            "success_metric": success_metric,
            "owner": owner_name,
            "due_date": due_date,
            "status": "معتمد",
        },
        source_ref=f"decision:{decision_id}", source_type="decision",
        observed_at=date.today(), case_id=decision["case_id"],
        decision_id=decision_id, owner_id=owner_name,
        verification_status="VERIFIED", freshness_class="SLOW",
        source_strength=80, verification_confidence=85, freshness_confidence=80,
        context={"kpi": success_metric},
        reason="قرار معتمد داخل غرفة القرار.",
    )
    record_memory(
        db, company_id, memory_key=f"execution:{task_id}",
        memory_type="execution",
        value={
            "task_id": task_id, "next_action": next_action, "owner": owner_name,
            "approver": approver_user_id, "due_date": due_date,
            "kpi": success_metric, "status": "لم تبدأ",
        },
        source_ref=f"task:{task_id}", source_type="task",
        observed_at=date.today(), case_id=decision["case_id"],
        decision_id=decision_id, task_id=task_id, owner_id=owner_name,
        verification_status="VERIFIED", freshness_class="FAST",
        source_strength=80, verification_confidence=85, freshness_confidence=90,
        context={"kpi": success_metric},
        reason="مهمة التنفيذ المنشأة ذريًا مع اعتماد القرار.",
    )
    return {"decision_id": decision_id, "task_id": task_id, "status": "معتمد"}
def create_risk(db, company_id, payload):
    evidence_ids = _validate_evidence(db, company_id, payload.get("evidence_ids"))
    risk_id = _id("RSK")
    values = (
        _text(payload.get("title"), "TITLE"),
        _text(payload.get("description"), "DESCRIPTION"),
        _json(evidence_ids),
        _text(payload.get("impact"), "IMPACT"),
        _text(payload.get("owner_id"), "OWNER"),
        _int(payload.get("probability"), "PROBABILITY", 1, 5),
        _int(payload.get("severity"), "SEVERITY", 1, 5),
        _text(payload.get("mitigation_plan"), "MITIGATION_PLAN"),
        _text(payload.get("status")) or "open",
        _text(payload.get("source_ref"), "SOURCE_REF"),
        _text(payload.get("review_due_at")),
    )
    if values[8] not in RISK_STATUSES:
        raise ValueError("RISK_STATUS_INVALID")
    db.execute(
        """INSERT INTO execution_risks
           (risk_id,company_id,title,description,evidence_ids_json,impact,owner_id,
            probability,severity,mitigation_plan,status,source_ref,review_due_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (risk_id, company_id, *values),
    )
    return dict(db.execute(
        "SELECT * FROM execution_risks WHERE risk_id=?", (risk_id,)
    ).fetchone())


def list_risks(db, company_id):
    return [dict(row) for row in db.execute(
        """SELECT *,probability*severity AS risk_score FROM execution_risks
           WHERE company_id=? ORDER BY
           CASE WHEN status IN ('open','mitigating') THEN 0 ELSE 1 END,
           probability*severity DESC,created_at DESC""",
        (company_id,),
    ).fetchall()]


def backlog_score(payload):
    impact = _int(payload.get("expected_impact"), "EXPECTED_IMPACT", 1, 5)
    ease = _int(payload.get("ease"), "EASE", 1, 5)
    speed = _int(payload.get("speed"), "SPEED", 1, 5)
    evidence = _int(payload.get("evidence_strength"), "EVIDENCE_STRENGTH", 1, 5)
    profit = _int(payload.get("profitability"), "PROFITABILITY", 1, 5)
    risk = _int(payload.get("risk"), "RISK", 1, 5)
    score = round((impact * 2 + ease + speed + evidence * 2 + profit * 2 - risk) / 9, 2)
    explanation = (
        f"({impact}×2 أثر + {ease} سهولة + {speed} سرعة + {evidence}×2 دليل "
        f"+ {profit}×2 ربحية − {risk} مخاطر) ÷ 9 = {score}"
    )
    return score, explanation, (impact, ease, speed, evidence, profit, risk)


def create_backlog(db, company_id, payload):
    score, explanation, values = backlog_score(payload)
    evidence_ids = payload.get("evidence_ids") or []
    if evidence_ids:
        evidence_ids = _validate_evidence(db, company_id, evidence_ids)
    backlog_id = _id("BLG")
    db.execute(
        """INSERT INTO execution_backlog
           (backlog_id,company_id,title,description,expected_impact,ease,speed,
            evidence_strength,profitability,risk,score,score_explanation,
            evidence_ids_json,status,owner_id,source_ref)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (backlog_id, company_id, _text(payload.get("title"), "TITLE"),
         _text(payload.get("description"), "DESCRIPTION"), *values, score,
         explanation, _json(evidence_ids), "backlog",
         _text(payload.get("owner_id")),
         _text(payload.get("source_ref"), "SOURCE_REF")),
    )
    return dict(db.execute(
        "SELECT * FROM execution_backlog WHERE backlog_id=?", (backlog_id,)
    ).fetchone())


def decide_backlog(db, company_id, backlog_id, payload, actor_id):
    item = db.execute(
        "SELECT * FROM execution_backlog WHERE backlog_id=? AND company_id=? FOR UPDATE",
        (backlog_id, company_id),
    ).fetchone()
    if not item:
        raise LookupError("BACKLOG_NOT_FOUND")
    status = _text(payload.get("status"), "STATUS")
    reason = _text(payload.get("reason"), "REASON")
    if status not in {"approved", "deferred", "stopped"}:
        raise ValueError("BACKLOG_DECISION_INVALID")
    db.execute(
        """UPDATE execution_backlog SET status=?,decision_reason=?,updated_at=now()
           WHERE backlog_id=? AND company_id=?""",
        (status, reason, backlog_id, company_id),
    )
    db.execute(
        """INSERT INTO execution_backlog_audit
           (audit_id,backlog_id,company_id,actor_id,from_status,to_status,reason)
           VALUES (?,?,?,?,?,?,?)""",
        (_id("BLA"), backlog_id, company_id, actor_id,
         item["status"], status, reason),
    )
    return {"backlog_id": backlog_id, "status": status}


def list_backlog(db, company_id):
    return [dict(row) for row in db.execute(
        """SELECT * FROM execution_backlog WHERE company_id=?
           ORDER BY CASE status WHEN 'approved' THEN 0 WHEN 'backlog' THEN 1
           WHEN 'deferred' THEN 2 ELSE 3 END,score DESC""",
        (company_id,),
    ).fetchall()]


def create_sop(db, company_id, payload, actor_id):
    evidence_ids = _validate_evidence(db, company_id, payload.get("evidence_ids"))
    steps = payload.get("steps") or []
    checklist = payload.get("checklist") or []
    if not isinstance(steps, list) or not steps or not isinstance(checklist, list) or not checklist:
        raise ValueError("SOP_STEPS_AND_CHECKLIST_REQUIRED")
    repetitions = _int(payload.get("repetitions", 0), "REPETITIONS", 0, 100000)
    experiment_id = _text(payload.get("experiment_id"))
    result = payload.get("result")
    if not isinstance(result, dict) or not result.get("source_ref"):
        raise ValueError("SOP_RESULT_WITH_SOURCE_REQUIRED")
    if experiment_id:
        experiment = db.execute(
            """SELECT e.status,p.manual_proven,p.repetitions
               FROM gos_experiments e LEFT JOIN gos_experiment_process p
                 ON p.experiment_id=e.experiment_id
               WHERE e.experiment_id=? AND e.company_id=?""",
            (experiment_id, company_id),
        ).fetchone()
        if not experiment:
            raise ValueError("EXPERIMENT_NOT_FOUND_OR_FORBIDDEN")
        if experiment["status"] != "closed":
            raise ValueError("SOP_GATE_EXPERIMENT_NOT_CLOSED")
        repetitions = max(repetitions, experiment["repetitions"] or 0)
        if not experiment["manual_proven"]:
            raise ValueError("SOP_GATE_MANUAL_NOT_PROVEN")
    maturity = _text(payload.get("maturity")) or "Manual"
    if maturity not in SOP_MATURITY:
        raise ValueError("SOP_MATURITY_INVALID")
    if maturity == "Automatable":
        raise ValueError("SOP_GATE_AUTOMATABLE_REQUIRES_RECORDED_APPLICATIONS")
    approved = bool(payload.get("approve"))
    if approved and maturity == "Standardized" and repetitions < 2:
        raise ValueError("SOP_GATE_REQUIRES_TWO_SUCCESSES")
    if maturity in {"Standardized", "Automatable"} and not approved:
        raise ValueError("SOP_GATE_APPROVAL_REQUIRED")
    sop_id = _text(payload.get("sop_id")) or _id("SOP")
    title = _text(payload.get("title"), "TITLE")
    owner = _text(payload.get("owner_role"), "OWNER_ROLE")
    source_ref = _text(payload.get("source_ref"), "SOURCE_REF")
    existing = db.execute(
        "SELECT * FROM execution_sops WHERE sop_id=? AND company_id=?",
        (sop_id, company_id),
    ).fetchone()
    if not existing:
        db.execute(
            """INSERT INTO execution_sops
               (sop_id,company_id,title,owner_role,maturity,experiment_id,source_ref)
               VALUES (?,?,?,?,?,?,?)""",
            (sop_id, company_id, title, owner, maturity, experiment_id, source_ref),
        )
        version_number = 1
    else:
        version_number = db.execute(
            "SELECT COALESCE(MAX(version_number),0)+1 AS n FROM execution_sop_versions WHERE sop_id=?",
            (sop_id,),
        ).fetchone()["n"]
    content = {
        "steps": steps, "checklist": checklist,
        "sla": _text(payload.get("sla"), "SLA"),
        "kpi": _text(payload.get("kpi"), "KPI"),
        "template": _text(payload.get("reusable_template"), "REUSABLE_TEMPLATE"),
        "evidence_ids": evidence_ids, "result": result,
    }
    digest = hashlib.sha256(
        _json(content).encode("utf-8")
    ).hexdigest()
    version_id = _id("SOPV")
    status = "approved" if approved else "draft"
    db.execute(
        """INSERT INTO execution_sop_versions
           (version_id,sop_id,company_id,version_number,status,steps_json,
            checklist_json,sla,kpi,reusable_template,evidence_ids_json,result_json,
            repetitions,source_ref,content_hash,approved_by,approved_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CASE WHEN ? THEN now() ELSE NULL END)""",
        (version_id, sop_id, company_id, version_number, status, _json(steps),
         _json(checklist), content["sla"], content["kpi"], content["template"],
         _json(evidence_ids), _json(result), repetitions, source_ref, digest,
         actor_id if approved else None, approved),
    )
    if approved:
        db.execute(
            """UPDATE execution_sop_versions SET status='deprecated'
               WHERE sop_id=? AND version_id<>? AND status='approved'""",
            (sop_id, version_id),
        )
        db.execute(
            """UPDATE execution_sops SET active_version_id=?,maturity=?,title=?,
               owner_role=?,experiment_id=?,source_ref=?,updated_at=now()
               WHERE sop_id=? AND company_id=?""",
            (version_id, maturity, title, owner, experiment_id, source_ref,
             sop_id, company_id),
        )
    return {"sop_id": sop_id, "version_id": version_id, "status": status,
            "maturity": maturity}

def _number(value, field):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_INVALID") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise ValueError(f"{field}_INVALID")
    return result
def list_sops(db, company_id):
    rows = db.execute(
        """SELECT s.*,v.version_number,v.status AS version_status,v.steps_json,
           v.checklist_json,v.sla,v.kpi,v.reusable_template,v.evidence_ids_json,
           v.result_json,v.repetitions
           FROM execution_sops s LEFT JOIN execution_sop_versions v
             ON v.version_id=s.active_version_id
           WHERE s.company_id=? ORDER BY s.updated_at DESC""",
        (company_id,),
    ).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        if data.get("active_version_id"):
            data["automation_readiness"] = sop_automation_readiness(
                db, company_id, data["sop_id"], data["active_version_id"]
            )
        else:
            data["automation_readiness"] = None
        result.append(data)
    return result


def _due_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _recipient_account(db, company_id, owner_id):
    exact = db.execute(
        """SELECT account_id FROM user_accounts
           WHERE company_id=? AND account_id=?""",
        (company_id, owner_id),
    ).fetchone()
    if exact:
        return exact["account_id"], "owner_matches_account"
    binding = db.execute(
        """SELECT b.account_id FROM execution_owner_bindings b
           JOIN user_accounts a ON a.account_id=b.account_id
           WHERE b.company_id=? AND b.owner_id=? AND a.company_id=b.company_id""",
        (company_id, owner_id),
    ).fetchone()
    if binding:
        return binding["account_id"], "explicit_owner_binding"
    accounts = db.execute(
        "SELECT account_id FROM user_accounts WHERE company_id=? ORDER BY account_id",
        (company_id,),
    ).fetchall()
    if len(accounts) == 1:
        return accounts[0]["account_id"], "sole_company_account"
    if not accounts:
        return None, "no_company_account"
    return None, "ambiguous_owner_account"


def bind_owner_account(db, company_id, owner_id, account_id, actor_id,
                       source_ref="owner-assignment"):
    owner_id = _text(owner_id, "OWNER_ID")
    account_id = _text(account_id, "ACCOUNT_ID")
    account = db.execute(
        "SELECT account_id FROM user_accounts WHERE account_id=? AND company_id=?",
        (account_id, company_id),
    ).fetchone()
    if not account:
        raise ValueError("ACCOUNT_NOT_FOUND_OR_FORBIDDEN")
    owner_exists = db.execute(
        """SELECT 1 FROM tasks WHERE company_id=? AND owner_user_id=?
           UNION ALL
           SELECT 1 FROM execution_risks WHERE company_id=? AND owner_id=?
           LIMIT 1""",
        (company_id, owner_id, company_id, owner_id),
    ).fetchone()
    if not owner_exists:
        raise ValueError("OWNER_NOT_FOUND_IN_COMPANY")
    db.execute(
        """INSERT INTO execution_owner_bindings
           (company_id,owner_id,account_id,source_ref,bound_by)
           VALUES (?,?,?,?,?)
           ON CONFLICT(company_id,owner_id) DO UPDATE SET
             account_id=EXCLUDED.account_id,source_ref=EXCLUDED.source_ref,
             bound_by=EXCLUDED.bound_by,updated_at=now()""",
        (company_id, owner_id, account_id,
         _text(source_ref) or "owner-assignment", actor_id),
    )
    return {"company_id": company_id, "owner_id": owner_id,
            "account_id": account_id}


def list_owner_bindings(db, company_id):
    return [dict(row) for row in db.execute(
        """SELECT b.*,a.email FROM execution_owner_bindings b
           JOIN user_accounts a ON a.account_id=b.account_id
           WHERE b.company_id=? AND a.company_id=?
           ORDER BY b.owner_id""",
        (company_id, company_id),
    ).fetchall()]


def _create_reminder(db, row, entity_type, today):
    due = _due_date(row["due_date"])
    kind = "overdue" if due < today else "upcoming"
    entity_label = "المهمة" if entity_type == "task" else "مراجعة الخطر"
    timing = "تجاوزت موعدها" if kind == "overdue" else "اقترب موعدها"
    title = f"{entity_label} {timing}: {row['title']}"
    message = (
        f"{entity_label} مستحقة في {due.isoformat()}. راجعها قبل أن تتحول إلى تأخير. "
        "هذا التنبيه لا يغيّر الحالة أو الأولوية."
    )
    reminder_id = _id("REM")
    recipient_account_id, resolution = _recipient_account(
        db, row["company_id"], row["owner_id"]
    )
    cur = db.execute(
        """INSERT INTO execution_reminders
           (reminder_id,company_id,owner_id,recipient_account_id,entity_type,
            entity_id,reminder_kind,due_date,title,message)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT (company_id,entity_type,entity_id,reminder_kind,due_date)
           DO NOTHING""",
        (reminder_id, row["company_id"], row["owner_id"], recipient_account_id,
         entity_type,
         row["entity_id"], kind, due.isoformat(), title, message),
    )
    if cur.rowcount == 0:
        existing = db.execute(
            """SELECT * FROM execution_reminders
               WHERE company_id=? AND entity_type=? AND entity_id=?
                 AND reminder_kind=? AND due_date=? FOR UPDATE""",
            (row["company_id"], entity_type, row["entity_id"], kind,
             due.isoformat()),
        ).fetchone()
        if not existing:
            return False
        recipient_account_id, resolution = _recipient_account(
            db, row["company_id"], row["owner_id"]
        )
        owner_changed = existing["owner_id"] != row["owner_id"]
        recipient_changed = (
            existing["recipient_account_id"] != recipient_account_id
        )
        if recipient_changed or owner_changed:
            db.execute(
                """UPDATE execution_reminders SET owner_id=?,
                   recipient_account_id=?,status='unread',read_at=NULL,
                   dismissed_at=NULL WHERE reminder_id=?""",
                (row["owner_id"], recipient_account_id,
                 existing["reminder_id"]),
            )
            db.execute(
                """INSERT INTO execution_reminder_attempts
                   (attempt_id,reminder_id,company_id,owner_id,channel,
                    outcome,detail) VALUES (?,?,?,?,?,?,?)""",
                (_id("RMA"), existing["reminder_id"], row["company_id"],
                 row["owner_id"], "internal",
                 "delivered" if recipient_account_id else "failed",
                 ("أعيد توجيه التنبيه إلى حساب المستلم"
                  if recipient_account_id
                  else f"أُلغي المستلم السابق وتعذر إعادة التوجيه: {resolution}")),
            )
            return "recovered" if recipient_account_id else None
        if not recipient_account_id:
            recent = db.execute(
                """SELECT 1 FROM execution_reminder_attempts
                   WHERE reminder_id=? AND outcome='failed'
                     AND attempted_at>now()-INTERVAL '1 hour' LIMIT 1""",
                (existing["reminder_id"],),
            ).fetchone()
            if not recent:
                db.execute(
                    """INSERT INTO execution_reminder_attempts
                       (attempt_id,reminder_id,company_id,owner_id,channel,
                        outcome,detail) VALUES (?,?,?,?,?,?,?)""",
                    (_id("RMA"), existing["reminder_id"], row["company_id"],
                     row["owner_id"], "internal", "failed",
                     f"تعذر تحديد حساب المستلم: {resolution}"),
                )
        return None
    db.execute(
        """INSERT INTO execution_reminder_attempts
           (attempt_id,reminder_id,company_id,owner_id,channel,outcome,detail)
           VALUES (?,?,?,?,?,?,?)""",
        (_id("RMA"), reminder_id, row["company_id"], row["owner_id"],
         "internal", "delivered" if recipient_account_id else "failed",
         ("أضيف إلى صندوق حساب الشركة الداخلي"
          if recipient_account_id else f"تعذر تحديد حساب المستلم: {resolution}")),
    )
    return "created"


def materialize_due_reminders(db, as_of=None, company_id=None, acquire_lock=True):
    """ينشئ تنبيهات داخلية قبل 3 أيام أو عند التأخير، دون تعديل السجل المصدر."""
    today = _due_date(as_of or date.today())
    horizon = today + timedelta(days=3)
    if acquire_lock:
        locked = db.execute(
            "SELECT pg_try_advisory_xact_lock(?) AS locked", (20260920,)
        ).fetchone()["locked"]
        if not locked:
            return {"run_id": None, "scanned": 0, "created": 0,
                    "status": "skipped_locked"}
    run_id = _id("RMR")
    db.execute(
        "INSERT INTO execution_reminder_runs(run_id,status) VALUES (?,?)",
        (run_id, "running"),
    )
    scanned = created = recovered = 0
    db.execute("SAVEPOINT reminder_materialization")
    try:
        company_clause = " AND company_id=?" if company_id else ""
        params = (horizon.isoformat(), company_id) if company_id else (horizon.isoformat(),)
        tasks = db.execute(
            f"""SELECT task_id AS entity_id,company_id,owner_user_id AS owner_id,
                      title,due_date
               FROM tasks
               WHERE status IN ('لم تبدأ','قيد التنفيذ','متوقفة')
                 AND owner_user_id IS NOT NULL AND due_date IS NOT NULL
                 AND due_date<=?{company_clause}""",
            params,
        ).fetchall()
        risks = db.execute(
            f"""SELECT risk_id AS entity_id,company_id,owner_id,title,
                      review_due_at AS due_date
               FROM execution_risks
               WHERE status IN ('open','mitigating') AND review_due_at IS NOT NULL
                 AND review_due_at<=?{company_clause}""",
            params,
        ).fetchall()
        for row, entity_type in (
            *((row, "task") for row in tasks),
            *((row, "risk") for row in risks),
        ):
            scanned += 1
            outcome = _create_reminder(db, row, entity_type, today)
            created += int(outcome == "created")
            recovered += int(outcome == "recovered")
        db.execute(
            """UPDATE execution_reminder_runs SET status='completed',
               scanned_count=?,created_count=?,recovered_count=?,finished_at=now()
               WHERE run_id=?""",
            (scanned, created, recovered, run_id),
        )
        db.execute("RELEASE SAVEPOINT reminder_materialization")
        return {"run_id": run_id, "scanned": scanned, "created": created,
                "recovered": recovered,
                "status": "completed"}
    except Exception as exc:
        db.execute("ROLLBACK TO SAVEPOINT reminder_materialization")
        db.execute(
            """UPDATE execution_reminder_runs SET status='failed',
               scanned_count=?,created_count=?,recovered_count=?,detail=?,finished_at=now()
               WHERE run_id=?""",
            (scanned, created, recovered, str(exc)[:500], run_id),
        )
        db.execute("RELEASE SAVEPOINT reminder_materialization")
        return {"run_id": run_id, "scanned": scanned, "created": created,
                "recovered": recovered,
                "status": "failed", "detail": str(exc)[:500]}


def list_reminders(db, company_id, owner_id=None, recipient_account_id=None,
                   include_dismissed=False):
    params = [company_id]
    clauses = ["company_id=?"]
    if owner_id:
        clauses.append("owner_id=?")
        params.append(owner_id)
    if recipient_account_id:
        clauses.append("recipient_account_id=?")
        params.append(recipient_account_id)
    if not include_dismissed:
        clauses.append("status<>'dismissed'")
    rows = db.execute(
        f"""SELECT * FROM execution_reminders WHERE {' AND '.join(clauses)}
            ORDER BY CASE status WHEN 'unread' THEN 0 ELSE 1 END,
                     CASE reminder_kind WHEN 'overdue' THEN 0 ELSE 1 END,
                     due_date ASC,created_at DESC""",
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def update_reminder_status(db, company_id, reminder_id, status, actor_id,
                           admin_preview=False):
    if status not in {"read", "dismissed"}:
        raise ValueError("REMINDER_STATUS_INVALID")
    row = db.execute(
        "SELECT * FROM execution_reminders WHERE reminder_id=? AND company_id=?",
        (reminder_id, company_id),
    ).fetchone()
    if not row:
        raise LookupError("REMINDER_NOT_FOUND")
    if not admin_preview and row["recipient_account_id"] != actor_id:
        raise PermissionError("REMINDER_OWNER_FORBIDDEN")
    timestamp = "read_at" if status == "read" else "dismissed_at"
    db.execute(
        f"""UPDATE execution_reminders SET status=?,{timestamp}=now()
            WHERE reminder_id=? AND company_id=?""",
        (status, reminder_id, company_id),
    )
    return {"reminder_id": reminder_id, "status": status}


def run_reminder_cycle(connect_db):
    """نفّذ دورة واحدة بعد ضمان وجود مخطط غرفة القرار."""
    db = connect_db()
    try:
        ensure_schema(db)
        result = materialize_due_reminders(db, acquire_lock=True)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def start_reminder_scheduler(connect_db, interval_seconds=900):
    """مجدول خفيف؛ منع التكرار في قاعدة البيانات، وكل دورة تفتح اتصالها."""
    def loop():
        while True:
            try:
                run_reminder_cycle(connect_db)
            except Exception as exc:
                print(f"[execution-reminders] failed: {exc}")
            time.sleep(interval_seconds)

    thread = threading.Thread(
        target=loop, name="sana-execution-reminders", daemon=True
    )
    thread.start()
    return thread


def decision_room(db, company_id):
    ensure_schema(db)
    company = db.execute(
        "SELECT * FROM companies WHERE company_id=?", (company_id,)
    ).fetchone()
    if not company:
        raise LookupError("COMPANY_NOT_FOUND")
    from sana_growth_os import get_company_profile
    profile = get_company_profile(db, company_id)
    primary_case = db.execute(
        """SELECT case_id,case_title,confidence_score,declared_problem,real_question
           FROM cases WHERE company_id=?
           ORDER BY opened_at DESC NULLS LAST LIMIT 1""",
        (company_id,),
    ).fetchone()
    evidence_count = db.execute(
        "SELECT COUNT(*) AS count FROM evidence WHERE company_id=?",
        (company_id,),
    ).fetchone()["count"]
    baseline = db.execute(
        """SELECT * FROM gos_baselines WHERE company_id=? AND status='complete'
           ORDER BY observed_at DESC,created_at DESC LIMIT 1""",
        (company_id,),
    ).fetchone()
    metrics = []
    if baseline:
        rows = db.execute(
            """SELECT bm.*,md.label_ar FROM gos_baseline_metrics bm
               JOIN gos_metric_definitions md ON md.metric_key=bm.metric_key
               WHERE bm.baseline_id=? AND bm.availability='available'
                 AND bm.classification='Fact'
               ORDER BY bm.metric_key LIMIT 3""",
            (baseline["baseline_id"],),
        ).fetchall()
        metrics = [{
            "key": row["metric_key"], "label": row["label_ar"],
            "value": float(row["value_numeric"]), "unit": row["unit"],
            "source_ref": row["source_ref"],
            "observed_at": row["observed_at"],
            "confidence": row["confidence"],
        } for row in rows]
    missing_metrics = max(0, 3 - len(metrics))

    bottleneck = db.execute(
        """SELECT b.*,c.created_at AS cycle_created_at
           FROM gos_bottlenecks b JOIN gos_bottleneck_cycles c ON c.cycle_id=b.cycle_id
           WHERE b.company_id=? AND b.is_primary=true AND c.status<>'EVIDENCE_GATE'
           ORDER BY c.created_at DESC LIMIT 1""",
        (company_id,),
    ).fetchone()
    opportunity = db.execute(
        """SELECT o.opp_id,o.title,o.revenue_stage_id,e.contract_value,
                  e.source_ref,e.observed_at,e.classification
           FROM opportunities o JOIN rc_opportunity_economics e ON e.opp_id=o.opp_id
           WHERE o.company_id=? AND o.archived=0 AND e.classification='Fact'
             AND e.source_ref IS NOT NULL
           ORDER BY e.contract_value DESC NULLS LAST,o.created_at DESC LIMIT 1""",
        (company_id,),
    ).fetchone()
    experiment = db.execute(
        """SELECT e.*,p.current_stage
           FROM gos_experiments e
           LEFT JOIN gos_experiment_process p ON p.experiment_id=e.experiment_id
           WHERE e.company_id=? AND e.status='running'
           ORDER BY e.started_at DESC NULLS LAST LIMIT 1""",
        (company_id,),
    ).fetchone()
    experiment_decision = db.execute(
        """SELECT d.* FROM gos_experiment_decisions d
           WHERE d.company_id=? AND d.status='proposed'
           ORDER BY d.created_at DESC LIMIT 1""",
        (company_id,),
    ).fetchone()
    case_decision = db.execute(
        """SELECT d.*,c.case_title FROM decisions d
           LEFT JOIN cases c ON c.case_id=d.case_id AND c.company_id=d.company_id
           WHERE d.company_id=? AND d.phase_label='P0' AND d.status='مقترح'
           ORDER BY d.created_at DESC LIMIT 1""",
        (company_id,),
    ).fetchone()
    next_task = db.execute(
        """SELECT * FROM tasks WHERE company_id=? AND status IN ('لم تبدأ','قيد التنفيذ','متوقفة')
           AND BTRIM(COALESCE(owner_user_id,'')) <> ''
           AND BTRIM(COALESCE(approver_user_id,'')) <> ''
           AND BTRIM(COALESCE(due_date,'')) <> ''
           AND BTRIM(COALESCE(kpi,'')) <> ''
           ORDER BY CASE priority WHEN 'عالية' THEN 0 WHEN 'متوسطة' THEN 1 ELSE 2 END,
                    due_date ASC LIMIT 1""",
        (company_id,),
    ).fetchone()
    incomplete_task = db.execute(
        """SELECT * FROM tasks WHERE company_id=? AND status IN ('لم تبدأ','قيد التنفيذ','متوقفة')
           AND (BTRIM(COALESCE(owner_user_id,'')) = ''
                OR BTRIM(COALESCE(approver_user_id,'')) = ''
                OR BTRIM(COALESCE(due_date,'')) = ''
                OR BTRIM(COALESCE(kpi,'')) = '')
           ORDER BY CASE priority WHEN 'عالية' THEN 0 WHEN 'متوسطة' THEN 1 ELSE 2 END,
                    created_at ASC LIMIT 1""",
        (company_id,),
    ).fetchone()
    urgent_risk = db.execute(
        """SELECT *,probability*severity AS risk_score FROM execution_risks
           WHERE company_id=? AND status IN ('open','mitigating')
           ORDER BY probability*severity DESC,created_at DESC LIMIT 1""",
        (company_id,),
    ).fetchone()
    missing_evidence = []
    if not baseline:
        missing_evidence.append({
            "key": "baseline",
            "title": "وثّق خط أساس كامل بمصدر وفترة ملاحظة",
            "reason": "خط الأساس هو أعلى دليل أثرًا لأنه يحدد نقطة المقارنة قبل أي قرار أو تجربة.",
        })
    if missing_metrics:
        missing_evidence.append({
            "key": "metrics",
            "title": f"وثّق {missing_metrics} مؤشرات مصنفة Fact",
            "reason": "المؤشرات الموثقة مطلوبة لقياس أثر القرار بدل الاعتماد على الانطباع.",
        })
    if not bottleneck and not experiment_decision and not experiment:
        missing_evidence.append({
            "key": "bottleneck",
            "title": "اربط الاختناق الرئيسي بدليل قابل للتتبع",
            "reason": "الدليل الحاسم يثبت سبب الأولوية ويمنع اختيار مشكلة عامة.",
        })
    top_evidence_request = missing_evidence[0] if missing_evidence else None

    def today_evidence(label, value=None, source_ref=None, status="available"):
        item = {"label": label, "status": status}
        if value not in (None, ""):
            item["value"] = value
        if source_ref not in (None, ""):
            item["source_ref"] = source_ref
        return item

    def today_action(label, href=None, kind="link", **extra):
        action = {"label": label, "kind": kind}
        if href:
            action["href"] = href
        action.update(extra)
        return action

    # قرار اليوم هو واجهة قيادة واحدة فوق أقسام الغرفة الثمانية.
    # الترتيب مقصود: لا نسمح للتنفيذ أو الاعتماد أن يسبق الدليل.
    if not primary_case and not evidence_count and not baseline:
        today = {
            "status": "new_company",
            "label": "ابدأ من واقع شركتك",
            "title": "لا يوجد قرار اليوم بعد — ابدأ بتشخيص واحد واضح.",
            "reason": "الشركة جديدة في سنع، ولا توجد قضية أو أدلة يمكن أن نبني عليها قرارًا.",
            "evidence": [
                today_evidence("حالة الدليل", "لا توجد أدلة أو Baseline بعد", status="missing")
            ],
            "action": today_action("ابدأ التشخيص", "/case/new"),
        }
    elif missing_evidence:
        target = (
            f"/case/{primary_case['case_id']}"
            if primary_case else "/case/new"
        )
        today = {
            "status": "evidence_needed",
            "label": "الدليل أولًا",
            "title": top_evidence_request["title"],
            "reason": top_evidence_request["reason"],
            "evidence": [
                today_evidence(
                    "طلب الدليل الأعلى أثرًا",
                    top_evidence_request["title"],
                    status="missing",
                )
            ],
            "action": today_action("وثّق هذا الدليل", target),
            "evidence_request": dict(top_evidence_request),
        }
    elif case_decision:
        try:
            evidence_ids = _loads(case_decision["evidence_ids"], [])
        except Exception:
            evidence_ids = []
        today = {
            "status": "decision_pending",
            "label": "قرار اليوم",
            "title": case_decision["title"],
            "reason": case_decision["reason"],
            "expected_impact": case_decision["expected_impact"],
            "decision_id": case_decision["decision_id"],
            "case_id": case_decision["case_id"],
            "evidence": [
                today_evidence("القضية", case_decision["case_title"] or case_decision["case_id"]),
                today_evidence("الأدلة المعتمدة", f"{len(evidence_ids)} دليل", case_decision["scan_id"]),
                today_evidence("الأثر المتوقع", case_decision["expected_impact"] or "غير موثق"),
            ],
            "action": today_action(
                "راجع القرار واعتمده",
                f"/case/{case_decision['case_id']}",
                kind="link",
                decision_id=case_decision["decision_id"],
            ),
        }
    elif experiment_decision:
        decision_evidence_ids = _loads(
            experiment_decision["evidence_ids_json"], []
        )
        today = {
            "status": "decision_pending",
            "label": "قرار اليوم",
            "title": f"راجع قرار {experiment_decision['action']} واعتمده.",
            "reason": experiment_decision["reason"],
            "evidence": [
                today_evidence(
                    "مصدر القرار",
                    f"أدلة مرتبطة: {', '.join(map(str, decision_evidence_ids)) or 'غير متاح'}",
                    experiment_decision["source_ref"],
                ),
                today_evidence("نتيجة التجربة", "مغلقة — القرار بانتظار الاعتماد"),
            ],
            "action": today_action(
                "راجع واعتمد القرار",
                "/growth-os",
                kind="approve_decision",
                decision_id=experiment_decision["decision_id"],
            ),
        }
    elif experiment:
        today = {
            "status": "measure",
            "label": "القياس الآن",
            "title": "التجربة جارية — سجّل النتيجة قبل أي قرار جديد.",
            "reason": "لا ينتقل سنع إلى Scale أو Modify أو Hold أو Kill قبل نتيجة قابلة للتتبع.",
            "evidence": [
                today_evidence(
                    "التجربة",
                    experiment["experiment_id"],
                    experiment["source_ref"],
                ),
                today_evidence("المرحلة الحالية", experiment["current_stage"] or "Measure"),
            ],
            "action": today_action("سجّل نتيجة التجربة", "/growth-os"),
        }
    elif incomplete_task:
        incomplete_task_data = dict(incomplete_task)
        missing_task_fields = _task_missing_fields(incomplete_task_data)
        incomplete_task_data["missing_fields"] = missing_task_fields
        today = {
            "status": "execution_blocked",
            "label": "أكمل جاهزية التنفيذ",
            "title": "أكمل بيانات المهمة قبل بدء التنفيذ.",
            "reason": (
                "لا تُعد المهمة جاهزة حتى تكتمل الحقول المطلوبة. "
                f"الناقص الآن: {', '.join(missing_task_fields)}."
            ),
            "evidence": [
                today_evidence("المهمة", incomplete_task_data["title"], "سجل المهام"),
                today_evidence(
                    "الناقص", "، ".join(missing_task_fields), status="missing"
                ),
            ],
            "action": today_action(
                "أكمل بيانات المهمة",
                f"/company/{company_id}/tasks-board",
            ),
            "task": incomplete_task_data,
        }
    elif next_task:
        today = {
            "status": "execute",
            "label": "خطوة التنفيذ",
            "title": "نفّذ المهمة الأولى ثم سجّل قياسها.",
            "reason": "القرار قابل للتنفيذ، والمهمة التالية مكتملة المسؤولية والموعد ومؤشر القياس.",
            "evidence": [
                today_evidence(
                    "المهمة الأولى",
                    next_task["title"],
                    next_task["evidence_ids_json"] or "سجل المهمة",
                ),
                today_evidence(
                    "القياس والموعد",
                    f"{next_task['kpi']} · {next_task['due_date']}",
                ),
            ],
            "action": today_action(
                "افتح المهمة الأولى",
                f"/company/{company_id}/tasks-board",
            ),
            "task": dict(next_task),
        }
    elif urgent_risk:
        today = {
            "status": "risk_review",
            "label": "مراجعة التنفيذ",
            "title": "راجع الخطر المفتوح قبل بدء خطوة جديدة.",
            "reason": "يوجد خطر مفتوح ذو أولوية يحتاج خطة تخفيف موثقة.",
            "evidence": [
                today_evidence(
                    "الخطر",
                    urgent_risk["title"],
                    urgent_risk["source_ref"],
                ),
                today_evidence(
                    "الأثر والاحتمال",
                    f"{urgent_risk['impact']} · {urgent_risk['probability']}/5",
                ),
            ],
            "action": today_action(
                "راجع خطر التنفيذ",
                f"/company/{company_id}/tasks-board",
            ),
        }
    else:
        today = {
            "status": "monitor",
            "label": "المتابعة",
            "title": "لا توجد خطوة معلّقة — راقب القياس التالي.",
            "reason": "لا يوجد قرار أو تنفيذ أو خطر يحتاج إجراءً فوريًا في السجلات الحالية.",
            "evidence": [
                today_evidence(
                    "آخر حالة موثقة",
                    f"{len(metrics)} مؤشرات متاحة",
                    baseline["source_ref"] if baseline else None,
                )
            ],
            "action": today_action("راجع الأثر الموثق", "/passport"),
        }
    state = {
        "company": company["name"],
        "profile_key": profile["profile_key"],
        "profile_label": profile["label_ar"],
        "baseline_status": "complete" if baseline else "missing",
        "baseline_period": (
            {"start": baseline["period_start"], "end": baseline["period_end"]}
            if baseline else None
        ),
    }
    return {
        "section_order": [
            "current_state", "top_metrics", "bottleneck", "opportunity",
            "running_experiment", "decision_today", "next_task", "urgent_risk",
        ],
        "current_state": state,
        "today": today,
        "top_evidence_request": (
            {
                **top_evidence_request,
                "status": "required",
                "href": (
                    f"/case/{primary_case['case_id']}"
                    if primary_case else "/case/new"
                ),
            }
            if top_evidence_request else None
        ),
        "top_metrics": metrics,
        "top_metrics_missing": missing_metrics,
        "bottleneck": dict(bottleneck) if bottleneck else {
            "status": "missing",
            "missing": "يلزم Baseline كامل ودليل مصنف قبل تحديد الاختناق.",
        },
        "opportunity": dict(opportunity) if opportunity else {
            "status": "missing",
            "missing": "لا توجد فرصة تجارية مصنفة Fact بمصدر وفترة ملاحظة.",
        },
        "running_experiment": dict(experiment) if experiment else {
            "status": "missing",
            "missing": "لا توجد تجربة Running مرتبطة بـBaseline قابل للاستخدام.",
        },
        "decision_today": dict(experiment_decision) if experiment_decision else {
            "status": "missing",
            "missing": "لا يوجد قرار تجربة مقترح من Scale/Modify/Hold/Kill.",
        },
        "next_task": dict(next_task) if next_task else {
            "status": "missing",
            "missing": "لا توجد مهمة مكتملة المسؤولية: Owner + Approver + Deadline + KPI.",
        },
        "urgent_risk": dict(urgent_risk) if urgent_risk else {
            "status": "missing",
            "missing": "لا يوجد خطر مفتوح مرتبط بدليل ومالك وخطة تخفيف.",
        },
        "profile": {
            "metrics": profile["metrics"], "stages": profile["stages"],
            "offer_fields": profile["offer_fields"],
        },
        "generated_at": date.today().isoformat(),
    }

def _application_dict(row):
    data = dict(row)
    for field in ("baseline_value", "result_value", "delta"):
        data[field] = float(data[field])
    data["evidence_ids"] = _loads(data.pop("evidence_ids_json"), [])
    data["successful"] = data["comparison_status"] == "improved"
    return data

def _sop_version(db, company_id, sop_id, version_id):
    row = db.execute(
        """SELECT v.*,s.maturity,s.active_version_id
           FROM execution_sop_versions v
           JOIN execution_sops s ON s.sop_id=v.sop_id AND s.company_id=v.company_id
           WHERE v.version_id=? AND v.sop_id=? AND v.company_id=?""",
        (version_id, sop_id, company_id),
    ).fetchone()
    if not row:
        raise LookupError("SOP_VERSION_NOT_FOUND")
    return row

def sop_automation_readiness(db, company_id, sop_id, version_id):
    _sop_version(db, company_id, sop_id, version_id)
    counts = db.execute(
        """SELECT COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE comparison_status='improved') AS successful
           FROM execution_sop_applications
           WHERE company_id=? AND sop_id=? AND version_id=?""",
        (company_id, sop_id, version_id),
    ).fetchone()
    successful = int(counts["successful"] or 0)
    total = int(counts["total"] or 0)
    return {
        "total_applications": total,
        "successful_applications": successful,
        "required_successful_applications": 2,
        "automatable": successful >= 2,
        "gate": None if successful >= 2 else "SOP_GATE_REQUIRES_TWO_RECORDED_SUCCESSES",
    }

def _iso_date(value, field):
    value = _text(value, field)
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field}_INVALID") from exc

def record_sop_application(db, company_id, sop_id, payload, actor_id):
    version_id = _text(payload.get("version_id"), "VERSION_ID")
    version = _sop_version(db, company_id, sop_id, version_id)
    if (
        version["status"] != "approved"
        or version["active_version_id"] != version_id
        or not version["approved_at"]
    ):
        raise ValueError("SOP_APPLICATION_REQUIRES_APPROVED_ACTIVE_VERSION")
    application_ref = _text(
        payload.get("application_ref") or payload.get("run_ref"),
        "APPLICATION_REF",
    )
    if db.execute(
        """SELECT 1 FROM execution_sop_applications
           WHERE company_id=? AND version_id=? AND application_ref=?""",
        (company_id, version_id, application_ref),
    ).fetchone():
        raise ValueError("SOP_APPLICATION_ALREADY_RECORDED")
    kpi = _text(payload.get("kpi") or payload.get("metric_key"), "KPI")
    direction = _text(payload.get("direction"), "DIRECTION")
    if direction not in SOP_KPI_DIRECTIONS:
        raise ValueError("SOP_KPI_DIRECTION_REQUIRED")
    baseline_id = _text(payload.get("baseline_id"), "BASELINE_ID")
    from sana_growth_os import require_usable_baseline
    baseline = require_usable_baseline(db, company_id, baseline_id)
    metric = next((item for item in baseline["metrics"] if item["metric_key"] == kpi), None)
    if (
        not metric
        or metric["availability"] != "available"
        or metric["classification"] != "Fact"
        or not metric["source_ref"]
        or metric["value_numeric"] is None
    ):
        raise ValueError("SOP_BASELINE_KPI_NOT_USABLE")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("SOP_RESULT_REQUIRED")
    result_value = _number(result.get("value"), "RESULT_VALUE")
    result_source = _text(result.get("source_ref"), "RESULT_SOURCE_REF")
    observed_at = _iso_date(result.get("observed_at"), "RESULT_OBSERVED_AT")
    if observed_at <= str(baseline["period_end"])[:10]:
        raise ValueError("SOP_RESULT_MUST_FOLLOW_BASELINE_PERIOD")
    if observed_at < str(version["approved_at"])[:10]:
        raise ValueError("SOP_RESULT_MUST_FOLLOW_VERSION_APPROVAL")
    result_evidence_id = _text(
        payload.get("result_evidence_id") or result.get("evidence_id"),
        "RESULT_EVIDENCE_ID",
    )
    evidence_ids = _validate_evidence(db, company_id, payload.get("evidence_ids"))
    if result_evidence_id not in evidence_ids:
        raise ValueError("SOP_RESULT_EVIDENCE_MUST_BE_LINKED")
    version_evidence_ids = set(_loads(version["evidence_ids_json"], []))
    if result_evidence_id in version_evidence_ids:
        raise ValueError("SOP_RESULT_EVIDENCE_MUST_BE_INDEPENDENT")
    result_evidence = db.execute(
        """SELECT evidence_type,source_ref FROM evidence
           WHERE evidence_id=? AND company_id=?""",
        (result_evidence_id, company_id),
    ).fetchone()
    if (
        not result_evidence
        or result_evidence["evidence_type"] != "Fact"
        or result_evidence["source_ref"] != result_source
    ):
        raise ValueError("SOP_RESULT_EVIDENCE_NOT_VERIFIABLE")
    if db.execute(
        """SELECT 1 FROM execution_sop_applications
           WHERE company_id=? AND result_evidence_id=?""",
        (company_id, result_evidence_id),
    ).fetchone():
        raise ValueError("SOP_RESULT_EVIDENCE_ALREADY_USED")
    if db.execute(
        """SELECT 1 FROM execution_sop_applications
           WHERE company_id=? AND version_id=? AND baseline_id=? AND kpi=?
             AND result_value=? AND result_source_ref=? AND result_observed_at=?""",
        (
            company_id, version_id, baseline_id, kpi, result_value,
            result_source, observed_at,
        ),
    ).fetchone():
        raise ValueError("SOP_APPLICATION_DUPLICATE_OUTCOME")
    baseline_value = float(metric["value_numeric"])
    delta = result_value - baseline_value
    if delta == 0:
        comparison_status = "unchanged"
    elif (
        (direction == "higher_is_better" and delta > 0)
        or (direction == "lower_is_better" and delta < 0)
    ):
        comparison_status = "improved"
    else:
        comparison_status = "not_improved"
    application_id = _id("SOPA")
    db.execute(
        """INSERT INTO execution_sop_applications
           (application_id,company_id,sop_id,version_id,application_ref,kpi,direction,baseline_id,
            baseline_value,baseline_source_ref,result_value,result_source_ref,
            result_observed_at,unit,delta,comparison_status,result_evidence_id,
            evidence_ids_json,source_ref,recorded_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            application_id, company_id, sop_id, version_id, application_ref,
            kpi, direction,
            baseline_id, baseline_value, metric["source_ref"], result_value,
            result_source, observed_at, result.get("unit") or metric["unit"],
            delta, comparison_status, result_evidence_id, _json(evidence_ids),
            _text(payload.get("source_ref")) or result_source, actor_id,
        ),
    )
    readiness = sop_automation_readiness(db, company_id, sop_id, version_id)
    db.execute(
        """UPDATE execution_sop_versions SET repetitions=?
           WHERE version_id=? AND company_id=?""",
        (readiness["successful_applications"], version_id, company_id),
    )
    row = db.execute(
        "SELECT * FROM execution_sop_applications WHERE application_id=? AND company_id=?",
        (application_id, company_id),
    ).fetchone()
    data = _application_dict(row)
    data["readiness"] = readiness
    return data

def promote_sop(db, company_id, sop_id, payload, actor_id):
    version_id = _text(payload.get("version_id"), "VERSION_ID")
    version = _sop_version(db, company_id, sop_id, version_id)
    maturity = _text(payload.get("maturity"), "MATURITY")
    if maturity not in SOP_MATURITY:
        raise ValueError("SOP_MATURITY_INVALID")
    readiness = sop_automation_readiness(db, company_id, sop_id, version_id)
    if maturity == "Automatable" and not readiness["automatable"]:
        raise ValueError("SOP_GATE_REQUIRES_TWO_RECORDED_SUCCESSES")
    db.execute(
        """UPDATE execution_sop_versions SET status='deprecated'
           WHERE sop_id=? AND company_id=? AND version_id<>? AND status='approved'""",
        (sop_id, company_id, version_id),
    )
    db.execute(
        """UPDATE execution_sop_versions SET status='approved',approved_by=?,
           approved_at=COALESCE(approved_at,now()),repetitions=?
           WHERE version_id=? AND sop_id=? AND company_id=?""",
        (
            actor_id, readiness["successful_applications"], version_id, sop_id,
            company_id,
        ),
    )
    db.execute(
        """UPDATE execution_sops SET active_version_id=?,maturity=?,updated_at=now()
           WHERE sop_id=? AND company_id=?""",
        (version_id, maturity, sop_id, company_id),
    )
    return {
        "sop_id": sop_id,
        "version_id": version_id,
        "status": "approved",
        "maturity": maturity,
        "readiness": readiness,
        "previous_status": version["status"],
    }

def list_sop_applications(db, company_id, sop_id, version_id=None):
    sop = db.execute(
        "SELECT 1 FROM execution_sops WHERE sop_id=? AND company_id=?",
        (sop_id, company_id),
    ).fetchone()
    if not sop:
        raise LookupError("SOP_NOT_FOUND")
    params = [company_id, sop_id]
    version_filter = ""
    if version_id:
        _sop_version(db, company_id, sop_id, version_id)
        version_filter = " AND a.version_id=?"
        params.append(version_id)
    rows = db.execute(
        f"""SELECT a.*,v.version_number
            FROM execution_sop_applications a
            JOIN execution_sop_versions v ON v.version_id=a.version_id
            WHERE a.company_id=? AND a.sop_id=?{version_filter}
            ORDER BY a.created_at DESC""",
        tuple(params),
    ).fetchall()
    return [_application_dict(row) for row in rows]
