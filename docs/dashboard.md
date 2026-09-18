# 看板（dashboard）

看板由 `src/stats_server.py`（后端，HTTP :19980，纯标准库零依赖）+ `src/dashboard.html`（前端单文件）组成，同源托管——浏览器直接访问 `http://<SERVER_IP>:19980/` 即可，无需额外 Web 服务。

## 1. 接口

| 接口 | 说明 |
|---|---|
| `GET /` | 返回 `dashboard.html` |
| `GET /stats` | 服务器状态：在线玩家、连接数、CPU、内存、Swap、磁盘、备份、运行时长、世界天数、昼夜状态 |
| `GET /logs?after_seq=N` | 增量拉取新日志（seq 游标，保证不重复） |
| `GET /logs?before=TS&limit=N` | 历史回溯（向上翻页；游标为 Unix 秒） |
| `GET /logs` | 默认最近 N 行 |
| `GET /colors` | 玩家配色映射（`colors` 服务端分配 + `fixed` 固定色） |
| `GET /items.json` | 物品 ID → 中文名（聊天中 `[i:ID]` 徽章渲染） |
| `POST /auth` `POST /cmd` | 管理入口鉴权与控制台命令注入（需令牌，见 `scripts/.api_token`） |

## 2. 呈现规则

| 元素 | 规则 |
|---|---|
| 玩家名徽章 | 固定色优先（扣哒粉 `#f472b6`、小T绿 `#4ade80`、Server 橙…），其余玩家"先来先得"分配并持久化到 `player_colors.json` |
| 聊天 | 玩家色徽章 + 内容；同一行内渲染物品徽章 |
| 进出 / 存档 / 启停 / 崩溃 / 连接噪音 | 折叠为可展开条（点击展开明细，避免刷屏） |
| 死亡 | 折叠为**玩家色分段条带**：每段长度 ∝ 该玩家死亡次数，悬浮显示"谁 ×几次" |
| 昼夜 | 逆推烘焙的 **分割线 + 悬浮数字**（数字 = 世界第 N 天）；标记数据由后端生成，前端不重复计算 |
| 事件（入侵/NPC/血月/日食/Boss） | 琥珀色"事件"条 |
| 历史翻页 | 滚动到顶部自动加载更早（QQ 式），可一路翻到日志起点 |

## 3. 实现要点

- **日志缓存**：内存环形缓存（默认上限 5 万行，约两周），带单调递增 `seq` 供前端做增量游标；`(时间戳, 内容)` 去重防止 journald 秒级游标重复返回同一行。
- **历史回溯**：缓存已覆盖全量时直接内存切片（毫秒级）；仅在游标早于缓存起点时才回落 `journalctl --until`。
- **并发**：`ThreadingHTTPServer` + 全局锁——避免早期单线程 + `journalctl` 全量扫描把前端轮询（颜色/日志）阻塞到"翻页卡死、丢弃颜色"。
- **噪音过滤**：`_NOISE` 正则丢弃进度刷屏、崩溃栈、扫描连接等无意义行（**注意带 `: ` 前缀的变体也要覆盖**）。AI 侧的黑名单（`AI_IGNORE_EVENTS`）与此保持同步。
- **名字迁移**：改名前的历史发言按时间戳改写（如旧 `<小T>` → `<扣哒>`），避免与新角色撞名。

## 4. 修改与部署

```bash
# 前端：改完直接覆盖，服务端每次请求实时读取
scp src/dashboard.html <user>@<server>:/tmp/
ssh <user>@<server> 'sudo cp /tmp/dashboard.html /opt/terraria/scripts/ && sudo systemctl restart terraria-stats'

# 后端：同上，改 src/stats_server.py
```

> 前端为**单文件、零构建**：无打包工具、无外部依赖（除可选的图表库 CDN）。修改后用浏览器强刷即可看到效果。

## 5. 扩展建议

- 新增事件类型：在 `stats_server.py` 的过滤规则与 `dashboard.html` 的 `parseEvent()` 中同步添加（注意前缀容错 `:?`）。
- 新增统计维度：扩展 `stats()` 返回字段，前端卡片按 `/stats` 键读取。
- 若日志量超过内存缓存上限：调大 `_LOG_CACHE` 的 `maxlen`，或改为 SQLite 落盘。
