# Terraria AI Server

把一台普通的 Terraria 专用服务器，改造成**有 AI 常驻、有实时看板、能自我沉淀记忆**的小型游戏社区服务器。

原版服务端不对外输出昼夜/血月/死亡/入侵等事件，也无法让机器人在游戏内以彩色玩家样式发言。本项目通过 **IL 插桩补丁**打通这条链路，再由两个不同人格的 LLM 机器人（扣哒 / 小T）消费同一份事件流，实现事件播报、日常闲聊与长期记忆。

> 面向场景：3–8 人小规模原版生存服（低带宽友好），亚服/跨境玩家可用。

---

## 特性

| 能力 | 说明 |
|---|---|
| 🎭 **双 AI 人格** | 扣哒（日常闲聊，性格自由发挥）与小T（世界意志，只在大事现身），同一份代码、靠配置区分 |
| 🧠 **长期记忆** | 每次重启自动从日志提炼"名场面"存入记忆库，之后聊天时可自然引用（如"你们俩昨天就是在这被 Ghost 追着跳下去的"） |
| 🗣️ **麦麦式发言决策** | 每条消息交给 LLM 自主判断"说不说"（`[SILENT]` 气氛判断），而非随机概率 |
| 🌗 **完整事件感知** | 昼夜、血月、日食、入侵、Boss 苏醒与击败、NPC 到达、死亡、进出、存档——全部进日志、上面板、可播报 |
| 🎨 **游戏内彩色发言** | 扣哒粉 / 小T绿，按发言者名字自动着色（IL 补丁） |
| 📊 **实时看板** | 玩家色徽章、事件折叠条、死亡分段色带、昼夜分割线、历史无限上翻 |
| 🌐 **跨境链路优化** | BBR + fq + TCP keepalive 调优，缓解高丢包链路的掉线与"幽灵会话"拒连 |
| 🛡️ **运维工具集** | 一键部署 / 版本跟进 / 健康监控 / 带宽核查 / DDoS 应急止血 |

---

## 架构

```
玩家 ──:7777──▶ TerrariaServer.exe（IL 插桩补丁版，mono/FNA）
                    │ stdout → systemd journal
                    ▼
   terraria-ai（扣哒）─────┐  双进程读取同一份日志
   terraria-ai-tt（小T）────┤  各自决策，向 console.fifo 写入 "say <名> 文本"
                    │        （AI_SKIP 互斥，不会互相接话死循环）
   terraria-stats（:19980）──▶ dashboard.html 实时看板
```

- **单一事件源**：游戏服 stdout（IL 插桩产生）→ journald → AI 与面板消费同一份日志，三者所见完全一致。
- **发言通道**：AI 向 FIFO 注入 `say <名字> 内容`，经补丁以玩家样式广播并按名字着色。
- **成本控制**：无人在线时不调用 LLM API。

---

## 目录结构

```
.
├── README.md                  # 本文件
├── LICENSE
├── CONTRIBUTING.md
├── CHANGELOG.md
├── docs/                      # 详细文档（见下方索引）
└── terraria-server/           # 主项目
    ├── src/                   # 核心源码
    │   ├── terraria_ai.py     #   双 AI 主程序（分类/事件/决策/记忆）
    │   ├── stats_server.py    #   看板后端（HTTP :19980）
    │   └── dashboard.html     #   看板前端
    ├── personas/              # 人设卡（可自由改写）
    ├── scripts/               # 部署与运维脚本
    ├── systemd/               # systemd 单元文件
    ├── examples/              # 配置模板（ai.env.example 等）
    └── ilpatch/               # IL 补丁工程（仅源码，二进制不入库）
```

---

## 快速开始

### 1. 部署游戏服

```bash
# 在目标服务器（Ubuntu 22.04/24.04，root 执行）
sudo TERRARIA_VERSION=1458 \
     SERVER_PASSWORD='<你的高强度密码>' \
     WORLD_NAME=MyWorld \
     bash scripts/deploy.sh
```

完成后放行安全组 TCP/UDP 7777，即可通过 `<SERVER_IP>:7777` 进入。

### 2. 部署 AI 机器人

```bash
# ① 安装运行文件
sudo cp src/terraria_ai.py /opt/terraria/scripts/
sudo cp personas/persona.txt personas/persona2.txt /opt/terraria/

# ② 配置（填入你的 LLM API Key）
cp examples/ai.env.example /opt/terraria/ai.env      # 扣哒
cp examples/ai2.env.example /opt/terraria/ai2.env    # 小T
"${EDITOR:-vi}" /opt/terraria/ai.env

# ③ 装服务并启动
sudo cp systemd/*.service systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now terraria-ai terraria-ai-tt terraria-stats
```

### 3. 打开看板

浏览器访问 `http://<SERVER_IP>:19980/`。

> 完整步骤（含 IL 补丁重建、看板、网络调优）见 [docs/deployment.md](docs/deployment.md)。

---

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/deployment.md](docs/deployment.md) | 从零部署：系统准备、游戏服、AI、看板、安全加固 |
| [docs/architecture.md](docs/architecture.md) | 架构与数据流、服务清单、目录约定 |
| [docs/ai-bots.md](docs/ai-bots.md) | 双 AI 分工、人格撰写、事件表、发言决策、记忆系统、全部 env 参数 |
| [docs/il-patch.md](docs/il-patch.md) | IL 插桩原理、注入点表、重打与回滚流程 |
| [docs/dashboard.md](docs/dashboard.md) | 看板接口、呈现规则、扩展方式 |
| [docs/operations.md](docs/operations.md) | 日常运维、网络调优、备份恢复、故障速查、已知坑 |

---

## 重要说明

- **不分发二进制**：`ilpatch/` 仅包含补丁程序源码。Terraria 服务端二进制及其补丁产物受 Re-Logic 版权保护，请自行从官方渠道获取原版后本地打补丁（流程见 [docs/il-patch.md](docs/il-patch.md)）。
- **敏感信息不入库**：服务器地址、SSH 密钥、LLM API Key、游戏密码、玩家数据（存档/备份/记忆）均通过配置模板与外置文件管理，仓库内一律为占位符。
- **参考项目**：[MaiBot（麦麦）](https://github.com/MaiM-with-u/MaiBot) 的设计理念（人格化、先决策再发言、记忆）对本项目影响很大；`mai-terraria/` 为本地参考仓库，不属于本仓库内容。

---

## License

[MIT](LICENSE)
