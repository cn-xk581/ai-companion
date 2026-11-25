#!/usr/bin/env bash
# =============================================================================
# deploy/lib.sh — 部署脚本共用的安全原语
#
# 设计目标：让「装」和「卸」都可重复执行、可审计、可预演，且**只动自己创建的东西**。
#   1) 所有写操作走 run()/write_file()，支持 --dry-run 只打印不落地；
#   2) 所有删除走 safe_remove()，拒绝 /、/usr、浅层目录、安装范围外的路径；
#   3) 安装清单（manifest）用纯 KEY=VALUE 文本，不依赖 jq，读取时做字符集校验；
#   4) 日志同时写 stderr 与 $STATE_DIR/deploy.log（dry-run 时写临时目录）。
# =============================================================================

set -euo pipefail

# ---------- 默认值（可被 install.sh 参数覆盖） ----------
DEFAULT_PREFIX="/opt/companion"
DEFAULT_STATE_DIR="/var/lib/companion"
DEFAULT_USER="companion"
DEFAULT_SERVICE="companion"
DEFAULT_PORT="8000"
DEFAULT_COMPOSE_PROJECT="companion"
DEPLOY_VERSION="2.0.0"

# shellcheck disable=SC2034
FORBIDDEN_PATHS=(
  "/" "/usr" "/usr/local" "/etc" "/bin" "/sbin" "/lib" "/lib64" "/boot"
  "/dev" "/proc" "/sys" "/root" "/home" "/var" "/opt" "/srv" "/tmp" "/run" "/mnt" "/media"
)

# ---------- 输出 ----------
if [[ -t 1 ]] && [[ "${NO_COLOR:-}" == "" ]]; then
  C_RESET=$'\033[0m'; C_DIM=$'\033[2m'; C_RED=$'\033[31m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BLUE=$'\033[36m'
else
  C_RESET=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""
fi

LOG_FILE="${LOG_FILE:-}"

_ts() { date '+%H:%M:%S'; }

log()  { printf '%s %s\n' "${C_DIM}$(_ts)${C_RESET}" "$*" >&2; _append_log "$*"; }
info() { printf '%s %s\n' "${C_BLUE}•${C_RESET}" "$*" >&2; _append_log "[info] $*"; }
ok()   { printf '%s %s\n' "${C_GREEN}✓${C_RESET}" "$*" >&2; _append_log "[ok] $*"; }
warn() { printf '%s %s\n' "${C_YELLOW}!${C_RESET}" "$*" >&2; _append_log "[warn] $*"; }
err()  { printf '%s %s\n' "${C_RED}✗${C_RESET}" "$*" >&2; _append_log "[err] $*"; }

_append_log() {
  [[ -n "$LOG_FILE" ]] || return 0
  mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || return 0
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG_FILE" 2>/dev/null || true
}

die() { err "$*"; exit 1; }

section() {
  printf '\n%s\n' "${C_DIM}── $* ─────────────────────────────${C_RESET}" >&2
}

# ---------- 执行封装：dry-run 只打印 ----------
DRY_RUN=${DRY_RUN:-0}

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '  %s %s\n' "${C_YELLOW}[dry-run]${C_RESET}" "$*" >&2
    return 0
  fi
  log "+ $*"
  "$@"
}

run_shell() {
  # 需要管道/重定向时用：run_shell 'cmd ...'
  local cmd="$1"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '  %s %s\n' "${C_YELLOW}[dry-run]${C_RESET}" "$cmd" >&2
    return 0
  fi
  log "+ $cmd"
  bash -c "$cmd"
}

# ---------- 基本校验 ----------
require_root() {
  if [[ "$DRY_RUN" == "1" ]]; then
    warn "dry-run 模式：跳过 root 校验（不会修改系统）"
    return 0
  fi
  [[ "$(id -u)" == "0" ]] || die "请用 root 执行（或 sudo bash $0 ...）"
}

require_linux() {
  [[ "$(uname -s)" == "Linux" ]] || die "本脚本面向 Linux（当前：$(uname -s)）。Windows/macOS 请用前端 dev + 本机后端调试。"
}

command_exists() { command -v "$1" >/dev/null 2>&1; }

confirm() {
  # confirm "提示"：非交互或 --yes 时直接通过
  local prompt="$1"
  if [[ "${ASSUME_YES:-0}" == "1" ]]; then
    return 0
  fi
  if [[ ! -t 0 ]]; then
    die "非交互环境下需要显式 --yes（或 --dry-run 预演）：$prompt"
  fi
  local answer
  read -r -p "$prompt [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" || "$answer" == "yes" ]]
}

# ---------- 路径安全 ----------
is_forbidden_path() {
  local p="${1%/}"
  local f
  for f in "${FORBIDDEN_PATHS[@]}"; do
    [[ "$p" == "$f" ]] && return 0
  done
  return 1
}

validate_install_path() {
  local p="$1"
  [[ -n "$p" ]] || { err "路径为空"; return 1; }
  [[ "$p" == /* ]] || { err "必须是绝对路径：$p"; return 1; }
  [[ "$p" != *".."* ]] || { err "不允许 ..：$p"; return 1; }
  [[ "$p" != *" "* ]] || { err "路径不允许空格：$p"; return 1; }
  [[ "$p" != *$'\n'* ]] || { err "路径不允许换行"; return 1; }
  local depth
  depth=$(awk -F/ '{print NF-1}' <<<"$p")
  (( depth >= 2 )) || { err "路径层级过浅（拒绝操作）：$p"; return 1; }
  is_forbidden_path "$p" && { err "系统目录禁止操作：$p"; return 1; }
  return 0
}

# 只允许删除「安装范围内」的路径：$PREFIX / $STATE_DIR 及其子树，或明确登记过的单文件
safe_remove() {
  local target="$1"
  validate_install_path "$target" || die "拒绝删除不安全路径：$target"

  case "$target" in
    "${PREFIX:-__none__}"|"${PREFIX:-__none__}"/*) ;;
    "${STATE_DIR:-__none__}"|"${STATE_DIR:-__none__}"/*) ;;
    "${SERVICE_UNIT:-__none__}"|"${NGINX_CONF:-__none__}"|"${NGINX_LINK:-__none__}") ;;
    *) die "拒绝删除安装范围外的路径：$target（PREFIX=$PREFIX STATE_DIR=$STATE_DIR）" ;;
  esac

  [[ -e "$target" || -L "$target" ]] || { log "已不存在，跳过：$target"; return 0; }

  local human_size="?"
  human_size=$(du -sh "$target" 2>/dev/null | awk '{print $1}' || echo "?")

  if [[ "$DRY_RUN" == "1" ]]; then
    printf '  %s rm -rf %s %s(%s)%s\n' "${C_YELLOW}[dry-run]${C_RESET}" "$target" "$C_DIM" "$human_size" "$C_RESET" >&2
    return 0
  fi
  log "+ rm -rf $target ($human_size)"
  rm -rf -- "$target"
}

# ---------- 清单（manifest） ----------
MANIFEST_KEYS=(
  MANIFEST_FORMAT PREFIX STATE_DIR USER GROUP SERVICE_NAME SERVICE_UNIT
  NGINX_CONF NGINX_LINK COMPOSE_PROJECT COMPOSE_FILE PG_VOLUME REDIS_VOLUME
  PG_USER PG_DB PORT DOMAIN CREATED_USER CREATED_PREFIX INSTALLED_AT VERSION
)

manifest_write() {
  local file="$1"
  [[ "$DRY_RUN" == "1" ]] && { printf '  %s manifest -> %s\n' "${C_YELLOW}[dry-run]${C_RESET}" "$file" >&2; return 0; }
  umask 077
  {
    printf 'MANIFEST_FORMAT=1\n'
    for key in "${MANIFEST_KEYS[@]}"; do
      [[ "$key" == "MANIFEST_FORMAT" ]] && continue
      printf '%s=%s\n' "$key" "${!key:-}"
    done
  } >"$file"
  chmod 600 "$file"
}

manifest_get() {
  # manifest_get FILE KEY -> 输出值（找不到为空）
  local file="$1" key="$2" line value
  [[ -f "$file" ]] || return 1
  line=$(grep -m1 "^${key}=" "$file" 2>/dev/null || true)
  [[ -n "$line" ]] || return 1
  value="${line#*=}"
  # 只允许安全字符，防止清单被篡改成注入
  if [[ -n "$value" && ! "$value" =~ ^[A-Za-z0-9._:/@+-]*$ ]]; then
    err "清单字段 $key 含非法字符，拒绝使用：$value"
    return 1
  fi
  printf '%s' "$value"
}

manifest_load() {
  local file="$1" key value
  [[ -f "$file" ]] || return 1
  for key in "${MANIFEST_KEYS[@]}"; do
    if value=$(manifest_get "$file" "$key"); then
      printf -v "$key" '%s' "$value"
      export "${key?}"
    fi
  done
  if [[ "${MANIFEST_FORMAT:-}" != "1" ]]; then
    # 兼容早期版本：没有格式号但 PREFIX 合法，仍允许卸载（否则会把人锁在门外）
    if [[ -n "${PREFIX:-}" ]]; then
      warn "清单缺少 MANIFEST_FORMAT，按兼容模式读取"
    else
      err "清单不可用（缺 MANIFEST_FORMAT 且无 PREFIX）"
      return 1
    fi
  fi
  return 0
}

# ---------- .env 读写（保留已有值） ----------
env_get() {
  local file="$1" key="$2"
  [[ -f "$file" ]] || return 1
  local line
  line=$(grep -m1 "^${key}=" "$file" 2>/dev/null || true)
  [[ -n "$line" ]] || return 1
  printf '%s' "${line#*=}"
}

env_set() {
  # env_set FILE KEY VALUE：存在则替换，不存在则追加（dry-run 时脱敏打印）
  local file="$1" key="$2" value="$3"
  if [[ "$DRY_RUN" == "1" ]]; then
    local shown="$value"
    case "$key" in
      *KEY*|*TOKEN*|*SECRET*|*PASSWORD*) shown="***" ;;
    esac
    printf '  %s env %s=%s\n' "${C_YELLOW}[dry-run]${C_RESET}" "$key" "$shown" >&2
    return 0
  fi
  touch "$file"
  if grep -q "^${key}=" "$file"; then
    local tmp
    tmp=$(mktemp)
    awk -v k="$key" -v v="$value" '
      BEGIN { FS="="; OFS="=" }
      $1 == k { print k "=" v; next }
      { print }
    ' "$file" >"$tmp"
    mv "$tmp" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >>"$file"
  fi
  chmod 600 "$file"
}

gen_secret() { openssl rand -hex "${1:-24}" 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

# ---------- 服务 / 容器 / 网络 ----------
systemd_reload() {
  run systemctl daemon-reload
}

service_exists() { systemctl list-unit-files "${1}.service" 2>/dev/null | grep -q "${1}.service"; }

docker_ready() {
  docker info >/dev/null 2>&1
}

compose_cmd() {
  # 统一入口：所有 compose 调用都带 project / env-file / file
  printf 'docker compose -p %s --env-file %s -f %s' \
    "$COMPOSE_PROJECT" "${PREFIX}/.env" "$COMPOSE_FILE"
}

wait_http() {
  local url="$1" timeout="${2:-120}" elapsed=0
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '  %s wait %s\n' "${C_YELLOW}[dry-run]${C_RESET}" "$url" >&2
    return 0
  fi
  while (( elapsed < timeout )); do
    if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
    if (( elapsed % 20 == 0 )); then log "  等待服务就绪… ${elapsed}s/${timeout}s"; fi
  done
  return 1
}

wait_tcp() {
  local host="$1" port="$2" timeout="${3:-60}" elapsed=0
  if [[ "$DRY_RUN" == "1" ]]; then return 0; fi
  while (( elapsed < timeout )); do
    if (exec 3<>"/dev/tcp/${host}/${port}") 2>/dev/null; then exec 3>&- 2>/dev/null || true; return 0; fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  return 1
}

port_in_use() {
  local port="$1"
  if command_exists ss; then
    ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}$"
  else
    (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null && { exec 3>&- 2>/dev/null || true; return 0; }
    return 1
  fi
}

# ---------- 交互输入 ----------
ask() {
  # ask KEY 提示 默认值
  local prompt="$1" default="${2:-}" answer
  if [[ ! -t 0 ]]; then
    printf '%s' "$default"
    return 0
  fi
  read -r -p "$prompt${default:+ [$default]}：" answer
  printf '%s' "${answer:-$default}"
}
