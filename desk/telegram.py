"""텔레그램 봇: 수집 알림 보내기 + 폰에서 보낸 명령 처리.

- 학교 PC 를 밖에 공개하지 않도록, 이 프로그램이 텔레그램에 '새 명령 있나?' 하고 묻는 방식(롱 폴링)입니다.
- 화면에 표시된 연결 코드를 보낸 텔레그램 계정 하나만 명령할 수 있습니다.
- 학생 이름·조퇴 사유는 텔레그램으로 보내지 않습니다 (인원수만). 증 발급은 번호로, 답장의 이름은 '김○○' 처럼.
- 브리핑·검색은 제목과 요약까지, 원문·파일은 단추나 '원문 n'·'파일 n' 으로 달라고 할 때만 (사용자 결정 2026-10-28).
"""

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

from . import absence, ai, assist, passlink, printing, providers, queries
from .collect import SOURCE_NAMES, item_file

HELP = (
    "📋 업무 데스크 명령\n"
    "브리핑 — 오늘 일정·마감 + 지난 1주일 AI 요약\n"
    "오늘 — 오늘 일정·마감·외출 인원\n"
    "마감 — 7일 안의 마감\n"
    "검색 낱말 — 메시지·공문 찾기 (그냥 낱말만 보내도 됨)\n"
    "원문 2 · 파일 2 — 브리핑·검색의 2번 자료 원문·원본 파일\n"
    "내일 수업 · 금요일 시간표 — 내 수업 (시간표를 불러왔을 때)\n"
    "조퇴 7 [13:30] — 7번 조퇴증 발급 (사유는 단추로)\n"
    "외출 7 [13:30] [14:30] — 7번 외출증 발급\n"
    "발급 뒤 기본 프린터 인쇄·학생용 웹 링크(이름 포함) 중에서 고를 수 있습니다.\n"
    "결석계 이름 번호 종류 기간 — 예: 결석계 김가온 7 병결 10/29~11/3\n"
    "종류: 병결·인정결·기타결·미인정결. 주말·학사일정 공휴일·휴업일 제외, 앱에 저장만 합니다.\n"
    "새로고침 — 지금 바로 수집\n"
    "요약 — 최근 받은 공문·메시지\n"
    "도움말 — 이 안내"
    "\n메뉴 — 자주 쓰는 단추 · 취소 — 진행 중인 입력·발급 선택 취소"
    "\n조퇴증·외출증·결석계·자료 검색만 보내면 단계별로 안내합니다."
    "\n목록에 없는 말은 명령어 안내와 AI 검색 단추를 보여 줍니다. 선택하기 전에는 검색하지 않습니다."
    "\n/ask 질문 — 일반 질문용 AI (앱의 AI 질문에서 선택). 업무자료는 붙이지 않습니다."
    "\n외부 API는 전송·요금 안내 후 매번 동의를 받습니다. 개인정보·비공개 자료는 금지."
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
KEYBOARD = [["브리핑", "오늘", "마감"], ["조퇴증", "외출증", "결석계"],
            ["자료 검색", "내 수업", "요약"], ["메뉴", "상태", "취소"]]


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
        f"{l['p']}교시 {l['cls'] or l['subj']}" + ("(교체)" if l.get("moved") else "") for l in lessons)


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
        if arg_cmd:
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

    def send_menu(self, cid, full=False):
        text = (HELP if full else
                "📋 업무 데스크\n아래 메뉴를 누르세요. 조퇴증·외출증·결석계는 차례대로 입력할 수 있습니다.\n"
                "자료 검색은 AI 없이 빠르게 찾고, 목록에 없는 질문은 AI 검색 여부를 먼저 묻습니다.\n"
                "작성 중에는 '취소'로 빠져나올 수 있습니다.")
        self.send(text, cid, keyboard=True)
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
            try:
                fields = self.dialog_absence_fields(dialog, text)
                dates = self.validate_absence(fields)
            except absence.AbsenceError as e:
                return self.reply(cid, str(e))
            dialog.update(step="confirm", period=text)
            prompt = (f"{dialog['number']}번 결석계\n{fields['start_date']}~{fields['end_date']} · 수업일 {len(dates)}일\n"
                      "주말·가져온 학사일정 휴업일을 뺐습니다. 아직 저장하지 않았습니다.")
            self.send(prompt, cid, [[{"text": "앱에 저장", "callback_data": f"dlg:{dialog['id']}:save"},
                                    {"text": "취소", "callback_data": f"dlg:{dialog['id']}:cancel"}]])
            return prompt
        return self.reply(cid, "위의 [앱에 저장] 또는 [취소]를 눌러 주세요.")

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
            rows.append(row)
        return rows or None

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
            ls = queries.my_lessons(self.db, self.config, d)
            lines.append(f"📚 {fmt_day(d.isoformat(), today)} 내 수업 {len(ls)}시간")
            lines += [f"  {l['p']}교시{' ' + l['start'] if l.get('start') else ''} {l['cls']} {l['subj']}"
                      + (" (교체)" if l["moved"] else "") for l in ls] or ["  없음"]
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
                return self.reply(cid, "결석 날짜를 보내세요. 예: 10/29~11/3 또는 2026-10-29\n"
                                  "주말·가져온 학사일정 휴업일을 뺀 날짜를 저장 전에 보여 드립니다.")
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
        if kind in ("pr", "px"):
            pid, _, choice = rest.partition(":")
            self.last_issued_pass_id = None
            text = self.finish_pass(pid, choice if kind == "pr" else None)
            markup = None
            if self.last_issued_pass_id:
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
        self.db.set_meta("telegram_pending", dict(list(pending.items())[-5:]))   # 최근 5개만
        when = time_out + (f"~{time_return}" if time_return else "")
        buttons = [[{"text": r, "callback_data": f"pr:{pid}:{i}"}] for i, r in enumerate(REASONS[kind])]
        buttons.append([{"text": "사유 없이 발급", "callback_data": f"pr:{pid}:-"},
                        {"text": "취소", "callback_data": f"px:{pid}"}])
        text = f"🎫 {number}번 {masked(students[0]['name'])} {word}증 {when}\n사유를 고르면 발급합니다."
        self.send(text, cid, buttons)
        return text

    def finish_pass(self, pid, choice):
        pending = self.db.get_meta("telegram_pending") or {}
        p = pending.pop(pid, None)
        self.db.set_meta("telegram_pending", pending)
        if not p:
            return "이미 처리했거나 오래된 요청입니다."
        if choice is None:
            return f"취소했습니다 ({p['number']}번 {KIND[p['kind']]}증)."
        students = [s for s in self.db.students() if s["number"] == p["number"]]
        if len(students) != 1:
            return f"학생 명단에 {p['number']}번이 없습니다."
        reasons = REASONS[p["kind"]]
        reason = reasons[int(choice)] if choice.isdigit() and int(choice) < len(reasons) else ""
        saved = queries.issue_pass(self.db, {
            "kind": p["kind"], "student_name": students[0]["name"], "student_number": p["number"],
            "date": p["date"], "time_out": p["time_out"], "time_return": p["time_return"],
            "reason": reason, "guardian_ok": 0, "share_reason": 0,
        })
        self.last_issued_pass_id = saved["id"]
        when = p["time_out"] + (f"~{p['time_return']}" if p["time_return"] else "")
        # 이름·사유는 답장에 쓰지 않음
        return (f"✅ {p['number']}번 {masked(students[0]['name'])} {KIND[p['kind']]}증 발급 · {when}\n"
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
            return ("⚙ 업무 데스크 상태\n"
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
            lines.append(f"\n오늘 일정 {len(s['today'])}건")
            lines += [fmt_event(e, today, detail) for e in s["today"][:10]]
            lines.append(f"\n7일 안 마감 {len(s['week_deadlines'])}건")
            lines += [fmt_event(e, today, detail) for e in s["week_deadlines"][:5]]
            lessons = queries.my_lessons(self.db, self.config, today)
            if lessons is not None:
                lines.insert(1, lesson_line(lessons))
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
            rows = self.db.query(f"SELECT id, source, title FROM items WHERE id IN ({marks}) "
                                 "ORDER BY received_at DESC, id DESC LIMIT 5", ids)
            self.set_refs([r["id"] for r in rows])
            for n, r in enumerate(rows, 1):
                lines.append(f"[{n}] [{SOURCE_NAMES.get(r['source'], r['source'])}] {r['title'][:50]}")
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
            ids = self.db.get_meta("telegram_refs", []) if stats.get("new_ids") else []
            buttons = self.item_buttons(ids, ids) if self.config["telegram_detail"] != "counts" else None
            self.send(msg, buttons=buttons)

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
