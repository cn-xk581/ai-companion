#!/usr/bin/env bash
# deploy/stop.sh — 停止后端服务；加 --all 同时停止基础设施。
#
# 用法：
#   bash deploy/stop.sh          # 只停后端，保留 Postgres/Redis（数据不丢）
#   bash deploy/stop.sh --all    # 后端 + 基础设施都停
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

STOP_INFRA="${1:-}"

main() {
  stop_service

  if [[ "$STOP_INFRA" == "--all" || "$STOP_INFRA" == "all" ]]; then
    log "停止基础设施 (docker compose down)..."
    docker compose down
    log "基础设施已停止（数据卷保留，下次 start 自动恢复）"
  else
    log "基础设施保持运行；如需一并停止：bash deploy/stop.sh --all"
  fi
}

main "$@"
