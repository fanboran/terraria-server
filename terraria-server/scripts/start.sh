#!/bin/bash
# Terraria 服务端启动器
# 用 FIFO 保持 stdin 常开（否则服务端空转烧 CPU），同时支持从外部注入控制台命令
TERRARIA_HOME="/opt/terraria"
SERVER_DIR="/opt/terraria/1458/Linux"
FIFO="$TERRARIA_HOME/console.fifo"
[ -p "$FIFO" ] || mkfifo -m 666 "$FIFO"
chmod 666 "$FIFO" 2>/dev/null || true
cd "$SERVER_DIR"
tail -f "$FIFO" | ./TerrariaServer.bin.x86_64 -config "$TERRARIA_HOME/serverconfig.txt"
