-- Phase 3B: semantic sticker pool. Lexical columns are live now; the two vectors stay NULL
-- until the embedding pass lands (no bulk model spend unattended), then backfilled by `pool reindex`.
-- NOTE vs the original plan: subject_vec/action_vec are NULLABLE here (spec: NOT NULL) for exactly that
-- reason; a CHECK added with the embedding pass will enforce them once reindex fills every row.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS sticker_index (
  sticker_id  text PRIMARY KEY REFERENCES stickers(id) ON DELETE CASCADE,
  subject     text NOT NULL, action text NOT NULL, search_text text NOT NULL,
  topics      text[] NOT NULL DEFAULT '{}',
  subject_vec vector(1536),
  action_vec  vector(1536),
  embed_model text NOT NULL DEFAULT 'none',
  shared      boolean NOT NULL DEFAULT true, hidden boolean NOT NULL DEFAULT false,
  indexed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sticker_index_topics ON sticker_index USING gin (topics);
CREATE INDEX IF NOT EXISTS sticker_index_text_trgm ON sticker_index USING gin (search_text gin_trgm_ops);

-- search_log exists since 001 (id, query, filters, hit_ids, latency_ms, created_at); extend, never re-create
ALTER TABLE search_log ADD COLUMN IF NOT EXISTS parsed jsonb;
ALTER TABLE search_log ADD COLUMN IF NOT EXISTS gap_generated int NOT NULL DEFAULT 0;
