# 服务器部署与卸载

> 目标：**一条命令装完，一条命令删干净**。
> 装：`sudo bash deploy/install.sh`；卸：`sudo bash deploy/uninstall.sh`（先 `--dry-run` 预演）。
> 所有创建物都记进清单，卸载只删清单里的东西，删完还会逐项自检并打印系统级残留。

---

## 1. 前置条件

| 项 | 要求 | 检查 |
|---|---|---|
| 系统 | Ubuntu 22.04+ / Debian 12+（其他发行版需自行确认 systemd 路径） | `cat /etc/os-release` |
| 权限 | root（或 sudo） | `id -u` = 0 |
| Python | ≥ 3.10 | `python3 -V` |
| Node | ≥ 18（仅构建前端；可用 `--no-frontend` 跳过） | `node -v` |
| Docker | 需要 `docker` + `docker compose` v2（`--no-docker` 可跳过） | `docker compose version` |
| 端口 | 8000（后端）、5432/6379（仅绑 127.0.0.1） | `ss -ltnp` |
| 磁盘 | ≥ 10 GB（默认 CUDA 版 torch 较大）；加 `--torch-cpu` 可省 2 GB+，`--skip-embed` 再降到 ~1 GB | `df -h` |
|
| 内存 | ≥ 2 GB（bge-small 常驻约 300MB） | `free -h` |

首次安装会下载 Python 依赖（含 torch，5~15 分钟）与 bge-small-zh 模型（约 95MB，首次启动时下载）。

---

## 2. 安装

```bash
# 建议先预览：会打印每一步要做什么，不改系统
sudo bash deploy/install.sh --dry-run --domain chat.example.com

# 正式安装（LLM key 二选一：命令行传，或 export LLM_API_KEY）
sudo LLM_API_KEY=sk-xxxx bash deploy/install.sh --domain chat.example.com
```

常用组合：

```bash
# 只给内网/本机用，不装 nginx
sudo bash deploy/install.sh --llm-key sk-xxx

# 已有 PG/Redis，不启动内置容器
sudo DATABASE_URL=postgresql+asyncpg://u:p@10.0.0.5:5432/companion \
     REDIS_URL=redis://10.0.0.6:6379/0 \
     sudo -E bash deploy/install.sh --no-docker --llm-key sk-xxx

# 先跑通联调（hash 嵌入 + dev 模式，不具备生产语义）
sudo bash deploy/install.sh --skip-embed --llm-key sk-xxx

# 磁盘紧张 / 不需要 GPU：只装 CPU 版 torch（省 2GB+）
sudo bash deploy/install.sh --llm-key sk-xxx --torch-cpu
```

参数全表：`bash deploy/install.sh --help`。

### 安装做了什么

| # | 动作 | 位置 |
|---|---|---|
| 1 | 校验 Python/Docker/端口/路径（拒绝 `/`、`/usr`、浅层目录） | — |
| 2 | 创建 system 用户（不存在时） | `companion` |
| 3 | 拷贝代码（排除 `.git/.venv/node_modules/dist/data/.env`） | `/opt/companion` |
| 4 | 生成 `.env`（JWT/ADMIN/PG 密码自动生成；旧值保留并备份） | `/opt/companion/.env`（640 root:group） |
| 5 | 建 venv 装依赖（含 embed 扩展） | `/opt/companion/.venv` |
| 6 | 构建前端并清理 `node_modules` | `/opt/companion/frontend/dist` |
| 7 | 拉镜像并起 PG(pgvector) + Redis（拉取失败重试 3 次），端口只绑 127.0.0.1 | 容器 `companion-postgres-1` / `companion-redis-1` |
| 8 | 写 systemd 单元（加固、Failing 自动重启） | `/etc/systemd/system/companion.service` |
| 9 | 可选：写 nginx 站点并 reload | `/etc/nginx/sites-available/companion.conf` |
| 10 | 健康检查 + 写安装清单 | `/var/lib/companion/install-manifest.env` |
> **顺序原则**：先下载完所有依赖（Python、npm、Docker 镜像），再启动容器与服务；任何下载失败都停在动系统之前。国内网络拉不动镜像时加 `--registry-mirror https://docker.m.daocloud.io`。


**资源命名是确定的**（便于审计与彻底回收）：

```
容器   companion-postgres-1 / companion-redis-1
网络   companion_default
卷     companion_pgdata / companion_redisdata
端口   127.0.0.1:8000（后端） 127.0.0.1:5432（PG） 127.0.0.1:6379（Redis）
```

---

## 3. 安装后验证

```bash
sudo bash deploy/status.sh            # 一屏看全：服务/健康/容器/卷/磁盘/最近日志

curl -s http://127.0.0.1:8000/api/v1/health   # 期望 status=ok, checkpoint.backend=postgres
systemctl is-active companion                   # active
ss -ltnp | grep -E ':(8000|5432|6379)'         # 都只在 127.0.0.1
docker compose -p companion --env-file /opt/companion/.env \
  -f /opt/companion/deploy/docker-compose.infra.yml ps
```

> 首次启动可能要等 bge 模型下载完成，健康检查在安装脚本里最多等 300s。
> 如果 `checkpoint.backend=sqlite` 而不是 `postgres`，说明 PG 连接失败：看
> `journalctl -u companion -n 80` 与 `docker compose -p companion logs postgres`。

---

## 4. 升级

```bash
cd /path/to/repo && git pull
sudo bash deploy/install.sh --upgrade
```

升级会：沿用清单里的路径/端口/域名 → 备份旧 `.env` → 覆盖代码（保留 `data/` 与数据库）→ 重装依赖 → 重建前端 → 重启 → 健康检查。
**不会**重建用户、不会动数据卷。

---

## 5. 卸载

### 5.1 先预演（推荐每次都做）

```bash
sudo bash deploy/uninstall.sh --dry-run
```

输出每一项将被删除的资源与体积，**不做任何修改**。

### 5.2 两种模式

```bash
# ① 彻底卸载（默认）：服务 + 代码 + 容器 + 网络 + 数据卷 + 安装时创建的用户
sudo bash deploy/uninstall.sh --yes

# ② 保留现场（停服但不丢数据）：只摘 systemd/nginx，保留代码、.env、data、数据卷
sudo bash deploy/uninstall.sh --keep-data --yes
```

交互式执行会让输入安装目录（`/opt/companion`）二次确认；`--yes` 用于自动化。

其它开关：

| 开关 | 作用 |
|---|---|
| `--keep-user` | 保留 `companion` 系统用户（默认删除，仅当是脚本创建时） |
| `--purge-images` | 额外删除 `pgvector/pgvector:pg16`、`redis:7-alpine`（**若被其他项目共用勿加**） |
| `--force --prefix DIR` | 清单丢失时按默认命名强制卸载（危险，需自行核对路径） |

### 5.3 卸载后自检

脚本最后会对每一项断言「已不存在」，输出类似：

```
✓ systemd 单元已清除
✓ nginx 站点已清除
✓ 容器已清除
✓ 数据卷已清除：companion_pgdata
✓ 数据卷已清除：companion_redisdata
✓ 代码目录已清除
✓ 状态目录已清除
✓ 系统用户已删除：companion
```

任何一项仍存在 → 脚本以非 0 退出并指出残留路径。

---

## 6. 「干净」的边界

脚本负责并自检的：

- systemd 单元与 enable 软链、nginx 站点与软链
- compose 容器、网络 `companion_default`、卷 `companion_pgdata`/`companion_redisdata`
- `/opt/companion`、`/var/lib/companion`
- 脚本创建的系统用户与同名用户组

脚本**不**负责（系统级、可能与其他项目共享），但会打印清理命令：

```bash
rm -rf /root/.cache/pip           # pip 缓存
rm -rf /root/.npm                 # npm 缓存
journalctl -u companion --rotate && journalctl --vacuum-time=1d
docker image rm pgvector/pgvector:pg16 redis:7-alpine   # 基础镜像（共用时别删）
apt remove --purge docker.io nginx ...                  # 你自装的系统包，按需
```

判断是否真的干净（全部应为空/不存在）：

```bash
systemctl status companion 2>&1 | head -1
ls -d /opt/companion /var/lib/companion 2>&1
docker ps -a --filter label=com.docker.compose.project=companion
docker volume ls --filter name=companion_
id companion
ls /etc/nginx/sites-enabled/companion.conf /etc/systemd/system/companion.service 2>&1
```

---

## 7. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
|
| 装依赖时 `[Errno 28] No space left on device` | 磁盘被 pip 缓存 / CUDA 版 torch 写满 | `df -h`；清 `~/.cache/pip`、`docker system prune -af`、`journalctl --vacuum-size=200M`；重跑加 `--torch-cpu` 或 `--skip-embed` |
| 安装报「端口 8000 已被占用」 | 已有服务占用 | `ss -ltnp \| grep 8000`，换 `--port` 或先停旧服务 |
| 启动即 `PermissionError ... load_cert_chain` | asyncpg 0.32 会探测 `~/.postgresql/postgresql.crt`，服务用户 home（`/var/lib/companion`）不可进入时抛 EACCES | 新版本已自动 `chown root:companion /var/lib/companion` 并给本机库 `ssl=False`；老安装手动执行一次 chown |
| `Application startup failed` + `表情包数据集缺失` | `app/mcp/data/memes.json` 缺失（旧 `.gitignore` 的 `data/` 把它吞了） | `git pull` 后重装；新版本缺文件只降级，不再拖垮启动 |
| `restart counter is at N` 一直涨 | 启动失败在重启循环 | `journalctl -u companion -n 60` 看最后一次 Traceback 末尾 |
| `docker 守护进程不可用` | Docker 未启动 | `systemctl enable --now docker` |
| 健康检查 300s 超时 | 首次下载 bge 模型慢 / PG 未就绪 | 看 `journalctl -u companion -n 80`；或先 `--skip-embed` 联调 |
| `password authentication failed` | 旧卷里的密码与 `.env` 不一致 | 更新安装：`.env` 的 `PG_PASSWORD` 与卷内一致；或卸载重装（会删数据） |
| `GET /` 返回 JSON 而不是页面 | `frontend/dist` 缺失 | `npm run build` 后重跑 `--upgrade` |
| nginx 502 | 后端未启动 / 端口不符 | `systemctl status companion`；核对 nginx 里的 `__PORT__` 渲染值 |
| 改完 `--domain` 不生效 | 升级模式沿用清单参数 | 先 `uninstall --keep-data`，再用新域名安装 |
| 卸载后仍有容器 | 曾用旧版脚本（无清单）安装 | `docker compose -p aigf down -v` / `-p mealplan down -v`；或 `uninstall --force --prefix <旧目录>` |

---

## 8. 从旧版脚本迁移

仓库早期的 `deploy/start.sh|stop.sh|redeploy.sh|common.sh|aigf.service` 已删除（它们没有安装清单、卸载无法保证干净，且服务名/资源名与新版本不一致）。

若服务器上装过旧版：

```bash
sudo systemctl disable --now aigf 2>/dev/null || true
sudo rm -f /etc/systemd/system/aigf.service && sudo systemctl daemon-reload
docker compose -p aigf down -v            # 旧项目名是 aigf
# 旧目录按实际安装位置删除（装前请先确认路径，不要照抄）
# 然后用新版：sudo bash deploy/install.sh --domain <域名> --llm-key <key>
```

> **命名变更（v2.1）**：安装目录 / 系统用户 / systemd 服务 / compose 项目统一为 `companion`。
> 如果按更早的命名装过（`/opt/mealplan`、`mealplan.service`、compose 项目 `mealplan`），
> 先用当时那份脚本 `uninstall --yes`，或手动 `systemctl disable --now mealplan`、
> `docker compose -p mealplan down -v`、`rm -rf /opt/mealplan /var/lib/mealplan`，再装新版。

---

## 9. 安全默认值

- 后端与数据库端口**只监听 127.0.0.1**，公网访问必须经 nginx（`--domain` 自动配置）；
- systemd：`ProtectSystem=strict`、`ProtectHome=true`、`NoNewPrivileges`，只开放 `data/` 与状态目录可写；
- `.env` 640 `root:group`，密钥不进代码、不进镜像；
- 运维接口需 `X-Admin-Token`（安装时自动生成，`sudo grep ADMIN_TOKEN /opt/companion/.env`）；
- 卸载默认删除数据卷并二次确认（输入安装目录），预演模式零副作用；
- `--registry-mirror` 写入的 `/etc/docker/daemon.json` 属于机器级设置，**卸载不回收**（会影响同机其他容器），需要时手工删。
---

## 10. 接入已有 nginx（服务器上已经有站点）

安装脚本只写自己的一份站点文件；机器上已经有 nginx 跑别的站点时，先判断用哪种接法。

### 10.1 先看你的 nginx 用哪个目录

| 系统 | 站点目录 | 主配置里的 include |
|---|---|---|
| Debian / Ubuntu | `/etc/nginx/sites-enabled/` | `include /etc/nginx/sites-enabled/*;` |
| RHEL / CentOS / Alma / Rocky / openSUSE | `/etc/nginx/conf.d/` | `include /etc/nginx/conf.d/*.conf;` |
| Alpine | `/etc/nginx/http.d/` | `include /etc/nginx/http.d/*.conf;` |

```bash
nginx -T 2>/dev/null | grep -E 'include .*(sites-enabled|conf\.d|http\.d)'
ls -l /etc/nginx/sites-enabled /etc/nginx/conf.d 2>/dev/null
```

只有 Debian/Ubuntu 的 `sites-enabled` 软链会被自动加载；RHEL 系没有这个 include，
`--domain` 写进 `sites-available` 的软链不会生效。

### 10.2 推荐：`--no-nginx`，站点文件自己管

```bash
sudo LLM_API_KEY=sk-xxx bash deploy/install.sh --no-nginx
```

把下面的 server 块存成 `/etc/nginx/sites-available/companion.conf`
（RHEL 系放 `/etc/nginx/conf.d/companion.conf`），按你的发行版建软链或直接 reload。

```nginx
# 80：只做 ACME 续期和跳转（不用 certbot 可整段删）
server {
    listen 80;
    listen [::]:80;
    server_name chat.example.com;

    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    # nginx < 1.25.1 用 listen 443 ssl http2;，>= 1.25.1 用下面这行
    http2 on;
    server_name chat.example.com;

    ssl_certificate     /etc/letsencrypt/live/chat.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/chat.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    client_max_body_size 4m;
    access_log /var/log/nginx/companion.access.log;
    error_log  /var/log/nginx/companion.error.log warn;

    root /opt/companion/frontend/dist;
    index index.html;

    location / { try_files $uri $uri/ /index.html; }
    location /assets/ {
        expires 7d;
        add_header Cache-Control "public, immutable";
        try_files $uri =404;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE（POST /api/v1/chat/stream）：关缓冲、关 gzip、给足超时，
        # 否则流式回复会被 nginx 攒住，看起来像“卡住不说话”
        proxy_buffering off;
        proxy_cache off;
        gzip off;
        proxy_connect_timeout 5s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }

    location = /api/v1/health {
        access_log off;
        proxy_pass http://127.0.0.1:8000;
    }
}
```

后端只监听 `127.0.0.1:8000`，nginx 在本机反代；`X-Forwarded-For` 必须传（登录/对话限流按真实 IP），
`.env` 里保持 `TRUST_PROXY_HEADERS=true`（`--domain` 安装会自动写，手工装要自己加）。

### 10.3 让脚本托管（仅当你的 nginx 是 sites-enabled 结构）

```bash
sudo LLM_API_KEY=sk-xxx bash deploy/install.sh --domain chat.example.com
```

脚本会写 `/etc/nginx/sites-available/companion.conf`、软链到 `sites-enabled`、`nginx -t` 后 reload。
**升级时会按模板重写这个文件**，所以：

- 不要在脚本生成的文件里手改 TLS 或加 location——升级会丢；
- 要 HTTPS：另建 `companion-ssl.conf`（443 + 上面的 location 片段），或在升级后重跑
  `certbot --nginx -d chat.example.com`；
- 卸载只删脚本自己那份文件与软链，不碰其他站点。

### 10.4 证书：复用已有的 certbot

```bash
# 已有 certbot：签发并让插件改 nginx（注意 10.3 的重写问题）
sudo certbot --nginx -d chat.example.com

# 或者只签证书，自己维护 443 块
sudo certbot certonly --webroot -w /var/www/html -d chat.example.com
```

证书默认在 `/etc/letsencrypt/live/<域名>/`，续期由 `certbot.timer` 负责。

### 10.5 冲突与排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 域名打开是别的站点 | 别的块是 `default_server` 或 server_name 通配 | 本块 `server_name` 精确匹配；`nginx -T \| grep -n default_server` |
| `nginx -t` 报 duplicate server | 端口 + server_name 重复 | 删旧同名站点，或改 `--port` 重建 |
| 502 | 后端没起 / 端口不一致 | `systemctl status companion`；核对 `proxy_pass` 与 `.env` 的 `PORT` |
| 504 / 首字很慢 | SSE 被缓冲、超时太短 | 确认 `proxy_buffering off; gzip off; proxy_read_timeout 300s;` |
| 登录限流误伤 | 没传 X-Forwarded-For | 加 `proxy_set_header X-Forwarded-For ...`，并设 `TRUST_PROXY_HEADERS=true` |
| RHEL 系 502 且日志 permission denied | SELinux 阻止 nginx 连本机端口 | `sudo setsebool -P httpd_can_network_connect 1` |
| nginx 在容器里 | 127.0.0.1 是容器自己 | 用宿主机网关反代，并让 uvicorn 绑到该网卡（改 systemd 的 `--host`） |
| 想挂在 `/companion/` 子路径 | 前端 base 与 API 前缀都要改 | 不推荐，用独立子域更省事 |

### 10.6 上线校验

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -I https://chat.example.com

# SSE 必须是逐帧吐出，不是一次性返回
curl -sN -X POST https://chat.example.com/api/v1/chat/stream \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"user_input":"在吗"}' | head -c 300
```
---

## 11. 用户体系升级（仅旧安装需要跑一次）

新版本把 `users` 改成 **BIGINT 主键**，并新增 `user_auth`（refresh token）与 `user_profile`（资料）。
旧库里 `users.id` 是 `VARCHAR(64)`，`sessions/turns/memories.user_id` 也是字符串，**必须先清空重建**（按约定，旧数据只是测试数据）。

```bash
# 0) 备份（强烈建议）
docker exec companion-postgres-1 pg_dump -U companion companion > backup-$(date +%F).sql

# 1) 清掉旧表（脚本见 deploy/migrations/001_reset_users.sql）
docker exec -i companion-postgres-1 psql -U companion -d companion \
  < deploy/migrations/001_reset_users.sql

# 2) 重启：应用启动时会 create_all 建新表结构
sudo systemctl restart companion

# 3) 验证（应看到 users / user_auth / user_profile 三张新表）
docker exec -i companion-postgres-1 psql -U companion -d companion -c '\dt'
```

升级后：

- 旧 JWT 全部失效（`sub` 从字符串改成 BIGINT 字符串形式），所有人需要重新登录；
- 密码哈希改为 **argon2id**；旧 PBKDF2 用户若还在，登录成功时会自动升级；
- 头像写入 `MEDIA_DIR/avatars/{user_id}.webp`，通过 `/media/...` 提供，随安装目录一起清理。
---

## 12. 角色卡功能（新表 + 会话绑定）

```bash
# 1) 补 sessions 的新列（新表由应用 create_all 自动建）
docker exec -i companion-postgres-1 psql -U companion -d companion \
  < deploy/migrations/002_characters.sql

# 2) 重启
sudo systemctl restart companion

# 3) 验证
docker exec -i companion-postgres-1 psql -U companion -d companion -c '\dt' | grep -E 'character|sessions'
```

- 导入：`POST /api/v1/characters/import`（multipart `file`，PNG 或 JSON，≤10MB / 2MB）
- 导出：`/characters/{id}/export.json`、`/export.png`（仅 `creator_id` 本人）
- 原始 PNG 走 `STORAGE_BACKEND`（默认本地 `data/media/characters/...`；配 `STORAGE_BACKEND=s3` + `S3_*` 切对象存储）
- 会话锁版本：`sessions.character_version_id` 固定；需要跟进新版时显式调 `/sessions/{id}/sync-character`
