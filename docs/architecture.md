# 架构

## 1. 数据流

```
                          ┌──────────────────────────────┐
  玩家 ──TCP:7777──▶      │  TerrariaServer.exe           │
                          │  （原版 + IL 插桩，mono/FNA）  │
                          └──────────────┬───────────────┘
                                         │ stdout
                                         ▼
                              systemd journal（journald）
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              ▼                          ▼                          ▼
   ┌────────────────────┐    ┌────────────────────┐    ┌────────────────────┐
   │ terraria-ai (扣哒)  │    │ terraria-ai-tt(小T) │    │ terraria-stats      │
   │ 轮询日志→分类→决策   │    │ 同左，配置不同       │    │ 日志缓存→HTTP 看板   │
   └─────────┬──────────┘    └─────────┬──────────┘    └────────────────────┘
             │  say <名字> <内容>        │
             └────────────┬────────────┘
                          ▼
                  console.fifo（命名管道）
                          │ 被 start.sh 的 tail -f 读取，作为服务端 stdin
                          ▼
                  服务端执行 say → 全服广播（按名字着色）
```

**为什么所有组件都读日志而不是互相 RPC？**
原版服务端不提供任何事件 API。IL 补丁把事件写进 stdout 后，journald 天然成为唯一、可靠、可回放的事件总线——AI 与看板各自独立消费，互不依赖，单个组件重启不影响其他组件。

## 2. 服务清单

| systemd 服务 | 运行用户 | 作用 | 说明 |
|---|---|---|---|
| `terraria` | terraria | 游戏服务端 | 经 `start.sh` 启动（FIFO 保持 stdin 常开） |
| `terraria-ai` | ubuntu | 扣哒 | 日常闲聊 + 昼夜播报 |
| `terraria-ai-tt` | ubuntu | 小T | 世界级事件播报，不参与闲聊 |
| `terraria-stats` | ubuntu | 看板后端 | HTTP :19980，同时提供 `/stats` 给 AI 查世界天数 |
| `terraria-monitor` | ubuntu | 健康采样 | 周期性采样写日志，供 `bwcheck.sh` 分析 |
| `terraria-ai-daily-restart.timer` | — | 每日 04:30 | 重启双 bot，顺带触发记忆提炼 |

## 3. 目录约定（运行态）

线上服务根为 `/opt/terraria`（可改，见各脚本顶部变量）：

```
/opt/terraria/
├── 1458/Linux/            # 官方服务端（TerrariaServer.exe 为补丁版）
├── .local/share/Terraria/Worlds/   # 世界存档（服务端 HOME 下）
├── backups/               # 每日 04:00 打包，保留 14 天
├── scripts/               # 运行脚本 + 面板 + 记忆/配色等状态文件
│   ├── terraria_ai.py     stats_server.py     dashboard.html
│   ├── memory.txt         # 记忆库（日期|内容）
│   ├── .mem_since         # 记忆提炼水位
│   └── player_colors.json # 玩家配色持久化
├── persona.txt / persona2.txt      # 人设卡
├── ai.env / ai2.env       # 双 bot 配置（含密钥，权限 600）
├── console.fifo           # 控制台注入通道
├── start.sh / backup.sh   # 启动器与备份
└── logs/                  # monitor.sh 采样输出
```

**仓库（本地）与运行态（线上）不是镜像关系**：仓库保存源码、脚本、单元、模板与文档；运行时产生的一切（存档、备份、密钥、记忆、配色、日志）只存在于线上，不入库。二者的同步方式见 [operations.md](operations.md#6-部署与同步)。

## 4. 资源占用参考

实测环境（腾讯云轻量 4C4G / 3Mbps）：

| 项 | 典型值 |
|---|---|
| 游戏服 CPU | 空载 ~0%，6 人在线 ~15–30% |
| 双 AI CPU | 各 <1%（事件驱动，每 4 秒轮询） |
| 双 AI 内存 | 各 ~30–40MB |
| 面板内存 | ~40MB（日志缓存 5 万行上限） |
| 磁盘 | 世界 ~13MB 且缓慢增长；每日备份 ~14MB × 14 天 |

## 5. 设计取舍

| 决策 | 原因 |
|---|---|
| IL 插桩而非 TShock/插件 | 保持原版体验与版本纯净；插件生态对 1.4.5.8 支持滞后。代价是补丁需随版本重打 |
| 双 AI 独立进程而非单进程多角色 | 配置/重启隔离，一个 AI 崩溃不影响另一个；代价是各自轮询日志（开销可忽略） |
| 记忆用纯文本文件 | 零依赖、可人工编辑、便于版本化外部备份；代价是无向量检索（当前场景够用） |
| 事件黑名单（`AI_IGNORE_EVENTS`） | 像 Ghost 这类高频刷屏事件对玩家无意义，过滤后 AI 与看板都更干净 |
