-- Phase 3B: the embedding pass. sticker_index.subject_vec / action_vec become 768-d (the local nomic embedding model, and OpenAI's
-- text-embedding-3-small with dimensions = 768), with cosine HNSW indexes. The columns stay NULLABLE: a row is indexed lexically the moment a
-- sticker is approved and its vectors are filled by `stickerloom pool reindex` (idempotent; `stickerloom pool status` counts what is still missing).
-- Re-runnable: the type change only happens while the column is not 768-d yet, so re-applying this file never wipes vectors.
DO $$
BEGIN
  IF (SELECT atttypmod FROM pg_attribute WHERE attrelid = 'sticker_index'::regclass AND attname = 'subject_vec') <> 768 THEN
    DROP INDEX IF EXISTS sticker_index_subject_vec;
    DROP INDEX IF EXISTS sticker_index_action_vec;
    ALTER TABLE sticker_index ALTER COLUMN subject_vec TYPE vector(768) USING NULL;
    ALTER TABLE sticker_index ALTER COLUMN action_vec TYPE vector(768) USING NULL;
    UPDATE sticker_index SET embed_model = 'none';
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS sticker_index_subject_vec ON sticker_index USING hnsw (subject_vec vector_cosine_ops);
CREATE INDEX IF NOT EXISTS sticker_index_action_vec ON sticker_index USING hnsw (action_vec vector_cosine_ops);
