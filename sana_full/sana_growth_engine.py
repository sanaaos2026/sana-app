"""Business Growth OS — محرك الاختناق والتجارب والقرار وحلقة التعلم.

المحرك حتمي أولًا: لا يستدعي AI ولا يسمح بإنشاء استنتاج أو تجربة أو قرار
إذا غاب الدليل أو كان الـBaseline غير صالح. أي صياغة ذكية مستقبلية تبقى
مسودة خارج هذه الطبقة.
"""
import json
import math
import uuid
from datetime import date

from database_config import acquire_schema_lock
from sana_growth_os import (
    CLASSIFICATIONS,
    GOS_VERSION,
    METRIC_KEYS,
    NA_DEFERRED,
    get_baseline,
    get_company_profile,
    require_usable_baseline,
)


DECISIONS = ("Scale", "Modify", "Hold", "Kill")
PROCESS_STAGES = ("Manual", "Measure", "Improve", "Standardize", "Automate")
NODE_TYPES = ("evidence", "pattern", "hypothesis", "experiment", "result", "decision", "knowledge", "sop")
ENGINE_GOVERNANCE_RECORDS = [
    ("bottleneck_cycle", "gos_bottleneck_cycles", "case_id", NA_DEFERRED, "framework:B2B-OS-001", "bottleneck.cycle.opened"),
    ("bottleneck", "gos_bottlenecks", "cycle_id", NA_DEFERRED, "framework:B2B-OS-001", "bottleneck.selected"),
    ("experiment", "gos_experiments", "cycle_id", NA_DEFERRED, "framework:B2B-OS-001", "experiment.recorded"),
    ("experiment_process", "gos_experiment_process", "experiment_id", NA_DEFERRED, "framework:B2B-OS-001", "experiment.process.advanced"),
    ("experiment_decision", "gos_experiment_decisions", "experiment_id", NA_DEFERRED, "framework:B2B-OS-001", "experiment.decision.recorded"),
    ("learning_link", "gos_learning_links", "from_id/to_id", NA_DEFERRED, "framework:B2B-OS-001", "learning.linked"),
]


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value, default=None):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _clean(value, field):
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{field} مطلوب")
    return value


def _date(value, field):
    value = _clean(value, field)
    try:
        date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ValueError(f"{field} يجب أن يكون بصيغة YYYY-MM-DD") from exc
    return value[:10]


def _number(value, field):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} يجب أن يكون رقمًا") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} يجب أن يكون رقمًا حقيقيًا")
    return number


def _int_range(value, field, minimum=0, maximum=100):
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} يجب أن يكون رقمًا") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{field} خارج النطاق")
    return value


def _source(value, field="source_ref"):
    return _clean(value, field)


def _company_case(db, company_id, case_id):
    if case_id and not db.execute(
        "SELECT 1 FROM cases WHERE case_id=? AND company_id=?", (case_id, company_id)
    ).fetchone():
        raise ValueError("case_id لا يتبع الشركة")


def _evidence_row(db, company_id, evidence_id):
    row = db.execute(
        "SELECT evidence_id AS source_id, evidence_type AS classification, title AS statement "
        "FROM evidence WHERE evidence_id=? AND company_id=?",
        (evidence_id, company_id),
    ).fetchone()
    if row:
        return dict(row)
    row = db.execute(
        "SELECT truth_id AS source_id, classification, subject AS statement "
        "FROM gos_truth_records WHERE truth_id=? AND company_id=?",
        (evidence_id, company_id),
    ).fetchone()
    return dict(row) if row else None


def _validate_evidence_ids(db, company_id, evidence_ids, required=True):
    if isinstance(evidence_ids, str):
        evidence_ids = [evidence_ids]
    evidence_ids = list(dict.fromkeys(evidence_ids or []))
    if required and not evidence_ids:
        raise ValueError("EVIDENCE_GATE: يلزم دليل واحد على الأقل")
    sources = []
    for evidence_id in evidence_ids:
        row = _evidence_row(db, company_id, evidence_id)
        if not row:
            raise ValueError(f"EVIDENCE_GATE: الدليل غير موجود أو لا يتبع الشركة: {evidence_id}")
        if row["classification"] == "Conflict":
            raise ValueError(f"EVIDENCE_GATE: يوجد تعارض غير محلول في الدليل: {evidence_id}")
        if row["classification"] not in {"Fact", "Evidence"}:
            raise ValueError(f"EVIDENCE_GATE: المصدر ليس Fact أو Evidence: {evidence_id}")
        sources.append(row)
    return evidence_ids, sources


def _financial_impact(value):
    if not isinstance(value, dict):
        raise ValueError("EVIDENCE_GATE: الأثر المالي يجب أن يكون كائنًا موثقًا")
    amount = value.get("value")
    if amount is None:
        raise ValueError("EVIDENCE_GATE: الأثر المالي غير متاح")
    amount = _number(amount, "financial_impact.value")
    return {
        "value": amount,
        "currency": _clean(value.get("currency", "SAR"), "financial_impact.currency"),
        "source_ref": _source(value.get("source_ref"), "financial_impact.source_ref"),
        "period": value.get("period"),
    }


def ensure_schema(db):
    """ترقية إضافية لجداول المحرك؛ لا تسقط أو تعدّل بيانات النظام القائم."""
    acquire_schema_lock(db)
    db.execute("""CREATE TABLE IF NOT EXISTS gos_bottleneck_cycles (
        cycle_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        case_id TEXT REFERENCES cases(case_id),
        baseline_id TEXT NOT NULL REFERENCES gos_baselines(baseline_id),
        project_profile_key TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('EVIDENCE_GATE','READY','CLOSED')),
        evidence_gate_json TEXT,
        governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        closed_at TIMESTAMPTZ
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS gos_bottlenecks (
        bottleneck_id TEXT PRIMARY KEY,
        cycle_id TEXT NOT NULL REFERENCES gos_bottleneck_cycles(cycle_id) ON DELETE CASCADE,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        problem TEXT NOT NULL,
        evidence_ids_json TEXT NOT NULL,
        financial_impact_json TEXT NOT NULL,
        candidate_cause TEXT NOT NULL,
        classification TEXT NOT NULL CHECK (classification IN ('Hypothesis','Conflict')),
        confidence INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
        priority INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 100),
        is_primary BOOLEAN NOT NULL DEFAULT false,
        governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_gos_one_primary_bottleneck
        ON gos_bottlenecks(cycle_id) WHERE is_primary = true""")
    db.execute("""CREATE TABLE IF NOT EXISTS gos_experiments (
        experiment_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        cycle_id TEXT NOT NULL REFERENCES gos_bottleneck_cycles(cycle_id),
        bottleneck_id TEXT REFERENCES gos_bottlenecks(bottleneck_id),
        hypothesis TEXT NOT NULL,
        one_change TEXT NOT NULL,
        target_segment TEXT NOT NULL,
        owner TEXT NOT NULL,
        start_date DATE NOT NULL,
        kpi TEXT NOT NULL,
        baseline_id TEXT NOT NULL REFERENCES gos_baselines(baseline_id),
        baseline_snapshot_json TEXT,
        target_numeric NUMERIC NOT NULL,
        success_boundary TEXT NOT NULL,
        stop_boundary TEXT NOT NULL,
        evidence_ids_json TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('draft','running','closed','cancelled')),
        locked_at TIMESTAMPTZ,
        started_at TIMESTAMPTZ,
        actual_result_json TEXT,
        outcome TEXT,
        decision_id TEXT,
        governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        closed_at TIMESTAMPTZ
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS gos_experiment_process (
        experiment_id TEXT PRIMARY KEY REFERENCES gos_experiments(experiment_id) ON DELETE CASCADE,
        current_stage TEXT NOT NULL CHECK (current_stage IN ('Manual','Measure','Improve','Standardize','Automate')),
        manual_proven BOOLEAN NOT NULL DEFAULT false,
        repetitions INTEGER NOT NULL DEFAULT 0 CHECK (repetitions >= 0),
        source_ref TEXT NOT NULL,
        governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS gos_experiment_decisions (
        decision_id TEXT PRIMARY KEY,
        experiment_id TEXT NOT NULL REFERENCES gos_experiments(experiment_id),
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        action TEXT NOT NULL CHECK (action IN ('Scale','Modify','Hold','Kill')),
        reason TEXT NOT NULL,
        evidence_ids_json TEXT NOT NULL,
        result_json TEXT NOT NULL,
        baseline_id TEXT NOT NULL REFERENCES gos_baselines(baseline_id),
        source_ref TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('proposed','approved')),
        governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        approved_at TIMESTAMPTZ
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS gos_learning_links (
        link_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        from_type TEXT NOT NULL CHECK (from_type IN ('evidence','pattern','hypothesis','experiment','result','decision','knowledge','sop')),
        from_id TEXT NOT NULL,
        link_type TEXT NOT NULL,
        to_type TEXT NOT NULL CHECK (to_type IN ('evidence','pattern','hypothesis','experiment','result','decision','knowledge','sop')),
        to_id TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        knowledge_version TEXT NOT NULL,
        governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (from_type <> to_type OR from_id <> to_id),
        UNIQUE(company_id, from_type, from_id, link_type, to_type, to_id, knowledge_version)
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_gos_cycles_company ON gos_bottleneck_cycles(company_id, created_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_gos_experiments_company ON gos_experiments(company_id, created_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_gos_learning_company ON gos_learning_links(company_id, created_at DESC)")
    for table_name, governance_id in (
        ("gos_bottlenecks", "GOS-BOTTLENECK"),
        ("gos_experiments", "GOS-EXPERIMENT"),
        ("gos_experiment_process", "GOS-EXPERIMENT_PROCESS"),
    ):
        columns = {
            row[0] for row in db.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=?",
                (table_name,),
            ).fetchall()
        }
        if "governance_id" not in columns:
            db.execute(f"ALTER TABLE {table_name} ADD COLUMN governance_id TEXT "
                       "REFERENCES gos_governance_registry(governance_id)")
            db.execute(f"UPDATE {table_name} SET governance_id=? WHERE governance_id IS NULL",
                       (governance_id,))
            db.execute(f"ALTER TABLE {table_name} ALTER COLUMN governance_id SET NOT NULL")


def seed_growth_engine(db):
    """يسجل الخانات الخمس لكل نوع سجل في المحرك دون تكرار."""
    for record_type, storage, case_link, asset_link, framework_link, event in ENGINE_GOVERNANCE_RECORDS:
        db.execute("""INSERT INTO gos_governance_registry
            (governance_id, record_type, storage_destination, case_link, asset_link,
             framework_link, business_event, version)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (record_type) DO NOTHING""",
            (f"GOS-{record_type.upper()}", record_type, storage, case_link, asset_link,
             framework_link, event, GOS_VERSION))


def _cycle_row(db, company_id, cycle_id):
    row = db.execute(
        "SELECT * FROM gos_bottleneck_cycles WHERE cycle_id=? AND company_id=?",
        (cycle_id, company_id),
    ).fetchone()
    if not row:
        raise LookupError("CYCLE_NOT_FOUND")
    data = dict(row)
    data["evidence_gate"] = _loads(data.pop("evidence_gate_json"), None)
    bottlenecks = db.execute(
        "SELECT * FROM gos_bottlenecks WHERE cycle_id=? ORDER BY is_primary DESC, priority DESC",
        (cycle_id,),
    ).fetchall()
    data["bottlenecks"] = []
    for item in bottlenecks:
        value = dict(item)
        value["evidence_ids"] = _loads(value.pop("evidence_ids_json"), [])
        value["financial_impact"] = _loads(value.pop("financial_impact_json"), {})
        data["bottlenecks"].append(value)
    return data


def list_cycles(db, company_id):
    rows = db.execute(
        "SELECT cycle_id FROM gos_bottleneck_cycles WHERE company_id=? ORDER BY created_at DESC",
        (company_id,),
    ).fetchall()
    return [_cycle_row(db, company_id, row["cycle_id"]) for row in rows]


def create_bottleneck_cycle(db, company_id, payload):
    baseline_id = _clean(payload.get("baseline_id"), "baseline_id")
    baseline = require_usable_baseline(db, company_id, baseline_id)
    case_id = payload.get("case_id") or None
    _company_case(db, company_id, case_id)
    profile = get_company_profile(db, company_id)
    candidates = payload.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("EVIDENCE_GATE: لا يوجد اختناق مرشح مدعوم")

    normalized = []
    for candidate in candidates:
        problem = _clean(candidate.get("problem"), "problem")
        evidence_ids, _ = _validate_evidence_ids(db, company_id, candidate.get("evidence_ids"))
        financial_impact = _financial_impact(candidate.get("financial_impact"))
        cause = _clean(candidate.get("candidate_cause"), "candidate_cause")
        if candidate.get("classification", "Hypothesis") not in {"Hypothesis", "Conflict"}:
            raise ValueError("classification للاختناق يجب أن تكون Hypothesis أو Conflict")
        if candidate.get("conflicts"):
            raise ValueError("EVIDENCE_GATE: توجد تعارضات تحتاج تسوية قبل اختيار الاختناق")
        normalized.append({
            "problem": problem,
            "evidence_ids": evidence_ids,
            "financial_impact": financial_impact,
            "candidate_cause": cause,
            "confidence": _int_range(candidate.get("confidence"), "confidence"),
            "priority": _int_range(candidate.get("priority"), "priority"),
        })

    winner = max(normalized, key=lambda item: (item["priority"], item["confidence"]))
    cycle_id = str(uuid.uuid4())
    db.execute("""INSERT INTO gos_bottleneck_cycles
        (cycle_id, company_id, case_id, baseline_id, project_profile_key, status, governance_id)
        VALUES (?,?,?,?,?,?,?)""",
        (cycle_id, company_id, case_id, baseline_id, profile["profile_key"], "READY", "GOS-BOTTLENECK_CYCLE"))
    for index, item in enumerate(normalized):
        bottleneck_id = str(uuid.uuid4())
        db.execute("""INSERT INTO gos_bottlenecks
            (bottleneck_id, cycle_id, company_id, problem, evidence_ids_json, financial_impact_json,
             candidate_cause, classification, confidence, priority, is_primary, governance_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (bottleneck_id, cycle_id, company_id, item["problem"], _json(item["evidence_ids"]),
             _json(item["financial_impact"]), item["candidate_cause"], "Hypothesis",
             item["confidence"], item["priority"], item is winner, "GOS-BOTTLENECK"))
        for evidence_id in item["evidence_ids"]:
            create_learning_link(db, company_id, {
                "from_type": "evidence",
                "from_id": evidence_id,
                "link_type": "supports",
                "to_type": "hypothesis",
                "to_id": bottleneck_id,
                "source_ref": item["financial_impact"]["source_ref"],
                "knowledge_version": GOS_VERSION,
            })
    return _cycle_row(db, company_id, cycle_id)


def _cycle_and_baseline(db, company_id, cycle_id, baseline_id):
    cycle = db.execute(
        "SELECT * FROM gos_bottleneck_cycles WHERE cycle_id=? AND company_id=?",
        (cycle_id, company_id),
    ).fetchone()
    if not cycle:
        raise LookupError("CYCLE_NOT_FOUND")
    if cycle["status"] != "READY":
        raise ValueError("EVIDENCE_GATE: دورة الاختناق ليست جاهزة لتجربة")
    if cycle["baseline_id"] != baseline_id:
        raise ValueError("BASELINE_MISMATCH: يجب استخدام Baseline الدورة نفسه")
    baseline = require_usable_baseline(db, company_id, baseline_id)
    return dict(cycle), baseline


def _experiment_input(db, company_id, payload, existing=None):
    data = dict(existing or {})
    data.update({key: value for key, value in payload.items() if value is not None})
    required = {
        "cycle_id": "cycle_id",
        "hypothesis": "hypothesis",
        "one_change": "one_change",
        "target_segment": "target_segment",
        "owner": "owner",
        "start_date": "start_date",
        "kpi": "kpi",
        "baseline_id": "baseline_id",
        "target": "target",
        "success_boundary": "success_boundary",
        "stop_boundary": "stop_boundary",
    }
    for key, field in required.items():
        if key == "target":
            if data.get(key) is None:
                raise ValueError("target مطلوب")
        else:
            _clean(data.get(key), field)
    cycle, baseline = _cycle_and_baseline(db, company_id, data["cycle_id"], data["baseline_id"])
    profile = get_company_profile(db, company_id)
    if data["kpi"] not in METRIC_KEYS:
        raise ValueError("KPI غير مدعوم")
    if data["kpi"] not in (profile.get("metrics") or []):
        raise ValueError("EVIDENCE_GATE: KPI غير مغطى بنوع المشروع المختار")
    metric = next(item for item in baseline["metrics"] if item["metric_key"] == data["kpi"])
    if metric["availability"] != "available" or metric["source_ref"] == NA_DEFERRED:
        raise ValueError("EVIDENCE_GATE: KPI Baseline غير متاح أو بلا مصدر")
    evidence_ids, _ = _validate_evidence_ids(db, company_id, data.get("evidence_ids"), required=True)
    return {
        "cycle": cycle,
        "baseline": baseline,
        "hypothesis": _clean(data["hypothesis"], "hypothesis"),
        "one_change": _clean(data["one_change"], "one_change"),
        "target_segment": _clean(data["target_segment"], "target_segment"),
        "owner": _clean(data["owner"], "owner"),
        "start_date": _date(data["start_date"], "start_date"),
        "kpi": data["kpi"],
        "baseline_id": data["baseline_id"],
        "target": _number(data["target"], "target"),
        "success_boundary": _clean(data["success_boundary"], "success_boundary"),
        "stop_boundary": _clean(data["stop_boundary"], "stop_boundary"),
        "evidence_ids": evidence_ids,
        "source_ref": _source(data.get("source_ref", f"cycle:{data['cycle_id']}")),
    }


def _experiment_row(db, company_id, experiment_id):
    row = db.execute(
        "SELECT * FROM gos_experiments WHERE experiment_id=? AND company_id=?",
        (experiment_id, company_id),
    ).fetchone()
    if not row:
        raise LookupError("EXPERIMENT_NOT_FOUND")
    data = dict(row)
    for field in ("baseline_snapshot_json", "actual_result_json"):
        key = field.replace("_json", "")
        data[key] = _loads(data.pop(field), None)
    data["evidence_ids"] = _loads(data.pop("evidence_ids_json"), [])
    if data.get("decision_id"):
        decision = db.execute(
            "SELECT * FROM gos_experiment_decisions WHERE decision_id=? AND company_id=?",
            (data["decision_id"], company_id),
        ).fetchone()
        data["decision"] = dict(decision) if decision else None
        if data["decision"]:
            data["decision"]["evidence_ids"] = _loads(data["decision"].pop("evidence_ids_json"), [])
            data["decision"]["result"] = _loads(data["decision"].pop("result_json"), {})
    process = db.execute(
        "SELECT * FROM gos_experiment_process WHERE experiment_id=?", (experiment_id,)
    ).fetchone()
    data["process"] = dict(process) if process else None
    return data


def list_experiments(db, company_id):
    rows = db.execute(
        "SELECT experiment_id FROM gos_experiments WHERE company_id=? ORDER BY created_at DESC",
        (company_id,),
    ).fetchall()
    return [_experiment_row(db, company_id, row["experiment_id"]) for row in rows]


def create_experiment(db, company_id, payload):
    data = _experiment_input(db, company_id, payload)
    experiment_id = str(uuid.uuid4())
    primary_bottleneck_id = next(
        item["bottleneck_id"] for item in _cycle_row(
            db, company_id, data["cycle"]["cycle_id"]
        )["bottlenecks"] if item["is_primary"]
    )
    db.execute("""INSERT INTO gos_experiments
        (experiment_id, company_id, cycle_id, bottleneck_id, hypothesis, one_change, target_segment,
         owner, start_date, kpi, baseline_id, target_numeric, success_boundary, stop_boundary,
         evidence_ids_json, source_ref, status, governance_id, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,now())""",
        (experiment_id, company_id, data["cycle"]["cycle_id"], primary_bottleneck_id,
         data["hypothesis"], data["one_change"], data["target_segment"], data["owner"],
         data["start_date"], data["kpi"], data["baseline_id"], data["target"],
         data["success_boundary"], data["stop_boundary"], _json(data["evidence_ids"]),
         data["source_ref"], "draft", "GOS-EXPERIMENT"))
    create_learning_link(db, company_id, {
        "from_type": "hypothesis",
        "from_id": primary_bottleneck_id,
        "link_type": "tested_by",
        "to_type": "experiment",
        "to_id": experiment_id,
        "source_ref": data["source_ref"],
        "knowledge_version": GOS_VERSION,
    })
    return _experiment_row(db, company_id, experiment_id)


def update_experiment(db, company_id, experiment_id, payload):
    current = db.execute(
        "SELECT * FROM gos_experiments WHERE experiment_id=? AND company_id=?",
        (experiment_id, company_id),
    ).fetchone()
    if not current:
        raise LookupError("EXPERIMENT_NOT_FOUND")
    if current["status"] != "draft":
        raise ValueError("EXPERIMENT_LOCKED: لا يمكن تعديل تجربة بعد بدءها")
    existing = dict(current)
    existing["target"] = existing.pop("target_numeric")
    existing["evidence_ids"] = _loads(existing.pop("evidence_ids_json"), [])
    data = _experiment_input(db, company_id, payload, existing)
    db.execute("""UPDATE gos_experiments SET
        cycle_id=?, hypothesis=?, one_change=?, target_segment=?, owner=?, start_date=?, kpi=?,
        baseline_id=?, target_numeric=?, success_boundary=?, stop_boundary=?, evidence_ids_json=?,
        source_ref=? WHERE experiment_id=? AND company_id=?""",
        (data["cycle"]["cycle_id"], data["hypothesis"], data["one_change"], data["target_segment"],
         data["owner"], data["start_date"], data["kpi"], data["baseline_id"], data["target"],
         data["success_boundary"], data["stop_boundary"], _json(data["evidence_ids"]),
         data["source_ref"], experiment_id, company_id))
    return _experiment_row(db, company_id, experiment_id)


def begin_experiment(db, company_id, experiment_id):
    row = db.execute(
        "SELECT * FROM gos_experiments WHERE experiment_id=? AND company_id=?",
        (experiment_id, company_id),
    ).fetchone()
    if not row:
        raise LookupError("EXPERIMENT_NOT_FOUND")
    if row["status"] != "draft":
        raise ValueError("EXPERIMENT_STATE: التجربة ليست Draft")
    baseline = require_usable_baseline(db, company_id, row["baseline_id"])
    db.execute("""UPDATE gos_experiments SET status='running', locked_at=now(),
        started_at=now(), baseline_snapshot_json=? WHERE experiment_id=? AND company_id=?""",
        (_json(baseline), experiment_id, company_id))
    db.execute("""INSERT INTO gos_experiment_process
        (experiment_id, current_stage, manual_proven, repetitions, source_ref, governance_id)
        VALUES (?,?,?,?,?,?) ON CONFLICT (experiment_id) DO NOTHING""",
        (experiment_id, "Manual", False, 0, f"experiment:{experiment_id}", "GOS-EXPERIMENT_PROCESS"))
    return _experiment_row(db, company_id, experiment_id)


def close_experiment(db, company_id, experiment_id, payload):
    row = db.execute(
        "SELECT * FROM gos_experiments WHERE experiment_id=? AND company_id=?",
        (experiment_id, company_id),
    ).fetchone()
    if not row:
        raise LookupError("EXPERIMENT_NOT_FOUND")
    if row["status"] != "running":
        raise ValueError("EXPERIMENT_STATE: لا تُغلق إلا تجربة Running")
    baseline = require_usable_baseline(db, company_id, row["baseline_id"])
    actual = payload.get("actual_result")
    if not isinstance(actual, dict):
        raise ValueError("actual_result مطلوب مع القيمة ومصدر القياس")
    actual_value = _number(actual.get("value"), "actual_result.value")
    actual_source = _source(actual.get("source_ref"), "actual_result.source_ref")
    action = payload.get("decision")
    if action not in DECISIONS:
        raise ValueError("decision يجب أن تكون Scale أو Modify أو Hold أو Kill")
    reason = _clean(payload.get("reason"), "reason")
    evidence_ids, _ = _validate_evidence_ids(db, company_id, payload.get("evidence_ids"))
    decision_source = _source(payload.get("source_ref", f"experiment:{experiment_id}"))
    result = {
        "value": actual_value,
        "source_ref": actual_source,
        "observed_at": _date(actual.get("observed_at"), "actual_result.observed_at"),
        "unit": actual.get("unit"),
        "target": float(row["target_numeric"]),
    }
    # لا نفترض أن الأعلى أفضل؛ CAC ودورة البيع مثالان على KPI يتحسن بالانخفاض.
    # حدود النجاح/الإيقاف محفوظة للمراجعة البشرية، والقرار لا يُستنتج آليًا.
    outcome = "measured"
    decision_id = str(uuid.uuid4())
    db.execute("""INSERT INTO gos_experiment_decisions
        (decision_id, experiment_id, company_id, action, reason, evidence_ids_json, result_json,
         baseline_id, source_ref, status, governance_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (decision_id, experiment_id, company_id, action, reason, _json(evidence_ids), _json(result),
         row["baseline_id"], decision_source, "proposed", "GOS-EXPERIMENT_DECISION"))
    db.execute("""UPDATE gos_experiments SET status='closed', actual_result_json=?,
        outcome=?, decision_id=?, closed_at=now() WHERE experiment_id=? AND company_id=?""",
        (_json(result), outcome, decision_id, experiment_id, company_id))
    create_learning_link(db, company_id, {
        "from_type": "experiment",
        "from_id": experiment_id,
        "link_type": "produced",
        "to_type": "result",
        "to_id": experiment_id,
        "source_ref": actual_source,
        "knowledge_version": GOS_VERSION,
    })
    create_learning_link(db, company_id, {
        "from_type": "result",
        "from_id": experiment_id,
        "link_type": "informed",
        "to_type": "decision",
        "to_id": decision_id,
        "source_ref": decision_source,
        "knowledge_version": GOS_VERSION,
    })
    return _experiment_row(db, company_id, experiment_id)


def approve_decision(db, company_id, decision_id):
    decision = db.execute(
        "SELECT * FROM gos_experiment_decisions WHERE decision_id=? AND company_id=?",
        (decision_id, company_id),
    ).fetchone()
    if not decision:
        raise LookupError("DECISION_NOT_FOUND")
    if decision["status"] != "proposed":
        raise ValueError("DECISION_STATE: القرار معتمد أو غير قابل للاعتماد")
    require_usable_baseline(db, company_id, decision["baseline_id"])
    experiment = db.execute(
        "SELECT status FROM gos_experiments WHERE experiment_id=? AND company_id=?",
        (decision["experiment_id"], company_id),
    ).fetchone()
    if not experiment or experiment["status"] != "closed":
        raise ValueError("DECISION_GATE: يجب إغلاق التجربة قبل اعتماد القرار")
    db.execute(
        "UPDATE gos_experiment_decisions SET status='approved', approved_at=now() "
        "WHERE decision_id=? AND company_id=?",
        (decision_id, company_id),
    )
    return dict(db.execute(
        "SELECT * FROM gos_experiment_decisions WHERE decision_id=?", (decision_id,)
    ).fetchone())


def advance_process(db, company_id, experiment_id, payload):
    row = db.execute(
        """SELECT p.*, e.status AS experiment_status FROM gos_experiment_process p
           JOIN gos_experiments e ON e.experiment_id=p.experiment_id
           WHERE p.experiment_id=? AND e.company_id=?""",
        (experiment_id, company_id),
    ).fetchone()
    if not row:
        raise LookupError("PROCESS_NOT_FOUND")
    current_index = PROCESS_STAGES.index(row["current_stage"])
    next_stage = payload.get("stage")
    if next_stage not in PROCESS_STAGES or PROCESS_STAGES.index(next_stage) != current_index + 1:
        raise ValueError("PROCESS_ORDER: الانتقال يجب أن يتبع Manual → Measure → Improve → Standardize → Automate")
    if next_stage in {"Improve", "Standardize", "Automate"} and row["experiment_status"] != "closed":
        raise ValueError("PROCESS_GATE: يجب قياس التجربة وإغلاقها قبل التحسين أو التوحيد")
    repetitions = int(payload.get("repetitions", row["repetitions"]))
    if repetitions < 0:
        raise ValueError("repetitions لا يمكن أن يكون سالبًا")
    manual_proven = bool(payload.get("manual_proven", row["manual_proven"]))
    if next_stage in {"Standardize", "Automate"} and not manual_proven:
        raise ValueError("AUTOMATION_GATE: يلزم إثبات نجاح العملية يدويًا أولًا")
    if next_stage == "Automate":
        sop_version_id = payload.get("sop_version_id")
        if not sop_version_id:
            raise ValueError("AUTOMATION_GATE: يلزم ربط نسخة SOP مقاسة")
        sop = db.execute(
            """SELECT s.sop_id FROM execution_sop_versions v
               JOIN execution_sops s ON s.sop_id=v.sop_id AND s.company_id=v.company_id
               WHERE v.version_id=? AND v.company_id=?""",
            (sop_version_id, company_id),
        ).fetchone()
        if not sop:
            raise ValueError("AUTOMATION_GATE: نسخة SOP غير موجودة أو لا تتبع الشركة")
        successful = db.execute(
            """SELECT COUNT(*) AS c FROM execution_sop_applications
               WHERE company_id=? AND sop_id=? AND version_id=?
                 AND comparison_status='improved'""",
            (company_id, sop["sop_id"], sop_version_id),
        ).fetchone()["c"]
        if successful < 2:
            raise ValueError("AUTOMATION_GATE: يلزم تطبيقان ناجحان موثقان للنسخة")
        repetitions = successful
    source_ref = _source(payload.get("source_ref", f"experiment:{experiment_id}"))
    db.execute("""UPDATE gos_experiment_process SET current_stage=?, manual_proven=?,
        repetitions=?, source_ref=?, updated_at=now() WHERE experiment_id=?""",
        (next_stage, manual_proven, repetitions, source_ref, experiment_id))
    return dict(db.execute(
        "SELECT * FROM gos_experiment_process WHERE experiment_id=?", (experiment_id,)
    ).fetchone())


def _validate_learning_node(db, company_id, node_type, node_id):
    if node_type == "evidence":
        exists = _evidence_row(db, company_id, node_id)
    elif node_type == "hypothesis":
        exists = db.execute(
            "SELECT 1 FROM gos_bottlenecks WHERE bottleneck_id=? AND company_id=?",
            (node_id, company_id),
        ).fetchone()
    elif node_type == "experiment":
        exists = db.execute(
            "SELECT 1 FROM gos_experiments WHERE experiment_id=? AND company_id=?",
            (node_id, company_id),
        ).fetchone()
    elif node_type == "result":
        exists = db.execute(
            "SELECT 1 FROM gos_experiments WHERE experiment_id=? AND company_id=? "
            "AND actual_result_json IS NOT NULL",
            (node_id, company_id),
        ).fetchone()
    elif node_type == "decision":
        exists = db.execute(
            "SELECT 1 FROM gos_experiment_decisions WHERE decision_id=? AND company_id=?",
            (node_id, company_id),
        ).fetchone()
    elif node_type in {"pattern", "knowledge"}:
        exists = db.execute(
            "SELECT 1 FROM knowledge_objects WHERE object_id=?", (node_id,)
        ).fetchone()
    elif node_type == "sop":
        exists = db.execute(
            "SELECT 1 FROM knowledge_objects WHERE object_id=? AND COALESCE(sop, '') <> ''",
            (node_id,),
        ).fetchone()
    else:
        exists = None
    if not exists:
        raise ValueError(f"LEARNING_GATE: عقدة {node_type} غير موجودة أو لا تتبع الشركة")


def create_learning_link(db, company_id, payload):
    from_type = payload.get("from_type")
    to_type = payload.get("to_type")
    if from_type not in NODE_TYPES or to_type not in NODE_TYPES:
        raise ValueError("نوع عقدة حلقة التعلم غير مدعوم")
    from_id = _clean(payload.get("from_id"), "from_id")
    to_id = _clean(payload.get("to_id"), "to_id")
    if from_type == to_type and from_id == to_id:
        raise ValueError("لا يمكن ربط العقدة بنفسها")
    _validate_learning_node(db, company_id, from_type, from_id)
    _validate_learning_node(db, company_id, to_type, to_id)
    link_type = _clean(payload.get("link_type"), "link_type")
    source_ref = _source(payload.get("source_ref"))
    version = _source(payload.get("knowledge_version", GOS_VERSION), "knowledge_version")
    existing = db.execute("""SELECT * FROM gos_learning_links WHERE company_id=?
        AND from_type=? AND from_id=? AND link_type=? AND to_type=? AND to_id=? AND knowledge_version=?""",
        (company_id, from_type, from_id, link_type, to_type, to_id, version)).fetchone()
    if existing:
        return dict(existing), False
    link_id = str(uuid.uuid4())
    db.execute("""INSERT INTO gos_learning_links
        (link_id, company_id, from_type, from_id, link_type, to_type, to_id,
         source_ref, knowledge_version, governance_id)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (link_id, company_id, from_type, from_id, link_type, to_type, to_id,
         source_ref, version, "GOS-LEARNING_LINK"))
    return dict(db.execute(
        "SELECT * FROM gos_learning_links WHERE link_id=?", (link_id,)
    ).fetchone()), True


def list_learning_links(db, company_id):
    return [dict(row) for row in db.execute(
        "SELECT * FROM gos_learning_links WHERE company_id=? ORDER BY created_at DESC",
        (company_id,),
    ).fetchall()]
