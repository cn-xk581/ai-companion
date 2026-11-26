-- 003: 情景记忆加入向量列（P1 用 JSONB 存向量，便于换 embedder 维度）
-- 仅当已经部署过 672edb9（relation_episodes 已存在）时需要执行；
-- 全新安装由 create_all 直接建好，无需运行。
--
--   docker exec -i companion-postgres-1 psql -U companion -d companion \
--     < deploy/migrations/003_memory_embedding.sql

BEGIN;

ALTER TABLE relation_episodes
    ADD COLUMN IF NOT EXISTS embedding JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMIT;
