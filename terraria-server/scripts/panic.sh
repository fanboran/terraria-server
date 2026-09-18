#!/usr/bin/env bash
#
# DDoS 应急止血脚本
# 策略：不抗攻击。攻击打进来就直接封游戏端口，让流量落在腾讯云基础防护上，
#       代价是服务暂时不可用——非生产环境，可用性让位于机器稳定。
#
# 用法：
#   bash panic.sh lockdown   一键封禁游戏端口（不动 SSH，管理通道永远保留）
#   bash panic.sh restore    攻击结束后恢复端口
#
set -euo pipefail

GAME_PORTS="7777/tcp 7777/udp"

case "${1:-}" in
  lockdown)
    for p in $GAME_PORTS; do
      # insert 1 是关键：插到规则链最前面，否则会被已有的 allow 规则先命中而失效
      sudo ufw insert 1 deny "$p" >/dev/null && echo "已封禁 $p"
    done
    sudo ufw status numbered | grep -E "7777|DENY"
    echo ""
    echo "游戏端口已全部封禁，SSH 22 未动。攻击结束后执行: $0 restore"
    ;;
  restore)
    for p in $GAME_PORTS; do
      sudo ufw delete deny "$p" >/dev/null 2>&1 && echo "已解封 $p" || echo "$p 无封禁规则，跳过"
    done
    sudo ufw status | grep 7777 || echo "7777 已无 deny 规则，allow 生效中"
    ;;
  *)
    echo "用法: bash $0 {lockdown|restore}" >&2
    exit 1
    ;;
esac
