#!/usr/bin/env bash
# =============================================================================
# deploy/status.sh — 一眼看清这台机器上装了没、活着没、数据在哪、占了多大
# 只读脚本：不修改任何东西，可以随时跑。
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/lib.sh
source "$SCRIPT_DIR/lib.sh"

STATE_DIR="${STATE_DIR:-$DEFAULT_STATE_DIR}"
MANIFEST="$STATE_DIR/install-manifest.env"

section "安装清单"
if [[ -f "$MANIFEST" ]]; then
  manifest_load "$MANIFEST" || die "清单解析失败：$MANIFEST"
  ok "已安装：$PREFIX（安装于 ${INSTALLED_AT:-未知}，版本 ${VERSION:-?}）"
else
  warn "未找到清单（$MANIFEST）：可能未安装，或用 --force 装过"
  exit 0
fi

section "systemd"
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
  ok "服务运行中：$SERVICE_NAME"
else
  err "服务未运行：$SERVICE_NAME（journalctl -u $SERVICE_NAME -n 50）"
fi
systemctl status "$SERVICE_NAME" --no-pager -n 5 2>/dev/null | sed 's/^/  /' || true

section "健康检查"
curl -fsS --max-time 5 "http://127.0.0.1:${PORT:-8000}/api/v1/health" 2>/dev/null \
  | sed 's/^/  /' || err "健康检查失败（curl http://127.0.0.1:${PORT:-8000}/api/v1/health）"
printf '\n' >&2

section "容器与卷"
if command_exists docker && docker_ready; then
  docker compose -p "$COMPOSE_PROJECT" --env-file "$PREFIX/.env" -f "$COMPOSE_FILE" ps 2>/dev/null \
    | sed 's/^/  /' || warn "compose ps 失败"
  docker volume ls --filter "name=${COMPOSE_PROJECT}_" --format '  volume {{.Name}}' 2>/dev/null || true
else
  warn "docker 不可用"
fi

section "磁盘占用"
du -sh "$PREFIX" 2>/dev/null | sed 's/^/  /' || true
du -sh "$STATE_DIR" 2>/dev/null | sed 's/^/  /' || true

section "最近日志"
journalctl -u "$SERVICE_NAME" -n 15 --no-pager 2>/dev/null | sed 's/^/  /' || true

echo >&2
info "升级：git pull && sudo bash deploy/install.sh --upgrade"
info "卸载预演：sudo bash deploy/uninstall.sh --dry-run"
