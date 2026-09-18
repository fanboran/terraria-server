#!/usr/bin/env bash
#
# Terraria 服务端版本跟进脚本
#
# 背景：Terraria 服务端与客户端版本必须严格一致，客户端一更新，所有人就都连不上了。
#       官方原版服务端由 Re-Logic 同步发布，所以跟进只需要换一个 zip。
#       但世界文件向后不兼容回退 —— 更新前必须备份，且本脚本拒绝降级。
#
# 用法：
#   sudo bash update.sh --check              检查官方是否已发布更新版本
#   sudo bash update.sh 1459                 更新到指定包版本
#   sudo bash update.sh 1459 --dry-run       只演练，不实际改动
#   sudo bash update.sh 1459 -y              跳过确认（配合 --check 做自动化）
#
set -euo pipefail

HOME_DIR="${TERRARIA_HOME:-/opt/terraria}"
SVC_USER="terraria"
SVC_NAME="terraria"
UNIT="/etc/systemd/system/${SVC_NAME}.service"
VERSION_FILE="$HOME_DIR/.version"
BACKUP_SCRIPT="$HOME_DIR/backup.sh"
BASE_URL="https://terraria.org/api/download/pc-dedicated-server"

usage() {
  cat <<'USAGE'
用法：
  sudo bash update.sh --check              检查官方是否已发布更新版本
  sudo bash update.sh 1459                 更新到指定包版本
  sudo bash update.sh 1459 --dry-run       只演练，不实际改动
USAGE
  exit 0
}

# 包版本号 -> 游戏版本号（1458 -> 1.4.5.8，1460 -> 1.4.6.0）
pretty_ver() {
  local v="$1"
  if [ "$v" -ge 1400 ] && [ "$v" -lt 1500 ]; then
    local rest=$((v - 1400))
    echo "1.4.$((rest / 10)).$((rest % 10))"
  else
    echo "包版本 $v"
  fi
}

# 探测官方是否已发布该包版本
remote_exists() {
  local v="$1" url="$BASE_URL/terraria-server-$v.zip"
  if command -v curl >/dev/null 2>&1; then
    [ "$(curl -s -o /dev/null -w '%{http_code}' -I --max-time 20 "$url" 2>/dev/null || echo 000)" = "200" ]
  else
    wget -q --spider --timeout=20 "$url" >/dev/null 2>&1
  fi
}

current_version() {
  if [ -f "$VERSION_FILE" ]; then
    cat "$VERSION_FILE"
  else
    # 兼容早期部署：从 start.sh 里反解
    sed -n 's|.*SERVER_DIR="'"$HOME_DIR"'/\([0-9]*\)/Linux".*|\1|p' "$HOME_DIR/start.sh" 2>/dev/null | head -1
  fi
}

do_check() {
  local cur="$1" v found=0 i
  echo "当前：$(pretty_ver "$cur")  (包版本 $cur)"
  echo "正在探测官方新版本..."
  for i in 1 2 3; do
    v=$((cur + i))
    if remote_exists "$v"; then
      echo "  [有新版本] $(pretty_ver "$v")  包版本 $v"
      echo "             -> sudo bash $0 $v"
      found=1
    fi
  done
  if [ "$found" -eq 0 ]; then
    echo "  官方暂未发布新版本，当前已是最新。"
  fi
  return 0
}

do_update() {
  local cur="$1" new="$2" dry="$3" auto="$4" ans

  if [ "$new" -le "$cur" ]; then
    echo "错误：目标包版本 $new 不高于当前 $cur。" >&2
    echo "世界文件无法回退，本脚本拒绝降级。" >&2
    exit 1
  fi

  echo "==> 检查官方发布状态：terraria-server-$new.zip"
  if ! remote_exists "$new"; then
    echo "错误：官方尚未发布 $(pretty_ver "$new")，或网络不可达。" >&2
    exit 1
  fi

  cat <<INFO

更新计划
  版本：$(pretty_ver "$cur")  ->  $(pretty_ver "$new")
  目录：$HOME_DIR/$new/Linux
  流程：备份世界 -> 停服 -> 下载 -> 切路径 -> 启服

INFO

  if [ "$dry" -eq 1 ]; then
    echo "[dry-run] 演练结束，未做任何改动。"
    return 0
  fi

  if [ "$auto" -ne 1 ]; then
    read -r -p "确认继续？[y/N] " ans || ans=""
    case "$ans" in
      y|Y|yes|YES) ;;
      *) echo "已取消。"; exit 0 ;;
    esac
  fi

  echo "==> [1/6] 备份当前世界"
  if [ -x "$BACKUP_SCRIPT" ]; then
    runuser -u "$SVC_USER" -- "$BACKUP_SCRIPT"
  else
    echo "警告：未找到 $BACKUP_SCRIPT，跳过备份（不推荐）" >&2
  fi

  echo "==> [2/6] 停止服务"
  systemctl stop "$SVC_NAME"

  echo "==> [3/6] 下载 terraria-server-$new.zip"
  # curl 优先：云服务器 DNS 常只回 IPv6，wget 不回退 IPv4
  runuser -u "$SVC_USER" -- bash -c "
    set -e
    cd '$HOME_DIR'
    [ -s 'terraria-server-$new.zip' ] || curl -fsSL --retry 3 --retry-delay 2 --max-time 600 -o 'terraria-server-$new.zip' '$BASE_URL/terraria-server-$new.zip'
    unzip -oq 'terraria-server-$new.zip'
    chmod +x '$HOME_DIR/$new/Linux/TerrariaServer.bin.x86_64'
  "

  echo "==> [4/6] 校验新二进制"
  if [ ! -f "$HOME_DIR/$new/Linux/TerrariaServer.bin.x86_64" ]; then
    echo "错误：新版本二进制缺失，正在回滚到 $cur" >&2
    systemctl start "$SVC_NAME"
    exit 1
  fi

  echo "==> [5/6] 切换启动路径并重载 systemd"
  sed -i "s|SERVER_DIR=\"$HOME_DIR/[0-9]*/Linux\"|SERVER_DIR=\"$HOME_DIR/$new/Linux\"|" "$HOME_DIR/start.sh"
  sed -i "s|Terraria Dedicated Server (vanilla [0-9]*)|Terraria Dedicated Server (vanilla $new)|" "$UNIT"
  echo "$new" > "$VERSION_FILE"
  chown "$SVC_USER":"$SVC_USER" "$VERSION_FILE" "$HOME_DIR/start.sh"
  systemctl daemon-reload

  echo "==> [6/6] 启动服务"
  systemctl start "$SVC_NAME"
  sleep 6
  systemctl --no-pager status "$SVC_NAME" | head -20

  cat <<TIP

更新完成：$(pretty_ver "$cur") -> $(pretty_ver "$new")

请确认所有玩家客户端也已更新到 $(pretty_ver "$new")，否则无法连接。
若出现异常，回滚步骤：
  systemctl stop terraria
  sed -i "s|SERVER_DIR=\"$HOME_DIR/[0-9]*/Linux\"|SERVER_DIR=\"$HOME_DIR/$cur/Linux\"|" $HOME_DIR/start.sh
  echo $cur > $VERSION_FILE
  systemctl start terraria
TIP
}

main() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "请用 root 运行：sudo bash $0 ..." >&2
    exit 1
  fi
  [ -f "$HOME_DIR/start.sh" ] || { echo "错误：未找到 $HOME_DIR/start.sh，请先运行 deploy.sh" >&2; exit 1; }

  local dry=0 auto=0 target="" cur
  while [ $# -gt 0 ]; do
    case "$1" in
      --check)   cur=$(current_version); [ -n "$cur" ] || { echo "错误：无法确定当前版本" >&2; exit 1; }; do_check "$cur"; exit 0 ;;
      --dry-run) dry=1 ;;
      -y|--yes)  auto=1 ;;
      -h|--help) usage ;;
      [0-9]*)    target="$1" ;;
      *)         echo "未知参数：$1" >&2; usage ;;
    esac
    shift
  done

  if [ -z "$target" ]; then usage; fi

  cur=$(current_version)
  if [ -z "$cur" ]; then
    echo "错误：无法确定当前版本，检查 $VERSION_FILE" >&2
    exit 1
  fi
  do_update "$cur" "$target" "$dry" "$auto"
}

main "$@"
