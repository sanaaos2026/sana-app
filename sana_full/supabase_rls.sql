-- Sana Supabase access boundary
--
-- Flask connects with the private database connection and remains the only
-- application data path.  The public Supabase roles must not be able to read
-- or mutate Sana tables directly.  This migration is deliberately separate
-- from app.py startup DDL: run it once against the target Supabase database,
-- and rerun it after adding a public table.
--
-- The migration is idempotent.  It does not revoke service_role access because
-- service_role is a trusted server-side role and bypasses RLS in Supabase.

BEGIN;

-- Coordinate with Sana's existing schema initialization and fail instead of
-- waiting indefinitely behind an unrelated long transaction.
SELECT set_config('lock_timeout', '30s', true);
SELECT pg_advisory_xact_lock(hashtext('sana.schema.initialization'));

-- Do not let the public API roles inherit table/sequence access, including
-- access accidentally granted through PUBLIC.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated, PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated, PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Keep future objects created by the current private database owner closed by
-- default.  This covers new Sana tables before this migration is rerun.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON TABLES FROM anon, authenticated, PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM anon, authenticated, PUBLIC;

DO $sana_rls$
DECLARE
    table_record RECORD;
BEGIN
    FOR table_record IN
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
    LOOP
        EXECUTE format(
            'ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',
            table_record.tablename
        );

        -- A named deny policy is defense in depth if a table grant is
        -- accidentally reintroduced later.  Future tenant-aware policies can
        -- be added separately without changing this first, server-only phase.
        EXECUTE format(
            'DROP POLICY IF EXISTS sana_deny_public_access ON public.%I',
            table_record.tablename
        );
        EXECUTE format(
            'CREATE POLICY sana_deny_public_access ON public.%I
             FOR ALL TO anon, authenticated
             USING (false)
             WITH CHECK (false)',
            table_record.tablename
        );
    END LOOP;
END
$sana_rls$;

COMMIT;