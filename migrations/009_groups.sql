-- Batch groups (flow/groups.py, 2026-10-04): the family a batch belongs to and why. `group_id` is the family root (G###): an explicit join, else the root of
-- its parent chain, else itself; `relation` is joined | redo | edit, NULL for a root. Written by every sync of a result; re-runnable.
ALTER TABLE generations ADD COLUMN IF NOT EXISTS group_id text;
ALTER TABLE generations ADD COLUMN IF NOT EXISTS relation text;
CREATE INDEX IF NOT EXISTS generations_group ON generations (group_id);
