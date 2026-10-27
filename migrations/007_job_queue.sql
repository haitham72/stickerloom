-- A durable job queue for the workers (`python -m stickerloom worker`). out/jobs/J###.json stays the truth for a job's request, ticket and result; this table is only who runs it and when.
-- QUEUED (waiting; also after a retry with backoff) -> RUNNING (a worker holds it) -> DONE | FAILED | DEAD.
--   FAILED = the provider reported a failure: never retried automatically (a retry can spend credits); a human retries it.
--   DEAD   = the worker itself kept failing (it died, the disk was full, ...) past max_attempts.
-- A DONE job still has to be followed up by the server (start the stills run, slice the video): ingested_at says it was, ingest_locked_at is a short lease. Re-runnable.
CREATE TABLE IF NOT EXISTS job_queue (
  job_id           text PRIMARY KEY,                       -- J004
  kind             text NOT NULL,                          -- sheet | video | single
  status           text NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED','RUNNING','DONE','FAILED','DEAD')),
  owner            text NOT NULL DEFAULT 'local',
  attempts         integer NOT NULL DEFAULT 0,
  max_attempts     integer NOT NULL DEFAULT 3,
  locked_by        text,
  locked_at        timestamptz,
  next_run_at      timestamptz NOT NULL DEFAULT now(),
  enqueued_at      timestamptz NOT NULL DEFAULT now(),
  finished_at      timestamptz,
  ingest_locked_at timestamptz,
  ingested_at      timestamptz,
  error            text
);
CREATE INDEX IF NOT EXISTS job_queue_ready ON job_queue (status, next_run_at);
CREATE INDEX IF NOT EXISTS job_queue_ingest ON job_queue (status, ingested_at);
