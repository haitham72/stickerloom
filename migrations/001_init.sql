-- Phase 3A: durable state, identity, lineage, decisions, search.
-- Plain SQL, applied in order by `stickerloom db migrate`. Re-runnable (IF NOT EXISTS everywhere).
-- NOTE vs the original plan: trace_run_id columns are included here directly (002 folded in),
-- reviews.decision also allows 'EDIT' (sticker-editor history), assets.kind includes
-- PLAIN_STICKER (outline-free twin) and tasks.kind covers the 1G manual tasks as 'sheet'.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS schema_migrations (
  name text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE SEQUENCE IF NOT EXISTS generation_seq;

-- IMMUTABLE helpers for the stickers.search_doc generated column
-- (array_to_string is only STABLE, and Postgres refuses non-IMMUTABLE generated columns).
CREATE OR REPLACE FUNCTION array_to_text(text[])
RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT array_to_string($1, ' ') $$;

CREATE OR REPLACE FUNCTION stickerloom_words(text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT replace($1, '_', ' ') $$;

CREATE TABLE IF NOT EXISTS generations (
  id            text PRIMARY KEY,
  parent_id     text REFERENCES generations(id),
  grid          int[] NOT NULL DEFAULT '{3,3}',
  regen_of      text,
  verify_version text NOT NULL DEFAULT '',
  prompt        text NOT NULL,
  subject       text NOT NULL DEFAULT '',
  source        text NOT NULL DEFAULT 'prepared',
  source_ref    jsonb NOT NULL DEFAULT '{}',
  task          text NOT NULL DEFAULT '',
  task_slug     text NOT NULL DEFAULT '',
  sheet_prompt  text,
  video_prompt  text,
  plan          jsonb NOT NULL DEFAULT '{}',
  engine_version text NOT NULL DEFAULT '',
  status        text NOT NULL DEFAULT 'FAILED',
  name_key      text NOT NULL DEFAULT '',
  task_id       text,
  template_id   text,
  template_version text,
  slots         jsonb,
  outline_px    int,
  erode_px      int,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS generations_parent_idx ON generations (parent_id);
CREATE INDEX IF NOT EXISTS generations_prompt_trgm ON generations USING gin (prompt gin_trgm_ops);

CREATE TABLE IF NOT EXISTS stickers (
  id               text PRIMARY KEY,
  generation_id    text NOT NULL REFERENCES generations(id),
  idx              int  NOT NULL,
  name             text NOT NULL,
  file_name        text,
  key              text NOT NULL,
  inherited_from   text REFERENCES stickers(id),
  tags             text[] NOT NULL DEFAULT '{}',
  concept          text,
  prompt           text NOT NULL DEFAULT '',
  emoji            text[] NOT NULL DEFAULT '{}',
  status           text NOT NULL DEFAULT 'FAILED',
  reason           text,
  report           jsonb NOT NULL DEFAULT '[]',
  metrics          jsonb NOT NULL DEFAULT '{}',
  edited           boolean NOT NULL DEFAULT false,
  edited_at        timestamptz,
  animation_status text NOT NULL DEFAULT 'NOT_REQUESTED',
  animation_reason text,
  still_review     text NOT NULL DEFAULT 'PENDING',
  anim_review      text NOT NULL DEFAULT 'PENDING',
  video_sheet_id   text,
  search_doc       tsvector GENERATED ALWAYS AS (
                     to_tsvector('simple',
                       coalesce(prompt,'') || ' ' || stickerloom_words(key) || ' ' || stickerloom_words(array_to_text(tags)))) STORED,
  UNIQUE (generation_id, idx)
);
CREATE INDEX IF NOT EXISTS stickers_concept_idx ON stickers (concept);
CREATE INDEX IF NOT EXISTS stickers_search_idx ON stickers USING gin (search_doc);
CREATE INDEX IF NOT EXISTS stickers_tags_idx ON stickers USING gin (tags);
CREATE INDEX IF NOT EXISTS stickers_key_trgm ON stickers USING gin (key gin_trgm_ops);

CREATE TABLE IF NOT EXISTS assets (
  id            bigserial PRIMARY KEY,
  generation_id text NOT NULL REFERENCES generations(id),
  sticker_id    text REFERENCES stickers(id),
  kind          text NOT NULL CHECK (kind IN ('SOURCE_SHEET','SOURCE_VIDEO','KEYED_SHEET','PROMPTS','PLAIN_STICKER',
                                              'PNG','WEBP','WEBM','VIDEO_SHEET','VIDEO_LAYOUT','RETURNED_VIDEO')),
  video_sheet_id text,
  object_key    text NOT NULL,
  sha256        text NOT NULL DEFAULT '',
  mime          text NOT NULL DEFAULT 'application/octet-stream',
  bytes         int  NOT NULL DEFAULT 0,
  width         int, height int, fps real, duration real,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (generation_id, object_key)
);

-- 1F video sheet (G3) and the video that came back for it
CREATE TABLE IF NOT EXISTS video_sheets (
  id            text PRIMARY KEY,
  generation_id text NOT NULL REFERENCES generations(id),
  attempt       int  NOT NULL,
  slots         int[] NOT NULL DEFAULT '{}',
  layout        jsonb NOT NULL DEFAULT '{}',
  status        text NOT NULL DEFAULT 'BUILT',
  ticket        text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (generation_id, attempt)
);

-- every decision at every gate, by every actor; append-only
CREATE TABLE IF NOT EXISTS reviews (
  id             bigserial PRIMARY KEY,
  generation_id  text NOT NULL REFERENCES generations(id),
  sticker_id     text REFERENCES stickers(id),
  video_sheet_id text REFERENCES video_sheets(id),
  gate           text NOT NULL CHECK (gate IN ('plan','still','video_sheet','anim','pack')),
  actor          text NOT NULL CHECK (actor IN ('python','human','vlm')),
  decision       text NOT NULL CHECK (decision IN ('PASS','BLOCK','APPROVE','REJECT','EDIT')),
  reason         text,
  detail         jsonb,
  user_id        text NOT NULL DEFAULT 'local',
  trace_run_id   uuid,
  ts             timestamptz NOT NULL,
  UNIQUE (generation_id, sticker_id, gate, actor, ts)
);
CREATE INDEX IF NOT EXISTS reviews_sticker_ts ON reviews (sticker_id, ts);

CREATE TABLE IF NOT EXISTS generation_events (
  id            bigserial PRIMARY KEY,
  generation_id text NOT NULL REFERENCES generations(id),
  ts            timestamptz NOT NULL,
  stage         text NOT NULL,
  status        text NOT NULL,
  ms            int,
  detail        jsonb,
  actor         text,
  trace_run_id  uuid,
  UNIQUE (generation_id, ts, stage, status)
);

-- the external task id is the join key between stickerloom and the provider
CREATE TABLE IF NOT EXISTS tasks (
  id               bigserial PRIMARY KEY,
  provider         text NOT NULL,
  external_task_id text NOT NULL,
  kind             text NOT NULL CHECK (kind IN ('sheet','video','single')),
  name_key         text NOT NULL,
  generation_id    text REFERENCES generations(id),
  video_sheet_id   text REFERENCES video_sheets(id),
  status           text NOT NULL DEFAULT 'REQUESTED',
  request          jsonb NOT NULL DEFAULT '{}',
  result_ref       jsonb,
  created_at       timestamptz NOT NULL DEFAULT now(),
  completed_at     timestamptz,
  UNIQUE (provider, external_task_id)
);
CREATE INDEX IF NOT EXISTS tasks_name_key ON tasks (name_key);
CREATE INDEX IF NOT EXISTS tasks_key_prefix ON tasks (name_key text_pattern_ops);

CREATE TABLE IF NOT EXISTS search_log (
  id bigserial PRIMARY KEY,
  query text NOT NULL,
  filters jsonb NOT NULL DEFAULT '{}',
  hit_ids text[] NOT NULL DEFAULT '{}',
  latency_ms int,
  created_at timestamptz NOT NULL DEFAULT now()
);
