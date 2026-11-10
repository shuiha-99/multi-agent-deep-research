# 本项目独立 Redis 向量服务

2026-10-31 使用本机已有的 Ubuntu WSL2，启动了只供本项目使用的 Redis 8.10.3 + Redis Search，Windows 连接地址为 `127.0.0.1:6380`。原有 Windows Redis 5 的 6379 服务及其数据保持原状。

没有全局安装 Redis、修改 Ubuntu 软件源、注册 Windows 服务或设置开机启动。Linux 官方软件包仅解包到项目的 `.cache/vendor/redis-8.10.3-linux/`。已验证的环境为 Ubuntu 26.04.1 x86_64；其他环境需要满足软件包运行库要求。

## 启动与停止

在项目根目录执行：

```powershell
# 第一次准备二进制；下载包有固定 SHA256 校验，已准备时可重复执行。
powershell -ExecutionPolicy Bypass -File scripts/redis-setup.ps1

# 后续启动
powershell -ExecutionPolicy Bypass -File scripts/redis-start.ps1

# 停止本项目实例并保存数据
powershell -ExecutionPolicy Bypass -File scripts/redis-stop.ps1
```

脚本默认使用已有的 `Ubuntu` 发行版，可用 `-Distribution <名称>` 指定其他已准备好的发行版。脚本不会自行安装或初始化 WSL 发行版。

`redis-local.sh` 只会停止能够通过密码认证、且 `INFO server` 中配置路径与本项目 `data/redis8/redis.conf` 完全一致的实例。不同服务占用 6380 时拒绝停止，不能通过这些脚本停止原 6379 服务。

## 配置与文件

- 二进制与 Search 模块：`.cache/vendor/redis-8.10.3-linux/`。
- 持久化文件、配置、日志、PID：`data/redis8/`。
- 本项目随机密码：`data/redis8/password.txt`，仅本地使用，不提交、不在日志或聊天中输出。
- `.env` 的 `RESEARCH_REDIS_URL` 使用 `redis://:<密码>@127.0.0.1:6380/0`；由项目配置步骤写入，不要把占位文字当作真实密码。

实例只绑定 WSL 的 `127.0.0.1`，通过 WSL 本机端口转发供 Windows 连接，启用密码认证、AOF、每秒同步；最大内存 256MB，使用 `noeviction`，避免淘汰任务租约。Redis 主要用于运行时投影和工具缓存，业务任务及图 checkpoint 的恢复依据仍是 SQLite。

不要删除 `data/redis8/` 来解决连接问题；密码丢失或数据目录变化需要明确对账。普通停止不会删除数据。`.cache/vendor/` 丢失可重新运行 setup；已有数据目录和密码会保留。

## 来源与校验

来源是 [Redis 官方 Ubuntu 软件包仓库](https://packages.redis.io/deb/) 的 noble amd64 包；固定版本 `6:8.10.3-1rl1~noble1`。校验值来自该仓库的 [Packages.gz 索引](https://packages.redis.io/deb/dists/noble/main/binary-amd64/Packages.gz)，下载后已逐项计算 SHA256 验证：

| 软件包 | SHA256 |
| --- | --- |
| `redis-server_8.10.3-1rl1~noble1_amd64.deb` | `2f531d1d8d16ee592a6fe9f18ab8d9f0565440de6b6e1a3be764f0ef876dd57d` |
| `redis-tools_8.10.3-1rl1~noble1_amd64.deb` | `5b228c5f384cf3987010fd225d9f1cb5d2e15184e31d356030bf5532b74b1f85` |

官方 server 包已包含 `redisearch.so`。本项目只加载这个所需模块，不把 Redis Vector Sets 误当作 `FT.SEARCH` 的替代实现。没有使用第三方 Windows Redis 构建来完成验收。

## 已实际验证

Windows 端使用新实例随机密码完成 AUTH/PING，并确认 `redis_version=8.10.3`；创建临时 HASH/FLAT/FLOAT32/COSINE 索引后写入两个不同向量，KNN 查询返回预期邻居，相同向量的余弦距离为 0。测试结束后只删除本次临时索引和其两个测试记录。

不含凭据的原始验证记录位于 `.cache/vendor/redis-runtime-validation.json`。这项验证证明真实 Redis 向量命令可用，完整研究和缓存效果另见主验收记录。
