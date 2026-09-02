"""اختبارات موثوقية إقلاع سنع وتهيئة مخطط PostgreSQL."""
import os
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import app as sana_app
from database_config import acquire_schema_lock


class _RecordingConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))


class StartupInitializationTest(unittest.TestCase):
    def test_configured_process_registers_drive_routes_before_app_run(self):
        probe = """
import runpy
import flask
def capture(self, *args, **kwargs):
    print('ROUTES=' + ','.join(sorted(rule.rule for rule in self.url_map.iter_rules())))
flask.Flask.run = capture
runpy.run_path('app.py', run_name='__main__')
"""
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=BASE_DIR,
            env={
                **os.environ,
                "ENABLE_EXECUTION_REMINDER_SCHEDULER": "0",
                "ENABLE_KNOWLEDGE_BACKUP_SCHEDULER": "0",
                "ENABLE_PERIODIC_KNOWLEDGE_RESEARCH": "0",
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        routes = completed.stdout.split("ROUTES=", 1)[-1]
        self.assertIn("/api/knowledge/drive/sync", routes)
        self.assertIn("/api/knowledge/drive/files/<drive_file_id>/extract", routes)
        self.assertIn("/api/knowledge/drive/coverage", routes)

    def test_schema_lock_is_transaction_local_and_not_reacquired(self):
        db = _RecordingConnection()

        acquire_schema_lock(db)
        acquire_schema_lock(db)

        self.assertEqual(2, len(db.calls))
        self.assertIn("set_config('lock_timeout'", db.calls[0][0])
        self.assertIn("pg_advisory_xact_lock", db.calls[1][0])
        self.assertTrue(db._schema_lock_acquired)

    def test_port_start_path_does_not_wait_for_schema_initialization(self):
        started = threading.Event()
        release = threading.Event()

        def blocked_initialization():
            started.set()
            release.wait(timeout=2)

        with patch.object(
            sana_app, "_initialize_and_start_schedulers",
            side_effect=blocked_initialization,
        ):
            started_at = time.monotonic()
            worker = sana_app._start_startup_initialization(True)
            self.assertTrue(started.wait(timeout=1))
            self.assertLess(time.monotonic() - started_at, 1)
            self.assertTrue(worker.is_alive())
            release.set()
            worker.join(timeout=1)
            self.assertFalse(worker.is_alive())

    def test_startup_initialization_is_repeatable(self):
        with patch.dict(
            os.environ,
            {
                "ENABLE_EXECUTION_REMINDER_SCHEDULER": "0",
                "ENABLE_KNOWLEDGE_BACKUP_SCHEDULER": "0",
            },
            clear=False,
        ), patch.object(sana_app, "init_db") as init_db, patch.object(
            sana_app, "seed_db"
        ), patch.object(
            sana_app, "seed_decision_impacts"
        ), patch.object(
            sana_app, "seed_knowledge_db"
        ):
            sana_app._initialize_and_start_schedulers()
            sana_app._initialize_and_start_schedulers()

        self.assertEqual(2, init_db.call_count)

    def test_schedulers_start_after_seeding(self):
        events = []

        with patch.dict(
            os.environ,
            {
                "ENABLE_EXECUTION_REMINDER_SCHEDULER": "1",
                "ENABLE_KNOWLEDGE_BACKUP_SCHEDULER": "0",
            },
            clear=False,
        ), patch.object(
            sana_app, "init_db", side_effect=lambda: events.append("schema")
        ), patch.object(
            sana_app, "seed_db", side_effect=lambda: events.append("seed")
        ), patch.object(
            sana_app, "seed_decision_impacts",
            side_effect=lambda: events.append("impact")
        ), patch.object(
            sana_app, "seed_knowledge_db",
            side_effect=lambda: events.append("knowledge")
        ), patch(
            "sana_decision_room.start_reminder_scheduler",
            side_effect=lambda connect_db: events.append("reminders"),
        ):
            sana_app._initialize_and_start_schedulers()

        self.assertEqual(["schema", "seed", "impact", "knowledge", "reminders"], events)


if __name__ == "__main__":
    unittest.main()