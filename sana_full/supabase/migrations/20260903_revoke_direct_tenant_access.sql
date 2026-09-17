-- Sana's application backend owns all database access. Browser-facing Supabase
-- roles must not read or mutate tenant data directly.
DO $$
DECLARE
    role_name text;
    table_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            FOREACH table_name IN ARRAY ARRAY[
                'companies',
                'user_accounts',
                'cases',
                'evidence',
                'decisions',
                'tasks'
            ]
            LOOP
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON TABLE public.%I FROM %I',
                    table_name,
                    role_name
                );
            END LOOP;
        END IF;
    END LOOP;

    FOREACH table_name IN ARRAY ARRAY[
        'companies',
        'user_accounts',
        'cases',
        'evidence',
        'decisions',
        'tasks'
    ]
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE public.%I FROM PUBLIC',
            table_name
        );
    END LOOP;
END
$$;