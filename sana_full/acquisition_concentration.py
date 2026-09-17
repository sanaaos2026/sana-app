"""Shared acquisition-channel risk vocabulary used by Discovery and Scan."""

ACQUISITION_CHANNEL_RISK_IMPACTS = (
    "😰 انخفاض كبير",
    "😟 انخفاض متوسط",
    "😰 نعم، بشكل كبير",
    "🙂 نعم، بدرجة متوسطة",
)
ACQUISITION_CHANNEL_IMPACT_EVIDENCE_PREFIX = "هشاشة مصدر العملاء:"


def is_acquisition_channel_risk_impact(value):
    """Return whether a Discovery impact answer indicates channel risk."""
    return str(value or "").strip() in ACQUISITION_CHANNEL_RISK_IMPACTS


def is_acquisition_channel_risk_evidence(statement):
    """Classify a stored Q4 follow-up using the exact Discovery answer rules."""
    text = str(statement or "").strip()
    if not text.startswith(ACQUISITION_CHANNEL_IMPACT_EVIDENCE_PREFIX):
        return False
    answer = text[len(ACQUISITION_CHANNEL_IMPACT_EVIDENCE_PREFIX):]
    return is_acquisition_channel_risk_impact(answer)