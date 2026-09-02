"""دورة بحث معرفي دورية محافظة؛ الاكتشاف لا يعني الاعتماد أو النشر."""
import difflib
import hashlib
import http.client
import json
import ipaddress
import os
import re
import socket
import ssl
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from database_config import acquire_schema_lock

LOCK_KEY = 813_260_930
_SCHEMA_READY = False
USER_AGENT = "SanaKnowledgeResearch/1.0 (+controlled-review-research)"
DEFAULT_CONFIG = {
    "enabled": False,
    "interval_hours": 168,
    "max_topics": 12,
    "max_results_per_topic": 5,
    "max_fetches": 20,
    "max_bytes": 1_500_000,
    "timeout_seconds": 12,
    "retries": 2,
    "per_domain_delay_seconds": 1.0,
}
ALLOWED_RIGHTS = {"public", "licensed", "owned"}
ALLOWED_TRUST = {"authoritative", "high"}
PRIVATE_PATTERNS = (
    r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b",
    r"\b(?:\+?\d[\s\-()]*){7,}\b",
    r"https?://\S+",
    r"\b(?:C|ACC|CASE|SCAN|EV|DEC)-[A-Za-z0-9_-]+\b",
)
GENERIC_SECTORS = {
    "legal": "legal professional services",
    "food": "food service operations",
    "manufacturing": "manufacturing operations",
    "retail": "retail commerce",
    "construction": "construction services",
    "tech": "technology services",
    "consulting": "management consulting",
    "realestate": "real estate services",
    "health": "healthcare operations",
    "education": "education services",
    "professional_services": "professional services B2B",
    "experts": "knowledge expert business",
    "ecommerce_retail": "ecommerce retail",
}
GENERIC_GOALS = {
    "acquisition": "customer acquisition",
    "retention": "customer retention",
    "revenue": "revenue growth",
    "operations": "operational efficiency",
    "quality": "service quality",
    "profitability": "unit economics profitability",
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def ensure_schema(db):
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    ready = db.execute("""SELECT
        (SELECT COUNT(*) FROM information_schema.tables
         WHERE table_schema='public' AND table_name IN (
           'knowledge_research_config','knowledge_research_runs',
           'knowledge_research_candidates','knowledge_research_alerts',
           'knowledge_research_gaps')) AS table_count,
        (SELECT COUNT(*) FROM information_schema.columns
         WHERE table_schema='public' AND table_name='knowledge_research_candidates'
           AND column_name IN ('domain_registry_source_id','rights_evidence_url',
                               'source_age_evidence_date','eligibility_checked_at'))
           AS safety_column_count""").fetchone()
    if (
        ready and int(ready["table_count"] or 0) == 5
        and int(ready["safety_column_count"] or 0) == 4
    ):
        _SCHEMA_READY = True
        return
    acquire_schema_lock(db)
    db.execute("""CREATE TABLE IF NOT EXISTS knowledge_research_config (
        config_id TEXT PRIMARY KEY,
        enabled SMALLINT NOT NULL DEFAULT 0,
        interval_hours INTEGER NOT NULL DEFAULT 168,
        max_topics INTEGER NOT NULL DEFAULT 12,
        max_results_per_topic INTEGER NOT NULL DEFAULT 5,
        max_fetches INTEGER NOT NULL DEFAULT 20,
        max_bytes BIGINT NOT NULL DEFAULT 1500000,
        timeout_seconds INTEGER NOT NULL DEFAULT 12,
        retries INTEGER NOT NULL DEFAULT 2,
        per_domain_delay_seconds REAL NOT NULL DEFAULT 1,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""INSERT INTO knowledge_research_config (config_id)
                  VALUES ('default') ON CONFLICT (config_id) DO NOTHING""")
    db.execute("""CREATE TABLE IF NOT EXISTS knowledge_research_runs (
        run_id TEXT PRIMARY KEY,
        trigger_type TEXT NOT NULL,
        status TEXT NOT NULL,
        started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        completed_at TIMESTAMPTZ,
        topics_json TEXT NOT NULL DEFAULT '[]',
        discovered_count INTEGER NOT NULL DEFAULT 0,
        rejected_count INTEGER NOT NULL DEFAULT 0,
        duplicate_count INTEGER NOT NULL DEFAULT 0,
        error_count INTEGER NOT NULL DEFAULT 0,
        fetch_count INTEGER NOT NULL DEFAULT 0,
        bytes_fetched BIGINT NOT NULL DEFAULT 0,
        estimated_cost NUMERIC,
        error_message TEXT,
        summary_json TEXT
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS knowledge_research_candidates (
        candidate_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES knowledge_research_runs(run_id),
        canonical_url TEXT NOT NULL,
        domain TEXT NOT NULL,
        title TEXT NOT NULL,
        publisher TEXT,
        source_age_years INTEGER,
        source_age_evidence_url TEXT,
        source_age_evidence_date DATE,
        rights_status TEXT NOT NULL,
        rights_evidence_url TEXT,
        rights_expires_at DATE,
        trust_level TEXT NOT NULL,
        eligibility_checked_at TIMESTAMPTZ,
        publisher_continuity_note TEXT,
        document_date DATE,
        domain_registry_source_id TEXT,
        topic TEXT NOT NULL,
        sector_tag TEXT,
        goal_tag TEXT,
        recommendation_reason TEXT,
        status TEXT NOT NULL DEFAULT 'pending_review',
        rejection_reason TEXT,
        retrieved_at TIMESTAMPTZ,
        http_status INTEGER,
        etag TEXT,
        last_modified TEXT,
        version_label TEXT,
        content_fingerprint TEXT,
        content_text TEXT,
        previous_candidate_id TEXT,
        diff_text TEXT,
        reviewer TEXT,
        reviewed_at TIMESTAMPTZ,
        promoted_source_id TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (status IN ('pending_review','approved','rejected','duplicate','blocked','superseded')),
        UNIQUE(run_id, canonical_url)
    )""")
    candidate_columns = {
        row["column_name"] for row in db.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name='knowledge_research_candidates'"""
        ).fetchall()
    }
    for column_name, definition in {
        "source_age_evidence_url": "TEXT",
        "source_age_evidence_date": "DATE",
        "rights_evidence_url": "TEXT",
        "rights_expires_at": "DATE",
        "eligibility_checked_at": "TIMESTAMPTZ",
        "publisher_continuity_note": "TEXT",
        "document_date": "DATE",
        "domain_registry_source_id": "TEXT",
    }.items():
        if column_name not in candidate_columns:
            db.execute(
                f"ALTER TABLE knowledge_research_candidates ADD COLUMN {column_name} {definition}"
            )
    db.execute("""CREATE TABLE IF NOT EXISTS knowledge_research_alerts (
        alert_id TEXT PRIMARY KEY,
        run_id TEXT REFERENCES knowledge_research_runs(run_id),
        candidate_id TEXT REFERENCES knowledge_research_candidates(candidate_id),
        alert_type TEXT NOT NULL,
        severity TEXT NOT NULL,
        message TEXT NOT NULL,
        resolved_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS knowledge_research_gaps (
        gap_key TEXT PRIMARY KEY,
        sector TEXT,
        library_type TEXT,
        occurrence_count INTEGER NOT NULL DEFAULT 1,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_research_candidates_status ON knowledge_research_candidates(status, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_research_candidates_url ON knowledge_research_candidates(canonical_url, created_at)")
    db.commit()
    _SCHEMA_READY = True


def get_config(db):
    ensure_schema(db)
    row = db.execute("SELECT * FROM knowledge_research_config WHERE config_id='default'").fetchone()
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    return result


def update_config(db, payload):
    current = get_config(db)
    proposed = dict(current)
    proposed.update(payload or {})
    try:
        values = {
            "enabled": 1 if bool(proposed["enabled"]) else 0,
            "interval_hours": max(1, min(int(proposed["interval_hours"]), 24 * 90)),
            "max_topics": max(1, min(int(proposed["max_topics"]), 50)),
            "max_results_per_topic": max(1, min(int(proposed["max_results_per_topic"]), 20)),
            "max_fetches": max(1, min(int(proposed["max_fetches"]), 100)),
            "max_bytes": max(1024, min(int(proposed["max_bytes"]), 10_000_000)),
            "timeout_seconds": max(2, min(int(proposed["timeout_seconds"]), 60)),
            "retries": max(0, min(int(proposed["retries"]), 4)),
            "per_domain_delay_seconds": max(0.0, min(float(proposed["per_domain_delay_seconds"]), 30.0)),
        }
    except (TypeError, ValueError, KeyError):
        return {"success": False, "error": "CONFIG_INVALID"}
    db.execute("""UPDATE knowledge_research_config SET enabled=?,interval_hours=?,
        max_topics=?,max_results_per_topic=?,max_fetches=?,max_bytes=?,
        timeout_seconds=?,retries=?,per_domain_delay_seconds=?,updated_at=now()
        WHERE config_id='default'""", tuple(values.values()))
    db.commit()
    return {"success": True, "config": {**values, "enabled": bool(values["enabled"])}}


def sanitize_topic(value, private_terms=None):
    text = str(value or "")
    for pattern in PRIVATE_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.I)
    for term in private_terms or ():
        term = str(term or "").strip()
        if term:
            text = re.sub(re.escape(term), " ", text, flags=re.I)
    text = re.sub(r"\d+", " ", text)
    text = re.sub(r"[^\w\u0600-\u06FF -]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:180]


def build_topics(taxonomy=None, gaps=None, private_terms=None, limit=12):
    taxonomy = taxonomy or {}
    sectors = taxonomy.get("sectors") or []
    goals = taxonomy.get("goals") or []
    topics = []
    for sector in sectors:
        generic = GENERIC_SECTORS.get(str(sector).lower())
        if generic:
            topics.append({"query": f"{generic} evidence based practices", "sector": str(sector), "goal": None})
    for goal in goals:
        generic = GENERIC_GOALS.get(str(goal).lower())
        if generic:
            topics.append({"query": f"{generic} evidence based practices", "sector": None, "goal": str(goal)})
    for gap in gaps or []:
        cleaned = sanitize_topic(gap, private_terms)
        if len(cleaned.split()) >= 2:
            topics.append({"query": f"{cleaned} evidence based guidance", "sector": None, "goal": None})
    unique = []
    seen = set()
    for topic in topics:
        query = sanitize_topic(topic["query"], private_terms).lower()
        if query and query not in seen:
            seen.add(query)
            unique.append({**topic, "query": query})
    return unique[:max(1, int(limit))]


def record_general_gap(db, sector=None, library_type=None):
    """يسجل التصنيف العام فقط؛ لا يحفظ استعلام العميل أو أدلته."""
    ensure_schema(db)
    clean_sector = str(sector or "general").strip().lower()
    clean_library = str(library_type or "all").strip().upper()
    if clean_sector not in set(GENERIC_SECTORS) | {"general"}:
        clean_sector = "general"
    allowed_libraries = {
        "ALL", "PROBLEM", "CAUSE", "DIAGNOSTIC_QUESTION", "EVIDENCE_REQUIREMENT",
        "KPI", "BENCHMARK", "DIAGNOSTIC_RULE", "DECISION_RULE", "DIAGNOSTIC_PATTERN",
        "OPPORTUNITY", "RECOMMENDATION", "SOP", "CASE", "FRAMEWORK",
    }
    if clean_library not in allowed_libraries:
        clean_library = "ALL"
    key = f"{clean_sector}:{clean_library}"
    db.execute("""INSERT INTO knowledge_research_gaps
        (gap_key,sector,library_type) VALUES (?,?,?)
        ON CONFLICT (gap_key) DO UPDATE SET
          occurrence_count=knowledge_research_gaps.occurrence_count+1,last_seen_at=now()""",
        (key, clean_sector, clean_library))
    db.commit()
    return key


def list_general_gaps(db, limit=20):
    ensure_schema(db)
    rows = db.execute("""SELECT sector,library_type,occurrence_count
        FROM knowledge_research_gaps ORDER BY occurrence_count DESC,last_seen_at DESC LIMIT ?""",
        (limit,)).fetchall()
    return [
        f"{row['sector']} {str(row['library_type']).lower()} knowledge gap"
        for row in rows
    ]


def candidate_eligibility(candidate, today=None):
    today = today or date.today()
    reasons = []
    age_evidence_url = _canonical_url(candidate.get("source_age_evidence_url"))
    try:
        age_evidence_date = date.fromisoformat(
            str(candidate.get("source_age_evidence_date") or "")[:10]
        )
    except ValueError:
        age_evidence_date = None
    evidence_age = (
        today.year - age_evidence_date.year
        - ((today.month, today.day) < (age_evidence_date.month, age_evidence_date.day))
        if age_evidence_date else 0
    )
    if int(candidate.get("source_age_years") or 0) < 10 or evidence_age < 10 or not age_evidence_url:
        reasons.append("SITE_AGE_BELOW_TEN_YEARS")
    if candidate.get("trust_level") not in ALLOWED_TRUST:
        reasons.append("PUBLISHER_TRUST_INSUFFICIENT")
    if candidate.get("rights_status") not in ALLOWED_RIGHTS:
        reasons.append("RIGHTS_NOT_ELIGIBLE")
    if not _canonical_url(candidate.get("rights_evidence_url")):
        reasons.append("RIGHTS_EVIDENCE_MISSING")
    expiry = candidate.get("rights_expires_at")
    if expiry:
        expiry = date.fromisoformat(str(expiry)[:10])
        if expiry < today:
            reasons.append("RIGHTS_EXPIRED")
    if candidate.get("publisher_changed") and not candidate.get("publisher_change_verified"):
        reasons.append("PUBLISHER_CHANGE_UNVERIFIED")
    if not str(candidate.get("publisher_continuity_note") or "").strip():
        reasons.append("PUBLISHER_CONTINUITY_UNVERIFIED")
    checked = candidate.get("eligibility_checked_at")
    if checked:
        try:
            checked_date = (
                checked.date() if isinstance(checked, datetime)
                else date.fromisoformat(str(checked)[:10])
            )
        except ValueError:
            checked_date = None
    else:
        checked_date = None
    if not checked_date or checked_date > today or (today - checked_date).days > 30:
        reasons.append("ELIGIBILITY_CHECK_EXPIRED")
    try:
        document_date = date.fromisoformat(str(candidate.get("document_date") or "")[:10])
    except ValueError:
        document_date = None
    if not document_date or document_date > today:
        reasons.append("DOCUMENT_DATE_INVALID")
    return {"eligible": not reasons, "reasons": reasons}


def _canonical_url(value):
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    path = re.sub(r"/+", "/", parsed.path or "/")
    return parsed._replace(fragment="", query="", path=path).geturl()


def _approved_domain_registry(db):
    rows = db.execute("""SELECT source_id,source_url,publisher,status,rights_status,
        publisher_trust,publisher_continuity,site_age_evidence_url,site_age_evidence_date,
        document_date,version_label,content_fingerprint,trust_level,reviewed_at,
        review_due_at,retrieved_at,author_identity,material_type,methodology_note,
        license_note,jurisdiction,eligibility_json
        FROM knowledge_sources
        WHERE status='approved' AND source_url IS NOT NULL
          AND COALESCE(material_type,'') <> 'web_research'
          AND eligibility_json LIKE '%%"eligible": true%%'""").fetchall()
    registry = {}
    for row in rows:
        url = _canonical_url(row["source_url"])
        if url:
            registry[urlparse(url).netloc.lower()] = dict(row)
    return registry


def _resolve_public_url(value, resolver=None):
    resolver = resolver or socket.getaddrinfo
    url = _canonical_url(value)
    if not url:
        raise RuntimeError("URL_INVALID")
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        raise RuntimeError("URL_CREDENTIALS_BLOCKED")
    if parsed.port and parsed.port not in {80, 443}:
        raise RuntimeError("URL_PORT_BLOCKED")
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        host in {"localhost", "localhost.localdomain"}
        or host.endswith((".local", ".internal", ".localhost"))
    ):
        raise RuntimeError("PRIVATE_ADDRESS_BLOCKED")
    try:
        addresses = resolver(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError as exc:
        raise RuntimeError("DNS_RESOLUTION_FAILED") from exc
    if not addresses:
        raise RuntimeError("DNS_RESOLUTION_FAILED")
    public_ips = []
    for address in addresses:
        raw_ip = address[4][0]
        ip = ipaddress.ip_address(raw_ip.split("%", 1)[0])
        if not ip.is_global:
            raise RuntimeError("PRIVATE_ADDRESS_BLOCKED")
        if str(ip) not in public_ips:
            public_ips.append(str(ip))
    return url, public_ips


def validate_public_url(value, resolver=None):
    return _resolve_public_url(value, resolver)[0]


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, pinned_ip, **kwargs):
        self._pinned_ip = pinned_ip
        super().__init__(host, **kwargs)

    def connect(self):
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, pinned_ip, **kwargs):
        self._pinned_ip = pinned_ip
        super().__init__(host, **kwargs)

    def connect(self):
        raw_socket = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


class _PinnedResponse:
    def __init__(self, connection, response):
        self.connection = connection
        self.response = response
        self.status = response.status
        self.headers = response.headers

    def read(self, limit=None):
        return self.response.read(limit)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.connection.close()
        return False


def _origin(url):
    parsed = urlparse(url)
    return (
        parsed.scheme.lower(), (parsed.hostname or "").lower(),
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )


def _redirect_headers(current_url, next_url, headers, allow_cross_origin):
    next_headers = dict(headers or {})
    if _origin(current_url) != _origin(next_url):
        if not allow_cross_origin:
            raise RuntimeError("CROSS_ORIGIN_REDIRECT_BLOCKED")
        for name in list(next_headers):
            if name.lower() in {"authorization", "cookie", "proxy-authorization"}:
                next_headers.pop(name, None)
    return next_headers


def _pinned_open(url, headers=None, timeout=12, redirects=0, allow_cross_origin=True):
    if redirects > 3:
        raise RuntimeError("TOO_MANY_REDIRECTS")
    url, public_ips = _resolve_public_url(url)
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connection_class = (
        _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
    )
    kwargs = {"port": port, "timeout": timeout}
    if parsed.scheme == "https":
        kwargs["context"] = ssl.create_default_context()
    connection = connection_class(parsed.hostname, public_ips[0], **kwargs)
    request_headers = dict(headers or {})
    request_headers["Host"] = parsed.netloc
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    try:
        connection.request("GET", path, headers=request_headers)
        response = connection.getresponse()
    except Exception:
        connection.close()
        raise
    if response.status in {301, 302, 303, 307, 308}:
        location = response.headers.get("Location")
        response.read(4096)
        connection.close()
        if not location:
            raise RuntimeError("REDIRECT_WITHOUT_LOCATION")
        from urllib.parse import urljoin
        next_url = urljoin(url, location)
        next_headers = _redirect_headers(
            url, next_url, headers, allow_cross_origin
        )
        return _pinned_open(
            next_url, headers=next_headers, timeout=timeout,
            redirects=redirects + 1, allow_cross_origin=allow_cross_origin,
        )
    return _PinnedResponse(connection, response)


class ConservativeFetcher:
    def __init__(self, config, opener=None, sleeper=time.sleep):
        self.config = config
        self.opener = opener
        self.sleeper = sleeper
        self.domain_last_request = {}

    def _open(self, request, timeout):
        if self.opener:
            return self.opener(request, timeout=timeout)
        return _pinned_open(
            request.full_url, headers=dict(request.header_items()), timeout=timeout
        )

    def _robots_allowed(self, url):
        url = validate_public_url(url)
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        validate_public_url(robots_url)
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            request = Request(robots_url, headers={"User-Agent": USER_AGENT})
            with self._open(request, timeout=self.config["timeout_seconds"]) as response:
                parser.parse(response.read(256_000).decode("utf-8", errors="replace").splitlines())
            return parser.can_fetch(USER_AGENT, url)
        except Exception:
            return False

    def fetch(self, url):
        url = validate_public_url(url)
        if not self._robots_allowed(url):
            raise RuntimeError("ROBOTS_DISALLOWED_OR_UNAVAILABLE")
        domain = urlparse(url).netloc.lower()
        elapsed = time.monotonic() - self.domain_last_request.get(domain, 0)
        delay = float(self.config["per_domain_delay_seconds"])
        if elapsed < delay:
            self.sleeper(delay - elapsed)
        last_error = None
        for attempt in range(int(self.config["retries"]) + 1):
            try:
                request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain"})
                with self._open(request, timeout=self.config["timeout_seconds"]) as response:
                    status = int(getattr(response, "status", 200))
                    content_type = response.headers.get("Content-Type", "")
                    if status != 200:
                        raise RuntimeError(f"HTTP_{status}")
                    if "text/html" not in content_type and "text/plain" not in content_type:
                        raise RuntimeError("CONTENT_TYPE_BLOCKED")
                    declared = int(response.headers.get("Content-Length") or 0)
                    if declared > int(self.config["max_bytes"]):
                        raise RuntimeError("CONTENT_TOO_LARGE")
                    raw = response.read(int(self.config["max_bytes"]) + 1)
                    if len(raw) > int(self.config["max_bytes"]):
                        raise RuntimeError("CONTENT_TOO_LARGE")
                    text = raw.decode("utf-8", errors="replace")
                    if re.search(r"\b(paywall|subscribe to continue|sign in to read)\b", text, re.I):
                        raise RuntimeError("PAYWALL_BLOCKED")
                    self.domain_last_request[domain] = time.monotonic()
                    return {
                        "status": status, "content": text,
                        "bytes": len(raw), "etag": response.headers.get("ETag"),
                        "last_modified": response.headers.get("Last-Modified"),
                    }
            except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
                last_error = exc
                if attempt < int(self.config["retries"]):
                    self.sleeper(min(2 ** attempt, 4))
        raise RuntimeError(str(last_error or "FETCH_FAILED"))


class JsonSearchProvider:
    """مزود JSON محدود ومصرّح به: endpoint يعيد results وبيانات الأهلية."""
    def __init__(self, endpoint=None, bearer_token=None, opener=None):
        self.endpoint = str(endpoint or "").strip()
        self.bearer_token = bearer_token
        self.opener = opener
        if not self.endpoint.startswith("https://"):
            raise RuntimeError("SEARCH_PROVIDER_NOT_CONFIGURED")

    def search(self, query, limit=5):
        from urllib.parse import urlencode
        separator = "&" if "?" in self.endpoint else "?"
        url = self.endpoint + separator + urlencode({"q": query, "limit": limit})
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        request = Request(url, headers=headers)
        response_context = (
            self.opener(request, timeout=15) if self.opener
            else _pinned_open(
                url, headers=headers, timeout=15, allow_cross_origin=False
            )
        )
        with response_context as response:
            if int(getattr(response, "status", 200)) != 200:
                raise RuntimeError(f"SEARCH_HTTP_{response.status}")
            payload = json.loads(response.read(1_000_001))
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise RuntimeError("SEARCH_RESPONSE_INVALID")
        return results[:limit]


class RegistrySearchProvider:
    """مراقبة افتراضية بلا طرف ثالث لروابط المصادر المؤهلة المعتمدة."""
    def __init__(self, db):
        self.db = db

    def search(self, query, limit=5):
        rows = list(_approved_domain_registry(self.db).values())[:limit]
        today = date.today()
        results = []
        for row in rows:
            age_date = row.get("site_age_evidence_date")
            age_years = (
                today.year - age_date.year
                - ((today.month, today.day) < (age_date.month, age_date.day))
                if age_date else 0
            )
            results.append({
                "url": row["source_url"],
                "title": row.get("title"),
                "publisher": row.get("publisher"),
                "source_age_years": age_years,
                "source_age_evidence_url": row.get("site_age_evidence_url"),
                "source_age_evidence_date": age_date,
                "rights_status": row.get("rights_status"),
                "rights_evidence_url": row.get("source_url"),
                "trust_level": row.get("trust_level"),
                "eligibility_checked_at": today.isoformat(),
                "publisher_continuity_note": row.get("publisher_continuity"),
                "document_date": row.get("document_date"),
                "version_label": row.get("version_label"),
                "recommendation_reason": "PERIODIC_APPROVED_SOURCE_RECHECK",
            })
        return results


def configured_search_provider(db=None):
    endpoint = os.environ.get("SANA_RESEARCH_SEARCH_ENDPOINT")
    if not endpoint:
        return RegistrySearchProvider(db) if db is not None else None
    return JsonSearchProvider(
        endpoint=endpoint,
        bearer_token=os.environ.get("SANA_RESEARCH_SEARCH_TOKEN"),
    )


def _plain_text(html):
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _diff(previous, current):
    return "\n".join(difflib.unified_diff(
        (previous or "").splitlines(), (current or "").splitlines(),
        fromfile="approved/current", tofile="candidate/new", lineterm=""
    ))[:20_000]


def _alert(db, run_id, alert_type, message, candidate_id=None, severity="warning"):
    db.execute("""INSERT INTO knowledge_research_alerts
        (alert_id,run_id,candidate_id,alert_type,severity,message)
        VALUES (?,?,?,?,?,?)""",
        (f"KRA-{uuid.uuid4().hex}", run_id, candidate_id, alert_type, severity, str(message)[:1000]))


def _acquire_run_lock(db):
    row = db.execute("SELECT pg_try_advisory_xact_lock(?) AS acquired", (LOCK_KEY,)).fetchone()
    return bool(row and row["acquired"])


def run_cycle(db, trigger_type="manual", search_provider=None, fetcher=None,
              taxonomy=None, gaps=None, private_terms=None, now=None,
              approved_domains=None):
    ensure_schema(db)
    config = get_config(db)
    run_id = f"KRR-{uuid.uuid4().hex}"
    now = now or datetime.now(timezone.utc)
    db.execute("INSERT INTO knowledge_research_runs (run_id,trigger_type,status) VALUES (?,?,'running')",
               (run_id, trigger_type))
    if not _acquire_run_lock(db):
        db.execute("""UPDATE knowledge_research_runs SET status='skipped_overlap',
                      completed_at=now(),error_message='RUN_ALREADY_ACTIVE' WHERE run_id=?""", (run_id,))
        db.commit()
        return {"success": False, "error": "RUN_ALREADY_ACTIVE", "run_id": run_id}
    topics = build_topics(taxonomy, gaps, private_terms, config["max_topics"])
    db.execute("UPDATE knowledge_research_runs SET topics_json=? WHERE run_id=?", (_json(topics), run_id))
    counters = {"discovered": 0, "rejected": 0, "duplicate": 0, "errors": 0, "fetches": 0, "bytes": 0}
    domain_registry = _approved_domain_registry(db)
    allowed_domains = (
        {str(domain).lower() for domain in approved_domains}
        if approved_domains is not None else set(domain_registry)
    )
    provider_error = None
    if search_provider is None:
        provider_error = "SEARCH_PROVIDER_UNAVAILABLE"
    else:
        fetcher = fetcher or ConservativeFetcher(config)
        seen_urls = set()
        for topic in topics:
            try:
                results = search_provider.search(topic["query"], limit=config["max_results_per_topic"])
            except Exception as exc:
                provider_error = f"SEARCH_PROVIDER_FAILED: {exc}"
                counters["errors"] += 1
                _alert(db, run_id, "provider_failure", provider_error, severity="error")
                break
            for item in results[:config["max_results_per_topic"]]:
                if counters["fetches"] >= config["max_fetches"]:
                    break
                url = _canonical_url(item.get("url"))
                if not url:
                    counters["rejected"] += 1
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                domain = urlparse(url).netloc.lower()
                registry_source = domain_registry.get(domain)
                if domain not in allowed_domains:
                    counters["rejected"] += 1
                    _alert(db, run_id, "source_rejected", "DOMAIN_NOT_PREAPPROVED")
                    continue
                if (
                    registry_source
                    and registry_source.get("publisher")
                    and item.get("publisher") != registry_source["publisher"]
                    and not item.get("publisher_change_verified")
                ):
                    counters["rejected"] += 1
                    _alert(db, run_id, "source_rejected", "PUBLISHER_CHANGE_UNVERIFIED")
                    continue
                eligibility = candidate_eligibility(item, now.date())
                if not eligibility["eligible"]:
                    counters["rejected"] += 1
                    _alert(db, run_id, "source_rejected", ",".join(eligibility["reasons"]))
                    continue
                try:
                    fetched = fetcher.fetch(url)
                    counters["fetches"] += 1
                    counters["bytes"] += fetched["bytes"]
                    text = _plain_text(fetched["content"])
                    fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    previous = db.execute("""SELECT candidate_id,content_fingerprint,content_text,
                        publisher,version_label FROM knowledge_research_candidates
                        WHERE canonical_url=? AND status IN ('approved','pending_review','duplicate')
                        ORDER BY created_at DESC LIMIT 1""", (url,)).fetchone()
                    if previous and previous["content_fingerprint"] == fingerprint:
                        status, reason = "duplicate", "CONTENT_UNCHANGED"
                        counters["duplicate"] += 1
                    else:
                        status, reason = "pending_review", item.get("recommendation_reason") or "NEW_OR_CHANGED_SOURCE"
                        counters["discovered"] += 1
                    candidate_id = f"KRC-{uuid.uuid4().hex}"
                    diff = _diff(previous["content_text"], text) if previous else _diff("", text)
                    db.execute("""INSERT INTO knowledge_research_candidates
                        (candidate_id,run_id,canonical_url,domain,title,publisher,source_age_years,
                         source_age_evidence_url,source_age_evidence_date,rights_status,
                         rights_evidence_url,rights_expires_at,trust_level,eligibility_checked_at,
                         publisher_continuity_note,document_date,domain_registry_source_id,
                         topic,sector_tag,goal_tag,recommendation_reason,
                         status,retrieved_at,http_status,etag,last_modified,version_label,
                         content_fingerprint,content_text,previous_candidate_id,diff_text)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (candidate_id, run_id, url, urlparse(url).netloc.lower(),
                         item.get("title") or url, item.get("publisher"), item.get("source_age_years"),
                         item.get("source_age_evidence_url"), item.get("source_age_evidence_date"),
                         item.get("rights_status"), item.get("rights_evidence_url"),
                         item.get("rights_expires_at"), item.get("trust_level"),
                         item.get("eligibility_checked_at"), item.get("publisher_continuity_note"),
                         item.get("document_date"),
                         registry_source["source_id"] if registry_source else None, topic["query"],
                         topic.get("sector"), topic.get("goal"), reason, status, now,
                         fetched["status"], fetched.get("etag"), fetched.get("last_modified"),
                         item.get("version_label"), fingerprint, text,
                         previous["candidate_id"] if previous else None, diff))
                except Exception as exc:
                    counters["errors"] += 1
                    _alert(db, run_id, "fetch_failure", f"{url}: {exc}", severity="error")
    if provider_error:
        _alert(db, run_id, "provider_unavailable", provider_error, severity="error")
    status = "failed" if provider_error and not counters["discovered"] else (
        "partial_success" if counters["errors"] else "success"
    )
    summary = {**counters, "provider_error": provider_error}
    db.execute("""UPDATE knowledge_research_runs SET status=?,completed_at=now(),
        discovered_count=?,rejected_count=?,duplicate_count=?,error_count=?,
        fetch_count=?,bytes_fetched=?,error_message=?,summary_json=? WHERE run_id=?""",
        (status, counters["discovered"], counters["rejected"], counters["duplicate"],
         counters["errors"], counters["fetches"], counters["bytes"], provider_error,
         _json(summary), run_id))
    db.commit()
    return {"success": status in {"success", "partial_success"}, "run_id": run_id,
            "status": status, **counters}


def list_dashboard(db, limit=30):
    ensure_schema(db)
    runs = [dict(row) for row in db.execute(
        "SELECT * FROM knowledge_research_runs ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()]
    candidates = [dict(row) for row in db.execute(
        """SELECT candidate_id,run_id,canonical_url,title,publisher,source_age_years,
                  source_age_evidence_url,source_age_evidence_date,rights_status,
                  rights_evidence_url,rights_expires_at,trust_level,eligibility_checked_at,
                  publisher_continuity_note,document_date,topic,sector_tag,goal_tag,recommendation_reason,
                  status,retrieved_at,version_label,content_fingerprint,previous_candidate_id,
                  diff_text,reviewer,reviewed_at,promoted_source_id
           FROM knowledge_research_candidates ORDER BY created_at DESC LIMIT ?""", (limit,)
    ).fetchall()]
    alerts = [dict(row) for row in db.execute(
        "SELECT * FROM knowledge_research_alerts WHERE resolved_at IS NULL ORDER BY created_at DESC LIMIT ?",
        (limit,)
    ).fetchall()]
    for run in runs:
        run["topics"] = _loads(run.pop("topics_json", None), [])
        run["summary"] = _loads(run.pop("summary_json", None), {})
    return {"config": get_config(db), "runs": runs, "candidates": candidates, "alerts": alerts}


def review_candidate(db, candidate_id, decision, reviewer):
    ensure_schema(db)
    if decision not in {"approved", "rejected", "rollback"}:
        return {"success": False, "error": "DECISION_INVALID"}
    row = db.execute("SELECT * FROM knowledge_research_candidates WHERE candidate_id=?",
                     (candidate_id,)).fetchone()
    if not row:
        return {"success": False, "error": "CANDIDATE_NOT_FOUND"}
    if decision == "rejected":
        db.execute("""UPDATE knowledge_research_candidates SET status='rejected',
                      reviewer=?,reviewed_at=now() WHERE candidate_id=?""", (reviewer, candidate_id))
        db.commit()
        return {"success": True, "status": "rejected", "candidate_id": candidate_id}
    target = row
    if decision == "rollback":
        if not row["previous_candidate_id"]:
            return {"success": False, "error": "PREVIOUS_VERSION_NOT_FOUND"}
        target = db.execute("SELECT * FROM knowledge_research_candidates WHERE candidate_id=?",
                            (row["previous_candidate_id"],)).fetchone()
        visited = set()
        while (
            target and not target["promoted_source_id"]
            and target["previous_candidate_id"]
            and target["candidate_id"] not in visited
        ):
            visited.add(target["candidate_id"])
            target = db.execute(
                "SELECT * FROM knowledge_research_candidates WHERE candidate_id=?",
                (target["previous_candidate_id"],),
            ).fetchone()
        if not target:
            return {"success": False, "error": "PREVIOUS_VERSION_NOT_FOUND"}
    eligibility = candidate_eligibility(dict(target))
    if not eligibility["eligible"]:
        return {"success": False, "error": "SOURCE_NOT_ELIGIBLE", "eligibility": eligibility}
    registry_source_id = target["domain_registry_source_id"]
    if registry_source_id:
        from sana_knowledge import assess_source_eligibility
        registry_source = db.execute(
            "SELECT * FROM knowledge_sources WHERE source_id=?", (registry_source_id,)
        ).fetchone()
        registry_eligibility = (
            assess_source_eligibility(dict(registry_source))
            if registry_source and registry_source["status"] == "approved" else None
        )
        if (
            not registry_source or not registry_eligibility["eligible"]
            or (
                registry_source["publisher"] and target["publisher"]
                and registry_source["publisher"] != target["publisher"]
            )
        ):
            return {
                "success": False, "error": "SOURCE_DOMAIN_NO_LONGER_ELIGIBLE",
                "eligibility": registry_eligibility,
            }
    prior = target
    visited = set()
    while (
        prior and not prior["promoted_source_id"] and prior["previous_candidate_id"]
        and prior["candidate_id"] not in visited
    ):
        visited.add(prior["candidate_id"])
        prior = db.execute(
            "SELECT * FROM knowledge_research_candidates WHERE candidate_id=?",
            (prior["previous_candidate_id"],),
        ).fetchone()
    source_id = (
        target["promoted_source_id"]
        or (prior["promoted_source_id"] if prior else None)
        or f"SRC-WEB-{uuid.uuid4().hex[:20].upper()}"
    )
    active = db.execute("""SELECT candidate_id FROM knowledge_research_candidates
                           WHERE canonical_url=? AND status='approved' AND candidate_id<>?""",
                        (target["canonical_url"], target["candidate_id"])).fetchall()
    for active_row in active:
        db.execute("UPDATE knowledge_research_candidates SET status='superseded' WHERE candidate_id=?",
                   (active_row["candidate_id"],))
    existing_source = db.execute("SELECT source_id FROM knowledge_sources WHERE source_id=?",
                                 (source_id,)).fetchone()
    version = target["version_label"] or f"web-{str(target['content_fingerprint'])[:12]}"
    version_collision = db.execute(
        "SELECT content_hash FROM knowledge_versions WHERE source_id=? AND version_label=?",
        (source_id, version),
    ).fetchone()
    if version_collision and version_collision["content_hash"] != target["content_fingerprint"]:
        version = f"{version}-{str(target['content_fingerprint'])[:12]}"
    eligibility_json = _json({"eligible": True, "reasons": [], "review_origin": "periodic_research"})
    if existing_source:
        db.execute("""UPDATE knowledge_sources SET title=?,publisher=?,source_url=?,
                      rights_status=?,retrieved_at=?,status='approved',version_label=?,
                      content_fingerprint=?,trust_level=?,reviewed_at=CURRENT_DATE,
                      eligibility_json=?,updated_at=to_char(now() AT TIME ZONE 'utc','YYYY-MM-DD HH24:MI:SS')
                      WHERE source_id=?""",
                   (target["title"], target["publisher"], target["canonical_url"],
                    target["rights_status"], target["retrieved_at"], version,
                    target["content_fingerprint"], target["trust_level"], eligibility_json, source_id))
    else:
        review_due = date.today() + timedelta(days=365)
        db.execute("""INSERT INTO knowledge_sources
            (source_id,title,source_type,publisher,source_url,rights_status,license_note,
             retrieved_at,review_due_at,status,author_identity,material_type,methodology_note,
             publisher_trust,publisher_continuity,site_age_evidence_url,
             site_age_evidence_date,document_date,version_label,content_fingerprint,trust_level,
             reviewed_at,eligibility_json,jurisdiction)
             VALUES (?,?,?,?,?,?,?,?,?,'approved',?,'web_research',
                     'اكتشاف دوري محافظ مع اعتماد بشري',?,'rechecked_each_cycle',?,
                     ?,CURRENT_DATE,?,?,?,CURRENT_DATE,?,'general')""",
            (source_id, target["title"], "public", target["publisher"], target["canonical_url"],
             target["rights_status"], "اعتماد بشري ضمن دورة البحث الدوري",
             target["retrieved_at"], review_due, target["publisher"] or "verified publisher",
             target["trust_level"], target["source_age_evidence_url"],
             target["source_age_evidence_date"], version,
             target["content_fingerprint"], target["trust_level"], eligibility_json))
    db.execute("""INSERT INTO knowledge_versions
        (version_id,source_id,version_label,content_hash,published_at,reviewed_at,reviewer,status)
        VALUES (?,?,?,?,?,CURRENT_DATE,?,'approved')
        ON CONFLICT (source_id,version_label) DO NOTHING""",
        (f"{source_id}:{version}", source_id, version, target["content_fingerprint"],
         target["retrieved_at"], reviewer))
    db.execute("""UPDATE knowledge_research_candidates SET status='approved',
                  reviewer=?,reviewed_at=now(),promoted_source_id=? WHERE candidate_id=?""",
               (reviewer, source_id, target["candidate_id"]))
    object_id = f"KO-{source_id}"
    db.execute("""INSERT INTO knowledge_objects
        (object_id,library_type,category,sector,title,problem,source,source_id,source_url,
         evidence_quality,confidence_level,knowledge_level,version,last_reviewed,status,
         source_excerpt,original_summary,domains,sector_tags,goal_tags)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_DATE,'approved',?,?,?,?,?)
        ON CONFLICT (object_id) DO UPDATE SET title=EXCLUDED.title,
          source_url=EXCLUDED.source_url,version=EXCLUDED.version,
          problem=EXCLUDED.problem,
          source_excerpt=EXCLUDED.source_excerpt,original_summary=EXCLUDED.original_summary,
          sector_tags=EXCLUDED.sector_tags,goal_tags=EXCLUDED.goal_tags,
          status='approved',updated_at=to_char(now() AT TIME ZONE 'utc','YYYY-MM-DD HH24:MI:SS')""",
        (object_id, "FRAMEWORK", "periodic_research", "shared",
         target["title"], str(target["content_text"] or "")[:2000],
         target["publisher"] or target["canonical_url"], source_id,
         target["canonical_url"], "human-reviewed external source", "High", "L1", version,
         str(target["content_text"] or "")[:2000],
         str(target["recommendation_reason"] or "")[:1000],
         _json(["periodic_research"]), _json([target["sector_tag"]] if target["sector_tag"] else []),
         _json([target["goal_tag"]] if target["goal_tag"] else [])))
    if target["candidate_id"] != row["candidate_id"]:
        db.execute("""UPDATE knowledge_research_candidates SET status='superseded',
                      reviewer=?,reviewed_at=now() WHERE candidate_id=?""",
                   (reviewer, row["candidate_id"]))
    db.commit()
    return {"success": True, "status": "approved", "candidate_id": target["candidate_id"],
            "source_id": source_id, "version_label": version}


def run_due_cycle(connect, search_provider_factory=None):
    db = connect()
    try:
        config = get_config(db)
        if not config["enabled"]:
            return {"success": True, "status": "disabled"}
        latest = db.execute("""SELECT started_at FROM knowledge_research_runs
                               WHERE trigger_type='scheduled'
                                 AND status IN ('success','partial_success')
                               ORDER BY started_at DESC LIMIT 1""").fetchone()
        if latest and latest["started_at"] > datetime.now(timezone.utc) - timedelta(hours=config["interval_hours"]):
            return {"success": True, "status": "not_due"}
        provider = (
            search_provider_factory() if search_provider_factory
            else configured_search_provider(db)
        )
        gaps = list_general_gaps(db)
        return run_cycle(db, "scheduled", search_provider=provider,
                         taxonomy={"sectors": list(GENERIC_SECTORS), "goals": list(GENERIC_GOALS)},
                         gaps=gaps)
    finally:
        db.close()


def start_scheduler(connect, search_provider_factory=None):
    def loop():
        while True:
            try:
                run_due_cycle(connect, search_provider_factory)
            except Exception as exc:
                print(f"[knowledge-research] scheduler failed: {exc}", flush=True)
            time.sleep(900)
    thread = threading.Thread(target=loop, name="knowledge-research-scheduler", daemon=True)
    thread.start()
    return thread