# 贡献指南

感谢愿意一起完善这个项目。这是一个"运维 + 集成"性质的项目：核心是让原版 Terraria 专用服具备事件感知与 AI 交互能力，因此**稳定性优先于功能数量**。

## 开始之前

1. 阅读 [架构](docs/architecture.md) 与 [AI 机器人](docs/ai-bots.md)，理解"单一事件源 + 多消费者"的设计。
2. 涉及服务端注入的改动，先读 [IL 补丁](docs/il-patch.md)——尤其是 mono 的 IL 验证陷阱。
3. **不要提交任何敏感信息**：真实 IP、SSH 密钥、LLM API Key、游戏密码、玩家数据。仓库内一律用占位符（`<SERVER_IP>`、`<SERVER_PASSWORD>`）。

## 开发环境

| 组件 | 要求 |
|---|---|
| Python | 3.10+（线上为 3.10，注意 `datetime.fromisoformat` 的时区兼容性） |
| .NET | 8.0 SDK（仅 IL 补丁工程需要） |
| 其他 | 无第三方 Python 依赖（刻意保持纯标准库，便于运维） |

本地校验：

```bash
python -m py_compile terraria-server/src/terraria_ai.py terraria-server/src/stats_server.py
cd terraria-server/ilpatch && dotnet build -c Release
```

## 提交规范

提交信息用**类型: 简述**（中文或英文均可）：

```
fix: 修复聊天行带 ": " 前缀时被丢弃
feat: 支持按世界天数播报日出
docs: 补充跨境链路调优说明
refactor: 抽取记忆提炼为独立函数
chore: 忽略 actions-runner 压缩包
```

**拆分原则**：一个提交只做一件事；协议/行为变更需同步更新对应文档（`docs/`）。

## 代码约定

- **Python**：标准库优先；新增配置一律走环境变量（`AI_*`），并在 `examples/*.env.example` 中补注释。
- **前端**：`dashboard.html` 保持单文件零构建；新增日志类型需同时更新后端过滤规则与前端 `parseEvent()`。
- **IL 补丁**：新增注入点必须在 `docs/il-patch.md` §3 表格登记；改动后必须逐路径核对栈平衡。
- **人格文案**：`personas/` 是内容而非代码，欢迎直接提 PR 分享有趣的人设（请勿写入真实玩家名）。

## 提 Issue

请说明：**现象 → 复现步骤 → 相关日志**（`journalctl -u terraria / terraria-ai` 原文片段）→ 环境（服务端版本、Python 版本、是否跨境链路）。**提交日志前请自行脱敏**。

## 提 Pull Request

1. Fork 并从 `main` 拉分支（`feat/xxx` 或 `fix/xxx`）。
2. 完成改动并本地校验通过。
3. 在 PR 描述中写清：动机、改动点、验证方式（如"部署后观察 30 分钟，`[AI ]` 行正常输出"）。
4. 若改动影响线上部署方式，同步更新 `docs/operations.md` 的映射表。

## 不接受的改动

- 提交二进制文件（Terraria 服务端/补丁产物）或反编译源码——版权与体积原因。
- 引入重量级依赖（数据库、Web 框架、爬虫框架）来替换现有的零依赖实现，除非有充分理由。
- 在代码中硬编码个人服务器地址、密钥或玩家信息。
