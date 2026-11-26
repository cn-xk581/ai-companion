-- 002: 角色卡（聊天对象）+ 会话绑定
--
-- 新表（characters / character_versions / character_lorebooks /
-- character_lorebook_entries / character_media）由应用启动时 create_all 自动创建；
-- 这里只补 sessions 的新列（create_all 不会给已有表加列）。
--
-- 用法：
--   docker exec -i companion-postgres-1 psql -U companion -d companion < deploy/migrations/002_characters.sql
--   sudo systemctl restart companion

BEGIN;

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS character_id BIGINT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS character_version_id BIGINT;

CREATE INDEX IF NOT EXISTS idx_sessions_character ON sessions (character_id);

COMMIT;
