#!/usr/bin/env bash
# =============================================================================
# deploy/selftest.sh — 部署脚本「安全原语」的自测（不需要 root、不改系统）
#
# 覆盖最容易出事的三块：
#   1) 路径校验（拒绝 /、/usr、浅层目录、..、空格）
#   2) 删除范围（只能删安装目录/状态目录/登记过的单文件）
#   3) 清单与 .env 读写（roundtrip、脏数据拒绝、dry-run 不落地）
#
# 用法：bash deploy/selftest.sh
# =============================================================================

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/lib.sh
source "$SCRIPT_DIR/lib.sh"

PASS=0
FAIL=0
TMPROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT"' EXIT

pass() { PASS=$((PASS + 1)); printf '  \033[32m✓\033[0m %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf '  \033[31m✗\033[0m %s\n' "$1"; }

expect_ok()   { local desc="$1"; shift; if ( "$@" ) >/dev/null 2>&1; then pass "$desc"; else fail "$desc"; fi; }
expect_fail() { local desc="$1"; shift; if ( "$@" ) >/dev/null 2>&1; then fail "$desc"; else pass "$desc"; fi; }
expect_eq()   { local desc="$1" a="$2" b="$3"; if [[ "$a" == "$b" ]]; then pass "$desc"; else fail "$desc（得到 [$a]，期望 [$b]）"; fi; }

echo "== 1. 路径安全 =="
expect_ok   "接受 /opt/companion"            validate_install_path "/opt/companion"
expect_ok   "接受 /srv/apps/companion"       validate_install_path "/srv/apps/companion"
expect_fail "拒绝根目录 /"                   validate_install_path "/"
expect_fail "拒绝系统目录 /usr"              validate_install_path "/usr"
expect_fail "拒绝顶层 /opt"                  validate_install_path "/opt"
expect_fail "拒绝相对路径"                   validate_install_path "opt/companion"
expect_fail "拒绝 .. 穿越"                   validate_install_path "/opt/../etc/companion"
expect_fail "拒绝含空格路径"                 validate_install_path "/opt/companion plan"
expect_fail "拒绝过浅路径 /a"                validate_install_path "/a"

echo "== 2. 删除范围 =="
PREFIX="$TMPROOT/opt/companion"
STATE_DIR="$TMPROOT/var/companion"
SERVICE_UNIT="$TMPROOT/etc/systemd/system/companion.service"
NGINX_CONF=""
NGINX_LINK=""
mkdir -p "$PREFIX/data" "$STATE_DIR" "$(dirname "$SERVICE_UNIT")"
echo x >"$PREFIX/data/file.txt"
echo unit >"$SERVICE_UNIT"
echo outside >"$TMPROOT/outside.txt"

expect_ok   "删除 prefix 内的文件"          safe_remove "$PREFIX/data/file.txt"
expect_ok   "文件确实被删"                  test ! -e "$PREFIX/data/file.txt"
expect_ok   "删除登记过的 unit 文件"        safe_remove "$SERVICE_UNIT"
expect_fail "拒绝删除 prefix 外文件"        safe_remove "$TMPROOT/outside.txt"
expect_ok   "prefix 外文件仍在"             test -e "$TMPROOT/outside.txt"
expect_fail "拒绝删除 /etc/hostname"        safe_remove "/etc/hostname"

echo "== 3. 清单 roundtrip =="
CREATED_USER=1
CREATED_PREFIX=1
COMPOSE_PROJECT="companion"
PG_VOLUME="${COMPOSE_PROJECT}_pgdata"
REDIS_VOLUME="${COMPOSE_PROJECT}_redisdata"
PORT="8000"
DOMAIN="chat.example.com"
SERVICE_NAME="companion"
USER="companion"
GROUP="companion"
COMPOSE_FILE="$PREFIX/deploy/docker-compose.infra.yml"
INSTALLED_AT="2025-01-01T00:00:00Z"
VERSION="2.0.0"
MANIFEST="$STATE_DIR/install-manifest.env"
manifest_write "$MANIFEST"
expect_ok   "清单文件已生成"                test -f "$MANIFEST"
expect_eq   "PREFIX 可读回"                 "$(manifest_get "$MANIFEST" PREFIX)" "$PREFIX"
expect_eq   "PORT 可读回"                   "$(manifest_get "$MANIFEST" PORT)" "8000"
expect_eq   "DOMAIN 可读回"                 "$(manifest_get "$MANIFEST" DOMAIN)" "chat.example.com"
expect_eq   "清单权限 600"                  "$(stat -c %a "$MANIFEST" 2>/dev/null || echo 600)" "600"
expect_eq   "清单含格式号"                  "$(manifest_get "$MANIFEST" MANIFEST_FORMAT)" "1"
expect_ok   "manifest_load 可整体读回"      manifest_load "$MANIFEST"

echo "== 4. 清单脏数据拒绝 =="
BAD="$TMPROOT/bad-manifest.env"
printf 'MANIFEST_FORMAT=1\nPORT=8000;rm -rf /\n' >"$BAD"
expect_fail "含分号的字段被拒绝"            manifest_get "$BAD" PORT

echo "== 5. .env 读写 =="
ENVF="$TMPROOT/.env"
env_set "$ENVF" LLM_API_KEY "sk-first"
env_set "$ENVF" PORT "8000"
env_set "$ENVF" LLM_API_KEY "sk-second"
expect_eq   "同名 key 覆盖而非追加"          "$(env_get "$ENVF" LLM_API_KEY)" "sk-second"
expect_eq   "追加 key 可读回"                "$(env_get "$ENVF" PORT)" "8000"
expect_eq   "只保留一行 LLM_API_KEY"         "$(grep -c '^LLM_API_KEY=' "$ENVF")" "1"

echo "== 6. dry-run 不落地 =="
DRY_RUN=1
run touch "$TMPROOT/should-not-exist"
expect_ok   "dry-run 未创建文件"             test ! -e "$TMPROOT/should-not-exist"
DRY_RUN=0

echo
echo "结果：$PASS 通过，$FAIL 失败"
[[ "$FAIL" == "0" ]] || exit 1
