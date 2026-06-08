-- BYOC test sessions — anti-abuse tracking per test pre-abbonamento
-- Apply in Supabase SQL Editor before deploying byoc blueprint.

CREATE TABLE IF NOT EXISTS byoc_test_sessions (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email        TEXT        NOT NULL,
    ip           TEXT        NOT NULL,
    gpu_name     TEXT,
    vram_gb      INTEGER,
    comfyui_url  TEXT        NOT NULL,
    workflow_id  TEXT        NOT NULL DEFAULT 'SDXL',
    token        TEXT        UNIQUE NOT NULL DEFAULT gen_random_uuid()::text,
    job_id       TEXT,
    status       TEXT        NOT NULL DEFAULT 'pending',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS byoc_test_sessions_email_idx  ON byoc_test_sessions(email);
CREATE INDEX IF NOT EXISTS byoc_test_sessions_ip_idx     ON byoc_test_sessions(ip, created_at);

ALTER TABLE byoc_test_sessions ENABLE ROW LEVEL SECURITY;
-- No policies: service_role only. Anon/authenticated keys get zero access.
