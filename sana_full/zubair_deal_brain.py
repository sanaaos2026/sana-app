"""Zubair Deal Brain — private, deterministic deal-memory beta.

The module deliberately separates extraction from commercial decisions:
extraction may be assisted by Claude, while matching, writes, attention
ordering, and metrics are deterministic and sourced from confirmed data.
"""
import hashlib
import json
import re
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from database_config import acquire_schema_lock


VERSION = "private-beta-v1"
GOVERNANCE_ID = "GOS-ZUBAIR_DEAL_BRAIN"
NA = "N/A — Deferred"
REVIEW_DECISIONS = {"stop", "modify", "pattern"}
REVIEW_ACCURACY_VALUES = {"accurate", "issue", "not_reviewed"}
REVIEW_COMPLETE_VALUES = {"accurate", "issue"}
ALLOWED_CLASSIFICATIONS = {"Fact", "Evidence", "Inference", "Unknown"}
ACTIVE_STAGES = {"عميل محتمل", "مؤهل", "عرض مرسل", "تفاوض"}
STAGES = ACTIVE_STAGES | {"فوز", "خسارة"}
FIELD_LABELS = {
    "person": "الشخص",
    "prospect_company": "شركة العميل",
    "phone": "الجوال",
    "relationship_type": "نوع العلاقة",
    "opportunity": "الفرصة",
    "amount": "القيمة المحتملة",
    "stage": "المرحلة",
    "decision_maker": "صاحب القرار",
    "need": "الاحتياج",
    "objection": "الاعتراض",
    "last_contact": "آخر تواصل",
    "next_action": "الخطوة التالية",
    "next_action_due": "تاريخ الخطوة التالية",
}
FIELD_KEYS = tuple(FIELD_LABELS)


def _id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _text(value):
    return str(value or "").strip()


def normalize_identity(value):
    """Stable, tenant-local identity key; never used across companies."""
    value = unicodedata.normalize("NFKC", _text(value)).lower()
    value = "".join(
        char for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )
    value = re.sub(r"[\u064B-\u065F\u0670]", "", value)
    return re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE)


def normalize_phone(value):
    value = _text(value)
    digits = re.sub(r"\D+", "", value)
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("05") and len(digits) == 10:
        digits = "966" + digits[1:]
    return digits


def _governance_columns():
    return """
        governance_id, case_link, asset_link, framework_link, business_event
    """


def ensure_schema(db):
    """Create only the private-beta storage, with the five governance fields."""
    acquire_schema_lock(db)
    db.execute(
        """INSERT INTO gos_governance_registry
           (governance_id, record_type, storage_destination, case_link,
            asset_link, framework_link, business_event)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT (governance_id) DO NOTHING""",
        (
            GOVERNANCE_ID, "zubair_deal_brain", "zubair_*",
            NA, NA, "framework:B2B-OS-001", "zubair.deal_brain.recorded",
        ),
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS zubair_capture_drafts (
            draft_id TEXT PRIMARY KEY,
            company_id TEXT NOT NULL REFERENCES companies(company_id),
            account_id TEXT NOT NULL REFERENCES user_accounts(account_id),
            input_hash TEXT NOT NULL,
            input_type TEXT NOT NULL,
            raw_text TEXT,
            extracted_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'review',
            processing_ms INTEGER,
            confirmed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
            case_link TEXT NOT NULL,
            asset_link TEXT NOT NULL,
            framework_link TEXT NOT NULL,
            business_event TEXT NOT NULL,
            UNIQUE(company_id, input_hash)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS zubair_attachments (
            attachment_id TEXT PRIMARY KEY,
            draft_id TEXT NOT NULL REFERENCES zubair_capture_drafts(draft_id) ON DELETE CASCADE,
            company_id TEXT NOT NULL REFERENCES companies(company_id),
            filename TEXT NOT NULL,
            mime_type TEXT,
            content BYTEA,
            evidence_id TEXT REFERENCES evidence(evidence_id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
            case_link TEXT NOT NULL,
            asset_link TEXT NOT NULL,
            framework_link TEXT NOT NULL,
            business_event TEXT NOT NULL
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS zubair_contacts (
            contact_id TEXT PRIMARY KEY,
            company_id TEXT NOT NULL REFERENCES companies(company_id),
            full_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            phone TEXT,
            normalized_phone TEXT,
            email TEXT,
            relationship_type TEXT,
            source_ref TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
            case_link TEXT NOT NULL,
            asset_link TEXT NOT NULL,
            framework_link TEXT NOT NULL,
            business_event TEXT NOT NULL,
            UNIQUE(company_id, normalized_name, normalized_phone)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS zubair_prospect_companies (
            prospect_company_id TEXT PRIMARY KEY,
            company_id TEXT NOT NULL REFERENCES companies(company_id),
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            domain TEXT,
            source_ref TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
            case_link TEXT NOT NULL,
            asset_link TEXT NOT NULL,
            framework_link TEXT NOT NULL,
            business_event TEXT NOT NULL,
            UNIQUE(company_id, normalized_name)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS zubair_timeline_events (
            event_id TEXT PRIMARY KEY,
            company_id TEXT NOT NULL REFERENCES companies(company_id),
            draft_id TEXT REFERENCES zubair_capture_drafts(draft_id),
            contact_id TEXT REFERENCES zubair_contacts(contact_id),
            prospect_company_id TEXT REFERENCES zubair_prospect_companies(prospect_company_id),
            opp_id TEXT REFERENCES opportunities(opp_id),
            event_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            actor_id TEXT,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
            case_link TEXT NOT NULL,
            asset_link TEXT NOT NULL,
            framework_link TEXT NOT NULL,
            business_event TEXT NOT NULL
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS zubair_experiment_reviews (
            review_id TEXT PRIMARY KEY,
            company_id TEXT NOT NULL REFERENCES companies(company_id),
            account_id TEXT NOT NULL REFERENCES user_accounts(account_id),
            window_start DATE NOT NULL,
            window_end DATE NOT NULL,
            metrics_json TEXT NOT NULL,
            audit_json TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('stop','modify','pattern')),
            rationale TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            governance_id TEXT NOT NULL REFERENCES gos_governance_registry(governance_id),
            case_link TEXT NOT NULL,
            asset_link TEXT NOT NULL,
            framework_link TEXT NOT NULL,
            business_event TEXT NOT NULL,
            UNIQUE(company_id, window_start, window_end)
        )"""
    )
    for table, index in (
        ("zubair_capture_drafts", "idx_zubair_drafts_company"),
        ("zubair_attachments", "idx_zubair_attachments_company"),
        ("zubair_contacts", "idx_zubair_contacts_company"),
        ("zubair_prospect_companies", "idx_zubair_prospect_companies_company"),
        ("zubair_timeline_events", "idx_zubair_timeline_company"),
        ("zubair_experiment_reviews", "idx_zubair_reviews_company"),
    ):
        db.execute(f"CREATE INDEX IF NOT EXISTS {index} ON {table}(company_id)")


def _unknown_fields(source):
    return {
        key: {"value": None, "classification": "Unknown", "source": source}
        for key in FIELD_KEYS
    }


def _field(value, classification, source):
    value = _text(value)
    classification = classification if classification in ALLOWED_CLASSIFICATIONS else "Unknown"
    if not value:
        return {"value": None, "classification": "Unknown", "source": source}
    return {"value": value, "classification": classification, "source": source}


def _safe_extraction(raw, source, candidate):
    """Accept only values grounded in the input; unknown stays unknown."""
    result = _unknown_fields(source)
    if not isinstance(candidate, dict):
        return result
    raw_folded = _text(raw).casefold()
    for key in FIELD_KEYS:
        item = candidate.get(key)
        if isinstance(item, dict):
            value = item.get("value")
            classification = item.get("classification", "Fact")
        else:
            value, classification = item, "Fact"
        if key in {"amount", "stage", "next_action_due"} and value is not None:
            value = _text(value)
        if key in {"amount", "stage", "next_action_due", "phone"} and value and key != "phone":
            grounded = _text(value).casefold() in raw_folded
        else:
            grounded = not value or _text(value).casefold() in raw_folded
        # Closed outcomes and probability are intentionally not inferred.
        if key == "stage" and value in {"فوز", "خسارة", "won", "lost"}:
            grounded = False
        if grounded:
            result[key] = _field(value, classification, item.get("source", source) if isinstance(item, dict) else source)
    return result


def _heuristic_extract(raw, source):
    """Conservative no-key fallback; every value must be a literal substring."""
    fields = _unknown_fields(source)
    text = _text(raw)
    phone = re.search(r"(?:\+?966|0)?5\d{8}", text.replace(" ", ""))
    if phone:
        fields["phone"] = _field(phone.group(0), "Fact", source)
    company = re.search(r"(?:شركة|مؤسسة)\s+([^\n،,.]+)", text)
    if company:
        fields["prospect_company"] = _field(company.group(1), "Fact", source)
    person = re.search(r"(?:مع|الأستاذ|أ\.|السيد)\s+([^\n،,.]+)", text)
    if person:
        fields["person"] = _field(person.group(1), "Fact", source)
    action = re.search(r"(?:الخطوة التالية|سأقوم بـ|سوف)\s*:?\s*([^\n،.]+)", text)
    if action:
        fields["next_action"] = _field(action.group(1), "Fact", source)
    for label, stage in (
        ("تفاوض", "تفاوض"), ("عرض", "عرض مرسل"), ("مؤهل", "مؤهل"),
        ("عميل محتمل", "عميل محتمل"),
    ):
        if label in text:
            fields["stage"] = _field(stage, "Fact", source)
            break
    amount = re.search(r"(?<!\d)(\d[\d,٬ ]{2,})(?:\s*(?:ريال|ر\.س))", text)
    if amount:
        fields["amount"] = _field(amount.group(1).replace(",", "").replace("٬", "").replace(" ", ""), "Fact", source)
    return fields


def extract_fields(raw, input_type, ask_ai=None):
    source = f"input:{input_type}"
    raw = _text(raw)
    if not raw:
        return _unknown_fields(source)
    if ask_ai:
        prompt = (
            "استخرج فقط ما قيل حرفيًا من النص التالي. أعد JSON object فقط، "
            "بالمفاتيح: " + ",".join(FIELD_KEYS) +
            ". كل قيمة object بالشكل {value,classification,source}. "
            "classification أحد Fact أو Evidence أو Inference أو Unknown. "
            "لا تخترع رقمًا أو اسمًا أو مرحلة فوز/خسارة أو احتمالية. "
            "غير المذكور value=null وclassification=Unknown. النص:\n" + raw
        )
        response = ask_ai(
            "أنت مستخرج حقائق محافظ لتجربة Zubair Deal Brain. لا تقدم توصية ولا قرارًا تجاريًا.",
            prompt,
        )
        try:
            if response.get("raw_text"):
                candidate = json.loads(response["raw_text"])
                return _safe_extraction(raw, source, candidate)
        except (AttributeError, TypeError, json.JSONDecodeError):
            pass
    return _heuristic_extract(raw, source)


def input_hash(input_type, raw_text, attachments):
    digest = hashlib.sha256()
    digest.update(_text(input_type).encode())
    digest.update(b"\0")
    digest.update(_text(raw_text).encode())
    for item in attachments or []:
        digest.update(_text(item.get("filename")).encode())
        digest.update(item.get("content") or b"")
    return digest.hexdigest()


def _row_dict(row):
    return dict(row) if row else None


def get_draft(db, company_id, draft_id):
    row = db.execute(
        "SELECT * FROM zubair_capture_drafts WHERE draft_id=? AND company_id=?",
        (draft_id, company_id),
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    data["fields"] = json.loads(data.pop("extracted_json") or "{}")
    data["attachments"] = [
        dict(item) for item in db.execute(
            """SELECT attachment_id, filename, mime_type, evidence_id, created_at
               FROM zubair_attachments WHERE draft_id=? ORDER BY created_at""",
            (draft_id,),
        ).fetchall()
    ]
    return data


def create_draft(db, company_id, account_id, input_type, raw_text, attachments, extractor):
    started = datetime.utcnow()
    digest = input_hash(input_type, raw_text, attachments)
    old = db.execute(
        "SELECT draft_id FROM zubair_capture_drafts WHERE company_id=? AND input_hash=?",
        (company_id, digest),
    ).fetchone()
    if old:
        return get_draft(db, company_id, old["draft_id"]), True
    fields = extract_fields(raw_text, input_type, extractor)
    draft_id = _id("ZDB")
    elapsed = max(0, int((datetime.utcnow() - started).total_seconds() * 1000))
    inserted = db.execute(
        f"""INSERT INTO zubair_capture_drafts
            (draft_id,company_id,account_id,input_hash,input_type,raw_text,
             extracted_json,status,processing_ms,{_governance_columns()})
            VALUES (?,?,?,?,?,?,?,'review',?,?,?,?,?,?)
            ON CONFLICT (company_id,input_hash) DO NOTHING
            RETURNING draft_id""",
        (
            draft_id, company_id, account_id, digest, input_type, _text(raw_text),
            json.dumps(fields, ensure_ascii=False), elapsed,
            GOVERNANCE_ID, NA, NA, "framework:B2B-OS-001",
            "zubair.capture.created",
        ),
    ).fetchone()
    if not inserted:
        old = db.execute(
            "SELECT draft_id FROM zubair_capture_drafts WHERE company_id=? AND input_hash=?",
            (company_id, digest),
        ).fetchone()
        return get_draft(db, company_id, old["draft_id"]), True
    for item in attachments or []:
        db.execute(
            f"""INSERT INTO zubair_attachments
                (attachment_id,draft_id,company_id,filename,mime_type,content,{_governance_columns()})
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                _id("ZATT"), draft_id, company_id, item["filename"],
                item.get("mime_type"), item.get("content"),
                GOVERNANCE_ID, NA, NA, "framework:B2B-OS-001",
                "zubair.attachment.received",
            ),
        )
    return get_draft(db, company_id, draft_id), False


def _matching_rows(db, company_id, fields):
    person = fields.get("person", {}).get("value")
    phone = fields.get("phone", {}).get("value")
    company = fields.get("prospect_company", {}).get("value")
    opportunity = fields.get("opportunity", {}).get("value")
    person_key, phone_key, company_key = normalize_identity(person), normalize_phone(phone), normalize_identity(company)
    if phone_key:
        contact_rows = db.execute(
            """SELECT * FROM zubair_contacts WHERE company_id=?
               AND normalized_phone=? ORDER BY created_at""",
            (company_id, phone_key),
        ).fetchall()
    else:
        contact_rows = db.execute(
            """SELECT * FROM zubair_contacts WHERE company_id=?
               AND normalized_name=? ORDER BY created_at""",
            (company_id, person_key or "__none__"),
        ).fetchall()
    contacts = [dict(row) for row in contact_rows]
    if not contacts and person_key:
        all_contacts = db.execute("SELECT * FROM zubair_contacts WHERE company_id=?", (company_id,)).fetchall()
        contacts = [dict(row) for row in all_contacts if person_key in row["normalized_name"] or row["normalized_name"] in person_key]
    companies = [
        dict(row) for row in db.execute(
            """SELECT * FROM zubair_prospect_companies
               WHERE company_id=? AND normalized_name=? ORDER BY created_at""",
            (company_id, company_key or "__none__"),
        ).fetchall()
    ]
    if not companies and company_key:
        rows = db.execute("SELECT * FROM zubair_prospect_companies WHERE company_id=?", (company_id,)).fetchall()
        companies = [dict(row) for row in rows if company_key in row["normalized_name"] or row["normalized_name"] in company_key]
    opp_rows = db.execute(
        """SELECT o.*, l.name AS lead_name, l.company_name AS lead_company_name
           FROM opportunities o LEFT JOIN leads l ON l.lead_id=o.lead_id
           WHERE o.company_id=? AND o.archived=0 ORDER BY o.created_at DESC""",
        (company_id,),
    ).fetchall()
    opps = []
    opp_key = normalize_identity(opportunity)
    for row in opp_rows:
        hay = " ".join(filter(None, (row["title"], row["lead_name"], row["lead_company_name"])))
        hay_key = normalize_identity(hay)
        if opp_key and (opp_key in hay_key or hay_key in opp_key):
            opps.append(dict(row))
        elif company_key and company_key in normalize_identity(row["lead_company_name"]):
            opps.append(dict(row))
    return {
        "contact": contacts,
        "prospect_company": companies,
        "opportunity": opps,
        "match_status": "ambiguous" if any(len(items) > 1 for items in (contacts, companies, opps)) else "matched",
    }


def match_draft(db, company_id, draft_id):
    draft = get_draft(db, company_id, draft_id)
    if not draft:
        raise LookupError("DRAFT_NOT_FOUND")
    result = _matching_rows(db, company_id, draft["fields"])
    result["draft_id"] = draft_id
    result["has_exact_match"] = any(
        len(result[key]) == 1 for key in ("contact", "prospect_company", "opportunity")
    )
    return result


def _value(fields, key):
    item = fields.get(key) or {}
    return _text(item.get("value")) if isinstance(item, dict) else _text(item)


def _confirmed_fields(draft_fields, edits):
    fields = json.loads(json.dumps(draft_fields, ensure_ascii=False))
    for key, value in (edits or {}).items():
        if key not in FIELD_KEYS:
            continue
        if isinstance(value, dict):
            value = value.get("value")
        value = _text(value)
        fields[key] = _field(value, "Fact", "founder-confirmed") if value else {
            "value": None, "classification": "Unknown", "source": "founder-confirmed"
        }
    # Never allow commercial outcomes/probability to be written by extraction.
    if _value(fields, "stage") in {"فوز", "خسارة", "won", "lost"}:
        fields["stage"] = {"value": None, "classification": "Unknown", "source": "founder-confirmed"}
    return fields


def _insert_timeline(db, company_id, *, event_type, summary, source_ref, account_id,
                     draft_id=None, contact_id=None, prospect_company_id=None,
                     opp_id=None, payload=None):
    db.execute(
        f"""INSERT INTO zubair_timeline_events
            (event_id,company_id,draft_id,contact_id,prospect_company_id,opp_id,
             event_type,summary,source_ref,payload_json,actor_id,{_governance_columns()})
            VALUES (?,?,?,?,?,?,?,?,?,?,?, ?,?,?,?,?)""",
        (
            _id("ZTL"), company_id, draft_id, contact_id, prospect_company_id, opp_id,
            event_type, summary, source_ref, json.dumps(payload or {}, ensure_ascii=False),
            account_id, GOVERNANCE_ID, NA, NA, "framework:B2B-OS-001",
            f"zubair.timeline.{event_type}",
        ),
    )


def _ensure_contact(db, company_id, fields, source_ref, selected_id=None):
    name, phone = _value(fields, "person"), _value(fields, "phone")
    if selected_id:
        row = db.execute(
            "SELECT * FROM zubair_contacts WHERE contact_id=? AND company_id=? FOR UPDATE",
            (selected_id, company_id),
        ).fetchone()
        if not row:
            raise ValueError("CONTACT_SELECTION_INVALID")
    elif not name:
        return None, False
    else:
        if normalize_phone(phone):
            row = db.execute(
                """SELECT * FROM zubair_contacts WHERE company_id=?
                   AND normalized_phone=? LIMIT 1 FOR UPDATE""",
                (company_id, normalize_phone(phone)),
            ).fetchone()
        else:
            row = db.execute(
                """SELECT * FROM zubair_contacts WHERE company_id=?
                   AND normalized_name=? LIMIT 1 FOR UPDATE""",
                (company_id, normalize_identity(name)),
            ).fetchone()
    if row:
        db.execute(
            """UPDATE zubair_contacts SET full_name=COALESCE(NULLIF(?,''),full_name),
               phone=COALESCE(NULLIF(?,''),phone),
               normalized_phone=COALESCE(NULLIF(?,''),normalized_phone),
               relationship_type=COALESCE(NULLIF(?,''),relationship_type),
               updated_at=now() WHERE contact_id=? AND company_id=?""",
            (name, phone, normalize_phone(phone), _value(fields, "relationship_type"),
             row["contact_id"], company_id),
        )
        return row["contact_id"], False
    contact_id = _id("ZC")
    db.execute(
        f"""INSERT INTO zubair_contacts
            (contact_id,company_id,full_name,normalized_name,phone,normalized_phone,
             relationship_type,source_ref,{_governance_columns()})
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            contact_id, company_id, name, normalize_identity(name), phone or None,
            normalize_phone(phone) or None, _value(fields, "relationship_type"),
            source_ref, GOVERNANCE_ID, NA, NA, "framework:B2B-OS-001",
            "zubair.contact.created",
        ),
    )
    return contact_id, True


def _ensure_prospect_company(db, company_id, fields, source_ref, selected_id=None):
    name = _value(fields, "prospect_company")
    if selected_id:
        row = db.execute(
            "SELECT * FROM zubair_prospect_companies WHERE prospect_company_id=? AND company_id=? FOR UPDATE",
            (selected_id, company_id),
        ).fetchone()
        if not row:
            raise ValueError("PROSPECT_COMPANY_SELECTION_INVALID")
    elif not name:
        return None, False
    else:
        row = db.execute(
            """SELECT * FROM zubair_prospect_companies
               WHERE company_id=? AND normalized_name=? LIMIT 1 FOR UPDATE""",
            (company_id, normalize_identity(name)),
        ).fetchone()
    if row:
        db.execute(
            "UPDATE zubair_prospect_companies SET name=COALESCE(NULLIF(?,''),name), updated_at=now() WHERE prospect_company_id=? AND company_id=?",
            (name, row["prospect_company_id"], company_id),
        )
        return row["prospect_company_id"], False
    prospect_id = _id("ZPC")
    db.execute(
        f"""INSERT INTO zubair_prospect_companies
            (prospect_company_id,company_id,name,normalized_name,source_ref,{_governance_columns()})
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            prospect_id, company_id, name, normalize_identity(name), source_ref,
            GOVERNANCE_ID, NA, NA, "framework:B2B-OS-001",
            "zubair.prospect_company.created",
        ),
    )
    return prospect_id, True


def _ensure_lead(db, company_id, fields, contact_id, prospect_company_id, source_ref):
    name, company = _value(fields, "person"), _value(fields, "prospect_company")
    if not name:
        return None
    row = db.execute(
        """SELECT lead_id FROM leads WHERE company_id=? AND
           (lower(name)=lower(?) OR (? <> '' AND phone=?)) LIMIT 1""",
        (company_id, name, normalize_phone(_value(fields, "phone")), _value(fields, "phone")),
    ).fetchone()
    if row:
        return row["lead_id"]
    lead_id = _id("L")
    db.execute(
        """INSERT INTO leads
           (lead_id,company_id,name,company_name,phone,source,status,notes)
           VALUES (?,?,?,?,?,?,?,?)""",
        (lead_id, company_id, name, company or None, _value(fields, "phone") or None,
         source_ref, "جديد", f"Zubair Deal Brain contact_id={contact_id or NA}; prospect_company_id={prospect_company_id or NA}"),
    )
    return lead_id


def _parse_amount(value):
    if not value:
        return None
    try:
        return Decimal(re.sub(r"[^\d.]", "", value))
    except (InvalidOperation, ValueError):
        return None


def confirm_draft(db, company_id, account_id, draft_id, edits=None, selections=None,
                  create_opportunity=False, attach_evidence=True):
    locked = db.execute(
        """SELECT * FROM zubair_capture_drafts
           WHERE draft_id=? AND company_id=? FOR UPDATE""",
        (draft_id, company_id),
    ).fetchone()
    if not locked:
        raise LookupError("DRAFT_NOT_FOUND")
    if locked["status"] == "confirmed":
        return {
            "already_confirmed": True,
            "draft": get_draft(db, company_id, draft_id),
        }
    draft = dict(locked)
    draft["fields"] = json.loads(draft.pop("extracted_json") or "{}")
    fields = _confirmed_fields(draft["fields"], edits)
    matches = _matching_rows(db, company_id, fields)
    selections = selections or {}
    for key in ("contact", "prospect_company", "opportunity"):
        if len(matches[key]) > 1 and not selections.get(key):
            raise ValueError("MATCH_SELECTION_REQUIRED")
    source_ref = f"zubair:{draft_id}"
    contact_id, contact_created = _ensure_contact(
        db, company_id, fields, source_ref, selections.get("contact")
    )
    prospect_id, prospect_created = _ensure_prospect_company(
        db, company_id, fields, source_ref, selections.get("prospect_company")
    )
    lead_id = _ensure_lead(db, company_id, fields, contact_id, prospect_id, source_ref)

    opp_id = selections.get("opportunity")
    opp = None
    if opp_id:
        opp = db.execute(
            "SELECT * FROM opportunities WHERE opp_id=? AND company_id=? AND archived=0 FOR UPDATE",
            (opp_id, company_id),
        ).fetchone()
        if not opp:
            raise ValueError("OPPORTUNITY_SELECTION_INVALID")
    elif len(matches["opportunity"]) == 1:
        opp = db.execute(
            "SELECT * FROM opportunities WHERE opp_id=? AND company_id=? FOR UPDATE",
            (matches["opportunity"][0]["opp_id"], company_id),
        ).fetchone()
    title = _value(fields, "opportunity")
    if not opp and create_opportunity and title:
        opp_id = _id("OPP")
        stage = _value(fields, "stage") if _value(fields, "stage") in ACTIVE_STAGES else "عميل محتمل"
        db.execute(
            """INSERT INTO opportunities
               (opp_id,company_id,lead_id,title,stage,amount,next_action,next_action_due,owner_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                opp_id, company_id, lead_id, title, stage, _parse_amount(_value(fields, "amount")),
                _value(fields, "next_action") or None, _value(fields, "next_action_due") or None,
                account_id,
            ),
        )
        db.execute(
            """INSERT INTO sales_stage_history
               (history_id,opp_id,company_id,from_stage,to_stage,changed_by)
               VALUES (?,?,?,NULL,?,?)""",
            (_id("SSH"), opp_id, company_id, stage, account_id),
        )
        opp = db.execute("SELECT * FROM opportunities WHERE opp_id=?", (opp_id,)).fetchone()
        try:
            from sana_revenue_cycle import record_legacy_transition
            record_legacy_transition(db, dict(opp), stage, account_id, reason=None)
        except Exception:
            pass
        _insert_timeline(
            db, company_id, event_type="opportunity_created",
            summary=f"أُنشئت الفرصة: {title}",
            source_ref=source_ref, account_id=account_id, draft_id=draft_id, opp_id=opp_id,
        )
    if opp:
        opp_id = opp["opp_id"]
        old_stage = opp["stage"]
        was_stale = (
            not opp["next_action_due"]
            or str(opp["next_action_due"])[:10] < date.today().isoformat()
        )
        next_stage = _value(fields, "stage")
        updates = {
            "amount": _parse_amount(_value(fields, "amount")),
            "next_action": _value(fields, "next_action") or None,
            "next_action_due": _value(fields, "next_action_due") or None,
        }
        sets, params = [], []
        for key, value in updates.items():
            if value is not None:
                sets.append(f"{key}=?")
                params.append(value)
        if next_stage in ACTIVE_STAGES and next_stage != old_stage:
            sets.append("stage=?")
            params.append(next_stage)
        if sets:
            params.extend([opp_id, company_id])
            db.execute(
                f"UPDATE opportunities SET {', '.join(sets)}, updated_at=now() WHERE opp_id=? AND company_id=?",
                tuple(params),
            )
        if next_stage in ACTIVE_STAGES and next_stage != old_stage:
            db.execute(
                """INSERT INTO sales_stage_history
                   (history_id,opp_id,company_id,from_stage,to_stage,changed_by)
                   VALUES (?,?,?,?,?,?)""",
                (_id("SSH"), opp_id, company_id, old_stage, next_stage, account_id),
            )
            _insert_timeline(
                db, company_id, event_type="stage_change",
                summary=f"تغيرت المرحلة من {old_stage} إلى {next_stage}",
                source_ref=source_ref, account_id=account_id, draft_id=draft_id, opp_id=opp_id,
                payload={"from": old_stage, "to": next_stage},
            )
        context_values = (
            _value(fields, "need"), _value(fields, "decision_maker"),
            _value(fields, "objection"),
        )
        if any(context_values):
            economics = db.execute(
                "SELECT opp_id FROM rc_opportunity_economics WHERE opp_id=? AND company_id=?",
                (opp_id, company_id),
            ).fetchone()
            if economics:
                db.execute(
                    """UPDATE rc_opportunity_economics
                       SET customer_problem=COALESCE(NULLIF(?,''),customer_problem),
                           decision_maker=COALESCE(NULLIF(?,''),decision_maker),
                           objection=COALESCE(NULLIF(?,''),objection),
                           source_ref=?, observed_at=?, classification='Fact', updated_at=now()
                       WHERE opp_id=? AND company_id=?""",
                    (
                        context_values[0], context_values[1], context_values[2],
                        source_ref, date.today().isoformat(), opp_id, company_id,
                    ),
                )
            else:
                db.execute(
                    """INSERT INTO rc_opportunity_economics
                       (opp_id,company_id,customer_problem,decision_maker,objection,
                        source_ref,observed_at,classification)
                       VALUES (?,?,?,?,?,?,?,'Fact')""",
                    (
                        opp_id, company_id, context_values[0] or None,
                        context_values[1] or None, context_values[2] or None,
                        source_ref, date.today().isoformat(),
                    ),
                )
        if was_stale and _value(fields, "next_action") and _value(fields, "next_action_due"):
            _insert_timeline(
                db, company_id, event_type="opportunity_reactivated",
                summary=f"أُعيدت متابعة الفرصة: {opp['title']}",
                source_ref=source_ref, account_id=account_id, draft_id=draft_id, opp_id=opp_id,
                payload={"next_action_due": _value(fields, "next_action_due")},
            )

    if _text(draft["raw_text"]):
        activity_id = _id("ACT")
        db.execute(
            """INSERT INTO sales_activities
               (activity_id,company_id,opp_id,type,subject,notes,occurred_at,actor_id)
               VALUES (?,?,?,?,?,?,now(),?)""",
            (activity_id, company_id, opp_id, "ملاحظة",
             "Zubair Deal Brain", draft["raw_text"], account_id),
        )
        _insert_timeline(
            db, company_id, event_type="interaction", summary=draft["raw_text"][:500],
            source_ref=source_ref, account_id=account_id, draft_id=draft_id,
            contact_id=contact_id, prospect_company_id=prospect_id, opp_id=opp_id,
        )
    if opp_id and _value(fields, "next_action") and _value(fields, "next_action_due"):
        exists = db.execute(
            """SELECT task_id FROM tasks WHERE company_id=? AND title=? AND due_date=?
               AND status IN ('لم تبدأ','قيد التنفيذ','متوقفة') LIMIT 1""",
            (company_id, _value(fields, "next_action"), _value(fields, "next_action_due")),
        ).fetchone()
        if not exists:
            task_id = _id("ZTASK")
            db.execute(
                """INSERT INTO tasks (task_id,company_id,title,owner_user_id,due_date,status,priority)
                   VALUES (?,?,?,?,?,'لم تبدأ','متوسطة')""",
                (task_id, company_id, _value(fields, "next_action"),
                 account_id,
                 _value(fields, "next_action_due")),
            )
            _insert_timeline(
                db, company_id, event_type="task_created",
                summary=f"متابعة: {_value(fields, 'next_action')}",
                source_ref=source_ref, account_id=account_id, draft_id=draft_id, opp_id=opp_id,
                payload={"task_id": task_id, "due": _value(fields, "next_action_due")},
            )
    attached = []
    if attach_evidence:
        for item in db.execute(
            "SELECT attachment_id,filename FROM zubair_attachments WHERE draft_id=? AND evidence_id IS NULL",
            (draft_id,),
        ).fetchall():
            evidence_id = _id("EV")
            db.execute(
                """INSERT INTO evidence
                   (evidence_id,company_id,title,source_type,confidence,evidence_type,source_ref)
                   VALUES (?,?,?,'Zubair attachment',100,'Evidence',?)""",
                (evidence_id, company_id, item["filename"], source_ref),
            )
            db.execute(
                "UPDATE zubair_attachments SET evidence_id=? WHERE attachment_id=? AND draft_id=?",
                (evidence_id, item["attachment_id"], draft_id),
            )
            attached.append({"attachment_id": item["attachment_id"], "evidence_id": evidence_id})
            _insert_timeline(
                db, company_id, event_type="evidence_attached",
                summary=f"أُرفق الدليل: {item['filename']}",
                source_ref=source_ref, account_id=account_id, draft_id=draft_id, opp_id=opp_id,
                payload={"evidence_id": evidence_id},
            )
    db.execute(
        "UPDATE zubair_capture_drafts SET extracted_json=?,status='confirmed',confirmed_at=now() WHERE draft_id=? AND company_id=?",
        (json.dumps(fields, ensure_ascii=False), draft_id, company_id),
    )
    _insert_timeline(
        db, company_id, event_type="capture_confirmed",
        summary="تم تأكيد مسودة Zubair Deal Brain",
        source_ref=source_ref, account_id=account_id, draft_id=draft_id,
        contact_id=contact_id, prospect_company_id=prospect_id, opp_id=opp_id,
        payload={"contact_created": contact_created, "prospect_company_created": prospect_created},
    )
    return {
        "draft_id": draft_id, "contact_id": contact_id,
        "prospect_company_id": prospect_id, "opp_id": opp_id,
        "attached_evidence": attached, "fields": fields,
    }


def attention_items(db, company_id):
    rows = db.execute(
        """SELECT o.*, l.name AS contact_name, l.company_name
           FROM opportunities o LEFT JOIN leads l ON l.lead_id=o.lead_id
           WHERE o.company_id=? AND o.archived=0
             AND (o.stage NOT IN ('فوز','خسارة')
                  OR o.revenue_stage_id IN ('delivery','collection','retention_upsell'))""",
        (company_id,),
    ).fetchall()
    today = date.today().isoformat()
    result = []
    for row in rows:
        item = dict(row)
        due = _text(row["next_action_due"])
        if not due or due < today:
            category, rank, rule = "تدخل عاجل", 0, "فرصة مفتوحة بلا متابعة أو بموعد متابعة متأخر"
        elif row["revenue_stage_id"] in {"collection", "delivery"} or row["stage"] == "فوز":
            category, rank, rule = "الأقرب للتحصيل", 1, "مرحلة دورة الإيراد تنفيذ/تحصيل مؤكدة"
        elif due == today:
            category, rank, rule = "تدخل عاجل", 0, "موعد المتابعة اليوم"
        else:
            category, rank, rule = "لا يستحق الوقت الآن", 3, "المتابعة المستقبلية ليست مستحقة اليوم"
        item.update({
            "category": category, "rank": rank,
            "rule": rule,
            "facts": [
                f"المرحلة: {row['stage']}",
                f"موعد المتابعة: {due or NA}",
                f"القيمة المسجلة: {row['amount'] if row['amount'] is not None else NA}",
            ],
            "evidence": "مصدر السجل: opportunities" if row["opp_id"] else NA,
        })
        result.append(item)
    result.sort(key=lambda x: (x["rank"], _text(x["next_action_due"]) or "0000-00-00", x["opp_id"]))
    return result


def metrics(db, company_id):
    count = lambda sql: db.execute(sql, (company_id,)).fetchone()["n"]
    drafts = count(
        """SELECT COUNT(*) AS n FROM zubair_capture_drafts
           WHERE company_id=? AND created_at >= now() - interval '30 days'"""
    )
    confirmed = count(
        """SELECT COUNT(*) AS n FROM zubair_capture_drafts
           WHERE company_id=? AND status='confirmed'
             AND confirmed_at >= now() - interval '30 days'"""
    )
    interactions = count(
        """SELECT COUNT(*) AS n FROM zubair_timeline_events
           WHERE company_id=? AND event_type='interaction'
             AND occurred_at >= now() - interval '30 days'"""
    )
    opps = count(
        """SELECT COUNT(DISTINCT opp_id) AS n FROM zubair_timeline_events
           WHERE company_id=? AND opp_id IS NOT NULL
             AND event_type='opportunity_created'
             AND occurred_at >= now() - interval '30 days'"""
    )
    wins = db.execute(
        """SELECT COUNT(DISTINCT o.opp_id) AS n
           FROM opportunities o JOIN zubair_timeline_events z ON z.opp_id=o.opp_id
           WHERE z.company_id=? AND o.stage='فوز'
             AND z.occurred_at >= now() - interval '30 days'""",
        (company_id,),
    ).fetchone()["n"]
    losses = db.execute(
        """SELECT COUNT(DISTINCT o.opp_id) AS n
           FROM opportunities o JOIN zubair_timeline_events z ON z.opp_id=o.opp_id
           WHERE z.company_id=? AND o.stage='خسارة'
             AND z.occurred_at >= now() - interval '30 days'""",
        (company_id,),
    ).fetchone()["n"]
    prevented = count(
        """SELECT COUNT(*) AS n FROM zubair_timeline_events
           WHERE company_id=? AND event_type='task_created'
             AND occurred_at >= now() - interval '30 days'"""
    )
    reactivated = count(
        """SELECT COUNT(*) AS n FROM zubair_timeline_events
           WHERE company_id=? AND event_type='opportunity_reactivated'
             AND occurred_at >= now() - interval '30 days'"""
    )
    revenue = db.execute(
        """SELECT SUM(e.recognized_revenue) AS v
           FROM rc_opportunity_economics e
           WHERE e.company_id=? AND e.classification='Fact'
             AND e.source_ref LIKE 'zubair:%%'
             AND e.observed_at >= CURRENT_DATE - 30""",
        (company_id,),
    ).fetchone()["v"]
    capture_time = db.execute(
        """SELECT AVG(processing_ms) AS v FROM zubair_capture_drafts
           WHERE company_id=? AND created_at >= now() - interval '30 days'""",
        (company_id,),
    ).fetchone()["v"]
    return {
        "window_days": 30,
        "window_label": "آخر 30 يومًا",
        "entries": drafts,
        "confirmed_entries": confirmed,
        "interactions": interactions,
        "opportunities_created": opps,
        "won": wins,
        "lost": losses,
        "overdue_followups_prevented": prevented,
        "saved_followups": prevented,
        "reactivated_opportunities": reactivated,
        "real_revenue_attributed": revenue if revenue is not None else NA,
        "capture_time_ms_avg": capture_time if capture_time is not None else NA,
        "recording_time_ms_avg": capture_time if capture_time is not None else NA,
        "rollout": "private-beta-only",
        "pattern_promotion": "manual review required",
    }


def _review_window():
    window_end = date.today()
    return window_end - timedelta(days=30), window_end


def _review_match_summary(result):
    def contact(item):
        return {
            "contact_id": item.get("contact_id"),
            "name": item.get("full_name"),
            "phone": item.get("phone"),
        }

    def prospect(item):
        return {
            "prospect_company_id": item.get("prospect_company_id"),
            "name": item.get("name"),
        }

    def opportunity(item):
        return {
            "opp_id": item.get("opp_id"),
            "title": item.get("title"),
            "stage": item.get("stage"),
            "amount": item.get("amount"),
        }

    return {
        "contact": [contact(item) for item in result["contact"]],
        "prospect_company": [prospect(item) for item in result["prospect_company"]],
        "opportunity": [opportunity(item) for item in result["opportunity"]],
        "match_status": result["match_status"],
    }


def _review_opportunity_links(db, company_id, window_start, window_end):
    """Return confirmed Deal Brain provenance for opportunities in a window.

    A matching opportunity name is not evidence that Deal Brain was used.
    Only timeline events carrying both the tenant-scoped draft and opportunity
    foreign keys establish that provenance.
    """
    rows = db.execute(
        """SELECT z.opp_id, z.draft_id, z.event_type, z.source_ref,
                  z.occurred_at, d.created_at AS draft_created_at
           FROM zubair_timeline_events z
           JOIN zubair_capture_drafts d
             ON d.draft_id=z.draft_id AND d.company_id=z.company_id
           WHERE z.company_id=? AND z.opp_id IS NOT NULL
             AND d.created_at >= ?::date
             AND d.created_at < (?::date + interval '1 day')
           ORDER BY z.opp_id, d.created_at ASC, z.occurred_at ASC, z.event_id ASC""",
        (company_id, window_start, window_end),
    ).fetchall()
    links = {}
    for row in rows:
        item = dict(row)
        opportunity_links = links.setdefault(item["opp_id"], [])
        link = next(
            (
                existing for existing in opportunity_links
                if existing["draft_id"] == item["draft_id"]
            ),
            None,
        )
        if link is None:
            link = {
                "draft_id": item["draft_id"],
                "event_type": item["event_type"],
                "event_types": [],
                "source_ref": item["source_ref"],
                "occurred_at": item["occurred_at"],
                "draft_created_at": item["draft_created_at"],
            }
            opportunity_links.append(link)
        if item["event_type"] not in link["event_types"]:
            link["event_types"].append(item["event_type"])
    return links


def _review_sample(db, company_id, sample_limit=5, window_start=None, window_end=None):
    if window_start is None or window_end is None:
        window_start, window_end = _review_window()
    rows = db.execute(
        """SELECT draft_id, input_type, raw_text, extracted_json, status,
                  processing_ms, created_at, confirmed_at
           FROM zubair_capture_drafts
           WHERE company_id=? AND created_at >= ?::date
             AND created_at < (?::date + interval '1 day')
           ORDER BY created_at ASC, draft_id ASC LIMIT ?""",
        (company_id, window_start, window_end, sample_limit),
    ).fetchall()
    links_by_opp = _review_opportunity_links(
        db, company_id, window_start, window_end
    )
    recommendations = []
    for item in attention_items(db, company_id):
        links = links_by_opp.get(item.get("opp_id"))
        if not links:
            continue
        recommendation = dict(item)
        recommendation["deal_brain_links"] = links
        recommendations.append(recommendation)
        if len(recommendations) >= sample_limit:
            break
    result = []
    for row in rows:
        draft = dict(row)
        fields = json.loads(draft.pop("extracted_json") or "{}")
        draft["fields"] = fields
        draft["match"] = _review_match_summary(_matching_rows(db, company_id, fields))
        draft["recommendations"] = [
            item for item in recommendations
            if any(
                link.get("draft_id") == draft["draft_id"]
                for link in item.get("deal_brain_links", [])
            )
        ]
        result.append(draft)
    return {"drafts": result, "recommendations": recommendations}


def latest_review(db, company_id):
    row = db.execute(
        """SELECT review_id, company_id, account_id, window_start, window_end,
                  metrics_json, audit_json, decision, rationale, created_at
           FROM zubair_experiment_reviews
           WHERE company_id=?
           ORDER BY created_at DESC, review_id DESC LIMIT 1""",
        (company_id,),
    ).fetchone()
    if not row:
        return None
    review = dict(row)
    review["metrics"] = json.loads(review.pop("metrics_json") or "{}")
    review["audit"] = json.loads(review.pop("audit_json") or "{}")
    review["rollout"] = "private-beta-only"
    review["access_scope_changed"] = False
    return review


def review_packet(db, company_id, sample_limit=5):
    """Return the evidence packet; it never promotes or changes access."""
    window_start, window_end = _review_window()
    sample = _review_sample(
        db, company_id, sample_limit, window_start=window_start, window_end=window_end
    )
    return {
        "window_days": 30,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "metrics": metrics(db, company_id),
        "audit_sample": sample,
        "latest_review": latest_review(db, company_id),
        "review_required": True,
        "rollout": "private-beta-only",
        "access_scope": "founder-and-authorized-admin-only",
        "automatic_pattern_promotion": False,
    }


def _valid_review_item(item, required):
    if not isinstance(item, dict):
        return False
    return all(item.get(key) in REVIEW_COMPLETE_VALUES for key in required)


def save_review(db, company_id, account_id, decision, rationale, audit,
                window_start=None, window_end=None):
    """Persist exactly one explicit founder/admin decision for a 30-day window."""
    decision = _text(decision).lower()
    rationale = _text(rationale)
    if decision not in REVIEW_DECISIONS:
        raise ValueError("INVALID_REVIEW_DECISION")
    if not rationale:
        raise ValueError("REVIEW_RATIONALE_REQUIRED")
    if audit is None:
        raise ValueError("REVIEW_AUDIT_REQUIRED")
    audit = audit if isinstance(audit, dict) else {}
    window_default_start, window_default_end = _review_window()
    try:
        start = date.fromisoformat(_text(window_start)) if window_start else window_default_start
        end = date.fromisoformat(_text(window_end)) if window_end else window_default_end
    except ValueError as exc:
        raise ValueError("INVALID_REVIEW_WINDOW") from exc
    if (end - start).days != 30 or end > date.today() or start >= end:
        raise ValueError("INVALID_REVIEW_WINDOW")

    samples = _review_sample(
        db, company_id, 5, window_start=start, window_end=end
    )
    draft_ids = {item["draft_id"] for item in samples["drafts"]}
    submitted_drafts = audit.get("drafts") or []
    if draft_ids:
        submitted_ids = {
            item.get("draft_id") for item in submitted_drafts if isinstance(item, dict)
        }
        if submitted_ids != draft_ids:
            raise ValueError("REVIEW_DRAFT_SAMPLE_INCOMPLETE")
    recommendation_ids = {
        item.get("opp_id") for item in samples["recommendations"] if item.get("opp_id")
    }
    submitted_recommendations = audit.get("recommendations") or []
    submitted_ids = {
        item.get("opp_id")
        for item in submitted_recommendations
        if isinstance(item, dict) and item.get("opp_id")
    }
    if (
        submitted_ids != recommendation_ids
        or len(submitted_ids) != len(submitted_recommendations)
    ):
        raise ValueError("REVIEW_RECOMMENDATION_SAMPLE_INCOMPLETE")
    for item in submitted_drafts:
        if not _valid_review_item(
            item, ("draft_accuracy", "match_accuracy", "no_fabrication")
        ):
            raise ValueError("REVIEW_DRAFT_AUDIT_REQUIRED")
        if item.get("recommendation_accuracy") is not None and (
            item.get("recommendation_accuracy") not in REVIEW_COMPLETE_VALUES
        ):
            raise ValueError("REVIEW_RECOMMENDATION_AUDIT_INVALID")
    for item in submitted_recommendations:
        if not _valid_review_item(item, ("accuracy", "no_fabrication")):
            raise ValueError("REVIEW_RECOMMENDATION_AUDIT_REQUIRED")

    review_id = _id("ZREV")
    packet_metrics = metrics(db, company_id)
    try:
        row = db.execute(
            f"""INSERT INTO zubair_experiment_reviews
                (review_id,company_id,account_id,window_start,window_end,
                 metrics_json,audit_json,decision,rationale,{_governance_columns()})
                VALUES (?,?,?,?,?,?,?,?,?, ?,?,?,?,?)
                ON CONFLICT (company_id,window_start,window_end)
                DO UPDATE SET account_id=EXCLUDED.account_id,
                    metrics_json=EXCLUDED.metrics_json,
                    audit_json=EXCLUDED.audit_json,
                    decision=EXCLUDED.decision,
                    rationale=EXCLUDED.rationale,
                    created_at=now()
                RETURNING review_id""",
            (
                review_id, company_id, account_id, start, end,
                json.dumps(packet_metrics, ensure_ascii=False, default=str),
                json.dumps(audit, ensure_ascii=False, default=str),
                decision, rationale, GOVERNANCE_ID, NA, NA,
                "framework:B2B-OS-001", "zubair.experiment.reviewed",
            ),
        ).fetchone()
    except Exception:
        raise
    review_id = row["review_id"]
    _insert_timeline(
        db, company_id, event_type="experiment_reviewed",
        summary=f"تم توثيق مراجعة Deal Brain بقرار: {decision}",
        source_ref=f"zubair:review:{review_id}", account_id=account_id,
        payload={
            "decision": decision,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "rollout": "private-beta-only",
        },
    )
    review = latest_review(db, company_id)
    review["new_review"] = True
    return review


def timeline(db, company_id, opp_id=None):
    if opp_id:
        rows = db.execute(
            """SELECT * FROM zubair_timeline_events WHERE company_id=? AND opp_id=?
               ORDER BY occurred_at DESC,event_id DESC""",
            (company_id, opp_id),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT * FROM zubair_timeline_events WHERE company_id=?
               ORDER BY occurred_at DESC,event_id DESC LIMIT 100""",
            (company_id,),
        ).fetchall()
    return [dict(row) for row in rows]
