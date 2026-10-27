-- Phase 4: the agentic chat. The file out/sessions/S###.json is the primary store (the chat works without Postgres);
-- these tables mirror it so a conversation is searchable and joins to generations and stickers. Re-runnable.
CREATE TABLE IF NOT EXISTS sessions (
  id            text PRIMARY KEY,                 -- S001
  user_id       text NOT NULL DEFAULT 'local',
  title         text NOT NULL DEFAULT '',
  settings      jsonb NOT NULL DEFAULT '{}',
  focus         jsonb NOT NULL DEFAULT '{}',      -- {generation, stickers[]}: what "it" means
  subjects      jsonb NOT NULL DEFAULT '[]',      -- per-subject metadata of its passes (ids, counts, likes)
  preferences   jsonb NOT NULL DEFAULT '{}',      -- only what the user said explicitly
  summary       jsonb NOT NULL DEFAULT '{}',
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS interactions (
  id                   bigserial PRIMARY KEY,
  session_id           text NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  seq                  int  NOT NULL,
  user_message         text NOT NULL,
  assistant_message    text NOT NULL DEFAULT '',
  intents              jsonb NOT NULL DEFAULT '[]',
  resolved             jsonb NOT NULL DEFAULT '{}',
  result_generation_id text,                      -- plain text: the generation may not be imported yet
  created_at           timestamptz NOT NULL,
  UNIQUE (session_id, seq)
);
CREATE INDEX IF NOT EXISTS interactions_generation ON interactions (result_generation_id);

-- "I like 2 and 7 but not 3 and 4": one row per sticker. TEMPORARY shapes the next generation only; PERSISTENT only when said explicitly.
CREATE TABLE IF NOT EXISTS feedback (
  id            bigserial PRIMARY KEY,
  session_id    text NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  ts            timestamptz NOT NULL,
  generation_id text,
  sticker_id    text,
  polarity      text NOT NULL CHECK (polarity IN ('POSITIVE','NEGATIVE')),
  scope         text NOT NULL CHECK (scope IN ('TEMPORARY','PERSISTENT')),
  text          text NOT NULL DEFAULT '',
  UNIQUE (session_id, ts, sticker_id, polarity)
);

-- "make 5 like 2": which sticker (or generation) a new one was made from, and in which role
CREATE TABLE IF NOT EXISTS generation_references (
  id            bigserial PRIMARY KEY,
  session_id    text REFERENCES sessions(id) ON DELETE CASCADE,
  source_id     text NOT NULL,                    -- G012/S2
  target_id     text,                             -- G013/S5, or the generation that used it
  role          text NOT NULL CHECK (role IN ('STYLE','POSE','SUBJECT','EXPRESSION','COMPOSITION','COLOR','ANIMATION')),
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- the annotation of an approved sticker (Phase 4C): what is VISIBLE, never a feeling; cached by asset hash
ALTER TABLE stickers ADD COLUMN IF NOT EXISTS annotation jsonb;
