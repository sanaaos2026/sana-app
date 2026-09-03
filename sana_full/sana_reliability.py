"""Diagnostic reality calibration shared by Discovery, Evidence, and Sana Scan."""

import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation


INFORMATION_TYPES = frozenset({"Actual", "Estimate", "Forecast", "Target", "Narrative"})
VERIFICATION_STATUSES = frozenset({
    "UNVERIFIED", "VERIFIED", "CONTRADICTED", "STALE", "REJECTED",
})
SOURCE_CATEGORIES = frozenset({
    "SELF_REPORTED", "SYSTEM", "DOCUMENT", "MARKET", "EXPERT", "CASE", "UNKNOWN",
})

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def parse_diagnostic_number(value):
    """Parse Arabic/Latin decimal input without treating missing data as zero."""
    if value is None or isinstance(value, bool):
        raise ValueError("القيمة الرقمية مطلوبة.")
    if isinstance(value, (int, float, Decimal)):
        try:
            parsed = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError("القيمة الرقمية غير صالحة.") from exc
        if not parsed.is_finite():
            raise ValueError("القيمة الرقمية غير صالحة.")
        return parsed
    raw = str(value).strip().translate(_ARABIC_DIGITS)
    if not raw:
        raise ValueError("القيمة الرقمية مطلوبة.")
    raw = raw.replace("\u066c", ",").replace("\u066b", ".").replace(" ", "")
    if not re.fullmatch(r"[+-]?[0-9][0-9,]*(?:\.[0-9]+)?", raw):
        raise ValueError("اكتب رقمًا واضحًا فقط، مثل 0 أو 1250.50.")
    if "," in raw:
        groups = raw.lstrip("+-").split(".")[0].split(",")
        if len(groups) < 2 or any(len(group) != 3 for group in groups[1:]):
            raise ValueError("فاصل الآلاف غير واضح؛ استخدم 1,250 أو 1250.")
        raw = raw.replace(",", "")
    try:
        parsed = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("القيمة الرقمية غير صالحة.") from exc
    if not parsed.is_finite():
        raise ValueError("القيمة الرقمية غير صالحة.")
    return parsed


def iso_date(value, field_name, *, required=False):
    if value in (None, ""):
        if required:
            raise ValueError(f"{field_name} مطلوب بصيغة YYYY-MM-DD.")
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name} يجب أن يكون تاريخًا بصيغة YYYY-MM-DD.") from exc


def validate_context(payload, *, discovery=False, numeric=False):
    """Return a normalized diagnostic context; never silently promotes a claim."""
    if numeric and not str(payload.get("information_type") or "").strip():
        raise ValueError("حدد معنى الرقم: Actual أو Estimate أو Forecast أو Target.")
    information_type = str(payload.get("information_type") or (
        "Actual" if numeric else "Narrative"
    )).strip()
    if information_type not in INFORMATION_TYPES:
        raise ValueError("نوع المعلومة يجب أن يكون Actual أو Estimate أو Forecast أو Target أو Narrative.")
    if numeric and information_type == "Narrative":
        raise ValueError("الرقم يحتاج معنى: Actual أو Estimate أو Forecast أو Target.")

    verification_status = str(payload.get("verification_status") or "UNVERIFIED").upper().strip()
    if verification_status not in VERIFICATION_STATUSES:
        raise ValueError("حالة التحقق غير صالحة.")
    if discovery:
        verification_status = "UNVERIFIED"

    source_category = str(payload.get("source_category") or (
        "SELF_REPORTED" if discovery else "UNKNOWN"
    )).upper().strip()
    if source_category not in SOURCE_CATEGORIES:
        raise ValueError("فئة المصدر غير صالحة.")
    if discovery:
        source_category = "SELF_REPORTED"

    period_start = iso_date(payload.get("period_start"), "بداية الفترة")
    period_end = iso_date(payload.get("period_end"), "نهاية الفترة")
    if (period_start and not period_end) or (period_end and not period_start):
        raise ValueError("حدد بداية الفترة ونهايتها معًا.")
    if period_start and period_end and period_end < period_start:
        raise ValueError("نهاية الفترة يجب ألا تسبق بدايتها.")
    if numeric and information_type in {"Actual", "Estimate", "Forecast", "Target"}:
        if not period_start or not period_end:
            raise ValueError("الفترة مطلوبة لكل رقم تشخيصي.")

    source_ref = str(payload.get("source_ref") or "").strip()
    if numeric and not source_ref:
        raise ValueError("مرجع المصدر مطلوب لكل رقم تشخيصي.")

    normalized_value = None
    raw_value = payload.get("raw_value", payload.get("value"))
    if numeric:
        normalized_value = parse_diagnostic_number(raw_value)
        unit = str(payload.get("unit") or "").strip()
        if not unit:
            raise ValueError("وحدة الرقم مطلوبة.")
    else:
        unit = str(payload.get("unit") or "").strip() or None

    return {
        "information_type": information_type,
        "verification_status": verification_status,
        "source_category": source_category,
        "period_start": period_start,
        "period_end": period_end,
        "source_ref": source_ref or None,
        "raw_value": None if raw_value is None else str(raw_value),
        "normalized_value": normalized_value,
        "unit": unit,
        "topic_key": str(payload.get("topic_key") or "").strip() or None,
        "seasonality_context": str(payload.get("seasonality_context") or "").strip() or None,
    }


def source_is_fresh(item, *, today=None, max_age_days=395):
    """Period end, not collection time, controls freshness of an Actual."""
    if item.get("information_type") not in {"Actual", "Estimate"}:
        return True
    period_end = item.get("period_end")
    if not period_end:
        return False
    today = today or datetime.now(timezone.utc).date()
    try:
        ended = date.fromisoformat(str(period_end)[:10])
    except ValueError:
        return False
    return (today - ended).days <= max_age_days


def source_family(item):
    """Return a stable provenance family so repeated claims do not multiply trust."""
    category = str(item.get("source_category") or "UNKNOWN").upper().strip()
    if category in {"SELF_REPORTED", "CASE"}:
        return category
    ref = str(item.get("source_ref") or "").strip().lower()
    if not ref:
        return category
    prefix = re.split(r"[:/#]", ref, maxsplit=1)[0].strip()
    return f"{category}:{prefix or ref}"


def _confidence_value(item):
    try:
        value = int(item.get("confidence"))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, value))


def decision_confidence(sources, conflicts=None):
    """Conservative confidence from distinct source families, without averaging."""
    conflicted_ids = {
        source_id
        for conflict in (conflicts or [])
        for source_id in conflict.get("source_ids", [])
    }
    families = {}
    for item in sources:
        confidence = _confidence_value(item)
        if (
            confidence is None
            or str(item.get("source_category") or "").upper() == "CASE"
            or item.get("verification_status") in {"CONTRADICTED", "REJECTED", "STALE"}
            or item.get("source_id") in conflicted_ids
            or not source_is_fresh(item)
        ):
            continue
        family = source_family(item)
        families[family] = min(confidence, families.get(family, confidence))
    score = min(families.values()) if families else None
    level = (
        "HIGH" if score is not None and score >= 80
        else "MEDIUM" if score is not None and score >= 50
        else "LOW"
    )
    return {
        "score": score,
        "level": level,
        "method": "MIN_DISTINCT_SOURCE_FAMILY_CONFIDENCE",
        "source_family_count": len(families),
        "source_families": [
            {"family": family, "confidence": confidence}
            for family, confidence in sorted(families.items())
        ],
    }


def diagnostic_quality(sources, conflicts=None):
    conflicts = list(conflicts or [])
    verified = sum(
        1 for item in sources
        if item.get("verification_status") == "VERIFIED" and source_is_fresh(item)
    )
    self_reported = sum(
        1 for item in sources if item.get("source_category") == "SELF_REPORTED"
    )
    stale = sum(1 for item in sources if not source_is_fresh(item))
    contradicted_ids = {
        source_id
        for conflict in conflicts
        for source_id in conflict.get("source_ids", [])
    }
    contradicted = sum(
        1 for item in sources
        if item.get("verification_status") == "CONTRADICTED"
        or item.get("source_id") in contradicted_ids
    )
    missing = sum(
        1 for item in sources
        if not item.get("source_ref")
        or (
            item.get("information_type") in {"Actual", "Estimate", "Forecast", "Target"}
            and (not item.get("period_start") or not item.get("period_end"))
        )
    )
    usable = [
        item for item in sources
        if item.get("verification_status") not in {"CONTRADICTED", "REJECTED", "STALE"}
        and item.get("information_type") in {"Actual", "Narrative"}
        and source_is_fresh(item)
        and _confidence_value(item) is not None
    ]
    families = {source_family(item) for item in usable}
    independent_families = {
        family for family in families if family not in {"SELF_REPORTED", "CASE", "UNKNOWN"}
    }
    verified_families = {
        source_family(item) for item in usable
        if item.get("verification_status") == "VERIFIED"
    }
    confidence = decision_confidence(usable, conflicts)
    verification = (
        "VERIFIED" if usable and all(
            item.get("verification_status") == "VERIFIED" for item in usable
        )
        else "MIXED" if verified_families
        else "UNVERIFIED"
    )
    independence = (
        "INDEPENDENT" if independent_families
        else "SELF_REPORTED_ONLY" if families & {"SELF_REPORTED", "CASE"}
        else "UNKNOWN"
    )
    if conflicts or confidence["score"] is None:
        reliability = "LOW"
    elif confidence["score"] >= 80 and verified_families:
        reliability = "HIGH"
    elif confidence["score"] >= 50:
        reliability = "MEDIUM"
    else:
        reliability = "LOW"
    return {
        "verified_count": verified,
        "self_reported_count": self_reported,
        "open_conflicts_count": len(conflicts),
        "contradicted_count": contradicted,
        "stale_count": stale,
        "missing_count": missing,
        "source_family_count": len(families),
        "independent_source_count": len(independent_families),
        "source_reliability": confidence["source_families"],
        "verification_status": verification,
        "independence": independence,
        "decision_confidence": confidence,
        "evidence_strength": (
            "STRONG" if len(independent_families) >= 2 and not conflicts
            else "MODERATE" if usable and not conflicts
            else "WEAK"
        ),
        "data_reliability": reliability,
    }


def triangulate_sources(sources):
    """Compare same-topic, same-period Actual claims without choosing a winner."""
    groups = {}
    for item in sources:
        if item.get("information_type") != "Actual":
            continue
        key = (
            item.get("topic_key"),
            item.get("period_start"),
            item.get("period_end"),
            item.get("unit"),
        )
        if not key[0] or not key[1] or not key[2]:
            continue
        groups.setdefault(key, []).append(item)

    agreements, conflicts = [], []
    for key, items in groups.items():
        categories = {item.get("source_category") for item in items}
        if len(categories - {None, "UNKNOWN"}) < 2:
            continue
        numeric = [item for item in items if item.get("normalized_value") is not None]
        if len(numeric) < 2:
            continue
        values = {Decimal(str(item["normalized_value"])) for item in numeric}
        source_ids = [item["source_id"] for item in numeric]
        if len(values) == 1:
            agreements.append({
                "topic_key": key[0],
                "period_start": key[1],
                "period_end": key[2],
                "source_ids": source_ids,
                "status": "SUPPORTED",
            })
        else:
            conflicts.append({
                "topic_key": key[0],
                "period_start": key[1],
                "period_end": key[2],
                "source_ids": source_ids,
                "values": [str(value) for value in sorted(values)],
                "status": "CONTRADICTED",
                "verification_question": (
                    f"لدينا قيم مختلفة لـ {key[0]} عن الفترة نفسها. "
                    "ما المرجع الذي يشرح تعريف كل قيمة وحدودها دون افتراض أن أحد المصدرين مخطئ؟"
                ),
            })
    return {"agreements": agreements, "conflicts": conflicts}