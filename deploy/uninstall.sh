#!/usr/bin/env bash
# =============================================================================
# deploy/uninstall.sh — 按「安装清单」精确回收，删除前可预演、删除后自检
#
# 两种模式：
#   默认（彻底卸载）：
#     systemd 单元 + nginx 站点 + 容器 + 网络 + 数据卷 + 代码目录 + 状态目录 + 安装时创建的用户
#   --keep-data（停服保留现场）：
#     只摘除 systemd / nginx，保留代码目录（含 .env）、data/ 与 PG/Redis 数据卷，可随时重装
#
# 安全：
#   • 只删除清单登记过的路径；路径必须通过 validate_install_path（拒 /、/usr、浅层目录）
#   • 默认交互确认（输入安装目录才继续）；--yes 跳过
#   • --dry-run 打印将要删除的每个路径与体积，不改系统
#   • 删除后逐项断言「已不存在」，并列出系统级残留与清理命令
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/lib.sh
source "$SCRIPT_DIR/lib.sh"

PREFIX=""
STATE_DIR="$DEFAULT_STATE_DIR"
USER=""
SERVICE_NAME="$DEFAULT_SERVICE"
NGINX_CONF=""
NGINX_LINK=""
COMPOSE_PROJECT="$DEFAULT_COMPOSE_PROJECT"
COMPOSE_FILE=""
PG_VOLUME=""
REDIS_VOLUME=""
CREATED_USER=0
KEEP_DATA=0
KEEP_USER=0
PURGE_IMAGES=0
FORCE=0
ASSUME_YES=0

usage() {
  cat <<'USAGE'
用法：sudo bash deploy/uninstall.sh [选项]

  --prefix DIR     安装目录（默认从清单读取）
  --state-dir DIR  清单目录（默认 /var/lib/companion）
  --keep-data      只摘除 systemd/nginx，保留代码、.env 与数据卷（可随时重装）
  --keep-user      不删除安装时创建的系统用户
  --purge-images   额外删除 pgvector/postgres 与 redis 基础镜像（若被其他项目共用请勿加）
  --force          清单缺失时，按 --prefix 强制卸载（危险，需自行确认路径）
  --dry-run        预演：列出将删除的路径与体积，不改系统
  --yes            跳过交互确认
  -h, --help       帮助
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --state-dir) STATE_DIR="$2"; shift 2 ;;
    --keep-data) KEEP_DATA=1; shift ;;
    --keep-user) KEEP_USER=1; shift ;;
    --purge-images) PURGE_IMAGES=1; shift ;;
    --force) FORCE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数：$1（--help 查看用法）" ;;
  esac
done

MANIFEST="$STATE_DIR/install-manifest.env"
LOG_FILE="${STATE_DIR}/uninstall.log"
# dry-run 不落任何文件（包括日志），保证「预演零副作用」
[[ "$DRY_RUN" == "1" ]] && LOG_FILE=""

section "1/8 读取安装清单"
if [[ -f "$MANIFEST" ]]; then
  manifest_load "$MANIFEST" || die "清单解析失败：$MANIFEST"
  ok "已读取清单：$MANIFEST"
elif [[ "$FORCE" == "1" && -n "$PREFIX" ]]; then
  warn "清单缺失：按 --force 使用默认资源名卸载（$PREFIX）"
  SERVICE_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
  COMPOSE_FILE="$PREFIX/deploy/docker-compose.infra.yml"
  PG_VOLUME="${COMPOSE_PROJECT}_pgdata"
  REDIS_VOLUME="${COMPOSE_PROJECT}_redisdata"
  CREATED_USER=1
else
  die "找不到安装清单（$MANIFEST）。若确认是手工安装，请用 --force --prefix <目录> 并自行核对路径"
fi

[[ -n "$PREFIX" ]] || die "清单里没有 PREFIX，拒绝继续"
validate_install_path "$PREFIX" || die "清单里的 PREFIX 不安全：$PREFIX"
validate_install_path "$STATE_DIR" || die "清单里的 STATE_DIR 不安全：$STATE_DIR"
[[ -n "$SERVICE_UNIT" ]] || SERVICE_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ "$DRY_RUN" != "1" ]]; then
  require_root
fi

# ---------- 计划 ----------
section "2/8 卸载计划"
if [[ "$KEEP_DATA" == "1" ]]; then
  warn "模式：保留现场（--keep-data）——代码、.env、data/、数据卷都保留，只摘除服务入口"
else
  warn "模式：彻底卸载 —— 服务、代码、数据卷、安装时创建的用户都会被删除"
fi

PLAN_ITEMS=()
[[ -e "$SERVICE_UNIT" ]] && PLAN_ITEMS+=("systemd 单元 $SERVICE_UNIT")
[[ -n "$NGINX_CONF" && -e "$NGINX_CONF" ]] && PLAN_ITEMS+=("nginx 站点 $NGINX_CONF")
[[ -n "$NGINX_LINK" && ( -e "$NGINX_LINK" || -L "$NGINX_LINK" ) ]] && PLAN_ITEMS+=("nginx 软链 $NGINX_LINK")
if [[ "$KEEP_DATA" == "0" ]]; then
  [[ -d "$PREFIX" ]] && PLAN_ITEMS+=("代码目录 $PREFIX（$(du -sh "$PREFIX" 2>/dev/null | awk '{print $1}')）")
  [[ -d "$STATE_DIR" ]] && PLAN_ITEMS+=("状态目录 $STATE_DIR（$(du -sh "$STATE_DIR" 2>/dev/null | awk '{print $1}')）")
fi
PLAN_ITEMS+=("docker compose 项目 $COMPOSE_PROJECT（容器 + 网络）")
if [[ "$KEEP_DATA" == "0" ]]; then
  PLAN_ITEMS+=("数据卷 $PG_VOLUME / $REDIS_VOLUME")
fi
if [[ "$CREATED_USER" == "1" && "$KEEP_USER" == "0" && "$KEEP_DATA" == "0" ]]; then
  PLAN_ITEMS+=("系统用户 $USER")
fi

for item in "${PLAN_ITEMS[@]}"; do
  printf '  - %s\n' "$item" >&2
done
printf '\n' >&2

if [[ "$DRY_RUN" == "1" ]]; then
  warn "dry-run：不执行删除。下面打印实际会执行的命令与命中文件。"
fi

if [[ "$DRY_RUN" != "1" ]]; then
  if [[ "$ASSUME_YES" != "1" ]]; then
    if [[ ! -t 0 ]]; then
      die "非交互环境需要 --yes 才会执行卸载"
    fi
    printf '确认删除以上内容，请输入安装目录（%s）以继续：' "$PREFIX" >&2
    read -r answer
    [[ "$answer" == "$PREFIX" ]] || die "输入不匹配，已取消"
  fi
fi

# ---------- 服务 ----------
section "3/8 停止并摘除 systemd"
if systemctl list-unit-files "${SERVICE_NAME}.service" >/dev/null 2>&1 || [[ -e "$SERVICE_UNIT" ]]; then
  run systemctl stop "$SERVICE_NAME" || warn "停止服务失败（可能已停止）"
  run systemctl disable "$SERVICE_NAME" || warn "禁用服务失败（可能已禁用）"
fi
[[ -e "$SERVICE_UNIT" ]] && safe_remove "$SERVICE_UNIT"
if [[ "$DRY_RUN" != "1" ]]; then
  systemctl daemon-reload || true
  systemctl reset-failed "$SERVICE_NAME" 2>/dev/null || true
fi

# ---------- nginx ----------
section "4/8 摘除 nginx 站点"
if [[ -n "$NGINX_LINK" && ( -e "$NGINX_LINK" || -L "$NGINX_LINK" ) ]]; then
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '  %s rm -f %s\n' "${C_YELLOW}[dry-run]${C_RESET}" "$NGINX_LINK" >&2
  else
    rm -f -- "$NGINX_LINK"
  fi
fi
[[ -n "$NGINX_CONF" && -e "$NGINX_CONF" ]] && safe_remove "$NGINX_CONF"
if command_exists nginx; then
  if [[ "$DRY_RUN" != "1" ]]; then
    nginx -t >/dev/null 2>&1 && systemctl reload nginx || warn "nginx 重载失败（请手动 systemctl reload nginx）"
  fi
fi

# ---------- 容器与卷 ----------
section "5/8 回收容器 / 网络 / 数据卷"
COMPOSE_ARGS=(-p "$COMPOSE_PROJECT")
[[ -n "$COMPOSE_FILE" && -f "$COMPOSE_FILE" ]] && COMPOSE_ARGS+=(-f "$COMPOSE_FILE")
if [[ -f "$PREFIX/.env" ]]; then COMPOSE_ARGS+=(--env-file "$PREFIX/.env"); fi

if command_exists docker && docker_ready; then
  if [[ -f "$COMPOSE_FILE" ]]; then
    DOWN_FLAGS=(--remove-orphans)
    [[ "$KEEP_DATA" == "0" ]] && DOWN_FLAGS+=(--volumes)
    if [[ "$DRY_RUN" == "1" ]]; then
      printf '  %s docker compose %s down %s\n' "${C_YELLOW}[dry-run]${C_RESET}" "${COMPOSE_ARGS[*]}" "${DOWN_FLAGS[*]}" >&2
    else
      docker compose "${COMPOSE_ARGS[@]}" down "${DOWN_FLAGS[@]}" || warn "compose down 返回非 0（可能容器已不存在）"
    fi
  else
    # 组合文件已丢：按 compose 标签清理，仍限本项目的资源
    containers=$(docker ps -aq --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" 2>/dev/null || true)
    if [[ -n "$containers" ]]; then
      # shellcheck disable=SC2086
      run docker rm -f $containers
    fi
    network="${COMPOSE_PROJECT}_default"
    docker network inspect "$network" >/dev/null 2>&1 && run docker network rm "$network" || true
    if [[ "$KEEP_DATA" == "0" ]]; then
      for vol in "$PG_VOLUME" "$REDIS_VOLUME"; do
        if docker volume inspect "$vol" >/dev/null 2>&1; then run docker volume rm "$vol"; fi
      done
    fi
  fi
  if [[ "$PURGE_IMAGES" == "1" ]]; then
    warn "--purge-images：删除基础镜像（若其他项目在用会一并受影响）"
    for img in "pgvector/pgvector:pg16" "redis:7-alpine"; do
      docker image inspect "$img" >/dev/null 2>&1 && run docker image rm "$img" || true
    done
  fi
else
  warn "docker 不可用或守护进程未运行：跳过容器/卷回收（若曾有容器，请手动 docker compose -p $COMPOSE_PROJECT down -v）"
fi

# ---------- 目录 ----------
section "6/8 删除目录"
if [[ "$KEEP_DATA" == "1" ]]; then
  info "--keep-data：保留 $PREFIX 与 $STATE_DIR"
else
  safe_remove "$PREFIX"
  safe_remove "$STATE_DIR"
  # 目录已删：清空日志句柄，避免后续日志把 STATE_DIR 又建回来
  LOG_FILE=""
fi

# ---------- 用户 ----------
section "7/8 删除系统用户"
if [[ "$CREATED_USER" == "1" && "$KEEP_USER" == "0" && "$KEEP_DATA" == "0" ]]; then
  if id "$USER" >/dev/null 2>&1; then
    run userdel "$USER" 2>/dev/null || warn "userdel 失败（可能有进程占用该用户）"
    run groupdel "$USER" 2>/dev/null || true
  fi
else
  info "保留用户 $USER（--keep-user / --keep-data / 非脚本创建）"
fi

# ---------- 自检 ----------
section "8/8 卸载自检"
if [[ "$DRY_RUN" == "1" ]]; then
  warn "dry-run：跳过删除后自检（上面的 [dry-run] 行就是实际会执行的命令）"
  ok "预演完成，未做任何修改"
  exit 0
fi

FAIL=0
check_absent() {
  local label="$1" path="$2"
  if [[ -e "$path" || -L "$path" ]]; then
    err "$label 仍存在：$path"; FAIL=1
  else
    ok "$label 已清除"
  fi
}
check_absent "systemd 单元" "$SERVICE_UNIT"
[[ -n "$NGINX_CONF" ]] && check_absent "nginx 站点" "$NGINX_CONF"
[[ -n "$NGINX_LINK" ]] && check_absent "nginx 软链" "$NGINX_LINK"
if [[ "$KEEP_DATA" == "0" ]]; then
  check_absent "代码目录" "$PREFIX"
  check_absent "状态目录" "$STATE_DIR"
fi

if command_exists docker && docker_ready; then
  left=$(docker ps -aq --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" 2>/dev/null | wc -l | tr -d ' ')
  [[ "$left" == "0" ]] && ok "容器已清除" || { err "仍有 $left 个容器残留"; FAIL=1; }
  if [[ "$KEEP_DATA" == "0" ]]; then
    for vol in "$PG_VOLUME" "$REDIS_VOLUME"; do
      if docker volume inspect "$vol" >/dev/null 2>&1; then err "数据卷仍存在：$vol"; FAIL=1; else ok "数据卷已清除：$vol"; fi
    done
  fi
fi

if [[ "$CREATED_USER" == "1" && "$KEEP_USER" == "0" && "$KEEP_DATA" == "0" ]]; then
  if id "$USER" >/dev/null 2>&1; then err "系统用户仍存在：$USER"; FAIL=1; else ok "系统用户已删除：$USER"; fi
fi

cat >&2 <<'RESIDUAL'

—— 脚本不负责的「系统级残留」（按需清理，不影响本项目干净）——
  pip 缓存        rm -rf /root/.cache/pip
  npm 缓存        rm -rf /root/.npm
  docker 基础镜像  docker images | grep -E 'pgvector/pgvector|redis:7-alpine'
  journal 日志     journalctl -u companion --rotate && journalctl --vacuum-time=1d
RESIDUAL

if [[ "$FAIL" == "0" ]]; then
  if [[ "$KEEP_DATA" == "1" ]]; then
    ok "已停服并摘除入口，数据保留：$PREFIX（重装见 deploy/install.sh --upgrade / --dry-run）"
  else
    ok "卸载完成：本项目相关的服务、目录、容器、网络、数据卷均已清除"
  fi
else
  err "存在残留项，请按上面的提示手动处理"
  exit 1
fi
