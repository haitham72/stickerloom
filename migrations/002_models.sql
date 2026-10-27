-- Phase 3A (A1): what Phase 2 wrote. Re-runnable (migrate re-applies every file).
--
-- model_calls: one row per line of out/model_calls.jsonl (every paid or model call). The file has no key, so the
-- row key is the sha256 of the line: importing the same file twice adds nothing. `cost_credits` is Higgsfield
-- credits (what the ledger's `cost` is), never dollars. generation_id / sticker_id are plain text on purpose: a
-- call may be logged before its generation is imported, and the ledger must never fail on ordering.
CREATE TABLE IF NOT EXISTS model_calls (
  id            bigserial PRIMARY KEY,
  line_sha      text NOT NULL UNIQUE,
  ts            timestamptz NOT NULL,
  kind          text NOT NULL,          -- LLM_PLAN | IMAGE_SHEET | IMAGE_SINGLE | VIDEO | VLM_STICKER | VLM_SHEET ...
  provider      text NOT NULL,
  model         text NOT NULL,
  status        text NOT NULL,          -- OK | ERROR | TIMEOUT | INVALID_OUTPUT ...
  attempt       int  NOT NULL DEFAULT 1,
  latency_ms    int,
  tokens_in     int,
  tokens_out    int,
  cost_credits  numeric(12,4),
  seed          text,
  prompt_version text,
  generation_id text,
  sticker_id    text,
  job_id        text,
  error         text,
  extra         jsonb NOT NULL DEFAULT '{}'    -- every other key of the line (task, params, output, sha256 ...)
);
CREATE INDEX IF NOT EXISTS model_calls_generation ON model_calls (generation_id);
CREATE INDEX IF NOT EXISTS model_calls_ts ON model_calls (ts);
CREATE INDEX IF NOT EXISTS model_calls_kind ON model_calls (kind, model);

-- out/jobs/J###.json: a claimed job IS a provider task, so it lands in `tasks` (one place for provider ids).
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS job_id text;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS model text;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS cost_credits numeric(12,4);
CREATE INDEX IF NOT EXISTS tasks_job_id ON tasks (job_id);

-- the vision judge (Phase 2 step S6): the latest verdict per sticker; every verdict is also a `reviews` row (actor 'vlm')
ALTER TABLE stickers ADD COLUMN IF NOT EXISTS judge jsonb;
ALTER TABLE stickers ADD COLUMN IF NOT EXISTS emoji_suggestion text[];
