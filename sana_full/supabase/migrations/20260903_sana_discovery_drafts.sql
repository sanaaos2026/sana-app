-- Temporary, account-owned SDS-001 drafts. They are not evidence and never
-- participate in Scan or decision calculations.
CREATE TABLE IF NOT EXISTS public.sana_discovery_drafts (
    draft_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES public.companies(company_id) ON DELETE CASCADE,
    account_id TEXT NOT NULL REFERENCES public.user_accounts(account_id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    current_step SMALLINT NOT NULL DEFAULT 0 CHECK (current_step BETWEEN 0 AND 8),
    status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'COMPLETED', 'EXPIRED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    UNIQUE(company_id, account_id)
);

CREATE INDEX IF NOT EXISTS idx_sana_discovery_drafts_expiry
    ON public.sana_discovery_drafts(expires_at);
CREATE INDEX IF NOT EXISTS idx_sana_discovery_drafts_owner
    ON public.sana_discovery_drafts(company_id, account_id, status, updated_at DESC);

DO $$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE public.sana_discovery_drafts FROM %I',
                role_name
            );
        END IF;
    END LOOP;
    REVOKE ALL PRIVILEGES ON TABLE public.sana_discovery_drafts FROM PUBLIC;
END
$$;