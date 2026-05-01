-- DELULUREEL — Supabase Schema
-- Run in: Supabase Dashboard → SQL Editor
-- Order matters: functions after tables, policies last.

-- ── PROFILES ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profiles (
    user_id                 UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    plan                    VARCHAR(20),            -- creator | pro | studio
    status                  VARCHAR(20) DEFAULT 'inactive',  -- inactive | trial | active | suspended | cancelled
    stripe_customer_id      VARCHAR(120),
    stripe_subscription_id  VARCHAR(120),
    credits_limit           INT DEFAULT 10,         -- credits/month for current plan (1 credit = 5s video)
    credits_used_this_month INT DEFAULT 0,          -- credits consumed this billing cycle
    trial_credits_used      INT DEFAULT 0,          -- credits consumed during trial (cap: TRIAL_MAX_CREDITS)
    month_reset_date        DATE DEFAULT CURRENT_DATE,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

-- ── REEL JOBS ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reel_jobs (
    id               UUID PRIMARY KEY,
    user_id          UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    status           VARCHAR(20) DEFAULT 'queued',  -- queued | analyzing | generating | processing | lipsyncing | completed | failed
    style            VARCHAR(50)  DEFAULT 'cinematic',
    aspect_ratio     VARCHAR(10)  DEFAULT '9:16',
    fal_request_id   VARCHAR(200),
    fal_endpoint     VARCHAR(200),
    prompt           TEXT,
    bpm              FLOAT,
    output_url       TEXT,
    error_message    TEXT,
    estimated_cost         FLOAT DEFAULT 0,
    actual_cost            FLOAT,
    credits_used           INT DEFAULT 0,           -- credits deducted for this job
    enable_lipsync         BOOLEAN DEFAULT false,   -- stored in DB for cross-instance webhook safety
    target_secs_requested  INTEGER DEFAULT 10,      -- stored in DB for cross-instance webhook safety
    n_clips_expected       INTEGER DEFAULT 1,       -- multi-clip: total clips submitted
    clip_results           JSONB DEFAULT '{}',      -- multi-clip: {idx: url} accumulated by add_clip_result RPC
    created_at             TIMESTAMPTZ DEFAULT NOW(),
    updated_at             TIMESTAMPTZ DEFAULT NOW()
);

-- ── DAILY BUDGET ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS daily_budget (
    date        DATE PRIMARY KEY DEFAULT CURRENT_DATE,
    usd_spent   FLOAT NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── FUNCTIONS ─────────────────────────────────────────────────────────────────

-- Deduct credits after successful generation
CREATE OR REPLACE FUNCTION deduct_credits(p_user_id UUID, p_credits INT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    UPDATE profiles
    SET
        credits_used_this_month = credits_used_this_month + p_credits,
        trial_credits_used      = CASE WHEN status = 'trial'
                                       THEN trial_credits_used + p_credits
                                       ELSE trial_credits_used END,
        updated_at              = NOW()
    WHERE user_id = p_user_id;
END;
$$;

-- Reset monthly credit usage (call via Supabase cron or pg_cron on 1st of month)
CREATE OR REPLACE FUNCTION reset_monthly_credits()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    UPDATE profiles
    SET
        credits_used_this_month = 0,
        month_reset_date        = CURRENT_DATE,
        updated_at              = NOW()
    WHERE month_reset_date < DATE_TRUNC('month', CURRENT_DATE);
END;
$$;

-- Add spend to daily budget tracker
CREATE OR REPLACE FUNCTION add_daily_spend(p_usd FLOAT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    INSERT INTO daily_budget (date, usd_spent, updated_at)
    VALUES (CURRENT_DATE, p_usd, NOW())
    ON CONFLICT (date)
    DO UPDATE SET
        usd_spent  = daily_budget.usd_spent + EXCLUDED.usd_spent,
        updated_at = NOW();
END;
$$;

-- ── ROW LEVEL SECURITY ────────────────────────────────────────────────────────
ALTER TABLE profiles  ENABLE ROW LEVEL SECURITY;
ALTER TABLE reel_jobs ENABLE ROW LEVEL SECURITY;

-- Profiles
CREATE POLICY "select_own_profile" ON profiles
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "update_own_profile" ON profiles
    FOR UPDATE USING (auth.uid() = user_id);

-- Reel jobs
CREATE POLICY "select_own_jobs" ON reel_jobs
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "insert_own_jobs" ON reel_jobs
    FOR INSERT WITH CHECK (auth.uid() = user_id);

-- ── INDEXES ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_jobs_user_id    ON reel_jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status     ON reel_jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON reel_jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_profiles_stripe ON profiles(stripe_customer_id);

-- ── STORAGE BUCKETS (run separately in Supabase Dashboard) ───────────────────
-- INSERT INTO storage.buckets (id, name, public) VALUES ('reel-uploads', 'reel-uploads', true)  ON CONFLICT DO NOTHING;
-- INSERT INTO storage.buckets (id, name, public) VALUES ('reel-outputs', 'reel-outputs', true)  ON CONFLICT DO NOTHING;


-- ═══════════════════════════════════════════════════════════════════════════════
-- MIGRATION — run once on existing DB to rename reel columns → credit columns
-- Skip if running on a fresh DB (the CREATE TABLE above already uses new names)
-- ═══════════════════════════════════════════════════════════════════════════════
/*
-- Step 1: rename columns
ALTER TABLE profiles RENAME COLUMN reel_limit             TO credits_limit;
ALTER TABLE profiles RENAME COLUMN reels_used_this_month  TO credits_used_this_month;
ALTER TABLE profiles RENAME COLUMN trial_reels_used       TO trial_credits_used;

-- Step 2: rescale values  (old reel counts × 2 = credits, since 1 reel ≈ 10s = 2 credits)
UPDATE profiles SET
    credits_limit           = CASE plan
                                  WHEN 'creator' THEN 10
                                  WHEN 'pro'     THEN 30
                                  WHEN 'studio'  THEN 80
                                  ELSE 10 END,
    credits_used_this_month = credits_used_this_month * 2,
    trial_credits_used      = trial_credits_used * 2;

-- Step 3: add credits_used to reel_jobs (if not already present)
ALTER TABLE reel_jobs ADD COLUMN IF NOT EXISTS credits_used INT DEFAULT 0;

-- Step 4: drop old function, create new ones
DROP FUNCTION IF EXISTS increment_reel_count(UUID);
-- (new functions deduct_credits and reset_monthly_credits are created above)
*/
