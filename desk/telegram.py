"""텔레그램 봇: 수집 알림 보내기 + 폰에서 보낸 명령 처리.

- 학교 PC 를 밖에 공개하지 않도록, 이 프로그램이 텔레그램에 '새 명령 있나?' 하고 묻는 방식(롱 폴링)입니다.
- 화면에 표시된 연결 코드를 보낸 텔레그램 계정 하나만 명령할 수 있습니다.
- 학생 이름·조퇴 사유는 텔레그램으로 보내지 않습니다 (인원수만).
"""

import json
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from . import queries
from .collect import SOURCE_NAMES

HELP = (
    "📋 업무 데스크 명령\n"
    "오늘 — 오늘 일정·마감·외출 인원\n"
    "마감 — 7일 안의 마감\n"
    "새로고침 — 지금 바로 수집\n"
    "요약 — 최근 받은 공문·메시지\n"
    "도움말 — 이 안내\n"
    "(/today /deadlines /refresh /summary /help 도 됩니다)"
)
COMMANDS = {
    "오늘": "today", "/today": "today",
    "마감": "deadlines", "/deadlines": "deadlines",
    "새로고침": "refresh", "/refresh": "refresh", "수집": "refresh",
    "요약": "summary", "/summary": "summary",
    "도움말": "help", "/help": "help", "/start": "help",
}
BOT_MENU = [
    {"command": "today", "description": "오늘 일정·마감"},
    {"command": "deadlines", "description": "7일 안의 마감"},
    {"command": "refresh", "description": "지금 수집"},
    {"command": "summary", "description": "최근 받은 자료"},
    {"command": "help", "description": "도움말"},
]
WD = "월화수목금토일"


def fmt_day(iso, today):
    d = date.fromisoformat(iso)
    rel = {0: "오늘", 1: "내일", 2: "모레"}.get((d - today).days)
    label = f"{d.month}/{d.day}({WD[d.weekday()]})"
    return f"{label} {rel}" if rel else label


def fmt_event(e, today, detail):
    when = fmt_day(e["date"], today) + (f" {e['time']}" if e.get("time") else "")
    mark = "⏰" if e["kind"] == "deadline" else "•"
    if detail == "counts":
        return f"{mark} {when} {e['category'] or ''}".rstrip()
    title = e.get("item_title") or e["text"]
    return f"{mark} {when} {title[:50]}"


class Bot:
    def __init__(self, db, config, collector=None):
        self.db, self.config, self.collector = db, config, collector
        self.stop_event = threading.Event()
        self.thread = None
        self.status = "꺼짐"

    # ---- 텔레그램 API ----
    @property
    def token(self):
        return self.config["telegram_token"]

    def api(self, method, params=None, timeout=35):
        url = f"{self.config['telegram_api_base'].rstrip('/')}/bot{self.token}/{method}"
        data = json.dumps(params or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(body.get("description", "텔레그램 오류"))
        return body["result"]

    def send(self, text, chat_id=None):
        chat_id = chat_id or self.chat_id
        if not (self.token and chat_id):
            return False
        self.api("sendMessage", {"chat_id": chat_id, "text": text[:4000]}, timeout=15)
        return True

    # ---- 연결 (내 계정만) ----
    @property
    def chat_id(self):
        return self.db.get_meta("telegram_chat_id")

    def pair_code(self, renew=False):
        code = self.db.get_meta("telegram_pair_code")
        if renew or not code:
            code = f"{secrets.randbelow(10**6):06d}"
            self.db.set_meta("telegram_pair_code", code)
        return code

    def unpair(self):
        self.db.set_meta("telegram_chat_id", None)
        self.pair_code(renew=True)

    # ---- 명령 처리 ----
    def handle_update(self, update):
        msg = update.get("message") or {}
        chat = msg.get("chat") or {}
        text = (msg.get("text") or "").strip()
        if not text or chat.get("type") != "private":
            return None
        cid = chat.get("id")
        words = text.split()
        if words[0] in ("/start", "연결") and len(words) > 1:
            if words[1] == self.pair_code():
                self.db.set_meta("telegram_chat_id", cid)
                self.pair_code(renew=True)  # 한 번 쓴 코드는 버림
                self.db.log("텔레그램 연결됨")
                return self.reply(cid, "✅ 업무 데스크와 연결되었습니다.\n\n" + HELP)
            # 코드를 마구 넣어 보는 것을 막으려고 5번 틀리면 코드를 바꿈
            fails = (self.db.get_meta("telegram_pair_fails") or 0) + 1
            if fails >= 5:
                self.pair_code(renew=True)
                self.db.log("텔레그램 연결 코드가 5번 틀려 새 코드로 바꿨습니다", "warn")
                fails = 0
            self.db.set_meta("telegram_pair_fails", fails)
            return self.reply(cid, "연결 코드가 맞지 않습니다. 업무 데스크 ⚙ 설정의 코드를 확인하세요.")
        if cid != self.chat_id:
            return None  # 연결되지 않은 사람에게는 답하지 않음
        cmd = COMMANDS.get(words[0].split("@")[0].lower())
        return self.reply(cid, self.answer(cmd))

    def reply(self, cid, text):
        self.send(text, cid)
        return text

    def answer(self, cmd, today=None):
        today = today or date.today()
        detail = self.config["telegram_detail"]
        if cmd == "today":
            s = queries.today_summary(self.db, today)
            lines = [f"📅 {fmt_day(today.isoformat(), today)} 업무"]
            if s["overdue"]:
                lines.append(f"\n⚠ 지난 마감 {len(s['overdue'])}건")
                lines += [fmt_event(e, today, detail) for e in s["overdue"][:5]]
            lines.append(f"\n오늘 일정 {len(s['today'])}건")
            lines += [fmt_event(e, today, detail) for e in s["today"][:10]]
            lines.append(f"\n7일 안 마감 {len(s['week_deadlines'])}건")
            lines += [fmt_event(e, today, detail) for e in s["week_deadlines"][:5]]
            out = sum(1 for p in s["passes"] if p["kind"] == "out")
            early = sum(1 for p in s["passes"] if p["kind"] == "early")
            lines.append(f"\n🎫 오늘 조퇴 {early}명 · 외출 {out}명")
            return "\n".join(lines)
        if cmd == "deadlines":
            evs = [e for e in queries.events_between(self.db, today, today + timedelta(days=7), include_done=False)
                   if e["kind"] == "deadline"]
            if not evs:
                return "7일 안의 마감이 없습니다 👍"
            return "⏰ 7일 안의 마감\n" + "\n".join(fmt_event(e, today, detail) for e in evs[:20])
        if cmd == "refresh":
            if not self.collector:
                return "수집기를 사용할 수 없습니다."
            stats = self.collector.run("telegram")
            if stats.get("busy"):
                return "이미 수집 중입니다. 잠시 뒤 다시 보내 주세요."
            return self.collect_message(stats) or "새로 들어온 자료가 없습니다."
        if cmd == "summary":
            rows = self.db.query("SELECT * FROM items WHERE source != 'paste' ORDER BY received_at DESC LIMIT 3")
            if not rows:
                return "아직 받은 자료가 없습니다."
            parts = []
            for r in rows:
                head = f"[{SOURCE_NAMES.get(r['source'], r['source'])}] {r['received_at'][5:16]} · {r['category']}"
                if detail == "counts":
                    parts.append(head)
                    continue
                head += f"\n{r['title'][:60]}"
                if detail == "summary":
                    head += "\n" + "\n".join("  " + s for s in json.loads(r["summary"] or "[]")[:4])
                parts.append(head)
            return "📥 최근 받은 자료\n\n" + "\n\n".join(parts)
        return HELP

    def collect_message(self, stats, today=None):
        """수집 결과 알림. 새 자료가 없으면 None (알림 안 보냄)."""
        if not stats.get("items"):
            return None
        today = today or date.today()
        detail = self.config["telegram_detail"]
        lines = [f"📥 새 자료 {stats['items']}건 · 일정 후보 {stats['events']}건 (마감 {stats['deadlines']}건)"]
        ids = stats.get("new_ids") or []
        if ids and detail != "counts":
            marks = ",".join("?" * len(ids))
            for r in self.db.query(f"SELECT source, title FROM items WHERE id IN ({marks}) "
                                   "ORDER BY received_at DESC LIMIT 5", ids):
                lines.append(f"• [{SOURCE_NAMES.get(r['source'], r['source'])}] {r['title'][:50]}")
            evs = self.db.query(
                f"SELECT e.*, CASE WHEN i.source = 'school' THEN NULL ELSE i.title END AS item_title "
                f"FROM events e JOIN items i ON i.id = e.item_id "
                f"WHERE e.item_id IN ({marks}) AND e.kind = 'deadline' AND e.date >= ? ORDER BY e.date LIMIT 3",
                ids + [today.isoformat()])
            if evs:
                lines.append("\n가까운 마감 후보 (수신함에서 체크하면 달력에 들어갑니다)")
                lines += [fmt_event(e, today, detail) for e in evs]
        return "\n".join(lines)

    def notify_collect(self, stats, reason):
        if reason == "telegram":
            return  # 명령에 대한 답장으로 이미 보냄
        msg = self.collect_message(stats)
        if msg and self.chat_id:
            self.send(msg)

    # ---- 폴링 ----
    def loop(self):
        offset, backoff, menu_set, last_token = None, 5, False, None
        while not self.stop_event.is_set():
            if not self.token:
                self.status = "토큰 없음"
                self.stop_event.wait(10)
                continue
            if self.token != last_token:  # 설정에서 토큰을 바꾼 경우
                offset, menu_set, last_token = None, False, self.token
            try:
                if not menu_set:
                    self.api("setMyCommands", {"commands": BOT_MENU}, timeout=15)
                    menu_set = True
                updates = self.api("getUpdates", {"timeout": 25, "offset": offset,
                                                  "allowed_updates": ["message"]}, timeout=35)
                self.status = "연결됨" if self.chat_id else "연결 대기 (코드를 보내세요)"
                backoff = 5
                for u in updates:
                    offset = u["update_id"] + 1
                    try:
                        self.handle_update(u)
                    except Exception as e:
                        self.db.log(f"텔레그램 명령 처리 오류: {e}", "error")
            except (urllib.error.URLError, OSError, RuntimeError, ValueError) as e:
                self.status = f"오류: {getattr(e, 'reason', e)}"
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, 300)

    def start(self):
        self.thread = threading.Thread(target=self.loop, name="telegram", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
