#!/usr/bin/env bash
#
# 带宽占用采样器（零依赖，直接读 /proc/net/dev）
#
# 用途：Terraria 服卡顿时的第一诊断工具。
#       腾讯云轻量 3Mbps 带宽 = 375 KB/s 上行上限，
#       5 人原版平时约 100-200 KB/s，血月/BOSS 弹幕齐飞时可能冲到 300-400 KB/s 打满。
#       跑一下这个，就知道该不该加带宽，还是该调 npcstream。
#
# 用法：
#   bash bwcheck.sh           采样 10 秒
#   bash bwcheck.sh 30        采样 30 秒
#   bash bwcheck.sh 10 eth0   指定网卡
#
set -uo pipefail

INTERVAL="${1:-10}"
IFACE="${2:-}"
PORT="${TERRARIA_PORT:-7777}"
LIMIT_KBPS="${BANDWIDTH_LIMIT_KBPS:-375}"   # 3Mbps ≈ 375 KB/s，按实际套餐改

if [ -z "$IFACE" ]; then
  IFACE=$(ip route get 8.8.8.8 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')
fi
[ -n "$IFACE" ] || { echo "错误：无法自动识别网卡，请手动指定：bash $0 10 eth0" >&2; exit 1; }

read_rx() { awk -v i="${IFACE}:" '$1==i {print $2;  f=1} END{if(!f) exit 1}' /proc/net/dev; }
read_tx() { awk -v i="${IFACE}:" '$1==i {print $10; f=1} END{if(!f) exit 1}' /proc/net/dev; }

RX1=$(read_rx) || { echo "错误：读取网卡 $IFACE 失败" >&2; exit 1; }
TX1=$(read_tx)

echo "网卡 $IFACE · 采样 ${INTERVAL}s · 上行上限 ${LIMIT_KBPS} KB/s"
echo "（采样期间最好有人在游戏里，空闲数据没有参考价值）"
echo ""

# 顺便记一下当前连了几个玩家
CONN=$(ss -tn 2>/dev/null | grep ":$PORT" | grep -c ESTAB || echo 0)
echo "当前 7777 端口已建立连接数：$CONN"

sleep "$INTERVAL"

RX2=$(read_rx); TX2=$(read_tx)

# 防御：网卡计数器回绕 / 接口被重置时差值会变负，此时采样无意义
if [ "$(awk -v a="$TX1" -v b="$TX2" 'BEGIN{print (b < a) ? 1 : 0}')" -eq 1 ]; then
  echo "警告：网卡 $IFACE 计数器出现回绕或接口被重置（TX $TX1 -> $TX2），本次采样无效。" >&2
  echo "      请重跑一次；若持续出现，用 bash $0 $INTERVAL <网卡名> 手动指定网卡。" >&2
  exit 1
fi

rx_kbs=$(awk -v a="$RX1" -v b="$RX2" -v t="$INTERVAL" 'BEGIN{printf "%.1f", (b-a)/t/1024}')
tx_kbs=$(awk -v a="$TX1" -v b="$TX2" -v t="$INTERVAL" 'BEGIN{printf "%.1f", (b-a)/t/1024}')
tx_mbps=$(awk -v k="$tx_kbs" 'BEGIN{printf "%.2f", k*8/1024}')
pct=$(awk -v k="$tx_kbs" -v l="$LIMIT_KBPS" 'BEGIN{printf "%.0f", k/l*100}')

echo ""
echo "========== 结果 =========="
echo "下行 RX：${rx_kbs} KB/s"
echo "上行 TX：${tx_kbs} KB/s  (${tx_mbps} Mbps)"
echo "带宽占用：${pct}%"
echo ""

if [ "$pct" -ge 90 ] 2>/dev/null; then
  cat <<'WARN'
[告警] 上行已接近或打满上限 —— 这就是卡顿的直接原因。
按性价比顺序处理：
  1. 把 /opt/terraria/serverconfig.txt 的 npcstream 从 30 再降到 20，重启服
  2. 让大家别同时开多个刷怪场 / 别扎堆放大量弹幕
  3. 世界别用大世界（autocreate=3）
  4. 仍不够就在腾讯云控制台升带宽，或换按流量计费的更高带宽套餐
WARN
elif [ "$pct" -ge 60 ] 2>/dev/null; then
  echo "[注意] 上行已过六成，高峰期（血月、BOSS）大概率会打满。建议调低 npcstream。"
else
  echo "[正常] 上行还有余量，如果仍然卡顿，问题多半不在带宽，"
  echo "       去查 CPU 单核占用（top 按 1）和磁盘 IO。"
fi
