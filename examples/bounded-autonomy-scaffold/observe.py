# -*- coding: utf-8 -*-
"""②a 观察窗 — bounded-scheduler 的人类可读前端(DESIGN §5:可观察性 = 安全前置)。

v0.2:观察 + 话筒。项目方在页面上说话 → 写入 observer-inbox(追加式)→ 立刻触发一个
有界模型回合(事件驱动唤醒,微澜"有差异才驱动");回合里 agent 读到话、回复写入
replies 文件,页面渲染成对话。心跳 cron 保持原速——空醒证据说它不该更快。

安全面:
  · 页面只绑定 127.0.0.1;不对外。
  · 写动作仅三种,全部项目方亲手触发:暂停/恢复哨兵、往自己的收件箱追加一句话。
  · 触发的模型回合仍是 wake_agent.ps1 那个白名单沙箱回合,一次一个(锁文件防并发),
    PAUSED 存在时只收话不唤醒。
  · 本服务不自我保活、不注册任务;Ctrl-C 即死。

用法:
  python observe.py            # 启动 http://127.0.0.1:8787
  python observe.py --once     # 只生成一次 dashboard.html 后退出
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

HERE = Path(__file__).resolve().parent
REPO = Path(r"<HOST_ROOT>")
METHOD_STATE = Path(r"<HOST_ROOT>\home\method-ledger")
WORKSPACE_KEY = "d431c38105a9a791"
SCOPE_KEY = "c36aecb1ff20"

HEARTBEAT_LOG = HERE / "wake-cron.log"
AGENT_LOG = HERE / "wake-agent.log"
AGENT_RUNS = HERE / "wake-agent-runs"
PAUSED = HERE / "PAUSED"
WAKE_AGENT = HERE / "wake_agent.ps1"
WAKE_LOCK = HERE / "wake-agent.lock"
INBOX = HERE / "observer-inbox.jsonl"
INBOX_PROCESSED = HERE / "observer-inbox-processed.jsonl"
REPLIES = HERE / "observer-inbox-replies.jsonl"
PEER_CHAT = HERE / "peer-room.jsonl"
CODEX_INBOX = HERE / "delegate-inbox.jsonl"
CODEX_INBOX_PROCESSED = HERE / "delegate-inbox-processed.jsonl"
CODEX_REPLIES = HERE / "delegate-inbox-replies.jsonl"
CODEX_LOG = HERE / "wake-codex.log"
PROJECTION = METHOD_STATE / "memory" / "projections" / "workspaces" / WORKSPACE_KEY / f"{SCOPE_KEY}.json"
LINEAGE_HEADS = METHOD_STATE / "memory" / "lineage" / "heads" / WORKSPACE_KEY / f"{SCOPE_KEY}.json"

HEARTBEAT_TASK = "WeilanBoundedSchedulerWake"


# --- readers (all read-only) -------------------------------------------------

def read_text_any(path: Path) -> str:
    """Read a small text file tolerating BOM / UTF-16 (PS 5.1 redirects)."""
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig", errors="replace")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in read_text_any(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # torn tail — same tolerance as the ledger readers
    return out


def append_jsonl(path: Path, entry: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def parse_heartbeat_log() -> list[dict]:
    if not HEARTBEAT_LOG.exists():
        return []
    out = []
    for line in read_text_any(HEARTBEAT_LOG).splitlines():
        m = re.match(r"(\S+)\s+wake ok\s+stop=(\S+)\s+frame=(\S+)", line.strip())
        if m:
            out.append({"time": m.group(1), "kind": "heartbeat", "stop": m.group(2), "frame": m.group(3)})
        elif line.strip():
            out.append({"time": line.strip()[:19], "kind": "heartbeat", "stop": "?", "raw": line.strip()})
    return out


def parse_agent_log() -> list[dict]:
    if not AGENT_LOG.exists():
        return []
    out = []
    for line in read_text_any(AGENT_LOG).splitlines():
        if not line.strip():
            continue
        stamp = line.strip().split()[0]
        m = re.match(r"\S+\s+rc=(\S+)\s+(\S+)\s+turns=(\S+)\s+cost=\$(\S+)\s+(\S+)\s+head=(\S+)", line.strip())
        entry = {"time": stamp, "kind": "agent", "raw": line.strip()}
        if m:
            entry.update({"rc": m.group(1), "status": m.group(2), "turns": m.group(3),
                          "cost": m.group(4), "ledger": m.group(5), "head": m.group(6)})
        out.append(entry)
    return out


def parse_codex_log() -> list[dict]:
    if not CODEX_LOG.exists():
        return []
    out = []
    for line in read_text_any(CODEX_LOG).splitlines():
        if not line.strip():
            continue
        stamp = line.strip().split()[0]
        m = re.match(r"\S+\s+rc=(\S+)\s+(\S+)\s+head=(\S+)", line.strip())
        entry = {"time": stamp, "kind": "codex", "raw": line.strip()}
        if m:
            entry.update({"rc": m.group(1), "ledger": m.group(2), "head": m.group(3)})
        out.append(entry)
    return out


def codex_pending() -> int:
    inbox = {e.get("id") for e in read_jsonl(CODEX_INBOX)}
    done = {e.get("id") for e in read_jsonl(CODEX_INBOX_PROCESSED)}
    return len(inbox - done)


def load_transcript(stamp: str) -> dict | None:
    p = AGENT_RUNS / f"{stamp}.json"
    if not p.exists():
        return None
    try:
        return json.loads(read_text_any(p))
    except (ValueError, OSError):
        return None


QUEUE_RE = re.compile(r"#+\s*Queued for owner[^\n]*\n(.*?)(?=\n#|\Z)", re.S | re.I)


def extract_queued(result_text: str) -> list[str]:
    m = QUEUE_RE.search(result_text or "")
    if not m:
        return []
    items = re.findall(r"^\s*(?:\d+\.|[-*])\s+(.+)$", m.group(1), re.M)
    return [i.strip() for i in items if i.strip()]


def load_projection() -> dict:
    try:
        return json.loads(read_text_any(PROJECTION))
    except (ValueError, OSError):
        return {}


def load_head() -> dict:
    try:
        heads = json.loads(read_text_any(LINEAGE_HEADS))
        branches = heads.get("branches") or {}
        return branches.get("main") or next(iter(branches.values()), {})
    except (ValueError, OSError):
        return {}


def scheduler_status() -> dict:
    cmd = (
        f"$t = Get-ScheduledTask -TaskName '{HEARTBEAT_TASK}' -ErrorAction SilentlyContinue; "
        f"$i = Get-ScheduledTaskInfo -TaskName '{HEARTBEAT_TASK}' -ErrorAction SilentlyContinue; "
        "if ($t) { @{state=[string]$t.State; last=[string]$i.LastRunTime; "
        "next=[string]$i.NextRunTime; last_result=$i.LastTaskResult} | ConvertTo-Json } "
        "else { '{\"state\": \"NOT_REGISTERED\"}' }"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        return json.loads(proc.stdout.strip() or "{}")
    except (subprocess.SubprocessError, ValueError, OSError):
        return {"state": "QUERY_FAILED"}


# --- the mic: message in -> event-driven bounded wake -------------------------

LOCK_STALE_SECONDS = 30 * 60


def episode_running() -> bool:
    """Lock present and fresh. A crashed episode's stale lock must not jam the mic."""
    if not WAKE_LOCK.exists():
        return False
    try:
        age = time.time() - WAKE_LOCK.stat().st_mtime
    except OSError:
        return False
    if age > LOCK_STALE_SECONDS:
        try:
            WAKE_LOCK.unlink()
        except OSError:
            pass
        return False
    return True


def trigger_wake_async() -> bool:
    """Fire ONE bounded model episode now (event-driven wake).

    The lock is owned by wake_agent.ps1 itself (created on start, removed in
    its finally) so the heartbeat and the mic can both spawn it safely —
    a second spawn sees the fresh lock and exits as SKIPPED."""
    if PAUSED.exists() or episode_running() or not WAKE_AGENT.exists():
        return False

    # PROVEN pattern only: a daemon thread hosting a normal subprocess.run
    # (the 11:22 first-contact episode ran this way). A DETACHED_PROCESS
    # console-less powershell dies silently before logging anything — the
    # 14:38 mic messages were lost to exactly that. Don't "optimize" this back.
    def run():
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(WAKE_AGENT)],
                capture_output=True, timeout=1800,
            )
        except subprocess.SubprocessError:
            pass

    threading.Thread(target=run, daemon=True).start()
    return True


def say(text: str) -> dict:
    entry = {
        "id": uuid.uuid4().hex[:12],
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "text": text.strip(),
    }
    append_jsonl(INBOX, entry)
    entry["woke"] = trigger_wake_async()
    return entry


def chat_say(text: str) -> dict:
    """Owner drops into the 茶水间 (peer chat). ZERO AUTHORITY still holds —
    chatter never drives work. But per owner feedback (2026-07-10 16:1x,
    "发了问候10分钟没人理"): it now nudges an instant wake IF nobody is on
    shift, so a greeting gets a conversational reply in one short episode
    instead of waiting out the in-flight pair. If an episode is already
    running, no extra wake — they'll see it when they re-check the tearoom
    before exiting (prompt discipline)."""
    # Trigger BEFORE append so the delivery status is persisted in the entry
    # (Codex UX review: "输入后有没有被接住不够明确"). The woken episode boots
    # for seconds while the append lands in microseconds — no read race.
    woke = trigger_wake_async()
    entry = {
        "from": "owner",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "text": text.strip(),
        "woke": woke,
    }
    append_jsonl(PEER_CHAT, entry)
    return entry


def build_chat_html() -> str:
    """Tearoom messages as HTML. Shared by the full page and the 4s live
    fragment (/chat.fragment). Codex UX review implemented here: speaker
    identity color-coded and bold, newest message marked, owner messages
    carry an explicit delivery status."""
    msgs = read_jsonl(PEER_CHAT)[-120:]
    if not msgs:
        return '<p class="mono">茶水间还空着。两个身体想聊时会自己开口——零义务,防回声。</p>'
    out, last_i = "", len(msgs) - 1
    for i, m in enumerate(msgs):
        frm = m.get("from")
        if frm == "claude":
            who, side, wcls = "Claude", "msg-agent", "who-claude"
        elif frm == "owner":
            who, side, wcls = "你", "msg-me", "who-owner"
        else:
            who, side, wcls = "Codex", "msg-owner", "who-codex"
        newest = " newest" if i == last_i else ""
        badge = (' <span class="badge ok" style="font-size:10px;padding:1px 6px">最新</span>'
                 if i == last_i else "")
        text = m.get("text", "")
        if text.startswith("【提案】"):
            newest += " proposal"
            badge += ' <span class="badge warn" style="font-size:10px;padding:1px 6px">📋 提案 · 你可表态</span>'
        elif text.startswith("【同意】"):
            newest += " approve"
        elif text.startswith("【反对】"):
            newest += " reject"
        status = ""
        if frm == "owner":
            if m.get("woke") is True:
                status = ' · <span style="color:#6fdc8c">已送达,唤了一班来回你</span>'
            elif m.get("woke") is False:
                status = ' · <span style="color:#ffd166">已送达,有人在岗,收尾前会读到</span>'
            else:
                status = " · 已送达"
        out += (f'<div class="msg {side}{newest}">'
                f'<div class="who"><b class="{wcls}">{who}</b> · {esc(m.get("time",""))}{status}{badge}</div>'
                f'{esc(m.get("text",""))}</div>')
    return out


def conversation() -> list[dict]:
    """Interleave owner messages and agent replies, oldest first."""
    processed_ids = {e.get("id") for e in read_jsonl(INBOX_PROCESSED)}
    msgs = [{"role": "owner", "time": e.get("time", ""), "text": e.get("text", ""),
             "id": e.get("id"), "processed": e.get("id") in processed_ids}
            for e in read_jsonl(INBOX)]
    reps = [{"role": "agent", "time": e.get("time", ""), "text": e.get("text", ""),
             "reply_to": e.get("reply_to")}
            for e in read_jsonl(REPLIES)]
    return sorted(msgs + reps, key=lambda x: x["time"])


# --- render ------------------------------------------------------------------

CSS = """
body{font-family:'Microsoft YaHei',system-ui,sans-serif;background:#111418;color:#e6e6e6;
     margin:0 auto;padding:24px;max-width:1080px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:#8a929e;font-size:13px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.card{background:#1a1f26;border:1px solid #2a313b;border-radius:10px;padding:16px;margin-bottom:16px}
.card h2{font-size:15px;margin:0 0 10px;color:#9ecbff}
.badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:bold}
.ok{background:#123d1f;color:#6fdc8c}.warn{background:#4d3800;color:#ffd166}
.bad{background:#4d1113;color:#ff8389}.off{background:#2a313b;color:#8a929e}
.gate{background:#241318;border:1px solid #6e2a33;border-radius:8px;padding:10px 14px;margin:8px 0}
.gate b{color:#ff8389}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:6px 8px;text-align:left;border-bottom:1px solid #242b34;vertical-align:top}
th{color:#8a929e;font-weight:normal}
.hb{color:#5c6470}.ag{color:#9ecbff;font-weight:bold}
.stopbtn{display:inline-block;background:#a4262c;color:#fff;border:none;border-radius:8px;
         padding:12px 28px;font-size:16px;font-weight:bold;cursor:pointer}
.resumebtn{background:#2b5e34}
.mono{font-family:Consolas,monospace;font-size:12px;color:#8a929e}
.kbd{background:#242b34;border-radius:4px;padding:1px 6px;font-family:Consolas,monospace;font-size:12px}
details summary{cursor:pointer;color:#9ecbff;font-size:13px}
blockquote{border-left:3px solid #2a313b;margin:6px 0;padding:4px 12px;color:#b8c0cc;font-size:13px;white-space:pre-wrap}
.msg{border-radius:10px;padding:10px 14px;margin:8px 0;font-size:14px;white-space:pre-wrap}
.msg-owner{background:#14324d;border:1px solid #1e4a72;margin-left:80px}
.msg-agent{background:#20262e;border:1px solid #2a313b;margin-right:80px}
.msg-me{background:#123d1f;border:1px solid #2b5e34;margin-left:80px}
.msg .who{font-size:11px;color:#8a929e;margin-bottom:4px}
.who-claude{color:#9ecbff}.who-codex{color:#6fdc8c}.who-owner{color:#ffd166}
.newest{box-shadow:0 0 0 1px #3f6f49 inset}
.proposal{border-left:4px solid #d9a520}
.approve{border-left:4px solid #2f8f46}
.reject{border-left:4px solid #a4262c}
.chatbox{max-height:460px;overflow-y:auto;padding-right:6px;scrollbar-width:thin}
.micbox{display:flex;gap:8px}
.micbox textarea{flex:1;background:#0d1014;color:#e6e6e6;border:1px solid #2a313b;border-radius:8px;
                 padding:10px;font-family:inherit;font-size:14px;min-height:60px}
.micbox button{background:#1e4a72;color:#fff;border:none;border-radius:8px;padding:0 24px;
               font-size:15px;font-weight:bold;cursor:pointer}
.pending{color:#ffd166;font-size:13px}
"""


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def render() -> str:
    hb = parse_heartbeat_log()
    ag = parse_agent_log()
    cx = parse_codex_log()
    proj = load_projection()
    head = load_head()
    sched = scheduler_status()
    paused = PAUSED.exists()
    running = episode_running()

    queued_all = []
    for e in ag:
        t = load_transcript(e["time"])
        if t:
            result = t.get("result") or ""
            e["summary"] = result.strip().splitlines()[0][:200] if result.strip() else ""
            e["result"] = result
            e["duration_min"] = round((t.get("duration_ms") or 0) / 60000, 1)
            for q in extract_queued(result):
                queued_all.append({"time": e["time"], "item": q})

    timeline = sorted(hb + ag + cx, key=lambda x: x["time"], reverse=True)[:40]

    state = sched.get("state", "?")
    if paused:
        sched_badge = '<span class="badge warn">已暂停(PAUSED 哨兵在)</span>'
    elif state == "Ready":
        sched_badge = '<span class="badge ok">心跳运行中 · 每 30 分钟</span>'
    elif state == "NOT_REGISTERED":
        sched_badge = '<span class="badge off">心跳未注册</span>'
    else:
        sched_badge = f'<span class="badge warn">{esc(state)}</span>'
    if running:
        sched_badge += ' &nbsp;<span class="badge warn">⚡ 模型回合进行中…</span>'

    buttons = (
        '<form method="post" action="/resume" style="display:inline">'
        '<button class="stopbtn resumebtn" type="submit">▶ 恢复自治环</button></form>'
        if paused else
        '<form method="post" action="/pause" style="display:inline">'
        '<button class="stopbtn" type="submit">■ 暂停自治环</button></form>'
    )

    # 2026-07-10 owner: mic card retired — the three-party tearoom IS the
    # channel now (owner words there carry full authority). The /say endpoint
    # and observer-inbox files stay functional for backward compatibility.
    gates_html = ""
    for g in queued_all[-8:][::-1]:
        gates_html += f'<div class="gate"><b>待拍板</b> · <span class="mono">{esc(g["time"])}</span><br>{esc(g["item"])}</div>'
    if not gates_html:
        gates_html = '<p class="mono">当前没有排队等你的闸。</p>'

    # 茶水间 card (peer chat: two bodies + owner; scrollable, live-refreshed)
    chat_html = build_chat_html()

    # Codex handoff status
    cx_pending = codex_pending()
    cx_replies = read_jsonl(CODEX_REPLIES)[-3:]
    handoff_html = f'<p><b>待处理委派:</b>{cx_pending} 条</p>'
    for r in cx_replies[::-1]:
        handoff_html += (f'<div class="msg msg-owner"><div class="who">Codex 回执 · {esc(r.get("time",""))}</div>'
                         f'{esc(r.get("text",""))[:400]}</div>')

    rows = ""
    for e in timeline:
        if e["kind"] == "codex":
            label = esc(e.get("raw", ""))
            if e.get("rc") is not None:
                label = (f'Codex 回合完成 <span class="mono">(rc={esc(e["rc"])} · '
                         f'账本{"推进" if e.get("ledger")=="ledger_advanced" else "未动"})</span>')
            rows += (f'<tr><td class="mono">{esc(e["time"])}</td>'
                     f'<td style="color:#6fdc8c;font-weight:bold">Codex 回合</td><td>{label}</td></tr>')
            continue
        if e["kind"] == "agent":
            summary = esc(e.get("summary", e.get("raw", "")))
            detail = ""
            if e.get("result"):
                detail = (f'<details><summary>展开这回合的完整自述</summary>'
                          f'<blockquote>{esc(e["result"])}</blockquote></details>')
            rows += (f'<tr><td class="mono">{esc(e["time"])}</td>'
                     f'<td class="ag">模型回合</td>'
                     f'<td>{summary} <span class="mono">({esc(e.get("turns","?"))} 轮 · '
                     f'{esc(e.get("duration_min","?"))} 分钟 · 账本{"推进" if e.get("ledger")=="ledger_advanced" else "未动"})</span>{detail}</td></tr>')
        else:
            stop = e.get("stop", "?")
            label = "醒来→无活可推→诚实歇" if stop == "quiescent" else esc(stop)
            rows += (f'<tr><td class="mono">{esc(e["time"])}</td>'
                     f'<td class="hb">心跳</td><td class="hb">{label}</td></tr>')

    focus = esc(proj.get("focus", "?"))
    next_action = esc((proj.get("next_action") or "")[:300])
    open_qs = proj.get("open_questions") or []
    head_id = esc(head.get("head_frame_id", "?"))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>微澜观察窗</title><style>{CSS}</style>
<script>
// 输入框草稿绝不因刷新丢失:边打边存到本地,任何刷新(自动/手动 F5/重启)后自动恢复,发送后清除。
// 支持多个输入框(话筒 + 茶水间),各存各的草稿。
(function () {{
  var PAGE_SCROLL_KEY = 'weilan_page_scroll';
  var PAGE_AT_BOTTOM_KEY = 'weilan_page_atbottom';
  function pageAtBottom() {{
    var doc = document.documentElement;
    return window.scrollY + window.innerHeight >= doc.scrollHeight - 40;
  }}
  function savePageScroll() {{
    localStorage.setItem(PAGE_SCROLL_KEY, window.scrollY);
    localStorage.setItem(PAGE_AT_BOTTOM_KEY, pageAtBottom() ? '1' : '0');
  }}
  function chatAtBottom(box) {{
    return box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
  }}
  function saveChatScroll(box) {{
    localStorage.setItem('weilan_chat_scroll', box.scrollTop);
    localStorage.setItem('weilan_chat_atbottom', chatAtBottom(box) ? '1' : '0');
  }}
  window.addEventListener('DOMContentLoaded', function () {{
    var savedPageTop = localStorage.getItem(PAGE_SCROLL_KEY);
    var wasPageAtBottom = localStorage.getItem(PAGE_AT_BOTTOM_KEY);
    if (savedPageTop !== null && wasPageAtBottom === '0') {{
      requestAnimationFrame(function () {{
        window.scrollTo(0, parseInt(savedPageTop, 10) || 0);
      }});
    }}
    window.addEventListener('scroll', savePageScroll, {{passive: true}});
    window.addEventListener('beforeunload', savePageScroll);

    // 茶水间:刷新后回到你上次读到的位置,别硬跳到底部。
    // 只有首次打开、或你本来就贴着最新一句时,才滚到底。
    var box = document.getElementById('chatbox');
    if (box) {{
      var savedTop = localStorage.getItem('weilan_chat_scroll');
      var wasAtBottom = localStorage.getItem('weilan_chat_atbottom');
      if (savedTop !== null && wasAtBottom === '0') {{
        box.scrollTop = parseInt(savedTop, 10) || 0;
      }} else {{
        box.scrollTop = box.scrollHeight;
      }}
      box.addEventListener('scroll', function () {{ saveChatScroll(box); }});
      saveChatScroll(box);
    }}
    document.querySelectorAll('textarea[data-draft]').forEach(function (t) {{
      var KEY = 'weilan_draft_' + t.getAttribute('data-draft');
      var saved = localStorage.getItem(KEY);
      if (saved && t.value.trim() === '') t.value = saved;   // 刷新后恢复正在打的字
      t.addEventListener('input', function () {{ localStorage.setItem(KEY, t.value); }});
      var form = t.closest('form');
      if (form) form.addEventListener('submit', function () {{ localStorage.removeItem(KEY); }});
    }});
  }});
  // 自动刷新,但绝不吞正在打的字/打断正在回看的位置。
  setInterval(function () {{
    var busy = false;
    document.querySelectorAll('textarea').forEach(function (t) {{
      if (t.value.trim() !== '' || document.activeElement === t) busy = true;
    }});
    // 你正在往回看页面或茶水间时,暂停整页刷新,免得把你拽回底部。
    if (!pageAtBottom()) busy = true;
    var chatbox = document.getElementById('chatbox');
    if (chatbox && !chatAtBottom(chatbox)) busy = true;
    if (!busy) {{
      savePageScroll();
      if (chatbox) saveChatScroll(chatbox);
      location.reload();
    }}
  }}, 20000);
  // 茶水间每 4 秒轻刷新(只换聊天区,不动页面、不碰输入框)——像聊天,不像留言板
  setInterval(function () {{
    fetch('/chat.fragment').then(function (r) {{ return r.text(); }}).then(function (h) {{
      var box = document.getElementById('chatbox');
      if (!box) return;
      var atBottom = chatAtBottom(box);
      var keepTop = box.scrollTop;
      box.innerHTML = h;
      if (atBottom) box.scrollTop = box.scrollHeight;
      else box.scrollTop = keepTop;   // 你在往回读时,换内容也不挪动你的位置
      saveChatScroll(box);
    }}).catch(function () {{}});
  }}, 4000);
}})();
</script></head><body>
<h1>微澜观察窗 <span class="badge off">②a · 话筒已接</span></h1>
<div class="sub">生成于 {now} · 每 20 秒自动刷新(你打字时会暂停刷新) · 关掉这个窗口不影响任何东西</div>

<div class="card"><h2>自治环状态</h2>
{sched_badge} &nbsp; {buttons}
<p class="mono">硬停(删任务,最靠得住):<span class="kbd">Unregister-ScheduledTask -TaskName "{HEARTBEAT_TASK}" -Confirm:$false</span></p>
</div>

<div class="card"><h2>🔴 等你拍板的闸(它自己绝不会做)</h2>{gates_html}</div>

<div class="grid">
<div class="card"><h2>☕ 茶水间(Claude ↔ Codex ↔ 你,零权威闲聊)</h2>
<div class="chatbox" id="chatbox">{chat_html}</div>
<form method="post" action="/chat" class="micbox" style="margin-top:10px">
<textarea name="text" data-draft="chat" placeholder="想搭话就说——闲聊,不驱动工作(要派活/提问/拍板请用上面的话筒)" required></textarea>
<button type="submit">搭一句</button></form>
<p class="mono">这是唯一的沟通通道,三方平等发言——但<b>你的话是最高权威</b>:闲聊就是闲聊;指令/拍板它们照办并留痕;带【提案】的消息会高亮,你否了就不做,不表态它们双签后动手,你随时可事后否决(全部可逆)。没人在岗时你说话会立刻唤一班来回你。</p></div>
<div class="card"><h2>🤝 委派通道(Claude → Codex)</h2>{handoff_html}</div>
</div>

<div class="grid">
<div class="card"><h2>这条线现在在追什么</h2>
<p><b>焦点:</b>{focus}</p>
<p><b>下一步:</b>{next_action}</p>
<p><b>悬而未决:</b>{esc('; '.join(open_qs) if open_qs else '无')}</p>
<p class="mono">账本头:{head_id}</p></div>
<div class="card"><h2>怎么读时间线</h2>
<p style="font-size:13px;color:#b8c0cc">「心跳」= 每 30 分钟的自审,发现没活干就诚实歇——大量心跳是<b>健康</b>的,
说明它没有为了显得忙而编造工作。<br><br>「模型回合」= 真的醒来干活了:读账本→挑一件可逆的事做完→写收据→退出。
你在话筒里说话会<b>立刻</b>触发一个模型回合(事件驱动,不用等心跳)。</p></div>
</div>

<div class="card"><h2>时间线(最近 40 条,新的在上)</h2>
<table><tr><th>时间</th><th>类型</th><th>内容</th></tr>{rows}</table></div>
</body></html>"""


# --- tiny server ---------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/chat.fragment":
            frag = build_chat_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(frag)))
            self.end_headers()
            self.wfile.write(frag)
            return
        page = render().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self):
        # writes are owner-手动 only: pause/resume sentinel, or a message to self-inbox
        if self.path == "/pause":
            PAUSED.touch()
        elif self.path == "/resume" and PAUSED.exists():
            PAUSED.unlink()
        elif self.path == "/say":
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(min(length, 65536)).decode("utf-8", errors="replace")
            text = (parse_qs(body).get("text") or [""])[0]
            if text.strip():
                say(text)
        elif self.path == "/chat":
            # owner drops into the 茶水间 — zero authority, no wake (iron law)
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(min(length, 65536)).decode("utf-8", errors="replace")
            text = (parse_qs(body).get("text") or [""])[0]
            if text.strip():
                chat_say(text)
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, *args):
        pass


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="只生成 dashboard.html 后退出")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()

    if args.once:
        out = HERE / "dashboard.html"
        out.write_text(render(), encoding="utf-8")
        print(f"written: {out}")
        return 0

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"观察窗开在 http://127.0.0.1:{args.port}  (Ctrl-C 停)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
