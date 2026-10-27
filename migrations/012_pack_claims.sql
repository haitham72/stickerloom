-- The pack claim ledger (generation/claims.py; docs/export to team/stickerloom-export-architecture.md §10.7): the Postgres copy of out/pack_sessions/<slug>.json.
-- The file is the record. Only new tables: no live table is altered. A claim is also a `tasks` row (kind 'sheet', external_task_id = the claim id), written by
-- repo.save_pack_session. No CHECK on status (a frozen constraint never reaches an existing database). Re-runnable.
CREATE TABLE IF NOT EXISTS pack_sessions (
  slug     text PRIMARY KEY,                 -- falcon (the session id is pack-<slug>)
  subject  text NOT NULL,
  title    text,
  owner    text,
  pack_id  text,                             -- the library pack it fills, once there is one
  created  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS claims (
  id       text PRIMARY KEY,                 -- C### (one sequence over every session)
  session  text NOT NULL REFERENCES pack_sessions(slug),
  preset   text NOT NULL,                    -- core-v1 | social-v1 | reactions-v1 | daily-v1
  grid     text NOT NULL,                    -- 3x3 | 2x2
  status   text NOT NULL,                    -- CLAIMED | PLANNED | REQUESTED | DONE
  plan     jsonb,
  created  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (session, preset)                   -- one claim per preset per session
);
-- append-only lineage: regenerate adds a revision, nothing is removed. No FK on generation_id: the mirror may see a claim before its generation.
CREATE TABLE IF NOT EXISTS claim_generations (
  claim_id       text NOT NULL REFERENCES claims(id),
  generation_id  text NOT NULL,
  revision       int  NOT NULL,
  ts             timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (claim_id, revision)
);
CREATE INDEX IF NOT EXISTS claim_generations_gen ON claim_generations (generation_id);
