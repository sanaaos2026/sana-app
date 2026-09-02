-- Independent production scheduler for Sana execution reminders.
-- The bearer token is generated inside Postgres and stored encrypted in Vault.

CREATE EXTENSION IF NOT EXISTS pg_net;
CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

CREATE TABLE IF NOT EXISTS public.sana_scheduler_credentials (
    credential_name TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.sana_scheduler_credentials ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.sana_scheduler_credentials FROM PUBLIC, anon, authenticated;

DO $setup$
DECLARE
    scheduler_token TEXT;
BEGIN
    SELECT decrypted_secret
      INTO scheduler_token
      FROM vault.decrypted_secrets
     WHERE name = 'sana_execution_reminders_token'
     LIMIT 1;

    IF scheduler_token IS NULL THEN
        scheduler_token := encode(extensions.gen_random_bytes(32), 'hex');
        PERFORM vault.create_secret(
            scheduler_token,
            'sana_execution_reminders_token',
            'Bearer token for the Sana execution-reminder cron endpoint'
        );
    END IF;

    INSERT INTO public.sana_scheduler_credentials
        (credential_name, token_hash, active, updated_at)
    VALUES
        ('execution-reminders', encode(extensions.digest(scheduler_token, 'sha256'), 'hex'), true, now())
    ON CONFLICT (credential_name) DO UPDATE SET
        token_hash = EXCLUDED.token_hash,
        active = true,
        updated_at = now();
END
$setup$;

DO $unschedule$
DECLARE
    existing_job BIGINT;
BEGIN
    SELECT jobid INTO existing_job
      FROM cron.job
     WHERE jobname = 'sana-execution-reminders-every-15-minutes'
     LIMIT 1;
    IF existing_job IS NOT NULL THEN
        PERFORM cron.unschedule(existing_job);
    END IF;
END
$unschedule$;

SELECT cron.schedule(
    'sana-execution-reminders-every-15-minutes',
    '*/15 * * * *',
    $job$
    SELECT net.http_post(
        url := 'https://simple-flask-run.replit.app/internal/execution-reminders/run',
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'Authorization', 'Bearer ' || (
                SELECT decrypted_secret
                  FROM vault.decrypted_secrets
                 WHERE name = 'sana_execution_reminders_token'
                 LIMIT 1
            )
        ),
        body := jsonb_build_object('scheduled_at', now()),
        timeout_milliseconds := 120000
    );
    $job$
);