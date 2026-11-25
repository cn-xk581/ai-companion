#!/usr/bin/env bash
# =============================================================================
# deploy/install.sh — 一条命令装完，且每一步都可回滚（配合 uninstall.sh）
#
# 安装形态（默认）：
#   /opt/companion            代码 + venv + .env + data/（FastAPI 直接托管 dist）
#   /var/lib/companion        安装清单、.env 备份、部署日志
#   /etc/systemd/system/companion.service
#   /etc/nginx/sites-available/companion.conf（仅 --domain 时）
#   docker compose 项目 companion：postgres(pgvector) + redis + 两个命名卷
#
# 特性：
#   • 幂等：重复执行不会重复建用户/卷/服务；--upgrade 用于升级代码
#   • 可预演：--dry-run 打印全部动作，不碰系统
#   • 可审计：所有创建物写入 manifest，卸载只删清单里的东西
#   • 安全：端口只监听 127.0.0.1，systemd 加固，.env 640 root:group
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=deploy/lib.sh
source "$SCRIPT_DIR/lib.sh"

# ---------- 默认参数 ----------
PREFIX="$DEFAULT_PREFIX"
STATE_DIR="$DEFAULT_STATE_DIR"
USER="$DEFAULT_USER"
GROUP=""
SERVICE_NAME="$DEFAULT_SERVICE"
PORT="$DEFAULT_PORT"
WORKERS="1"
DOMAIN=""
LLM_KEY="${LLM_API_KEY:-}"
LLM_BASE_URL="${LLM_BASE_URL:-https://api.deepseek.com/v1}"
LLM_MODEL="${LLM_MODEL:-deepseek-chat}"
PIP_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
NPM_REGISTRY="https://registry.npmmirror.com"
WITH_EMBED=1
REGISTRY_MIRROR=""
TORCH_CPU=0
WITH_DOCKER=1
WITH_NGINX=1
WITH_FRONTEND=1
UPGRADE=0
PG_PORT="5432"
REDIS_PORT="6379"
ASSUME_YES=0

usage() {
  cat <<'USAGE'
用法：sudo bash deploy/install.sh [选项]

  --prefix DIR        安装目录（默认 /opt/companion）
  --state-dir DIR     清单/日志/备份目录（默认 /var/lib/companion）
  --user NAME         运行用户（默认 companion，不存在则创建 system 用户）
  --port PORT         后端端口（默认 8000，只监听 127.0.0.1）
  --workers N         uvicorn worker 数（默认 1）
  --domain DOMAIN     配置 nginx 站点（如 chat.example.com）；不传则不装 nginx
  --llm-key KEY       LLM API Key（也可用环境变量 LLM_API_KEY；缺省时交互询问）
  --llm-base-url URL  OpenAI 兼容网关（默认 https://api.deepseek.com/v1）
  --llm-model NAME    模型名（默认 deepseek-chat）
  --pip-index URL     pip 源（默认清华镜像）
  --npm-registry URL  npm 源（默认 npmmirror）
  --registry-mirror URL  配置 Docker registry 镜像（写入 /etc/docker/daemon.json 并重启 docker）
  --torch-cpu        安装 CPU 版 torch（不拉 CUDA 依赖，省 2GB+ 磁盘）
  --skip-embed        不装 sentence-transformers，改用 hash 嵌入 + dev 模式（仅联调用）
  --no-docker         不启动内置 PG/Redis（需自备 DATABASE_URL / REDIS_URL）
  --no-frontend       跳过前端构建（沿用已有 frontend/dist）
  --no-nginx          即使给了 --domain 也不装 nginx
  --pg-port PORT      PG 端口（默认 5432，绑定 127.0.0.1）
  --redis-port PORT   Redis 端口（默认 6379，绑定 127.0.0.1）
  --upgrade           升级已有安装（沿用清单参数，保留 .env 与 data/）
  --dry-run           预演：只打印动作，不改系统
  --yes               非交互确认
  -h, --help          帮助

示例：
  sudo bash deploy/install.sh --domain chat.example.com --llm-key sk-xxx
  sudo bash deploy/install.sh --upgrade
  sudo bash deploy/install.sh --dry-run --domain chat.example.com
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --state-dir) STATE_DIR="$2"; shift 2 ;;
    --user) USER="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --llm-key) LLM_KEY="$2"; shift 2 ;;
    --llm-base-url) LLM_BASE_URL="$2"; shift 2 ;;
    --llm-model) LLM_MODEL="$2"; shift 2 ;;
    --pip-index) PIP_INDEX="$2"; shift 2 ;;
    --npm-registry) NPM_REGISTRY="$2"; shift 2 ;;
    --skip-embed) WITH_EMBED=0; shift ;;
    --torch-cpu) TORCH_CPU=1; shift ;;
    --no-docker) WITH_DOCKER=0; shift ;;
    --no-frontend) WITH_FRONTEND=0; shift ;;
    --no-nginx) WITH_NGINX=0; shift ;;
    --registry-mirror) REGISTRY_MIRROR="$2"; shift 2 ;;
    --pg-port) PG_PORT="$2"; shift 2 ;;
    --redis-port) REDIS_PORT="$2"; shift 2 ;;
    --upgrade) UPGRADE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数：$1（--help 查看用法）" ;;
  esac
done

[[ -z "$DOMAIN" ]] && WITH_NGINX=0
[[ -z "$GROUP" ]] && GROUP="$USER"

# ---------- 派生路径（卸载时要清理的对象，全部进 manifest） ----------
MANIFEST="$STATE_DIR/install-manifest.env"
SERVICE_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
NGINX_CONF=""
NGINX_LINK=""
COMPOSE_PROJECT="$DEFAULT_COMPOSE_PROJECT"
COMPOSE_FILE="$PREFIX/deploy/docker-compose.infra.yml"
PG_USER="companion"
PG_DB="companion"
PG_VOLUME="${COMPOSE_PROJECT}_pgdata"
REDIS_VOLUME="${COMPOSE_PROJECT}_redisdata"
CREATED_USER=0
CREATED_PREFIX=0
LOG_FILE="${STATE_DIR}/deploy.log"
# dry-run 不落任何文件（包括日志）
[[ "$DRY_RUN" == "1" ]] && LOG_FILE=""

# ---------- 升级：以清单为准 ----------
if [[ -f "$MANIFEST" ]]; then
  [[ "$UPGRADE" == "1" ]] || die "检测到已有安装（$MANIFEST）。升级用 --upgrade，彻底卸载用 deploy/uninstall.sh"
  manifest_load "$MANIFEST" || die "安装清单解析失败：$MANIFEST"
  validate_install_path "$PREFIX" || die "清单里的 --prefix 不安全：$PREFIX"
  validate_install_path "$STATE_DIR" || die "清单里的 --state-dir 不安全：$STATE_DIR"
  SERVICE_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
  # DOMAIN 也在清单里：原安装配了 nginx 就继续维护它，没配就保持不装
  if [[ -n "${DOMAIN:-}" ]]; then WITH_NGINX=1; else WITH_NGINX=0; fi
  COMPOSE_FILE="$PREFIX/deploy/docker-compose.infra.yml"
  PG_VOLUME="${COMPOSE_PROJECT}_pgdata"
  REDIS_VOLUME="${COMPOSE_PROJECT}_redisdata"
  LOG_FILE="${STATE_DIR}/deploy.log"
  warn "升级模式：沿用清单中的参数（prefix=$PREFIX port=$PORT domain=${DOMAIN:-无}），本次传入的参数仅 --llm-* 生效"
fi

# ---------- Docker 镜像加速（可选，--registry-mirror）----------
configure_docker_mirror() {
  local url="$1" cfg="/etc/docker/daemon.json"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '  %s 合并 registry-mirrors=%s 到 %s 并重启 docker\n' \
      "${C_YELLOW}[dry-run]${C_RESET}" "$url" "$cfg" >&2
    return 0
  fi
  mkdir -p /etc/docker
  "$PYTHON_BIN" - "$cfg" "$url" <<'PY'
import json, os, sys
path, url = sys.argv[1], sys.argv[2]
data = {}
if os.path.exists(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}
mirrors = [m for m in data.get("registry-mirrors", []) if m != url]
mirrors.insert(0, url)
data["registry-mirrors"] = mirrors
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2, ensure_ascii=False)
PY
  systemctl restart docker
  docker info >/dev/null 2>&1 || die "配置镜像加速后 docker 不可用，检查 $cfg"
  ok "已配置 Docker 镜像加速：$url"
}

# ---------- 前置检查 ----------
section "1/9 前置检查"
if [[ "$DRY_RUN" != "1" ]]; then
  require_linux
  require_root
else
  warn "dry-run：跳过 Linux/root 校验，仅打印计划"
fi

validate_install_path "$PREFIX" || die "--prefix 不安全"
validate_install_path "$STATE_DIR" || die "--state-dir 不安全"
[[ "$PREFIX" != "$STATE_DIR" ]] || die "--prefix 与 --state-dir 不能相同"
case "$PREFIX" in "$STATE_DIR"/*) die "--prefix 不能放在 --state-dir 里" ;; esac
case "$STATE_DIR" in "$PREFIX"/*) die "--state-dir 不能放在 --prefix 里" ;; esac

command_exists curl || die "缺少 curl"
command_exists tar || die "缺少 tar"
command_exists openssl || die "缺少 openssl（用于生成密钥）"

# dry-run 允许在任意机器上预演：缺工具只告警，不中断
need_cmd() {
  local cmd="$1" hint="$2"
  if command_exists "$cmd"; then return 0; fi
  if [[ "$DRY_RUN" == "1" ]]; then warn "缺少 $cmd（dry-run 跳过）：$hint"; return 0; fi
  die "$hint"
}

PYTHON_BIN=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command_exists "$candidate" && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    PYTHON_BIN="$candidate"; break
  fi
done
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ "$DRY_RUN" == "1" ]]; then PYTHON_BIN="python3"; warn "缺少 Python>=3.10（dry-run 跳过）"; else die "需要 Python >= 3.10"; fi
fi
if "$PYTHON_BIN" -c 'import sys' >/dev/null 2>&1; then
  ok "Python: $($PYTHON_BIN -V 2>&1 | head -1)"
else
  warn "Python 不可用（dry-run 继续）：$PYTHON_BIN"
fi

if [[ "$WITH_DOCKER" == "1" ]]; then
  need_cmd docker "缺少 docker（或用 --no-docker 并自备 DATABASE_URL/REDIS_URL）"
  if [[ "$DRY_RUN" != "1" ]]; then
    docker_ready || die "docker 守护进程不可用（systemctl start docker 后重试）"
    docker compose version >/dev/null 2>&1 || die "需要 docker compose v2"
  fi
fi
[[ "$WITH_FRONTEND" == "1" ]] && need_cmd npm "缺少 npm（或用 --no-frontend 提供 frontend/dist）"
if [[ "$WITH_NGINX" == "1" ]] && ! command_exists nginx; then
  warn "未安装 nginx，将跳过站点配置"; WITH_NGINX=0
fi
if [[ "$DRY_RUN" != "1" ]] && port_in_use "$PORT"; then
  die "端口 $PORT 已被占用（ss -ltnp | grep :$PORT）"
fi
# 磁盘：torch + 依赖 + 镜像，默认至少 4GB 可用
if [[ "$DRY_RUN" != "1" ]]; then
  avail_kb=$(df -Pk "$(dirname "$PREFIX")" 2>/dev/null | awk 'NR==2 {print $4}')
  avail_kb=${avail_kb:-0}
  if (( avail_kb < 4*1024*1024 )); then
    die "磁盘空间不足：$(dirname "$PREFIX") 仅剩 $((avail_kb/1024))MB，至少需要 4GB。先清理或用 --torch-cpu / --skip-embed"
  elif (( avail_kb < 8*1024*1024 )); then
    warn "磁盘仅剩 $((avail_kb/1024))MB：首次装 torch 可能占用 2~3GB，建议腾到 8GB 以上或加 --torch-cpu"
  fi
fi

if [[ -n "$REGISTRY_MIRROR" ]]; then
  if [[ "$WITH_DOCKER" == "1" ]]; then
    configure_docker_mirror "$REGISTRY_MIRROR"
  else
    warn "--registry-mirror 已忽略：--no-docker 不使用容器"
  fi
fi
ok "前置检查通过"

# ---------- 用户与目录 ----------
section "2/9 用户与目录"
if id "$USER" >/dev/null 2>&1; then
  info "用户 $USER 已存在，复用"
else
  run useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$USER"
  CREATED_USER=1
  ok "已创建 system 用户 $USER"
fi
[[ -d "$PREFIX" ]] || CREATED_PREFIX=1
run mkdir -p "$PREFIX" "$PREFIX/data" "$STATE_DIR"
run chmod 750 "$PREFIX" "$STATE_DIR"

# ---------- 代码 ----------
section "3/9 拷贝代码（排除 .git/.venv/node_modules/dist/data/.env）"
if [[ -d "$PREFIX" && ! -f "$MANIFEST" && "$UPGRADE" == "0" && "$DRY_RUN" != "1" ]]; then
  warn "目录 $PREFIX 已存在但没有安装清单：将覆盖同名文件（不会删除目录里的其他文件）"
  warn "如需干净重装：先 backup 或 sudo bash deploy/uninstall.sh --force --prefix $PREFIX --dry-run 评估"
  confirm "继续覆盖安装？" || die "已取消"
fi
if [[ "$DRY_RUN" == "1" ]]; then
  printf '  %s tar -C %s --exclude ... | tar -C %s -x\n' "${C_YELLOW}[dry-run]${C_RESET}" "$REPO_DIR" "$PREFIX" >&2
else
  tar -C "$REPO_DIR" \
    --exclude='./.git' --exclude='./.venv' --exclude='./node_modules' \
    --exclude='./frontend/node_modules' --exclude='./frontend/dist' \
    --exclude='./data' --exclude='./.env' --exclude='./.pytest_cache' \
    --exclude='./.idea' --exclude='./.dsh' --exclude='./__pycache__' \
    --exclude='*/__pycache__' --exclude='./pytest-cache-files-*' \
    -cf - . | tar -C "$PREFIX" -xf -
fi
ok "代码已就位"

# ---------- .env ----------
section "4/9 写入 .env（保留已有密钥）"
ENV_FILE="$PREFIX/.env"
if [[ -f "$ENV_FILE" && "$DRY_RUN" != "1" ]]; then
  BACKUP="$STATE_DIR/backups/env.$(date +%Y%m%d%H%M%S).bak"
  mkdir -p "$(dirname "$BACKUP")"
  cp -p "$ENV_FILE" "$BACKUP" && chmod 600 "$BACKUP"
  info "旧 .env 已备份：$BACKUP"
fi

EXISTING_LLM_KEY=""; EXISTING_JWT=""; EXISTING_ADMIN=""; EXISTING_PG_PASS=""
if [[ -f "$ENV_FILE" ]]; then
  EXISTING_LLM_KEY=$(env_get "$ENV_FILE" LLM_API_KEY || true)
  EXISTING_JWT=$(env_get "$ENV_FILE" JWT_SECRET || true)
  EXISTING_ADMIN=$(env_get "$ENV_FILE" ADMIN_TOKEN || true)
  EXISTING_PG_PASS=$(env_get "$ENV_FILE" PG_PASSWORD || true)
fi

[[ -z "$LLM_KEY" && -n "$EXISTING_LLM_KEY" ]] && LLM_KEY="$EXISTING_LLM_KEY"
if [[ -z "$LLM_KEY" ]]; then
  if [[ "$DRY_RUN" == "1" ]]; then
    LLM_KEY="sk-dry-run-placeholder"
  elif [[ -t 0 ]]; then
    LLM_KEY=$(ask "请输入 LLM_API_KEY（DeepSeek/OpenAI 兼容）")
  fi
  [[ -n "$LLM_KEY" ]] || die "缺少 LLM_API_KEY：用 --llm-key 传，或先 export LLM_API_KEY"
fi

JWT_SECRET="${EXISTING_JWT:-$(gen_secret 32)}"
ADMIN_TOKEN="${EXISTING_ADMIN:-$(gen_secret 24)}"
PG_PASSWORD="${EXISTING_PG_PASS:-$(gen_secret 16)}"
[[ ${#JWT_SECRET} -ge 32 ]] || die "JWT_SECRET 太短"
[[ ${#ADMIN_TOKEN} -ge 16 ]] || die "ADMIN_TOKEN 太短"

if [[ "$WITH_EMBED" == "1" ]]; then
  APP_ENV="prod"; EMBED_BACKEND="sentence-transformers"; CHECKPOINT="auto"
else
  APP_ENV="dev"; EMBED_BACKEND="hash"; CHECKPOINT="sqlite"
  warn "--skip-embed：hash 嵌入 + dev 模式，仅供联调，不具备生产语义"
fi

if [[ "$WITH_DOCKER" == "1" ]]; then
  DB_URL="postgresql+asyncpg://${PG_USER}:${PG_PASSWORD}@127.0.0.1:${PG_PORT}/${PG_DB}"
  REDIS_URL="redis://127.0.0.1:${REDIS_PORT}/0"
  VECTOR_BACKEND="pgvector"
else
  DB_URL="${DATABASE_URL:-}"; REDIS_URL="${REDIS_URL:-}"
  VECTOR_BACKEND="${VECTOR_STORE_BACKEND:-pgvector}"
  [[ -n "$DB_URL" && -n "$REDIS_URL" ]] || die "--no-docker 需要显式提供 DATABASE_URL 与 REDIS_URL 环境变量"
fi

[[ "$DRY_RUN" == "1" ]] || : >"$ENV_FILE"
env_set "$ENV_FILE" APP_NAME "AI 伴侣 Agent"
env_set "$ENV_FILE" ENVIRONMENT "$APP_ENV"
env_set "$ENV_FILE" LOG_LEVEL "INFO"
env_set "$ENV_FILE" LLM_API_KEY "$LLM_KEY"
env_set "$ENV_FILE" LLM_BASE_URL "$LLM_BASE_URL"
env_set "$ENV_FILE" LLM_MODEL "$LLM_MODEL"
env_set "$ENV_FILE" EMBEDDING_BACKEND "$EMBED_BACKEND"
env_set "$ENV_FILE" EMBEDDING_MODEL "BAAI/bge-small-zh-v1.5"
env_set "$ENV_FILE" HF_ENDPOINT "https://hf-mirror.com"
env_set "$ENV_FILE" DATABASE_URL "$DB_URL"
env_set "$ENV_FILE" REDIS_URL "$REDIS_URL"
env_set "$ENV_FILE" VECTOR_STORE_BACKEND "$VECTOR_BACKEND"
env_set "$ENV_FILE" CHECKPOINT_BACKEND "$CHECKPOINT"
env_set "$ENV_FILE" CHECKPOINT_SQLITE_PATH "$PREFIX/data/checkpoints.sqlite3"
env_set "$ENV_FILE" JWT_SECRET "$JWT_SECRET"
env_set "$ENV_FILE" ADMIN_TOKEN "$ADMIN_TOKEN"
env_set "$ENV_FILE" TRUST_PROXY_HEADERS "true"
env_set "$ENV_FILE" TRACE_ENABLED "true"
env_set "$ENV_FILE" SINGLEFLIGHT_ENABLED "true"
env_set "$ENV_FILE" PG_USER "$PG_USER"
env_set "$ENV_FILE" PG_DB "$PG_DB"
env_set "$ENV_FILE" PG_PASSWORD "$PG_PASSWORD"
env_set "$ENV_FILE" PG_PORT "$PG_PORT"
env_set "$ENV_FILE" REDIS_PORT "$REDIS_PORT"
if [[ "$DRY_RUN" != "1" ]]; then
  # systemd 以 root 读取环境文件，再用 User= 降权；服务进程需要能读，故 root:group 640
  chown root:"$GROUP" "$ENV_FILE"
  chmod 640 "$ENV_FILE"
fi
ok ".env 已写入（root:$GROUP 640）"

# ---------- Python ----------
section "5/9 Python 环境（venv + 依赖）"
if [[ "$DRY_RUN" == "1" ]]; then
  if [[ "$TORCH_CPU" == "1" ]]; then
    printf '  %s pip install torch --index-url https://download.pytorch.org/whl/cpu（CPU 版）\n' "${C_YELLOW}[dry-run]${C_RESET}" >&2
  fi
  printf '  %s %s -m venv %s/.venv && pip install -e "%s%s"\n' \
    "${C_YELLOW}[dry-run]${C_RESET}" "$PYTHON_BIN" "$PREFIX" "$PREFIX" "$([[ $WITH_EMBED == 1 ]] && echo '[embed]')" >&2
else
  # pip 缓存与临时文件放 data 目录，避免把 / 写满（torch 单个 wheel 500MB+）
  PIP_CACHE_DIR="$PREFIX/data/pip-cache"
  TMPDIR="$PREFIX/data/tmp"
  export PIP_CACHE_DIR TMPDIR
  run mkdir -p "$PIP_CACHE_DIR" "$TMPDIR"

  [[ -x "$PREFIX/.venv/bin/python" ]] || run "$PYTHON_BIN" -m venv "$PREFIX/.venv"
  run "$PREFIX/.venv/bin/python" -m pip install --quiet --upgrade pip -i "$PIP_INDEX"
  if [[ "$WITH_EMBED" == "1" ]]; then INSTALL_TARGET="$PREFIX[embed]"; else INSTALL_TARGET="$PREFIX"; fi
  if [[ "$TORCH_CPU" == "1" ]]; then
    log "安装 CPU 版 torch（不拉 CUDA 依赖，省 2GB+）"
    run "$PREFIX/.venv/bin/python" -m pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
  fi
  log "安装依赖（首次含 torch，5~15 分钟）…"
  run_shell "'$PREFIX/.venv/bin/python' -m pip install -e '$INSTALL_TARGET' -i '$PIP_INDEX'"
  run env "PYTHONPATH=$PREFIX" "$PREFIX/.venv/bin/python" -c "import app.main" >/dev/null
  ok "Python 依赖就绪"
fi

# ---------- 前端 ----------
section "6/9 前端构建"
if [[ "$WITH_FRONTEND" == "1" ]]; then
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '  %s cd %s/frontend && npm ci && npm run build && rm -rf node_modules\n' "${C_YELLOW}[dry-run]${C_RESET}" "$PREFIX" >&2
  else
    pushd "$PREFIX/frontend" >/dev/null
    if [[ -f package-lock.json ]]; then
      run npm ci --no-audit --no-fund --registry "$NPM_REGISTRY"
    else
      run npm install --no-audit --no-fund --registry "$NPM_REGISTRY"
    fi
    run npm run build
    popd >/dev/null
    [[ -f "$PREFIX/frontend/dist/index.html" ]] || die "前端构建失败：dist/index.html 不存在"
    run rm -rf "$PREFIX/frontend/node_modules"
    ok "前端构建完成（已清理 node_modules）"
  fi
else
  [[ -f "$PREFIX/frontend/dist/index.html" ]] || warn "--no-frontend 但 dist 不存在：GET / 会返回接口索引"
fi

# ---------- 基础设施 ----------
section "7/9 基础设施（postgres + redis）"
if [[ "$WITH_DOCKER" == "1" ]]; then
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '  %s %s pull（先拉镜像，失败重试 3 次）\n' "${C_YELLOW}[dry-run]${C_RESET}" "$(compose_cmd)" >&2
    printf '  %s %s up -d --remove-orphans\n' "${C_YELLOW}[dry-run]${C_RESET}" "$(compose_cmd)" >&2
  else
    pulled=0
    for attempt in 1 2 3; do
      if run_shell "$(compose_cmd) pull"; then pulled=1; break; fi
      warn "镜像拉取失败（第 $attempt/3 次），15 秒后重试…"
      sleep 15
    done
    [[ "$pulled" == "1" ]] || die "镜像拉取失败：国内网络请加 --registry-mirror https://docker.m.daocloud.io 后重跑"
    run_shell "$(compose_cmd) up -d --remove-orphans"
    log "等待 Postgres 就绪…"
    ready=0
    for _ in $(seq 1 60); do
      if docker compose -p "$COMPOSE_PROJECT" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" \
           exec -T postgres pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
        ready=1; break
      fi
      sleep 2
    done
    [[ "$ready" == "1" ]] || die "Postgres 120s 内未就绪：docker compose -p $COMPOSE_PROJECT logs postgres"
    ok "Postgres 就绪"
  fi
else
  info "--no-docker：跳过容器基础设施"
fi

# ---------- systemd ----------
section "8/9 systemd 服务"
if [[ "$DRY_RUN" == "1" ]]; then
  printf '  %s install %s（模板 %s）\n' \
    "${C_YELLOW}[dry-run]${C_RESET}" "$SERVICE_UNIT" "$PREFIX/deploy/companion.service.in" >&2
  printf '  %s systemctl daemon-reload && systemctl enable --now %s\n' \
    "${C_YELLOW}[dry-run]${C_RESET}" "$SERVICE_NAME" >&2
else
  UNIT_TMP=$(mktemp)
  sed -e "s|__USER__|$USER|g" -e "s|__GROUP__|$GROUP|g" \
      -e "s|__PREFIX__|$PREFIX|g" -e "s|__STATE_DIR__|$STATE_DIR|g" \
      -e "s|__PORT__|$PORT|g" -e "s|__WORKERS__|$WORKERS|g" \
      "$PREFIX/deploy/companion.service.in" >"$UNIT_TMP"
  install -m 644 "$UNIT_TMP" "$SERVICE_UNIT"
  rm -f "$UNIT_TMP"
  systemd_reload
  run systemctl enable "$SERVICE_NAME"
  run systemctl restart "$SERVICE_NAME"
  ok "服务已启用：$SERVICE_NAME"
fi

# ---------- nginx ----------
if [[ "$WITH_NGINX" == "1" ]]; then
  section "8.1/9 nginx 站点"
  NGINX_CONF="/etc/nginx/sites-available/${SERVICE_NAME}.conf"
  NGINX_LINK="/etc/nginx/sites-enabled/${SERVICE_NAME}.conf"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '  %s install %s（模板 %s）\n' \
      "${C_YELLOW}[dry-run]${C_RESET}" "$NGINX_CONF" "$PREFIX/deploy/nginx.conf.in" >&2
    printf '  %s ln -sfn %s %s && nginx -t && systemctl reload nginx\n' \
      "${C_YELLOW}[dry-run]${C_RESET}" "$NGINX_CONF" "$NGINX_LINK" >&2
  else
    NGINX_TMP=$(mktemp)
    sed -e "s|__DOMAIN__|$DOMAIN|g" -e "s|__PREFIX__|$PREFIX|g" \
        -e "s|__PORT__|$PORT|g" "$PREFIX/deploy/nginx.conf.in" >"$NGINX_TMP"
    install -m 644 "$NGINX_TMP" "$NGINX_CONF"
    rm -f "$NGINX_TMP"
    ln -sfn "$NGINX_CONF" "$NGINX_LINK"
    nginx -t || die "nginx 配置校验失败（$NGINX_CONF）"
    systemctl reload nginx
    ok "nginx 已配置：http://$DOMAIN"
  fi
fi

# ---------- 权限 ----------
section "8.2/9 权限收紧"
if [[ "$DRY_RUN" != "1" ]]; then
  chown -R root:"$GROUP" "$PREFIX"
  chmod -R g+rX,o-rwx "$PREFIX"
  chown -R "$USER":"$GROUP" "$PREFIX/data"
  chmod 700 "$PREFIX/data"
  chmod 640 "$ENV_FILE"
  chown root:"$GROUP" "$ENV_FILE"
  ok "代码 root:group 只读、data 归 $USER、.env 640"
fi

# ---------- 健康检查 ----------
section "9/9 健康检查"
HEALTH_URL="http://127.0.0.1:${PORT}/api/v1/health"
if [[ "$DRY_RUN" == "1" ]]; then
  printf '  %s curl %s（最多 300s；首次启动会下载 bge 模型）\n' "${C_YELLOW}[dry-run]${C_RESET}" "$HEALTH_URL" >&2
elif wait_http "$HEALTH_URL" 300; then
  ok "健康检查通过：$HEALTH_URL"
else
  err "健康检查失败，最近日志："
  journalctl -u "$SERVICE_NAME" -n 60 --no-pager || true
  die "安装未完成：修复后重跑 --upgrade，或先 deploy/uninstall.sh 回滚"
fi

# ---------- 清单 ----------
INSTALLED_AT=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
VERSION="$DEPLOY_VERSION"
manifest_write "$MANIFEST"
[[ "$DRY_RUN" == "1" ]] || ok "安装清单：$MANIFEST"

cat >&2 <<SUMMARY

$(printf '%s' "${C_GREEN}")安装完成${C_RESET}

  代码/虚拟环境   $PREFIX
  运行数据        $PREFIX/data
  清单与日志      $STATE_DIR
  systemd         $SERVICE_NAME
  监听            127.0.0.1:$PORT （仅本机）

  健康检查        curl -s $HEALTH_URL
$( [[ -n "$DOMAIN" ]] && printf '  公网入口        http://%s（nginx）\n' "$DOMAIN" )
  运维令牌        sudo grep ADMIN_TOKEN $ENV_FILE

  升级            git pull && sudo bash deploy/install.sh --upgrade
  卸载预演        sudo bash deploy/uninstall.sh --dry-run
  彻底卸载        sudo bash deploy/uninstall.sh --yes

SUMMARY
