# 泰拉瑞亚服务器部署文档

> 更新时间：2026-09-01 17:20 · 状态：**服务端已部署并运行中**，仅剩腾讯云控制台放行端口
> 方案：官方原版服务端 + systemd + FIFO 命令注入 + 每日备份

## 服务器信息

| 项 | 值 |
|---|---|
| 实例 | <INSTANCE_ID> |
| IP | <SERVER_IP> |
| 配置 | 4核 / 4GB / 40GB SSD / 3Mbps / 300GB月流量 |
| 系统 | Ubuntu 22.04.5 LTS · x86_64 · 磁盘余 34G |
| SSH 用户名 | **ubuntu**（sudo 免密） |
| SSH 密钥 | `~/.ssh/<YOUR_KEY>`（本机，ed25519，已绑定实例） |

## 选型结论

- **官方原版服务端**（terraria-server-1458.zip）：Re-Logic 与客户端同步发布，版本跟进零延迟。TShock 最新稳定版停在 1.4.5.6 且滞后半年，需要全员客户端降级，不采用。
- **无需安装 .NET/Mono**：官方 Linux 服务端是 self-contained 原生二进制。
- **唯一瓶颈是 3Mbps 上行**：已通过 npcstream=30、中世界、限 8 人缓解。内存/硬盘/流量全部富余。

## 部署参数（已生效）

| 参数 | 值 |
|---|---|
| 服务端版本 | Terraria **1.4.5.8**（运行中，日志已确认） |
| 世界文件 | `/opt/terraria/.local/share/Terraria/Worlds/QAQ.wld` |
| 游戏内世界名 | <世界名>（组合种子：get fixed boi + 19 附加种子） |
| 游戏密码 | `<SERVER_PASSWORD>`（自定，务必高强度） |
| 难度 / 大小 | 大师 / 中世界（固化在世界文件内） |
| serverconfig | **最小化 5 行**（world/worldpath/port/password/maxplayers）——见踩坑记录 5 |
| 最大玩家 / npcstream | 8 / 60 |
| 服务账号 | terraria（非 root 运行） |
| systemd 服务名 | terraria（active，开机自启） |

## 已完成

- [x] SSH 密钥生成、导入腾讯云、绑定实例
- [x] 上传脚本与世界文件（单机生成的 .wld，服务端直接加载）
- [x] deploy.sh 全流程执行，服务 v1.4.5.8 启动，监听 7777
- [x] ufw 启用（22 / 7777 tcp+udp 已放行，SSH 未锁）
- [x] 每日凌晨 4 点自动备份（cron，保留 14 天）
- [x] 踩坑修复回写脚本（见下）

## 部署完成（2026-09-01 17:24 全链路验证通过）

- [x] 腾讯云控制台防火墙已放行（用户操作，全 TCP/UDP；系统层 ufw 仍只暴露 22/7777，双层防护生效）
- [x] 外网实测：TCP 7777 可达，SSH 22 正常

**进游戏**：多人游戏 → 通过 IP 加入 → `<SERVER_IP>:7777` → 密码 `<SERVER_PASSWORD>`

> 安全提示：控制台全放行没问题（ufw 是第二道门，实际对外只开 22/7777），但讲究最小暴露的话可把控制台规则窄化为 22 和 7777。

## 运维速查

```bash
ssh -i ~/.ssh/<YOUR_KEY> ubuntu@<SERVER_IP>    # 本地直连

systemctl status terraria                          # 运行状态
sudo journalctl -u terraria -f                     # 实时日志
echo save > /opt/terraria/console.fifo             # 手动存档
echo "say 5分钟后重启" > /opt/terraria/console.fifo # 全服广播
echo exit > /opt/terraria/console.fifo             # 存档并关服
sudo /opt/terraria/backup.sh                       # 立即备份
sudo bash /opt/terraria/scripts/update.sh --check  # 检查官方新版本
sudo bash /opt/terraria/scripts/update.sh 1459     # 更新到指定版本
sudo bash /opt/terraria/scripts/bwcheck.sh 30      # 带宽采样（要有人在游戏里）
sudo /opt/terraria/scripts/tc.sh stop             # 安全停服：有人在线自动拒绝
sudo /opt/terraria/scripts/tc.sh start            # 启动
sudo /opt/terraria/scripts/tc.sh players          # 在线人数
sudo /opt/terraria/scripts/panic.sh lockdown      # DDoS 应急：一键封游戏端口
sudo /opt/terraria/scripts/panic.sh restore       # 解封恢复
systemctl status terraria-stats                   # 统计 API 服务状态
```

## AI 聊天机器人（terraria-ai.service，2026-09-01 上线，v4 麦麦式特化）

**决策过程（已实测）**：本机 clone MaiBot 源码分析——依赖清单含 faiss/numpy/pandas/pyarrow/scipy/playwright 等重依赖（pip 约 1-1.5GB，核心进程 300-800MB），736 个 py 文件大半为 QQ 协议/WebUI/插件系统。**硬装可行但为 QQ 群设计，杀鸡用牛刀**。最终采纳"借鉴部分源码"路线，将其 prompts 精华特化进轻量 bot：

- **人设卡**：`/opt/terraria/persona.txt` 定义人格（性格/口头禅/说话风格），注入每次对话 system prompt，改文件即换人格
- **决策式接话**（借鉴 MaiBot maisaka_chat.prompt 的"先判断再发言"）：两层判断——概率层（`AI_TALK_PROB` 默认 0.6）+ **气氛判断层**（prompt 指示 LLM 若话题不需要接话就回复 `[SILENT]`，bot 收到即沉默）。事件自主说话不受此限
- **风格模仿**（借鉴 learn_style.prompt）：携带最近群聊上下文，prompt 强制模仿群内说话风格与用词
- **事件感知**：玩家进出/聊天/存档/启动/被踢；有人在线才说话；接聊天一句、事件 2-3 句
- 配置 `/opt/terraria/ai.env`：`AI_API_KEY`（DeepSeek platform.deepseek.com）/ `AI_TALK_PROB` / `AI_BOT_NAME` 等；填 key 后 `sudo systemctl restart terraria-ai` 激活

## 统计看板（2026-09-01 上线，已升级为管理控制台）

服务端：`terraria-stats.service`，零依赖 Python API，端口 **19980**：

| 端点 | 方法 | 说明 | 鉴权 |
|---|---|---|---|
| `/stats` | GET | 在线人数/CPU/内存/swap/磁盘/备份 | 匿名 |
| `/logs?after_seq=<n>` | GET | 增量拉取日志（单调 seq 游标，杜绝秒级时间戳重复推送） | 匿名 |
| `/logs?before=<ts>&limit=N` | GET | 历史回溯（往前翻页，按 key 去重） | 匿名 |
| `/cmd` | POST | 执行控制台命令（聊天用 `say 文本`），写 FIFO 注入 | **令牌** |

- **看板功能**：三条进度条（CPU / 内存含 swap 联动变色 / 磁盘）、**事件流 v2**：三列网格（玩家徽章 | 内容 | 时间右对齐），时间按分钟分组（同分钟只显示一次）、每 30 分钟插入时间轴刻度线、玩家名徽章带用户色（hash 稳定、避开红绿黄蓝紫语义色）、存档为绿色单行（不再折叠）、区块同步异常（`Invariant Failed: Error on message 150`）、连接拒绝、玩家死亡聚合成**比例条带**（连续同类事件按顺序分段，段长与次数成正比，如 空气×2｜空间×1｜空气×2 → 2:1:2）、`Resetting game objects N%` 进度行合并为一条"服务器初始化中…"、**滑到顶部自动加载更早**（QQ 式无限上翻，视口不跳动）、初始加载 400 行、服务端过滤启动横幅/崩溃栈噪音行、uptime 从部署基准计算
- **世界状态消息（预解析）**：血月/日食/入侵/Boss 苏醒与击杀/玩家死亡/队伍变更等 wiki 状态消息模式已全部预解析（血月→琥珀事件、Boss→事件、死亡→红色条形图聚合）。注：这些消息原版服务端通常只在玩家客户端显示、不打印到控制台（TShock 才输出），若服务器广播到日志即自动生效；TShock/OTAPI 目前停 1.4.5.6，等其跟进 1.4.5.8 后可拿到完整事件源
- **反编译结论（2026-09-02，TerrariaServer.exe 未混淆，ilspycmd 反编译 1548 文件）**：血月触发（`Main.bloodMoon=true`）与昼夜切换只走 `ChatHelper.BroadcastChatMessage`（广播客户端），服务端控制台不打印；但控制台存在官方只读命令 **`Time`**（输出 `时间：10:30 上午` / `Time: 10:30 AM`）。已实现 **time 探针**：stats 服务每 60s 向控制台 FIFO 注入 `time` 命令，解析出游戏时间与昼夜状态（白天 4:30 AM-7:30 PM，实测 `22:08 · 夜晚` 正确），前端日志区顶部显示 **3px 昼夜扁条**（白天白/夜晚灰）。反编译源码保留在 `terraria-server/decomp/`
- **用户色 v2**：16 个高区分度预设（色相均匀、避开纯语义色），动态分配——新玩家优先选与已用颜色色相差最大的，同人永远同色（localStorage 持久化，实测同时出现的玩家颜色互不相同）
- **死亡/异常/拒绝聚合条带 v2**：改为 **3px 扁条**（不占垂直空间，纯用户色分段、hover 显示 名字×次数），与昼夜扁条样式可区分
- **回溯合并**：`Resetting game objects N%` 与 `Loading world data N%` 进度行在"加载更早"回溯时同样合并为一条"服务器初始化中…"；time 探针输出（`Time: ...`）不显示在事件流中
- **回溯边界修复（2026-09-02）**：`journalctl --until` 是 `<=` 语义会返回游标当刻的行，前端过滤后为空被误判"已到最早"导致翻页卡死。已改为 `--until @(ts-1)` 严格早于 + 连续 2 次空返回才判定到最早 + 加载后仍贴顶则自动续载
- **回溯刻度线**：往前翻的历史同样按**整小时**补刻度线（每个整点后第一条消息划一条，该小时无消息不划），跨天时刻度线带日期（`─── 09-02 01:00 ───`）
- **昼夜逆推补录（2026-09-02）**：游戏时间只在有玩家时流动（反编译确认），因此从 time 探针锚点 + 日志玩家进出记录可**逆推全部历史昼夜切换点**（世界创建至今），前端在对应时间位置渲染**全宽分割线**：白天开始=太阳黄+☀ 方格、夜晚开始=月灰+☾ 方格，间距均匀（7px），只占信息流夹缝。实测逆推 21:20:25 夜晚开始与当前 22:08 夜晚自洽。同时逆推**世界初始时刻**（实测 17:06 白天，与新世界默认 4:30AM 差约 8 游戏小时——说明世界在服务端加载前有约 8 分钟单机时间流动）与**游戏内天数**（实测第 1 天），显示在顶部昼夜条
- **聊天物品图标（[i:ID]）**：反编译提取物品 ID→中文名映射（ItemID.cs + zh-Hans Items.json，6180 条），`stats_server` 托管 `/items.json`，前端把聊天里的 `[i:54]`、`[i/p77:5395]`、`[i/s4:22]` 渲染成物品名徽章（实测：赫尔墨斯靴/臭臭/铁锭）
- **聚合条带深色化**：异常/拒绝/死亡/连接的条带段色统一为深灰 `#3a4150`（不再用用户色，避免喧宾夺主），IP 不再套玩家徽章色
- **聚合条带 v3**：段底色深灰 + **段顶 1px 用户色细描边**（极细彩色线，不喧宾夺主）；**点击条带展开明细**（每段 名字×次数 列表）；IP/断开/Server 等状态名不再套玩家徽章框（徽章仅限真实玩家 chat/io）
- **刻度线只看有意义事件**：扫描噪音（连接/拒绝/异常）不再触发整点刻度（修复"02:00 没发生什么却划了线"）；刻度显示该小时第一条有意义消息的真实时间
- **boot 横幅去重**：每次服务器启动打印两次版本横幅（游戏逻辑），5 秒内重复只显示一条
- **昼夜分割线去图标**：纯色细线 + 中点（白天=太阳黄、夜晚=月灰），无月亮字符
- **连接尝试标签**：扫描噪音混合簇与纯连接簇统一改名为"连接尝试 ×N"（不再叫"扫描/连接"）
- **玩家色服务端分配（2026-09-02）**：废除前端 hash/localStorage 方案，改为**服务端固定色库 + 先来先得 + 持久化**（`/colors` 接口 + `player_colors.json`）。10 色库前 6 色高区分度（青绿/橙/天青/黄绿/靛蓝/浅橙），粉系沉底（>6 人时才可能用到，maxplayers=8 富余），避开红/绿/黄/蓝/紫语义色；固定色：空气=灰 `#9ca3af`、Server=橙红 `#f97316`。同一玩家跨设备/跨刷新/服务重启永远同色
- **死亡标记线（2026-09-02）**：死亡事件渲染为**全宽 2px 分段彩线**（与昼夜线同级占位，无文字无时间），连续死亡按顺序拆用户色段（段长∝次数，如 空气×2｜警告×1 = 2:1），被聊天/进出打断拆成两条；连接/拒绝/异常聚合条带段色改为**类型色半透明 2px 实心条**（conn 蓝/deny 红/err 红），点击展开明细
- **聚合条带 v4**：行间距统一（`.item` margin-bottom 3px、`.item.seg` margin 4px 0）；自动存档/连接尝试字号统一 13px
- **在线名单去重修复（2026-09-02）**：`players_from_journal`/`player_intervals` 在 joined→left→joined 循环时重复追加同名，导致在线列表出现 9 条"空气"、昼夜逆推区间重叠重复计时间（world_day 虚高、markers 错乱）。已按名字去重
- **昼夜锚点修复（2026-09-02）**：① `game_time()` 遍历日志缓存取**最旧**一条 Time（应取最新）导致锚点被固定在数小时前的错误游戏时间——改为 `reversed` 取最新；② 锚点**持久化固定**（`probe_anchor.json`，首次解析落盘后不再随探针移动）——修复"每次刷新昼夜条位置都不一样"。实测：26 个切换点严格 day/night 交替、跨刷新结果一致、03:14:19 进入白天与玩家 03:07 上线吻合
- **死亡事件数据源（已解决——IL 注入，见下方"事件注入"章节）**：原版服务端日志**默认不打印**玩家死亡/昼夜/血月（走 `BroadcastChatMessage` 仅发客户端）。已通过直接修改 `TerrariaServer.exe` 的 IL 注入 Console.WriteLine 解决，不再等 TShock

## 事件注入（2026-09-02，直接改 TerrariaServer.exe IL）

**原理**：Terraria 未混淆，dnlib 直接改程序集，在目标方法开头/赋值后插入 `Console.WriteLine`，让原版服务端把事件写进日志。工具在本地 `terraria-server/ilpatch/`（`dotnet run -- <in.exe> <out.exe>`），已注入 4 处：

| 注入点 | 输出 | 触发 |
|---|---|---|
| `Main.UpdateTime_StartDay` | `[DAY] 进入白天` | 游戏内 4:30 AM |
| `Main.UpdateTime_StartNight` | `[NIGHT] 进入夜晚` | 游戏内 7:30 PM |
| `Main.UpdateTime_StartNight` 中 `bloodMoon=true`（stsfld）后 | `[BLOODMOON] 血月降临` | 血月触发（夜晚 30% 概率） |
| `Player.KillMe` 开头 | `KILLME: <玩家名>` | 任意玩家死亡 |

**关键坑（已踩）**：dnlib 的 `mod.Import(typeof(Console))` 会引用 .NET 8 的 `System.Console 8.0.0.0`，而服务器是 **Mono**（Console 在 mscorlib）→ 运行即 `FileNotFoundException` 崩溃循环。必须手动构造对 **mscorlib** 的引用（`TypeRefUser(mod, "System", "Console", mod.CorLibTypes.AssemblyRef)`）。另外静态字段赋值 IL 是 **`stsfld`** 不是 `stfld`。
**版本跟进**：游戏更新（1459+）后对新 exe 重跑 ilpatch 即可（30 秒）；原版 exe 备份在 `TerrariaServer.exe.orig`，一条命令回滚。
**已验证**：`KILLME: 空气` 真实捕获（03:44:43），替换后零崩溃。

- **昼夜/死亡/血月注入事件已接入看板**：`[DAY]/[NIGHT]/[BLOODMOON]` → 琥珀事件行（昼夜/血月）；`KILLME: 名` → **死亡条分段彩线**（真实数据，不再依赖模拟）。昼夜分割线：**白天线=悬浮无框数字**（世界第 N 天，透明背景琥珀色字）、**夜晚线=原点灰点**
- **冒号前缀玩家名修复**：玩家进出日志偶有 `: 空气 has left.`（带冒号），前端 parseEvent 与服务端 `_JOIN_PAT`/进出解析三处正则均已允许可选冒号前缀（`^:?\s*`），避免 `: 空气` 被当成独立玩家（另框另色）；旧 `player_colors.json` 中的 `: 空气` 条目已清理
- **崩溃转储折叠（2026-09-02）**：`at System/wrapper`、`Thread:`、`Culture:`、`Exception:`、`HResult:`、`FATAL`、`[ERROR]`、`====`、`terraria.service:`、`tail:` 等崩溃/systemd 刷屏行**折叠成一条**"异常转储 ×N（点击展开）"（不删行，明细可查），增量与回溯同样生效；**单条不折叠**（×1 无意义，直接显示原文）
- **服务启停流程聚合（2026-09-02）**：停止段（正在停止→`:`→异常栈→已停止）与启动段（Started→版本横幅×2→启动完成）各**折叠成一条**"服务 · 服务器已停止/启动完成"（紫色，点击展开明细），流程内杂行全部收进折叠组不再散落；纯 `:` 冒号行（控制台残留回显）直接过滤；实时与回溯（prependLines）同样生效
- **上滑加载修复（2026-09-02）**：崩溃折叠引入时 CRASH 行在 pushRow/prependLines 被 `continue` 跳过**未更新 earliest 游标** → 回溯加载卡死（"完全不能往上滑"）。已修复 + 新增 `ensureFill()`：内容不足一屏时自动加载更早（无滚动条也能翻历史）。实测 28→317 行连续加载
- **顶部天数数字**：`游戏时间 01:27 夜晚 12`（无"·"无"第/天"，纯阿拉伯数字并回文本流，非独立徽章）
- **自动存档时间列对齐**：存档行无 body 导致 grid 自动放置把时间放中间列，已固定 `grid-column: 3`（与聊天行同列，实测偏差 0px）
- **崩溃栈过滤修复**：`at Terraria.xxx` 栈行此前因 `strip()` 去掉前导空格导致 `\s+` 正则失配而漏网，前后端均改为 `\s*` 匹配（实测 0 残留）
- **昼夜分割线补插**：`/logs` 先于 `/stats` 返回时分割线此前漏插，现按时间定位补插到对应日志行前（实测 21:20 夜晚分割线显示在 21:46 首行上方）；游戏内天数显示在顶部昼夜条
- **物品徽章补名**：items.json 加载完成前渲染的物品徽章（占位"物品ID"）在映射到达后自动补上中文名（实测 [i:54]→赫尔墨斯靴）
- **整点刻度修正**：刻度线显示**每个整点后第一条消息的真实时间**（如 1:20 第一条消息写 1:20），该小时无消息不划；跨天判断基于上一条已渲染日志的日期（非"当天第一条"），回溯历史同样生效
- **进度行服务端统一过滤**：`Saving world data/Settling/Validating/Resetting/Loading world data` 全部在服务端过滤（含 BOM 前缀的 Error Logging、journal Boot 分隔线），回溯不再因进度行海导致有效行稀疏而误判"已到最早"卡死（21:05 卡死根因已修）
- **按钮去闪烁**：自动加载（滑到顶）静默模式，不反复切换"加载中/加载更早"；空返回连续 2 次才判定"已到最早"（服务器日志仅从部署时刻起，翻到底即正常终点）
- **连接扫描聚合**：`is connecting...` / `lost connection...`（含 `: ` 冒号前缀回显）聚合为**蓝色 3px 连接条带**（段=IP）；`Invariant Failed: Error on message ?`（Anonymous 畸形包）同样识别为区块异常红色条带
- **重要格式发现**：Terraria 服务端日志的玩家聊天格式是 `<玩家名> 内容`（尖括号）——看板分类与 AI 机器人的聊天识别均已按此修正（此前 AI 从未收到过玩家聊天）
- **日志去重（2026-09-02 修复）**：此前增量轮询用秒级时间戳做游标，而 `journalctl --since @T` 是 `>=T` 语义，导致最新一条日志每 4 秒重复推送、页面上"世界已保存"不断冒出来。已改为服务端单调 seq 游标 + `(t,msg)` 去重、前端按 seq 增量，40 秒无头浏览器实测零增长
- **回溯修复（2026-09-02）**：systemd 249 下 `journalctl --until @T -n N` 连用会返回空（`-n` 覆盖 until 窗口），回溯接口一度失效。已改为 `--until` 不带 `-n`、取全量后代码切片，实测连续点"加载更早"逐页回溯正常

- **昼夜分割线烘焙重构（2026-09-02）**：放弃前端 `MARKERS/markerPos/initMarkers/markersBefore` 游标补插机制（同一段 marker 多次插入导致全部昼夜条挤在启动完成与自动存档之间），改为服务端 `/logs` 流里**直接烘焙**昼夜标记行 `{t, type:"day|night", dn, msg}`——`stats_server._merge_markers` 按 ts 与日志行稳定混排（区间 `(lo, hi]`、跨批不重复），前端 `pushRow` / `prependLines` 看到 `l.type` 直接渲染 `.dnline`（带准确 `dataset.key` 与原始行同键）。结果：昼夜条作为日志流里的固定行天然就位，不再"移动"、不再重复、不再回溯错位。服务端 `_NOISE` 过滤 `[DAY]/[NIGHT]` 注入原始行（与烘焙行不会双份）
- **昼条数字调大居中（2026-09-02）**：`.dnline .num` 字号 9.5px → 15px + `display: flex/align-items/justify-content: center` 严格水平垂直居中（不再像之前贴在线段边角）
- **折叠行跨异步去重（2026-09-02）**：`server` 偶发重复推送横幅（`Terraria Server v`）+ 完成标志（`Server started`/`Listening on port`），原本会因实时 pushRow 与回溯 prependLines 异步交错、同次启动周期被拆成 2-3 条 start 折叠（如 03:37 cycle 出现 rows[7]+rows[8] 两条"启动完成"）。已修：(1) 折叠行加 `data-lines`（原始行 key 列表，回溯按行去重时一并标记）；(2) 全局 `seenStartTs/seenStopTs` Set + `dupBootNear(t,kind)`：跨异步边界生效（box 查询在初始 pushRow 阶段为空不可靠），flushBoot 即追加；(3) 30 秒周期窗口：journal 重复推送在同次启动周期内直接丢弃；(4) `flushBoot` 同步更新 `earliest` 防回溯重拿到启动段。实测 6 个独立启停周期 = 12 条折叠（stop+start 各 6），零重复
- **时间列规范化（2026-09-02）**：行内时间列只显示 `HH:MM`（去掉 `MM-DD` 日期前缀，日期由整点刻度条承担）；同分钟多条事件（含折叠行/单条崩溃/回溯行）只显示第一条（`timeCol` 全局 / `timeColL` 回溯 localMin，与 box 最早行分钟衔接）；折叠行时间列不再每次都显示
- **刻度条日期恢复（2026-09-02）**：原 `timebarText` 依赖 `lastLogDate`（被每条行的 updateLogDate 提前刷新成当天，导致当天第一条刻度条不带日期）。改为 `lastTimebarDate`：**每天第一条刻度条带日期**（`─── 09-02 04:00 ───`），后续同天不带，跨天自动切换
- **首次加载顺序修复（2026-09-02）**：loadLogs 首次无参返回后**先回溯补齐更早历史（before=minT）再渲染最新行**（loadingEarlier 占位防并发），保证同一启停段完整落入 box、`seenStartTs` 先记录，杜绝"回溯段 1455 + 实时段 1464"异步切分导致的 03:37 ×2
- **昼分割线视觉修复（2026-09-02）**：`.dnline` 加 `align-items: center`（线段与数字同轴垂直居中，修复数字偏上）；`.num` 去掉 `height:1.3em` 撑高（修复上下大留白）、margin `0 10px→0 5px`（数字总宽 ≈ 夜线圆点总宽 25px，两端线段与夜线端点对齐）
- **连续存档折叠（2026-09-02）**：两次自动存档间无其他已渲染事件 → 合并为绿色"自动存档 ×N（点击展开）"（明细为每次存档时间）；判断用 DOM（跳过 timebar/diebar/dnline 装饰行后 box 最后一行是否仍是本存档行）而非标志位——连接尝试等噪音只更新条带文本不产生新行 → 存档跨噪音合并；聊天/进出/死亡等真实事件产生新行 → 打断。实时 pushRow 与回溯 prependLines 同步实现（`savePush` / `localSavePush`）
- **连接尝试跨存档合并（2026-09-02）**：存档分支不再 `flushSeg`——撞库扫描等失败连接尝试跨 10 分钟存档周期持续累积为一个大条带（此前被存档打断成 ×39/×8/×47/×1 碎片）；玩家真实活动（chat/io/die/boot/event/marker/普通行）仍打断聚合，正常玩家"连接→joined"按次分段合理

## AI 接入硅基流动 + say 纯文本化（2026-09-02）

**背景**：原版服务端 `say` 命令广播 `NetworkText.FromKey("CLI.ServerMessage", text)`，客户端本地化渲染成 `<服务器> {0}`（zh-Hans）/ `<Server> {0}`（英文）——bot 发言永远带"Server 前缀"，无法像玩家一样说话。

**服务端注入 patched4**（ilpatch 第 4 版，叠加在 patched3 上）：
- 改写 `Terraria.Main.startDedInputCallBack` 的 Say 命令分支两处调用：
  - `Console.WriteLine(Language.GetTextValue("CLI.ServerMessage", text5))` → `Console.WriteLine(text5)`（控制台/看板日志显示原文本）
  - `ChatHelper.BroadcastChatMessage(NetworkText.FromKey("CLI.ServerMessage", text5), ...)` → `BroadcastChatMessage(NetworkText.FromLiteral(text5), ...)`（游戏内广播原文本，无 `<服务器>` 模板）
- **踩坑**：`FromKey` 是 `params object[]`，IL 里有 `newarr` 数组构造，只替换 call 会让栈残留数组 → JIT InvalidProgramException 崩溃循环。必须把 `ldc.i4.1/newarr/dup/ldc.i4.0/stelem.ref` 全部 nop、只留 `ldloc text5` 再 call `FromLiteral(string)`。GetTextValue(string,object) 单参重载无数组，nop call 即可
- 原版 exe 备份 `TerrariaServer.exe.orig`、patched3 备份 `TerrariaServer.patched3.exe`（同目录）
- 验证：`say <小T> 大家好` → journal 输出 `<小T> 大家好`（玩家聊天格式），看板识别为 chat"小T"徽章（服务端自动配色 #fdba74）

**AI 机器人（terraria_ai.py）**：
- `say()` 广播文本自带 `<{BOT_NAME}> ` 前缀 → 游戏内以玩家样式显示（无 Server 前缀）
- 自聊防护：classify 的 SKIP_NAMES 含 BOT_NAME，bot 自己的 `<小T> ...` 不触发回复
- `ai.env` 配置硅基流动：`AI_BASE_URL=https://api.siliconflow.cn/v1`、`AI_API_KEY=sk-rrhtax...`、`AI_MODEL=deepseek-ai/DeepSeek-V3.2`（实测 API 端到端返回正常，模型列表含 DeepSeek-V3.2）
- 行为：有人在线才说话；聊天接话概率 0.6 + [SILENT] 气氛判断；事件（进出/存档/启动）必说带冷却

- **管理令牌**（务必保密，等于服务器管理权）：`<ADMIN_TOKEN>`（首次部署时生成，存于服务器 `/opt/terraria/scripts/.api_token`，权限 600，切勿外泄），存于 `/opt/terraria/scripts/.api_token`
- **访问方式（推荐）**：浏览器直接打开 `http://<SERVER_IP>:19980/`——页面已同源托管在服务器上，任何设备可用，无本地文件 mixed content 问题；本地 `dashboard.html` 双击亦可（file:// 下自动改用绝对地址）
- `发聊天`模式自动加 `say` 前缀，`命令`模式为原始控制台命令
- 安全边界：`/cmd` 是写通道，拿到令牌者可完全控制服务器；当前 http 明文，公网传输令牌有被嗅探风险，建议仅可信网络使用
- 已观察到公网 IP 对 7777 端口的错误密码连接尝试（撞库扫描）——**建议尽快更换为高强度密码**

## DDoS 应急策略（2026-09-01 决策：不抵抗）

非生产环境，可用性让位于机器稳定：**被打就直接封端口，不做任何对抗**。

- `panic.sh lockdown`：一键将 7777 tcp/udp 插到 ufw 规则链最前（deny 优先于 allow），攻击流量落在腾讯云基础防护（默认自动开启，无需配置）上
- SSH 22 永不封禁，管理通道始终保留
- 攻击结束后 `panic.sh restore` 恢复
- 未来加 MC（25565）时记得把端口加进 panic.sh 的 GAME_PORTS 列表

## 内存扩容记录

- 2026-09-01 已加 **4G swap**（`/swapfile`，fstab 持久化，`vm.swappiness=10` 保护游戏延迟）
- 内存账：物理可用约 1.9G + swap 4G 应急池
- 常驻服务优先省内存（GitHub runner ~150M < Gitea ~300M）；重活（构建/转码）跑前用 `tc.sh stop` 释放 Terraria 的 1.1G

脚本正本：服务器 `/opt/terraria/scripts/`，本地 `F:\VSCode\WB\Service\terraria-server\`。

## 版本跟进纪律

- 客户端一更新，服务端必须跟上，否则所有人无法连接。
- **世界文件向后兼容但不能回退**，update.sh 更新前会自动备份。
- 1.4.5.7（8/20）→ 1.4.5.8（8/23）间隔仅 3 天，跟进是常态。

## 备份与容灾

- 自动备份：每日 04:00，先 `save` 再打包，保留 14 天，位于 `/opt/terraria/backups/`
- 恢复方法：停服 → 解包覆盖 `.wld` 到 `/opt/terraria/.local/share/Terraria/Worlds/` → 起服
- 恢复演练：已通过（2026-09-01，包内容含 QAQ.wld + 2 个滚动副本，gzip 校验通过）
- **异地副本**：`F:\VSCode\WB\Service\terraria-server\backups\`（2026-09-01 首份已拉取并校验）。
  备份与世界同盘，磁盘故障会一起丢——重要节点（进困难模式、打完大 Boss）建议手动再拉一份：
  ```bash
  scp -i ~/.ssh/<YOUR_KEY> ubuntu@<SERVER_IP>:/opt/terraria/backups/world_最新.tar.gz ./backups/
  ```

## 本次部署踩坑记录（已修复回脚本）

1. **IPv6 下载陷阱**：云服务器 DNS 对 terraria.org 只回 IPv6 记录，wget 不回退 IPv4 静默失败；curl 会自动回退。脚本已全部改为 curl 并加 `--retry`。
2. **目录属主**：`/opt/terraria` 若由 root 提前创建，`useradd -m` 不会改属主，服务账号写入失败。脚本已在创建目录后立即 `chown -R`。
3. **set -e 不继承**：`runuser -- bash -c "..."` 子 shell 不继承外层 `set -e`，下载失败会静默继续执行后续步骤。已在子 shell 内显式开启。
4. **FIFO 权限**：console.fifo 由 terraria 用户创建（默认 644），ubuntu 用户运维时写入被拒。start.sh 已改为 `mkfifo -m 666` 并在启动时强制 chmod，普通用户可直接注入命令。
5. **autocreate 毒药参数（本次事故的根因，1.4.5.5+ 官方确认的未文档化变更）**：serverconfig 只要出现 `autocreate`（任意值），服务端就忽略该选项、改用其数值推断世界尺寸而不是读世界文件实际尺寸；尺寸不匹配时**多人区块流式传输损坏 → 大片区域（本例为高空区域）物块不加载**，连带玩家视角瞬移/实体失步/放置物块重进消失。单机正常、普通种子世界正常（群友"没动配置"即未写该参数），组合种子世界（实际尺寸与标准尺寸不符）必现。官方论坛 2026-07 已确认并给出修复：**不写 autocreate**。修复：配置已最小化至 5 行（world/worldpath/port/password/maxplayers），deploy.sh 已同步修复并强制世界文件存在校验。
