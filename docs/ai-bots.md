# AI 机器人（扣哒 / 小T）

两个 AI 共用同一份代码 `src/terraria_ai.py`，差异全部由环境文件（`ai.env` / `ai2.env`）与人设卡（`personas/*.txt`）承载。

## 1. 分工

| | 扣哒（`terraria-ai`，`ai.env`） | 小T（`terraria-ai-tt`，`ai2.env`） |
|---|---|---|
| 人设 | 邻家妹妹，性格自由发挥 | 毒舌少女 + 世界意志（被更高存在创造，替世界发声） |
| 日常闲聊 | 参与（每条消息 LLM 自主决策） | 不参与（`AI_CHAT_ENABLED=0`，仅点名回应） |
| 事件播报 | `AI_EVENTS=day,night`（昼夜） | `AI_EVENTS=event,start`（世界级事件） |
| 事件旁听 | `0.4`（别的 AI 负责的事件也可能搭话） | `0`（保持"大事才现身"） |
| 互斥 | `AI_SKIP=小T` | `AI_SKIP=扣哒` |

**互斥的意义**：两个进程都读同一份日志，彼此的发言前缀（`<扣哒>` / `<小T>`）会被对方当作"玩家聊天"。若不屏蔽就会互相接话、无限往返。`AI_SKIP` 让对方的名字进入忽略名单，只与真人互动。

**闲置保护**：任何事件/聊天/idle 分支都有 `if not sessions` 门卫——**没人在线不调用 LLM**，省 API 费。

## 2. 事件识别（`classify()`）

| 日志形态 | 事件类型 | 来源 |
|---|---|---|
| `X has joined.` / `X has left.` | join / left | 原版 |
| `KILLME: X` | death | IL 插桩 |
| `[DAY] 进入白天` / `[NIGHT] 进入夜晚` | day / night | IL 插桩 |
| `[BLOODMOON] 血月降临` | event | IL 插桩 |
| `[EVENT] <文本>` | event | IL 插桩（入侵/日食/Boss） |
| `[NPC] <文本>` | event | IL 插桩（城镇 NPC 到达） |
| `Server started` | start | 原版 |
| `Backing up world file` | save | 原版 |
| `<玩家名> 内容`（容忍 `: ` 前缀） | chat | 原版 |

- **点名**：消息含 bot 名 → 必回（8 秒防刷屏冷却）。
- **黑名单**：`AI_IGNORE_EVENTS`（正则）过滤无价值事件（默认 `Ghost`），AI 与看板同步生效。
- 世界天数：AI 向 `stats_server` 的 `/stats` 取 `world_day`，用于日出播报。

## 3. 发言决策（麦麦式"先决策再发言"）

```
玩家消息 ──▶ 冷却检查（距上次发言 AI_REPLY_GAP 秒）
                │
                ▼
       交给 LLM 做气氛判断（带入：最近聊天、在线名单、记忆、人设）
                │
        ┌───────┴────────┐
        ▼                ▼
   需要接话           不需要接话
   → 生成一句          → 模型回复 [SILENT]
   → 广播 + 计时        → 不说，且不占冷却
```

**刻意不用随机概率**：早期版本用"掷骰子决定接不接"，会出现节奏怪异（该说时不说、不该说时冒头）。改为每条消息都让模型自己判断，AI 才真正"在参与对话"。

其余节奏参数：

| 行为 | 规则 |
|---|---|
| 单人死亡 | 旁听概率 `AI_DEATH_CHIME_PROB`（默认 0.05，死太频繁基本不吭声） |
| 连环团灭 | 窗口内死亡人次 ≥ `AI_DEATH_RALLY_N`（默认 8，窗口 180s）→ **必说**，prompt 强调"集体团灭" |
| 事件播报 | 白名单内必说（冷却 `AI_EVENT_COOLDOWN`）；白名单外按 `AI_EVENT_CHIME_PROB` |
| 空闲自语 | `AI_IDLE_PROB`（默认 0 = 关闭，避免冷场时莫名其妙冒一句） |

## 4. 语言红线（写死在 persona + prompt + 输出层兜底）

1. **纯聊天文本**：不写括号动作/旁白（`（脸红）` 这类）；`say()` 会兜底剥离括号内容。
2. **不输出思考过程**：`llm_reply()` 剥离 `<think>…</think>` 及内部伪标签（DeepSeek 等混合推理模型偶发泄漏）。
3. **不堆语气词**：不刻意加"诶呀/哼"。
4. **少用疑问句**：没有真实想问的事就用陈述句结尾。
5. **少叫名字**：大多数回复不需要出现玩家名，只有直接回应/针对某人才叫；名字只能取自在场名单，不得自创。
6. **只用在场名单**：prompt 实时注入在线玩家 + 3 分钟内刚离线玩家（`RECENT_LEFT`）——刚走的人可以感慨"怎么有人走了"，走远的不要翻旧账。
7. **中文名保持原样**：不翻译、不拆分。

## 5. 记忆系统

```
重启（含每日 04:30 timer）
   │
   ├─ 读水位 .mem_since（上次提炼到哪）
   ├─ 从 journal 取水位以来的 聊天 / 世界事件 / 死亡
   ├─ LLM 提炼 0–3 条"值得长期记住的瞬间" → 追加 memory.txt（日期|内容）
   └─ 更新水位

每次生成 ── persona() 注入最近 N 条记忆：
            "这些是你亲身经历过的往事，聊到相关话题可自然引用，但不要背诵原文"
```

| 项 | 值 |
|---|---|
| 存储 | `scripts/memory.txt`，每行 `MM-DD|一句话` |
| 保留 | `AI_MEMORY_MAX`（默认 40 条，滚动） |
| 注入 | `AI_MEMORY_INJECT`（默认 12 条） |
| 防重 | 水位文件 `.mem_since`；无需人工干预 |
| 失败处理 | 静默跳过，不影响 bot 运行 |

**实现注意**：DeepSeek 对超长 `system` 消息会返回 0 token 空响应——提炼调用固定采用"人设(system) + 记录/指令(user)"的消息形态；`llm_reply()` 对空 content 自动重试一次。

## 6. 撰写人设（`personas/*.txt`）

人设卡直接拼接进 system prompt，建议包含：身份、性格、自称、说话规则、输出红线。示例（扣哒）：

```
你是泰拉瑞亚服务器里的聊天机器人「扣哒」，一个住在服务器里、像邻家妹妹一样的小女孩。
性格不用刻意设定：就像一个真实的女孩子，情绪和说话方式随当下情境自然变化……
自称「我」。
说话规则：口语化；句子中间不要用标点（不要逗号、句号），句尾可用一个！？～……
输出红线：只发聊天文字……不要总叫玩家名字……
```

> 提示：**不要把性格写死**（如"必须是毒舌/必须关心别人"），角色会变得脸谱化。给出情境倾向，让模型自由发挥，台词更自然。

## 7. 全部环境参数

见 [`examples/ai.env.example`](../terraria-server/examples/ai.env.example)（扣哒）与 [`examples/ai2.env.example`](../terraria-server/examples/ai2.env.example)（小T），每个键都有中文注释。核心键速查：

| 键 | 默认 | 说明 |
|---|---|---|
| `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL` | — | OpenAI 兼容接口（实测 SiliconFlow + DeepSeek-V3.2） |
| `AI_BOT_NAME` / `AI_PERSONA_FILE` | — | 名字与人设卡路径 |
| `AI_EVENTS` | 空=全播 | 事件白名单，逗号分隔 |
| `AI_EVENT_CHIME_PROB` | 0.4 | 白名单外事件搭话概率（0=严格分工，1=都必说） |
| `AI_REPLY_GAP` | 20 | 发言后防连击间隔（秒） |
| `AI_DEATH_CHIME_PROB` / `AI_DEATH_RALLY_N` / `AI_DEATH_RALLY_WINDOW` | 0.05 / 8 / 180 | 单人死亡概率 / 团灭阈值 / 判定窗口 |
| `AI_HISTORY` / `AI_MAX_LEN` | 6 / 120 | 上下文条数 / 单条广播长度上限 |
| `AI_IDLE_PROB` | 0 | 冷场自语概率 |
| `AI_MEMORY_*` | 见模板 | 记忆文件、水位、保留与注入条数 |
| `AI_SKIP` | — | 屏蔽另一个 AI 的名字（多 AI 互斥） |
| `AI_IGNORE_EVENTS` | `Ghost` | 杂鱼事件黑名单（正则） |
| `AI_CHAT_ENABLED` | 1 | 0=不参与日常闲聊（小T 用） |
| `AI_STATS_URL` | `http://127.0.0.1:19980` | 查询世界天数的面板地址 |
