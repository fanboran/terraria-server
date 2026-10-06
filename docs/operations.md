# 运维手册

## 1. 服务速查

```bash
systemctl status terraria terraria-ai terraria-ai-tt terraria-stats terraria-monitor

journalctl -u terraria -f            # 游戏服日志（事件源；AI 与看板同源）
journalctl -u terraria-ai -f         # 扣哒调试输出（[AI hh:mm:ss] 前缀）
journalctl -u terraria-ai-tt -f      # 小T
journalctl -u terraria-stats -f      # 看板后端

sudo systemctl restart terraria-ai terraria-ai-tt    # 重启 AI（不影响游戏服与玩家）
sudo systemctl restart terraria-stats                # 重启看板
sudo systemctl restart terraria                      # 重启游戏服（会踢掉在线玩家）
```

## 2. 控制台注入（FIFO）

```bash
echo save > /opt/terraria/console.fifo                  # 立即存档
echo 'say 大家好' > /opt/terraria/console.fifo           # 全服广播
echo 'time' > /opt/terraria/console.fifo                # 原版命令均可
echo exit > /opt/terraria/console.fifo                  # 优雅停服（同 systemctl stop）
```

> FIFO 由 `start.sh` 创建并 `tail -f` 喂给服务端 stdin——**保持 stdin 常开可避免服务端空转烧 CPU**，同时提供外部注入通道。

## 3. 备份与恢复

```bash
# 自动：每日 04:00 打包（先 save 落盘再打包），保留 14 天
ls /opt/terraria/backups/

# 手动立即备份
bash /opt/terraria/backup.sh

# 恢复世界
sudo systemctl stop terraria
cd /opt/terraria/.local/share/Terraria/Worlds/
sudo tar xzf /opt/terraria/backups/world_YYYYMMDD_HHMM.tar.gz
sudo chown terraria:terraria -R .
sudo systemctl start terraria

# 拉一份到本地
scp <user>@<server>:/opt/terraria/backups/world_latest.tar.gz ./backups/
```

## 4. 网络调优（跨境/高丢包链路）

写入 `/etc/sysctl.d/99-terraria-net.conf`：

```ini
# 幽灵会话快速回收：断线后 ~90 秒清除（默认 7200 秒），
# 避免"重连被拒：already on this server"长时间无法进入
net.ipv4.tcp_keepalive_time=60
net.ipv4.tcp_keepalive_intvl=10
net.ipv4.tcp_keepalive_probes=6

# 拥塞控制：BBR 不依赖丢包信号建模带宽，跨境高丢包链路上吞吐与稳定性显著优于 CUBIC
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
```

```bash
sudo sysctl --system     # 立即生效；对新建连接生效，无需重启游戏服
sysctl net.ipv4.tcp_congestion_control net.core.default_qdisc   # 确认
```

**效果与边界**：可缩短断线后的残留会话时间、提升丢包链路的连接存活率；但**丢包发生在玩家到服务器之间的路径上**，服务器端无法改变路径。若玩家仍频繁掉线，剩余手段在客户端侧（游戏加速器）。

## 5. 监控与诊断

```bash
bash scripts/monitor.sh      # 链路质量采样（由 terraria-monitor.service 常驻）
bash scripts/bwcheck.sh      # 带宽占用核查（读 /proc/net/dev）
bash scripts/tc.sh           # 控制台安全启停工具
bash scripts/panic.sh        # DDoS 应急止血（仅在确认被攻击时使用）
```

排查玩家的连接问题：

```bash
# 最近连接/断开事件（谁、何时、原因）
journalctl -u terraria --since "30 min ago" --no-pager | grep -E "connecting|joined|lost|has left|was booted"

# 当前在线连接与拥塞算法
ss -tn state established '( sport = :7777 )'
ss -tni state established '( sport = :7777 )' | grep -o 'bbr\|cubic'

# 某 IP 归属（判断是否跨境链路）
curl -s "http://ip-api.com/json/<IP>?lang=zh-CN"
```

| 日志现象 | 含义 |
|---|---|
| `X was booted: Incorrect password` | 密码输错（不是被墙） |
| `X was booted: Y is already on this server` | 存在残留会话；等 keepalive 回收（约 90 秒） |
| `X was booted: This server is full right now` | 槽位被占——**先查是不是垃圾连接占槽**（见 §5.1） |
| `X lost connection...` | 连接异常中断（客户端网络/链路问题居多） |
| 同一 IP 反复 connecting/booted | 客户端网络抖动或握手包丢失 |

### 5.1 地区白名单（境外扫描器防护，2026-10-06 上线）

**背景**：7777 端口长期被境外扫描器盯上，它们建立 TCP 连接后只发垃圾包，会占满 `maxplayers=8` 的槽位，导致真人被拒"server is full"。

**机制**：`ipset cn_hk` 白名单（中国大陆 + 香港全部 IP 段，来自 APNIC 官方分配数据）+ iptables 规则：**源 IP 不在白名单内访问 7777（TCP/UDP）直接 DROP**。

```bash
# 手动更新白名单（数据源 APNIC，每周一 05:00 由 timer 自动执行）
sudo python3 /opt/terraria/scripts/update_cn_ipset.py            # 完整更新（下载+重建+应用）
sudo python3 /opt/terraria/scripts/update_cn_ipset.py --rules-only  # 只应用 iptables 规则

# 查看状态
sudo ipset list cn_hk | grep "Number of entries"   # 当前 11631 段
sudo iptables -S INPUT | grep match-set             # 运行时规则

# 海外朋友要来玩？把 IP/CIDR 写进例外文件（下次更新后生效），或临时手动加：
echo "<IP>" | sudo tee -a /opt/terraria/scripts/cn_hk_overrides.txt
sudo ipset add cn_hk <IP>
```

**组件**：
- `/etc/ipset-terraria.conf` — ipset 恢复文件（原子生成，下载失败不清空现有白名单）
- `terraria-ipset.service` — 开机恢复 ipset（`Before=ufw.service`，先于白名单规则加载）
- `terraria-geoip-update.timer` — 每周一 05:00 更新
- `/etc/ufw/before.rules` — DROP 规则（ufw reload/重启后自动加载）
- `cn_hk_overrides.txt` — 手动例外（海外玩家 IP）

**注意事项**：
- 玩家若漫游到境外网络（出国旅行换网络），IP 不在白名单内会进不来——加进 overrides 即可
- 白名单生效期间 **不要清空 cn_hk 集合**（等于封禁所有人）；更新脚本采用原子写入规避

## 6. 部署与同步

**本地仓库 → 线上运行态**（二者非镜像）：

| 仓库路径 | 线上目标 |
|---|---|
| `terraria-server/src/terraria_ai.py` | `/opt/terraria/scripts/terraria_ai.py` |
| `terraria-server/src/stats_server.py` | `/opt/terraria/scripts/stats_server.py` |
| `terraria-server/src/dashboard.html` | `/opt/terraria/scripts/dashboard.html` |
| `terraria-server/personas/persona.txt` | `/opt/terraria/persona.txt` |
| `terraria-server/personas/persona2.txt` | `/opt/terraria/persona2.txt` |
| `terraria-server/scripts/*.sh` | `/opt/terraria/scripts/`（`start.sh`、`backup.sh` 在 `/opt/terraria/`） |
| `terraria-server/systemd/*` | `/etc/systemd/system/` |
| `terraria-server/examples/*.env.example` | 复制为 `/opt/terraria/ai.env`、`ai2.env` 后填真实值 |

标准流程：

```bash
scp <文件> <user>@<server>:/tmp/
ssh <user>@<server> '
  sudo cp /tmp/<文件> <目标路径> &&
  sudo chown ubuntu:ubuntu <目标路径> &&
  sudo systemctl restart <相关服务>'
```

**线上 → 仓库**：仅源码类文件需要回拉（如线上临时调试后）；运行态数据（存档、备份、密钥、记忆、配色）**不回拉、不入库**。

## 7. 每日自动任务

| 时间 | 任务 | 实现 |
|---|---|---|
| 04:00 | 世界自动备份（保留 14 天） | `backup.sh` + 定时器/计划任务 |
| 04:30 | 重启双 AI（顺带触发记忆提炼） | `terraria-ai-daily-restart.timer` |

## 8. 故障速查

| 现象 | 根因 | 处理 |
|---|---|---|
| AI 完全无反应、日志无 `[AI ` 行 | journald 时间戳带 `+0800`，Python 3.10 `fromisoformat` 解析失败 | 已修：改用 `strptime("%Y-%m-%dT%H:%M:%S%z")` |
| 点名不回话 | 服务端聊天行带 `: ` 前缀未被识别 | 已修：正则容忍 `:?` 前缀 |
| AI 说话夹带思考内容 | 混合推理模型输出 `<think>` 泄漏 | 已修：`llm_reply()` 输出层剥离 |
| 看板翻不动/颜色丢失 | 单线程 + journalctl 全量扫描阻塞 | 已修：内存切片 + `ThreadingHTTPServer` |
| 看板看不到更早历史 | 缓存上限过小 + 重启只回填 600 行 | 已修：缓存 5 万行 + 全量回填 |
| 广播是黄色 | 原版 `say` 用 `ServerMessage` 色 | 需 IL 补丁着色（见 il-patch.md） |
| 服务端起不来（InvalidProgramException） | IL 补丁栈不平衡 | 回滚 exe，按 il-patch.md §5 排查 |
| 更换世界/密码 | 需改服务端配置 | 编辑 `/opt/terraria/serverconfig.txt` 后重启 `terraria` |

## 9. 安全建议

1. **游戏密码务必高强度**——公网 7777 端口存在持续扫描与撞库尝试；日志中可见错误密码连接记录。
2. **SSH 仅密钥登录**，禁用密码；密钥文件绝不入库。
3. **LLM API Key 权限 600**，`ai.env` / `ai2.env` 不进版本库。
4. **面板管理令牌**存于 `/opt/terraria/scripts/.api_token`，不要写入前端或文档。
5. 定期 `apt upgrade`；云安全组只放行必要端口（SSH / 7777 / 面板按需）。
