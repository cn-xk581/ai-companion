#!/usr/bin/env bash
# deploy/common.sh — 部署脚本共享变量与工具函数（供 start/stop/redeploy 引用）。
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$DEPLOY_DIR/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"
SERVICE_NAME="aigf"
PID_FILE="$PROJECT_ROOT/aigf.pid"
LOG_FILE="$PROJECT_ROOT/aigf.log"

# 后端监听地址/端口（可用环境变量覆盖；默认只监听本机，由 nginx 反代对外）
BIND_HOST="${BIND_HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

cd "$PROJECT_ROOT"

log()  { printf '\033[1;34m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[deploy]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[deploy]\033[0m %s\n' "$*" >&2; exit 1; }

SUDO=""
[[ "$(id -u)" == "0" ]] || SUDO="sudo"

# ---- .env 解析 ----

# 读取 .env 中某个键的值（未找到返回空；支持 KEY=value 的简单格式）
env_val() {
  local key="$1" line v
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$ENV_FILE" 2>/dev/null | tail -n1 || true)"
  [[ -z "$line" ]] && return 0
  v="${line#*=}"                    # 去掉 key=
  v="${v%%#*}"                      # 去掉行内注释
  v="$(printf '%s' "$v" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  printf '%s' "$v"
}

ensure_env_file() {
  [[ -f "$ENV_FILE" ]] || die "缺少 $ENV_FILE：请先 cp .env.example .env 并填写生产配置"
}

require_env() {
  local key="$1" v
  v="$(env_val "$key")"
  [[ -n "$v" ]] || die ".env 缺少 $key"
  case "$v" in
    change-me*|dev-only-secret-change-me|sk-your-key|"<"*) die ".env 的 $key 仍是占位值，请填写真实值" ;;
  esac
}

# 生产环境强制校验（与 app/main.py:_validate_production 对齐）
check_prod_env() {
  local env
  env="$(env_val ENVIRONMENT)"
  [[ "$env" == "prod" ]] || return 0
  log "检测到 ENVIRONMENT=prod，校验关键配置..."
  require_env LLM_API_KEY
  require_env DATABASE_URL
  require_env REDIS_URL
  require_env JWT_SECRET
  local vec emb
  vec="$(env_val VECTOR_STORE_BACKEND)"
  emb="$(env_val EMBEDDING_BACKEND)"
  [[ "$vec" == "pgvector" || "$vec" == "milvus" ]] || die "生产要求 VECTOR_STORE_BACKEND=pgvector 或 milvus，当前=[$vec]"
  [[ "$emb" != "hash" && -n "$emb" ]] || die "生产禁止 EMBEDDING_BACKEND=hash，当前=[$emb]"
}

# ---- 基础设施 ----

wait_for_infra() {
  local i
  log "等待 PostgreSQL 就绪..."
  for i in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U postgres >/dev/null 2>&1; then
      log "PostgreSQL 已就绪"
      return 0
    fi
    sleep 2
  done
  warn "PostgreSQL 60s 内未就绪，继续启动（后端可能因连不上库而失败）"
}

# ---- 依赖 / 前端 ----

install_deps() {
  local force="${1:-0}" py="$PROJECT_ROOT/.venv/bin/python"
  if [[ -x "$py" && "$force" != "1" ]]; then
    log "Python 虚拟环境已存在，跳过依赖安装（删 .venv 或用 redeploy 强制重装）"
    return 0
  fi
  log "创建虚拟环境并安装依赖（首次含 torch，较慢）..."
  command -v python3 >/dev/null 2>&1 || die "未找到 python3，请先安装 Python 3.10+"
  [[ -x "$py" ]] || python3 -m venv "$PROJECT_ROOT/.venv"
  "$py" -m pip install -U pip
  local embed
  embed="$(env_val EMBEDDING_BACKEND)"
  if [[ "$embed" == "sentence-transformers" ]]; then
    "$py" -m pip install -e ".[embed]"
  else
    "$py" -m pip install -e "."
  fi
}

build_frontend() {
  local dist="$PROJECT_ROOT/frontend/dist/index.html"
  if [[ -f "$dist" && "${REBUILD:-0}" != "1" ]]; then
    log "前端产物已存在，跳过构建（REBUILD=1 可强制重建）"
    return 0
  fi
  if ! command -v node >/dev/null 2>&1; then
    if [[ -f "$dist" ]]; then
      warn "未检测到 node，但 dist 已存在，跳过构建"
    else
      warn "未检测到 node，无法构建前端；后端将以纯 API 模式运行（无聊天 UI）"
    fi
    return 0
  fi
  log "构建前端 (npm install + vite build)..."
  ( cd "$PROJECT_ROOT/frontend" && \
      { [[ -d node_modules ]] || npm install --no-audit --no-fund; } && \
      npm run build )
}

# ---- 后端服务（systemd 优先，nohup 降级） ----

using_systemd() { command -v systemctl >/dev/null 2>&1; }

install_systemd_unit() {
  local tpl="$DEPLOY_DIR/aigf.service" dst="/etc/systemd/system/${SERVICE_NAME}.service" py="$PROJECT_ROOT/.venv/bin/python"
  [[ -f "$tpl" ]] || die "缺少模板 $tpl"
  [[ -x "$py" ]] || die "找不到 $py，请先安装依赖"
  sed -e "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" \
      -e "s|__USER__|$(id -un)|g" \
      -e "s|__BIND_HOST__|$BIND_HOST|g" \
      -e "s|__PORT__|$PORT|g" \
      "$tpl" | $SUDO tee "$dst" >/dev/null
  log "已生成 $dst"
}

start_foreground() {
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    warn "后端已在运行 (pid $(cat "$PID_FILE"))"
    return 0
  fi
  log "无 systemd，使用 nohup 后台进程启动..."
  (
    set -a; source "$ENV_FILE"; set +a
    nohup "$PROJECT_ROOT/.venv/bin/python" -m uvicorn app.main:app \
      --host "$BIND_HOST" --port "$PORT" > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
  )
  log "已启动 (pid $(cat "$PID_FILE"))，日志见 $LOG_FILE"
}

stop_foreground() {
  if [[ -f "$PID_FILE" ]]; then
    local pid; pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
      log "停止后端 (pid $pid)..."
      kill "$pid"
    fi
    rm -f "$PID_FILE"
  fi
}

deploy_service() {
  if using_systemd; then
    install_systemd_unit
    $SUDO systemctl daemon-reload
    $SUDO systemctl enable --now "$SERVICE_NAME"
    log "已通过 systemd 启动 $SERVICE_NAME"
  else
    start_foreground
  fi
}

stop_service() {
  if using_systemd; then
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
      log "停止后端服务 $SERVICE_NAME ..."
      $SUDO systemctl stop "$SERVICE_NAME"
    else
      warn "后端服务未在运行"
    fi
  else
    stop_foreground
  fi
}

restart_service() {
  if using_systemd; then
    install_systemd_unit
    $SUDO systemctl daemon-reload
    $SUDO systemctl restart "$SERVICE_NAME"
    log "已通过 systemd 重启 $SERVICE_NAME"
  else
    stop_foreground
    start_foreground
  fi
}

print_status() {
  echo
  log "完成。访问方式："
  if [[ "$BIND_HOST" == "0.0.0.0" ]]; then
    log "  聊天界面: http://<服务器IP>:$PORT/"
  else
    log "  后端监听 127.0.0.1:$PORT（请用 nginx 反代到 80/443）"
  fi
  log "  API 文档: http://127.0.0.1:$PORT/docs"
  log "  健康检查: curl http://127.0.0.1:$PORT/api/v1/health"
  if using_systemd; then
    log "  查看日志: journalctl -u $SERVICE_NAME -f"
  else
    log "  查看日志: tail -f $LOG_FILE"
  fi
}
