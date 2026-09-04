import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from threading import Barrier, Lock, local
from unittest.mock import MagicMock, patch

import app as sana_app
import psycopg2
import sana_decision_room
from sana_decision_room import (
    create_backlog,
    create_risk,
    create_sop,
    decision_room,
    ensure_schema,
    list_sop_applications,
    list_reminders,
    materialize_due_reminders,
    promote_sop,
    record_sop_application,
    update_reminder_status,
    update_task,
)
from sana_growth_os import (
    METRIC_DEFINITIONS,
    create_baseline,
    ensure_schema as ensure_growth_schema,
    seed_growth_os,
)
from sana_growth_engine import (
    approve_decision,
    begin_experiment,
    close_experiment,
    create_bottleneck_cycle,
    create_experiment,
)


class DecisionRoomAcceptanceTests(unittest.TestCase):
    def test_today_card_precedes_four_collapsed_detail_groups(self):
        template = (
            Path(__file__).parent / "templates" / "01-ceo-home.html"
        ).read_text(encoding="utf-8")
        today_position = template.index('aria-label="بطاقة اليوم"')
        details_position = template.index('<details class="room-details">')
        self.assertLess(today_position, details_position)
        for group in ("القرار", "الدليل", "التنفيذ", "الخطر"):
            self.assertIn(f"<summary>{group}</summary>", template)
        self.assertNotIn('<details class="room-group" open', template)
        self.assertIn("طلب الدليل الأعلى أثرًا", template)

    @classmethod
    def setUpClass(cls):
        sana_app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        cls.db = sana_app._connect_pg()
        ensure_growth_schema(cls.db)
        seed_growth_os(cls.db)
        ensure_schema(cls.db)
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def setUp(self):
        self.db.rollback()
        self.company_id = f"DR{uuid.uuid4().hex[:8].upper()}"
    @classmethod
    def setUpClass(cls):
        sana_app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        cls.db = sana_app._connect_pg()
        ensure_growth_schema(cls.db)
        seed_growth_os(cls.db)
        ensure_schema(cls.db)
        cls.db.commit()

    @classmethod

    def tearDownClass(cls):
        cls.db.close()

    def setUp(self):
        self.db.rollback()
        self.company_id = f"DR{uuid.uuid4().hex[:8].upper()}"
        self.other_id = f"DR{uuid.uuid4().hex[:8].upper()}"
        for company_id in (self.company_id, self.other_id):
            self.db.execute(
                """INSERT INTO companies
                   (company_id,name,sector,city,employee_count,annual_revenue,main_goal,
                    signup_code)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (company_id, f"شركة {company_id}", "خدمات B2B", "الرياض", 10, 100000,
                 "النمو", f"TEST-{company_id}"),
            )
            self.db.execute(
                """INSERT INTO user_accounts
                   (account_id,email,password_hash,company_id)
                   VALUES (?,?,?,?)""",
                (f"ACC-{company_id}", f"{company_id.lower()}@test.local",
                 "not-used", company_id),
            )
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        for company_id in (self.company_id, self.other_id):
            decision_ids = [
                row["decision_id"]
                for row in self.db.execute(
                    "SELECT decision_id FROM decisions WHERE company_id=?",
                    (company_id,),
                ).fetchall()
            ]
            if decision_ids:
                placeholders = ",".join("?" for _ in decision_ids)
                self.db.execute(
                    f"DELETE FROM decision_asset_impacts WHERE decision_id IN ({placeholders})",
                    decision_ids,
                )
            for table in (
                "execution_task_audit", "execution_backlog_audit",
                "execution_reminder_attempts", "execution_reminders",
                "execution_owner_bindings",
                "execution_sop_applications",
                "execution_sop_versions", "execution_risks", "execution_backlog",
                "execution_sops", "gos_experiment_decisions",
                "gos_experiment_process", "gos_learning_links", "gos_experiments",
                "gos_bottlenecks", "gos_bottleneck_cycles", "gos_truth_records",
                "tasks", "decisions", "evidence", "gos_company_profiles",
            ):
                column = "company_id"
                if table == "gos_experiment_process":
                    self.db.execute(
                        """DELETE FROM gos_experiment_process
                           WHERE experiment_id IN
                             (SELECT experiment_id FROM gos_experiments WHERE company_id=?)""",
                        (company_id,),
                    )
                elif table == "gos_learning_links":
                    self.db.execute(
                        "DELETE FROM gos_learning_links WHERE company_id=?",
                        (company_id,),
                    )
                else:
                    self.db.execute(f"DELETE FROM {table} WHERE {column}=?", (company_id,))
            self.db.execute(
                """DELETE FROM gos_baseline_metrics WHERE baseline_id IN
                   (SELECT baseline_id FROM gos_baselines WHERE company_id=?)""",
                (company_id,),
            )
            self.db.execute("DELETE FROM gos_baselines WHERE company_id=?", (company_id,))
            self.db.execute("DELETE FROM user_accounts WHERE company_id=?", (company_id,))
            self.db.execute("DELETE FROM companies WHERE company_id=?", (company_id,))
        self.db.commit()

    def _evidence(self, company_id=None):
        company_id = company_id or self.company_id
        evidence_id = f"E{uuid.uuid4().hex[:10].upper()}"
        self.db.execute(
            """INSERT INTO evidence
               (evidence_id,company_id,title,source_type,confidence,evidence_type,source_ref)
               VALUES (?,?,?,?,?,?,?)""",
            (evidence_id, company_id, "دليل موثق", "manual-test", 90, "Fact", "test:evidence"),
        )
        self.db.commit()
        return evidence_id

    def _baseline(self, company_id=None, value=10):
        company_id = company_id or self.company_id
        metrics = {
            key: {
                "value": value,
                "source_ref": f"ledger:{company_id}:{key}",
                "confidence": 90,
                "verification_status": "VERIFIED",
                "source_category": "SYSTEM",
            }
            for key, *_ in METRIC_DEFINITIONS
        }
        baseline = create_baseline(self.db, company_id, {
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "observed_at": "2026-09-01",
            "confidence": 90,
            "source_ref": f"ledger:{company_id}:august",
            "metrics": metrics,
        })
        self.db.commit()
        return baseline

    def _growth_experiment(self):
        """Create a fully sourced experiment fixture for room transition tests."""
        from sana_growth_os import create_truth_record

        baseline = self._baseline()
        truth = create_truth_record(self.db, self.company_id, {
            "subject": "عدد الفرص المؤهلة",
            "value": 4,
            "classification": "Fact",
            "source_ref": "crm:decision-room-test",
            "observed_at": "2026-09-01",
            "confidence": 90,
        })
        cycle = create_bottleneck_cycle(self.db, self.company_id, {
            "baseline_id": baseline["baseline_id"],
            "candidates": [{
                "problem": "انخفاض التحويل",
                "evidence_ids": [truth["truth_id"]],
                "financial_impact": {
                    "value": 1000, "currency": "SAR",
                    "source_ref": "finance:decision-room-test",
                },
                "candidate_cause": "المتابعة غير منتظمة",
                "confidence": 90,
                "priority": 90,
            }],
        })
        experiment = create_experiment(self.db, self.company_id, {
            "cycle_id": cycle["cycle_id"],
            "hypothesis": "المتابعة الأسرع ترفع الإيراد",
            "one_change": "متابعة خلال ساعتين",
            "target_segment": "الفرص المؤهلة",
            "owner": "مالك المبيعات",
            "start_date": "2026-09-02",
            "kpi": "revenue",
            "baseline_id": baseline["baseline_id"],
            "target": 15,
            "success_boundary": "الإيراد >= 15",
            "stop_boundary": "الإيراد < 8",
            "evidence_ids": [truth["truth_id"]],
            "source_ref": "test:decision-room-experiment",
        })
        return baseline, truth, experiment

    def test_empty_company_and_incomplete_baseline_are_deferred(self):
        room = decision_room(self.db, self.company_id)
        self.assertEqual(3, room["top_metrics_missing"])
        self.assertEqual("missing", room["bottleneck"]["status"])
        self.assertEqual("missing", room["running_experiment"]["status"])
        self.assertEqual("missing", room["decision_today"]["status"])
        self.assertEqual("new_company", room["today"]["status"])
        self.assertEqual("ابدأ التشخيص", room["today"]["action"]["label"])
        self.assertEqual("/case/new", room["today"]["action"]["href"])
        self.assertEqual(8, len(room["section_order"]))

    def test_today_stops_at_evidence_before_offering_decision(self):
        self._evidence()
        room = decision_room(self.db, self.company_id)
        self.assertEqual("evidence_needed", room["today"]["status"])
        self.assertEqual("/case/new", room["today"]["action"]["href"])
        self.assertEqual("وثّق هذا الدليل", room["today"]["action"]["label"])
        self.assertEqual(
            "baseline", room["top_evidence_request"]["key"]
        )
        self.assertEqual(
            room["top_evidence_request"]["title"],
            room["today"]["evidence"][0]["value"],
        )
        self.assertTrue(room["today"]["evidence"])
        self.assertEqual(1, len(room["today"]["evidence"]))
        self.assertEqual("missing", room["today"]["evidence"][0]["status"])

    def test_documented_experiment_decision_has_one_approval_state(self):
        _, truth, experiment = self._growth_experiment()
        begin_experiment(self.db, self.company_id, experiment["experiment_id"])
        closed = close_experiment(self.db, self.company_id, experiment["experiment_id"], {
            "actual_result": {
                "value": 16,
                "source_ref": "ledger:decision-room-result",
                "observed_at": "2026-09-30",
            },
            "decision": "Scale",
            "reason": "النتيجة موثقة وتجاوزت الهدف.",
            "evidence_ids": [truth["truth_id"]],
            "source_ref": "review:decision-room",
        })
        room = decision_room(self.db, self.company_id)
        self.assertEqual("decision_pending", room["today"]["status"])
        self.assertEqual("approve_decision", room["today"]["action"]["kind"])
        self.assertEqual(closed["decision_id"], room["today"]["action"]["decision_id"])
        self.assertEqual("proposed", room["decision_today"]["status"])
        self.assertEqual("missing", room["running_experiment"]["status"])

    def test_running_experiment_is_measure_only_without_early_approval(self):
        _, _, experiment = self._growth_experiment()
        begin_experiment(self.db, self.company_id, experiment["experiment_id"])
        room = decision_room(self.db, self.company_id)
        self.assertEqual("measure", room["today"]["status"])
        self.assertEqual("سجّل نتيجة التجربة", room["today"]["action"]["label"])
        self.assertNotIn("decision_id", room["today"]["action"])
        self.assertEqual("missing", room["decision_today"]["status"])

    def test_ready_task_is_one_execution_step_and_incomplete_task_is_blocked(self):
        _, truth, experiment = self._growth_experiment()
        begin_experiment(self.db, self.company_id, experiment["experiment_id"])
        closed = close_experiment(self.db, self.company_id, experiment["experiment_id"], {
            "actual_result": {
                "value": 16,
                "source_ref": "ledger:decision-room-task-result",
                "observed_at": "2026-09-30",
            },
            "decision": "Scale",
            "reason": "النتيجة موثقة.",
            "evidence_ids": [truth["truth_id"]],
            "source_ref": "review:decision-room-task",
        })
        approve_decision(self.db, self.company_id, closed["decision_id"])
        ready_id = f"T{uuid.uuid4().hex[:10].upper()}"
        self.db.execute(
            """INSERT INTO tasks
               (task_id,company_id,title,status,owner_user_id,approver_user_id,due_date,kpi)
               VALUES (?,?,?,?,?,?,?,?)""",
            (ready_id, self.company_id, "تنفيذ القياس", "لم تبدأ",
             "owner-a", "approver-b", "2026-09-20", "الإيراد"),
        )
        self.db.commit()
        room = decision_room(self.db, self.company_id)
        self.assertEqual("execute", room["today"]["status"])
        self.assertEqual(ready_id, room["today"]["task"]["task_id"])
        self.assertEqual("افتح المهمة الأولى", room["today"]["action"]["label"])

        self.db.execute("DELETE FROM tasks WHERE task_id=?", (ready_id,))
        incomplete_id = f"T{uuid.uuid4().hex[:10].upper()}"
        self.db.execute(
            """INSERT INTO tasks
               (task_id,company_id,title,status,owner_user_id,due_date,kpi)
               VALUES (?,?,?,?,?,?,?)""",
            (incomplete_id, self.company_id, "مهمة بلا موعد", "لم تبدأ",
             "owner-a", None, "الإيراد"),
        )
        self.db.commit()
        room = decision_room(self.db, self.company_id)
        self.assertEqual("execution_blocked", room["today"]["status"])
        self.assertEqual(["Approver", "Deadline"], room["today"]["task"]["missing_fields"])
        self.assertIn("Approver", room["today"]["reason"])
        self.assertIn("Deadline", room["today"]["reason"])

    def test_task_responsibility_different_owners_delay_and_isolation(self):
        task_id = f"T{uuid.uuid4().hex[:10].upper()}"
        self.db.execute(
            "INSERT INTO tasks(task_id,company_id,title,status) VALUES (?,?,?,?)",
            (task_id, self.company_id, "مهمة تنفيذ", "لم تبدأ"),
        )
        self.db.commit()
        updated = update_task(self.db, self.company_id, task_id, {
            "owner_user_id": "owner-a", "approver_user_id": "approver-b",
            "deadline": "2026-09-10", "kpi": "3 عقود",
            "status": "متوقفة", "delay_reason": "بانتظار بيانات العميل",
            "source_ref": "acceptance-test",
        }, "tester")
        self.db.commit()
        self.assertNotEqual(updated["owner_user_id"], updated["approver_user_id"])
        self.assertTrue(updated["delay_reason"])
        with self.assertRaises(LookupError):
            update_task(self.db, self.other_id, task_id, updated, "tester")

    def _decision_for_approval(self):
        decision_id = f"D{uuid.uuid4().hex[:10].upper()}"
        self.db.execute(
            """INSERT INTO decisions
               (decision_id,company_id,title,status)
               VALUES (?,?,?,?)""",
            (decision_id, self.company_id, "قرار يحتاج تنفيذًا", "مقترح"),
        )
        self.db.commit()
        return decision_id

    def _approval_client(self):
        client = sana_app.app.test_client()
        with client.session_transaction() as session:
            session["account_id"] = f"ACC-{self.company_id}"
            session["company_id"] = self.company_id
            session["email"] = f"{self.company_id.lower()}@test.local"
        return client

    def test_approval_rejects_missing_responsibility_without_partial_state(self):
        decision_id = self._decision_for_approval()
        client = self._approval_client()
        response = client.post(
            f"/api/decisions/{decision_id}/approve",
            json={"success_metric": "رفع التحويل"},
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual(
            "DECISION_RESPONSIBILITY_FIELDS_REQUIRED",
            response.get_json()["error"],
        )
        decision = self.db.execute(
            "SELECT status,owner_name,due_date,success_metric FROM decisions WHERE decision_id=?",
            (decision_id,),
        ).fetchone()
        self.assertEqual("مقترح", decision["status"])
        self.assertIsNone(decision["owner_name"])
        self.assertIsNone(decision["due_date"])
        self.assertIsNone(decision["success_metric"])
        self.assertIsNone(
            self.db.execute(
                "SELECT task_id FROM tasks WHERE decision_id=?", (decision_id,)
            ).fetchone()
        )

    def test_approval_rolls_back_when_task_creation_fails_and_retry_is_idempotent(self):
        decision_id = self._decision_for_approval()
        client = self._approval_client()
        payload = {
            "owner_name": "مالك القرار",
            "due_date": "2026-09-10",
            "success_metric": "رفع التحويل من 5% إلى 10%",
            "next_action": "إطلاق التجربة الأولى",
        }
        request_db = sana_app._connect_pg()
        original_execute = request_db.execute
        failed_once = {"value": False}

        def fail_task_insert(sql, params=()):
            if "INSERT INTO tasks" in sql and not failed_once["value"]:
                failed_once["value"] = True
                raise RuntimeError("simulated task failure")
            return original_execute(sql, params)

        try:
            with patch.object(sana_app, "get_db", return_value=request_db), \
                 patch.object(request_db, "execute", side_effect=fail_task_insert):
                failed = client.post(
                    f"/api/decisions/{decision_id}/approve", json=payload
                )
        finally:
            request_db.close()
        self.assertEqual(500, failed.status_code)
        self.assertEqual("DECISION_APPROVAL_FAILED", failed.get_json()["error"])
        self.assertTrue(failed.get_json()["retryable"])
        self.assertEqual(
            "مقترح",
            self.db.execute(
                "SELECT status FROM decisions WHERE decision_id=?", (decision_id,)
            ).fetchone()["status"],
        )
        self.assertIsNone(
            self.db.execute(
                "SELECT task_id FROM tasks WHERE decision_id=?", (decision_id,)
            ).fetchone()
        )

        retried = client.post(
            f"/api/decisions/{decision_id}/approve", json=payload
        )
        self.assertEqual(200, retried.status_code, retried.get_data(as_text=True))
        self.assertEqual("معتمد", retried.get_json()["data"]["status"])
        self.assertEqual(
            1,
            self.db.execute(
                "SELECT COUNT(*) FROM tasks WHERE decision_id=?", (decision_id,)
            ).fetchone()[0],
        )
        retried_again = client.post(
            f"/api/decisions/{decision_id}/approve", json=payload
        )
        self.assertEqual(200, retried_again.status_code)
        self.assertEqual(
            1,
            self.db.execute(
                "SELECT COUNT(*) FROM tasks WHERE decision_id=?", (decision_id,)
            ).fetchone()[0],
        )

    def test_approval_retries_transient_serialization_conflict(self):
        decision_id = self._decision_for_approval()
        client = self._approval_client()
        payload = {
            "owner_name": "مالك القرار",
            "due_date": "2026-09-10",
            "success_metric": "رفع التحويل من 5% إلى 10%",
            "next_action": "إطلاق التجربة الأولى",
        }
        original_approval = sana_decision_room.approve_decision_with_task
        calls = {"count": 0}

        def fail_once_then_approve(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise psycopg2.errors.SerializationFailure("simulated conflict")
            return original_approval(*args, **kwargs)

        with patch.object(
            sana_decision_room,
            "approve_decision_with_task",
            side_effect=fail_once_then_approve,
        ):
            response = client.post(
                f"/api/decisions/{decision_id}/approve", json=payload
            )

        self.assertEqual(200, response.status_code, response.get_data(as_text=True))
        self.assertEqual(sana_app.DECISION_APPROVAL_MAX_ATTEMPTS - 1, calls["count"])
        self.assertEqual("معتمد", response.get_json()["data"]["status"])
        self.assertEqual(
            1,
            self.db.execute(
                "SELECT COUNT(*) FROM tasks WHERE decision_id=?", (decision_id,)
            ).fetchone()[0],
        )

    def test_approval_logs_and_returns_conflict_after_retry_limit(self):
        decision_id = self._decision_for_approval()
        client = self._approval_client()
        payload = {
            "owner_name": "مالك القرار",
            "due_date": "2026-09-10",
            "success_metric": "رفع التحويل من 5% إلى 10%",
            "next_action": "إطلاق التجربة الأولى",
        }
        calls = {"count": 0}

        def always_conflict(*args, **kwargs):
            calls["count"] += 1
            raise psycopg2.errors.DeadlockDetected("simulated deadlock")

        with patch.object(
            sana_decision_room,
            "approve_decision_with_task",
            side_effect=always_conflict,
        ), self.assertLogs(sana_app.app.logger, level="WARNING") as logs:
            response = client.post(
                f"/api/decisions/{decision_id}/approve", json=payload
            )

        self.assertEqual(500, response.status_code)
        body = response.get_json()
        self.assertEqual("DECISION_APPROVAL_FAILED", body["error"])
        self.assertTrue(body["retryable"])
        self.assertTrue(body["conflict"])
        self.assertEqual(sana_app.DECISION_APPROVAL_MAX_ATTEMPTS, body["attempts"])
        self.assertEqual(sana_app.DECISION_APPROVAL_MAX_ATTEMPTS, calls["count"])
        self.assertTrue(
            any("conflict exhausted retries" in entry for entry in logs.output)
        )
        self.assertEqual(
            "مقترح",
            self.db.execute(
                "SELECT status FROM decisions WHERE decision_id=?", (decision_id,)
            ).fetchone()["status"],
        )
        self.assertEqual(
            0,
            self.db.execute(
                "SELECT COUNT(*) FROM tasks WHERE decision_id=?", (decision_id,)
            ).fetchone()[0],
        )

    def test_concurrent_postgres_serializable_approvals_create_one_complete_task(self):
        decision_id = self._decision_for_approval()
        payload = {
            "owner_name": "مالك القرار",
            "due_date": "2026-09-10",
            "success_metric": "رفع التحويل من 5% إلى 10%",
            "next_action": "إطلاق التجربة الأولى",
        }
        entered_approval = Barrier(2)
        original_approval = sana_decision_room.approve_decision_with_task
        connection_lock = Lock()
        request_local = local()
        backend_pids = []
        close_statuses = {}
        attempts_by_pid = {}

        def synchronize_approvals(*args, **kwargs):
            db = args[0]
            pid = backend_pids_by_connection[id(db)]
            with connection_lock:
                attempts_by_pid[pid] = attempts_by_pid.get(pid, 0) + 1
                attempt = attempts_by_pid[pid]
            if attempt == 1:
                entered_approval.wait(timeout=15)
            return original_approval(*args, **kwargs)

        backend_pids_by_connection = {}

        def get_thread_db():
            return request_local.db

        def approve():
            request_db = sana_app._connect_pg()
            request_db._conn.set_isolation_level(
                psycopg2.extensions.ISOLATION_LEVEL_SERIALIZABLE
            )
            pid = request_db.execute("SELECT pg_backend_pid()").fetchone()[0]
            request_db.rollback()
            with connection_lock:
                backend_pids_by_connection[id(request_db)] = pid
                backend_pids.append(pid)

            request_local.db = request_db
            try:
                client = self._approval_client()
                return client.post(
                    f"/api/decisions/{decision_id}/approve", json=payload
                )
            finally:
                close_statuses[pid] = request_db._conn.status
                request_db.close()

        with patch.object(
            sana_decision_room,
            "approve_decision_with_task",
            side_effect=synchronize_approvals,
        ), patch.object(sana_app, "get_db", side_effect=get_thread_db), \
             self.assertLogs(sana_app.app.logger, level="INFO") as logs:
            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(lambda _: approve(), range(2)))

        # العزل القابل للتسلسل يجعل اللقطة التي أخذها الطلبان قبل القفل
        # متعارضة فعليًا في PostgreSQL؛ يجب أن يعيد أحدهما المعاملة من الصفر.
        self.assertEqual(
            2,
            len(set(backend_pids)),
            f"الطلبان لم يستخدما اتصالين PostgreSQL منفصلين: {backend_pids}",
        )
        self.assertTrue(
            any("Retrying atomic decision approval after transient conflict" in entry
                for entry in logs.output),
            logs.output,
        )
        self.assertTrue(
            any(attempts > 1 for attempts in attempts_by_pid.values()),
            attempts_by_pid,
        )
        self.assertTrue(
            all(1 <= attempts <= sana_app.DECISION_APPROVAL_MAX_ATTEMPTS
                for attempts in attempts_by_pid.values()),
            attempts_by_pid,
        )
        for response in responses:
            self.assertEqual(200, response.status_code, response.get_data(as_text=True))
            self.assertEqual("معتمد", response.get_json()["data"]["status"])

        self.assertEqual(
            2,
            len(close_statuses),
            f"لم تُغلق اتصالات الطلبين: {close_statuses}",
        )
        self.assertTrue(
            all(status == psycopg2.extensions.STATUS_READY
                for status in close_statuses.values()),
            f"أُغلقت اتصالات وبها معاملة مفتوحة: {close_statuses}",
        )

        decision = self.db.execute(
            """SELECT status,owner_name,due_date,success_metric
               FROM decisions WHERE decision_id=? AND company_id=?""",
            (decision_id, self.company_id),
        ).fetchone()
        self.assertEqual("معتمد", decision["status"])
        self.assertTrue(
            all(decision[field] for field in ("owner_name", "due_date", "success_metric"))
        )
        self.assertEqual(
            1,
            self.db.execute(
                """SELECT COUNT(*) FROM tasks
                   WHERE decision_id=? AND company_id=?""",
                (decision_id, self.company_id),
            ).fetchone()[0],
        )
        task = self.db.execute(
            """SELECT status,owner_user_id,approver_user_id,due_date,kpi
               FROM tasks WHERE decision_id=? AND company_id=?""",
            (decision_id, self.company_id),
        ).fetchone()
        self.assertEqual("لم تبدأ", task["status"])
        self.assertTrue(
            all(task[field] for field in (
                "owner_user_id", "approver_user_id", "due_date", "kpi"
            ))
        )

    def test_urgent_risk_and_backlog_do_not_change_active_priority(self):
        evidence_id = self._evidence()
        risk = create_risk(self.db, self.company_id, {
            "title": "خطر عاجل", "description": "تأخر التسليم",
            "evidence_ids": [evidence_id], "impact": "فقد العميل",
            "owner_id": "owner-a", "probability": 5, "severity": 5,
            "mitigation_plan": "مراجعة يومية", "source_ref": "evidence:test",
        })
        item = create_backlog(self.db, self.company_id, {
            "title": "فكرة", "description": "اختبار قناة جديدة",
            "expected_impact": 5, "ease": 3, "speed": 4,
            "evidence_strength": 4, "profitability": 5, "risk": 2,
            "evidence_ids": [evidence_id], "source_ref": "evidence:test",
        })
        self.db.commit()
        room = decision_room(self.db, self.company_id)
        self.assertEqual(risk["risk_id"], room["urgent_risk"]["risk_id"])
        self.assertEqual("backlog", item["status"])
        self.assertNotIn("backlog", room["section_order"])
        with self.assertRaises(ValueError):
            create_risk(self.db, self.other_id, {
                "title": "ممنوع", "description": "x",
                "evidence_ids": [evidence_id], "impact": "x", "owner_id": "x",
                "probability": 5, "severity": 5, "mitigation_plan": "x",
                "source_ref": "test",
            })

    def test_sop_manual_gate_and_version(self):
        evidence_id = self._evidence()
        payload = {
            "title": "تسليم العميل", "owner_role": "مدير المشروع",
            "steps": ["راجع النطاق", "سلّم التقرير"],
            "checklist": ["النطاق مؤكد", "الاستلام موثق"],
            "sla": "48 ساعة", "kpi": "التسليم في الموعد",
            "reusable_template": "قالب تسليم", "evidence_ids": [evidence_id],
            "result": {"status": "success", "source_ref": "evidence:test"},
            "source_ref": "evidence:test", "maturity": "Manual",
        }
        result = create_sop(
            self.db, self.company_id,
            {**payload, "approve": True, "repetitions": 0}, "approver",
        )
        self.db.commit()
        self.assertEqual("approved", result["status"])
        self.assertEqual("Manual", result["maturity"])

    def test_sop_applications_compare_to_sourced_baseline_and_gate_automation(self):
        evidence_id = self._evidence()
        baseline = self._baseline()
        sop = create_sop(self.db, self.company_id, {
            "title": "متابعة الفرص", "owner_role": "مدير المبيعات",
            "steps": ["راجع الفرصة", "تابع العميل"],
            "checklist": ["تمت المتابعة"],
            "sla": "ساعتان", "kpi": "الإيراد",
            "reusable_template": "قالب متابعة", "evidence_ids": [evidence_id],
            "result": {"status": "manual_capture", "source_ref": "test:sop"},
            "source_ref": "test:sop", "maturity": "Manual", "approve": True,
        }, "approver")
        result_evidence_ids = [self._evidence() for _ in range(3)]
        for index, result_evidence_id in enumerate(result_evidence_ids, start=1):
            self.db.execute(
                """UPDATE evidence SET evidence_type='Fact',source_ref=?
                   WHERE evidence_id=? AND company_id=?""",
                (f"ledger:application-{index}", result_evidence_id, self.company_id),
            )
        application = {
            "version_id": sop["version_id"],
            "application_ref": "deal-001",
            "kpi": "revenue",
            "direction": "higher_is_better",
            "baseline_id": baseline["baseline_id"],
            "evidence_ids": [result_evidence_ids[0]],
            "result_evidence_id": result_evidence_ids[0],
            "result": {
                "value": 9,
                "source_ref": "ledger:application-1",
                "observed_at": "2026-09-10",
            },
        }
        first = record_sop_application(
            self.db, self.company_id, sop["sop_id"], application, "operator"
        )
        self.assertEqual("not_improved", first["comparison_status"])
        self.assertEqual(-1, first["delta"])
        with self.assertRaisesRegex(ValueError, "TWO_RECORDED_SUCCESSES"):
            promote_sop(self.db, self.company_id, sop["sop_id"], {
                "version_id": sop["version_id"], "maturity": "Automatable",
            }, "approver")
        for index, value in enumerate((12, 13), start=2):
            record_sop_application(
                self.db, self.company_id, sop["sop_id"], {
                    **application,
                    "application_ref": f"deal-00{index}",
                    "evidence_ids": [result_evidence_ids[index - 1]],
                    "result_evidence_id": result_evidence_ids[index - 1],
                    "result": {
                        "value": value,
                        "source_ref": f"ledger:application-{index}",
                        "observed_at": f"2026-09-{index + 9:02d}",
                    },
                }, "operator",
            )
        promoted = promote_sop(self.db, self.company_id, sop["sop_id"], {
            "version_id": sop["version_id"], "maturity": "Automatable",
        }, "approver")
        self.assertTrue(promoted["readiness"]["automatable"])
        self.assertEqual(2, promoted["readiness"]["successful_applications"])
        self.assertEqual(
            3, len(list_sop_applications(self.db, self.company_id, sop["sop_id"]))
        )
        with self.assertRaises(LookupError):
            list_sop_applications(self.db, self.other_id, sop["sop_id"])

    def test_sop_application_rejects_draft_reused_evidence_and_early_result(self):
        sop_evidence = self._evidence()
        baseline = self._baseline()
        draft = create_sop(self.db, self.company_id, {
            "title": "مسودة", "owner_role": "العمليات",
            "steps": ["نفّذ"], "checklist": ["تحقق"], "sla": "يوم",
            "kpi": "الإيراد", "reusable_template": "قالب",
            "evidence_ids": [sop_evidence],
            "result": {"status": "capture", "source_ref": "test:draft"},
            "source_ref": "test:draft", "maturity": "Manual", "approve": False,
        }, "approver")
        payload = {
            "version_id": draft["version_id"], "kpi": "revenue",
            "application_ref": "draft-run-001",
            "direction": "higher_is_better",
            "baseline_id": baseline["baseline_id"],
            "evidence_ids": [sop_evidence],
            "result_evidence_id": sop_evidence,
            "result": {
                "value": 12, "source_ref": "test:evidence",
                "observed_at": "2026-09-10",
            },
        }
        with self.assertRaisesRegex(ValueError, "APPROVED_ACTIVE_VERSION"):
            record_sop_application(
                self.db, self.company_id, draft["sop_id"], payload, "operator"
            )
        approved = create_sop(self.db, self.company_id, {
            **{
                "title": "معتمد", "owner_role": "العمليات",
                "steps": ["نفّذ"], "checklist": ["تحقق"], "sla": "يوم",
                "kpi": "الإيراد", "reusable_template": "قالب معتمد",
                "evidence_ids": [sop_evidence],
                "result": {"status": "capture", "source_ref": "test:approved"},
                "source_ref": "test:approved", "maturity": "Manual",
            },
            "approve": True,
        }, "approver")
        with self.assertRaisesRegex(ValueError, "MUST_FOLLOW_BASELINE"):
            record_sop_application(self.db, self.company_id, approved["sop_id"], {
                **payload, "version_id": approved["version_id"],
                "result": {**payload["result"], "observed_at": "2026-08-31"},
            }, "operator")
        with self.assertRaisesRegex(ValueError, "MUST_BE_INDEPENDENT"):
            record_sop_application(self.db, self.company_id, approved["sop_id"], {
                **payload, "version_id": approved["version_id"],
            }, "operator")

    def test_duplicate_sop_outcome_cannot_count_as_second_application(self):
        sop_evidence = self._evidence()
        baseline = self._baseline()
        sop = create_sop(self.db, self.company_id, {
            "title": "منع التكرار", "owner_role": "العمليات",
            "steps": ["نفّذ"], "checklist": ["تحقق"], "sla": "يوم",
            "kpi": "الإيراد", "reusable_template": "قالب",
            "evidence_ids": [sop_evidence],
            "result": {"status": "capture", "source_ref": "test:sop"},
            "source_ref": "test:sop", "maturity": "Manual", "approve": True,
        }, "approver")
        result_source = "ledger:one-observation"
        result_evidence = self._evidence()
        duplicate_evidence = self._evidence()
        for evidence_id in (result_evidence, duplicate_evidence):
            self.db.execute(
                """UPDATE evidence SET evidence_type='Fact',source_ref=?
                   WHERE evidence_id=? AND company_id=?""",
                (result_source, evidence_id, self.company_id),
            )
        payload = {
            "version_id": sop["version_id"], "application_ref": "run-001",
            "kpi": "revenue", "direction": "higher_is_better",
            "baseline_id": baseline["baseline_id"],
            "evidence_ids": [result_evidence],
            "result_evidence_id": result_evidence,
            "result": {
                "value": 12, "source_ref": result_source,
                "observed_at": "2026-09-12",
            },
        }
        record_sop_application(
            self.db, self.company_id, sop["sop_id"], payload, "operator"
        )
        with self.assertRaisesRegex(ValueError, "DUPLICATE_OUTCOME"):
            record_sop_application(self.db, self.company_id, sop["sop_id"], {
                **payload,
                "application_ref": "run-002",
                "evidence_ids": [duplicate_evidence],
                "result_evidence_id": duplicate_evidence,
            }, "operator")
        with self.assertRaisesRegex(ValueError, "TWO_RECORDED_SUCCESSES"):
            promote_sop(self.db, self.company_id, sop["sop_id"], {
                "version_id": sop["version_id"], "maturity": "Automatable",
            }, "approver")

    def test_sop_promotion_route_is_registered_before_direct_launch(self):
        rules = {rule.rule for rule in sana_app.app.url_map.iter_rules()}
        self.assertIn(
            "/api/companies/<company_id>/execution/sops/<sop_id>/promote",
            rules,
        )

    def test_profile_change_changes_room_labels(self):
        from sana_growth_os import set_company_profile
        profiles = self.db.execute(
            "SELECT profile_key FROM gos_project_profiles ORDER BY profile_key"
        ).fetchall()
        if len(profiles) < 2:
            self.skipTest("يلزم ملفا مشروع لاختبار التغيير")
        set_company_profile(self.db, self.company_id, profiles[1]["profile_key"], "test:profile")
        self.db.commit()
        room = decision_room(self.db, self.company_id)
        self.assertEqual(profiles[1]["profile_key"], room["current_state"]["profile_key"])
        self.assertEqual(room["profile"]["stages"], room["profile"]["stages"])

    def test_task_and_risk_reminders_are_internal_idempotent_and_isolated(self):
        task_id = f"T{uuid.uuid4().hex[:10].upper()}"
        due = (date.today() + timedelta(days=2)).isoformat()
        self.db.execute(
            """INSERT INTO tasks
               (task_id,company_id,title,status,owner_user_id,due_date)
               VALUES (?,?,?,?,?,?)""",
            (task_id, self.company_id, "مهمة قريبة", "قيد التنفيذ", "owner-a", due),
        )
        evidence_id = self._evidence()
        create_risk(self.db, self.company_id, {
            "title": "مراجعة قريبة", "description": "خطر",
            "evidence_ids": [evidence_id], "impact": "تأخير",
            "owner_id": "owner-b", "probability": 4, "severity": 4,
            "mitigation_plan": "راجع", "review_due_at": due,
            "source_ref": "test:risk",
        })
        first = materialize_due_reminders(self.db, company_id=self.company_id)
        second = materialize_due_reminders(self.db, company_id=self.company_id)
        self.db.commit()
        self.assertEqual(2, first["created"])
        self.assertEqual(0, second["created"])
        reminders = list_reminders(
            self.db, self.company_id,
            recipient_account_id=f"ACC-{self.company_id}",
        )
        self.assertEqual(2, len(reminders))
        self.assertEqual([], list_reminders(self.db, self.other_id, "owner-a"))
        task = self.db.execute(
            "SELECT status FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        self.assertEqual("قيد التنفيذ", task["status"])

    def test_overdue_reminder_owner_cannot_be_read_by_another_owner(self):
        task_id = f"T{uuid.uuid4().hex[:10].upper()}"
        self.db.execute(
            """INSERT INTO tasks
               (task_id,company_id,title,status,owner_user_id,due_date)
               VALUES (?,?,?,?,?,?)""",
            (task_id, self.company_id, "مهمة متأخرة", "لم تبدأ", "owner-a",
             (date.today() - timedelta(days=1)).isoformat()),
        )
        materialize_due_reminders(self.db, company_id=self.company_id)
        self.db.commit()
        reminder = list_reminders(
            self.db, self.company_id,
            recipient_account_id=f"ACC-{self.company_id}",
        )[0]
        self.assertEqual("overdue", reminder["reminder_kind"])
        with self.assertRaises(PermissionError):
            update_reminder_status(
                self.db, self.company_id, reminder["reminder_id"],
                "read", f"ACC-{self.other_id}",
            )
        update_reminder_status(
            self.db, self.company_id, reminder["reminder_id"], "read",
            f"ACC-{self.company_id}"
        )
        self.db.commit()
        attempts = self.db.execute(
            """SELECT * FROM execution_reminder_attempts
               WHERE reminder_id=? AND outcome='delivered'""",
            (reminder["reminder_id"],),
        ).fetchall()
        self.assertEqual(1, len(attempts))

    def test_unresolved_reminder_is_delivered_when_account_becomes_available(self):
        account_id = f"ACC-{self.company_id}"
        self.db.execute(
            "DELETE FROM user_accounts WHERE account_id=?", (account_id,)
        )
        task_id = f"T{uuid.uuid4().hex[:10].upper()}"
        self.db.execute(
            """INSERT INTO tasks
               (task_id,company_id,title,status,owner_user_id,due_date)
               VALUES (?,?,?,?,?,?)""",
            (task_id, self.company_id, "تنبيه قابل للاسترداد", "لم تبدأ",
             "owner-a", date.today().isoformat()),
        )
        first = materialize_due_reminders(self.db, company_id=self.company_id)
        self.db.commit()
        self.assertEqual(1, first["created"])
        unresolved = self.db.execute(
            """SELECT * FROM execution_reminders
               WHERE company_id=? AND entity_id=?""",
            (self.company_id, task_id),
        ).fetchone()
        self.assertIsNone(unresolved["recipient_account_id"])
        self.db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id) VALUES (?,?,?,?)""",
            (account_id, f"restored-{self.company_id.lower()}@test.local",
             "not-used", self.company_id),
        )
        recovered = materialize_due_reminders(
            self.db, company_id=self.company_id
        )
        self.db.commit()
        self.assertEqual(0, recovered["created"])
        self.assertEqual(1, recovered["recovered"])
        delivered = list_reminders(
            self.db, self.company_id, recipient_account_id=account_id
        )
        self.assertEqual(1, len(delivered))
        count = self.db.execute(
            """SELECT COUNT(*) AS c FROM execution_reminders
               WHERE company_id=? AND entity_id=?""",
            (self.company_id, task_id),
        ).fetchone()["c"]
        self.assertEqual(1, count)

    def test_explicit_owner_binding_delivers_only_to_mapped_account(self):
        from sana_decision_room import bind_owner_account
        first_account = f"ACC-{self.company_id}"
        second_account = f"ACC2-{self.company_id}"
        self.db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id) VALUES (?,?,?,?)""",
            (second_account, f"second-{self.company_id.lower()}@test.local",
             "not-used", self.company_id),
        )
        task_id = f"T{uuid.uuid4().hex[:10].upper()}"
        self.db.execute(
            """INSERT INTO tasks
               (task_id,company_id,title,status,owner_user_id,due_date)
               VALUES (?,?,?,?,?,?)""",
            (task_id, self.company_id, "مهمة متعددة الحسابات", "لم تبدأ",
             "سارة", date.today().isoformat()),
        )
        bind_owner_account(
            self.db, self.company_id, "سارة", second_account,
            first_account, "test:explicit-binding",
        )
        result = materialize_due_reminders(
            self.db, company_id=self.company_id
        )
        self.db.commit()
        self.assertEqual(1, result["created"])
        self.assertEqual([], list_reminders(
            self.db, self.company_id, recipient_account_id=first_account
        ))
        reminders = list_reminders(
            self.db, self.company_id, recipient_account_id=second_account
        )
        self.assertEqual(1, len(reminders))
        with self.assertRaises(PermissionError):
            update_reminder_status(
                self.db, self.company_id, reminders[0]["reminder_id"],
                "read", first_account,
            )
        client = sana_app.app.test_client()
        with client.session_transaction() as session:
            session["account_id"] = first_account
            session["company_id"] = self.company_id
            session["email"] = f"{self.company_id.lower()}@test.local"
        previous_csrf = sana_app.app.config.get("WTF_CSRF_ENABLED", True)
        sana_app.app.config["WTF_CSRF_ENABLED"] = False
        try:
            response = client.put(
                f"/api/companies/{self.company_id}/execution/reminder-owners",
                json={"owner_id": "سارة", "account_id": first_account},
            )
        finally:
            sana_app.app.config["WTF_CSRF_ENABLED"] = previous_csrf
        self.assertEqual(403, response.status_code)
        binding = self.db.execute(
            """SELECT account_id FROM execution_owner_bindings
               WHERE company_id=? AND owner_id=?""",
            (self.company_id, "سارة"),
        ).fetchone()
        self.assertEqual(second_account, binding["account_id"])

    def test_sole_account_delivery_is_revoked_when_company_becomes_ambiguous(self):
        from sana_decision_room import bind_owner_account
        first_account = f"ACC-{self.company_id}"
        second_account = f"ACC2-{self.company_id}"
        task_id = f"T{uuid.uuid4().hex[:10].upper()}"
        self.db.execute(
            """INSERT INTO tasks
               (task_id,company_id,title,status,owner_user_id,due_date)
               VALUES (?,?,?,?,?,?)""",
            (task_id, self.company_id, "تنبيه يتطلب تعيينًا", "لم تبدأ",
             "مالك بشري", date.today().isoformat()),
        )
        materialize_due_reminders(self.db, company_id=self.company_id)
        self.db.commit()
        self.assertEqual(1, len(list_reminders(
            self.db, self.company_id, recipient_account_id=first_account
        )))
        self.db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id) VALUES (?,?,?,?)""",
            (second_account, f"ambiguous-{self.company_id.lower()}@test.local",
             "not-used", self.company_id),
        )
        materialize_due_reminders(self.db, company_id=self.company_id)
        self.db.commit()
        self.assertEqual([], list_reminders(
            self.db, self.company_id, recipient_account_id=first_account
        ))
        self.assertEqual([], list_reminders(
            self.db, self.company_id, recipient_account_id=second_account
        ))
        bind_owner_account(
            self.db, self.company_id, "مالك بشري", second_account,
            "admin-preview", "test:resolve-ambiguity",
        )
        recovered = materialize_due_reminders(
            self.db, company_id=self.company_id
        )
        self.db.commit()
        self.assertEqual(1, recovered["recovered"])
        self.assertEqual([], list_reminders(
            self.db, self.company_id, recipient_account_id=first_account
        ))
        self.assertEqual(1, len(list_reminders(
            self.db, self.company_id, recipient_account_id=second_account
        )))

    def test_authenticated_reminder_inbox_is_never_cacheable(self):
        account_id = f"ACC-{self.company_id}"
        client = sana_app.app.test_client()
        with client.session_transaction() as session:
            session["account_id"] = account_id
            session["company_id"] = self.company_id
            session["email"] = f"{self.company_id.lower()}@test.local"
        response = client.get(
            f"/api/companies/{self.company_id}/execution/reminders"
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("no-store, private", response.headers["Cache-Control"])
        self.assertEqual("no-cache", response.headers["Pragma"])

    def test_one_shot_scheduler_runs_without_web_request(self):
        import run_execution_reminders
        fake_db = MagicMock()
        with patch.object(
            run_execution_reminders, "_connect_pg", return_value=fake_db
        ), patch.object(
            run_execution_reminders, "ensure_schema"
        ) as schema, patch.object(
            run_execution_reminders, "materialize_due_reminders",
            return_value={"status": "completed", "created": 1},
        ) as materialize:
            self.assertEqual(0, run_execution_reminders.main())
        schema.assert_called_once_with(fake_db)
        materialize.assert_called_once_with(fake_db, acquire_lock=True)
        fake_db.commit.assert_called_once()
        fake_db.close.assert_called_once()

    def test_scheduler_cycle_initializes_schema_before_materialization(self):
        import sana_decision_room
        fake_db = MagicMock()
        calls = []
        with patch.object(
            sana_decision_room, "ensure_schema",
            side_effect=lambda db: calls.append(("schema", db)),
        ), patch.object(
            sana_decision_room, "materialize_due_reminders",
            side_effect=lambda db, acquire_lock: (
                calls.append(("materialize", db)),
                {"status": "completed"},
            )[1],
        ):
            result = sana_decision_room.run_reminder_cycle(lambda: fake_db)
        self.assertEqual(["schema", "materialize"], [name for name, _ in calls])
        self.assertTrue(all(db is fake_db for _, db in calls))
        self.assertEqual({"status": "completed"}, result)
        fake_db.commit.assert_called_once()
        fake_db.close.assert_called_once()

    def test_scheduler_endpoint_rejects_missing_token(self):
        client = sana_app.app.test_client()
        response = client.post("/internal/execution-reminders/run")
        self.assertEqual(401, response.status_code)
        self.assertEqual("UNAUTHORIZED", response.get_json()["error"])

    def test_scheduler_endpoint_runs_shared_cycle_after_authentication(self):
        client = sana_app.app.test_client()
        with patch.object(
            sana_app, "_valid_scheduler_token", return_value=True
        ), patch(
            "sana_decision_room.run_reminder_cycle",
            return_value={"status": "completed", "created": 1},
        ) as cycle:
            response = client.post(
                "/internal/execution-reminders/run",
                headers={"Authorization": "Bearer test-token"},
            )
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["success"])
        cycle.assert_called_once_with(sana_app._connect_pg)

    def test_sop_promotion_route_is_registered_before_direct_launch(self):
        rules = {rule.rule for rule in sana_app.app.url_map.iter_rules()}
        self.assertIn(
            "/api/companies/<company_id>/execution/sops/<sop_id>/promote",
            rules,
        )

    def test_profile_change_changes_room_labels(self):
        from sana_growth_os import set_company_profile
        profiles = self.db.execute(
            "SELECT profile_key FROM gos_project_profiles ORDER BY profile_key"
        ).fetchall()
        if len(profiles) < 2:
            self.skipTest("يلزم ملفا مشروع لاختبار التغيير")
        set_company_profile(self.db, self.company_id, profiles[1]["profile_key"], "test:profile")
        self.db.commit()
        room = decision_room(self.db, self.company_id)
        self.assertEqual(profiles[1]["profile_key"], room["current_state"]["profile_key"])
        self.assertEqual(room["profile"]["stages"], room["profile"]["stages"])

    def test_task_and_risk_reminders_are_internal_idempotent_and_isolated(self):
        task_id = f"T{uuid.uuid4().hex[:10].upper()}"
        due = (date.today() + timedelta(days=2)).isoformat()
        self.db.execute(
            """INSERT INTO tasks
               (task_id,company_id,title,status,owner_user_id,due_date)
               VALUES (?,?,?,?,?,?)""",
            (task_id, self.company_id, "مهمة قريبة", "قيد التنفيذ", "owner-a", due),
        )
        evidence_id = self._evidence()
        create_risk(self.db, self.company_id, {
            "title": "مراجعة قريبة", "description": "خطر",
            "evidence_ids": [evidence_id], "impact": "تأخير",
            "owner_id": "owner-b", "probability": 4, "severity": 4,
            "mitigation_plan": "راجع", "review_due_at": due,
            "source_ref": "test:risk",
        })
        first = materialize_due_reminders(self.db, company_id=self.company_id)
        second = materialize_due_reminders(self.db, company_id=self.company_id)
        self.db.commit()
        self.assertEqual(2, first["created"])
        self.assertEqual(0, second["created"])
        reminders = list_reminders(
            self.db, self.company_id,
            recipient_account_id=f"ACC-{self.company_id}",
        )
        self.assertEqual(2, len(reminders))
        self.assertEqual([], list_reminders(self.db, self.other_id, "owner-a"))
        task = self.db.execute(
            "SELECT status FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        self.assertEqual("قيد التنفيذ", task["status"])

    def test_overdue_reminder_owner_cannot_be_read_by_another_owner(self):
        task_id = f"T{uuid.uuid4().hex[:10].upper()}"
        self.db.execute(
            """INSERT INTO tasks
               (task_id,company_id,title,status,owner_user_id,due_date)
               VALUES (?,?,?,?,?,?)""",
            (task_id, self.company_id, "مهمة متأخرة", "لم تبدأ", "owner-a",
             (date.today() - timedelta(days=1)).isoformat()),
        )
        materialize_due_reminders(self.db, company_id=self.company_id)
        self.db.commit()
        reminder = list_reminders(
            self.db, self.company_id,
            recipient_account_id=f"ACC-{self.company_id}",
        )[0]
        self.assertEqual("overdue", reminder["reminder_kind"])
        with self.assertRaises(PermissionError):
            update_reminder_status(
                self.db, self.company_id, reminder["reminder_id"],
                "read", f"ACC-{self.other_id}",
            )
        update_reminder_status(
            self.db, self.company_id, reminder["reminder_id"], "read",
            f"ACC-{self.company_id}"
        )
        self.db.commit()
        attempts = self.db.execute(
            """SELECT * FROM execution_reminder_attempts
               WHERE reminder_id=? AND outcome='delivered'""",
            (reminder["reminder_id"],),
        ).fetchall()
        self.assertEqual(1, len(attempts))

    def test_unresolved_reminder_is_delivered_when_account_becomes_available(self):
        account_id = f"ACC-{self.company_id}"
        self.db.execute(
            "DELETE FROM user_accounts WHERE account_id=?", (account_id,)
        )
        task_id = f"T{uuid.uuid4().hex[:10].upper()}"
        self.db.execute(
            """INSERT INTO tasks
               (task_id,company_id,title,status,owner_user_id,due_date)
               VALUES (?,?,?,?,?,?)""",
            (task_id, self.company_id, "تنبيه قابل للاسترداد", "لم تبدأ",
             "owner-a", date.today().isoformat()),
        )
        first = materialize_due_reminders(self.db, company_id=self.company_id)
        self.db.commit()
        self.assertEqual(1, first["created"])
        unresolved = self.db.execute(
            """SELECT * FROM execution_reminders
               WHERE company_id=? AND entity_id=?""",
            (self.company_id, task_id),
        ).fetchone()
        self.assertIsNone(unresolved["recipient_account_id"])
        self.db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id) VALUES (?,?,?,?)""",
            (account_id, f"restored-{self.company_id.lower()}@test.local",
             "not-used", self.company_id),
        )
        recovered = materialize_due_reminders(
            self.db, company_id=self.company_id
        )
        self.db.commit()
        self.assertEqual(0, recovered["created"])
        self.assertEqual(1, recovered["recovered"])
        delivered = list_reminders(
            self.db, self.company_id, recipient_account_id=account_id
        )
        self.assertEqual(1, len(delivered))
        count = self.db.execute(
            """SELECT COUNT(*) AS c FROM execution_reminders
               WHERE company_id=? AND entity_id=?""",
            (self.company_id, task_id),
        ).fetchone()["c"]
        self.assertEqual(1, count)

    def test_explicit_owner_binding_delivers_only_to_mapped_account(self):
        from sana_decision_room import bind_owner_account
        first_account = f"ACC-{self.company_id}"
        second_account = f"ACC2-{self.company_id}"
        self.db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id) VALUES (?,?,?,?)""",
            (second_account, f"second-{self.company_id.lower()}@test.local",
             "not-used", self.company_id),
        )
        task_id = f"T{uuid.uuid4().hex[:10].upper()}"
        self.db.execute(
            """INSERT INTO tasks
               (task_id,company_id,title,status,owner_user_id,due_date)
               VALUES (?,?,?,?,?,?)""",
            (task_id, self.company_id, "مهمة متعددة الحسابات", "لم تبدأ",
             "سارة", date.today().isoformat()),
        )
        bind_owner_account(
            self.db, self.company_id, "سارة", second_account,
            first_account, "test:explicit-binding",
        )
        result = materialize_due_reminders(
            self.db, company_id=self.company_id
        )
        self.db.commit()
        self.assertEqual(1, result["created"])
        self.assertEqual([], list_reminders(
            self.db, self.company_id, recipient_account_id=first_account
        ))
        reminders = list_reminders(
            self.db, self.company_id, recipient_account_id=second_account
        )
        self.assertEqual(1, len(reminders))
        with self.assertRaises(PermissionError):
            update_reminder_status(
                self.db, self.company_id, reminders[0]["reminder_id"],
                "read", first_account,
            )
        client = sana_app.app.test_client()
        with client.session_transaction() as session:
            session["account_id"] = first_account
            session["company_id"] = self.company_id
            session["email"] = f"{self.company_id.lower()}@test.local"
        previous_csrf = sana_app.app.config.get("WTF_CSRF_ENABLED", True)
        sana_app.app.config["WTF_CSRF_ENABLED"] = False
        try:
            response = client.put(
                f"/api/companies/{self.company_id}/execution/reminder-owners",
                json={"owner_id": "سارة", "account_id": first_account},
            )
        finally:
            sana_app.app.config["WTF_CSRF_ENABLED"] = previous_csrf
        self.assertEqual(403, response.status_code)
        binding = self.db.execute(
            """SELECT account_id FROM execution_owner_bindings
               WHERE company_id=? AND owner_id=?""",
            (self.company_id, "سارة"),
        ).fetchone()
        self.assertEqual(second_account, binding["account_id"])

    def test_sole_account_delivery_is_revoked_when_company_becomes_ambiguous(self):
        from sana_decision_room import bind_owner_account
        first_account = f"ACC-{self.company_id}"
        second_account = f"ACC2-{self.company_id}"
        task_id = f"T{uuid.uuid4().hex[:10].upper()}"
        self.db.execute(
            """INSERT INTO tasks
               (task_id,company_id,title,status,owner_user_id,due_date)
               VALUES (?,?,?,?,?,?)""",
            (task_id, self.company_id, "تنبيه يتطلب تعيينًا", "لم تبدأ",
             "مالك بشري", date.today().isoformat()),
        )
        materialize_due_reminders(self.db, company_id=self.company_id)
        self.db.commit()
        self.assertEqual(1, len(list_reminders(
            self.db, self.company_id, recipient_account_id=first_account
        )))
        self.db.execute(
            """INSERT INTO user_accounts
               (account_id,email,password_hash,company_id) VALUES (?,?,?,?)""",
            (second_account, f"ambiguous-{self.company_id.lower()}@test.local",
             "not-used", self.company_id),
        )
        materialize_due_reminders(self.db, company_id=self.company_id)
        self.db.commit()
        self.assertEqual([], list_reminders(
            self.db, self.company_id, recipient_account_id=first_account
        ))
        self.assertEqual([], list_reminders(
            self.db, self.company_id, recipient_account_id=second_account
        ))
        bind_owner_account(
            self.db, self.company_id, "مالك بشري", second_account,
            "admin-preview", "test:resolve-ambiguity",
        )
        recovered = materialize_due_reminders(
            self.db, company_id=self.company_id
        )
        self.db.commit()
        self.assertEqual(1, recovered["recovered"])
        self.assertEqual([], list_reminders(
            self.db, self.company_id, recipient_account_id=first_account
        ))
        self.assertEqual(1, len(list_reminders(
            self.db, self.company_id, recipient_account_id=second_account
        )))

    def test_authenticated_reminder_inbox_is_never_cacheable(self):
        account_id = f"ACC-{self.company_id}"
        client = sana_app.app.test_client()
        with client.session_transaction() as session:
            session["account_id"] = account_id
            session["company_id"] = self.company_id
            session["email"] = f"{self.company_id.lower()}@test.local"
        response = client.get(
            f"/api/companies/{self.company_id}/execution/reminders"
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("no-store, private", response.headers["Cache-Control"])
        self.assertEqual("no-cache", response.headers["Pragma"])

    def test_one_shot_scheduler_runs_without_web_request(self):
        import run_execution_reminders
        fake_db = MagicMock()
        with patch.object(
            run_execution_reminders, "_connect_pg", return_value=fake_db
        ), patch.object(
            run_execution_reminders, "ensure_schema"
        ) as schema, patch.object(
            run_execution_reminders, "materialize_due_reminders",
            return_value={"status": "completed", "created": 1},
        ) as materialize:
            self.assertEqual(0, run_execution_reminders.main())
        schema.assert_called_once_with(fake_db)
        materialize.assert_called_once_with(fake_db, acquire_lock=True)
        fake_db.commit.assert_called_once()
        fake_db.close.assert_called_once()

    def test_scheduler_cycle_initializes_schema_before_materialization(self):
        import sana_decision_room
        fake_db = MagicMock()
        calls = []
        with patch.object(
            sana_decision_room, "ensure_schema",
            side_effect=lambda db: calls.append(("schema", db)),
        ), patch.object(
            sana_decision_room, "materialize_due_reminders",
            side_effect=lambda db, acquire_lock: (
                calls.append(("materialize", db)),
                {"status": "completed"},
            )[1],
        ):
            result = sana_decision_room.run_reminder_cycle(lambda: fake_db)
        self.assertEqual(["schema", "materialize"], [name for name, _ in calls])
        self.assertTrue(all(db is fake_db for _, db in calls))
        self.assertEqual({"status": "completed"}, result)
        fake_db.commit.assert_called_once()
        fake_db.close.assert_called_once()

    def test_scheduler_endpoint_rejects_missing_token(self):
        client = sana_app.app.test_client()
        response = client.post("/internal/execution-reminders/run")
        self.assertEqual(401, response.status_code)
        self.assertEqual("UNAUTHORIZED", response.get_json()["error"])

    def test_scheduler_endpoint_runs_shared_cycle_after_authentication(self):
        client = sana_app.app.test_client()
        with patch.object(
            sana_app, "_valid_scheduler_token", return_value=True
        ), patch(
            "sana_decision_room.run_reminder_cycle",
            return_value={"status": "completed", "created": 1},
        ) as cycle:
            response = client.post(
                "/internal/execution-reminders/run",
                headers={"Authorization": "Bearer test-token"},
            )
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["success"])
        cycle.assert_called_once_with(sana_app._connect_pg)

if __name__ == "__main__":
    unittest.main()
