"""Sana Scan v1.0 — تشخيص موحّد، حتمي، وقابل للتتبع.

هذا المحرك لا يستدعي AI ولا يغيّر درجات الأصول التشغيلية. كل درجة Scan
تظهر فقط عندما تتوفر إجابة SDS-001 ودليل مستقل/حقيقة داعمة، ومعها مكوّنات
الحساب ومصادرها.
"""
import json
import hashlib
import uuid
from datetime import datetime

from database_config import acquire_schema_lock
from sana_reliability import (
    diagnostic_quality,
    source_is_fresh,
    source_family,
    triangulate_sources,
)


METHODOLOGY_VERSION = "Sana Scan v1.0 — قيد المعايرة"
SCAN_STATUSES = frozenset({"NOT_RUN", "INCOMPLETE", "REVIEW_REQUIRED", "COMPLETE"})
MAX_CRITICAL_EVIDENCE_REQUESTS = 2
EVIDENCE_REQUEST_CYCLE_VERSION = "SANA-EVIDENCE-CYCLE-v1"
_SCAN_SCHEMA_READY = False
EVIDENCE_TYPES = {
    "Fact",
    "Evidence",
    "Hypothesis",
    "Assumption",
    "Inference",
    "Recommendation",
}

ASSET_LABELS = {
    "Knowledge": "المعرفة",
    "Operations": "التشغيل",
    "Brand": "البراند",
    "Data": "البيانات",
    "Independence": "الاستقلال",
}


def normalize_scan_status(status, has_run=True):
    """Return one of the four public journey states.

    ``scan_runs.status`` predates the unified customer journey, so old or
    malformed values must not leak into the client-facing contract.
    """
    if not has_run:
        return "NOT_RUN"
    return status if status in SCAN_STATUSES - {"NOT_RUN"} else "INCOMPLETE"

# قواعد محدودة من إطار Scan المعتمد. لا تُنشئ قرارًا خارج هذه القائمة.
BOTTLENECK_RULES = [
    {
        "rule_id": "SCAN-FOUNDER-DEPENDENCY",
        "asset_type": "Independence",
        "tokens": ("يتعطل أغلب العمل", "الشركة لا تعمل بدوني", "تتوقف بعض العمليات المهمة"),
        "title": "اعتماد تشغيلي مرتفع على المؤسس",
        "hypothesis": "قد يكون اعتماد التشغيل على المؤسس سببًا مرشحًا للاختناق.",
        "inference": "تُظهر الأدلة المرتبطة أن غياب المؤسس يهدد استمرارية عمليات مهمة.",
        "opportunity": "نقل عملية حرجة واحدة وقرار يومي واحد من المؤسس إلى الفريق.",
        "decision": "اعتماد تجربة تفويض موثقة لمدة 30 يومًا لعملية حرجة واحدة.",
        "effort": "Medium",
        "priority": 100,
    },
    {
        "rule_id": "SCAN-DATA-INTUITION",
        "asset_type": "Data",
        "tokens": ("الحدس", "خبرتي الشخصية", "بلا طريقة ثابتة", "لا توجد طريقة ثابتة", "حسب الموقف"),
        "title": "القرار لا يعتمد على قياس ثابت",
        "hypothesis": "قد يكون غياب القياس المنتظم سببًا في تفاوت جودة القرارات.",
        "inference": "توضح الأدلة المرتبطة أن القرارات المهمة لا تستند إلى مؤشرات ثابتة.",
        "opportunity": "تحديد ثلاثة مؤشرات مرتبطة بالسؤال التشخيصي ومراجعتها أسبوعيًا.",
        "decision": "اعتماد لوحة أسبوعية تجريبية لثلاثة مؤشرات لمدة 30 يومًا.",
        "effort": "Low",
        "priority": 90,
    },
    {
        "rule_id": "SCAN-ACQUISITION-CONCENTRATION",
        "asset_type": "Brand",
        "tokens": (
            "انخفاض كبير",
            "انخفاض متوسط",
            "نعم، بشكل كبير",
            "نعم، بدرجة متوسطة",
            "هشاشة مصدر العملاء",
        ),
        "title": "اعتماد اكتساب العملاء على مصدر واحد",
        "hypothesis": "قد يكون تركّز اكتساب العملاء في مصدر واحد سببًا لهشاشة المبيعات.",
        "inference": "توضح الأدلة المرتبطة أن توقف مصدر العملاء الرئيسي سيؤثر في المبيعات.",
        "opportunity": "اختبار قناة اكتساب ثانية مع مقياس نجاح محدد قبل التوسع.",
        "decision": "اعتماد اختبار قناة اكتساب ثانية لمدة 30 يومًا قبل أي توسع.",
        "effort": "Medium",
        "priority": 80,
    },
    {
        "rule_id": "SCAN-OPERATIONS-FRICTION",
        "asset_type": "Operations",
        "tokens": ("إعادة عمل", "تأخير", "التشغيل", "يتعطل"),
        "title": "تفاوت أو تعطل في تسليم العمل",
        "hypothesis": "قد يكون غياب معيار قبول موحد سببًا مرشحًا لتعطل التسليم.",
        "inference": "تربط الأدلة بين تحدي التشغيل وتعطل أو تفاوت في التنفيذ.",
        "opportunity": "توثيق معيار قبول واحد لأكثر عملية متكررة.",
        "decision": "اعتماد معيار قبول تجريبي لعملية واحدة ومراجعته بعد 30 يومًا.",
        "effort": "Medium",
        "priority": 70,
    },
    {
        "rule_id": "SCAN-KNOWLEDGE-CONCENTRATION",
        "asset_type": "Knowledge",
        "tokens": ("في رأس", "غير موثق", "لا يوجد sop", "غياب المؤسس"),
        "title": "المعرفة الحرجة غير قابلة للنقل",
        "hypothesis": "قد يكون تركّز المعرفة في شخص واحد سببًا مرشحًا للاختناق.",
        "inference": "توضح الأدلة أن المعرفة الحرجة غير موثقة بما يكفي لنقلها.",
        "opportunity": "توثيق إجراء واحد تتوقف عليه الاستمرارية.",
        "decision": "اعتماد توثيق SOP واحد لعملية حرجة خلال 30 يومًا.",
        "effort": "Medium",
        "priority": 60,
    },
]


def _loads(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def evidence_request_fingerprint(case_id, question):
    """Return a stable, case-scoped fingerprint for an evidence question."""
    normalized = " ".join(str(question or "").split()).casefold()
    return hashlib.sha256(
        f"{case_id}:{normalized}".encode("utf-8")
    ).hexdigest()[:24]


def _previous_evidence_request_cycle(db, case_id):
    row = db.execute(
        """SELECT result FROM scan_runs
           WHERE case_id=? ORDER BY created_at DESC, scan_id DESC LIMIT 1""",
        (case_id,),
    ).fetchone()
    if not row:
        return {}
    result = _loads(row["result"], {})
    cycle = result.get("evidence_request_cycle")
    return cycle if isinstance(cycle, dict) else {}


def _evidence_request_cycle(
    db,
    case_id,
    missing_evidence,
    *,
    proposed_decision=None,
    response=None,
):
    """Track at most two critical questions for one decision journey."""
    previous = _previous_evidence_request_cycle(db, case_id)
    if previous.get("outcome") in {"DECISION", "CONDITIONAL_EXPERIMENT", "UNKNOWN"} and not response:
        previous = {}

    cycle_id = previous.get("cycle_id") or (
        "EVIDENCE-CYCLE-" + uuid.uuid4().hex[:12].upper()
    )
    requests = []
    for item in previous.get("requests") or []:
        if not isinstance(item, dict) or not item.get("fingerprint"):
            continue
        requests.append({
            "fingerprint": str(item["fingerprint"]),
            "request_key": str(item.get("request_key") or item["fingerprint"]),
            "question": str(item.get("question") or item.get("label") or ""),
            "label": str(item.get("label") or item.get("question") or ""),
            "kind": "CRITICAL",
            "status": item.get("status") or "PENDING",
        })
    by_fingerprint = {item["fingerprint"]: item for item in requests}

    normalized_missing = []
    for question in missing_evidence or []:
        question = str(question or "").strip()
        if not question:
            continue
        fingerprint = evidence_request_fingerprint(case_id, question)
        if fingerprint not in {item["fingerprint"] for item in normalized_missing}:
            normalized_missing.append({
                "fingerprint": fingerprint,
                "question": question,
            })
    missing_fingerprints = {item["fingerprint"] for item in normalized_missing}

    if isinstance(response, dict) and (
        response.get("fingerprint") or response.get("request_key")
    ):
        response_fingerprint = str(
            response.get("fingerprint") or response.get("request_key")
        )
        response_status = (
            "UNKNOWN" if response.get("outcome") == "UNKNOWN" else "ANSWERED"
        )
        target = by_fingerprint.get(response_fingerprint)
        if target:
            target["status"] = response_status

    for item in requests:
        if item["status"] == "PENDING" and item["fingerprint"] not in missing_fingerprints:
            item["status"] = "ANSWERED"

    for item in normalized_missing:
        if len(requests) >= MAX_CRITICAL_EVIDENCE_REQUESTS:
            break
        if item["fingerprint"] not in by_fingerprint:
            request = {
                "fingerprint": item["fingerprint"],
                "request_key": item["fingerprint"],
                "question": item["question"],
                "label": item["question"],
                "kind": "CRITICAL",
                "status": "PENDING",
            }
            requests.append(request)
            by_fingerprint[item["fingerprint"]] = request

    if proposed_decision:
        decision_status = str(proposed_decision.get("decision_status") or "")
        outcome = (
            "CONDITIONAL_EXPERIMENT"
            if "Conditional" in decision_status
            else "DECISION"
        )
    elif isinstance(response, dict) and response.get("outcome") == "UNKNOWN":
        outcome = "UNKNOWN"
    elif (
        requests
        and missing_fingerprints
        and all(item["status"] != "PENDING" for item in requests)
    ):
        outcome = "UNKNOWN"
    else:
        outcome = previous.get("outcome")

    pending = [
        item for item in requests
        if item["status"] == "PENDING" and item["fingerprint"] in missing_fingerprints
    ]
    if outcome == "UNKNOWN":
        pending = []
    exhausted = len(requests) >= MAX_CRITICAL_EVIDENCE_REQUESTS and bool(pending)
    return {
        "version": EVIDENCE_REQUEST_CYCLE_VERSION,
        "cycle_id": cycle_id,
        "critical_request_limit": MAX_CRITICAL_EVIDENCE_REQUESTS,
        "critical_request_count": len(requests),
        "requested_fingerprints": [item["fingerprint"] for item in requests],
        "requests": requests,
        "pending_requests": pending,
        "exhausted": exhausted,
        "outcome": outcome,
        "reevaluation_count": int(previous.get("reevaluation_count") or 0)
        + (1 if response else 0),
    }


def ensure_schema(db):
    """ترقية غير هدّامة لبيانات Scan وتصنيف الأدلة."""
    global _SCAN_SCHEMA_READY
    if _SCAN_SCHEMA_READY:
        return
    schema_ready = db.execute(
        """SELECT
             EXISTS (
               SELECT 1 FROM information_schema.columns
               WHERE table_schema='public' AND table_name='evidence'
                 AND column_name='verification_status'
             ) AS evidence_ready,
             EXISTS (
               SELECT 1 FROM information_schema.tables
               WHERE table_schema='public' AND table_name='scan_findings'
             ) AS findings_ready,
             EXISTS (
               SELECT 1
               FROM information_schema.referential_constraints
               WHERE constraint_schema='public'
                 AND constraint_name='diagnostic_baselines_case_id_fkey'
                 AND delete_rule='CASCADE'
             ) AS baseline_ready,
             EXISTS (
               SELECT 1 FROM information_schema.tables
               WHERE table_schema='public' AND table_name='evidence_relations'
             ) AS relations_ready"""
    ).fetchone()
    if (
        schema_ready
        and bool(schema_ready["evidence_ready"])
        and bool(schema_ready["findings_ready"])
        and bool(schema_ready["baseline_ready"])
        and bool(schema_ready["relations_ready"])
    ):
        _SCAN_SCHEMA_READY = True
        return
    acquire_schema_lock(db)
    evidence_type_col = db.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_name='evidence' AND column_name='evidence_type'"""
    ).fetchone()
    if not evidence_type_col:
        db.execute("ALTER TABLE evidence ADD COLUMN evidence_type TEXT DEFAULT 'Evidence'")
    source_ref_col = db.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_name='evidence' AND column_name='source_ref'"""
    ).fetchone()
    if not source_ref_col:
        db.execute("ALTER TABLE evidence ADD COLUMN source_ref TEXT")
    evidence_columns = {
        row["column_name"] for row in db.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name='evidence'"""
        ).fetchall()
    }
    additions = {
        "information_type": "TEXT NOT NULL DEFAULT 'Narrative'",
        "verification_status": "TEXT NOT NULL DEFAULT 'UNVERIFIED'",
        "source_category": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "period_start": "DATE",
        "period_end": "DATE",
        "raw_value": "TEXT",
        "normalized_value": "NUMERIC",
        "unit": "TEXT",
        "topic_key": "TEXT",
        "seasonality_context": "TEXT",
    }
    for column, definition in additions.items():
        if column not in evidence_columns:
            db.execute(f"ALTER TABLE evidence ADD COLUMN {column} {definition}")
    db.execute(
        """UPDATE evidence SET evidence_type='Evidence'
           WHERE evidence_type IS NULL OR evidence_type=''"""
    )
    evidence_constraint = db.execute(
        """SELECT 1 FROM pg_constraint WHERE conname='evidence_type_allowed'"""
    ).fetchone()
    if not evidence_constraint:
        db.execute(
            """ALTER TABLE evidence ADD CONSTRAINT evidence_type_allowed
               CHECK (evidence_type IN
                 ('Fact','Evidence','Hypothesis','Assumption','Inference','Recommendation'))
               NOT VALID"""
        )
    db.execute("""CREATE TABLE IF NOT EXISTS scan_runs (
        scan_id TEXT PRIMARY KEY,
        case_id TEXT NOT NULL REFERENCES cases(case_id),
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        status TEXT NOT NULL,
        result TEXT NOT NULL,
        methodology_version TEXT NOT NULL,
        created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS scan_findings (
        finding_id TEXT PRIMARY KEY,
        scan_id TEXT NOT NULL REFERENCES scan_runs(scan_id),
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        asset_id TEXT,
        classification TEXT NOT NULL,
        title TEXT NOT NULL,
        statement TEXT NOT NULL,
        source_ids TEXT NOT NULL,
        review_status TEXT NOT NULL DEFAULT 'Pending Review',
        created_at TEXT DEFAULT (to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS diagnostic_baselines (
        baseline_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        case_id TEXT REFERENCES cases(case_id) ON DELETE CASCADE,
        baseline_start DATE NOT NULL,
        baseline_end DATE NOT NULL,
        comparison_start DATE,
        comparison_end DATE,
        seasonality_context TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(company_id, case_id),
        CHECK (baseline_end >= baseline_start),
        CHECK (
          (comparison_start IS NULL AND comparison_end IS NULL)
          OR (comparison_start IS NOT NULL AND comparison_end >= comparison_start)
        )
    )""")
    baseline_fk = db.execute(
        """SELECT confdeltype FROM pg_constraint
           WHERE conname='diagnostic_baselines_case_id_fkey'"""
    ).fetchone()
    if baseline_fk and baseline_fk["confdeltype"] != "c":
        db.execute(
            """ALTER TABLE diagnostic_baselines
               DROP CONSTRAINT diagnostic_baselines_case_id_fkey"""
        )
        db.execute(
            """ALTER TABLE diagnostic_baselines
               ADD CONSTRAINT diagnostic_baselines_case_id_fkey
               FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE"""
        )
    db.execute("""CREATE TABLE IF NOT EXISTS evidence_relations (
        relation_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        case_id TEXT REFERENCES cases(case_id),
        from_evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE CASCADE,
        to_evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE CASCADE,
        relation_type TEXT NOT NULL CHECK (relation_type IN ('SUPPORTS','CONTRADICTS')),
        status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','RESOLVED')),
        verification_question TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(from_evidence_id, to_evidence_id, relation_type)
    )""")
    finding_constraint = db.execute(
        """SELECT 1 FROM pg_constraint WHERE conname='scan_finding_classification_allowed'"""
    ).fetchone()
    if not finding_constraint:
        db.execute(
            """ALTER TABLE scan_findings ADD CONSTRAINT scan_finding_classification_allowed
               CHECK (classification IN
                 ('Fact','Evidence','Hypothesis','Assumption','Inference','Recommendation'))"""
        )
    db.execute("CREATE INDEX IF NOT EXISTS idx_scan_runs_case ON scan_runs(case_id, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_scan_findings_scan ON scan_findings(scan_id)")
    _SCAN_SCHEMA_READY = True


def _source_item(row):
    evidence_type = row["evidence_type"] or "Evidence"
    if evidence_type not in EVIDENCE_TYPES:
        raise ValueError(f"Invalid stored evidence_type: {evidence_type}")
    return {
        "source_id": row["evidence_id"],
        "classification": evidence_type,
        "statement": row["title"],
        "source_type": row["source_type"],
        "source_ref": row["source_ref"] or row["source_type"] or row["evidence_id"],
        "source_date": row["date_collected"],
        "asset_id": row["asset_id"],
        "confidence": row["confidence"],
        "information_type": row["information_type"] or "Narrative",
        "verification_status": row["verification_status"] or "UNVERIFIED",
        "source_category": row["source_category"] or "UNKNOWN",
        "period_start": row["period_start"].isoformat() if row["period_start"] else None,
        "period_end": row["period_end"].isoformat() if row["period_end"] else None,
        "raw_value": row["raw_value"],
        "normalized_value": (
            float(row["normalized_value"]) if row["normalized_value"] is not None else None
        ),
        "unit": row["unit"],
        "topic_key": row["topic_key"],
        "seasonality_context": row["seasonality_context"],
    }


def _is_discovery(item):
    return (
        str(item.get("source_ref") or "").startswith("SDS-001")
        or item.get("source_type") in {"اكتشاف_ذاتي", "sana_discovery"}
    )


def _asset_sources(asset, sources):
    if not asset.get("asset_id"):
        return []
    direct = [
        item for item in sources
        if item.get("asset_id") == asset["asset_id"]
        and item["classification"] in {"Fact", "Evidence"}
        and item.get("information_type") in {"Actual", "Narrative"}
    ]
    label = asset["asset_type"]
    if label == "Data":
        direct += [
            item for item in sources
            if "decision_style" in str(item.get("statement") or "")
            and item["classification"] in {"Fact", "Evidence"}
            and item.get("information_type") in {"Actual", "Narrative"}
            and item not in direct
        ]
    if label == "Knowledge":
        direct += [
            item for item in sources
            if item["classification"] in {"Fact", "Evidence"}
            and item.get("information_type") in {"Actual", "Narrative"}
            and (
                "sop" in str(item.get("statement") or "").lower()
                or str(item.get("source_ref") or "").startswith("SDS-001 Q5")
            )
            and item not in direct
        ]
    return direct


def _score_asset(asset, sources):
    """افصل القوة الفعلية عن الثقة واكتمال المعلومات."""
    if not asset.get("asset_id"):
        return {
            "asset_id": None,
            "asset_type": asset["asset_type"],
            "asset_name": asset["asset_name"],
            "status": "INCOMPLETE",
            "score": None,
            "score_label": "غير مكتمل",
            "explanation": "لا توجد خانة أصل قانونية واحدة لهذا المحور.",
            "missing_evidence": [asset["integrity_issue"]],
            "source_ids": [],
            "components": [],
            "asset_score": {
                "value": None, "change": 0, "source_ids": [],
                "reason": "لا يوجد أصل قانوني واحد لهذا المحور.",
            },
            "evidence_confidence": {
                "score": 0, "source_ids": [],
                "reason": "لا توجد معلومات مرتبطة بأصل قانوني.",
            },
            "information_completeness": {
                "score": 0, "source_ids": [],
                "reason": "صورة المحور غير مكتملة.",
            },
        }
    direct = _asset_sources(asset, sources)
    discovery = [item for item in direct if _is_discovery(item)]
    adaptive_claims = [
        item for item in direct
        if str(item.get("source_ref") or "").startswith("SDS-002:")
        and item.get("source_category") == "SELF_REPORTED"
    ]
    sourced_unverified = [
        item for item in direct
        if item.get("source_category") not in {"SELF_REPORTED", "CASE", "UNKNOWN"}
        and item.get("verification_status") == "UNVERIFIED"
        and str(item.get("source_ref") or "").strip()
        and source_is_fresh(item)
    ]
    independent_evidence = [
        item for item in direct
        if item["classification"] == "Evidence"
        and not _is_discovery(item)
        and item.get("source_type") != "Case"
        and item.get("verification_status") == "VERIFIED"
        and item.get("information_type") in {"Actual", "Narrative"}
        and source_is_fresh(item)
    ]
    facts = [
        item for item in direct
        if item["classification"] == "Fact"
        and item.get("verification_status") == "VERIFIED"
        and item.get("information_type") in {"Actual", "Narrative"}
        and source_is_fresh(item)
    ]
    corroborating = independent_evidence + facts
    completeness_components = [
        (25, discovery, "إجابة Discovery مرتبطة بالمحور"),
        (20, adaptive_claims, "إجابة تكيفية جديدة"),
        (20, sourced_unverified, "معلومة ذات مصدر لم تُراجع بعد"),
        (35, corroborating, "معلومة موثقة وحديثة"),
    ]
    completeness_score = min(
        100,
        sum(points for points, items, _ in completeness_components if items),
    )
    completeness_source_ids = list(dict.fromkeys(
        item["source_id"]
        for _, items, _ in completeness_components
        for item in items
    ))
    confidence_score = min(
        100,
        (20 if discovery else 0)
        + min(10, 5 * len(adaptive_claims))
        + min(25, 15 * len(sourced_unverified))
        + min(60, 30 * len(corroborating)),
    )
    confidence_source_ids = list(dict.fromkeys(
        item["source_id"] for item in (
            discovery + adaptive_claims + sourced_unverified + corroborating
        )
    ))
    recorded_score = max(0, min(100, int(asset.get("current_score") or 0)))
    missing = []
    if not discovery:
        missing.append("إجابة SDS-001 مرتبطة بهذا الأصل")
    if not corroborating:
        missing.append("Actual موثّق أو Evidence مستقل حديث يؤكد إفادة Discovery")
    if missing:
        return {
            "asset_id": asset["asset_id"],
            "asset_type": asset["asset_type"],
            "asset_name": asset["asset_name"],
            "status": "INCOMPLETE",
            "score": None,
            "score_label": "غير مكتمل",
            "explanation": "لا تُعرض درجة قبل اكتمال الحد الأدنى من الأدلة.",
            "missing_evidence": missing,
            "source_ids": [item["source_id"] for item in direct],
            "components": [],
            "asset_score": {
                "value": recorded_score,
                "baseline_value": recorded_score,
                "change": 0,
                "source_ids": [],
                "reason": (
                    "لم تتغير قوة الأصل؛ المعلومات الحالية غير موثقة بما يكفي."
                ),
            },
            "evidence_confidence": {
                "score": confidence_score,
                "source_ids": confidence_source_ids,
                "reason": (
                    "الإفادة الذاتية تضيف ثقة محدودة، ولا تعادل Evidence موثقًا."
                ),
            },
            "information_completeness": {
                "score": completeness_score,
                "source_ids": completeness_source_ids,
                "reason": (
                    "يقيس مقدار ما عُرف عن المحور، ولا يساوي قوة الأصل."
                ),
            },
        }

    risk_source_ids = []
    for rule in BOTTLENECK_RULES:
        if rule["asset_type"] != asset["asset_type"]:
            continue
        risk_source_ids.extend(
            item["source_id"] for item in _matching_sources(rule, direct)
        )
    fact_ids = [item["source_id"] for item in facts]
    evidence_ids = [item["source_id"] for item in independent_evidence]
    risk_source_ids = list(dict.fromkeys(risk_source_ids))
    verified_ids = {item["source_id"] for item in corroborating}
    verified_risk_ids = sorted(verified_ids & set(risk_source_ids))
    verified_support_ids = sorted(verified_ids - set(verified_risk_ids))
    score_delta = (
        min(4, 2 * len(verified_support_ids))
        - min(4, 2 * len(verified_risk_ids))
    )
    score = max(0, min(100, recorded_score + score_delta))
    score_sources = verified_support_ids + verified_risk_ids
    components = [{
        "label": "تعديل محدود بسبب معلومات موثقة",
        "points": score_delta,
        "source_ids": score_sources,
    }] if score_delta else []
    return {
        "asset_id": asset["asset_id"],
        "asset_type": asset["asset_type"],
        "asset_name": asset["asset_name"],
        "status": "COMPLETE",
        "score": score,
        "score_label": f"{score}/100",
        "explanation": (
            "قوة الأصل تبدأ من الدرجة التشغيلية المسجلة، ولا تتغير هنا إلا "
            "بتعديل محدود من Evidence موثق؛ ليست تقييمًا ماليًا."
        ),
        "missing_evidence": [],
        "source_ids": list(dict.fromkeys(
            [item["source_id"] for item in discovery] + evidence_ids + fact_ids
        )),
        "components": components,
        "asset_score": {
            "value": score,
            "baseline_value": recorded_score,
            "change": score_delta,
            "source_ids": score_sources,
            "reason": (
                "تعديل محدود مرتبط حصريًا بمعلومات موثقة وحديثة: "
                + "، ".join(score_sources)
                if score_delta
                else "لا توجد نتيجة موثقة تسمح بتغيير قوة الأصل."
            ),
        },
        "evidence_confidence": {
            "score": confidence_score,
            "source_ids": confidence_source_ids,
            "reason": (
                "الإفادة الذاتية تضيف ثقة محدودة؛ المصدر الأقوى والمراجعة "
                "الموثقة يرفعان الثقة بدرجة أكبر."
            ),
        },
        "information_completeness": {
            "score": completeness_score,
            "source_ids": completeness_source_ids,
            "reason": (
                "يقيس مقدار ما عُرف عن المحور، ولا يساوي قوة الأصل."
            ),
        },
    }


def _raw_sources(sources):
    return [
        item for item in sources
        if item["classification"] in {"Fact", "Evidence"}
        and item.get("information_type") in {"Actual", "Narrative"}
    ]


def _source_is(item, question, legacy_prefix):
    ref = str(item.get("source_ref") or "")
    statement = str(item.get("statement") or "")
    if question == "Q5" and ref.startswith("SDS-001"):
        return ref == "SDS-001 Q5"
    return ref.startswith(f"SDS-001 {question}") or statement.startswith(legacy_prefix)


def _contains_any(item, tokens):
    text = str(item.get("statement") or "").lower()
    return any(token.lower() in text for token in tokens)


def _matching_sources(rule, sources):
    """شروط إطار Scan المركبة؛ لا مطابقة عالمية بكلمة عامة."""
    raw = _raw_sources(sources)
    rule_id = rule["rule_id"]
    if rule_id == "SCAN-FOUNDER-DEPENDENCY":
        severe = [
            item for item in raw
            if _source_is(item, "Q5", "مستوى اعتماد الشركة على المؤسس:")
            and _contains_any(item, ("يتعطل أغلب العمل", "الشركة لا تعمل بدوني"))
        ]
        corroborating = [
            item for item in raw
            if severe
            and item["source_id"] not in {source["source_id"] for source in severe}
            and item.get("asset_id") == severe[0].get("asset_id")
            and not _is_discovery(item)
            and item.get("source_type") != "Case"
            and _contains_any(item, ("توقف", "تعطل", "غياب المؤسس"))
        ]
        return severe + corroborating
    if rule_id == "SCAN-DATA-INTUITION":
        direct = [
            item for item in raw
            if _source_is(item, "Q6", "[decision_style]")
            and _contains_any(item, ("الحدس", "خبرتي الشخصية", "بلا طريقة ثابتة"))
        ]
        corroborating = [
            item for item in raw
            if direct
            and item["source_id"] not in {source["source_id"] for source in direct}
            and item.get("asset_id") == direct[0].get("asset_id")
            and not _is_discovery(item)
            and item.get("source_type") != "Case"
            and _contains_any(item, ("حدس", "بلا بيانات", "لا توجد لوحة", "دون قياس"))
        ]
        return direct + corroborating
    if rule_id == "SCAN-ACQUISITION-CONCENTRATION":
        channel = [
            item for item in raw
            if _source_is(item, "Q4", "مصدر اكتساب العملاء:")
            and "follow-up" not in str(item.get("source_ref") or "")
        ]
        impact = [
            item for item in raw
            if _source_is(item, "Q4", "هشاشة مصدر العملاء:")
            and _contains_any(
                item,
                (
                    "انخفاض كبير",
                    "انخفاض متوسط",
                    "نعم، بشكل كبير",
                    "نعم، بدرجة متوسطة",
                ),
            )
        ]
        corroborating = [
            item for item in raw
            if channel
            and item["source_id"] not in {
                source["source_id"] for source in channel + impact
            }
            and item.get("asset_id") == channel[0].get("asset_id")
            and not _is_discovery(item)
            and item.get("source_type") != "Case"
            and _contains_any(
                item,
                ("قناة واحدة", "عميل واحد", "إحالات", "منصة واحدة", "مصدر واحد"),
            )
        ]
        return channel + impact + corroborating if channel and impact else []
    if rule_id == "SCAN-OPERATIONS-FRICTION":
        delivery_problem = [
            item for item in raw
            if item.get("source_type") in {"Case", "Open Case"}
            and _contains_any(item, ("تأخير", "إعادة عمل"))
        ]
        missing_acceptance = [
            item for item in raw
            if _contains_any(item, ("لا يوجد معيار قبول", "غياب معيار قبول", "بدون معيار قبول"))
        ]
        return delivery_problem + missing_acceptance if delivery_problem and missing_acceptance else []
    if rule_id == "SCAN-KNOWLEDGE-CONCENTRATION":
        founder_impact = [
            item for item in raw
            if _source_is(item, "Q5", "مستوى اعتماد الشركة على المؤسس:")
            and _contains_any(item, ("يتعطل أغلب العمل", "الشركة لا تعمل بدوني"))
        ]
        missing_sop = [
            item for item in raw
            if _contains_any(item, ("لا يوجد sop", "لا يوجد ملف sop", "غياب sop", "غير موثق"))
        ]
        return founder_impact + missing_sop if founder_impact and missing_sop else []
    return []


def _independent_rule_sources(items):
    """مصادر خارج إجابات Discovery والمشكلة المعلنة الحالية."""
    return [
        item for item in items
        if not _is_discovery(item)
        and item.get("source_type") != "Case"
        and item["classification"] in {"Fact", "Evidence"}
        and item.get("verification_status") == "VERIFIED"
        and item.get("information_type") in {"Actual", "Narrative"}
        and source_is_fresh(item)
    ]


def _canonical_assets(rows):
    """يعيد خمسة محاور دائمًا، ويوقف المحور عند النقص أو التكرار."""
    grouped = {asset_type: [] for asset_type in ASSET_LABELS}
    for row in rows:
        if row["asset_type"] in grouped:
            grouped[row["asset_type"]].append(dict(row))
    canonical = []
    for asset_type, label in ASSET_LABELS.items():
        matches = grouped[asset_type]
        if len(matches) == 1:
            canonical.append(matches[0])
        else:
            issue = (
                f"أصل {label} غير موجود"
                if not matches
                else f"يوجد {len(matches)} أصول من نوع {label}؛ يلزم أصل قانوني واحد"
            )
            canonical.append({
                "asset_id": None,
                "asset_type": asset_type,
                "asset_name": label,
                "integrity_issue": issue,
            })
    return canonical


def _priority_values(rule, matched_sources, recurrence):
    source_families = {source_family(item) for item in matched_sources}
    has_fact = any(item["classification"] == "Fact" for item in matched_sources)
    impact = "High" if rule["priority"] >= 80 or recurrence >= 2 else "Medium"
    effort = rule["effort"]
    confidence = "High" if has_fact and len(source_families) >= 2 else (
        "Medium" if len(source_families) >= 2 else "Low"
    )
    urgency = "High" if recurrence >= 3 else ("Medium" if recurrence >= 2 else "Low")
    if impact == "Low":
        priority = "لا تنفّذ الآن"
    elif impact == "High" and effort == "High":
        priority = "خطط لاحقًا"
    elif impact == "High" and confidence != "High":
        priority = "اختبر أولًا"
    elif impact == "High" and effort in {"Low", "Medium"} and confidence == "High":
        priority = "نفّذ الآن"
    else:
        priority = "راقب"
    return {
        "impact": impact,
        "effort": effort,
        "confidence": confidence,
        "urgency": urgency,
        "recurring_open_cases": recurrence,
        "priority": priority,
    }


def _capacity_utilization(sources):
    """Derive utilization arithmetically; it does not establish a cause."""
    current_keys = {"students", "student_count", "enrolled_students", "current_students"}
    capacity_keys = {"capacity", "student_capacity", "maximum_capacity"}
    current = next(
        (
            item for item in sources
            if str(item.get("topic_key") or "").lower() in current_keys
            and item.get("information_type") == "Actual"
            and item.get("normalized_value") is not None
        ),
        None,
    )
    capacity = next(
        (
            item for item in sources
            if str(item.get("topic_key") or "").lower() in capacity_keys
            and item.get("information_type") in {"Actual", "Target"}
            and item.get("normalized_value") is not None
        ),
        None,
    )
    if not current or not capacity or float(capacity["normalized_value"]) <= 0:
        return None
    utilization = round(
        (float(current["normalized_value"]) / float(capacity["normalized_value"])) * 100,
        2,
    )
    return {
        "current": current,
        "capacity": capacity,
        "utilization_percent": utilization,
        "source_ids": [current["source_id"], capacity["source_id"]],
    }


def _derived_item(classification, title, statement, source_ids, **extra):
    if classification not in EVIDENCE_TYPES:
        raise ValueError(f"Unsupported Scan classification: {classification}")
    item = {
        "classification": classification,
        "title": title,
        "statement": statement,
        "source_ids": list(dict.fromkeys(source_ids)),
        "review_status": "Pending Review",
    }
    item.update(extra)
    return item


def run_scan(db, case_id, evidence_response=None):
    """ينشئ Sana Scan واحدًا للقضية ويحفظ سلسلة التتبع كاملة.

    ``evidence_response`` is metadata from the single save that triggered
    this re-evaluation; it is never used as diagnostic evidence.
    """
    ensure_schema(db)
    case = db.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not case:
        return None
    company = db.execute("SELECT * FROM companies WHERE company_id=?", (case["company_id"],)).fetchone()
    asset_rows = db.execute(
        """SELECT asset_id, asset_type, asset_name, current_score FROM assets
           WHERE company_id=? ORDER BY asset_type""",
        (case["company_id"],),
    ).fetchall()
    assets = _canonical_assets(asset_rows)
    evidence_rows = db.execute(
        """SELECT evidence_id, asset_id, title, source_type, source_ref,
                  evidence_type, confidence, date_collected, information_type,
                  verification_status, source_category, period_start, period_end,
                  raw_value, normalized_value, unit, topic_key, seasonality_context
           FROM evidence WHERE case_id=? ORDER BY date_collected, evidence_id""",
        (case_id,),
    ).fetchall()
    sources = [_source_item(row) for row in evidence_rows]
    case_source = {
        "source_id": f"CASE:{case_id}:DECLARED_PROBLEM",
        "classification": "Evidence",
        "statement": case["declared_problem"] or case["real_question"] or case["case_title"],
        "case_title": case["case_title"],
        "real_question": case["real_question"],
        "source_type": "Case",
        "source_ref": "القضية: المشكلة المعلنة",
        "source_date": case["opened_at"],
        "asset_id": case["related_asset_id"],
        "confidence": case["confidence_score"],
        "information_type": "Narrative",
        "verification_status": "UNVERIFIED",
        "source_category": "CASE",
        "period_start": None,
        "period_end": None,
        "raw_value": None,
        "normalized_value": None,
        "unit": None,
        "topic_key": "declared_problem",
        "seasonality_context": None,
    }
    sources.append(case_source)
    triangulation = triangulate_sources(sources)
    contradicted_ids = {
        source_id
        for conflict in triangulation["conflicts"]
        for source_id in conflict["source_ids"]
    }
    for item in sources:
        if item["source_id"] in contradicted_ids:
            item["verification_status"] = "CONTRADICTED"
    for conflict in triangulation["conflicts"]:
        evidence_ids = [
            source_id for source_id in conflict["source_ids"]
            if not source_id.startswith("CASE:")
        ]
        for source_id in evidence_ids:
            db.execute(
                """UPDATE evidence SET verification_status='CONTRADICTED'
                   WHERE evidence_id=? AND company_id=? AND case_id=?""",
                (source_id, case["company_id"], case_id),
            )
        for index, left in enumerate(evidence_ids):
            for right in evidence_ids[index + 1:]:
                db.execute(
                    """INSERT INTO evidence_relations
                       (relation_id, company_id, case_id, from_evidence_id,
                        to_evidence_id, relation_type, verification_question)
                       VALUES (?,?,?,?,?,'CONTRADICTS',?)
                       ON CONFLICT (from_evidence_id, to_evidence_id, relation_type)
                       DO UPDATE SET status='OPEN',
                         verification_question=EXCLUDED.verification_question""",
                    (
                        "ER-" + uuid.uuid4().hex[:10].upper(),
                        case["company_id"], case_id, left, right,
                        conflict["verification_question"],
                    ),
                )
    for agreement in triangulation["agreements"]:
        evidence_ids = [
            source_id for source_id in agreement["source_ids"]
            if not source_id.startswith("CASE:")
        ]
        for index, left in enumerate(evidence_ids):
            for right in evidence_ids[index + 1:]:
                db.execute(
                    """INSERT INTO evidence_relations
                       (relation_id, company_id, case_id, from_evidence_id,
                        to_evidence_id, relation_type, status)
                       VALUES (?,?,?,?,?,'SUPPORTS','RESOLVED')
                       ON CONFLICT (from_evidence_id, to_evidence_id, relation_type)
                       DO UPDATE SET status='RESOLVED'""",
                    (
                        "ER-" + uuid.uuid4().hex[:10].upper(),
                        case["company_id"], case_id, left, right,
                    ),
                )

    quality = diagnostic_quality(sources, triangulation["conflicts"])
    decision_confidence = quality["decision_confidence"]
    baseline_row = db.execute(
        """SELECT * FROM diagnostic_baselines
           WHERE company_id=? AND case_id=?""",
        (case["company_id"], case_id),
    ).fetchone()
    baseline = dict(baseline_row) if baseline_row else None
    if baseline:
        for key, value in list(baseline.items()):
            if hasattr(value, "isoformat"):
                baseline[key] = value.isoformat()
    baseline_valid = bool(
        baseline
        and baseline.get("baseline_start")
        and baseline.get("baseline_end")
        and baseline["baseline_end"] >= baseline["baseline_start"]
        and (
            not baseline.get("comparison_start")
            or (
                baseline.get("comparison_end")
                and baseline["comparison_end"] >= baseline["comparison_start"]
            )
        )
    )
    asset_scores = [_score_asset(asset, sources) for asset in assets]

    matches = []
    for rule in BOTTLENECK_RULES:
        matched_sources = _matching_sources(rule, sources)
        if matched_sources:
            matches.append((rule, matched_sources))
    matches.sort(key=lambda pair: (pair[0]["priority"], len(pair[1])), reverse=True)

    findings = []
    bottleneck = opportunity = proposed_decision = None
    fallback = not matches
    if matches:
        rule, matched_sources = matches[0]
    else:
        # لا نخترع سببًا عند غياب نمط معروف؛ نُخرج فرضية جمع دليل قابلة للمراجعة.
        matched_sources = [case_source]
        rule = {
            "rule_id": "SCAN-EVIDENCE-GATE",
            "asset_type": next(
                (
                    asset["asset_type"] for asset in assets
                    if asset.get("asset_id") == case_source.get("asset_id")
                ),
                None,
            ),
            "title": "التحدي المعلن يحتاج تحققًا مستقلًا",
            "hypothesis": "قد يكون التحدي المعلن هو الاختناق الأول، لكن لا يوجد دليل مستقل كافٍ لتأكيد سببه.",
            "inference": "",
            "opportunity": "تحويل التحدي المعلن إلى فرضية واختباره بـ Fact أو Evidence مستقل.",
            "decision": "اعتماد جمع دليل مستقل واحد قبل اتخاذ قرار تنفيذي.",
            "priority": 50,
            "effort": "Low",
        }

    source_ids = [item["source_id"] for item in matched_sources]
    independent_rule_sources = _independent_rule_sources(matched_sources)
    matched_conflicts = [
        conflict for conflict in triangulation["conflicts"]
        if set(conflict["source_ids"]) & set(source_ids)
    ]
    rule_qualified = (
        not fallback
        and bool(independent_rule_sources)
        and not matched_conflicts
        and baseline_valid
    )
    capacity_utilization = _capacity_utilization(sources)
    submitted_client_sources = [
        item for item in sources
        if not _is_discovery(item)
        and item.get("source_type") != "Case"
        and item["classification"] in {"Fact", "Evidence"}
        and str(item.get("source_ref") or "").strip()
    ]
    hypothesis = _derived_item(
        "Hypothesis",
        rule["title"],
        rule["hypothesis"],
        source_ids,
        rule_id=rule["rule_id"],
        asset_type=rule["asset_type"],
        evidence_strength=quality["evidence_strength"],
        data_reliability=quality["data_reliability"],
        conflicts=matched_conflicts,
    )
    findings.append(hypothesis)
    if rule_qualified and len(set(source_ids)) >= 2:
        bottleneck = _derived_item(
            "Inference",
            rule["title"],
            rule["inference"],
            source_ids,
            rule_id=rule["rule_id"],
            asset_type=rule["asset_type"],
            candidate_root_cause=True,
            evidence_strength=quality["evidence_strength"],
            data_reliability=quality["data_reliability"],
            conflicts=[],
        )
    else:
        bottleneck = hypothesis
    if bottleneck is not hypothesis:
        findings.append(bottleneck)

    if rule_qualified:
        recurrence = len({
            item["source_id"].split(":")[1]
            for item in matched_sources
            if item["source_id"].startswith("CASE:") and ":" in item["source_id"]
        })
        priority = _priority_values(rule, matched_sources, recurrence)
        opportunity = _derived_item(
            "Recommendation",
            "الفرصة الأعلى أولوية",
            rule["opportunity"],
            source_ids,
            **priority,
            evidence_strength=quality["evidence_strength"],
            data_reliability=quality["data_reliability"],
            conflicts=[],
        )
        proposed_decision = _derived_item(
            "Recommendation",
            "قرار مقترح للمراجعة البشرية",
            rule["decision"],
            source_ids,
            decision_status="Proposed — Human Review Required",
            evidence_strength=quality["evidence_strength"],
            data_reliability=quality["data_reliability"],
            conflicts=[],
        )
        findings.extend([opportunity, proposed_decision])
    elif (
        capacity_utilization
        and decision_confidence["score"] is not None
        and decision_confidence["score"] >= 50
        and not triangulation["conflicts"]
    ):
        source_ids = capacity_utilization["source_ids"]
        proposed_decision = _derived_item(
            "Recommendation",
            "تجربة قياس آمنة قبل قرار كبير",
            "نفّذ تجربة قياس لمدة 7–14 يومًا للتحقق من استغلال السعة قبل أي توسع أو خفض.",
            source_ids,
            decision_status="Conditional — Human Review Required",
            decision_risk="Low",
            decision_confidence=decision_confidence,
            kpi={
                "name": "نسبة استغلال السعة",
                "baseline": capacity_utilization["utilization_percent"],
                "unit": "%",
            },
            causal_claim=False,
        )
        findings.append(proposed_decision)
    elif (
        baseline_valid
        and len({item.get("asset_id") for item in submitted_client_sources if item.get("asset_id")}) >= 2
        and not triangulation["conflicts"]
    ):
        source_ids = [item["source_id"] for item in submitted_client_sources]
        proposed_decision = _derived_item(
            "Recommendation",
            "تجربة قياس مشروطة قبل القرار",
            "راجع المعلومتين مع مختص، ثم نفّذ تجربة قياس قصيرة مرتبطة بالسؤال قبل أي تغيير كبير.",
            source_ids,
            decision_status="Conditional — Human Review Required",
            decision_risk="Low",
            decision_confidence=decision_confidence,
            causal_claim=False,
        )
        findings.append(proposed_decision)

    missing_evidence = []
    for score in asset_scores:
        if score["status"] == "INCOMPLETE":
            missing_evidence.extend(
                f"{score['asset_name']}: {item}" for item in score["missing_evidence"]
            )
    if not rule_qualified:
        missing_evidence.append(
            "Fact أو Evidence مستقل يدعم فرضية الاختناق قبل إصدار استنتاج أو توصية أو قرار"
        )
    if not baseline_valid:
        missing_evidence.append("تحديد الفترة التي توجد عنها بيانات فعلية")
    for conflict in triangulation["conflicts"]:
        missing_evidence.append(conflict["verification_question"])
    stale_sources = [
        item["source_id"] for item in sources
        if item.get("information_type") in {"Actual", "Estimate"}
        and not source_is_fresh(item)
    ]
    if stale_sources:
        missing_evidence.append(
            "تحديث البيانات القديمة قبل الاستنتاج: " + "، ".join(stale_sources)
        )
    missing_evidence = list(dict.fromkeys(missing_evidence))

    evidence_request_cycle = _evidence_request_cycle(
        db,
        case_id,
        missing_evidence,
        proposed_decision=proposed_decision,
        response=evidence_response,
    )

    readiness = (
        "READY" if rule_qualified
        else "CONDITIONAL" if proposed_decision
        else "NOT_READY"
    )
    status = "REVIEW_REQUIRED" if readiness in {"READY", "CONDITIONAL"} else "INCOMPLETE"
    result = {
        "status": status,
        "methodology_version": METHODOLOGY_VERSION,
        "case_id": case_id,
        "company_id": case["company_id"],
        "company_name": company["name"] if company else None,
        "taxonomy": sorted(EVIDENCE_TYPES),
        "classified_inputs": sources,
        "findings": findings,
        "asset_scores": asset_scores,
        "score_complete_count": sum(1 for score in asset_scores if score["status"] == "COMPLETE"),
        "score_total_count": len(asset_scores),
        "bottleneck": bottleneck,
        "opportunity": opportunity,
        "proposed_decision": proposed_decision,
        "missing_evidence": missing_evidence,
        "evidence_requests": evidence_request_cycle["pending_requests"],
        "critical_evidence_requests": evidence_request_cycle["pending_requests"],
        "evidence_request_cycle": evidence_request_cycle,
        "journey_outcome": evidence_request_cycle["outcome"],
        "evidence_progress": {
            "completed": sum(
                1 for item in evidence_request_cycle["requests"]
                if item["status"] != "PENDING"
            ),
            "total": evidence_request_cycle["critical_request_count"],
        },
        "diagnostic_quality": quality,
        "decision_readiness": readiness,
        "decision_confidence": decision_confidence,
        "diagnostic_baseline": baseline,
        "diagnostic_baseline_valid": baseline_valid,
        "triangulation": triangulation,
        "open_conflicts": triangulation["conflicts"],
        "verification_questions": [
            conflict["verification_question"] for conflict in triangulation["conflicts"]
        ],
        "decisions_available_now": (
            [proposed_decision] if proposed_decision else []
        ),
        "decisions_waiting_for_evidence": (
            [] if proposed_decision else [{
                "title": "القرار التنفيذي مؤجل",
                "reason": "الثقة غير كافية أو البيانات متعارضة أو قديمة.",
            }]
        ),
        "assumptions": [],
        "human_review_required": True,
        "ai_used": False,
        "reference_knowledge_used_as_evidence": False,
        "reference_knowledge_effect": "none",
        "explainability_rule": (
            "Asset Score قوة فعلية، وEvidence Confidence ثقة، وInformation "
            "Completeness اكتمال؛ الإفادة الذاتية لا تغيّر Asset Score. "
            "لا درجة معتمدة بلا SDS-001 وActual/Evidence مستقل موثّق وحديث، "
            "ولا تختلط Actual وForecast وTarget، ولا يُحسم التعارض تلقائيًا؛ "
            "المعرفة العامة لا تدخل Evidence ولا تغيّر التشخيص أو الدرجة أو القرار."
        ),
        "diagnostic_problem": {
            "title": case["case_title"],
            "statement": case["declared_problem"] or case["real_question"] or case["case_title"],
            "real_question": case["real_question"],
            "source_id": case_source["source_id"],
            "source_type": case_source["source_type"],
            "source_date": case_source["source_date"],
        },
    }

    scan_id = (
        "SCAN-"
        + datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        + "-"
        + uuid.uuid4().hex[:6].upper()
    )
    db.execute(
        """INSERT INTO scan_runs
           (scan_id, case_id, company_id, status, result, methodology_version)
           VALUES (?,?,?,?,?,?)""",
        (
            scan_id,
            case_id,
            case["company_id"],
            status,
            json.dumps(result, ensure_ascii=False),
            METHODOLOGY_VERSION,
        ),
    )
    asset_ids = {
        asset["asset_type"]: asset["asset_id"]
        for asset in assets if asset.get("asset_id")
    }
    for finding in findings:
        if finding["classification"] in {"Hypothesis", "Inference"} and not finding["source_ids"]:
            raise ValueError("Hypothesis/Inference must have source_ids")
        db.execute(
            """INSERT INTO scan_findings
               (finding_id, scan_id, company_id, asset_id, classification,
                title, statement, source_ids, review_status)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "SF-" + uuid.uuid4().hex[:10].upper(),
                scan_id,
                case["company_id"],
                asset_ids.get(finding.get("asset_type")),
                finding["classification"],
                finding["title"],
                finding["statement"],
                json.dumps(finding["source_ids"], ensure_ascii=False),
                finding["review_status"],
            ),
        )
    db.commit()
    result["scan_id"] = scan_id
    return result


def latest_scan(db, case_id):
    ensure_schema(db)
    row = db.execute(
        """SELECT scan_id, result, created_at FROM scan_runs
           WHERE case_id=? ORDER BY created_at DESC, scan_id DESC LIMIT 1""",
        (case_id,),
    ).fetchone()
    if not row:
        return None
    result = _loads(row["result"], {})
    result["scan_id"] = row["scan_id"]
    result["created_at"] = row["created_at"]
    return result
