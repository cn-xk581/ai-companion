#!/usr/bin/env bash
# deploy/redeploy.sh — 重部署：拉代码 → 重装依赖 → 重建前端 → 重启后端。
#
# 用法：
#   bash deploy/redeploy.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

main() {
  ensure_env_file
  check_prod_env

  if [[ -d "$PROJECT_ROOT/.git" ]]; then
    log "拉取最新代码..."
    ( cd "$PROJECT_ROOT" && git pull --ff-only )
  else
    warn "非 git 仓库，跳过 git pull"
  fi

  log "重装 Python 依赖..."
  install_deps 1

  log "重新构建前端..."
  REBUILD=1 build_frontend

  log "重启后端服务..."
  restart_service

  log "重部署完成"
  print_status
}

main "$@"
