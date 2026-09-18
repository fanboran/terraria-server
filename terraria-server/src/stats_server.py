#!/usr/bin/env python3
"""
Terraria 服务器管理 API
端口 19980：
  GET  /stats            只读状态（在线人数/CPU/内存/磁盘/备份）
  GET  /logs?after=<ts>  增量拉取服务端日志/聊天（after 为 Unix 秒，缺省返回最近 150 行）
  POST /cmd              执行控制台命令（需 token，写 FIFO 注入）

安全：/cmd 是写通道，任何拿到 token 的人可完全控制服务器，token 务必保密。
零第三方依赖（纯标准库），以 ubuntu 用户运行。
"""
import collections
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = 19980
TOKEN_FILE = "/opt/terraria/scripts/.api_token"
FIFO = "/opt/terraria/console.fifo"
PAGE_FILE = "/opt/terraria/scripts/dashboard.html"
ITEMS_FILE = "/opt/terraria/scripts/items_map.json"

_PLAYERS_CACHE = {"ts": 0.0, "list": []}
_LOG_RE = re.compile(r"^(\S+)\s+\S+\s+\S+:\s?(.*)$")

# 日志缓存：每行带单调递增 seq，作为前端增量游标。
# 关键：journalctl --since @T 是">=T"，秒级时间戳做游标会反复返回同一秒的行，
# 必须用 (t,msg) 去重 + seq 游标才能保证增量不重复。
_LOG_CACHE = collections.deque(maxlen=50000)  # [{"seq","t","msg"}] 约12天量(4天~1.8万行), 内存~20MB
_LOG_LOCK = threading.Lock()                   # 缓存/回溯互斥（ThreadingHTTPServer 并发请求）
_LOG_KEYS = set()                              # {(t, msg)}
_LOG_SEQ = 0
_CACHE_LAST_TS = None

# 游戏内时间探针：反编译确认服务端有官方只读命令 `Time`
# （输出 "时间：10:30 上午" / "Time: 10:30 AM" 到控制台），每 60s 注入一次即可拿到昼夜状态。
_TIME_CMD_INTERVAL = 60.0
_TIME_CMD_LOCK = threading.Lock()
_TIME_PROBE_LAST = 0.0


def _time_probe():
    """后台线程：周期性向服务器控制台发送 time 命令（只读，无副作用）。"""
    global _TIME_PROBE_LAST
    while True:
        try:
            with _TIME_CMD_LOCK:
                with open(FIFO, "w") as f:
                    f.write("time\n")
                _TIME_PROBE_LAST = time.time()
        except Exception:
            pass
        time.sleep(_TIME_CMD_INTERVAL)


_PROBE_ANCHOR = {"real": 0, "gsec": None}   # 探针锚点：真实时间 → 游戏秒（0=4:30AM）
_ANCHOR_FILE = "/opt/terraria/scripts/probe_anchor.json"
_DAY_NIGHT_CACHE = {"ts": 0.0, "markers": [], "world_day": None, "gstart": None}


def _anchor() -> dict:
    """读取锚点：优先用持久化的首锚点（保证昼夜逆推结果跨刷新稳定），
    没有则退回内存锚点。首锚点在 game_time 首次解析到时落盘。"""
    try:
        with open(_ANCHOR_FILE, encoding="utf-8") as f:
            a = json.load(f)
        if a.get("gsec") is not None:
            return {"real": int(a["real"]), "gsec": int(a["gsec"])}
    except Exception:
        pass
    return dict(_PROBE_ANCHOR)


def _save_anchor():
    try:
        with open(_ANCHOR_FILE, "w", encoding="utf-8") as f:
            json.dump({"real": _PROBE_ANCHOR["real"], "gsec": _PROBE_ANCHOR["gsec"]}, f)
    except Exception:
        pass


def _to_game_sec(h24: float) -> int:
    """游戏小时(4.5=4:30AM 黎明零点) → 游戏秒(0-86400 循环)。"""
    return int(((h24 - 4.5) / 24.0 * 86400.0)) % 86400


def game_time() -> dict:
    """从日志缓存解析最新一条 Time 命令输出。
    昼夜判定（游戏时间 24h 制）：白天 4:30 AM - 7:30 PM（h24 ∈ [4.5, 19.5)），其余为夜晚。"""
    global _PROBE_ANCHOR
    pats = [
        re.compile(r"^:?\s*时间：(\d{1,2}):(\d{2}) (上午|下午)"),
        re.compile(r"^:?\s*Time: (\d{1,2}):(\d{2}) (AM|PM)", re.I),
    ]
    best = None
    for l in reversed(_LOG_CACHE):   # 取缓存中最新一条 Time 输出（deque 头=最旧）
        for p in pats:
            if p.match(l["msg"]):
                best = l
                break
        if best:
            break
    if not best:
        return {"day": None, "time": None, "label": "未知"}
    msg = best["msg"]
    m = pats[0].match(msg)
    if m:
        h24 = int(m.group(1)) % 12 + (12 if m.group(3) == "下午" else 0)
        mm = int(m.group(2))
    else:
        m = pats[1].match(msg)
        h24 = int(m.group(1)) % 12 + (12 if m.group(3).upper() == "PM" else 0)
        mm = int(m.group(2))
    hf = h24 + mm / 60.0
    day = 4.5 <= hf < 19.5
    # 锚点：只在首次解析到时落盘固定（此后保持稳定，昼夜条跨刷新位置不变）；
    # 锚点固定后不再随每次请求移动，锚点之后的实时昼夜切换由前端检测补插
    if _PROBE_ANCHOR["gsec"] is None:
        _PROBE_ANCHOR["real"] = int(time.time())
        _PROBE_ANCHOR["gsec"] = _to_game_sec(hf)
        _save_anchor()
    return {"day": day, "time": f"{h24:02d}:{mm:02d}", "label": "白天" if day else "夜晚"}


def player_intervals():
    """从日志全部 joined/left 构建有玩家活跃区间 [(s, e)]，当前在线者 e=now。"""
    try:
        out = subprocess.run(
            ["sudo", "-n", "journalctl", "-u", "terraria",
             "--output=short-iso", "--no-pager"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception:
        return []
    pat = re.compile(r"^(\S+)\s+\S+\s+\S+:\s*:?\s*(.+?)\s+has\s+(joined|left)\.?\s*$")
    sessions, order = {}, []
    events = []
    for line in out.splitlines():
        m = pat.match(line)
        if not m:
            continue
        iso, name, action = m.group(1), m.group(2), m.group(3)
        ts = _parse_ts(iso)
        if not ts:
            continue
        if action == "joined":
            # 崩溃/重连时会重复 joined 而无 left：保持首次起点不覆盖，
            # 否则会丢失该玩家此前的在线时长（03:07 joined → 03:34 重连 joined 覆盖起点）
            if name not in sessions:
                order.append(name)
                sessions[name] = ts
        else:
            if name in sessions:
                events.append((sessions.pop(name), ts))
    now = int(time.time())
    seen = set()
    for name in order:
        if name in sessions and name not in seen:
            seen.add(name)
            events.append((sessions[name], now))
    return sorted(events)


def _merge_intervals(intervals):
    """合并重叠/相邻区间为并集（多人同时在线会产生重叠区间，
    重叠部分重复模拟会在同一时刻产生成对的伪昼夜切换点，如 day/night 差 13 秒）。"""
    if not intervals:
        return []
    intervals = sorted(intervals)
    merged = []
    cs, ce = intervals[0]
    for s, e in intervals[1:]:
        if s <= ce:
            ce = max(ce, e)
        else:
            merged.append((cs, ce))
            cs, ce = s, e
    merged.append((cs, ce))
    return merged


def day_night_history():
    """从探针锚点逆推全部历史昼夜切换点。
    关键：泰拉瑞亚游戏时间流速 = 现实 ×60（一天 24 游戏小时 = 24 现实分钟，
    现实 1 秒 = 游戏 60 秒），且游戏时间只在有玩家时流动。
    锚点持久化固定 → markers 跨刷新稳定；world_day 用全部在线时间实时增长。
    返回 {markers: [{ts, type}], world_day, gstart, gstart_label}"""
    global _DAY_NIGHT_CACHE
    now = time.time()
    if now - _DAY_NIGHT_CACHE["ts"] < 30 and _DAY_NIGHT_CACHE["markers"]:
        return _DAY_NIGHT_CACHE
    anchor = _anchor()
    if anchor["gsec"] is None:
        return {"markers": [], "world_day": None, "gstart": None, "gstart_label": None}
    real0, g0 = anchor["real"], anchor["gsec"]
    intervals = _merge_intervals(player_intervals())
    # 锚点前在线时间（逆推世界起点用）与全部在线时间（world_day 实时用）
    before = sum(min(e, real0) - s for s, e in intervals if s < real0) * 60
    total_active = sum(e - s for s, e in intervals) * 60
    gstart = (g0 - before) % 86400
    world_day = (gstart + total_active) // 86400 + 1
    # 正向模拟（只到锚点为止，锚点后由前端实时检测补）：跨过 54000 → 夜晚开始；跨过 86400(0) → 白天开始
    markers = []
    g = gstart
    for s, e in intervals:
        if s >= real0:
            continue
        e2 = min(e, real0)
        dur = (e2 - s) * 60
        g_end_i = g + dur
        gg = g
        while gg < g_end_i:
            cur = gg % 86400
            nxt = min((54000 - cur) % 86400, (86400 - cur) % 86400)
            if nxt == 0:
                gg += 1   # 已在阈值点，跳过 1 游戏秒（1/60 现实秒误差可忽略）
                continue
            if gg + nxt > g_end_i:
                break
            gg += nxt
            mtype = "night" if gg % 86400 == 54000 else "day"
            markers.append({"ts": int(s + (gg - g) / 60.0), "type": mtype,
                            "dn": gg // 86400 + 1})   # 该切换点发生的世界天数
        g = g_end_i
    h = gstart / 86400.0 * 24.0 + 4.5
    h %= 24.0
    _DAY_NIGHT_CACHE = {
        "ts": now, "markers": markers,
        "world_day": world_day,
        "gstart": f"{int(h):02d}:{int((h - int(h)) * 60):02d}",
        "gstart_label": "白天" if 4.5 <= h < 19.5 else "夜晚",
    }
    return _DAY_NIGHT_CACHE


def _parse_ts(iso: str) -> int:
    """解析 short-iso 时间戳。Python 3.10 的 fromisoformat 不支持带冒号时区(+08:00)，
    统一用 strptime 取前 19 字符（服务器本地时间，journal 输出即本地时区）。"""
    try:
        return int(datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").timestamp())
    except Exception:
        return 0


def players_from_journal():
    """从服务端日志重放 join/left 事件，得到真实在线名单。20 秒缓存。"""
    now = time.time()
    if now - _PLAYERS_CACHE["ts"] < 20:
        return _PLAYERS_CACHE["list"]
    result = []
    try:
        out = subprocess.run(
            ["sudo", "-n", "journalctl", "-u", "terraria",
             "--output=short-iso", "--since", "-12 hours", "--no-pager"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        sessions, order = {}, []
        pat = re.compile(r"^(\S+)\s+\S+\s+\S+:\s*:?\s*(.+?)\s+has\s+(joined|left)\.?\s*$")
        for line in out.splitlines():
            m = pat.match(line)
            if not m:
                continue
            iso, name, action = m.group(1), m.group(2), m.group(3)
            ts = _parse_ts(iso)
            if not ts:
                continue
            if action == "joined":
                # 同上：崩溃重连的重复 joined 不覆盖起点
                if name not in sessions:
                    order.append(name)
                    sessions[name] = ts
            else:
                sessions.pop(name, None)
        seen = set()
        for name in order:
            if name in sessions and name not in seen:
                seen.add(name)
                result.append({
                    "name": name,
                    "minutes": max(0, int((now - sessions[name]) / 60)),
                })
    except Exception:
        pass
    _PLAYERS_CACHE["ts"] = now
    _PLAYERS_CACHE["list"] = result
    return result


def read_online() -> int:
    try:
        out = subprocess.run(
            ["ss", "-tn", "state", "established", "( sport = :7777 )"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        return max(0, len(out.strip().splitlines()) - 1)
    except Exception:
        return -1


def terraria_active() -> str:
    try:
        return subprocess.run(
            ["systemctl", "is-active", "terraria"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def mem() -> dict:
    d = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":", 1)
        d[k] = int(v.split()[0])
    swapt, swapf = d.get("SwapTotal", 0), d.get("SwapFree", 0)
    return {
        "mem_total_mb": d["MemTotal"] // 1024,
        "mem_used_mb": (d["MemTotal"] - d["MemAvailable"]) // 1024,
        "swap_total_mb": swapt // 1024,
        "swap_used_mb": (swapt - swapf) // 1024,
    }


def cpu_percent(sample: float = 0.15) -> float:
    def snap():
        vals = list(map(int, open("/proc/stat").readline().split()[1:]))
        return vals[3] + vals[4], sum(vals)
    i1, t1 = snap()
    time.sleep(sample)
    i2, t2 = snap()
    dt, di = t2 - t1, i2 - i1
    if dt <= 0:
        return 0.0
    return round(100.0 * (dt - di) / dt, 1)


def disk() -> dict:
    s = os.statvfs("/")
    return {
        "disk_total_gb": round(s.f_blocks * s.f_frsize / 2**30, 1),
        "disk_free_gb": round(s.f_bavail * s.f_frsize / 2**30, 1),
    }


_NET_CACHE = {"ts": 0.0, "tx": 0.0}


def net_tx_kbs() -> float:
    """网卡上行速率 KB/s（1 秒采样，5 秒缓存复用）。"""
    now = time.time()
    if now - _NET_CACHE["ts"] < 5:
        return _NET_CACHE["tx"]
    def read_tx():
        for line in open("/proc/net/dev"):
            parts = line.split()
            if ":" in parts[0] and not parts[0].startswith("lo"):
                return int(parts[9])   # TX bytes 列
        return 0
    try:
        t1 = read_tx()
        time.sleep(1)
        t2 = read_tx()
        tx = max(0, (t2 - t1)) / 1024.0
    except Exception:
        tx = 0.0
    _NET_CACHE["ts"] = now
    _NET_CACHE["tx"] = tx
    return tx


def backups() -> dict:
    try:
        d = "/opt/terraria/backups"
        files = sorted(f for f in os.listdir(d) if f.endswith(".tar.gz"))
        latest, human = None, None
        if files:
            latest = files[-1]
            ts = latest[6:-7]
            if len(ts) == 13:
                human = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}"
        return {"count": len(files), "latest": human}
    except Exception:
        return {"count": 0, "latest": None}


def uptime_since() -> int:
    """部署基准时间戳：读 /opt/terraria/.uptime_since，无则取最早备份文件时间。"""
    try:
        with open("/opt/terraria/.uptime_since") as f:
            return int(f.read().strip())
    except Exception:
        try:
            files = sorted(os.listdir("/opt/terraria/backups"))
            return int(os.path.getmtime("/opt/terraria/backups/" + files[0]))
        except Exception:
            return int(time.time())


def stats() -> dict:
    players = players_from_journal()
    dnh = day_night_history()
    return {
        "online": len(players),
        "players": players,
        "conn_count": read_online(),
        "terraria": terraria_active(),
        "cpu_percent": cpu_percent(),
        "net_tx_kbs": net_tx_kbs(),
        **mem(),
        **disk(),
        "backups": backups(),
        "uptime_since": uptime_since(),
        "game_time": game_time(),
        "world_day": dnh["world_day"],
        "day_night": dnh["markers"],
        "world_start": {"time": dnh["gstart"], "label": dnh["gstart_label"]},
        "ts": int(time.time()),
    }


def _pull_journal(since_ts=None, until_ts=None, n=None):
    """执行 journalctl 并解析为 [{t, msg}]。
    注意：systemd 249 下 `--until` 与 `-n` 连用会返回空（-n 覆盖 until 窗口），
    因此 until 分支不带 -n，由调用方自行切片。"""
    args = ["sudo", "-n", "journalctl", "-u", "terraria",
            "--output=short-iso", "--no-pager"]
    if until_ts is not None:
        args += ["--until", f"@{int(until_ts)}"]
    elif since_ts is not None:
        args += ["--since", f"@{int(since_ts)}"]
    else:
        args += ["-n", str(n or 600)]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    lines = []
    for line in out.splitlines():
        m = _LOG_RE.match(line)
        if m:
            msg = m.group(2).strip()
            if _NOISE.match(msg):
                continue
            t = _parse_ts(m.group(1))
            lines.append({"t": t, "msg": _migrate_bot_name(t, msg)})
        else:
            lines.append({"t": 0, "msg": line.strip()})
    return lines


def refresh_cache():
    """把 journal 最新行增量并入内存缓存，(t,msg) 去重，seq 单调递增。"""
    global _LOG_SEQ, _CACHE_LAST_TS, _LOG_KEYS
    rows = _pull_journal(since_ts=_CACHE_LAST_TS)
    if not rows:
        return
    for r in rows:
        key = (r["t"], r["msg"])
        if key in _LOG_KEYS:
            continue
        _LOG_KEYS.add(key)
        _LOG_SEQ += 1
        _LOG_CACHE.append({"seq": _LOG_SEQ, "t": r["t"], "msg": r["msg"]})
        if r["t"]:
            _CACHE_LAST_TS = max(_CACHE_LAST_TS or 0, r["t"])
    # 防内存膨胀：keys 超限时按缓存重建
    if len(_LOG_KEYS) > 60000:
        _LOG_KEYS = {(l["t"], l["msg"]) for l in _LOG_CACHE}


def _marker_row(m):
    """逆推昼夜标记 → 烘焙日志行（准确的 ts/type/dn，前端直接渲染昼夜条）"""
    return {"t": m["ts"], "type": m["type"], "dn": m.get("dn"),
            "msg": "[DAY] 进入白天" if m["type"] == "day" else "[NIGHT] 进入夜晚"}


def _merge_markers(lines, lo_ts, hi_ts):
    """把逆推昼夜标记烘焙进行流：仅插入 (lo_ts, hi_ts] 区间，按 ts 与日志行混排。
    标记行是固定数据，插一次即定位，前端无需任何游标/回退。"""
    if not lines:
        return lines
    dnh = day_night_history()
    mks = [m for m in dnh.get("markers", []) if lo_ts < m["ts"] <= hi_ts]
    if not mks:
        return lines
    out = []
    i = 0
    for l in lines:
        while i < len(mks) and mks[i]["ts"] <= l["t"]:
            out.append(_marker_row(mks[i]))
            i += 1
        out.append(l)
    for k in range(i, len(mks)):
        out.append(_marker_row(mks[k]))
    return out


def fetch_logs(after_seq=None, before_ts=None, limit=150):
    """拉取日志。
    after_seq: 返回 seq > after_seq 的新行（增量轮询，保证不重复）
    before_ts: 返回该时间之前最近的 limit 行（历史回溯，剔除已在缓存的行）
    两者皆空: 返回最近 limit 行
    昼夜标记烘焙：逆推 markers 按 ts 混排进返回行流（区间 (lo, hi]，跨批不重复）
    返回 (lines, next_seq)
    """
    with _LOG_LOCK:
        refresh_cache()
        if after_seq is not None:
            lines = [l for l in _LOG_CACHE if l["seq"] > after_seq]
            hi_ts = lines[-1]["t"] if lines else 0
            if hi_ts:
                prev_ts = 0
                for l in _LOG_CACHE:
                    if l["seq"] <= after_seq:
                        prev_ts = l["t"]
                    else:
                        break
                lines = _merge_markers(lines, prev_ts, hi_ts)
            return lines, _LOG_SEQ
        if before_ts:
            # 缓存已全量回填(含 journal 全部历史)时直接内存切片：瞬时返回，
            # 避免 journalctl 全量扫描(上万行/数秒)阻塞单线程 server 拖垮前端轮询。
            # 只有游标早于缓存最早(缓存滚动淘汰后)才回落 journalctl。
            cache_first_t = next((l["t"] for l in _LOG_CACHE if l["t"]), None)
            if cache_first_t is not None and before_ts - 1 >= cache_first_t:
                pool = [l for l in _LOG_CACHE if l["t"] and l["t"] < before_ts]
                out = pool[-limit:]
            else:
                # --until 是 <= 语义，减 1 秒保证严格早于游标，避免前端过滤后为空误判"已到最早"
                lines = _pull_journal(until_ts=before_ts - 1)
                out = []
                for l in lines:
                    if (l["t"], l["msg"]) not in _LOG_KEYS:
                        out.append(l)
                out = out[-limit:]
            if out:
                ts = [x["t"] for x in out if x["t"]]
                if ts:
                    out = _merge_markers(out, min(ts) - 1, max(ts))
            return out, _LOG_SEQ
        lines = list(_LOG_CACHE)[-limit:]
        next_seq = lines[-1]["seq"] if lines else 0
        if lines:
            ts = [x["t"] for x in lines if x["t"]]
            if ts:
                lines = _merge_markers(lines, min(ts) - 1, max(ts))
        return lines, next_seq


# 服务端噪音行：启动横幅/帮助提示/崩溃栈/全部进度刷屏行
# （进度行在服务端统一过滤：避免 before 回溯返回大量无意义行导致前端误判"已到最早"卡死）
_NOISE = re.compile(
    r"^(Type 'help' for a list of commands\.|"
    r"^:?\s*\[(?:DAY|NIGHT)\]|"
    r"^:?\s*\[EVENT\]\s*(?:Ghost|Slime\((?:Rainbow|Neon|Purple|Red|Green|Blue|Yellow|Black|White)\))[^!]*已苏醒|"
    r"\ufeff*Error Logging Enabled\.|"
    r"\s*at Terraria\.|"
    r"Saving world data: \d+%|"
    r"Settling liquids \d+%|"
    r"Validating world save: \d+%|"
    r"Resetting game objects \d+%|"
    r"Loading world data: \d+%|"
    r"-- (?:Boot|Reboot) [0-9a-f]+ --)"
)


# ============ 玩家颜色：服务端固定色库 + 持久化分配 ============
# 服务端在 /colors 请求时扫描日志中的玩家名，按"先来先得"从固定色库分配，
# 持久化到 player_colors.json —— 同一玩家永远同色，跨设备/跨刷新一致。
# 色库避开系统语义色（错误红/存档绿/警告黄/连接蓝/启动紫），前 6 色高区分度，
# 后 4 色（粉系/玫红/蓝灰）沉底，仅当玩家数超过 6 时才用到（maxplayers=8，富余）。
_PLAYER_COLORS = [
    "#2dd4bf", "#fb923c", "#67e8f9", "#a3e635",
    "#818cf8", "#fdba74", "#f472b6", "#e879f9",
    "#fb7185", "#94a3b8",
]
# 固定色（用户指定，优先且不占用色库）：空气灰、Server 橙、扣哒粉、小T 绿
_FIXED_COLORS = {"空气": "#9ca3af", "Server": "#f97316", "扣哒": "#f472b6", "小T": "#4ade80"}
_COLOR_FILE = "/opt/terraria/scripts/player_colors.json"
_COLOR_CACHE = {"ts": 0.0, "colors": None}

# ---- 历史 bot 名迁移：2026-09-02 16:08 起旧 AI"小T"改名"扣哒" ----
# 该时刻之前日志里的 <小T> 是扣哒的旧人格发言，统一迁移为 <扣哒>；
# 该时刻之后（含未来）的 <小T> 是新的第二 AI"小T（世界的意志）"，保留原名。
# 在数据层（_pull_journal）处理，dashboard/玩家色/搜索全部自然正确，且不会误伤新小T。
_OLD_BOT_CUTOFF = int(datetime.strptime("2026-09-02 16:08:00", "%Y-%m-%d %H:%M:%S").timestamp())
_BOT_MSG_RE = re.compile(r"^(:?\s*)<小T>")


def _migrate_bot_name(t, msg):
    if t and t < _OLD_BOT_CUTOFF and _BOT_MSG_RE.match(msg):
        return _BOT_MSG_RE.sub(r"\1<扣哒>", msg, count=1)
    return msg


_NAME_PAT = re.compile(r"^:?\s*<([^>]+)>\s")
_JOIN_PAT = re.compile(r"^:?\s*([^<].+?)\s+has\s+(?:joined|left)\.?$")
_BAD_NAME = re.compile(r"^(Server|Anonymous|连接|断开|区块|拒绝|异常|系统)$|\d|\.")


def _scan_player_names():
    """全量扫描 journalctl 提取玩家名（聊天 <名> + 进出 joined/left），排除系统名与掩码 IP。
    不依赖内存缓存：缓存窗口会被连接噪音挤占，玩家名可能落在窗口外。"""
    args = ["sudo", "-n", "journalctl", "-u", "terraria",
            "--output=short-iso", "--no-pager"]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=25).stdout
    except Exception:
        return set()
    names = set()
    for line in out.splitlines():
        m = _LOG_RE.match(line)
        if not m:
            continue
        msg = m.group(2).strip()
        if _NOISE.match(msg):
            continue
        m2 = _NAME_PAT.match(msg)
        if m2:
            names.add(m2.group(1))
            continue
        m2 = _JOIN_PAT.match(msg)
        if m2:
            names.add(m2.group(1))
    return {n for n in names if not _BAD_NAME.search(n)}


def player_colors() -> dict:
    """返回 玩家名 -> 颜色 映射（带 30 秒缓存）。新名字按先来先得分配并持久化。"""
    now = time.time()
    if _COLOR_CACHE["colors"] is not None and now - _COLOR_CACHE["ts"] < 30:
        return _COLOR_CACHE["colors"]
    mapping = {}
    try:
        with open(_COLOR_FILE, encoding="utf-8") as f:
            mapping = json.load(f)
    except Exception:
        mapping = {}
    used = set(mapping.values())
    changed = False
    for n in sorted(_scan_player_names()):
        if n in mapping or n in _FIXED_COLORS:
            continue
        for c in _PLAYER_COLORS:
            if c not in used:
                mapping[n] = c
                used.add(c)
                changed = True
                break
        else:  # 色库耗尽（>10 玩家）：确定性兜底
            mapping[n] = _PLAYER_COLORS[sum(ord(ch) for ch in n) % len(_PLAYER_COLORS)]
            changed = True
    if changed:
        try:
            with open(_COLOR_FILE, "w", encoding="utf-8") as f:
                json.dump(mapping, f, ensure_ascii=False, indent=1)
        except Exception:
            pass
    _COLOR_CACHE["ts"] = now
    _COLOR_CACHE["colors"] = mapping
    return mapping


def check_token(token) -> bool:
    try:
        with open(TOKEN_FILE) as f:
            return token == f.read().strip()
    except Exception:
        return False


def send_cmd(cmd: str):
    """写命令到服务端控制台 FIFO。"""
    with open(FIFO, "w") as f:
        f.write(cmd + "\n")
    return True


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._reply(204, {})

    def _serve_page(self):
        """同源托管看板页面：http://IP:19980/ 直接打开，避免本地 file:// 的 mixed content 拦截"""
        try:
            with open(PAGE_FILE, "r", encoding="utf-8") as f:
                body = f.read().encode()
        except Exception:
            self._reply(500, {"error": "page file missing"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_items(self):
        """物品 ID → 中文名映射（聊天物品代码 [i:ID] 渲染用）。"""
        try:
            with open(ITEMS_FILE, "r", encoding="utf-8") as f:
                body = f.read().encode()
        except Exception:
            self._reply(500, {"error": "items map missing"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            self._serve_page()
        elif u.path == "/items.json":
            self._serve_items()
        elif u.path == "/stats":
            self._reply(200, stats())
        elif u.path == "/colors":
            self._reply(200, {"colors": player_colors(), "fixed": _FIXED_COLORS})
        elif u.path == "/logs":
            q = parse_qs(u.query)
            after_seq = q.get("after_seq", [None])[0]
            before = q.get("before", [None])[0]
            limit = q.get("limit", [None])[0]
            lines, next_seq = fetch_logs(
                after_seq=int(after_seq) if after_seq and after_seq.isdigit() else None,
                before_ts=int(before) if before and before.isdigit() else None,
                limit=int(limit) if limit and limit.isdigit() else 400,
            )
            self._reply(200, {"lines": lines, "next_seq": next_seq})
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/auth":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                self._reply(400, {"error": "bad json"})
                return
            if not check_token(data.get("token", "")):
                self._reply(401, {"error": "invalid token"})
                return
            self._reply(200, {"ok": True})
            return
        if u.path != "/cmd":
            self._reply(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._reply(400, {"error": "bad json"})
            return
        if not check_token(data.get("token", "")):
            self._reply(401, {"error": "invalid token"})
            return
        cmd = str(data.get("cmd", "")).strip()
        if not cmd:
            self._reply(400, {"error": "empty cmd"})
            return
        if len(cmd) > 500:
            self._reply(400, {"error": "cmd too long"})
            return
        try:
            send_cmd(cmd)
        except Exception as e:
            self._reply(500, {"error": f"fifo write failed: {e}"})
            return
        self._reply(200, {"ok": True, "cmd": cmd})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=_time_probe, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
