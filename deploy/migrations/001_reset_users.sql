-- 001: 用户体系改造（BIGINT 主键 + refresh token + 资料）
--
-- ⚠️ 会清空账号相关数据（按约定：现有仅为测试数据）。
-- 执行前建议：docker exec companion-postgres-1 pg_dump -U companion companion > backup.sql
--
-- 用法：
--   docker exec -i companion-postgres-1 psql -U companion -d companion < deploy/migrations/001_reset_users.sql
-- 然后重启服务（会自动 create_all 建新表结构）：
--   sudo systemctl restart companion

BEGIN;

DROP TABLE IF EXISTS turn_events CASCADE;
DROP TABLE IF EXISTS turns CASCADE;
DROP TABLE IF EXISTS messages CASCADE;
DROP TABLE IF EXISTS memories CASCADE;
DROP TABLE IF EXISTS sessions CASCADE;
DROP TABLE IF EXISTS memory_vectors CASCADE;
DROP TABLE IF EXISTS user_auth CASCADE;
DROP TABLE IF EXISTS user_profile CASCADE;
DROP TABLE IF EXISTS users CASCADE;

COMMIT;
