#!/usr/bin/env bash
# deploy/start.sh — 一键启动完整栈：
#   基础设施(Docker) → Python 依赖 → 前端构建 → 后端服务(systemd/nohup)
#
# 用法：
#   bash deploy/start.sh                 # 首次启动 / 增量启动
#   REBUILD=1 bash deploy/start.sh       # 强制重建前端
#   BIND_HOST=0.0.0.0 bash deploy/start.sh  # 想直接对外暴露 8000（不推荐，应走 nginx）
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

main() {
  ensure_env_file
  check_prod_env

  log "1/5 启动基础设施 (Postgres+pgvector, Redis)..."
  docker compose up -d
  wait_for_infra

  log "2/5 安装 Python 依赖..."
  install_deps

  log "3/5 构建前端..."
  build_frontend

  log "4/5 启动后端服务..."
  deploy_service

  log "5/5 完成"
  print_status
}

main "$@"
