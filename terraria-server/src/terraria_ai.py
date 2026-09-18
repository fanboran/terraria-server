#!/usr/bin/env python3
"""
Terraria AI 聊天机器人 v3 —— 麦麦式特化版
借鉴 MaiBot(MaiSaka) 的设计理念，但为泰拉瑞亚场景轻量化：

  1. 人设卡    : /opt/terraria/persona.txt 定义人格（名字/性格/说话风格/口头禅），注入 system prompt
  2. 该说才说  : 聊天接话按概率触发（AI_TALK_PROB，默认 0.6），不每条都接；事件自主说话保持必说（有冷却）
  3. 风格模仿  : 记录每个玩家的最近发言，prompt 提示模仿群内说话风格
  4. 事件感知  : 玩家进出/聊天/存档/启动/被踢（日志解析）；有人在线才说话
  5. 长短分级  : 接聊天一句；事件 2-3 句（换行分隔，逐条 say）

配置：/opt/terraria/ai.env
"""
import json
import os
import random
import re
import subprocess
import time
from datetime import datetime
from urllib import request

FIFO = "/opt/terraria/console.fifo"
PERSONA_FILE = os.environ.get("AI_PERSONA_FILE", "/opt/terraria/persona.txt")
BASE_URL = os.environ.get("AI_BASE_URL", "https://api.deepseek.com/v1")
API_KEY = os.environ.get("AI_API_KEY", "")
MODEL = os.environ.get("AI_MODEL", "deepseek-chat")
BOT_NAME = os.environ.get("AI_BOT_NAME", "扣哒")
PERSONA_FILE = os.environ.get("AI_PERSONA_FILE", "/opt/terraria/persona.txt")
REPLY_GAP = int(os.environ.get("AI_REPLY_GAP", "20"))   # 实际发言后的防连击间隔（LLM 沉默决策不占）
EVENT_COOLDOWN = int(os.environ.get("AI_EVENT_COOLDOWN", "90"))
DEATH_COOLDOWN = int(os.environ.get("AI_DEATH_COOLDOWN", "45"))  # 死亡损人独立冷却（血月连死不刷屏）
# 播报分工（AI_EVENTS 逗号分隔白名单）：扣哒=day,night(昼夜)；小T=death,event,join,left,save,start(其余)。
# 不设置=全部事件都播报。白名单外的事件不"必说"，但仍按概率照常搭话（保持分工前都会接话的氛围）。
EVENT_WHITELIST = {x.strip() for x in os.environ.get("AI_EVENTS", "").split(",") if x.strip()} or None
CHIME_PROB = float(os.environ.get("AI_EVENT_CHIME_PROB", "0.4"))   # 白名单外事件的搭话概率（分工前=必说）
IDLE_AFTER = int(os.environ.get("AI_IDLE_AFTER", "600"))         # 玩家静默多少秒后允许空闲观察说话
IDLE_PROB = float(os.environ.get("AI_IDLE_PROB", "0.1"))         # 空闲观察触发概率（冷场基本不主动说）
ACTIVE_WINDOW = int(os.environ.get("AI_ACTIVE_WINDOW", "180"))   # 玩家活跃度统计窗口（秒）
STATS_URL = os.environ.get("AI_STATS_URL", "http://127.0.0.1:19980/stats")  # 同机 stats_server（拿世界天数）
HISTORY = int(os.environ.get("AI_HISTORY", "6"))
MAX_LEN = int(os.environ.get("AI_MAX_LEN", "120"))
# 死亡事件：游戏里死太频繁，单人死亡几乎不值得提；只有连环团灭才奇怪
DEATH_CHIME_PROB = float(os.environ.get("AI_DEATH_CHIME_PROB", "0.05"))  # 单人死亡旁听概率
DEATH_RALLY_N = int(os.environ.get("AI_DEATH_RALLY_N", "8"))             # 窗口内死满 N 人次 = 团灭
DEATH_RALLY_WINDOW = int(os.environ.get("AI_DEATH_RALLY_WINDOW", "180")) # 团灭判定窗口（秒）
# 无播报价值的事件黑名单（Ghost 等伪 Boss/杂鱼刷新），AI 忽略、面板不显示
IGNORE_EVENT_PAT = re.compile(
    os.environ.get("AI_IGNORE_EVENTS", r"Ghost|Slime\((?:Rainbow|Neon|Purple|Red|Green|Blue|Yellow|Black|White)\)")
)
ONLINE_NOW = []    # 当前在线玩家名（main 循环随 join/left 实时更新，prompt 注入用）
RECENT_LEFT = []   # [(name, ts)] 最近离线的玩家（3 分钟内可被自然提起，如感慨怎么有人走了）
MEMORY_FILE = os.environ.get("AI_MEMORY_FILE", "/opt/terraria/scripts/memory.txt")
MEM_SINCE_FILE = os.environ.get("AI_MEM_SINCE", "/opt/terraria/scripts/.mem_since")
MEM_MAX = int(os.environ.get("AI_MEMORY_MAX", "40"))     # 记忆最多保留条数
MEM_PROMPT_N = int(os.environ.get("AI_MEMORY_INJECT", "12"))  # 注入 prompt 的条数
TALK_PROB = float(os.environ.get("AI_TALK_PROB", "0.6"))  # 接话概率
CHAT_ENABLED = os.environ.get("AI_CHAT_ENABLED", "1") == "1"  # 是否参与日常闲聊(非点名)。小T=0：只大事播报+点名才说话

# 只屏蔽自己与系统名；AI_SKIP=对方 bot 名（如 ai.env: AI_SKIP=小T），互不视为玩家 → 防两 AI 互聊死循环
_SKIP_EXTRA = {x.strip() for x in os.environ.get("AI_SKIP", "").split(",") if x.strip()}
SKIP_NAMES = {"Server", "服务器", "Console", BOT_NAME} | _SKIP_EXTRA
# Terraria 服务端日志的玩家聊天格式：<玩家名> 内容（尖括号）
CHAT_RE = re.compile(r"^<([^>]+)>\s+(.+)$")
LOG_RE = re.compile(r"^(\S+)\s+\S+\s+\S+:\s?(.*)$")


def dbg(msg):
    print(f"[AI {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def persona():
    try:
        with open(PERSONA_FILE, encoding="utf-8") as f:
            base = f.read().strip()
    except Exception:
        base = f"你是泰拉瑞亚服务器里的聊天机器人「{BOT_NAME}」，性格活泼爱吐槽。"
    mem = load_memory()
    if mem:
        base += ("\n\n服务器记忆（这些是你亲身经历过的往事，聊到相关话题时可以自然引用，但不要背诵原文）：\n"
                 + "\n".join(mem[-MEM_PROMPT_N:]))
    return base


def load_memory():
    """读记忆文件（每行一条：日期|内容），返回最新 MEM_MAX 条。"""
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            lines = [x.strip() for x in f.read().splitlines() if x.strip()]
        return lines[-MEM_MAX:]
    except Exception:
        return []


def extract_memory():
    """启动时从上次水位以来的日志里让 LLM 提炼值得长期记住的片段，追加进记忆文件。
    水位文件防重复提取；无 LLM/无新内容时静默跳过。"""
    try:
        last = 0
        if os.path.exists(MEM_SINCE_FILE):
            last = int(open(MEM_SINCE_FILE).read().strip() or 0)
        rows = tail_logs(max(last, time.time() - 86400 * 2))
        chats = []
        for ts, msg in rows:
            ev = classify(msg)
            if not ev:
                continue
            etype, payload = ev
            if etype == "chat":
                name, text = payload
                chats.append(f"{name}: {text}")
            elif etype == "event":
                chats.append(f"[世界事件] {payload}")
            elif etype == "death":
                v = payload[0] if isinstance(payload, tuple) else payload
                chats.append(f"[死亡] {v} 死了")
        watermark = max((ts for ts, _ in rows), default=last)
        if not chats:
            _save_mem_watermark(watermark)
            return
        existing = "\n".join(load_memory())
        today = datetime.now().strftime("%m-%d")
        # 注意：不能用超长 system 消息——DeepSeek 对超长 system 会返回 0 token 空响应，
        # 人设走 system、记录与指令走 user 消息（与聊天同形态，已验证稳定）。
        blob = "\n".join(chats[-200:])
        instruction = (
            f"以上是服务器最近发生的事的记录（今天 {today}）。"
            "从中提炼 0-3 条最值得长期记住的瞬间（有趣的对话、大事件、玩家间的名场面或梗），"
            "每条一行，格式为 日期|一句话（40字内）。只写真正有味道、值得回忆的，"
            "没有就什么都不输出；不要与已知记忆重复：\n" + (existing or "（还没有任何记忆）")
        )
        rep = llm_reply(persona(), [("记录", blob), ("指令", instruction)], max_tokens=400)
        if rep:
            new_lines = []
            for x in rep.splitlines():
                x = x.strip().lstrip("-•* ")
                if not x or x.upper() == "[SILENT]":
                    continue
                if "|" in x:
                    new_lines.append(x)
                elif len(x) <= 60:   # 模型没带日期前缀时自动补
                    new_lines.append(f"{today}|{x}")
            if new_lines:
                with open(MEMORY_FILE, "a", encoding="utf-8") as f:
                    f.write("\n".join(new_lines[-5:]) + "\n")
                dbg(f"记忆提炼 +{len(new_lines[-5:])} 条")
        _save_mem_watermark(watermark)
    except Exception as e:
        dbg(f"记忆提炼失败: {e}")


def _save_mem_watermark(ts):
    try:
        with open(MEM_SINCE_FILE, "w") as f:
            f.write(str(int(ts)))
    except Exception:
        pass


def _parse_ts(s):
    """解析 journalctl --output=short-iso 的时间戳（如 2026-09-02T14:19:10+0800）。

    Python 3.10 的 datetime.fromisoformat 不支持无冒号时区(+0800)，
    会抛 ValueError 导致整行被跳过、tail_logs 永远返回空。
    改用 strptime：其 %z 指令原生支持 +0800 / +08:00，跨版本稳妥。
    """
    try:
        return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z").timestamp())
    except ValueError:
        return None


def tail_logs(since_ts):
    try:
        out = subprocess.run(
            ["sudo", "-n", "journalctl", "-u", "terraria",
             "--output=short-iso", "--since", f"@{int(since_ts)}", "--no-pager"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        m = LOG_RE.match(line)
        if not m:
            continue
        ts = _parse_ts(m.group(1))
        if ts is None:
            continue
        rows.append((ts, m.group(2).strip()))
    return rows


def classify(msg):
    # 服务端日志部分行带 ": " 前缀（如 ": <空气> 小T在干嘛" / ": Time: 9:36 AM"），
    # 不剥掉会导致聊天/事件被 CHAT_RE 漏解析，点名与接话全部失效。
    msg = re.sub(r"^:\s*", "", msg)
    m = re.match(r"^(.+?)\s+has\s+joined\.?$", msg)
    if m:
        return ("join", m.group(1))
    m = re.match(r"^(.+?)\s+has\s+left\.?$", msg)
    if m:
        return ("left", m.group(1))
    m = re.match(r"^(.+?)\s+was\s+booted:\s*(.+)$", msg)
    if m:
        return ("booted", m.group(1))
    # 玩家死亡：IL 注入行 KILLME: 名字（无死法）；若未来注入带死法则识别 was slain by
    m = re.match(r"^KILLME:\s*(.+?)\s*$", msg)
    if m:
        return ("death", m.group(1))
    m = re.match(r"^(.+?)\s+was slain by\s+(.+?)\s*$", msg)
    if m:
        return ("death", (m.group(1), m.group(2)))
    # 日出/日落（IL 注入 [DAY] 进入白天 / [NIGHT] 进入夜晚）
    if re.match(r"^\[DAY\]", msg):
        return ("day", None)
    if re.match(r"^\[NIGHT\]", msg):
        return ("night", None)
    # 世界事件（IL 注入）：入侵 [EVENT]…、城镇NPC [NPC]…已到达、血月 [BLOODMOON] 血月降临
    # 杂鱼事件过滤：Ghost 等非 Boss 事件无播报价值，直接忽略（与 stats_server._NOISE 同步）
    m = re.match(r"^\[EVENT\]\s*(.+)$", msg)
    if m:
        if not IGNORE_EVENT_PAT.search(m.group(1)):
            return ("event", m.group(1))
        return None
    m = re.match(r"^\[NPC\]\s*(.+)$", msg)
    if m:
        return ("event", m.group(1))
    if re.match(r"^\[BLOODMOON\]", msg):
        return ("event", re.sub(r"^\[BLOODMOON\]\s*", "", msg))
    if re.match(r"^Server started\.?$", msg):
        return ("start", None)
    if re.match(r"^Backing up world file\.?$", msg):
        return ("save", None)
    m = CHAT_RE.match(msg)
    if m:
        name, text = m.group(1), m.group(2)
        if name in SKIP_NAMES or "is connecting" in text:
            return None
        return ("chat", (name, text))
    return None


def llm_reply(system_prompt, context, max_tokens, enable_thinking=False):
    if not API_KEY:
        return None
    messages = [{"role": "system", "content": system_prompt}]
    for name, text in context:
        messages.append({"role": "user", "content": f"{name}: {text}"})
    for attempt in range(2):   # V3.2 偶发把输出写进思考通道(content 空)，重试一次
        # enable_thinking=False：显式关思考；个别供应商不认该参数则原样重试
        for extra in ({"enable_thinking": False}, {}):
            body = json.dumps({
                "model": MODEL, "messages": messages, "max_tokens": max_tokens,
                "temperature": 0.8, **extra,
            }).encode()
            req = request.Request(
                BASE_URL.rstrip("/") + "/chat/completions",
                data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + API_KEY},
            )
            try:
                with request.urlopen(req, timeout=45) as r:
                    data = json.load(r)
                break
            except Exception:
                if not extra:
                    continue
                if attempt == 1:
                    raise
        msg = data["choices"][0]["message"]
        text = (msg.get("content") or "").strip()
        # 思考内容泄漏防护：剥掉 <think>...</think>（含未闭合到末尾的半截）
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
        text = re.sub(r"<think>.*$", "", text, flags=re.S | re.I)
        text = re.sub(r"</?think>", "", text, flags=re.I)
        # 内部伪标签（<|...|> 特殊 token 与常见思考标签）；玩家名 <空气> 不误杀
        text = re.sub(r"<\|[^>]*\|>", "", text)
        text = re.sub(r"</?(?:thinking|reasoning|answer|output|response)>", "", text, flags=re.I)
        if text.strip():
            return text.strip()
    return None


def world_day():
    """从同机 stats_server 拿当前世界第几天；拿不到返回 None。"""
    try:
        with request.urlopen(STATS_URL, timeout=5) as r:
            data = json.load(r)
        return data.get("world_day")
    except Exception:
        return None


def say(text):
    # 兜底清洗：剥离括号动作/旁白（（脸红）（笑）等）→ 只留聊天文字；句中标点转空格；压缩截断
    text = re.sub(r"[（(][^（()）]*[)）]", "", text)
    text = re.sub(r"[，。、；：,.;:]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()[:MAX_LEN]
    if not text:
        return
    # patched4 服务端：say 命令已纯文本化（广播 NetworkText.FromLiteral，不再带 <服务器> 模板），
    # 因此机器人自带 <bot名> 前缀，游戏内以玩家聊天样式显示（无 Server 前缀）。
    with open(FIFO, "w") as f:
        f.write("say <" + BOT_NAME + "> " + text + "\n")


def say_multi(text, max_lines):
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines() if l.strip()]
    lines = lines[:max_lines]
    for i, line in enumerate(lines):
        say(line)
        if i < len(lines) - 1:
            time.sleep(2.5)


def chat_prompt():
    online = "、".join(ONLINE_NOW) if ONLINE_NOW else "（暂时没有人）"
    recent = ""
    if RECENT_LEFT:
        now = time.time()
        rl = [(n, int(now - t)) for n, t in RECENT_LEFT if now - t < 180]
        if rl:
            recent = "。刚刚离开的玩家：" + "、".join(f"{n}（{s}秒前）" for n, s in rl)
    return (
        persona() + "\n"
        f"当前在线玩家：{online}" + recent + "。"
        "只提当前在线的人；离开很久的人不要主动提；对刚刚离开的人可以自然感慨一句怎么有人走了。\n"
        "说话规则：自然友好即可，不要刻意堆诶呀、哼这类语气词；"
        "不要没话找话地反问——没有真实想问的事就不要用问句结尾，陈述句优先；"
        "句子中间不要用标点（尤其逗号句号），句尾可用一个！？～收尾；"
        "只发聊天文字，不要写括号动作或旁白，不要输出思考过程或任何标记；"
        "大多数回复不需要出现任何玩家名字，只有在直接回答某个玩家的问题或明显针对他时才叫名字，"
        "名字只能用名单里真实存在的名字，不要自创。\n"
        "【气氛判断】如果当前话题不需要你接话（比如玩家在私下交流、话题与你无关、插话会冷场），"
        "直接回复 [SILENT] 三个字母，不要输出其他任何内容。"
    )


def event_prompt(etype, payload, rally_n=0):
    who = payload or ""
    if etype == "death":
        # payload 可能是纯名字，或 (死者, 击杀者)
        if isinstance(payload, tuple) and len(payload) == 2:
            victim, killer = payload
            desc = f"玩家 {victim} 刚刚被 {killer} 杀死了"
        else:
            victim = who
            desc = f"玩家 {who} 刚刚死了"
        if rally_n:
            desc += f"。而且短短几分钟内服务器已经死了 {rally_n} 个人次，这是一场大型连环团灭事故"
            return (
                persona() + "\n"
                f"{desc}。面对这场惨烈的集体团灭，吐槽一句这场盛况（可提刚死的 {victim}，"
                "也可以感叹整体惨状），只说一句话，口语化，句中不要用逗号句号，不要堆语气词。"
            )
        return (
            persona() + "\n"
            f"{desc}。用中文说一句（可安抚可打趣），只说一句话，"
            "口语化，句中不要用逗号句号，不要堆语气词。"
        )
    if etype == "day":
        head = f"游戏世界太阳升起，进入第 {payload} 天" if payload else "游戏世界天亮了"
        return (
            persona() + "\n"
            f"{head}。用中文自然说一句，不超过20字，句中不要用逗号句号，"
            "可以提醒熬夜的玩家去休息。"
        )
    if etype == "night":
        head = f"游戏世界太阳落山，进入第 {payload} 天的夜晚" if payload else "游戏世界天黑了"
        return (
            persona() + "\n"
            f"{head}。用中文自然说一句，不超过20字，句中不要用逗号句号，"
            "可以提醒玩家天黑注意安全。"
        )
    if etype == "event":
        return (
            persona() + "\n"
            f"世界刚发生：{who}。不要复述这行消息，用你自己的口吻接一句，"
            "可以吐槽、可以看戏、也可以点破缘由；只说一句话，不超过20字，句中不要用逗号句号。"
        )
    if etype == "join":
        return (
            persona() + "\n"
            f"玩家 {who} 刚刚加入了服务器。用中文欢迎 {who} 一句，不超过20字，"
            "句中不要用逗号句号，不要堆语气词。"
        )
    if etype == "left":
        return persona() + f"\n玩家 {who} 刚刚离开了服务器。用中文自然说一句，不超过20字，句中不要用逗号句号。"
    if etype == "save":
        return persona() + "\n服务器刚完成存档。用中文自然说一句，不超过20字，句中不要用逗号句号。"
    if etype == "start":
        return (
            persona() + "\n"
            "服务器刚刚启动完毕。用中文自然宣告一句，不超过20字，句中不要用逗号句号。"
        )
    return chat_prompt()


def idle_prompt(players):
    return (
        persona() + "\n"
        f"现在在线玩家：{'、'.join(players)}。大家安静好一会儿没人说话了，"
        "你像突然想起什么似的随口吐槽一句或念叨一句，只说一句话，短，句中不要用逗号句号。"
    )


def talk_prob(times, now, window=None):
    """接话概率随玩家近 window 秒活跃消息量浮动：玩家话少(近3分钟<2条)完全不接，
    热聊(≥1.5条/分)≈TALK_PROB 封顶。只有点名/事件播报不受此限制。"""
    window = window or ACTIVE_WINDOW
    t0 = now - window
    m = sum(1 for t in times if t > t0)
    if m < 2:
        return 0.0
    rate = m / (window / 60.0)
    return min(TALK_PROB, 0.15 + rate * 0.32)


def usable(reply):
    """回复是否值得说：非空、非 [SILENT]、不含含糊指代（某人）。"""
    if not reply:
        return False
    s = reply.strip().upper()
    return s != "[SILENT]" and "某人" not in reply


def maybe_idle(sessions, last_talk, history, last_player_msg):
    """玩家静默超过 IDLE_AFTER 后，概率冷不丁蹦一句观察现状（麦麦式"活着"的表现）。"""
    if not sessions:
        return
    now = time.time()
    if now - last_player_msg < IDLE_AFTER:
        return
    if now - last_talk.get("idle", 0) < IDLE_AFTER:
        return
    if random.random() > IDLE_PROB:
        return
    last_talk["idle"] = now
    ctx = history[-3:] if history else [("系统", "服务器安安静静的")]
    reply = llm_reply(idle_prompt(sorted(sessions)), ctx, max_tokens=200)
    dbg(f"空闲观察回复: {reply!r}"[:200])
    if usable(reply):
        say_multi(reply, 1)


def active_players():
    """启动时从最近日志推断当前在线玩家（join 后无 left）——重启后不丢会话"""
    online = set()
    try:
        rows = tail_logs(time.time() - 3600 * 3)
    except Exception:
        return online
    for _ts, msg in rows:
        ev = classify(msg)
        if not ev:
            continue
        etype, payload = ev
        if etype == "join":
            online.add(payload)
        elif etype == "left":
            online.discard(payload)
    return online


def main():
    cursor = int(time.time()) - 300
    history = []
    sessions = {p: int(time.time()) for p in active_players()}
    if sessions:
        dbg(f"启动恢复在线玩家: {list(sessions)}")
    ONLINE_NOW.clear()
    ONLINE_NOW.extend(sorted(sessions))
    extract_memory()   # 重启时从上次水位以来的日志提炼新记忆（LLM，失败静默）
    last_talk = {}
    last_player_msg = time.time()   # 玩家最近一次说话/进出时间（空闲观察用）
    player_times = []               # 玩家活跃度时间戳（接话概率随消息量浮动）
    while True:
        try:
            rows = tail_logs(cursor)
            for ts, msg in rows:
                if ts <= cursor:
                    continue
                cursor = ts
                ev = classify(msg)
                if not ev:
                    continue
                etype, payload = ev

                if etype == "join":
                    sessions[payload] = ts
                    last_player_msg = ts
                    dbg(f"玩家 {payload} 加入（在线: {list(sessions)}）")
                elif etype == "left":
                    sessions.pop(payload, None)
                    last_player_msg = ts
                    RECENT_LEFT.append((payload, time.time()))
                    RECENT_LEFT[:] = [(n, t) for n, t in RECENT_LEFT if time.time() - t < 180]
                    dbg(f"玩家 {payload} 离开（在线: {list(sessions)}）")
                ONLINE_NOW.clear()
                ONLINE_NOW.extend(sorted(sessions))

                if etype == "chat":
                    history.append(payload)
                    history = history[-HISTORY:]
                    if not sessions:
                        continue
                    now = time.time()
                    last_player_msg = ts
                    name, text = payload
                    # 点名（消息里提到 bot 名字）→ 必回：只留短冷却防刷屏
                    mentioned = BOT_NAME in text
                    if mentioned:
                        dbg(f"点名来自 {name}: {text[:20]}")
                        if now - last_talk.get("chat", 0) < 8:
                            dbg("  点名冷却中，跳过")
                            continue
                    else:
                        # 日常闲聊开关：关闭时非点名一律不回（点名在上面分支已放行）
                        if not CHAT_ENABLED:
                            continue
                        # 麦麦式"积极决策"：每条消息都交给 LLM 气氛判断（[SILENT]=不说），
                        # 不再用概率层猜；只在真正发言后留短暂间隔防连击
                        if now - last_talk.get("chat", 0) < REPLY_GAP:
                            continue
                    reply = llm_reply(chat_prompt(), history, max_tokens=150)
                    dbg(f"LLM 回复: {reply!r}"[:200])
                    # 气氛判断层（借鉴 MaiBot 的"先决策再发言"）：LLM 说 [SILENT] 就不接
                    if usable(reply):
                        last_talk["chat"] = now   # 只在实际发言后计时，沉默不占冷却
                        say_multi(reply, 1)
                    else:
                        dbg("回复被过滤（空/[SILENT]/含糊指代）→ 本条不发言")
                else:
                    if not sessions or etype == "booted":
                        continue
                    now = time.time()
                    # 团灭检测：滑动窗口内死亡人次达标 = 大型事故（值得说），否则单人死亡随缘
                    if etype == "death":
                        death_times.append(now)
                        while death_times and death_times[0] < now - DEATH_RALLY_WINDOW:
                            death_times.pop(0)
                        rally_n = len(death_times) if len(death_times) >= DEATH_RALLY_N else 0
                    # 播报分工：白名单内=必说（负责播报）；白名单外=不保证，按概率照常搭话
                    if EVENT_WHITELIST and etype not in EVENT_WHITELIST:
                        if etype == "death":
                            prob = 1.0 if rally_n else DEATH_CHIME_PROB
                        else:
                            prob = CHIME_PROB
                        if random.random() > prob:
                            continue
                    # death 用更短独立冷却（血月多人连死也别太吵），其余事件统一冷却
                    cooldown = DEATH_COOLDOWN if etype == "death" else EVENT_COOLDOWN
                    if now - last_talk.get(etype, 0) < cooldown:
                        continue
                    last_talk[etype] = now
                    if etype == "death":
                        ctx = history[-3:] if history else [("系统", "服务器刚有人死了")]
                        reply = llm_reply(event_prompt("death", payload, rally_n), ctx, max_tokens=250)
                    elif etype in ("day", "night"):
                        dn = world_day()
                        dbg(f"{'日出' if etype == 'day' else '日落'}事件，世界第 {dn or '?'} 天")
                        ctx = history[-3:] if history else [("系统", "天亮了")]
                        reply = llm_reply(event_prompt(etype, dn), ctx, max_tokens=250)
                    else:
                        ctx = history[-3:] if history else [("系统", "服务器刚有些动静")]
                        reply = llm_reply(event_prompt(etype, payload), ctx, max_tokens=400)
                    dbg(f"事件 {etype} 回复: {reply!r}"[:200])
                    if usable(reply):
                        say_multi(reply, 3)
            # 空闲观察：玩家静默超时后冷不丁蹦一句
            maybe_idle(sessions, last_talk, history, last_player_msg)
            time.sleep(4)
        except Exception:
            time.sleep(10)


if __name__ == "__main__":
    main()
