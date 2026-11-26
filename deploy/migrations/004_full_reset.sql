-- 004: 彻底重建用户/角色/记忆相关表（仅测试数据时使用）
--
-- 背景：用户表从 VARCHAR 主键改成 BIGINT + 新增 user_auth/user_profile 后，
-- 旧库的 users 表不会自动升级（create_all 只建新表、不改旧表），
-- 注册会在 INSERT users 时因缺列报 500。
--
-- ⚠️ 会删除全部账号、会话、消息、记忆、角色卡。执行前先备份：
--   docker exec companion-postgres-1 pg_dump -U companion companion > backup-$(date +%F).sql
--
-- 用法：
--   docker exec -i companion-postgres-1 psql -U companion -d companion \
--     < deploy/migrations/004_full_reset.sql
--   sudo systemctl restart companion

BEGIN;

DROP TABLE IF EXISTS turn_events CASCADE;
DROP TABLE IF EXISTS turns CASCADE;
DROP TABLE IF EXISTS messages CASCADE;
DROP TABLE IF EXISTS memories CASCADE;
DROP TABLE IF EXISTS sessions CASCADE;
DROP TABLE IF EXISTS memory_vectors CASCADE;

DROP TABLE IF EXISTS session_summaries CASCADE;
DROP TABLE IF EXISTS relation_episodes CASCADE;
DROP TABLE IF EXISTS relation_facts CASCADE;
DROP TABLE IF EXISTS user_character_relations CASCADE;

DROP TABLE IF EXISTS character_media CASCADE;
DROP TABLE IF EXISTS character_lorebook_entries CASCADE;
DROP TABLE IF EXISTS character_lorebooks CASCADE;
DROP TABLE IF EXISTS character_versions CASCADE;
DROP TABLE IF EXISTS characters CASCADE;

DROP TABLE IF EXISTS user_auth CASCADE;
DROP TABLE IF EXISTS user_profile CASCADE;
DROP TABLE IF EXISTS users CASCADE;

COMMIT;
