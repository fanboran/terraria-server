# terraria-server

本仓库的主项目：Terraria 专用服 + 双 AI 机器人 + 实时看板。

> 完整文档已整理到仓库根目录 [`../docs/`](../docs/)；部署与运维请直接看那里。

## 目录

| 路径 | 内容 |
|---|---|
| `src/terraria_ai.py` | 双 AI 主程序：日志分类、事件识别、发言决策、记忆系统 |
| `src/stats_server.py` | 看板后端（HTTP :19980），同时为 AI 提供世界天数 |
| `src/dashboard.html` | 看板前端（单文件零构建） |
| `personas/*.txt` | 扣哒 / 小T 人设卡 |
| `scripts/` | `deploy.sh` 一键部署 · `update.sh` 版本跟进 · `start.sh` 启动器 · `backup.sh` 备份 · `monitor.sh` 健康采样 · `bwcheck.sh` 带宽核查 · `tc.sh` 控制台工具 · `panic.sh` 应急 |
| `systemd/` | 7 个 systemd 单元（游戏服 / 双 AI / 看板 / 监控 / 每日重启） |
| `examples/` | 配置模板：`ai.env.example`、`ai2.env.example`、`items_map.json` |
| `ilpatch/` | IL 插桩补丁工程（仅源码，二进制不入库） |

## 文档导航

- [部署](../docs/deployment.md) · [架构](../docs/architecture.md) · [AI 机器人](../docs/ai-bots.md)
- [IL 补丁](../docs/il-patch.md) · [看板](../docs/dashboard.md) · [运维手册](../docs/operations.md)

## 本地文件与运行态的关系

仓库保存**源码/脚本/单元/模板/文档**；线上 `/opt/terraria` 额外承载**运行态数据**（世界存档、备份、密钥、记忆库、配色、日志）——这些只在线上、不入库。同步映射表见 [运维手册 §6](../docs/operations.md#6-部署与同步)。

## 不入库的内容（见根 `.gitignore`）

| 排除项 | 原因 |
|---|---|
| `decomp/` | Terraria 官方程序集的反编译产物，受 Re-Logic 版权保护 |
| `ilpatch/*.exe` | 官方服务端及其补丁产物（商业软件、体积大） |
| `worlds/` `backups/` | 玩家存档与备份（隐私 + 体积） |
| `ai.env` / `ai2.env` / `.api_token` | 含 LLM API Key、管理令牌 |
| `memory.txt` / `player_colors.json` / `probe_anchor.json` | 运行态数据 |
| `mai-terraria/` | 第三方（MaiBot 衍生）参考仓库 |
