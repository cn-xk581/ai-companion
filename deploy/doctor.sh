#!/usr/bin/env bash
# 诊断：注册 500 时先跑这个，把输出贴给开发。
set -uo pipefail
echo "== 1) users 表结构（是否还是旧 schema）=="
docker exec -i companion-postgres-1 psql -U companion -d companion -c '\d users' || true
echo
echo "== 2) 表清单 =="
docker exec -i companion-postgres-1 psql -U companion -d companion -c '\dt' || true
echo
echo "== 3) 最近的注册错误 =="
sudo journalctl -u companion -n 200 --no-pager 2>/dev/null \
  | grep -E "Traceback|Error|UndefinedColumn|UndefinedTable|asyncpg|register" \
  | tail -n 25 || true
echo
echo "== 4) 健康检查 =="
curl -s --max-time 5 http://127.0.0.1:8000/api/v1/health || true
echo
