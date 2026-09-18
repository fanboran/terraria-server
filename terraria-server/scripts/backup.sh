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
