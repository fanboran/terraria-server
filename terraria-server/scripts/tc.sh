#!/usr/bin/env bash
#
# Terraria 安全启停工具
# 核心约束：stop 前强制检查在线人数，有人在线一律拒绝（防止把玩家踢下线 / 存档期误杀）
#
# 用法：
#   tc.sh players   查看在线人数
#   tc.sh stop      存档并停止（有人在线则拒绝）
#   tc.sh start     启动
#   tc.sh status    服务状态
#
set -euo pipefail

HOME_DIR="${TERRARIA_HOME:-/opt/terraria}"
FIFO="$HOME_DIR/console.fifo"
PORT="${TERRARIA_PORT:-7777}"

players() {
  sudo ss -tn state established "( sport = :$PORT )" | tail -n +2 | wc -l
}

case "${1:-}" in
  players)
    echo "在线连接数: $(players)"
    ;;
  status)
    systemctl is-active terraria && sudo ss -tlnp | grep ":$PORT" | head -1
    ;;
  start)
    sudo systemctl start terraria
    sleep 3
    echo "Terraria: $(systemctl is-active terraria)"
    ;;
  stop)
    ONLINE=$(players)
    if [ "$ONLINE" -gt 0 ]; then
      echo "拒绝：当前有 $ONLINE 个连接在线。让所有人下线后再执行。" >&2
      exit 1
    fi
    echo "无人在线，存档中..."
    echo "save" > "$FIFO"
    sleep 8
    sudo systemctl stop terraria
    echo "已存档并停止 Terraria（释放约 1.1G 内存）"
    ;;
  *)
    echo "用法: tc.sh {players|status|start|stop}" >&2
    exit 1
    ;;
esac
