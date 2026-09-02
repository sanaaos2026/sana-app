"""اختبار كامل لدورة البحث الدوري دون اتصال خارجي."""
import os
import sys
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import _connect_pg
from sana_knowledge import create_source, ensure_schema as ensure_knowledge_schema, search_knowledge
from sana_research_cycle import (
    ConservativeFetcher,
    _pinned_open,
    _redirect_headers,
    build_topics,
    candidate_eligibility,
    ensure_schema,
    get_config,
    review_candidate,
    run_cycle,
    run_due_cycle,
    sanitize_topic,
    update_config,
    validate_public_url,
)


class FakeProvider:
    def __init__(self, url, title="دليل تشغيلي موثق"):
        self.url = url
        self.title = title
        self.queries = []

    def search(self, query, limit=5):
        self.queries.append(query)
        return [{
            "url": self.url,
            "title": self.title,
            "publisher": "مؤسسة بحثية موثوقة",
            "source_age_years": 15,
            "source_age_evidence_url": "https://web.archive.org/example",
            "source_age_evidence_date": "2010-01-01",
            "rights_status": "public",
            "rights_evidence_url": "https://research.example.org/rights",
            "trust_level": "authoritative",
            "eligibility_checked_at": "2026-09-01",
            "publisher_continuity_note": "الناشر والنطاق متطابقان مع السجل المؤسسي",
            "document_date": "2026-08-01",
            "version_label": "2026.1",
            "recommendation_reason": "يغطي فجوة تشغيلية متكررة",
        }]


class FakeFetcher:
    def __init__(self, content):
        self.content = content
        self.calls = 0

    def fetch(self, url):
        self.calls += 1
        raw = self.content.encode()
        return {
            "status": 200, "content": self.content, "bytes": len(raw),
            "etag": f'"{len(raw)}"', "last_modified": "Tue, 01 Sep 2026 00:00:00 GMT",
        }


class FakeResponse:
    def __init__(self, body, content_type="text/plain", content_length=None):
        self.body = body
        self.status = 200
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(content_length if content_length is not None else len(body)),
        }

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, limit=None):
        return self.body if limit is None else self.body[:limit]


class PeriodicResearchPolicyTest(unittest.TestCase):
    def test_topics_remove_private_identifiers_and_only_use_known_taxonomy(self):
        private = ["شركة الأسرار", "سارة الخاصة"]
        topics = build_topics(
            {"sectors": ["legal"], "goals": ["revenue"]},
            ["شركة الأسرار CASE-123 هاتف 0501234567 تعاني من ضعف التحويل"],
            private_terms=private,
        )
        combined = " ".join(item["query"] for item in topics)
        for forbidden in private + ["CASE-123", "0501234567"]:
            self.assertNotIn(forbidden.lower(), combined.lower())
        self.assertIn("legal professional services", combined)
        self.assertNotRegex(sanitize_topic("عميل 123 test@example.com"), r"\d|@")

    def test_eligibility_rechecks_age_trust_rights_expiry_and_publisher_change(self):
        rejected = candidate_eligibility({
            "source_age_years": 9, "trust_level": "medium",
            "rights_status": "licensed",
            "source_age_evidence_url": "https://web.archive.org/example",
            "source_age_evidence_date": "2010-01-01",
            "rights_evidence_url": "https://example.org/rights",
            "rights_expires_at": (date.today() - timedelta(days=1)).isoformat(),
            "publisher_changed": True, "publisher_change_verified": False,
            "publisher_continuity_note": "تغير غير موثق",
            "eligibility_checked_at": date.today().isoformat(),
            "document_date": date.today().isoformat(),
        })
        self.assertFalse(rejected["eligible"])
        self.assertEqual({
            "SITE_AGE_BELOW_TEN_YEARS", "PUBLISHER_TRUST_INSUFFICIENT",
            "RIGHTS_EXPIRED", "PUBLISHER_CHANGE_UNVERIFIED",
        }, set(rejected["reasons"]))

    def test_private_reserved_addresses_and_unsafe_ports_are_blocked(self):
        private_resolver = lambda *args: [
            (None, None, None, None, ("127.0.0.1", 80))
        ]
        with self.assertRaisesRegex(RuntimeError, "PRIVATE_ADDRESS"):
            validate_public_url("http://internal.example/path", resolver=private_resolver)
        with self.assertRaisesRegex(RuntimeError, "PRIVATE_ADDRESS"):
            validate_public_url("http://127.0.0.1/admin")
        with self.assertRaisesRegex(RuntimeError, "URL_PORT_BLOCKED"):
            validate_public_url("https://example.org:8443/admin")

    def test_connection_uses_the_exact_public_ip_that_was_validated(self):
        public_ip = "93.184.216.34"
        dns_answer = [(None, None, None, None, (public_ip, 80))]
        with patch("sana_research_cycle.socket.getaddrinfo", return_value=dns_answer) as resolver:
            with patch(
                "sana_research_cycle.socket.create_connection",
                side_effect=OSError("stop after observing target"),
            ) as connector:
                with self.assertRaises(OSError):
                    _pinned_open("http://approved.example/guide", timeout=2)
        self.assertEqual(1, resolver.call_count)
        self.assertEqual(public_ip, connector.call_args.args[0][0])

    def test_provider_token_never_crosses_redirect_origin(self):
        headers = {"Authorization": "Bearer secret", "Accept": "application/json"}
        with self.assertRaisesRegex(RuntimeError, "CROSS_ORIGIN_REDIRECT_BLOCKED"):
            _redirect_headers(
                "https://provider.example/search",
                "https://attacker.example/capture",
                headers,
                allow_cross_origin=False,
            )
        sanitized = _redirect_headers(
            "https://source.example/page",
            "https://cdn.example/page",
            {**headers, "Cookie": "session=secret"},
            allow_cross_origin=True,
        )
        self.assertNotIn("Authorization", sanitized)
        self.assertNotIn("Cookie", sanitized)

    def test_quotation_only_rights_cannot_store_full_page(self):
        candidate = {
            "source_age_years": 15,
            "source_age_evidence_url": "https://web.archive.org/example",
            "source_age_evidence_date": "2010-01-01",
            "rights_status": "quotation",
            "rights_evidence_url": "https://example.org/rights",
            "trust_level": "authoritative",
            "publisher_continuity_note": "موثق",
            "eligibility_checked_at": date.today().isoformat(),
            "document_date": date.today().isoformat(),
        }
        self.assertIn("RIGHTS_NOT_ELIGIBLE", candidate_eligibility(candidate)["reasons"])

    def test_fetcher_respects_robots_size_content_type_and_paywall(self):
        robots = b"User-agent: *\nAllow: /\n"

        def oversized(request, timeout=None):
            if request.full_url.endswith("/robots.txt"):
                return FakeResponse(robots)
            return FakeResponse(b"x", content_length=2_000)

        config = {
            "timeout_seconds": 2, "max_bytes": 1000, "retries": 0,
            "per_domain_delay_seconds": 0,
        }
        with self.assertRaisesRegex(RuntimeError, "CONTENT_TOO_LARGE"):
            ConservativeFetcher(config, opener=oversized).fetch("https://example.org/guide")

        def denied(request, timeout=None):
            return FakeResponse(b"User-agent: *\nDisallow: /\n")

        with self.assertRaisesRegex(RuntimeError, "ROBOTS"):
            ConservativeFetcher(config, opener=denied).fetch("https://example.org/guide")

        def paywall(request, timeout=None):
            if request.full_url.endswith("/robots.txt"):
                return FakeResponse(robots)
            return FakeResponse(b"subscribe to continue")

        with self.assertRaisesRegex(RuntimeError, "PAYWALL"):
            ConservativeFetcher(config, opener=paywall).fetch("https://example.org/guide")


@unittest.skipUnless(os.environ.get("DATABASE_URL"), "DATABASE_URL غير مضبوط")
class PeriodicResearchIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = _connect_pg()
        ensure_knowledge_schema(cls.db)
        ensure_schema(cls.db)
        cls.token = uuid.uuid4().hex[:12]
        cls.url = f"https://research.example.org/{cls.token}"
        cls.run_ids = []
        cls.candidate_ids = []
        cls.source_ids = []
        cls.object_ids = []
        cls.original_config = get_config(cls.db)
        cls.registry_source_id = f"TEST-DOMAIN-{cls.token}"
        registry = create_source(cls.db, {
            "source_id": cls.registry_source_id,
            "title": "سجل نطاق بحث اختباري",
            "source_type": "official",
            "publisher": "مؤسسة بحثية موثوقة",
            "jurisdiction": "general",
            "source_url": "https://research.example.org/",
            "rights_status": "public",
            "license_note": "محتوى عام يسمح بالحفظ والمراجعة",
            "retrieved_at": "2026-09-01",
            "review_due_at": "2027-09-01",
            "status": "approved",
            "author_identity": "مؤسسة بحثية موثوقة",
            "material_type": "official_guidance",
            "methodology_note": "سجل نطاق مؤهل لاختبار الدورة",
            "publisher_trust": "authoritative",
            "publisher_continuity": "الناشر والنطاق مستمران",
            "site_age_evidence_url": "https://web.archive.org/example",
            "site_age_evidence_date": "2010-01-01",
            "document_date": "2026-08-01",
            "version_label": "registry-v1",
            "content_fingerprint": uuid.uuid4().hex + uuid.uuid4().hex,
            "trust_level": "authoritative",
            "reviewed_at": "2026-09-01",
        })
        if not registry["success"]:
            raise AssertionError(registry)
        cls.source_ids.append(cls.registry_source_id)

    @classmethod
    def tearDownClass(cls):
        cls.db.rollback()
        for source_id in cls.source_ids:
            cls.db.execute("DELETE FROM knowledge_objects WHERE source_id=?", (source_id,))
            cls.db.execute("DELETE FROM knowledge_versions WHERE source_id=?", (source_id,))
            cls.db.execute("DELETE FROM knowledge_sources WHERE source_id=?", (source_id,))
        for run_id in cls.run_ids:
            cls.db.execute("DELETE FROM knowledge_research_alerts WHERE run_id=?", (run_id,))
            cls.db.execute("DELETE FROM knowledge_research_candidates WHERE run_id=?", (run_id,))
            cls.db.execute("DELETE FROM knowledge_research_runs WHERE run_id=?", (run_id,))
        update_config(cls.db, cls.original_config)
        cls.db.commit()
        cls.db.close()

    def setUp(self):
        self.db.rollback()
        update_config(self.db, {
            "enabled": False, "max_topics": 2, "max_results_per_topic": 2,
            "max_fetches": 1, "max_bytes": 10000, "timeout_seconds": 2,
            "retries": 0, "per_domain_delay_seconds": 0,
        })

    def _run(self, content, provider=None):
        result = run_cycle(
            self.db, search_provider=provider or FakeProvider(self.url),
            fetcher=FakeFetcher(content), taxonomy={"sectors": ["legal"]},
            gaps=[], private_terms=["عميل سري"],
            now=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        self.run_ids.append(result["run_id"])
        rows = self.db.execute(
            "SELECT candidate_id FROM knowledge_research_candidates WHERE run_id=?",
            (result["run_id"],),
        ).fetchall()
        self.candidate_ids.extend(row["candidate_id"] for row in rows)
        return result, rows

    def test_provider_failure_is_explicit_not_false_success(self):
        result = run_cycle(
            self.db, search_provider=None, taxonomy={"sectors": ["legal"]}
        )
        self.run_ids.append(result["run_id"])
        self.assertFalse(result["success"])
        self.assertEqual("failed", result["status"])
        row = self.db.execute(
            "SELECT error_message,error_count FROM knowledge_research_runs WHERE run_id=?",
            (result["run_id"],),
        ).fetchone()
        self.assertEqual("SEARCH_PROVIDER_UNAVAILABLE", row["error_message"])

    def test_overlap_is_skipped_safely(self):
        with patch("sana_research_cycle._acquire_run_lock", return_value=False):
            result = run_cycle(
                self.db, search_provider=FakeProvider(self.url),
                taxonomy={"sectors": ["legal"]},
            )
        self.run_ids.append(result["run_id"])
        self.assertEqual("RUN_ALREADY_ACTIVE", result["error"])
        row = self.db.execute(
            "SELECT status FROM knowledge_research_runs WHERE run_id=?", (result["run_id"],)
        ).fetchone()
        self.assertEqual("skipped_overlap", row["status"])

    def test_real_database_lock_blocks_a_second_connection(self):
        holder = _connect_pg()
        try:
            holder.execute("SELECT pg_advisory_xact_lock(?)", (813_260_930,))
            second = _connect_pg()
            try:
                result = run_cycle(
                    second, search_provider=FakeProvider(self.url),
                    taxonomy={"sectors": ["legal"]},
                )
                self.run_ids.append(result["run_id"])
                self.assertEqual("RUN_ALREADY_ACTIVE", result["error"])
            finally:
                second.close()
        finally:
            holder.rollback()
            holder.close()

    def test_approval_rechecks_domain_registry_eligibility(self):
        provider = FakeProvider(
            f"https://research.example.org/expiry-{self.token}",
            title="مرشح يتطلب إعادة تحقق",
        )
        result, rows = self._run(
            f"fresh candidate for registry recheck {self.token}", provider=provider
        )
        self.assertTrue(result["success"])
        candidate_id = rows[0]["candidate_id"]
        self.db.execute(
            "UPDATE knowledge_sources SET review_due_at='2020-01-01' WHERE source_id=?",
            (self.registry_source_id,),
        )
        self.db.commit()
        try:
            blocked = review_candidate(self.db, candidate_id, "approved", "test-reviewer")
            self.assertEqual("SOURCE_DOMAIN_NO_LONGER_ELIGIBLE", blocked["error"])
        finally:
            self.db.execute(
                "UPDATE knowledge_sources SET review_due_at='2027-09-01' WHERE source_id=?",
                (self.registry_source_id,),
            )
            self.db.commit()

    def test_pending_dedupe_change_review_gate_and_rollback(self):
        version_one = f"evidence based operating guidance version one {self.token}"
        version_two = (
            f"evidence based operating guidance version two with a meaningful update {self.token}"
        )
        first, rows = self._run(version_one)
        self.assertTrue(first["success"])
        self.assertEqual(1, first["discovered"])
        first_id = rows[0]["candidate_id"]
        self.assertEqual([], [
            item for item in search_knowledge(self.db, "operating guidance", sector="professional_services")
            if item.get("source_url") == self.url
        ])

        approved = review_candidate(self.db, first_id, "approved", "test-reviewer")
        self.assertTrue(approved["success"], approved)
        self.source_ids.append(approved["source_id"])
        found = search_knowledge(
            self.db, "operating guidance", sector="professional_services"
        )
        self.assertIn(approved["source_id"], {item.get("source_id") for item in found})

        duplicate, _ = self._run(version_one)
        self.assertEqual(1, duplicate["duplicate"])

        changed, changed_rows = self._run(version_two)
        self.assertEqual(1, changed["discovered"])
        changed_id = changed_rows[0]["candidate_id"]
        changed_row = self.db.execute(
            "SELECT previous_candidate_id,diff_text,status FROM knowledge_research_candidates WHERE candidate_id=?",
            (changed_id,),
        ).fetchone()
        self.assertEqual("pending_review", changed_row["status"])
        self.assertTrue(changed_row["previous_candidate_id"])
        self.assertIn("meaningful update", changed_row["diff_text"])

        changed_approval = review_candidate(self.db, changed_id, "approved", "test-reviewer")
        self.assertTrue(changed_approval["success"], changed_approval)
        self.assertEqual(approved["source_id"], changed_approval["source_id"])
        versions = self.db.execute(
            "SELECT version_label FROM knowledge_versions WHERE source_id=?",
            (approved["source_id"],),
        ).fetchall()
        self.assertGreaterEqual(len(versions), 2)

        rolled_back = review_candidate(self.db, changed_id, "rollback", "test-reviewer")
        self.assertTrue(rolled_back["success"], rolled_back)
        self.assertEqual(first_id, rolled_back["candidate_id"])
        current = self.db.execute(
            """SELECT content_text FROM knowledge_research_candidates
               WHERE canonical_url=? AND status='approved'""", (self.url,)
        ).fetchone()
        self.assertIn("version one", current["content_text"])

    def test_schedule_disabled_and_not_due_do_not_call_provider(self):
        self.assertEqual("disabled", run_due_cycle(_connect_pg)["status"])
        update_config(self.db, {"enabled": True, "interval_hours": 24})
        run_id = f"KRR-SCHEDULE-{self.token}"
        self.db.execute("""INSERT INTO knowledge_research_runs
            (run_id,trigger_type,status,started_at,completed_at)
            VALUES (?,'scheduled','success',now(),now()) ON CONFLICT DO NOTHING""", (run_id,))
        self.db.commit()
        self.run_ids.append(run_id)
        factory_called = []
        result = run_due_cycle(_connect_pg, lambda: factory_called.append(True))
        self.assertEqual("not_due", result["status"])
        self.assertEqual([], factory_called)


if __name__ == "__main__":
    unittest.main(verbosity=2)