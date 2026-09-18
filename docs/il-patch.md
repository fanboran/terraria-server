# IL 插桩补丁

## 1. 为什么需要它

原版 Terraria 专用服务端**不把游戏事件写进控制台**——昼夜更替、血月、日食、入侵、Boss 苏醒/击败、NPC 到达、玩家死亡，玩家在游戏里能看到横幅，但服务端日志只有连接与存档信息。同时，`say` 命令广播的文本会被强制套用固定颜色（`ChatColors.ServerMessage`，黄色）。

要让 AI"看得见世界"并"以彩色的玩家样式说话"，只能改服务端程序集——本项目用 [dnlib](https://github.com/0xd4d/dnlib)（Mono.Cecil 分支）做 IL 插桩。

## 2. 工程结构

```
terraria-server/ilpatch/
├── ilpatch.csproj      # .NET 8 控制台工程（依赖 dnlib）
├── Program.cs          # 全部注入逻辑（单文件）
└── README.md           # 构建说明
```

> **仓库不含二进制**：`TerrariaServer.exe`（原版）与 `TerrariaServer.patched*.exe`（补丁产物）受 Re-Logic 版权保护且体积巨大（各 ~26MB），一律不入库。请自行从官方渠道获取原版。

## 3. 注入点一览

| 能力 | 注入位置 | 产出日志形态 |
|---|---|---|
| 昼夜切换 | `Main::UpdateTime_StartDay` / `StartNight` | `[DAY] 进入白天` / `[NIGHT] 进入夜晚` |
| 血月 | `Main::UpdateTime_StartNight`（`bloodMoon` 判定处） | `[BLOODMOON] 血月降临` |
| 玩家死亡 | `Player::KillMe` | `KILLME: <玩家名>` |
| Say 纯文本化 | `Main::startDedInputCallBack`（say 分支） | 广播原样文本（不再套用 `ServerMessage` 格式） |
| Say 按名着色 | 同上（`ChatColors.ServerMessage` → `new Color(...)`） | `<扣哒>` 粉 `#f472b6` / `<小T>` 绿 `#4ade80` |
| 入侵事件 | `Main::InvasionWarning` 广播前 | `[EVENT] 哥布林军队已经到达！`（逼近/到达/击退） |
| 城镇 NPC 到达 | `WorldGen::SpawnHomelessNPC` / `SpawnTravelNPC` / `SpawnTownNPC` | `[NPC] 旅商 已到达！` |
| 日食 | `Main::UpdateTime_StartDay`（`eclipse = true`） | `[EVENT] 日食发生，今天白天也会刷怪！` |
| Boss 苏醒 | `NPC::SpawnOnPlayer` | `[EVENT] 克苏鲁之眼 已苏醒！`（中文名） |
| Boss 被击败 | `NPC::DoDeathEvents_CelebrateBossDeath` | `[EVENT] 血肉墙 已被击败！`（中文名） |

注入方式统一为"在目标调用前插入 `Console.WriteLine(...)` 序列"，不改变原有控制流。

## 4. 重新构建与部署

```bash
# ① 构建补丁程序
cd terraria-server/ilpatch
dotnet build -c Release

# ② 生成补丁版服务端（第一个参数=原版 exe，第二个=输出）
dotnet bin/Release/net8.0/ilpatch.dll TerrariaServer.exe TerrariaServer.patchedX.exe
#    输出中每项补丁应显示 OK；出现 WARN 需先排查

# ③ 上传并替换（会踢掉在线玩家）
scp TerrariaServer.patchedX.exe <user>@<server>:/tmp/
ssh <user>@<server>
  sudo systemctl stop terraria
  sudo cp /opt/terraria/1458/Linux/TerrariaServer.exe /opt/terraria/1458/Linux/TerrariaServer.exe.bak_$(date +%Y%m%d_%H%M%S)
  sudo cp /tmp/TerrariaServer.patchedX.exe /opt/terraria/1458/Linux/TerrariaServer.exe
  sudo chmod 755 /opt/terraria/1458/Linux/TerrariaServer.exe
  sudo systemctl start terraria
  journalctl -u terraria -n 20 --no-pager     # 确认 Server started，无 InvalidProgramException
```

**回滚**：`systemctl stop terraria` → 用备份覆盖 → `start`。

## 5. 踩过的坑（重要）

### 5.1 mono 的 IL 验证比 CoreCLR 严格

服务器在 mono/FNet 下运行，JIT 验证会拒绝"能通过 dnlib 写出但栈不平衡"的方法。

**症状**：`System.InvalidProgramException` → 服务端启动即崩。

**两个真实的栈错误**（双色着色补丁，patched8 阶段）：

1. 粉色分支段末尾**缺少跳过绿色段的跳转** → 执行完粉色又落入绿色，栈多压一层；
2. 第二段判断（`<小T>` 前缀）前**忘记重新压入待检字符串** → `"<小T>"` 被当作 `this`。

补上跳转与第二次压栈后即通过。**结论：手写 IL 时必须逐条核对每个执行路径的栈平衡，`MaxStack` 不是根因。**

### 5.2 分支 opcode 的构造

`Instruction.Create(OpCodes.Brtrue_S)` 会因缺少操作数抛异常，需显式传 `(Instruction)null` 占位后再回填目标：

```csharp
var b1 = Instruction.Create(OpCodes.Brtrue_S, (Instruction)null);
// ... 稍后 b1.Operand = target;
```

插入新指令后原有短跳转可能越界，写回前统一调用 `method.Body.SimplifyBranches()` 放宽为长跳转。

### 5.3 目标方法可能不在你以为的类里

`HasBeenDefeated` 广播**不在** `NPC::NPCLoot` 中，而在 `NPC::DoDeathEvents_CelebrateBossDeath`。定位注入点时需按方法边界核对，不要凭行距猜测。

### 5.4 无分支方案有时更安全

按名字双色（分支）曾多次被 mono 拒绝。若只需单色，把 `ldsfld ChatColors.ServerMessage` 直接替换为 `ldc.i4 ×3 + newobj Color`（栈进出不变、无跳转）即可稳定通过。

## 6. 版本跟进

Terraria 客户端更新后服务端必须同版本，否则玩家全部无法连接。流程：

1. `scripts/update.sh --check` 检查官方是否发布新版；
2. `scripts/update.sh <版本号>` 更新服务端；
3. **重新打补丁**：注入点方法名/签名可能随版本变化，需按 §4 重建并逐项确认输出 OK；
4. 若有失效注入点，对照 `decomp/`（本地反编译源码）重新定位。

## 7. 可扩展方向

- **死亡死法**：`Player::KillMe` 已能拿到 `PlayerDeathReason`（NPC/玩家/弹幕/自定义原因），注入死法需要更复杂的 IL（读取结构体字段并拼接字符串），当前未做。
- **更多事件**：需要先在 `decomp/` 里定位"服务端已知但未输出"的广播点，再按相同模式注入。
- **第三个 AI**：着色分支需扩展；同时新增 env、persona 与 systemd 单元即可（代码无需改动）。
