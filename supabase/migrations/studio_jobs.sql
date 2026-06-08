-- Studio Jobs — tabella per job ComfyUI su RunPod
-- Eseguire in Supabase SQL Editor

CREATE TABLE IF NOT EXISTS studio_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    runpod_id       TEXT,                          -- ID job RunPod (null finché non inviato)
    tier            TEXT NOT NULL,                 -- creator | pro | studio
    workflow_name   TEXT,                          -- nome workflow usato
    status          TEXT NOT NULL DEFAULT 'queued', -- queued | running | completed | failed
    output_urls     JSONB DEFAULT '[]'::jsonb,     -- [{"filename": ..., "url": ..., "type": ...}]
    error           TEXT,
    credits_used    INTEGER NOT NULL DEFAULT 1,
    gpu_seconds     FLOAT,                         -- secondi GPU effettivi (da RunPod executionTime)
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- RLS: utenti vedono solo i propri job
ALTER TABLE studio_jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users_select_own_studio_jobs"
    ON studio_jobs FOR SELECT
    USING (user_id = auth.uid());

-- Index per query frequenti
CREATE INDEX idx_studio_jobs_user_status
    ON studio_jobs (user_id, status);

CREATE INDEX idx_studio_jobs_created
    ON studio_jobs (created_at DESC);

-- Auto-aggiorna updated_at
CREATE OR REPLACE FUNCTION update_studio_jobs_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_studio_jobs_updated_at
    BEFORE UPDATE ON studio_jobs
    FOR EACH ROW EXECUTE FUNCTION update_studio_jobs_updated_at();
