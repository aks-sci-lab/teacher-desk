"""텔레그램 봇: 수집 알림 보내기 + 폰에서 보낸 명령 처리.

- 학교 PC 를 밖에 공개하지 않도록, 이 프로그램이 텔레그램에 '새 명령 있나?' 하고 묻는 방식(롱 폴링)입니다.
- 화면에 표시된 연결 코드를 보낸 텔레그램 계정 하나만 명령할 수 있습니다.
- 학생 이름·조퇴 사유는 텔레그램으로 보내지 않습니다 (인원수만). 증 발급은 번호로, 답장의 이름은 '김○○' 처럼.
- 브리핑·검색은 제목과 요약까지, 원문·파일은 단추나 '원문 n'·'파일 n' 으로 달라고 할 때만 (사용자 결정 2026-10-28).
"""

import calendar
import json
import re
import secrets
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from . import VERSION, absence, ai, assist, passlink, printing, providers, queries
from .collect import SOURCE_NAMES, item_file

HELP = (
    "📋 업무 데스크 명령\n"
    "\n■ 일정·마감\n"
    "오늘 — 오늘 일정·오늘 마감·외출 인원\n"
    "마감 — 7일 안의 마감\n"
    "브리핑 — 오늘 + 지난 1주일 AI 요약\n"
    "내일 수업 · 금요일 시간표 — 내 수업\n"
    "\n■ 학생\n"
    "조퇴 7 [13:30] · 외출 7 [13:30] [14:30] — 사유는 단추로, 발급 뒤 인쇄·웹 링크 선택\n"
    "결석계 이름 번호 종류 기간 — 예: 결석계 김가온 7 병결 10/29~11/3\n"
    "종류: 병결·인정결·기타결·미인정결 (주말·학사일정 공휴일·휴업일 제외, 앱에 저장만)\n"
    "\n■ 자료\n"
    "낱말만 보내면 메시지·공문 검색 · 원문 2 · 파일 2 — 2번 자료\n"
    "요약 — 최근 받은 자료 · 새로고침 — 지금 수집\n"
    "\n■ 기타\n"
    "도움말 · 메뉴 · 취소 · 상태 · /ask 질문(일반 질문 AI, 업무자료는 붙이지 않음)\n"
    "조퇴증·외출증·결석계·자료 검색만 보내면 단계별로 안내합니다. 목록에 없는 말은 AI 검색 여부를 먼저 묻습니다."
)
COMMANDS = {
    "오늘": "today", "/today": "today",
    "마감": "deadlines", "/deadlines": "deadlines",
    "새로고침": "refresh", "/refresh": "refresh", "수집": "refresh",
    "요약": "summary", "/summary": "summary",
    "브리핑": "brief", "/brief": "brief",
    "도움말": "help", "/help": "help", "/start": "help",
    "메뉴": "menu", "/menu": "menu",
    "취소": "cancel", "/cancel": "cancel",
    "상태": "status", "/status": "status",
    "자료 검색": "guided-search", "내 수업": "lessons", "/lessons": "lessons",
}
# 뒤에 낱말이 붙는 명령
ARG_COMMANDS = {
    "검색": "search", "/search": "search", "찾기": "search",
    "원문": "text", "/text": "text",
    "파일": "file", "/file": "file",
    "조퇴": "early", "/early": "early", "조퇴증": "early",
    "외출": "out", "/out": "out", "외출증": "out",
    "결석계": "absence", "결석": "absence", "/absence": "absence",
    "/ask": "general",
}
# 명령 뒤에 붙어도 같은 명령으로 보는 말 ('브리핑 보여줘'·'오늘 일정'). 그 밖의 낱말이 붙으면 명령이 아니라 검색·질문
POLITE_TAILS = {"보여줘", "알려줘", "보내줘", "해줘", "줘", "좀", "주세요", "보여주세요", "알려주세요", "부탁", "부탁해", "부탁해요", "요"}
COMMAND_TAILS = {"today": {"일정", "업무"}, "deadlines": {"일정"}}
P_ARG_NUMBER = re.compile(r"^\d")   # 숫자로 시작하면 명령 ('1시' 같은 잘못된 번호는 명령이 안내)
BOT_MENU = [
    {"command": "brief", "description": "브리핑 (오늘 + 1주일 요약)"},
    {"command": "today", "description": "오늘 일정·마감"},
    {"command": "deadlines", "description": "7일 안의 마감"},
    {"command": "search", "description": "자료 검색: /search 낱말"},
    {"command": "early", "description": "조퇴증: /early 번호 [시각]"},
    {"command": "out", "description": "외출증: /out 번호 [나감] [복귀]"},
    {"command": "absence", "description": "결석계: /absence 이름 번호 병결 10/29~11/3"},
    {"command": "refresh", "description": "지금 수집"},
    {"command": "summary", "description": "최근 받은 자료"},
    {"command": "help", "description": "도움말"},
    {"command": "menu", "description": "자주 쓰는 메뉴와 단계별 작성"},
    {"command": "cancel", "description": "진행 중인 입력·발급 선택 취소"},
    {"command": "status", "description": "AI·수집·시간표 준비 상태"},
    {"command": "lessons", "description": "오늘 내 수업"},
    {"command": "ask", "description": "일반 질문 (업무자료 제외): /ask 질문"},
]
# 화면(passes.js)의 사유 단추와 같음
REASONS = {
    "early": ["병원 진료", "몸이 아픔 (보건실 확인)", "가정 사정", "학교 대표 활동"],
    "out": ["병원 진료", "개인 용무", "가정 사정", "학교 행사 준비"],
}
KIND = {"early": "조퇴", "out": "외출"}
P_TIME = re.compile(r"^(\d{1,2})(?::|시\s*)?(\d{2})?분?$")
TEXT_LIMIT = 3800       # 텔레그램 한 번에 4096자
TEXT_AS_FILE = 3 * TEXT_LIMIT
FILE_LIMIT = 45 * 1024 * 1024   # 봇이 보낼 수 있는 파일은 50MB 까지
WD = "월화수목금토일"
DIALOG_TTL = 10 * 60
# 화면을 덜 가리게 2줄 (요약·상태·취소·자료 검색·새로고침은 '메뉴' 의 단추로)
KEYBOARD = [["브리핑", "오늘", "마감", "내 수업"], ["조퇴증", "외출증", "결석계", "메뉴"]]
MENU_MORE = ("자료 검색", "요약", "새로고침", "상태", "취소")


BATCH_LIMIT = 10   # 새 자료가 이보다 많으면 알림에 목록을 싣지 않고 요약만


def cut(text, n=40):
    """n 글자 안으로 줄바꿈 없이, 단어 중간이 아니라 띄어쓰기에서 끊고 …"""
    text = " ".join((text or "").split())
    if len(text) <= n:
        return text
    head = text[:n]
    if " " in head and len(head) - head.rfind(" ") < 12:
        head = head[:head.rfind(" ")]
    return head.rstrip(" ,.·-:~") + "…"


P_SENDER = re.compile(r"\(보낸 사람:\s*(.+?)\)\s*$")


def sender_of(body):
    """쿨메신저 쪽지 끝의 '(보낸 사람: 이름)' → 이름 ('이창헌(이창헌)' 은 한 번만)."""
    m = P_SENDER.search(body or "")
    if not m:
        return ""
    name = m.group(1).strip()
    same = re.fullmatch(r"(.+?)\s*\(\s*\1\s*\)", name)
    return (same.group(1) if same else name)[:20]


def fmt_day(iso, today):
    d = date.fromisoformat(iso)
    rel = {0: "오늘", 1: "내일", 2: "모레"}.get((d - today).days)
    label = f"{d.month}/{d.day}({WD[d.weekday()]})"
    return f"{label} {rel}" if rel else label


def masked(name):
    """'김민준' → '김○○' (텔레그램에는 이름을 다 쓰지 않음)."""
    name = (name or "").strip()
    return name[:1] + "○" * max(len(name) - 1, 1) if name else "○○"


def parse_time(word):
    m = P_TIME.match(word.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None


def rounded_now(now=None):
    """지금 시각을 5분 단위로 올림 (화면과 같음)."""
    now = now or datetime.now()
    total = -(-(now.hour * 60 + now.minute) // 5) * 5
    return f"{total // 60 % 24:02d}:{total % 60:02d}"


def plus_hour(t):
    h, m = map(int, t.split(":"))
    return f"{min(h + 1, 23):02d}:{m:02d}"


def chunks(text, size=TEXT_LIMIT):
    """긴 글을 줄 단위로 나눔."""
    out, cur = [], ""
    for line in text.splitlines():
        while len(line) > size:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:size])
            line = line[size:]
        if len(cur) + len(line) + 1 > size:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        out.append(cur)
    return out or [""]


def lesson_line(lessons):
    if not lessons:
        return "📚 내 수업 없음"
    return f"📚 내 수업 {len(lessons)}시간: " + " · ".join(
        f"{l['p']}교시 {l['cls'] or l['subj']}" + ("(교체)" if l.get("moved") else "") + queries.note_text(l) for l in lessons)


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
        self.conflicts = 0      # 같은 봇을 다른 곳에서도 쓰는 중이라 받은 409 횟수
        self.last_poll = None   # 마지막으로 텔레그램에서 새 글을 확인한 시각
        self.asking = None      # 지금 답을 만드는 질문 (한 번에 하나)
        self.sync = False       # 시험: 질문의 답을 기다렸다가 돌려줌
        self.choice_requests = {}
        self.dialog = None

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

    def send(self, text, chat_id=None, buttons=None, keyboard=False):
        chat_id = chat_id or self.chat_id
        if not (self.token and chat_id):
            return False
        params = {"chat_id": chat_id, "text": text[:4000]}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        elif keyboard:
            params["reply_markup"] = {"keyboard": KEYBOARD, "resize_keyboard": True,
                                      "is_persistent": True, "input_field_placeholder": "메뉴를 고르거나 명령을 입력하세요"}
        self.api("sendMessage", params, timeout=15)
        return True

    def send_file(self, chat_id, name, data, caption=""):
        """파일 하나 보내기 (sendDocument, multipart). 표준 라이브러리만."""
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in (("chat_id", str(chat_id)), ("caption", caption[:1000])):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8"))
        quoted = urllib.parse.quote(name)
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{quoted}"; '
                      f"filename*=UTF-8''{quoted}\r\nContent-Type: application/octet-stream\r\n\r\n").encode("utf-8")
                     + data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        url = f"{self.config['telegram_api_base'].rstrip('/')}/bot{self.token}/sendDocument"
        req = urllib.request.Request(url, data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=120) as r:
            body = json.loads(r.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(body.get("description", "텔레그램 오류"))
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
        self.db.set_meta("telegram_pending", {})
        self.choice_requests.clear()
        self.dialog = None
        self.pair_code(renew=True)

    # ---- 명령 처리 ----
    def handle_update(self, update):
        if update.get("callback_query"):
            return self.handle_button(update["callback_query"])
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
                self.dialog = None
                self.choice_requests.clear()
                self.db.set_meta("telegram_pending", {})
                text = "✅ 업무 데스크와 연결되었습니다.\n\n" + HELP
                self.send(text, cid, keyboard=True)
                return text
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
        head = words[0].split("@")[0].lower()
        cmd = COMMANDS.get(text) or COMMANDS.get(head)
        if cmd and not self.plain_command(text, head, words, cmd):
            cmd = None   # '오늘 식단표' 는 오늘 브리핑이 아니라 자료 검색으로
        if cmd in ("help", "menu"):
            self.dialog = None
            return self.send_menu(cid, full=cmd == "help")
        if cmd == "cancel":
            self.dialog = None
            self.choice_requests.clear()
            self.db.set_meta("telegram_pending", {})
            text = "진행 중인 입력·발급 선택을 취소했습니다. 이미 저장한 기록이나 실행한 인쇄는 취소하지 않습니다."
            if self.asking:
                text += "\n이미 시작한 AI 답변은 계속 만들어집니다."
            self.send(text, cid, keyboard=True)
            return text
        if cmd == "guided-search":
            return self.begin_dialog(cid, "search")
        if cmd == "lessons":
            self.dialog = None
            return self.reply(cid, self.lessons_text(""))
        if cmd and len(words) == 1:
            self.dialog = None
            if cmd == "brief":
                return self.send_brief(cid)
            return self.send_answer(cid, cmd)
        arg_cmd = ARG_COMMANDS.get(head)
        if arg_cmd and self.arg_command_fits(arg_cmd, head, words[1:]):
            self.dialog = None
            return self.run_arg(cid, arg_cmd, words[1:])
        if cmd:
            self.dialog = None
            if cmd == "brief":
                return self.send_brief(cid)
            return self.send_answer(cid, cmd)
        if self.dialog and not text.startswith("/"):
            return self.dialog_input(cid, text)
        if text.startswith("/"):
            self.dialog = None
            return self.offer_choices(cid, text)
        # 자연스러운 말: '7번 조퇴 1시 반'·'브리핑 보여줘'·'3번 원문' 은 명령으로
        found = assist.intent(text)
        if found:
            cmd, args = found
            return self.send_brief(cid) if cmd == "brief" else self.run_arg(cid, cmd, args)
        return self.offer_choices(cid, text)

    @staticmethod
    def plain_command(text, head, words, cmd):
        """인자 없는 명령은 그 낱말 그대로(또는 공손한 꼬리만)일 때만. '/today' 처럼 슬래시는 항상 명령."""
        if head.startswith("/") or text in COMMANDS:
            return True
        tails = POLITE_TAILS | COMMAND_TAILS.get(cmd, set())
        return all(w.strip("?!.~") in tails for w in words[1:])

    @staticmethod
    def arg_command_fits(arg_cmd, head, args):
        """뒤따르는 말이 그 명령에 맞는 모양일 때만 명령 ('결석 인원 알려줘' 는 검색·질문으로)."""
        if head.startswith("/") or not args:
            return True
        if arg_cmd in ("early", "out"):
            return bool(P_ARG_NUMBER.match(args[0]))
        if arg_cmd == "absence":
            joined = " ".join(args)
            return bool(absence.P_DATE.search(joined)) or any(
                re.search(rf"(?<!\S){w}(?!\S)", joined) for w in absence.TYPE_WORDS)
        return True

    def send_menu(self, cid, full=False):
        text = (HELP if full else
                "📋 업무 데스크\n아래 단추나 명령을 쓰세요. 조퇴증·외출증·결석계는 차례대로 입력할 수 있습니다.\n"
                "자료 검색은 AI 없이 빠르게 찾고, 목록에 없는 질문은 AI 검색 여부를 먼저 묻습니다.")
        self.send(text, cid, keyboard=True)
        if not full:   # 아래 단추 판에 없는 것은 여기 단추로
            self.send("그 밖의 명령", cid, [[{"text": name, "callback_data": f"mn:{name}"} for name in MENU_MORE[:3]],
                                          [{"text": name, "callback_data": f"mn:{name}"} for name in MENU_MORE[3:]]])
        return text

    def begin_dialog(self, cid, kind):
        self.dialog = {"id": secrets.token_hex(6), "kind": kind, "step": "query" if kind == "search" else "number",
                       "expires": time.monotonic() + DIALOG_TTL}
        prompt = ("찾을 낱말을 보내세요. 예: 연수 신청\n자료만 검색하며 AI는 실행하지 않습니다."
                  if kind == "search" else "학생 번호를 함께 보내세요. 예: 7\n명단에 있는 번호만 쓸 수 있습니다.")
        self.send(prompt + "\n10분 안에 입력하세요. 그만하려면 '취소'.", cid, keyboard=True)
        return prompt

    def dialog_input(self, cid, text):
        dialog = self.dialog
        if not dialog:
            return self.send_menu(cid)
        if time.monotonic() > dialog["expires"]:
            self.dialog = None
            return self.reply(cid, "입력 시간이 10분을 넘었습니다. 메뉴에서 다시 시작해 주세요.")
        kind, step = dialog["kind"], dialog["step"]
        if kind == "search":
            if len(text) > 100:
                return self.reply(cid, "검색어를 100자 안쪽으로 보내세요. 그만하려면 '취소'.")
            self.dialog = None
            return self.send_search(cid, text)
        if step == "number":
            if not text.isdigit() or len(text) > 5:
                return self.reply(cid, "학생 번호만 보내세요. 예: 7. 그만하려면 '취소'.")
            number = int(text)
            students = [s for s in self.db.students() if s["number"] == number]
            if len(students) != 1:
                return self.reply(cid, f"명단에서 {number}번 학생을 한 명으로 찾지 못했습니다. 번호를 다시 보내거나 앱 명단을 확인하세요.")
            dialog["number"] = number
            dialog["student_name"] = students[0]["name"]  # 이 PC 메모리에만, 답장·단추에는 싣지 않음
            if kind == "absence":
                dialog["step"] = "type"
                buttons = [[{"text": word, "callback_data": f"dlg:{dialog['id']}:type:{n}"}]
                           for n, word in enumerate(absence.TYPE_WORDS)]
                self.send(f"{number}번 결석 종류를 고르세요. 이름은 명단에서 이 PC 안에서만 찾습니다.", cid, buttons)
                return "결석 종류 선택"
            dialog["step"] = "time"
            prompt = (f"{number}번 {KIND[kind]} 시각을 보내세요. "
                      + ("예: 13:30 14:30 (나감·복귀)" if kind == "out" else "예: 13:30"))
            self.send(prompt, cid, [[{"text": "지금 시각으로", "callback_data": f"dlg:{dialog['id']}:now"}]])
            return prompt
        if step == "time":
            args = text.split()
            count = 2 if kind == "out" else 1
            if len(args) != count or any(parse_time(word) is None for word in args):
                return self.reply(cid, "시각을 " + ("13:30 14:30" if kind == "out" else "13:30") + "처럼 보내세요.")
            return self.start_pass(cid, kind, [str(dialog["number"]), *args])
        if step == "type":
            return self.reply(cid, "위의 결석 종류 단추를 눌러 주세요. 그만하려면 '취소'.")
        if step == "date":
            return self.confirm_absence(cid, dialog, text)
        return self.reply(cid, "위의 [앱에 저장] 또는 [취소]를 눌러 주세요.")

    def show(self, cid, message_id, text, buttons):
        """단추 메시지는 새로 보내지 않고 같은 메시지를 고쳐 보여 줌 (고치지 못하면 새로 보냄)."""
        if message_id is not None:
            try:
                self.api("editMessageText", {"chat_id": cid, "message_id": message_id, "text": text[:4000],
                                             "reply_markup": {"inline_keyboard": buttons}}, timeout=15)
                return text
            except (urllib.error.URLError, OSError, RuntimeError):
                pass
        self.send(text, cid, buttons)
        return text

    def confirm_absence(self, cid, dialog, period, message_id=None):
        try:
            fields = self.dialog_absence_fields(dialog, period)
            dates = self.validate_absence(fields)
        except absence.AbsenceError as e:
            return self.reply(cid, str(e))
        dialog.update(step="confirm", period=period)
        prompt = (f"{dialog['number']}번 결석계\n{fields['start_date']}~{fields['end_date']} · 수업일 {len(dates)}일\n"
                  "주말·가져온 학사일정 휴업일을 뺐습니다. 아직 저장하지 않았습니다.")
        return self.show(cid, message_id, prompt, [[
            {"text": "앱에 저장", "callback_data": f"dlg:{dialog['id']}:save"},
            {"text": "취소", "callback_data": f"dlg:{dialog['id']}:cancel"}]])

    def calendar_buttons(self, dialog, year, month):
        """한 달 달력 단추 (일~토). 휴업일·공휴일은 (12), 고른 시작일은 ▶12. callback: cm:YYYYMM 달 이동, cd:YYYYMMDD 날짜."""
        did, start = dialog["id"], dialog.get("cal_start")
        prev_y, prev_m = (year, month - 1) if month > 1 else (year - 1, 12)
        next_y, next_m = (year, month + 1) if month < 12 else (year + 1, 1)
        nop = {"text": " ", "callback_data": f"dlg:{did}:nop"}
        rows = [[{"text": "◀", "callback_data": f"dlg:{did}:cm:{prev_y:04d}{prev_m:02d}"},
                 {"text": f"{year}년 {month}월", "callback_data": f"dlg:{did}:nop"},
                 {"text": "▶", "callback_data": f"dlg:{did}:cm:{next_y:04d}{next_m:02d}"}],
                [{"text": w, "callback_data": f"dlg:{did}:nop"} for w in "일월화수목금토"]]
        last = calendar.monthrange(year, month)[1]
        closed = absence.closed_days(self.db, date(year, month, 1), date(year, month, last))
        for week in calendar.Calendar(firstweekday=6).monthdayscalendar(year, month):
            row = []
            for day in week:
                if not day:
                    row.append(nop)
                    continue
                d = date(year, month, day)
                label = f"▶{day}" if d == start else f"({day})" if d in closed else str(day)
                row.append({"text": label, "callback_data": f"dlg:{did}:cd:{d:%Y%m%d}"})
            rows.append(row)
        if start:
            rows.append([{"text": "하루만", "callback_data": f"dlg:{did}:one"},
                         {"text": "시작일 다시 고르기", "callback_data": f"dlg:{did}:cr"},
                         {"text": "취소", "callback_data": f"dlg:{did}:cancel"}])
        else:
            rows.append([{"text": "취소", "callback_data": f"dlg:{did}:cancel"}])
        return rows

    def show_calendar(self, cid, dialog, message_id, year, month):
        start = dialog.get("cal_start")
        head = (f"{dialog['number']}번 결석 날짜\n"
                + (f"시작일 {start:%m/%d} · 끝나는 날을 누르세요 (같은 날을 또 누르거나 [하루만])"
                   if start else "결석이 시작되는 날을 누르세요"))
        text = head + "\n주말은 누를 수 있고 수업일 계산에서만 뺍니다. (숫자)는 학사일정 휴업일·공휴일입니다.\n글로 보내도 됩니다. 예: 10/29~11/3"
        dialog["cal_month"] = (year, month)
        return self.show(cid, message_id, text, self.calendar_buttons(dialog, year, month))

    def calendar_action(self, cid, dialog, action, message_id):
        """달력 단추. 처리했으면 결과 글, 아니면 None."""
        if dialog["kind"] != "absence" or dialog["step"] != "date":
            return None
        year, month = dialog.get("cal_month") or (date.today().year, date.today().month)
        if action == "nop":
            return ""
        if action.startswith("cm:") and re.fullmatch(r"\d{6}", action[3:]):
            y, m = int(action[3:7]), int(action[7:9])
            if not (2000 <= y <= 2100 and 1 <= m <= 12):
                return self.reply(cid, "달력에 없는 달입니다.")
            return self.show_calendar(cid, dialog, message_id, y, m)
        if action == "cr":
            dialog.pop("cal_start", None)
            return self.show_calendar(cid, dialog, message_id, year, month)
        if action == "one" and dialog.get("cal_start"):
            return self.finish_calendar(cid, dialog, dialog["cal_start"], dialog["cal_start"], message_id)
        if action.startswith("cd:"):
            try:
                picked = datetime.strptime(action[3:], "%Y%m%d").date()
            except ValueError:
                return self.reply(cid, "달력에 없는 날짜입니다.")
            start = dialog.get("cal_start")
            if start is None or picked < start:   # 첫 선택이거나 시작일보다 앞이면 새 시작일
                dialog["cal_start"] = picked
                return self.show_calendar(cid, dialog, message_id, picked.year, picked.month)
            return self.finish_calendar(cid, dialog, start, picked, message_id)
        return None

    def finish_calendar(self, cid, dialog, start, end, message_id):
        period = f"{start.isoformat()}~{end.isoformat()}"
        dialog.pop("cal_start", None)
        text = self.confirm_absence(cid, dialog, period, message_id)
        if dialog["step"] == "date":   # 저장할 수 없는 기간(학사일정 없음 등): 달력으로 다시
            self.show_calendar(cid, dialog, message_id, start.year, start.month)
        return text

    def dialog_absence_fields(self, dialog, period):
        students = [s for s in self.db.students() if s["number"] == dialog["number"]]
        if len(students) != 1 or students[0]["name"] != dialog["student_name"]:
            raise absence.AbsenceError("학생 명단이 바뀌었습니다. 메뉴에서 다시 시작하세요.")
        if len(period) > 100:
            raise absence.AbsenceError("날짜만 100자 안쪽으로 보내세요. 예: 10/29~11/3")
        return absence.parse_command(
            f"{students[0]['name']} {dialog['number']} {dialog['type']} {period}")

    def offer_choices(self, cid, text):
        request_id = secrets.token_hex(6)
        self.choice_requests[request_id] = {"chat_id": cid, "text": text}
        while len(self.choice_requests) > 10:
            del self.choice_requests[next(iter(self.choice_requests))]
        options = [
            [("브리핑", "brief"), ("오늘", "today"), ("마감", "deadlines")],
            [("조퇴증 작성", "early"), ("외출증 작성", "out"), ("결석계 작성", "absence")],
            [("이 내용으로 AI 검색", "ai"), ("AI 없이 자료 검색", "search")],
        ]
        buttons = [[{"text": label, "callback_data": f"choice:{request_id}:{action}"}
                    for label, action in row] for row in options]
        message = ("입력한 내용을 명령어로 알아듣지 못했습니다.\n"
                   "아래 목록의 명령을 보내거나 단추를 선택하세요.\n\n" + HELP
                   + "\n\nAI 검색은 방금 보낸 내용으로 이 PC의 자료를 찾습니다."
                   + ("" if self.config["ai_enabled"] else "\n현재 AI가 꺼져 있어 검색한 자료만 보여 드립니다."))
        self.send(message, cid, buttons)
        return message

    # ---- 질문에 답하기 (assist) ----
    def ask(self, cid, question):
        if not self.config["ai_enabled"]:
            return self.send_found(cid, question, "(AI 를 켜면 자료를 읽고 답해 드립니다: 업무 데스크 설정 → AI 요약)")
        if self.asking:
            return self.reply(cid, f"앞 질문('{self.asking[:20]}')에 답하는 중입니다. 끝나면 다시 보내 주세요.")
        self.asking = question
        if self.sync:
            return self._ask(cid, question)
        self.send("🤔 자료를 찾아 읽는 중… (이 PC 의 AI 라 1~2분 걸립니다)", cid)
        threading.Thread(target=self._ask, args=(cid, question), name="telegram-ask", daemon=True).start()
        return None

    def _ask(self, cid, question):
        try:
            text, ids, cited = assist.answer(self.db, self.config, question)
            self.set_refs(ids)
            buttons = self.item_buttons(cited, ids)
            parts = chunks("💬 " + text + ("\n\n[번호] 자료: 아래 단추 또는 '원문 2'·'파일 2'" if ids else ""))
            for i, part in enumerate(parts):
                self.send(part, cid, buttons if i == len(parts) - 1 else None)
            return text
        except ai.AIError as e:
            return self.send_found(cid, question, f"(AI 답을 만들지 못했습니다: {e})")
        except Exception as e:   # 폴링이 멈추지 않게
            self.db.log(f"텔레그램 질문 처리 오류: {type(e).__name__}", "error")
            return self.reply(cid, "답을 만들다가 오류가 났습니다. 잠시 뒤 다시 보내 주세요.")
        finally:
            self.asking = None

    def send_found(self, cid, question, note):
        """AI 없이: 질문에 맞게 찾은 자료·일정을 보여 줌."""
        found = assist.gather(self.db, question)
        ids = [it["id"] for it in found["items"]]
        self.set_refs(ids)
        if not ids and not found["events"]:
            return self.reply(cid, f"'{question[:30]}' 에 맞는 자료를 찾지 못했습니다.\n'도움말' 을 보내면 명령 안내를 볼 수 있습니다.")
        text = self.items_text(found["items"], found["events"], f"🔍 '{question[:30]}'") + "\n\n" + note
        parts = chunks(text)
        buttons = self.item_buttons(ids, ids)
        for i, part in enumerate(parts):
            self.send(part, cid, buttons if i == len(parts) - 1 else None)
        return text

    def item_buttons(self, show_ids, ids):
        """자료마다 [n 원문] [n 파일] 단추 (번호는 ids 안의 순서)."""
        rows = []
        for iid in show_ids[:8]:
            n = ids.index(iid) + 1
            row = [{"text": f"{n} 원문", "callback_data": f"o:{iid}"}]
            fp = self.db.one("SELECT file_path FROM items WHERE id = ?", (iid,))
            if fp and item_file(self.config, fp["file_path"]):
                row.append({"text": f"{n} 원본 파일 받기", "callback_data": f"f:{iid}"})
            st = self.db.one("SELECT registered, (SELECT COUNT(*) FROM events e WHERE e.item_id = items.id AND e.done = 0) AS n "
                             "FROM items WHERE id = ?", (iid,))
            if st and not st["registered"] and st["n"]:   # 일정 후보가 있고 아직 달력에 안 넣은 것
                row.append({"text": f"📅 {n} 일정 등록", "callback_data": f"rg:{iid}"})
            rows.append(row)
        return rows or None

    def register_item(self, cid, item_id):
        """수신함에서 체크하는 것과 같음: 이 자료의 일정을 달력·브리핑에 넣음."""
        it = self.db.one("SELECT id, title, registered FROM items WHERE id = ?", (item_id,))
        if not it:
            return self.reply(cid, "없는 자료입니다.")
        if it["registered"]:
            return self.reply(cid, "이미 달력에 들어 있습니다.")
        self.db.execute("UPDATE items SET registered = 1 WHERE id = ?", (item_id,))
        n = self.db.one("SELECT COUNT(*) AS n FROM events WHERE item_id = ?", (item_id,))["n"]
        return self.reply(cid, f"📅 '{cut(self.mask(it['title']), 30)}' 일정 {n}건을 달력에 넣었습니다. (취소는 업무 데스크 수신함에서 체크를 푸세요)")

    def run_arg(self, cid, cmd, args):
        if cmd == "general":
            return self.offer_general(cid, " ".join(args))
        if cmd == "absence":
            if not args:
                return self.begin_dialog(cid, "absence")
            return self.create_absence(cid, " ".join(args))
        if cmd == "search":
            if not args:
                return self.begin_dialog(cid, "search")
            return self.send_search(cid, " ".join(args))
        if cmd == "lessons":
            return self.reply(cid, self.lessons_text(" ".join(args)))
        if cmd in ("text", "file"):
            if not args or not args[0].isdigit():
                return self.reply(cid, f"번호를 함께 보내세요. 예) {'원문' if cmd == 'text' else '파일'} 2")
            item_id = self.ref_item(int(args[0]))
            if not item_id:
                return self.reply(cid, f"{args[0]}번 자료가 없습니다. 브리핑이나 검색을 먼저 보내세요.")
            return self.send_original(cid, item_id) if cmd == "text" else self.send_item_file(cid, item_id)
        return self.start_pass(cid, cmd, args)

    def offer_general(self, cid, question):
        try:
            question = providers.validate_question(question)
        except ai.AIError as e:
            return self.reply(cid, str(e))
        provider = self.config["ai_provider"]
        if provider == "browser":
            return self.reply(cid, "브라우저 내장 AI는 앱의 'AI 질문'에서만 실행할 수 있습니다. 텔레그램 일반 질문은 로컬 또는 외부 API를 선택하세요.")
        if provider not in providers.PROVIDERS:
            return self.reply(cid, "앱의 AI 질문 화면에서 사용 방식을 다시 저장하세요.")
        if provider == "local":
            return self.general_ask(cid, question, provider)
        request_id = secrets.token_hex(6)
        self.choice_requests[request_id] = {"chat_id": cid, "text": question, "provider": provider,
                                            "expires": time.monotonic() + DIALOG_TTL}
        while len(self.choice_requests) > 10:
            del self.choice_requests[next(iter(self.choice_requests))]
        self.send(f"일반 질문을 {providers.PROVIDERS[provider]} API로 전송할까요?\n"
                  "수신함 자료는 붙이지 않습니다. 학생 정보·비공개 자료를 입력하지 마세요.\n"
                  "입력 내용이 외부 회사로 전송되고 API 요금·보관 정책이 적용됩니다. 보조 검사는 모든 민감정보를 찾지 못합니다.",
                  cid, [[{"text": "공개 질문 · 전송 동의", "callback_data": f"general:{request_id}:send"},
                         {"text": "취소", "callback_data": f"general:{request_id}:cancel"}]])

    def general_ask(self, cid, question, provider):
        if self.asking:
            return self.reply(cid, "앞 질문에 답하는 중입니다. 끝나면 다시 보내 주세요.")
        self.asking = question
        if self.sync:
            return self._general_ask(cid, question, provider)
        self.send("일반 질문에 답하는 중… 업무자료는 찾아 붙이지 않습니다.", cid)
        threading.Thread(target=self._general_ask, args=(cid, question, provider),
                         name="telegram-general", daemon=True).start()

    def _general_ask(self, cid, question, provider):
        try:
            answer = providers.ask(self.db, self.config, question, provider=provider, confirmed=True)
            answer = ai.mask_names(answer, [s["name"] for s in self.db.students()])
            for part in chunks("일반 질문 답변 (업무자료 검색 아님)\n" + answer):
                self.send(part, cid)
            return answer
        except ai.AIError as e:
            return self.reply(cid, str(e))
        except Exception as e:
            self.db.log(f"텔레그램 일반 질문 오류: {type(e).__name__}", "error")
            return self.reply(cid, "일반 질문 처리 중 오류가 났습니다. 다시 시도하기 전에 서비스 이용 내역을 확인하세요.")
        finally:
            self.asking = None

    def create_absence(self, cid, text):
        try:
            fields = absence.parse_command(text)
            dates = self.validate_absence(fields)
        except absence.AbsenceError as e:
            return self.reply(cid, str(e))
        return self.save_absence(cid, fields, dates)

    def validate_absence(self, fields):
        students = [s for s in self.db.students() if s["number"] == fields["student_number"]]
        if len(students) != 1 or students[0]["name"] != fields["student_name"]:
            raise absence.AbsenceError("입력한 번호·이름이 학생 명단과 일치하지 않습니다. 앱의 명단을 확인하세요.")
        dates = absence.school_dates(
            self.db, date.fromisoformat(fields["start_date"]), date.fromisoformat(fields["end_date"]),
            self.config["grade"], require_calendar=True)
        if not dates:
            raise absence.AbsenceError("해당 기간에는 결석 처리할 수업일이 없습니다. 주말·공휴일·휴업일을 제외했습니다.")
        return dates

    def save_absence(self, cid, fields, dates):
        saved = absence.save(self.db, fields)
        return self.reply(cid, (
            f"✅ {fields['student_number']}번 결석계를 앱에 저장했습니다.\n"
            f"{fields['start_date']}~{fields['end_date']} · {len(dates)}일 (주말·공휴일·휴업일 제외)\n"
            f"작성 번호 {saved['serial']}\n상세 내용과 인쇄는 앱의 출결 → 결석계에서 확인하세요."))

    def lessons_text(self, text, today=None):
        today = today or date.today()
        if queries.my_lessons(self.db, self.config, today) is None:
            return "불러온 시간표가 없습니다. 업무 데스크 → 시간표에서 학교 시간표 엑셀을 불러오세요."
        lines = []
        for d in assist.lesson_days(text, today) or [today]:
            ls = queries.day_lessons(self.db, self.config, d)
            lines.append(f"📚 {fmt_day(d.isoformat(), today)} 내 수업 {len(ls)}시간")
            lines += [f"  {l['p']}교시{' ' + l['start'] if l.get('start') else ''} {l['cls']} {l['subj']}"
                      + (" (교체)" if l["moved"] else "") + queries.note_text(l) for l in ls] or ["  없음"]
        return "\n".join(lines)

    # ---- 단추 ----
    def handle_button(self, cq):
        msg = cq.get("message") or {}
        cid = (msg.get("chat") or {}).get("id")
        if cid is None or cid != self.chat_id or (cq.get("from") or {}).get("id", cid) != cid:
            return None
        try:
            self.api("answerCallbackQuery", {"callback_query_id": cq.get("id")}, timeout=10)
        except (urllib.error.URLError, OSError, RuntimeError):
            pass
        data = cq.get("data") or ""
        kind, _, rest = data.partition(":")
        if kind == "general":
            request_id, _, action = rest.partition(":")
            request = self.choice_requests.get(request_id)
            if not request or request["chat_id"] != cid or "provider" not in request:
                return self.reply(cid, "이미 처리했거나 오래된 질문입니다. /ask 질문을 다시 보내세요.")
            if action not in ("send", "cancel"):
                return self.reply(cid, "알 수 없는 선택입니다.")
            if action == "send" and self.asking:
                return self.reply(cid, "앞 질문에 답하는 중입니다. 끝나면 다시 눌러 주세요.")
            del self.choice_requests[request_id]
            if action == "cancel":
                return self.reply(cid, "외부 전송을 취소했습니다.")
            if time.monotonic() > request["expires"]:
                return self.reply(cid, "확인 시간이 10분을 넘었습니다. 질문을 다시 보내세요.")
            if self.config["ai_provider"] != request["provider"]:
                return self.reply(cid, "AI 사용 방식이 바뀌었습니다. 새 설정을 확인하고 질문을 다시 보내세요.")
            return self.general_ask(cid, request["text"], request["provider"])
        if kind == "dlg":
            dialog_id, _, action = rest.partition(":")
            dialog = self.dialog
            if not dialog or dialog["id"] != dialog_id:
                return self.reply(cid, "이미 처리했거나 오래된 입력 단추입니다. 메뉴에서 다시 시작하세요.")
            if time.monotonic() > dialog["expires"]:
                self.dialog = None
                return self.reply(cid, "입력 시간이 10분을 넘었습니다. 메뉴에서 다시 시작하세요.")
            if action == "cancel":
                self.dialog = None
                return self.reply(cid, "입력을 취소했습니다. 저장하지 않았습니다.")
            if action.startswith("type:") and dialog["kind"] == "absence" and dialog["step"] == "type":
                n = action[5:]
                if n not in ("0", "1", "2", "3"):
                    return self.reply(cid, "결석 종류 단추를 다시 골라 주세요.")
                dialog.update(step="date", type=list(absence.TYPE_WORDS)[int(n)])
                today = date.today()
                return self.show_calendar(cid, dialog, msg.get("message_id"), today.year, today.month)
            calendar_result = self.calendar_action(cid, dialog, action, msg.get("message_id"))
            if calendar_result is not None:
                return calendar_result
            if action == "now" and dialog["kind"] in KIND and dialog["step"] == "time":
                return self.start_pass(cid, dialog["kind"], [str(dialog["number"])])
            if action == "save" and dialog["kind"] == "absence" and dialog["step"] == "confirm":
                self.dialog = None  # 중복 클릭으로 두 번 저장하지 않음
                try:
                    fields = self.dialog_absence_fields(dialog, dialog["period"])
                    dates = self.validate_absence(fields)
                except absence.AbsenceError as e:
                    return self.reply(cid, str(e))
                return self.save_absence(cid, fields, dates)
            return self.reply(cid, "현재 단계의 단추를 사용해 주세요. 그만하려면 '취소'.")
        if kind == "choice":
            request_id, _, action = rest.partition(":")
            request = self.choice_requests.get(request_id)
            if not request or request["chat_id"] != cid:
                return self.reply(cid, "이미 처리했거나 오래된 선택입니다. 내용을 다시 보내 주세요.")
            if action not in ("ai", "search", "brief", "today", "deadlines", "early", "out", "absence"):
                return self.reply(cid, "알 수 없는 선택입니다. 내용을 다시 보내 주세요.")
            if action == "ai" and self.asking:
                return self.reply(cid, "앞 질문에 답하는 중입니다. 끝나면 AI 검색 단추를 다시 눌러 주세요.")
            del self.choice_requests[request_id]
            if action == "ai":
                return self.ask(cid, request["text"])
            if action == "search":
                return self.send_search(cid, request["text"][:100])
            if action == "brief":
                return self.send_brief(cid)
            if action in ("today", "deadlines"):
                return self.reply(cid, self.answer(action))
            if action == "absence":
                return self.begin_dialog(cid, "absence")
            return self.run_arg(cid, action, [])
        if kind == "mn" and rest in MENU_MORE:   # '메뉴' 의 단추: 같은 이름의 글을 보낸 것처럼 처리
            return self.handle_update({"message": {"chat": {"id": cid, "type": "private"}, "text": rest}})
        if kind == "rg" and rest.isdigit():
            return self.register_item(cid, int(rest))
        if kind == "o" and rest.isdigit():
            return self.send_original(cid, int(rest))
        if kind == "f" and rest.isdigit():
            return self.send_item_file(cid, int(rest))
        if kind == "pp":
            pid, _, choice = rest.partition(":")
            p = self.db.one("SELECT * FROM passes WHERE id = ?", (pid,))
            if not p or choice not in ("0", "1", "2", "3"):
                return self.reply(cid, "이미 처리했거나 없는 발급 기록입니다.")
            who = f"{p['student_number']}번 {KIND[p['kind']]}증"
            lines, link = [], None
            if choice in ("1", "3"):
                lines.append(self.queue_print(p, who))
            if choice in ("2", "3"):
                link = passlink.share_url(self.config["pass_page_url"], p, self.config.values)
                lines.append(f"🔗 {who} 학생용 링크를 아래에 보냈습니다." if link else
                             f"⚠️ {who} 링크를 만들지 못했습니다. 업무 데스크 설정의 공유 페이지 주소를 확인하세요.")
            if choice == "0":
                lines.append(f"✅ {who} 발급 완료 · 인쇄·링크 보내기 없음")
            text = "\n".join(lines)
            try:
                self.api("editMessageText", {"chat_id": cid, "message_id": msg.get("message_id"), "text": text}, timeout=15)
            except (urllib.error.URLError, OSError, RuntimeError):
                self.send(text, cid)
            if link:   # 이름이 들어 있는 링크 (사용자 결정 2026-10-30: 사유는 뺌) — 학생에게만 전달
                self.send(f"🔗 {who} 학생용 링크 (이름 포함, 사유 없음)\n{link}\n학생에게만 전달하세요.", cid)
            return text
        if kind in ("pr", "px", "pc"):
            pid, _, choice = rest.partition(":")
            self.last_issued_pass_id = self.last_confirm_pid = None
            if kind == "pc" and choice not in ("0", "1"):
                return self.reply(cid, "알 수 없는 선택입니다.")
            text = self.finish_pass(pid, None if kind == "px" else choice, "confirm" if kind == "pc" else "reason")
            markup = None
            if self.last_confirm_pid:
                markup = {"inline_keyboard": [[
                    {"text": "✅ 학부모 확인함", "callback_data": f"pc:{pid}:1"},
                    {"text": "🚫 거절", "callback_data": f"pc:{pid}:0"}]]}
            elif self.last_issued_pass_id:
                pid = self.last_issued_pass_id
                markup = {"inline_keyboard": [
                    [{"text": "🖨 기본 프린터로 인쇄", "callback_data": f"pp:{pid}:1"},
                     {"text": "🔗 웹 링크 받기", "callback_data": f"pp:{pid}:2"}],
                    [{"text": "인쇄 + 웹 링크", "callback_data": f"pp:{pid}:3"},
                     {"text": "아무것도 안 함", "callback_data": f"pp:{pid}:0"}],
                ]}
            try:
                params = {"chat_id": cid, "message_id": msg.get("message_id"), "text": text}
                if markup:
                    params["reply_markup"] = markup
                self.api("editMessageText", params, timeout=15)
            except (urllib.error.URLError, OSError, RuntimeError):
                self.send(text, cid, markup["inline_keyboard"] if markup else None)
            return text
        return None

    # ---- 조퇴증·외출증 (번호로, 사유는 단추로) ----
    def start_pass(self, cid, kind, args, now=None):
        word = KIND[kind]
        if not args:
            return self.begin_dialog(cid, kind)
        if not args[0].isdigit():
            return self.reply(cid, f"번호를 함께 보내세요. 예) {word} 7" + (" 13:30 14:30" if kind == "out" else " 13:30"))
        number = int(args[0])
        students = [s for s in self.db.students() if s["number"] == number]
        if len(students) != 1:
            return self.reply(cid, f"학생 명단에 {number}번이 없습니다 (또는 번호 중복). 업무 데스크의 학생 명단을 확인하세요.")
        if len(args) > (3 if kind == "out" else 2):
            return self.reply(cid, "번호와 시각만 보내세요. 예) " + ("외출 7 13:30 14:30" if kind == "out" else "조퇴 7 13:30"))
        times = [parse_time(a) for a in args[1:3]]
        if any(t is None for t in times):
            return self.reply(cid, "시각은 13:30 이나 1330 처럼 보내세요.")
        time_out = times[0] if times else rounded_now(now)
        time_return = (times[1] if len(times) > 1 else plus_hour(time_out)) if kind == "out" else None
        if time_return and time_return <= time_out:
            return self.reply(cid, "복귀 시각은 나가는 시각보다 뒤여야 합니다. 예: 13:30 14:30")
        self.dialog = None
        pending = {k: v for k, v in (self.db.get_meta("telegram_pending") or {}).items()}
        pid = secrets.token_hex(3)
        pending[pid] = {"kind": kind, "number": number, "time_out": time_out, "time_return": time_return,
                        "date": (now or datetime.now()).date().isoformat()}
        self.db.set_meta("telegram_pending", dict(list(pending.items())[-20:]))   # 최근 20개만 (학부모 확인 대기 포함)
        when = time_out + (f"~{time_return}" if time_return else "")
        buttons = [[{"text": r, "callback_data": f"pr:{pid}:{i}"}] for i, r in enumerate(REASONS[kind])]
        buttons.append([{"text": "사유 없이 발급", "callback_data": f"pr:{pid}:-"},
                        {"text": "취소", "callback_data": f"px:{pid}"}])
        text = f"🎫 {number}번 {masked(students[0]['name'])} {word}증 {when}\n사유를 고르면 발급합니다."
        self.send(text, cid, buttons)
        return text

    def finish_pass(self, pid, choice, stage="reason"):
        """증 발급 2단계. stage 'reason': 사유를 고르면 학부모 확인 대기로 / 'confirm': 학부모 확인함(1)·거절(0)이면 발급.
        choice None 은 취소. 학부모 확인을 거치기 전에는 발급하지 않음 (사용자 결정 2026-10-30)."""
        pending = self.db.get_meta("telegram_pending") or {}
        p = pending.get(pid)
        if not p:
            return "이미 처리했거나 오래된 요청입니다."
        word = KIND[p["kind"]]
        if choice is None:
            pending.pop(pid)
            self.db.set_meta("telegram_pending", pending)
            return f"취소했습니다 ({p['number']}번 {word}증)."
        students = [s for s in self.db.students() if s["number"] == p["number"]]
        if len(students) != 1:
            return f"학생 명단에 {p['number']}번이 없습니다."
        who = f"{p['number']}번 {masked(students[0]['name'])}"
        when = p["time_out"] + (f"~{p['time_return']}" if p["time_return"] else "")
        if stage == "reason":
            if p.get("stage") != "confirm":   # 사유 단추를 또 눌러도 처음 고른 사유·확인 단추를 그대로 둠
                reasons = REASONS[p["kind"]]
                p["reason"] = reasons[int(choice)] if choice.isdigit() and int(choice) < len(reasons) else ""
                p["stage"] = "confirm"
                self.db.set_meta("telegram_pending", pending)
            self.last_confirm_pid = pid
            phone = students[0]["parent_phone"]
            # 학부모 연락처는 확인 알림에만 (사용자 결정 2026-10-30). 사유는 싣지 않음
            return (f"📞 {who} {word}증 {when}\n학부모 연락처: {phone or '없음 (학생 명단에서 입력하세요)'}\n"
                    "학부모와 통화해 확인한 뒤 [학부모 확인함]을 누르면 발급합니다.")
        if p.get("stage") != "confirm":
            return "먼저 사유를 골라 주세요."
        pending.pop(pid)
        self.db.set_meta("telegram_pending", pending)
        if choice != "1":
            return f"🚫 {who} {word}증을 거절했습니다. 발급하지 않았습니다."
        saved = queries.issue_pass(self.db, {
            "kind": p["kind"], "student_name": students[0]["name"], "student_number": p["number"],
            "date": p["date"], "time_out": p["time_out"], "time_return": p["time_return"],
            "reason": p.get("reason", ""), "guardian_ok": 1, "share_reason": 0,
        })
        self.last_issued_pass_id = saved["id"]
        # 이름·사유는 답장에 쓰지 않음
        return (f"✅ {who} {word}증 발급 (학부모 확인) · {when}\n"
                f"발급 번호 {saved['serial']}\n인쇄할지, 학생에게 줄 웹 링크를 받을지 고르세요.")

    def queue_print(self, p, who):
        """기본 프린터로 인쇄를 요청하고 알릴 글을 돌려줌. 시작하지 못하면 대기열에서 빼서 나중에 되살아나지 않게 함."""
        printing.enqueue(self.db, int(p["id"]))
        try:
            started = printing.start_silent_pass(self.config, int(p["id"]))
        except printing.PrintError as e:
            printing.dequeue(self.db, int(p["id"]))
            return f"⚠️ {who} 자동 인쇄를 시작하지 못했습니다: {e}\n업무 데스크가 켜져 있는지 확인하세요."
        return f"🖨️ {who}을 기본 프린터로 {'보냈습니다.' if started else '보내는 중입니다.'}"

    # ---- 브리핑 ----
    def brief_text(self, today=None):
        """오늘 일정·마감 + 지난 1주일 AI 요약 (지운 줄은 빼고). 자료 번호는 '원문 n' 으로."""
        today = today or date.today()
        lines = [self.answer("today", today).replace("업무", "브리핑", 1)]
        waiting = [p for p in (self.db.get_meta("telegram_pending") or {}).values() if p.get("stage") == "confirm"]
        if waiting:
            lines.append(f"\n■ 학부모 확인 대기 {len(waiting)}건: " + ", ".join(
                f"{p['number']}번 {KIND[p['kind']]} {p['time_out']}" for p in waiting[:10]))
        mine = [t for t in queries.todos(self.db, today) if not t["done"]]
        if mine:
            lines.append(f"\n■ 꼭 할 일 (직접 넣은 것 {len(mine)})")
            lines += [f"- {fmt_day(t['date'], today) + (' ' + t['time'] if t['time'] else '') + ' · ' if t['date'] else ''}{t['text'][:60]}"
                      for t in mine[:15]]
        res = self.db.get_meta("ai_weekly") or {}   # weekly_summary 가 저장한 요약 그대로 ({at, text, ids, updates})
        hidden = set(self.db.get_meta("ai_hidden", []))
        refs = res.get("ids") or []
        # 전체 요약과 그 뒤 '새로 들어온 것' 들을 한 벌로 합침 (같은 줄은 하나만, 새 줄은 '(새)') — 화면과 같음
        blocks = [(u.get("text", ""), u.get("ids") or [], True) for u in (res.get("updates") or [])]
        if res.get("text"):
            blocks.append((res["text"], refs, False))
        merged = {"todo": [], "event": [], "note": []}
        seen = set()
        for text, ids, fresh in blocks:
            for kind, when, what, nums in ai.parse_summary(text):
                key = re.sub(r"\s+", "", f"{kind}|{when}|{what}")
                if key in hidden or key in seen:
                    continue
                seen.add(key)
                merged[kind].append((when, what, [ids[n - 1] for n in nums if 0 < n <= len(ids)], fresh))
        if not any(merged.values()):
            lines.append("\n(AI 요약 없음 — 업무 데스크 브리핑에서 만들어집니다)" if not blocks else "\n(AI 요약은 모두 지웠습니다)")
            self.set_refs(refs)
            return "\n".join(lines)
        for kind in ("todo", "event"):   # 할 일·행사는 날짜 순 (날짜 없는 것은 뒤)
            merged[kind].sort(key=lambda r: ai.row_date(r[0], today) or date.max)
        merged["note"].sort(key=lambda r: not r[3])
        n_upd = len(res.get("updates") or [])
        lines.append(f"\n━━ 지난 1주일 요약 · {res.get('at', '')[5:16]}" + (f" (+새 자료 {n_upd}번)" if n_upd else "") + " ━━")
        heads = {"todo": "■ 꼭 할 일", "event": "■ 행사·회의", "note": "■ 알아 둘 내용"}
        all_refs = []
        for kind, rows in merged.items():
            if not rows:
                continue
            lines.append(heads[kind])
            for when, what, item_ids, fresh in rows:
                marks = []   # 자료 번호는 이 메시지 안에서 하나로 이어지게 다시 매김
                for iid in item_ids:
                    if iid not in all_refs:
                        all_refs.append(iid)
                    marks.append(str(all_refs.index(iid) + 1))
                lines.append(f"- {'(새) ' if fresh else ''}{when + ' · ' if when else ''}{what}{' [' + ','.join(marks) + ']' if marks else ''}")
        self.set_refs(all_refs)
        if all_refs:
            lines.append("\n[번호] 자료의 원문: '원문 2' · 원본 파일: '파일 2'")
        return "\n".join(lines)

    def send_brief(self, cid):
        text = self.brief_text()
        for part in chunks(text):
            self.send(part, cid)
        return text

    # ---- 검색 ----
    def search_text(self, q):
        """제목·요약까지 (학생 명단·조퇴 기록은 텔레그램 검색에서 뺌). (글, 단추)"""
        q = q.strip()
        if not q:
            return "찾을 낱말을 함께 보내세요. 예) 검색 학업성적", None
        r = queries.search(self.db, q, limit=8)
        items = r["items"]
        ids = [it["id"] for it in items]
        self.set_refs(ids)
        if not items and not r["events"]:
            return f"🔍 '{q}' — 찾은 자료가 없습니다.", None
        return self.items_text(items, r["events"], f"🔍 '{q}'"), self.item_buttons(ids, ids)

    def items_text(self, items, events, title):
        """자료 목록 글: 번호 · 출처 · 받은 날 · 분류 · 가까운 마감, 제목, 요약 두 줄 + 일정."""
        today = date.today()
        lines = [f"{title} 자료 {len(items)}건" + (f" · 일정 {len(events)}건" if events else "")]
        for n, it in enumerate(items, 1):
            row = self.db.one("SELECT summary FROM items WHERE id = ?", (it["id"],))
            summary = json.loads(row["summary"] or "[]")
            nxt = self.db.one("SELECT MIN(date) AS d FROM events WHERE item_id = ? AND kind = 'deadline' AND done = 0 AND date >= ?",
                              (it["id"], today.isoformat()))["d"]
            lines.append(f"\n{n}. [{SOURCE_NAMES.get(it['source'], it['source'])}] {it['received_at'][5:10].replace('-', '/')} · {it['category']}"
                         + (f" · ⏰ 마감 {fmt_day(nxt, today)}" if nxt else ""))
            lines.append(it["title"][:80])
            lines += ["  " + s[:90] for s in summary[:2]]
        if events:
            lines.append("\n📅 일정")
            lines += [fmt_event(e, today, "titles") for e in events[:8]]
        return "\n".join(lines)

    def send_search(self, cid, q):
        text, buttons = self.search_text(q)
        parts = chunks(text)
        for i, part in enumerate(parts):
            self.send(part, cid, buttons if i == len(parts) - 1 else None)
        return text

    # ---- 원문·파일 (달라고 할 때만) ----
    def set_refs(self, ids):
        self.db.set_meta("telegram_refs", list(ids)[:100])

    def ref_item(self, n):
        ids = self.db.get_meta("telegram_refs") or []
        return ids[n - 1] if 0 < n <= len(ids) else None

    def send_original(self, cid, item_id):
        it = self.db.one("SELECT id, title, body, source, received_at FROM items WHERE id = ?", (item_id,))
        if not it:
            return self.reply(cid, "없는 자료입니다.")
        head = f"📄 [{SOURCE_NAMES.get(it['source'], it['source'])}] {it['title']}\n{it['received_at'][:16]}\n\n"
        body = it["body"] or ""
        if len(body) > TEXT_AS_FILE:   # 아주 길면 글 파일로
            name = re.sub(r'[\\/:*?"<>|]', "_", it["title"])[:60] + ".txt"
            self.send_file(cid, name, body.encode("utf-8"), caption=head.strip())
            return head + f"(원문 {len(body):,}자 — 글 파일로 보냄)"
        for part in chunks(head + body):
            self.send(part, cid)
        return head + body

    def send_item_file(self, cid, item_id):
        it = self.db.one("SELECT id, title, file_path FROM items WHERE id = ?", (item_id,))
        if not it:
            return self.reply(cid, "없는 자료입니다.")
        p = item_file(self.config, it["file_path"])
        if not p:
            return self.reply(cid, "원본 파일이 없습니다 (쿨메신저 쪽지이거나 폴더에서 옮겨짐). '원문' 으로 글을 받으세요.")
        if p.stat().st_size > FILE_LIMIT:
            return self.reply(cid, "파일이 너무 커서 텔레그램으로 보낼 수 없습니다 (50MB).")
        self.send_file(cid, p.name, p.read_bytes(), caption=it["title"][:200])
        return f"📎 {p.name}"

    def reply(self, cid, text):
        self.send(text, cid)
        return text

    def send_answer(self, cid, cmd):
        text = self.answer(cmd)
        ids = self.db.get_meta("telegram_refs", []) if cmd in ("refresh", "summary") else []
        buttons = self.item_buttons(ids, ids) if self.config["telegram_detail"] != "counts" else None
        self.send(text, cid, buttons)
        return text

    def answer(self, cmd, today=None):
        today = today or date.today()
        detail = self.config["telegram_detail"]
        if cmd == "status":
            try:
                names = ai.models(self.config["ai_base"]) if self.config["ai_enabled"] else []
                ai_status = ("AI 연결됨 · 대화용 모델 있음" if names else
                             "AI 켜짐 · 모델을 불러오세요" if self.config["ai_enabled"] else "AI 꺼짐 · 자료 검색은 가능")
            except ai.AIError as e:
                ai_status = f"AI 연결 오류: {e}"
            return (f"⚙ 업무 데스크 상태 · 버전 {VERSION}\n"
                    + f"텔레그램: {self.status}" + (f" · 마지막 확인 {self.last_poll}" if self.last_poll else "") + "\n"
                    + "업무자료 요약·검색 (로컬): " + ai_status
                    + "\n일반 질문: " + providers.PROVIDERS.get(self.config["ai_provider"], "설정 확인 필요")
                    + (" · 텔레그램 실행 불가" if self.config["ai_provider"] == "browser" else " · /ask 질문")
                    + ("\nAI가 앞 질문에 답하는 중입니다." if self.asking else "")
                    + ("\n시간표 준비됨" if queries.my_lessons(self.db, self.config, today) is not None else "\n시간표 미등록")
                    + "\n업무자료 AI 연결은 설정 → 업무자료 AI 요약, 일반 질문 설정은 AI 질문에서 확인하세요.")
        if cmd == "today":
            s = queries.today_summary(self.db, today)
            lines = [f"📅 {fmt_day(today.isoformat(), today)} 업무"]
            if s["overdue"]:
                lines.append(f"\n⚠ 지난 마감 {len(s['overdue'])}건")
                lines += [fmt_event(e, today, detail) for e in s["overdue"][:5]]
            if s["due_today"]:
                lines.append(f"\n⏰ 오늘 마감 {len(s['due_today'])}건")
                lines += [fmt_event(e, today, detail) for e in s["due_today"][:10]]
            lines.append(f"\n오늘 일정 {len(s['today'])}건")
            lines += [fmt_event(e, today, detail) for e in s["today"][:10]]
            lines.append(f"\n7일 안 마감 {len(s['week_deadlines'])}건")
            lines += [fmt_event(e, today, detail) for e in s["week_deadlines"][:5]]
            lessons = queries.day_lessons(self.db, self.config, today)
            if lessons is not None:
                lines.insert(1, lesson_line(lessons))
            out = sum(1 for p in s["passes"] if p["kind"] == "out")
            early = sum(1 for p in s["passes"] if p["kind"] == "early")
            lines.append(f"\n🎫 오늘 조퇴 {early}명 · 외출 {out}명")
            return "\n".join(lines)
        if cmd == "deadlines":
            evs = [e for e in queries.events_between(self.db, today, today + timedelta(days=7), include_done=False)
                   if e["kind"] == "deadline" and (e["end_date"] or e["date"]) <= (today + timedelta(days=7)).isoformat()]
            if not evs:
                return "7일 안의 마감이 없습니다 👍"
            return "⏰ 7일 안의 마감\n" + "\n".join(fmt_event(e, today, detail) for e in evs[:20])
        if cmd == "refresh":
            self.set_refs([])
            if not self.collector:
                return "수집기를 사용할 수 없습니다."
            stats = self.collector.run("telegram")
            if stats.get("busy"):
                return "이미 수집 중입니다. 잠시 뒤 다시 보내 주세요."
            return self.collect_message(stats) or "새로 들어온 자료가 없습니다."
        if cmd == "summary":
            rows = self.db.query("SELECT * FROM items WHERE source != 'paste' ORDER BY received_at DESC LIMIT 3")
            self.set_refs([r["id"] for r in rows] if detail != "counts" else [])
            if not rows:
                return "아직 받은 자료가 없습니다."
            parts = []
            for n, r in enumerate(rows, 1):
                head = f"[{SOURCE_NAMES.get(r['source'], r['source'])}] {r['received_at'][5:16]} · {r['category']}"
                if detail == "counts":
                    parts.append(head)
                    continue
                head += f"\n[{n}] {r['title'][:60]}"
                if detail == "summary":
                    head += "\n" + "\n".join("  " + s for s in json.loads(r["summary"] or "[]")[:4])
                parts.append(head)
            return "📥 최근 받은 자료\n\n" + "\n\n".join(parts)
        return HELP

    def mask(self, text):
        """텔레그램으로 나가는 글에서 학생 이름은 가림."""
        return ai.mask_names(text or "", [s["name"] for s in self.db.students()])

    def collect_message(self, stats, today=None):
        """수집 결과 알림 (0.5.39). 새 자료가 없으면 None.
        적을 때는 마감이 있는 것·에듀파인 공문을 위에 두고 보낸 사람·무엇을 언제까지인지를 함께,
        많을 때(처음 연결 등)는 한 줄 요약과 가까운 마감만."""
        if not stats.get("items"):
            return None
        today = today or date.today()
        detail = self.config["telegram_detail"]
        n_all = stats["items"]
        ids = list(stats.get("new_ids") or [])
        head = f"📥 새 자료 {n_all}건"
        if detail == "counts" or not ids:
            return head + (f" · ⏰ 마감 후보 {stats['deadlines']}건" if stats.get("deadlines") else "")
        marks = ",".join("?" * len(ids))
        rows = self.db.query(f"SELECT id, source, title, body, received_at, summary FROM items WHERE id IN ({marks})", ids)
        by_src = {}
        for r in rows:
            by_src[r["source"]] = by_src.get(r["source"], 0) + 1
        if len(by_src) > 1 or n_all > BATCH_LIMIT:
            head += " (" + " · ".join(f"{SOURCE_NAMES.get(s, s)} {c}" for s, c in by_src.items()) + ")"
        soon = {}   # 자료별 가장 가까운 마감 (90일 안)
        for e in self.db.query(
                f"SELECT item_id, date, end_date, time, text FROM events WHERE item_id IN ({marks}) AND kind = 'deadline' "
                "AND done = 0 AND COALESCE(end_date, date) >= ? AND COALESCE(end_date, date) <= ? "
                "ORDER BY COALESCE(end_date, date), COALESCE(time, '99')",
                ids + [today.isoformat(), (today + timedelta(days=90)).isoformat()]):
            soon.setdefault(e["item_id"], e)
        rows.sort(key=lambda r: r["received_at"], reverse=True)
        rows.sort(key=lambda r: ((0, soon[r["id"]]["end_date"] or soon[r["id"]]["date"]) if r["id"] in soon
                                 else (1 if r["source"] == "edufine" else 2, "")))
        big = n_all > BATCH_LIMIT
        shown = [r for r in rows if r["id"] in soon][:3] if big else rows[:5]
        lines = [head]
        if big:
            lines.append("한꺼번에 많이 들어와 목록은 줄였습니다. 전부는 업무 데스크 수신함에서 보세요.")
            if shown:
                lines.append("\n⏰ 가까운 마감")
        for n, r in enumerate(shown, 1):
            sender = sender_of(r["body"])
            lines.append(f"{n}. [{SOURCE_NAMES.get(r['source'], r['source'])}] {cut(self.mask(r['title']), 40)}"
                         + (f" · {self.mask(sender)}" if sender else ""))
            e = soon.get(r["id"])
            if e:
                end = e["end_date"] or e["date"]
                days = (date.fromisoformat(end) - today).days
                what = cut(self.mask(e["text"]), 40)
                same = what.rstrip("…") in r["title"] or r["title"][:30] in what
                lines.append(f"   ⏰ {fmt_day(end, today)}{' ' + e['time'] if e['time'] else ''}"
                             + (f" (D-{days})" if days > 2 else "") + ("" if same else f" · {what}"))
            elif detail == "summary":
                summary = json.loads(r["summary"] or "[]")
                if summary:
                    lines.append("   " + cut(self.mask(summary[0]), 60))
        if len(rows) > len(shown) and not big:
            lines.append(f"그 외 {len(rows) - len(shown)}건")
        self.set_refs([r["id"] for r in shown])
        return "\n".join(lines)

    def notify_collect(self, stats, reason):
        if reason == "telegram":
            return  # 명령에 대한 답장으로 이미 보냄
        msg = self.collect_message(stats)
        if msg and self.chat_id:
            ids = self.db.get_meta("telegram_refs", []) if stats.get("new_ids") else []
            buttons = self.item_buttons(ids, ids) if self.config["telegram_detail"] != "counts" else None
            self.send(msg, buttons=buttons)

    def notify_update(self, info):
        """새 버전이 나왔다고 텔레그램으로 한 번 알림 (같은 버전은 다시 보내지 않음). 바꾸는 것은 화면에서 직접 눌러야 함."""
        version = str(info.get("version", ""))[:20]
        if not version or not self.chat_id or self.db.get_meta("telegram_update_notified") == version:
            return False
        sent = self.send(f"⬆ 업무 데스크 새 버전 {version} 이 나왔습니다 (지금 {VERSION}).\n"
                         "업무 데스크 화면 아래 막대의 [업데이트] 를 누르면 바뀝니다. 직접 누르기 전에는 바뀌지 않습니다.")
        if sent:
            self.db.set_meta("telegram_update_notified", version)
        return sent

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
                                                  "allowed_updates": ["message", "callback_query"]}, timeout=35)
                self.status = "연결됨" if self.chat_id else "연결 대기 (코드를 보내세요)"
                backoff, self.conflicts = 5, 0
                self.last_poll = datetime.now().strftime("%H:%M:%S")
                for u in updates:
                    offset = u["update_id"] + 1
                    try:
                        self.handle_update(u)
                    except Exception as e:
                        self.db.log(f"텔레그램 명령 처리 오류: {type(e).__name__}: {e}", "error")
                        self.tell_error(u)
            except urllib.error.HTTPError as e:
                if e.code == 409:   # 같은 봇을 다른 곳(다른 폴더·PC)에서도 켜 둠: 오래 쉬면 명령에 답이 없으므로 짧게만
                    self.conflicts += 1
                    self.status = "오류: 같은 봇을 다른 곳에서도 쓰는 중 (409 Conflict)"
                    if self.conflicts in (1, 30):
                        self.db.log("텔레그램 409 Conflict: 같은 봇 토큰을 쓰는 업무 데스크가 다른 곳에서도 켜져 있습니다. "
                                    "한 곳만 남기세요 (개발 폴더에는 토큰을 넣지 않습니다)", "warn")
                    self.stop_event.wait(8)
                    continue
                self.status = f"오류: HTTP {e.code} {e.reason}"
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, 300)
            except (urllib.error.URLError, OSError, RuntimeError, ValueError) as e:
                self.status = f"오류: {getattr(e, 'reason', e)}"
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, 300)

    def tell_error(self, update):
        """명령을 처리하다 오류가 나면 조용히 넘기지 않고 알림 (원인은 업무 데스크의 '기록')."""
        msg = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
        cid = (msg.get("chat") or {}).get("id")
        if cid is None or cid != self.chat_id:
            return
        try:
            self.send("⚠️ 처리 중 오류가 났습니다. 잠시 뒤 다시 시도하세요. 계속되면 업무 데스크 아래의 '기록'에서 원인을 볼 수 있습니다.", cid)
        except Exception:
            pass

    def start(self):
        self.thread = threading.Thread(target=self.loop, name="telegram", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
