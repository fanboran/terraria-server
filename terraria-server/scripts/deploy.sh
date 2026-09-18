#!/usr/bin/env bash
#
# Terraria 官方原版专用服务端 —— 一键部署脚本
# 目标环境：腾讯云轻量应用服务器 / Ubuntu 22.04 或 24.04 / 4C4G / 3Mbps
# 适用场景：5 人左右原版生存
#
# 用法（全部参数可选，不传则用默认值）：
#   sudo TERRARIA_VERSION=1458 SERVER_PASSWORD=你的密码 WORLD_NAME=FanboWorld bash deploy.sh
#
set -euo pipefail

# ---------- 可调参数 ----------
VER="${TERRARIA_VERSION:-1458}"          # 服务端包版本号，1458 = Terraria 1.4.5.8
SVC_USER="terraria"
HOME_DIR="/opt/terraria"
WORLD_NAME="${WORLD_NAME:-FanboWorld}"
SERVER_PASSWORD="${SERVER_PASSWORD:-change_me_please}"
MAX_PLAYERS="${MAX_PLAYERS:-8}"
WORLD_SIZE="${WORLD_SIZE:-2}"            # 1=小 2=中 3=大。3Mbps 带宽下务必用 2，别开大世界
DIFFICULTY="${DIFFICULTY:-1}"            # 0=普通 1=专家 2=大师 3=旅程
NPC_STREAM="${NPC_STREAM:-60}"           # NPC 同步上限：官方默认 60，实测带宽吃紧再降（30 省近一半流量，代价是远处怪跳帧）
PORT="${PORT:-7777}"

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root 运行：sudo bash deploy.sh" >&2
  exit 1
fi

echo "==> [1/7] 安装依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq wget unzip tmux curl

echo "==> [2/7] 创建服务账号 $SVC_USER"
if ! id -u "$SVC_USER" >/dev/null 2>&1; then
  useradd -r -m -d "$HOME_DIR" -s /bin/bash "$SVC_USER"
fi
mkdir -p "$HOME_DIR/.local/share/Terraria/Worlds" "$HOME_DIR/backups"
# 目录可能由 root 提前创建，useradd -m 对已存在目录不会改属主，必须显式 chown
chown -R "$SVC_USER":"$SVC_USER" "$HOME_DIR"

echo "==> [3/7] 下载官方服务端 terraria-server-$VER.zip"
# 注意：bash -c 不继承外层 set -e，必须显式开启，否则下载失败会静默继续
# 注意：用 curl 而非 wget —— 云服务器 DNS 常只回 IPv6，wget 不回退 IPv4，curl 会
runuser -u "$SVC_USER" -- bash -c "
  set -e
  cd '$HOME_DIR'
  if [ ! -s 'terraria-server-$VER.zip' ]; then
    curl -fsSL --retry 3 --retry-delay 2 --max-time 600 -o 'terraria-server-$VER.zip' 'https://terraria.org/api/download/pc-dedicated-server/terraria-server-$VER.zip'
  fi
  unzip -oq 'terraria-server-$VER.zip'
  chmod +x '$HOME_DIR/$VER/Linux/TerrariaServer.bin.x86_64'
"
SERVER_DIR="$HOME_DIR/$VER/Linux"
if [ ! -f "$SERVER_DIR/TerrariaServer.bin.x86_64" ]; then
  echo "错误：未找到服务端二进制 $SERVER_DIR/TerrariaServer.bin.x86_64" >&2
  exit 1
fi
echo "$VER" > "$HOME_DIR/.version"

echo "==> [4/7] 校验世界文件并写入 serverconfig.txt"
if [ ! -f "$HOME_DIR/.local/share/Terraria/Worlds/$WORLD_NAME.wld" ]; then
  echo "错误：世界文件不存在：$HOME_DIR/.local/share/Terraria/Worlds/$WORLD_NAME.wld" >&2
  echo "1.4.5.5+ 的 autocreate 有官方确认的 bug，不能依赖服务端自动建世界。" >&2
  echo "请先把 .wld 放到该目录后重跑本脚本。" >&2
  exit 1
fi
cat > "$HOME_DIR/serverconfig.txt" <<CFG
# 注意：不要写入 autocreate/difficulty/worldname —— 1.4.5.5+ 有未文档化变更，
# 只要配置里出现 autocreate（任意值），服务端会用它推断世界尺寸而不是读文件，
# 尺寸不匹配时多人区块流式传输损坏（表现为大片区域不加载）。官方确认的修复：不写。
world=$HOME_DIR/.local/share/Terraria/Worlds/$WORLD_NAME.wld
worldpath=$HOME_DIR/.local/share/Terraria/Worlds/
maxplayers=$MAX_PLAYERS
port=$PORT
password=$SERVER_PASSWORD
motd=Welcome to $WORLD_NAME
language=zh-CN
npcstream=$NPC_STREAM
priority=1
secure=1
upnp=0
CFG

echo "==> [5/7] 写入启动器 start.sh（FIFO 保持 stdin，支持外部注入命令）"
cat > "$HOME_DIR/start.sh" <<'SH'
#!/bin/bash
# Terraria 服务端启动器
# 用 FIFO 保持 stdin 常开（否则服务端空转烧 CPU），同时支持从外部注入控制台命令
TERRARIA_HOME="/opt/terraria"
SERVER_DIR="__SERVER_DIR__"
FIFO="$TERRARIA_HOME/console.fifo"
[ -p "$FIFO" ] || mkfifo -m 666 "$FIFO"
chmod 666 "$FIFO" 2>/dev/null || true
cd "$SERVER_DIR"
tail -f "$FIFO" | ./TerrariaServer.bin.x86_64 -config "$TERRARIA_HOME/serverconfig.txt"
SH
sed -i "s|__SERVER_DIR__|$SERVER_DIR|" "$HOME_DIR/start.sh"

echo "==> [6/7] 写入 systemd 单元 terraria.service"
cat > /etc/systemd/system/terraria.service <<UNIT
[Unit]
Description=Terraria Dedicated Server (vanilla $VER)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SVC_USER
WorkingDirectory=$HOME_DIR
ExecStart=$HOME_DIR/start.sh
ExecStop=/bin/sh -c 'echo exit > $HOME_DIR/console.fifo'
TimeoutStopSec=90
Restart=on-failure
RestartSec=20

[Install]
WantedBy=multi-user.target
UNIT

echo "==> [7/7] 写入备份脚本并设置每日凌晨 4 点自动备份"
cat > "$HOME_DIR/backup.sh" <<'SH'
#!/bin/bash
# Terraria 世界自动备份：先让服务端落盘，再打包，保留 14 天
TERRARIA_HOME="/opt/terraria"
FIFO="$TERRARIA_HOME/console.fifo"
SRC="$TERRARIA_HOME/.local/share/Terraria/Worlds"
DST="$TERRARIA_HOME/backups"
mkdir -p "$DST"

if [ -p "$FIFO" ]; then
  echo "save" > "$FIFO"
  sleep 10
fi

STAMP=$(date +%Y%m%d_%H%M)
tar czf "$DST/world_$STAMP.tar.gz" -C "$SRC" .
find "$DST" -name 'world_*.tar.gz' -mtime +14 -delete
echo "[$(date '+%F %T')] backup done: world_$STAMP.tar.gz"
SH
sed -i "s|/opt/terraria|$HOME_DIR|g" "$HOME_DIR/backup.sh"

chmod +x "$HOME_DIR/start.sh" "$HOME_DIR/backup.sh"
chown -R "$SVC_USER":"$SVC_USER" "$HOME_DIR"

printf '0 4 * * * %s %s/backup.sh >> %s/backups/backup.log 2>&1\n' \
  "$SVC_USER" "$HOME_DIR" "$HOME_DIR" > /etc/cron.d/terraria-backup
chmod 644 /etc/cron.d/terraria-backup

if command -v ufw >/dev/null 2>&1; then
  ufw allow 22/tcp
  ufw allow "${PORT}/tcp"
  ufw allow "${PORT}/udp"
  echo "提示：已放行 22/${PORT}，请确认 SSH 已连通后再手动执行 ufw enable"
fi

systemctl daemon-reload
systemctl enable --now terraria

cat <<'TIP'

======================== 部署完成 ========================

常用命令：
  systemctl status terraria          查看运行状态
  journalctl -u terraria -f          实时看服务端日志
  echo save > /opt/terraria/console.fifo    手动存档
  echo "say 服务器5分钟后重启" > /opt/terraria/console.fifo    全服广播

还剩两件事必须做：
  1. 腾讯云控制台 -> 防火墙/安全组 -> 放行 TCP 7777（和 UDP 7777）
  2. 确认 SSH 可用后执行 ufw enable

连接地址：<你的服务器公网IP>:7777

注意：Terraria 服务端与客户端版本必须严格一致。
客户端更新后，重跑本脚本并修改 TERRARIA_VERSION 即可跟进，
世界文件向后兼容，但无法回退，更新前先跑一次 /opt/terraria/backup.sh。
==========================================================
TIP
