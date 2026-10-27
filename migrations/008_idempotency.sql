-- The durable idempotency backstop. `Idempotency-Key` answers come from the cache first (Redis, or the process's memory when Redis is down); a restart without Redis
-- forgets them, and a repeated click or a retried request could then start (and pay for) a second job. The answer is also kept here for 24 hours.
-- The key is stored hashed (sha256), scoped by the route and the caller, and the FIRST answer wins. Re-runnable.
CREATE TABLE IF NOT EXISTS idempotency_keys (
  scope      text NOT NULL,
  key_sha    text NOT NULL,
  response   jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (scope, key_sha)
);
CREATE INDEX IF NOT EXISTS idempotency_keys_created ON idempotency_keys (created_at);
