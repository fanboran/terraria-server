#!/usr/bin/env bash
#
# Terraria 链路质量监控采样器
# 每 30 秒记录：时间 | 在线连接 | 主线程CPU% | 游戏连接重传率% | 游戏连接流量KB/s | 系统总上行KB/s
#
# 读数解释（看卡顿时段的行）：
#   game_retr_pct > 3%   → 玩家到服务器的链路在丢包，实锤网络问题（与带宽无关）
#   game_retr_pct < 1%   → 链路健康，卡顿另有原因（游戏版本 bug / 客户端侧）
#   game_tx_kbs          → 纯游戏流量（不含 SSH 等干扰），对照 375KB/s 上限
#
# 日志：/opt/terraria/logs/monitor.log（每日 04:30 截断保留最近 1 万行）
#
set -u
INTERVAL=30
LOG_DIR=/opt/terraria/logs
LOG=$LOG_DIR/monitor.log
IFACE=eth0
mkdir -p "$LOG_DIR"
[ -f "$LOG" ] || echo "time,online,main_thread_cpu,game_retr_pct,game_tx_kbs,sys_tx_kbs" >> "$LOG"

# 7777 端口所有连接的 retrans / segs_out / bytes_sent 求和（ss -ti 按连接统计）
get_game() {
  ss -tin state established "( sport = :7777 )" 2>/dev/null \
    | grep -oP '(retrans:\d+|segs_out:\d+|bytes_sent:\d+)' \
    | awk -F: '{a[$1]+=$2} END{print a["retrans"]+0, a["segs_out"]+0, a["bytes_sent"]+0}'
}
get_dev() { awk -v i="${IFACE}:" '$1==i {print $2, $10}' /proc/net/dev; }

set -- $(get_game); r0=$1; s0=$2; b0=$3
set -- $(get_dev); rx0=$1; tx0=$2

while true; do
  ts=$(date '+%F %T')
  online=$(ss -tn state established "( sport = :7777 )" 2>/dev/null | tail -n +2 | wc -l)

  pid=$(pgrep -f TerrariaServer.bin.x86_64 | head -1)
  cpu=0
  [ -n "$pid" ] && cpu=$(ps -L -p "$pid" -o pcpu= --sort=-pcpu | head -1 | tr -d ' ')
  [ -z "$cpu" ] && cpu=0

  set -- $(get_game); r1=$1; s1=$2; b1=$3
  set -- $(get_dev); rx1=$1; tx1=$2

  rpct=0
  if [ "$s1" -gt "$s0" ] 2>/dev/null; then
    rpct=$(awk -v r=$((r1 - r0)) -v s=$((s1 - s0)) 'BEGIN{ if(s>0 && r>=0) printf "%.2f", r*100/s; else print "0" }')
  fi
  gkbs=$(awk -v b="$b0" -v c="$b1" -v t="$INTERVAL" 'BEGIN{ if(c>=b) printf "%.1f", (c-b)/t/1024; else print 0 }')
  tkbs=$(awk -v a="$tx0" -v b="$tx1" -v t="$INTERVAL" 'BEGIN{ if(b>=a) printf "%.1f", (b-a)/t/1024; else print 0 }')

  echo "$ts,$online,$cpu,$rpct,$gkbs,$tkbs" >> "$LOG"

  r0=$r1; s0=$s1; b0=$b1; rx0=$rx1; tx0=$tx1
  sleep "$INTERVAL"
done
