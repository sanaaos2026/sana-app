"""اختبار عقد حماية Supabase ومسار الخادم الخاص.

التشغيل:
    cd sana_full && python3 -m unittest test_supabase_rls.py -v

لا ينشئ الاختبار بياناتًا ولا يغيّر المخطط. يعتمد على قاعدة Supabase الحالية
المتصلة عبر نفس resolve_database_url الذي يستخدمه Flask.
"""

import os
import sys
import unittest
from pathlib import Path

import psycopg2


BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from database_config import resolve_database_url


class SupabaseRlsContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (
            os.environ.get("DATABASE_URL")
            or os.environ.get("SUPABASE_DB_PASSWORD")
        ):
            raise unittest.SkipTest("قاعدة Supabase غير مضبوطة")
        cls.db = psycopg2.connect(resolve_database_url())
        cls.db.autocommit = True

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "db", None) is not None:
            cls.db.close()

    def test_every_public_table_is_rls_protected_and_client_closed(self):
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.relname,
                       c.relrowsecurity,
                       has_table_privilege(
                           'anon', format('public.%I', c.relname), 'SELECT'
                       ) AS anon_select,
                       has_table_privilege(
                           'authenticated',
                           format('public.%I', c.relname),
                           'SELECT'
                       ) AS authenticated_select
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                ORDER BY c.relname
                """
            )
            rows = cursor.fetchall()

        self.assertTrue(rows, "لم يُعثر على جداول Sana في public")
        failures = [
            (name, rls_enabled, anon_select, authenticated_select)
            for name, rls_enabled, anon_select, authenticated_select in rows
            if not rls_enabled or anon_select or authenticated_select
        ]
        self.assertEqual([], failures, f"جداول غير محمية: {failures}")

    def test_every_public_table_has_explicit_deny_policy(self):
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.relname
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relkind = 'r'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM pg_policies AS p
                      WHERE p.schemaname = 'public'
                        AND p.tablename = c.relname
                        AND p.policyname = 'sana_deny_public_access'
                  )
                ORDER BY c.relname
                """
            )
            missing = [row[0] for row in cursor.fetchall()]

        self.assertEqual([], missing, f"جداول بلا سياسة رفض: {missing}")

    def test_private_flask_role_keeps_database_access(self):
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_user,
                       has_table_privilege(
                           current_user, 'public.companies', 'SELECT'
                       ),
                       EXISTS (
                           SELECT 1
                           FROM pg_roles
                           WHERE rolname = current_user AND rolbypassrls
                       )
                """
            )
            current_user, can_select, bypasses_rls = cursor.fetchone()

        self.assertEqual("postgres", current_user)
        self.assertTrue(can_select)
        self.assertTrue(bypasses_rls)

        # This is the same private path used by app.py, not a Supabase client
        # key.  A real query confirms the hardening did not break psycopg2.
        with self.db.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM public.companies")
            self.assertIsInstance(cursor.fetchone()[0], int)


if __name__ == "__main__":
    unittest.main(verbosity=2)