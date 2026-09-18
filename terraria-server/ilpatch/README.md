# ilpatch · Terraria 服务端 IL 插桩补丁

用 [dnlib](https://github.com/0xd4d/dnlib) 给 Terraria 专用服务端做 IL 插桩，让它把游戏事件写进 stdout、并支持按发言者名字着色。

完整的注入点清单、原理与踩坑记录见 **[../../docs/il-patch.md](../../docs/il-patch.md)**。

## 快速构建

```bash
dotnet build -c Release

# 生成补丁版服务端（参数：原版 exe → 输出 exe）
dotnet bin/Release/net8.0/ilpatch.dll TerrariaServer.exe TerrariaServer.patched.exe
```

输出中每项补丁应显示 `OK`，出现 `WARN` 表示该注入点未命中（通常是版本不匹配导致方法名/结构变化，需要用反编译源码重新定位）。

## 重要说明

- 本目录**只包含补丁程序源码**。
- `TerrariaServer.exe`（原版）与生成的 `TerrariaServer.patched*.exe` 受 Re-Logic 版权保护、且单文件约 26MB，**不随仓库分发**（见根目录 `.gitignore`）。
- 请自行通过官方渠道获取对应版本的服务端后本地打补丁。

## 注入能力速览

| 补丁 | 目标方法 |
|---|---|
| 昼夜 / 日食 | `Main::UpdateTime_StartDay`、`StartNight` |
| 血月 | `Main::UpdateTime_StartNight` |
| 玩家死亡 | `Player::KillMe` |
| Say 纯文本化 + 按名着色 | `Main::startDedInputCallBack` |
| 入侵事件 | `Main::InvasionWarning` |
| 城镇 NPC 到达 | `WorldGen::SpawnHomelessNPC` / `SpawnTravelNPC` / `SpawnTownNPC` |
| Boss 苏醒 / 击败 | `NPC::SpawnOnPlayer` / `NPC::DoDeathEvents_CelebrateBossDeath` |
