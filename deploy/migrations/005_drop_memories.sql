-- 005: 删除旧版 user 级 memories 表（记忆已迁到 relation_facts / relation_episodes / session_summaries）
--
--   docker exec -i companion-postgres-1 psql -U companion -d companion \
--     < deploy/migrations/005_drop_memories.sql

BEGIN;
DROP TABLE IF EXISTS memories CASCADE;
COMMIT;
