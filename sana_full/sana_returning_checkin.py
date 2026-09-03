"""Incremental check-in for returning companies.

This module reads the existing case/decision/task/evidence state and asks only
questions that can affect the active priority, its KPI, or an open hypothesis.
All answers remain self-reported and never overwrite prior evidence.
"""
import json
import uuid
from datetime import date, datetime, timezone


def ensure_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS returning_checkins (
        checkin_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(company_id),
        case_id TEXT REFERENCES cases(case_id),
        decision_id TEXT REFERENCES decisions(decision_id),
        prior_task_id TEXT REFERENCES tasks(task_id),
        account_id TEXT REFERENCES user_accounts(account_id),
        questions_json TEXT NOT NULL,
        answers_json TEXT,
        summary_json TEXT,
        status TEXT NOT NULL DEFAULT 'OPEN'
          CHECK (status IN ('OPEN','COMPLETED')),
        started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        completed_at TIMESTAMPTZ
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_returning_checkins_company
                  ON returning_checkins(company_id,started_at DESC)""")


def _as_dict(row):
    return dict(row) if row else None


def _json(value, default):
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _stale(collected_at, days=90):
    if not collected_at:
        return True
    try:
        observed = datetime.fromisoformat(str(collected_at).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - observed).days > days
    except ValueError:
        return True


def _comparable(previous, current):
    p_start, p_end = _date(previous.get("period_start")), _date(previous.get("period_end"))
    c_start, c_end = _date(current.get("period_start")), _date(current.get("period_end"))
    if not all((p_start, p_end, c_start, c_end)) or p_end >= c_start:
        return False, "الفترتان متداخلتان أو غير مكتملتين."
    p_days, c_days = (p_end - p_start).days + 1, (c_end - c_start).days + 1
    if not p_days or not c_days or not 0.8 <= c_days / p_days <= 1.25:
        return False, "مدة الفترتين غير متقاربة."
    previous_season = str(previous.get("seasonality_context") or "").strip()
    current_season = str(current.get("seasonality_context") or "").strip()
    if previous_season and current_season and previous_season != current_season:
        return False, "سياق الفترتين مختلف."
    if str(previous.get("unit") or "").strip() != str(current.get("unit") or "").strip():
        return False, "وحدة القياس مختلفة."
    return True, None


def company_state(db, company_id):
    company = db.execute(
        """SELECT company_id,name,main_goal,success_criteria,sds_done
           FROM companies WHERE company_id=?""", (company_id,)
    ).fetchone()
    if not company:
        raise LookupError("COMPANY_NOT_FOUND")
    case = db.execute(
        """SELECT * FROM cases WHERE company_id=?
           ORDER BY opened_at DESC,case_id DESC LIMIT 1""", (company_id,)
    ).fetchone()
    if not case:
        return {"company": dict(company), "case": None}
    decision = db.execute(
        """SELECT * FROM decisions WHERE company_id=? AND case_id=?
           ORDER BY created_at DESC,decision_id DESC LIMIT 1""",
        (company_id, case["case_id"]),
    ).fetchone()
    task = None
    impact = None
    if decision:
        task = db.execute(
            """SELECT * FROM tasks WHERE company_id=? AND decision_id=?
               ORDER BY created_at DESC,task_id DESC LIMIT 1""",
            (company_id, decision["decision_id"]),
        ).fetchone()
        impact = db.execute(
            """SELECT * FROM p0_impact_reviews
               WHERE company_id=? AND case_id=? AND decision_id=?
               ORDER BY reviewed_at DESC,review_id DESC LIMIT 1""",
            (company_id, case["case_id"], decision["decision_id"]),
        ).fetchone()
    evidence = [
        dict(row) for row in db.execute(
            """SELECT * FROM evidence WHERE company_id=? AND case_id=?
               ORDER BY date_collected DESC,evidence_id DESC""",
            (company_id, case["case_id"]),
        ).fetchall()
    ]
    numeric = next((
        item for item in evidence
        if item.get("normalized_value") is not None
        and item.get("period_start") and item.get("period_end")
    ), None)
    customer_source = next((
        item for item in evidence if item.get("source_ref") == "SDS-001 Q4"
    ), None)
    founder_dependency = next((
        item for item in evidence if item.get("source_ref") == "SDS-001 Q5"
    ), None)
    baseline = db.execute(
        """SELECT * FROM diagnostic_baselines
           WHERE company_id=? AND case_id=?""", (company_id, case["case_id"])
    ).fetchone()
    return {
        "company": dict(company),
        "case": dict(case),
        "decision": _as_dict(decision),
        "task": _as_dict(task),
        "impact": _as_dict(impact),
        "numeric_evidence": numeric,
        "customer_source": customer_source,
        "founder_dependency": founder_dependency,
        "diagnostic_baseline": _as_dict(baseline),
    }


def select_questions(state):
    case, decision, task = state.get("case"), state.get("decision"), state.get("task")
    if not case:
        return []
    questions = [{
        "id": "challenge_change",
        "kind": "choice",
        "text": f"آخر مرة كان أهم تحدٍ عندك هو «{case.get('declared_problem') or case.get('case_title')}». وش صار عليه؟",
        "options": ["صار أفضل", "صار أسوأ", "مثل ما هو", "مو متأكد"],
        "why": "مرتبط مباشرة بالقضية الحالية وقد يغيّر ترتيب الأولوية.",
        "link_type": "CASE",
        "link_id": case["case_id"],
    }]
    if task:
        questions.append({
            "id": "task_execution", "kind": "choice",
            "text": f"هل نفذت الخطوة التي اتفقنا عليها: «{task.get('title')}»؟",
            "options": ["نعم", "جزئيًا", "لا"],
            "why": "يقيس تنفيذ آخر خطوة بدل إعادة التشخيص.",
            "link_type": "TASK", "link_id": task["task_id"],
        })
    numeric = state.get("numeric_evidence")
    if decision and decision.get("success_metric") and numeric:
        questions.append({
            "id": "kpi_current", "kind": "metric",
            "text": f"آخر مقياس مرتبط كان «{decision['success_metric']}». كم صار الآن؟",
            "unit": numeric.get("unit"),
            "previous": {
                "value": float(numeric["normalized_value"]),
                "raw_value": numeric.get("raw_value"),
                "unit": numeric.get("unit"),
                "period_start": str(numeric.get("period_start") or ""),
                "period_end": str(numeric.get("period_end") or ""),
                "seasonality_context": numeric.get("seasonality_context"),
                "source_ref": numeric.get("source_ref"),
            },
            "why": "يوجد قياس رقمي سابق مرتبط بقرار نشط؛ لذلك يمكن قياس التغير بشروط.",
            "link_type": "KPI", "link_id": decision["decision_id"],
        })
    # P0 deliberately stops here. Do not turn the check-in into a second
    # discovery, monthly follow-up, or multi-metric change engine.
    return questions[:3]


def start_checkin(db, company_id, account_id):
    state = company_state(db, company_id)
    questions = select_questions(state)
    if not questions:
        raise ValueError("INITIAL_DISCOVERY_REQUIRED")
    checkin_id = "RCI-" + uuid.uuid4().hex[:12].upper()
    db.execute(
        """INSERT INTO returning_checkins
           (checkin_id,company_id,case_id,decision_id,prior_task_id,account_id,questions_json)
           VALUES (?,?,?,?,?,?,?)""",
        (
            checkin_id, company_id, state["case"]["case_id"],
            (state.get("decision") or {}).get("decision_id"),
            (state.get("task") or {}).get("task_id"), account_id,
            json.dumps(questions, ensure_ascii=False),
        ),
    )
    return {"checkin_id": checkin_id, "questions": questions}


def _answer_map(answers):
    return {
        str(item.get("question_id")): item
        for item in answers if isinstance(item, dict) and item.get("question_id")
    }


def complete_checkin(db, row, answers):
    questions = _json(row["questions_json"], [])
    by_id = _answer_map(answers)
    if any(question["id"] not in by_id for question in questions):
        raise ValueError("CHECKIN_INCOMPLETE")
    improvements, unchanged, new_changes = [], [], []
    comparison = None
    for question in questions:
        answer = by_id[question["id"]]
        value = str(answer.get("value") or "").strip()
        if question["id"] == "challenge_change":
            if value == "صار أفضل":
                improvements.append("التحدي السابق تحسن حسب إفادتك")
            elif value == "مثل ما هو":
                unchanged.append("التحدي السابق ما زال كما هو")
            elif value == "صار أسوأ":
                new_changes.append("التحدي السابق ازداد أثره")
        elif question["id"] == "task_execution":
            if value == "نعم":
                improvements.append("تم تنفيذ الخطوة السابقة")
            elif value == "جزئيًا":
                unchanged.append("الخطوة السابقة نُفذت جزئيًا")
            elif value == "لا":
                unchanged.append("الخطوة السابقة لم تُنفذ بعد")
        elif question["id"] == "kpi_current":
            previous = question["previous"]
            current = {
                "value": answer.get("numeric_value"),
                "unit": previous.get("unit"),
                "period_start": answer.get("period_start"),
                "period_end": answer.get("period_end"),
                "seasonality_context": answer.get("seasonality_context"),
            }
            try:
                current["value"] = float(current["value"])
            except (TypeError, ValueError):
                raise ValueError("INVALID_CURRENT_KPI")
            comparable, reason = _comparable(previous, current)
            comparison = {
                "comparable": comparable, "reason": reason,
                "before": previous, "after": current, "change": None,
            }
            if comparable:
                delta = current["value"] - previous["value"]
                comparison["change"] = {
                    "absolute": delta,
                    "percent": (
                        round(delta / previous["value"] * 100, 2)
                        if previous["value"] != 0 else None
                    ),
                }
                if delta == 0:
                    unchanged.append("المؤشر الرقمي لم يتغير")
                else:
                    new_changes.append(
                        f"تغير المؤشر من {previous['value']:g} إلى "
                        f"{current['value']:g} {current['unit'] or ''}".strip()
                    )
    material = bool(improvements or new_changes)
    summary = {
        "improvements": improvements[:2],
        "unchanged": unchanged[:1],
        "new_changes": new_changes[:1],
        "material_change": material,
        "message": (
            "ظهر تغير يستحق إعادة ترتيب التركيز."
            if material else
            "ما ظهر تغيير جوهري. نقدر نكمل على نفس القرار أو نراجع قضية جديدة."
        ),
        "comparison": comparison,
        "next_url": (
            f"/case/{row['case_id']}"
        ),
    }
    db.execute(
        """UPDATE returning_checkins SET answers_json=?,summary_json=?,
                  status='COMPLETED',completed_at=now() WHERE checkin_id=?""",
        (
            json.dumps(answers, ensure_ascii=False),
            json.dumps(summary, ensure_ascii=False), row["checkin_id"],
        ),
    )
    return summary