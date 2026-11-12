"""텔레그램 봇: 수집 알림 보내기 + 폰에서 보낸 명령 처리.

- 학교 PC 를 밖에 공개하지 않도록, 이 프로그램이 텔레그램에 '새 명령 있나?' 하고 묻는 방식(롱 폴링)입니다.
- 화면에 표시된 연결 코드를 보낸 텔레그램 계정 하나만 명령할 수 있습니다.
- 조퇴·외출 사유는 텔레그램으로 보내지 않습니다. 증 발급은 번호로.
- 학생 이름은 선생님 본인 채팅이라 그대로 씁니다 (0.5.93 선생님 결정). 설정 '텔레그램에서 학생 이름 가리기' 를 켜면 '김○○' 처럼 가림.
  기록(db.log)·오류 문구의 이름은 설정과 관계없이 늘 가림.
- 브리핑·검색은 제목과 요약까지, 원문·파일은 단추나 '원문 n'·'파일 n' 으로 달라고 할 때만 (사용자 결정 2026-10-28).
"""

import calendar
import hashlib
import http.client
import io
import json
import re
import secrets
import ssl
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from . import VERSION, absence, ai, calllog, assist, holidays, passlink, printing, providers, queries, timetable, weekplan
from .collect import SOURCE_NAMES, item_file

HELP = (
    "📋 업무 데스크 명령 (웹 화면 왼쪽 메뉴와 같은 순서)\n"
    "아래 단추 판이나 '☰ 메뉴' 의 단추로 대부분 글 없이 할 수 있습니다.\n"
    "\n📋 브리핑 — 오늘 + 지난 접속 이후 AI 요약\n"
    "🗓 주간 — 이번 주 날짜별 일정·마감·할 일 (다음 주 단추)\n"
    "\n📥 수신함\n"
    "안 읽은 자료 · 요약(최근 받은 자료) · 새로고침(지금 수집)\n"
    "낱말만 보내면 메시지·공문 검색 · 원문 2 · 파일 2 — 2번 자료\n"
    "\n📅 달력\n"
    "오늘 — 오늘 일정·오늘 마감·외출 인원 · 마감 — 7일 안의 마감\n"
    "일정 추가 (일정 등록·새 일정도 됨) — 달력에서 날짜를 누르고 내용을 보내면 등록 (등록 직후 취소 가능)\n"
    "\n▦ 시간표\n"
    "내 수업 · 내일 수업 · 금요일 시간표 — 내 수업 · 우리 반 시간표\n"
    "2-3 시간표 · 2-3 내일 · 2학년 3반 목요일 — 그 학급 시간표(과목·선생님)\n"
    "2-3 지금 — 지금(쉬는 시간이면 다음) 그 반 수업\n"
    "빈 시간 김철수 이영희 [다음주] — 나와 같이 비는 시간\n"
    "\n☑ 출결\n"
    "조퇴 7 [13:30] · 외출 7 [13:30] [14:30] — 사유는 단추로, 발급 뒤 인쇄·웹 링크 선택\n"
    "결석계 이름 번호 종류 기간 — 예: 결석계 김가온 7 병결 10/29~11/3\n"
    "종류: 병결·인정결·기타결·미인정결 (주말·학사일정 공휴일·휴업일 제외, 앱에 저장만)\n"
    "통계 — 이번 달 학급 합계 (건수만)\n"
    "\n🔍 검색 — 자료 검색 (AI 없이)\n"
    "🤖 AI 질문 — /ask 질문 (일반 질문 AI, 업무자료는 붙이지 않음)\n"
    "\n⚙ 기타: 메뉴 · 도움말 · 취소 · 상태 · 업데이트(새 버전 확인·바꾸기)\n"
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
    "업데이트": "update", "/update": "update",
    "자료 검색": "guided-search", "내 수업": "lessons", "/lessons": "lessons",
    # 0.5.78 웹 화면 메뉴와 맞춘 명령 (아래 MENU_COMMANDS 가 처리)
    "주간": "week", "주간 계획": "week", "/week": "week",
    "수신함": "g-inbox", "/inbox": "g-inbox", "안 읽은 자료": "unread", "안읽은 자료": "unread", "/unread": "unread",
    "달력": "g-calendar", "/calendar": "g-calendar",
    "시간표": "g-timetable", "/timetable": "g-timetable", "내일 수업": "tomorrow", "/tomorrow": "tomorrow",
    "우리 반 시간표": "myclass", "우리반 시간표": "myclass", "/myclass": "myclass",
    "출결": "g-attendance", "/attendance": "g-attendance",
    "통계": "stats", "출결 통계": "stats", "이번 달 통계": "stats", "/stats": "stats",
    "AI 질문": "ai-dialog", "/ai": "ai-dialog",
}
MENU_COMMANDS = {"week", "unread", "tomorrow", "myclass", "stats", "ai-dialog",
                 "g-inbox", "g-calendar", "g-timetable", "g-attendance", "g-more"}
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
# '/' 봇 명령 메뉴: 웹 화면 왼쪽 메뉴 순서(main.js orderViews) → 자주 쓰는 바로가기 → 기타
BOT_MENU = [
    {"command": "brief", "description": "📋 브리핑 (오늘 + AI 요약)"},
    {"command": "week", "description": "🗓 주간 계획 (이번 주)"},
    {"command": "inbox", "description": "📥 수신함 (안 읽은 자료·수집)"},
    {"command": "calendar", "description": "📅 달력 (오늘·마감·일정 추가)"},
    {"command": "timetable", "description": "▦ 시간표 (내 수업·우리 반)"},
    {"command": "attendance", "description": "☑ 출결 (조퇴·외출·결석계·통계)"},
    {"command": "search", "description": "🔍 자료 검색: /search 낱말"},
    {"command": "ask", "description": "🤖 AI 질문 (업무자료 제외)"},
    {"command": "menu", "description": "☰ 메뉴 단추"},
    {"command": "today", "description": "오늘 일정·마감"},
    {"command": "deadlines", "description": "7일 안의 마감"},
    {"command": "addevent", "description": "일정 추가 (달력에서 날짜)"},
    {"command": "lessons", "description": "오늘 내 수업"},
    {"command": "early", "description": "조퇴증: /early 번호 [시각]"},
    {"command": "out", "description": "외출증: /out 번호 [나감] [복귀]"},
    {"command": "absence", "description": "결석계: /absence 이름 번호 병결 10/29~11/3"},
    {"command": "unread", "description": "안 읽은 자료"},
    {"command": "refresh", "description": "지금 수집"},
    {"command": "status", "description": "준비 상태"},
    {"command": "update", "description": "새 버전 확인·업데이트"},
    {"command": "cancel", "description": "진행 중인 입력 취소"},
    {"command": "help", "description": "도움말"},
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
# 아래 단추 판 (0.5.78): 웹 화면 왼쪽 메뉴와 같은 순서·묶음, 폰 한 화면에 3줄 × 3
KEYBOARD = [["📋 브리핑", "🗓 주간", "📥 수신함"], ["📅 달력", "▦ 시간표", "☑ 출결"], ["🔍 검색", "🤖 AI 질문", "☰ 메뉴"]]
KEY_TEXT = {"📋 브리핑": "브리핑", "🗓 주간": "주간", "📥 수신함": "수신함", "📅 달력": "달력", "▦ 시간표": "시간표",
            "☑ 출결": "출결", "🔍 검색": "자료 검색", "🤖 AI 질문": "AI 질문", "☰ 메뉴": "메뉴"}
# 인라인 메뉴 단추 `mn:<이름>` → 같은 뜻의 글 명령 (여기 있는 이름만 받음)
MENU_ACTIONS = {
    "brief": "브리핑", "week": "주간", "unread": "안 읽은 자료", "summary": "요약", "refresh": "새로고침",
    "today": "오늘", "deadlines": "마감", "addevent": "일정 추가",
    "lessons": "내 수업", "tomorrow": "내일 수업", "myclass": "우리 반 시간표",
    "early": "조퇴증", "out": "외출증", "absence": "결석계", "stats": "통계",
    "search": "자료 검색", "ai": "AI 질문", "status": "상태", "update": "업데이트", "help": "도움말", "cancel": "취소",
}
MAIN_MENU = [[("📋 브리핑", "brief"), ("🗓 주간", "week"), ("📥 수신함", "g:inbox")],
             [("📅 달력", "g:calendar"), ("▦ 시간표", "g:timetable"), ("☑ 출결", "g:attendance")],
             [("🔍 검색", "search"), ("🤖 AI 질문", "ai"), ("⚙ 기타", "g:more")]]
SUBMENUS = {
    "inbox": ("📥 수신함", [[("🆕 안 읽은 자료", "unread"), ("📨 최근 받은 자료", "summary")], [("🔄 지금 수집", "refresh")]]),
    "calendar": ("📅 달력", [[("📅 오늘", "today"), ("⏰ 마감 7일", "deadlines")], [("➕ 일정 추가", "addevent")]]),
    "timetable": ("▦ 시간표", [[("📚 오늘 내 수업", "lessons"), ("📚 내일 수업", "tomorrow")], [("🏫 우리 반 시간표", "myclass")]]),
    "attendance": ("☑ 출결", [[("🏃 조퇴증", "early"), ("🚶 외출증", "out")], [("📝 결석계", "absence"), ("📊 이번 달 통계", "stats")]]),
    "more": ("⚙ 기타", [[("⚙ 상태", "status"), ("⬆ 업데이트", "update")], [("❓ 도움말", "help"), ("✖ 취소", "cancel")]]),
}
GROUP_HINT = {
    "inbox": "낱말만 보내면 자료 검색 · '원문 2'·'파일 2'",
    "calendar": "",
    "timetable": "학급은 '2-3 시간표'·'2-3 지금', 같이 비는 시간은 '빈 시간 이름'",
    "attendance": "글로도: 조퇴 7 13:30 · 외출 7 13:30 14:30",
    "more": "",
}
# 예전 '그 밖의 명령' 메시지(0.5.39~0.5.77)의 단추도 계속 받음
MENU_MORE = ("자료 검색", "일정 추가", "요약", "새로고침", "상태", "취소")
UNREAD_PAGE = 10
# 일정 추가 시작 말 (0.5.87: '일정 추가' 만 알아듣던 것을 넓힘). 문장 맨 앞이 명령 모양일 때만 — 다른 말의 '일정' 은 가로채지 않음
_EV_NOUN = r"(?:새\s*)?(?:일정|스케줄|스케쥴)"
_EV_VERB = r"(?:추가|등록|입력|만들기|넣기|넣어)"
_EV_TAIL = r"(?:하기|해|해\s*줘|해\s*주세요|해\s*주라|해요|할게|할래|\s*줘|\s*주세요)?"
P_ADD_EVENT = re.compile(rf"^(?:/addevent(?:@\S+)?|{_EV_NOUN}\s*{_EV_VERB}{_EV_TAIL})(?:\s+(.*))?$", re.S)
P_ADD_EVENT_BARE = re.compile(rf"^(?:새\s*(?:일정|스케줄|스케쥴)|등록)(?:\s*(?:할게|할래|해\s*줘|하기|해요))?[.!~]*$")   # 뒤에 내용 없이만
P_EVENT_QUESTION = re.compile(r"[?？]\s*$|방법|어떻게|언제|어디|뭐야|뭐지|무엇")   # 내용 자리가 질문이면 명령이 아님
EVENT_TEXT_LIMIT = 100
EVENT_WEEK_HEAD = ("🔴일", "월", "화", "수", "목", "금", "🔵토")   # 일정 추가 달력의 요일 머리줄 (주말 색은 여기만)


STALE_AFTER = 600    # PC 가 꺼져 있던 동안 쌓인 명령은 이보다 오래되면 실행하지 않음 (초)
WATCH_EVERY = 30     # 폴링 스레드가 죽었는지 살펴 되살리는 간격 (초)
# 메시지를 화면·텔레그램·기록에 그대로 보여 줘도 되는 오류 (연결·응답 오류). 그 밖의 오류는 이름만
# 다시 쓴 연결이 이미 닫혀 있을 때 나는 오류 (RemoteDisconnected 는 ConnectionResetError 의 한 종류)
STALE_ERRORS = (ConnectionError, http.client.RemoteDisconnected, http.client.CannotSendRequest)
SAFE_ERRORS = (OSError, http.client.IncompleteRead, http.client.RemoteDisconnected)
P_BOT_TOKEN = re.compile(r"/bot[^/\s]*")          # URL 속 '/bot<번호>:<토큰>'
P_TOKEN_LIKE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{10,}")   # 토큰 모양 (번호:글자)
POLL_BACKOFF = 5     # 오류 뒤 다시 시도하기까지 처음 기다리는 초 (실패가 이어지면 두 배씩, 최대 300초)
PHOTO_LIMIT = 10     # 한 번 수집에서 텔레그램으로 보내는 쿨메신저 사진 최대
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


def fmt_range(start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return f"{a.month}/{a.day}" if a == b else f"{a.month}/{a.day}~{b.month}/{b.day}"


def masked(name):
    """'김민준' → '김○○' (설정 '텔레그램에서 학생 이름 가리기' 를 켰을 때)."""
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
        self.last_error = None  # 마지막 오류 한 줄 (시각 포함) — 상태 화면·'상태' 명령에서 보임
        self.last_handled = None   # 마지막으로 명령·단추를 처리한 시각
        self.polled_at = None      # 직전에 텔레그램에서 새 글을 받아 온 때 (epoch 초) — 오래 끊겼다 돌아왔는지 알아봄
        self.monitor = None
        self.asking = None      # 지금 답을 만드는 질문 (한 번에 하나)
        self.read_image = None  # desk.py 가 연결: 쿨메신저 사진 주소 → (바이트, 종류) 또는 None
        self.updates = None     # desk.py 가 연결: state()·check()·apply(force) — 텔레그램에서 업데이트
        self.sync = False       # 시험: 질문의 답을 기다렸다가 돌려줌
        self.choice_requests = {}
        self.dialog = None
        self.local = threading.local()   # 스레드마다 다시 쓰는 텔레그램 연결 (0.5.87)
        self.direct_cache = {}

    # ---- 텔레그램 API ----
    @property
    def token(self):
        return self.config["telegram_token"]

    def api(self, method, params=None, timeout=35):
        base = self.config['telegram_api_base'].rstrip('/')
        url = f"{base}/bot{self.token}/{method}"
        data = json.dumps(params or {}).encode("utf-8")
        if self.direct(base):
            raw = self.keepalive_post(base, url, data, timeout)
        else:   # 프록시를 쓰는 PC: 예전 방식 (urllib 가 프록시 설정을 따름)
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
        body = json.loads(raw.decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(body.get("description", "텔레그램 오류"))
        return body["result"]

    # ---- 연결 다시 쓰기 (0.5.87): 부를 때마다 새 HTTPS 연결(TCP+TLS)을 열면 단추 한 번에 왕복이 여러 번 늘어 느림 ----
    def direct(self, base):
        """프록시 없이 바로 연결하는 주소인지 (5분마다 다시 확인). 프록시가 있으면 예전 urllib 방식."""
        now = time.monotonic()
        hit = self.direct_cache.get(base)
        if hit and now - hit[1] < 300:
            return hit[0]
        parts = urllib.parse.urlsplit(base)
        try:
            proxied = bool(urllib.request.getproxies().get(parts.scheme)) and not urllib.request.proxy_bypass(parts.hostname or "")
        except Exception:
            proxied = True   # 확인하지 못하면 안전하게 예전 방식
        self.direct_cache[base] = (not proxied and parts.scheme in ("http", "https"), now)
        return self.direct_cache[base][0]

    def keepalive_post(self, base, url, data, timeout):
        """같은 스레드에서는 한 연결을 계속 씀. 오래 쉬어 서버가 닫은 연결이면 새로 열어 한 번만 다시 보냄."""
        parts = urllib.parse.urlsplit(url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        conns = self.local.__dict__.setdefault("conns", {})
        for attempt in (0, 1):
            conn = conns.get(base)
            reused = conn is not None
            if conn is None:
                cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
                conn = conns[base] = cls(parts.hostname, parts.port, timeout=timeout)
            try:
                conn.timeout = timeout
                if conn.sock:
                    conn.sock.settimeout(timeout)
                conn.request("POST", path, body=data, headers={"Content-Type": "application/json", "Connection": "keep-alive"})
                r = conn.getresponse()
                raw = r.read()
            except BaseException as e:
                conn.close()
                conns.pop(base, None)
                # 쉬는 동안 서버가 닫은 연결 (0.5.88: 윈도우는 ConnectionAbortedError·WinError 10053 으로 옴 → ConnectionError 전체).
                # TLS 가 끊겨 SSL 오류로 오는 것은 다시 쓴 연결일 때만 (새 연결의 SSL 오류는 인증서 문제일 수 있어 그대로)
                stale = isinstance(e, STALE_ERRORS) or (reused and isinstance(e, ssl.SSLError))
                if not stale:
                    raise
                if reused and attempt == 0:
                    continue   # 새 연결로 한 번만
                raise urllib.error.URLError(e) from e
            if r.getheader("Connection", "").lower() == "close":
                conn.close()
                conns.pop(base, None)
            if r.status >= 400:   # 예전과 같게 HTTPError (409 Conflict 처리·오류 문구가 그대로 동작)
                raise urllib.error.HTTPError(url, r.status, r.reason, r.headers, io.BytesIO(raw))
            return raw
        raise urllib.error.URLError("연결 실패")

    def send(self, text, chat_id=None, buttons=None, keyboard=False):
        chat_id = chat_id or self.chat_id
        if not (self.token and chat_id):
            return False
        parts = chunks(text) if len(text) > TEXT_LIMIT else [text]
        for part in parts[:-1]:   # 긴 글은 줄 단위로 나눠 보내고, 단추는 마지막 조각에만
            self.api("sendMessage", {"chat_id": chat_id, "text": part[:4000]}, timeout=15)
        params = {"chat_id": chat_id, "text": parts[-1][:4000]}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        elif keyboard:
            params["reply_markup"] = {"keyboard": KEYBOARD, "resize_keyboard": True,
                                      "is_persistent": True, "input_field_placeholder": "메뉴를 고르거나 명령을 입력하세요"}
        self.api("sendMessage", params, timeout=15)
        return True

    def send_file(self, chat_id, name, data, caption="", photo=False):
        """파일 하나 보내기 (sendDocument, photo=True 면 sendPhoto, multipart). 표준 라이브러리만."""
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in (("chat_id", str(chat_id)), ("caption", caption[:1000])):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8"))
        quoted = urllib.parse.quote(name)
        field, method = ("photo", "sendPhoto") if photo else ("document", "sendDocument")
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{quoted}"; '
                      f"filename*=UTF-8''{quoted}\r\nContent-Type: application/octet-stream\r\n\r\n").encode("utf-8")
                     + data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        url = f"{self.config['telegram_api_base'].rstrip('/')}/bot{self.token}/{method}"
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
        if msg.get("document"):
            return self.handle_calllog_file(msg)   # 통화·문자 기록 파일 (0.5.84)
        chat = msg.get("chat") or {}
        text = (msg.get("text") or "").strip()
        text = KEY_TEXT.get(text, text)   # 아래 단추 판의 '📋 브리핑' 같은 단추 글 → 명령 글
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
        event_dialog = self.dialog and self.dialog.get("kind") == "event"
        if event_dialog and COMMANDS.get(text) != "cancel" and head != "/cancel":
            # 일정 추가 대화 중에는 '취소' 말고는 모두 대화로: 명령 낱말·'/…'·'일정 추가' 도 일정 내용으로 쓸 수 없다고 안내하고 다시 받음
            return self.dialog_input(cid, text)
        added = self.add_event_words(text)
        if added is not None:   # '일정 추가'·'일정 등록'·'새 일정' 같은 명령 모양 또는 메뉴 단추로만 시작
            return self.begin_event(cid, added)
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
        if cmd == "update":
            self.dialog = None
            return self.send_update_offer(cid)
        if cmd == "lessons":
            self.dialog = None
            return self.reply(cid, self.lessons_text(""))
        if cmd in MENU_COMMANDS:
            self.dialog = None
            return self.menu_command(cid, cmd)
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
        found_tt = assist.timetable_intent(text)   # '2-3 시간표'·'빈 시간 …' 처럼 뚜렷한 모양일 때만
        if found_tt:
            self.dialog = None
            return self.timetable_command(cid, *found_tt)
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
        if full:
            self.send(HELP, cid, keyboard=True)
            return HELP
        # 아래 단추 판을 새것으로 바꾸고(예전 2줄 판이 남아 있을 수 있음), 같은 묶음의 인라인 메뉴를 한 번 더
        self.send("📋 업무 데스크 — 아래 단추 판은 웹 화면 왼쪽 메뉴와 같은 순서입니다.", cid, keyboard=True)
        text, buttons = self.main_menu()
        self.send(text, cid, buttons)
        return text

    # ---- 메뉴 단추 (0.5.78: 웹 화면 메뉴와 같은 묶음, 하위 메뉴는 같은 메시지를 고쳐 보여 줌) ----
    @staticmethod
    def menu_rows(rows):
        return [[{"text": label, "callback_data": f"mn:{action}"} for label, action in row] for row in rows]

    def main_menu(self):
        return "☰ 메뉴 — 묶음을 누르면 그 안의 단추가 나옵니다.", self.menu_rows(MAIN_MENU)

    def group_menu(self, name):
        title, rows = SUBMENUS[name]
        hint = GROUP_HINT.get(name)
        return title + (f"\n{hint}" if hint else ""), self.menu_rows(rows) + [[{"text": "← 메뉴", "callback_data": "mn:home"}]]

    def menu_button(self, cid, action, message_id):
        """인라인 메뉴 단추. 정해 둔 이름만 받고, 나머지는 무시."""
        if action == "home":
            return self.show(cid, message_id, *self.main_menu())
        if action.startswith("g:") and action[2:] in SUBMENUS:
            return self.show(cid, message_id, *self.group_menu(action[2:]))
        if action.startswith("unread:") and action[7:].isdigit():
            return self.send_unread(cid, int(action[7:]))
        if action in ("week:0", "week:1"):
            return self.send_week(cid, nxt=action == "week:1", message_id=message_id)
        text = MENU_ACTIONS.get(action) or (action if action in MENU_MORE else None)
        if not text:
            return None
        return self.handle_update({"message": {"chat": {"id": cid, "type": "private"}, "text": text}})

    def menu_command(self, cid, cmd):
        if cmd.startswith("g-"):
            text, buttons = self.group_menu(cmd[2:])
            self.send(text, cid, buttons)
            return text
        if cmd == "week":
            return self.send_week(cid)
        if cmd == "unread":
            return self.send_unread(cid, 0)
        if cmd == "tomorrow":
            return self.reply(cid, self.lessons_text("내일"))
        if cmd == "myclass":
            grade, no = str(self.config["grade"] or "").strip(), str(self.config["class_no"] or "").strip()
            if not (grade.isdigit() and no.isdigit()):
                return self.reply(cid, "담임 학급이 설정에 없습니다. 업무 데스크 ⚙ 설정 → 기본 설정에서 학년·반을 넣거나 '2-3 시간표'처럼 보내세요.")
            return self.timetable_command(cid, "class_day", {"cls": f"{grade}-{no}", "text": "오늘"})
        if cmd == "stats":
            return self.reply(cid, self.month_stats_text())
        if cmd == "ai-dialog":
            return self.begin_dialog(cid, "general")
        return None

    def week_text(self, nxt=False, today=None):
        """주간 계획 (웹 '주간 계획' 과 같은 자료, 읽기만). 오늘·마감·브리핑과 같은 조회 답이라 일정 제목은 가리지 않음 (2026-11-06 결정)."""
        today = today or date.today()
        plan = weekplan.week_plan(self.db, today + timedelta(days=7 if nxt else 0), 7)
        counts = self.config["telegram_detail"] == "counts"
        start, end = date.fromisoformat(plan["start"]), date.fromisoformat(plan["end"])
        lines = [f"🗓 {'다음 주' if nxt else '이번 주'} 계획 {start.month}/{start.day}(월)~{end.month}/{end.day}(일)"]

        def day_lines(col, note):
            d = date.fromisoformat(col["date"])
            out = [f"\n■ {d.month}/{d.day}({WD[d.weekday()]})" + (" · 오늘" if d == today else "") + (f" · {note}" if note else "")]
            out += [f"  🏫 {cut(t, 40)}" for t in col["school"][:3]]
            evs = col["events"]
            if counts:
                if evs or col["todos"]:
                    out.append(f"  일정 {len(evs)}건 · 할 일 {len(col['todos'])}건")
                return out
            out += [f"  {'⏰' if e['kind'] == 'deadline' else '•'}{' ' + e['time'] if e.get('time') else ''} "
                    f"{cut(e.get('item_title') or e['text'], 45)}" for e in evs[:8]]
            if len(evs) > 8:
                out.append(f"  … 그 외 {len(evs) - 8}건")
            out += [f"  ☐ {cut(t['text'], 45)}" for t in col["todos"][:5]]
            return out

        for col in plan["cols"]:
            lines += day_lines(col, f"⚠ {cut(col['check'], 20)}" if col.get("check") else "")
        for col in plan["other"]:
            d = date.fromisoformat(col["date"])
            note = "주말" if d.weekday() >= 5 else ("수업 없음" + (f"({cut(col['reason'], 20)})" if col.get("reason") else ""))
            lines += day_lines(col, note)
        if plan["overdue"] and not counts:
            lines.append(f"\n⚠ 지난 할 일 {len(plan['overdue'])}건")
            lines += [f"  ☐ {cut(t['text'], 45)}" for t in plan["overdue"][:5]]
        if plan["undated"] and not counts:
            lines.append(f"\n꼭 할 일 (날짜 없음) {len(plan['undated'])}건")
            lines += [f"  ☐ {cut(t['text'], 45)}" for t in plan["undated"][:5]]
        if len(lines) == 1:
            lines.append("이 주에는 일정·할 일이 없습니다.")
        return "\n".join(lines)

    def send_week(self, cid, nxt=False, message_id=None):
        text = self.week_text(nxt)
        buttons = [[{"text": "◀ 이번 주" if nxt else "다음 주 ▶", "callback_data": f"mn:week:{0 if nxt else 1}"}],
                   [{"text": "← 메뉴", "callback_data": "mn:home"}]]
        if len(text) <= TEXT_LIMIT:
            return self.show(cid, message_id, text, buttons)
        self.send(text, cid, buttons)
        return text

    def unread_text(self, offset=0):
        """안 읽은 자료 (수신함 '안 읽음'), 제목까지. (글, 단추)"""
        rows = self.db.query("SELECT id, source, received_at, category, title FROM items "
                             "WHERE status = 'new' AND source != 'paste' ORDER BY received_at DESC, id DESC")
        if not rows:
            self.set_refs([])
            return "🆕 안 읽은 자료가 없습니다 👍", None
        detail = self.config["telegram_detail"]
        if detail == "counts":
            self.set_refs([])
            by = {}
            for r in rows:
                name = SOURCE_NAMES.get(r["source"], r["source"])
                by[name] = by.get(name, 0) + 1
            return f"🆕 안 읽은 자료 {len(rows)}건 (" + " · ".join(f"{k} {v}" for k, v in by.items()) + ")", None
        offset = max(0, min(offset, len(rows) - 1))
        page = rows[offset:offset + UNREAD_PAGE]
        ids = [r["id"] for r in rows[:offset + UNREAD_PAGE]][:100]   # '원문 n' 번호가 앞 쪽부터 이어지게
        self.set_refs(ids)
        lines = [f"🆕 안 읽은 자료 {len(rows)}건" + (f" ({offset + 1}~{offset + len(page)})" if len(rows) > UNREAD_PAGE else "")]
        for n, r in enumerate(page, offset + 1):
            lines.append(f"\n{n}. [{SOURCE_NAMES.get(r['source'], r['source'])}] {r['received_at'][5:10].replace('-', '/')} · {r['category']}")
            lines.append(cut(self.mask(r["title"]), 70))
        lines.append("\n읽음 처리는 업무 데스크 수신함에서 합니다. 원문은 단추나 '원문 n'.")
        buttons = self.item_buttons([r["id"] for r in page], ids) or []
        if offset + UNREAD_PAGE < len(rows) and offset + UNREAD_PAGE < 100:
            buttons.append([{"text": f"더보기 ▸ ({len(rows) - offset - UNREAD_PAGE}건 더)",
                             "callback_data": f"mn:unread:{offset + UNREAD_PAGE}"}])
        return "\n".join(lines), buttons

    def send_unread(self, cid, offset=0):
        text, buttons = self.unread_text(offset)
        self.send(text, cid, buttons)
        return text

    def month_stats_text(self, today=None):
        """이번 달 출결 통계 — 학급 합계 건수만 (학생 이름·번호·사유는 싣지 않음)."""
        today = today or date.today()
        first = today.replace(day=1)
        last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        passes = self.db.query("SELECT kind, COUNT(*) AS n FROM passes WHERE date >= ? AND date <= ? AND status != 'cancelled' "
                               "GROUP BY kind", (first.isoformat(), last.isoformat()))
        count = {r["kind"]: r["n"] for r in passes}
        rows = self.db.query("SELECT start_date, end_date, absence_type FROM absences WHERE start_date <= ? AND end_date >= ?",
                             (last.isoformat(), first.isoformat()))
        days = dict.fromkeys(absence.TYPES, 0)
        for r in rows:
            got = absence.school_dates(self.db, max(date.fromisoformat(r["start_date"]), first),
                                       min(date.fromisoformat(r["end_date"]), last), self.config["grade"])
            days[r["absence_type"]] = days.get(r["absence_type"], 0) + len(got)
        kinds = " · ".join(f"{k} {v}" for k, v in days.items() if v)
        return (f"📊 {today.month}월 출결 (학급 합계, {today.month}/{today.day} 까지 기록)\n"
                f"🏃 조퇴 {count.get('early', 0)}건 · 🚶 외출 {count.get('out', 0)}건\n"
                f"📝 결석계 {len(rows)}건 · 결석 {sum(days.values())}일" + (f" ({kinds})" if kinds else "")
                + "\n학생별 통계는 업무 데스크 → 출결 → 월별 통계에서 봅니다.")

    def begin_dialog(self, cid, kind):
        self.dialog = {"id": secrets.token_hex(6), "kind": kind, "step": "query" if kind == "search" else "number",
                       "expires": time.monotonic() + DIALOG_TTL}
        if kind == "general":
            self.dialog["step"] = "question"
        prompt = ("찾을 낱말을 보내세요. 예: 연수 신청\n자료만 검색하며 AI는 실행하지 않습니다." if kind == "search" else
                  "🤖 질문을 보내세요. 일반 질문 AI 로 답하며 업무자료는 붙이지 않습니다.\n학생 정보·비공개 자료는 쓰지 마세요."
                  if kind == "general" else "학생 번호를 함께 보내세요. 예: 7\n명단에 있는 번호만 쓸 수 있습니다.")
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
        if kind == "event":
            return self.event_text_input(cid, dialog, text)
        if kind == "general":
            self.dialog = None
            return self.offer_general(cid, text)
        if kind == "free":
            return self.free_pick(cid, dialog, text.strip())
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
            return self.pass_time_prompt(cid, dialog)
        if step == "time":
            args = text.split()
            count = 2 if kind == "out" else 1
            if len(args) != count or any(parse_time(word) is None for word in args):
                return self.reply(cid, "시각을 " + ("13:30 14:30" if kind == "out" else "13:30") + "처럼 보내세요.")
            return self.start_pass(cid, kind, [str(dialog["number"]), *args], day=dialog.get("pass_day"))
        if step == "pdate":
            return self.reply(cid, "위 달력에서 날짜를 눌러 주세요. 그만하려면 '취소'.")
        if step == "type":
            return self.reply(cid, "위의 결석 종류 단추를 눌러 주세요. 그만하려면 '취소'.")
        if step == "date":
            return self.confirm_absence(cid, dialog, text)
        return self.reply(cid, "위의 [앱에 저장] 또는 [취소]를 눌러 주세요.")

    def pass_time_prompt(self, cid, dialog, message_id=None):
        """조퇴증·외출증 시각 묻기. 날짜는 기본 오늘, [📅 다른 날짜] 로 달력에서 고름."""
        kind, day = dialog["kind"], dialog.get("pass_day")
        dialog["step"] = "time"
        prompt = (f"{dialog['number']}번 {KIND[kind]}" + (f" {fmt_day(day.isoformat(), date.today())}" if day else "")
                  + " 시각을 보내세요. " + ("예: 13:30 14:30 (나감·복귀)" if kind == "out" else "예: 13:30"))
        row = [] if day else [{"text": "지금 시각으로", "callback_data": f"dlg:{dialog['id']}:now"}]
        row.append({"text": "📅 다른 날짜" if not day else "📅 날짜 바꾸기", "callback_data": f"dlg:{dialog['id']}:pd"})
        return self.show(cid, message_id, prompt, [row])

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
        dups = self.absence_duplicates(fields["student_number"], fields["student_name"], fields["start_date"], fields["end_date"])
        if dups:   # 이미 신청한 기간과 겹침 (신청할 때 알림)
            prompt = ("⚠ 중복 신청일 수 있습니다. 이미 있는 결석계:\n" + "\n".join(f"- {n}" for n in dups[:5]) + "\n\n" + prompt)
        return self.show(cid, message_id, prompt, [[
            {"text": "그래도 앱에 저장" if dups else "앱에 저장", "callback_data": f"dlg:{dialog['id']}:save"},
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
                [{"text": w, "callback_data": f"dlg:{did}:nop"}
                 for w in (EVENT_WEEK_HEAD if dialog["kind"] == "event" else "일월화수목금토")]]
        last = calendar.monthrange(year, month)[1]
        # 학사일정 휴업일은 결석계·조퇴·외출에서만 사용하고, 일정 추가는 법정공휴일 표로 표시.
        closed = set() if dialog["kind"] == "event" else absence.closed_days(self.db, date(year, month, 1), date(year, month, last))
        for week in calendar.Calendar(firstweekday=6).monthdayscalendar(year, month):
            row = []
            for day in week:
                if not day:
                    row.append(nop)
                    continue
                d = date(year, month, day)
                row.append({"text": self.day_label(d, start, closed, dialog["kind"] == "event"),
                            "callback_data": f"dlg:{did}:cd:{d:%Y%m%d}"})
            rows.append(row)
        if dialog["kind"] == "event":
            rows.append([{"text": "취소", "callback_data": f"dlg:{did}:cancel"}])
        elif dialog["kind"] in KIND:   # 조퇴·외출: 날짜 하나만 고름
            rows.append([{"text": "오늘로", "callback_data": f"dlg:{did}:td"},
                         {"text": "취소", "callback_data": f"dlg:{did}:cancel"}])
        elif start:
            rows.append([{"text": "하루만", "callback_data": f"dlg:{did}:one"},
                         {"text": "시작일 다시 고르기", "callback_data": f"dlg:{did}:cr"},
                         {"text": "취소", "callback_data": f"dlg:{did}:cancel"}])
        else:
            rows.append([{"text": "취소", "callback_data": f"dlg:{did}:cancel"}])
        return rows

    # ---- 일정 추가: 달력에서 날짜 → 내용 한 줄 → 확인 → 등록 (등록 직후 취소 가능) ----
    def begin_event(self, cid, preset=None):
        preset = " ".join((preset or "").split())
        if preset and self.event_text_error(preset):
            return self.reply(cid, self.event_text_error(preset))
        self.dialog = {"id": secrets.token_hex(6), "kind": "event", "step": "edate", "text": preset or None,
                       "expires": time.monotonic() + DIALOG_TTL}
        today = date.today()
        return self.show_calendar(cid, self.dialog, None, today.year, today.month)

    @staticmethod
    def add_event_words(text):
        """일정 추가를 시작하는 말이면 미리 적은 내용('' 이면 없음), 아니면 None.
        '일정 등록 방법'·'새 일정 있어?' 처럼 뒤가 질문이면 None (검색·질문으로, 0.5.47 규칙)."""
        text = (text or "").strip()
        if P_ADD_EVENT_BARE.match(text):
            return ""
        m = P_ADD_EVENT.match(text)
        if not m:
            return None
        preset = (m.group(1) or "").strip()
        if preset and not text.startswith("/") and P_EVENT_QUESTION.search(preset):
            return None
        return preset

    @staticmethod
    def event_text_error(text):
        if not text:
            return "일정 내용을 글 한 줄로 보내세요. 예: 학년 협의회"
        if len(text) > EVENT_TEXT_LIMIT:
            return f"일정 내용이 너무 깁니다 ({EVENT_TEXT_LIMIT}자 이하로 줄여 보내세요)."
        if text.startswith("/") or text in COMMANDS or text in ARG_COMMANDS or Bot.add_event_words(text) is not None:
            return "명령어로 보이는 글은 일정 내용으로 쓸 수 없습니다. 내용을 다시 보내거나 '취소' 를 보내세요."
        return None

    @staticmethod
    def plain_day(d):
        return f"{d.month}/{d.day}({WD[d.weekday()]})"

    def event_action(self, cid, dialog, action, message_id):
        """일정 추가 대화의 단추. 처리했으면 결과 글, 이 단계의 단추가 아니면 None."""
        step = dialog["step"]
        year, month = dialog.get("cal_month") or (date.today().year, date.today().month)
        if action == "nop":
            return ""
        if action.startswith("cm:") and step == "edate" and re.fullmatch(r"\d{6}", action[3:]):
            y, m = int(action[3:7]), int(action[7:9])
            if not (2000 <= y <= 2100 and 1 <= m <= 12):
                return self.reply(cid, "달력에 없는 달입니다.")
            return self.show_calendar(cid, dialog, message_id, y, m)
        if action.startswith("cd:") and step == "edate":
            try:
                picked = datetime.strptime(action[3:], "%Y%m%d").date()
            except ValueError:
                return self.reply(cid, "달력에 없는 날짜입니다.")
            dialog["date"] = picked
            if dialog.get("text"):   # '일정 추가 학년 협의회' 처럼 내용을 함께 보냈으면 바로 확인
                return self.event_confirm(cid, dialog, message_id)
            dialog["step"] = "etext"
            return self.show(cid, message_id,
                             f"📅 {self.plain_day(picked)} · 일정 내용을 글 한 줄로 보내세요 ({EVENT_TEXT_LIMIT}자 이하). 예: 학년 협의회\n그만하려면 '취소'.",
                             [[{"text": "날짜 바꾸기", "callback_data": f"dlg:{dialog['id']}:ed"},
                               {"text": "취소", "callback_data": f"dlg:{dialog['id']}:cancel"}]])
        if action == "ed" and step in ("etext", "econfirm"):
            dialog["step"] = "edate"
            d = dialog.get("date") or date.today()
            return self.show_calendar(cid, dialog, message_id, d.year, d.month)
        if action in ("reg", "regd") and step == "econfirm":
            return self.register_event(cid, dialog, "deadline" if action == "regd" else "event", message_id)
        return None

    def event_text_input(self, cid, dialog, text):
        step = dialog["step"]
        if step == "edate":
            return self.reply(cid, "위 달력에서 날짜를 눌러 주세요. 그만하려면 '취소'.")
        if step == "econfirm":
            return self.reply(cid, "위의 [등록] 또는 [취소]를 눌러 주세요.")
        text = " ".join(text.split())
        error = self.event_text_error(text)
        if error:
            return self.reply(cid, error)
        dialog["text"] = text
        return self.event_confirm(cid, dialog, None)

    def event_confirm(self, cid, dialog, message_id):
        d, text = dialog["date"], dialog["text"]
        dialog["step"] = "econfirm"
        dup = self.db.one("SELECT id FROM events WHERE date = ? AND text = ?", (d.isoformat(), text))
        prompt = f"📅 {self.plain_day(d)} {self.mask(text)} — 등록할까요?"
        if dup:
            prompt += "\n⚠ 같은 날 같은 내용이 이미 있습니다."
        holiday = holidays.holiday_name(d)
        if holiday:
            prompt += f"\n⚠ {holiday}입니다" if holiday.startswith("대체공휴일") else f"\n⚠ 공휴일({holiday})입니다"
        elif holidays.is_weekend(d):
            prompt += f"\n⚠ {'토요일' if d.weekday() == 5 else '일요일'}입니다"
        return self.show(cid, message_id, prompt, [
            [{"text": "그래도 등록" if dup else "등록", "callback_data": f"dlg:{dialog['id']}:reg"},
             {"text": "마감으로 등록", "callback_data": f"dlg:{dialog['id']}:regd"}],
            [{"text": "날짜 바꾸기", "callback_data": f"dlg:{dialog['id']}:ed"},
             {"text": "취소", "callback_data": f"dlg:{dialog['id']}:cancel"}]])

    def register_event(self, cid, dialog, kind, message_id):
        d, text = dialog["date"], dialog["text"]
        self.dialog = None   # 중복 클릭으로 두 번 등록하지 않음
        row = queries.insert_event(self.db, {"date": d.isoformat(), "kind": kind, "text": text,
                                             "category": "제출·마감" if kind == "deadline" else "참고"})
        # 텔레그램에서 더한 것만 [방금 등록 취소] 로 지울 수 있음. 번호는 지우면 새 일정이 이어받을 수 있어 날짜·내용 앞 20자도 함께 적어 둠
        token = secrets.token_hex(3)   # 등록마다 하나 — 번호가 재사용돼도 옛 단추가 다른 일정을 지우지 못하게 단추에 함께 넣음
        entries = (self.db.get_meta("telegram_added_events") or [])[-19:] + [
            {"id": row["id"], "date": row["date"], "key": queries.added_event_key(text), "tok": token}]
        self.db.set_meta("telegram_added_events", entries)
        self.db.log(f"텔레그램에서 일정 추가 ({self.plain_day(d)} {'마감' if kind == 'deadline' else '일정'})")
        return self.show(cid, message_id,
                         f"✅ {self.plain_day(d)} {self.mask(text)} 등록했습니다 ({'마감' if kind == 'deadline' else '일정'}). 달력에서 볼 수 있습니다.",
                         [[{"text": "방금 등록 취소", "callback_data": f"evu:{row['id']}:{token}"}]])

    def undo_event(self, cid, eid, message_id, token=""):
        # 취소 목록 읽기·검사·삭제·목록 갱신을 한 잠금 안에서 (화면에서 같은 번호를 지우고 다시 만드는 일과 엇갈리지 않게).
        # 텔레그램 답은 잠금을 푼 뒤에 보냄
        with self.db.lock:
            text = self._undo_event_locked(eid, token)
        return self.show(cid, message_id, text, [])

    def _undo_event_locked(self, eid, token):
        entries = self.db.get_meta("telegram_added_events") or []
        entry = next((e for e in entries if (e.get("id") if isinstance(e, dict) else e) == eid), None)
        if isinstance(entry, dict) and entry.get("tok") and entry["tok"] != token:
            entry = None   # 같은 번호를 이어받은 다른 등록의 단추가 아님 (이 단추는 이미 지워진 옛 등록의 것)
        if entry is None:
            return "이미 취소했거나 지울 수 없는 일정입니다."
        row = self.db.one("SELECT * FROM events WHERE id = ?", (eid,))
        rest = [e for e in entries if e is not entry]
        if not isinstance(entry, dict):   # 예전 형식(번호만): 같은 일정인지 확인할 수 없어 지우지 않음
            self.db.set_meta("telegram_added_events", rest)
            return "예전 방식으로 등록한 일정이라 같은 일정인지 확인할 수 없어 취소하지 않았습니다. 달력에서 직접 지워 주세요."
        # 그 번호의 지금 행이 등록한 그 일정(날짜·내용 앞 20자)일 때만 지움 — 화면에서 지운 번호를 새 일정이 이어받았을 수 있음
        if not row or row["item_id"] is not None or row["date"] != entry.get("date") \
                or queries.added_event_key(row["text"]) != entry.get("key"):
            self.db.set_meta("telegram_added_events", rest)
            return "이미 지워졌거나 바뀐 일정이라 취소하지 않았습니다."
        self.db.execute("DELETE FROM events WHERE id = ?", (eid,))
        queries.bump_events_rev(self.db)
        self.db.set_meta("telegram_added_events", rest)
        self.db.log("텔레그램에서 등록한 일정을 취소")
        return f"↩ 등록을 취소했습니다 ({self.plain_day(date.fromisoformat(row['date']))} {self.mask(row['text'] or '')})."

    @staticmethod
    def day_label(d, start, closed, event=False):
        """달력 칸에 쓸 글 (폰 7칸에서 잘리지 않게 보이는 글자 3자 이하, 0.5.88).
        일정 추가 달력: 공휴일은 숫자 뒤 '*' ('15*'), 주말은 요일 머리줄(🔴일·🔵토)로 구분하고 칸에는 숫자만.
        예전 '🔴15' 는 좁은 폰에서 '🔴1' 로 잘려 보였음 (선생님 신고 2026-11-10)."""
        if d == start:
            return f"▶{d.day}"
        if event:
            return f"{d.day}*" if holidays.holiday_name(d) else str(d.day)
        return f"({d.day})" if d in closed else str(d.day)

    def show_calendar(self, cid, dialog, message_id, year, month):
        if dialog["kind"] == "event":
            dialog["cal_month"] = (year, month)
            preset = f"\n내용: {cut(self.mask(dialog['text']), 40)}" if dialog.get("text") else ""
            first, last = holidays.year_range()
            text = (f"📅 일정을 넣을 날짜를 누르세요 (하루만 고릅니다).{preset}\n"
                    f"🔴일 🔵토 칸이 주말 · 숫자 뒤 * 는 공휴일 (공휴일 표: {first}~{last}년)")
            return self.show(cid, message_id, text, self.calendar_buttons(dialog, year, month))
        if dialog["kind"] in KIND:
            dialog["cal_month"] = (year, month)
            text = (f"{dialog['number']}번 {KIND[dialog['kind']]}증 날짜를 누르세요\n오늘 이후만 고를 수 있습니다. "
                    "(숫자)는 학사일정 휴업일·공휴일입니다.")
            return self.show(cid, message_id, text, self.calendar_buttons(dialog, year, month))
        start = dialog.get("cal_start")
        head = (f"{dialog['number']}번 결석 날짜\n"
                + (f"시작일 {start:%m/%d} · 끝나는 날을 누르세요 (같은 날을 또 누르거나 [하루만])"
                   if start else "결석이 시작되는 날을 누르세요"))
        text = head + "\n주말은 누를 수 있고 수업일 계산에서만 뺍니다. (숫자)는 학사일정 휴업일·공휴일입니다.\n글로 보내도 됩니다. 예: 10/29~11/3"
        dialog["cal_month"] = (year, month)
        return self.show(cid, message_id, text, self.calendar_buttons(dialog, year, month))

    def calendar_action(self, cid, dialog, action, message_id):
        """달력 단추. 처리했으면 결과 글, 아니면 None."""
        if dialog["kind"] in KIND and action in ("pd", "td") and dialog["step"] in ("time", "pdate"):
            if action == "pd":
                dialog["step"] = "pdate"
                today = date.today()
                return self.show_calendar(cid, dialog, message_id, today.year, today.month)
            dialog.pop("pass_day", None)   # 오늘로 돌아감
            return self.pass_time_prompt(cid, dialog, message_id)
        if dialog["kind"] in KIND and dialog["step"] == "pdate" and (action in ("nop",) or action.startswith(("cm:", "cd:"))):
            return self.pass_calendar_action(cid, dialog, action, message_id)
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

    def pass_calendar_action(self, cid, dialog, action, message_id):
        if action == "nop":
            return ""
        if action.startswith("cm:"):
            if not re.fullmatch(r"\d{6}", action[3:]) or not (2000 <= int(action[3:7]) <= 2100 and 1 <= int(action[7:9]) <= 12):
                return self.reply(cid, "달력에 없는 달입니다.")
            return self.show_calendar(cid, dialog, message_id, int(action[3:7]), int(action[7:9]))
        try:
            picked = datetime.strptime(action[3:], "%Y%m%d").date()
        except ValueError:
            return self.reply(cid, "달력에 없는 날짜입니다.")
        if picked < date.today():
            return self.reply(cid, "지난 날짜는 고를 수 없습니다. 오늘 이후 날짜를 누르세요.")
        if picked == date.today():
            dialog.pop("pass_day", None)
        else:
            dialog["pass_day"] = picked
        return self.pass_time_prompt(cid, dialog, message_id)

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
            if not args:
                return self.begin_dialog(cid, "general")
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
        if self.absence_duplicates(fields["student_number"], fields["student_name"], fields["start_date"], fields["end_date"]):
            # 이미 신청한 기간과 겹치면 한 줄 명령도 바로 저장하지 않고 확인을 거침 (단계별 입력과 같은 화면)
            self.dialog = {"id": secrets.token_hex(6), "kind": "absence", "step": "date", "number": fields["student_number"],
                           "student_name": fields["student_name"], "type": fields["reason"],
                           "expires": time.monotonic() + DIALOG_TTL}
            return self.confirm_absence(cid, self.dialog, f"{fields['start_date']}~{fields['end_date']}")
        return self.save_absence(cid, fields, dates)

    def absence_duplicates(self, number, name, start, end):
        """같은 학생의 겹치는 결석계 줄들 (이름·사유 없이 발급 번호와 기간만)."""
        rows = self.db.query(
            "SELECT serial, start_date, end_date FROM absences WHERE (student_number = ? OR student_name = ?) "
            "AND start_date <= ? AND end_date >= ? ORDER BY start_date", (number, name, end, start))
        return [f"결석계 {r['serial']} ({fmt_range(r['start_date'], r['end_date'])})" for r in rows]

    def pass_duplicates(self, kind, number, name, day, time_out):
        """같은 학생의 같은 날 조퇴·외출 발급, 학부모 확인 대기 중인 신청, 그날을 덮는 결석계."""
        notes = []
        for r in self.db.query(
                "SELECT serial, kind, time_out FROM passes WHERE (student_number = ? OR student_name = ?) "
                "AND date = ? AND status != 'cancelled' ORDER BY time_out, id", (number, name, day)):
            notes.append(f"{KIND[r['kind']]}증 {r['serial']} 발급 ({r['time_out'] or '시각 없음'})")
        for p in (self.db.get_meta("telegram_pending") or {}).values():
            if p.get("stage") == "confirm" and p["number"] == number and p["date"] == day:
                notes.append(f"{KIND[p['kind']]}증 {self.pass_when(p)} 학부모 확인 대기 중")
        notes += self.absence_duplicates(number, name, day, day)
        return notes

    def show_duplicate_warning(self, cid, head, notes, yes_label, yes_data, no_data, message_id=None):
        text = f"⚠ {head}\n이미 있는 기록:\n" + "\n".join(f"- {n}" for n in notes[:5]) + "\n그래도 계속할까요?"
        return self.show(cid, message_id, text, [[{"text": yes_label, "callback_data": yes_data},
                                                  {"text": "취소", "callback_data": no_data}]])

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

    # ---- 시간표: 학급 시간표·지금 수업·같이 비는 시간 (0.5.65, 선생님 이름·학급·과목·교시만, 학생 이름 없음) ----
    def timetable_command(self, cid, kind, args, today=None, now=None):
        today = today or date.today()
        tt = self.db.get_meta("timetable")
        if not tt:
            return self.reply(cid, "불러온 시간표가 없습니다. 업무 데스크 → 시간표에서 학교 시간표 엑셀을 불러오세요.")
        if kind == "free":
            return self.begin_free(cid, tt, args, today)
        cls = timetable.find_class(tt, args["cls"])
        if not cls:
            return self.reply(cid, f"시간표에 {args['cls']} 학급이 없습니다. 예: 2-3 시간표 (학급: {', '.join(timetable.class_list(tt)[:8])}…)")
        if kind == "class_now":
            return self.reply(cid, self.mask(self.class_now_text(tt, cls, now or datetime.now())))
        days = assist.lesson_days(args["text"], today)[:5]
        weekend = not days
        if weekend:   # 주말이라 수업일이 없으면 다음 월요일
            days = [today + timedelta(days=7 - today.weekday())]
        text = "\n".join(self.class_day_text(tt, cls, d, today) for d in days)
        if weekend:
            text = "주말이라 다음 월요일을 보여 드립니다.\n" + text
        return self.reply(cid, self.mask(text))

    def period_span(self, p):
        t = (self.db.get_meta("timetable_periods") or {}).get(str(p)) or {}
        return f" {t['start']}~{t.get('end', '')}" if t.get("start") else ""

    def class_day_text(self, tt, cls, d, today):
        swaps = self.db.get_meta("timetable_swaps") or []
        lessons = timetable.class_lessons_on(tt, cls, d, swaps)
        note = timetable.day_note(queries.school_day_events(self.db, d, d), d, timetable.grade_of(cls))
        lines = [f"📚 {fmt_day(d.isoformat(), today)} {cls} 시간표 {len({l['period'] for l in lessons})}시간"]
        if note:
            lines.append((f"⛔ 수업 없음({note['text'][:30]})" if note["level"] == "off" else f"⚠ 학사일정 확인({note['text'][:30]})"))
        lines += [f"  {l['period']}교시{self.period_span(l['period'])} {l['subj']} {l['teacher']}" + (" (교체)" if l["moved"] else "")
                  for l in lessons] or ["  없음"]
        return "\n".join(lines)

    def class_now_text(self, tt, cls, now):
        """지금(쉬는 시간이면 다음) 교시의 과목·선생님. 교시 시간은 시간표 화면에서 정한 것(meta.timetable_periods)."""
        periods = self.db.get_meta("timetable_periods") or {}
        if not periods:
            return "교시 시간이 정해져 있지 않습니다. 업무 데스크 → 시간표에서 교시 시간을 정하세요. (시간표는 '2-3 시간표' 로 볼 수 있습니다)"
        today = now.date()
        if today.weekday() >= len(tt["days"]) or today.weekday() >= 5:
            return f"오늘은 주말이라 {cls} 수업이 없습니다."
        lessons = timetable.class_lessons_on(tt, cls, today, self.db.get_meta("timetable_swaps") or [])
        note = timetable.day_note(queries.school_day_events(self.db, today, today), today, timetable.grade_of(cls))
        if note and note["level"] == "off":
            return f"⛔ 오늘은 {cls} 수업이 없는 날입니다 ({note['text'][:30]})."
        spans = []
        for p, t in periods.items():
            try:
                start = datetime.combine(today, datetime.strptime(t["start"], "%H:%M").time())
                end = datetime.combine(today, datetime.strptime(t["end"], "%H:%M").time())
            except (KeyError, ValueError, TypeError):
                continue
            spans.append((int(p), start, end))
        spans.sort()

        def who(p):
            rows = [l for l in lessons if l["period"] == p]
            return " · ".join(f"{l['subj']} {l['teacher']}" + (" (교체)" if l["moved"] else "") for l in rows) or "수업 없음"
        warn = f"\n⚠ 학사일정 확인({note['text'][:30]})" if note else ""
        for p, start, end in spans:
            if start <= now < end:
                return f"🔔 지금 {cls} {p}교시 ({start:%H:%M}~{end:%H:%M})\n  {who(p)}{warn}"
        upcoming = [x for x in spans if x[1] > now]
        if upcoming:
            p, start, end = upcoming[0]
            lead = "아직 수업 전입니다" if now < spans[0][1] else "지금은 쉬는 시간입니다"
            return f"⏳ {lead}. 다음은 {cls} {p}교시 ({start:%H:%M}~{end:%H:%M})\n  {who(p)}{warn}"
        return f"오늘 {cls} 수업이 모두 끝났습니다."

    def free_candidates(self, tt, name):
        """이름에 맞는 선생님 번호들 (이름이 정확히 같은 사람이 있으면 그들, 없으면 이름에 낱말이 든 사람)."""
        exact = [t["id"] for t in tt["teachers"] if t["name"].replace(" ", "") == name]
        return exact or [t["id"] for t in tt["teachers"] if name in t["name"].replace(" ", "")]

    def teacher_label(self, tt, tid):
        from collections import Counter
        t = tt["teachers"][tid]
        subj = Counter(l["subj"] for l in t["lessons"].values() if l.get("subj")).most_common(1)
        return t["name"] + (f" ({subj[0][0]})" if subj else "")

    def begin_free(self, cid, tt, args, today):
        if not args["names"]:
            return self.reply(cid, "같이 비는 시간을 볼 선생님 이름을 함께 보내세요. 예: 빈 시간 김철수 이영희 (나는 자동으로 들어갑니다)")
        groups = []
        for name in args["names"]:
            found = self.free_candidates(tt, name)
            if not found:
                return self.reply(cid, f"시간표에서 '{name}' 선생님을 찾지 못했습니다. 이름을 다시 확인하세요.")
            groups.append(found[:9])
        dialog = {"id": secrets.token_hex(6), "kind": "free", "step": "pick", "names": args["names"], "groups": groups,
                  "chosen": [g[0] if len(g) == 1 else None for g in groups], "next": args["next"],
                  "expires": time.monotonic() + DIALOG_TTL}
        if None not in dialog["chosen"]:
            return self.reply(cid, self.mask(self.free_text(tt, dialog["chosen"], args["next"], today)))
        self.dialog = dialog
        return self.free_ask(cid, tt, dialog, None)

    def free_ask(self, cid, tt, dialog, message_id):
        idx = dialog["chosen"].index(None)
        cands = dialog["groups"][idx]
        text = (f"'{dialog['names'][idx]}' 이(가) 여러 명입니다. 번호를 보내거나 단추를 누르세요.\n"
                + "\n".join(f"{n}. {self.teacher_label(tt, tid)}" for n, tid in enumerate(cands, 1)))
        buttons = [[{"text": f"{n}", "callback_data": f"dlg:{dialog['id']}:fp:{n}"} for n in range(1, len(cands) + 1)],
                   [{"text": "취소", "callback_data": f"dlg:{dialog['id']}:cancel"}]]
        return self.show(cid, message_id, text, buttons)

    def free_pick(self, cid, dialog, choice, message_id=None):
        tt = self.db.get_meta("timetable")
        if not tt or dialog["step"] != "pick" or None not in dialog["chosen"]:
            return self.reply(cid, "이미 처리했거나 오래된 선택입니다. 다시 보내 주세요.")
        idx = dialog["chosen"].index(None)
        cands = dialog["groups"][idx]
        if not choice.isdigit() or not 1 <= int(choice) <= len(cands):
            return self.reply(cid, f"1~{len(cands)} 번호를 보내거나 단추를 눌러 주세요. 그만하려면 '취소'.")
        dialog["chosen"][idx] = cands[int(choice) - 1]
        if None in dialog["chosen"]:
            return self.free_ask(cid, tt, dialog, message_id)
        self.dialog = None
        return self.show(cid, message_id, self.mask(self.free_text(tt, dialog["chosen"], dialog["next"], date.today())), [])

    def free_text(self, tt, tids, nxt, today):
        """'나' 와 고른 선생님들이 모두 비는 교시 (이번 주는 남은 날만, 다음 주는 월~)."""
        me = self.db.get_meta("timetable_me")
        notes = []
        if me is not None and 0 <= me < len(tt["teachers"]):
            tids = [me] + [t for t in tids if t != me]
        else:
            notes.append("(내 이름이 시간표에 정해져 있지 않아 '나' 는 빼고 계산했습니다)")
        tids = list(dict.fromkeys(tids))
        mon = timetable.monday(today)
        if nxt or today.weekday() >= 5:
            mon += timedelta(days=7)
            if not nxt:
                notes.append("(이번 주는 끝나서 다음 주로 계산했습니다)")
        evs = queries.school_day_events(self.db, mon, mon + timedelta(days=6))
        periods = self.db.get_meta("timetable_periods") or {}
        cells = [c for c in timetable.common_free(tt, tids, mon, self.db.get_meta("timetable_swaps") or [], evs)
                 if c["date"] >= today.isoformat() and not (c["note"] and c["note"]["level"] == "off")]
        names = {t: ("나" if t == me else tt["teachers"][t]["name"]) for t in tids}

        def span(p):
            t = periods.get(str(p)) or {}
            return f" {t['start']}~{t.get('end', '')}" if t.get("start") else ""
        week = f"{'다음' if mon > timetable.monday(today) else '이번'} 주 {mon.month}/{mon.day}~{(mon + timedelta(days=6)).month}/{(mon + timedelta(days=6)).day}"
        lines = [f"🕒 같이 비는 시간 · {' · '.join(names[t] for t in tids)} · {week}"]
        allfree = [c for c in cells if len(c["free"]) == len(tids)]
        if allfree:
            by_day = {}
            for c in allfree:
                by_day.setdefault((c["date"], c["day"], bool(c["note"])), []).append(c)
            for (d, day, maybe), cs in by_day.items():
                dd = date.fromisoformat(d)
                lines.append(f"  {day}({dd.month}/{dd.day}) " + ", ".join(f"{c['period']}교시{span(c['period'])}" for c in cs)
                             + (" ⚠ 학사일정 확인" if maybe else ""))
        else:
            best = timetable.rank_free(cells)[:10]
            lines.append("모두 비는 시간이 없습니다." + (" 많이 비는 순:" if best else ""))
            for c in best:
                dd = date.fromisoformat(c["date"])
                busy = ", ".join(names[b["tid"]] + (f"({' '.join(x for x in (b.get('cls'), b.get('subj')) if x)})" if b.get("cls") or b.get("subj") else "")
                                 for b in c["busy_lessons"])
                lines.append(f"  {c['day']}({dd.month}/{dd.day}) {c['period']}교시{span(c['period'])} · "
                             f"{c['total']}명 중 {c['free_n']}명 빔 · 수업: {busy}"
                             + (" ⚠ 학사일정 확인" if c["note"] else ""))
        near = [c for c in cells if allfree and len(tids) > 2 and len(c["free"]) == len(tids) - 1]
        if near:
            lines.append("거의 다 비는 시간 (한 명만 수업):")
            for c in near[:5]:
                dd = date.fromisoformat(c["date"])
                lines.append(f"  {c['day']}({dd.month}/{dd.day}) {c['period']}교시{span(c['period'])} · 수업: "
                             + ", ".join(names[t] for t in c["busy"]))
        return "\n".join(lines + notes)

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
            if action.startswith("fp:") and dialog["kind"] == "free" and dialog["step"] == "pick":
                return self.free_pick(cid, dialog, action[3:], msg.get("message_id"))
            if action.startswith("type:") and dialog["kind"] == "absence" and dialog["step"] == "type":
                n = action[5:]
                if n not in ("0", "1", "2", "3"):
                    return self.reply(cid, "결석 종류 단추를 다시 골라 주세요.")
                dialog.update(step="date", type=list(absence.TYPE_WORDS)[int(n)])
                today = date.today()
                return self.show_calendar(cid, dialog, msg.get("message_id"), today.year, today.month)
            if dialog["kind"] == "event":
                event_result = self.event_action(cid, dialog, action, msg.get("message_id"))
                if event_result is not None:
                    return event_result
            calendar_result = self.calendar_action(cid, dialog, action, msg.get("message_id"))
            if calendar_result is not None:
                return calendar_result
            if action == "now" and dialog["kind"] in KIND and dialog["step"] == "time":
                return self.start_pass(cid, dialog["kind"], [str(dialog["number"])], day=dialog.get("pass_day"))
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
        if kind == "mn":   # 메뉴 단추: 정해 둔 이름만 (같은 뜻의 글을 보낸 것처럼 처리)
            return self.menu_button(cid, rest, msg.get("message_id"))
        if kind == "rg" and rest.isdigit():
            return self.register_item(cid, int(rest))
        if kind == "o" and rest.isdigit():
            return self.send_original(cid, int(rest))
        if kind == "f" and rest.isdigit():
            return self.send_item_file(cid, int(rest))
        if kind == "dk":
            pid, _, _ = rest.partition(":")
            pending = self.db.get_meta("telegram_pending") or {}
            p = pending.get(pid)
            students = [s for s in self.db.students() if p and s["number"] == p["number"]]
            if not p or p.get("stage") != "dup" or len(students) != 1:
                return self.reply(cid, "이미 처리했거나 오래된 요청입니다.")
            p.pop("stage")
            self.db.set_meta("telegram_pending", pending)
            buttons = [[{"text": r, "callback_data": f"pr:{pid}:{i}"}] for i, r in enumerate(REASONS[p["kind"]])]
            buttons.append([{"text": "사유 없이 발급", "callback_data": f"pr:{pid}:-"},
                            {"text": "취소", "callback_data": f"px:{pid}"}])
            return self.show(cid, msg.get("message_id"),
                             f"🎫 {p['number']}번 {self.student_name(students[0]['name'])} {KIND[p['kind']]}증 {self.pass_when(p)}\n"
                             "사유를 고르면 발급합니다.", buttons)
        if kind == "evu":
            eid, _, token = rest.partition(":")
            if eid.isdigit():
                return self.undo_event(cid, int(eid), msg.get("message_id"), token)
        if kind in ("up", "ua", "uf", "uc"):
            return self.update_button(cid, kind, rest, msg.get("message_id"))
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
    def start_pass(self, cid, kind, args, now=None, day=None):
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
                        "date": (day or (now or datetime.now()).date()).isoformat()}
        self.db.set_meta("telegram_pending", dict(list(pending.items())[-20:]))   # 최근 20개만 (학부모 확인 대기 포함)
        when = self.pass_when(pending[pid])
        notes = self.pass_duplicates(kind, number, students[0]["name"], pending[pid]["date"], time_out)
        if notes:   # 신청할 때 이미 있는 기록이 있으면 먼저 알리고, [그래도 신청] 을 눌러야 사유 고르기로 감
            pending[pid]["stage"] = "dup"
            self.db.set_meta("telegram_pending", dict(list(pending.items())[-20:]))
            self.show_duplicate_warning(cid, f"{number}번 {self.student_name(students[0]['name'])} {word}증 {when} — 중복 신청일 수 있습니다",
                                        notes, "그래도 신청", f"dk:{pid}:1", f"px:{pid}")
            return "중복 확인"
        buttons = [[{"text": r, "callback_data": f"pr:{pid}:{i}"}] for i, r in enumerate(REASONS[kind])]
        buttons.append([{"text": "사유 없이 발급", "callback_data": f"pr:{pid}:-"},
                        {"text": "취소", "callback_data": f"px:{pid}"}])
        text = f"🎫 {number}번 {self.student_name(students[0]['name'])} {word}증 {when}\n사유를 고르면 발급합니다."
        self.send(text, cid, buttons)
        return text

    @staticmethod
    def pass_when(p):
        """'13:30' 또는 '13:30~14:30', 오늘이 아니면 앞에 날짜 ('11/3(화) 13:30')."""
        when = p["time_out"] + (f"~{p['time_return']}" if p["time_return"] else "")
        return when if p["date"] == date.today().isoformat() else f"{fmt_day(p['date'], date.today())} {when}"

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
        who = f"{p['number']}번 {self.student_name(students[0]['name'])}"
        when = self.pass_when(p)
        if stage == "reason":
            if p.get("stage") == "dup":
                return "먼저 중복 확인에서 [그래도 신청]을 눌러 주세요."
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
                f"{p['number']}번 {KIND[p['kind']]} {self.pass_when(p)}" for p in waiting[:10]))
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

    # ---- 통화·문자 기록 파일 받기 (0.5.84, 사용자 결정 2026-11-10 예외: 기본 끔, 선생님 채팅만, 처리 후 삭제, 답은 건수만) ----
    def handle_calllog_file(self, msg):
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if chat.get("type") != "private" or cid is None or cid != self.chat_id:
            return None   # 연결된 선생님이 아닌 사람의 파일은 받지도, 답하지도 않음
        if not self.config["telegram_calllog"]:
            return self.reply(cid, "파일은 받지 않았습니다. 통화·문자 기록 파일을 받으려면 업무 데스크 설정 → 텔레그램에서 "
                                   "'통화·문자 기록 파일을 텔레그램으로 받기'를 켜세요.")
        doc = msg["document"]
        try:
            if (doc.get("file_size") or 0) > calllog.MAX_TOTAL:
                raise ValueError("파일이 너무 큽니다 (14MB 이하)")
            info = self.api("getFile", {"file_id": doc.get("file_id")}, timeout=30)
            raw = self.download_file(info["file_path"])   # 메모리에서만 읽음 (디스크에 쓰지 않음)
            r = calllog.receive_file(self.db, raw)
            extra = [f"학부모 번호와 맞지 않아 버림 {r['discarded']}건"]
            if r["already"]:
                extra.append(f"이미 저장한 것 {r['already']}건")
            if r["old"]:
                extra.append(f"{calllog.TELEGRAM_SINCE_DAYS}일보다 오래된 것 {r['old']}건")
            text = (f"📱 통화·문자 기록에서 상담 초안 {r['drafts']}건을 만들었습니다 ({', '.join(extra)}).\n"
                    f"업무 데스크 → 학급 → 상담 화면에서 확인해 저장하세요. 확인하지 않은 초안은 {calllog.PENDING_DAYS}일 뒤 지워집니다.")
        except ValueError as e:   # 파일 형식 문제 (안내 글에는 파일 내용·이름이 들어가지 않음)
            text = f"파일을 읽지 못했습니다: {e}"
        except Exception as e:
            self.db.log(f"텔레그램 통화·문자 파일 받기 오류: {type(e).__name__}", "error")
            text = "파일을 받지 못했습니다. 잠시 뒤 다시 보내거나 업무 데스크에서 직접 불러오세요."
        try:
            self.api("deleteMessage", {"chat_id": cid, "message_id": msg.get("message_id")}, timeout=15)
        except Exception:
            text += "\n⚠ 보낸 파일 메시지를 지우지 못했습니다. 텔레그램 대화에서 직접 지워 주세요."
        return self.reply(cid, text)

    def download_file(self, file_path):
        url = f"{self.config['telegram_api_base'].rstrip('/')}/file/bot{self.token}/{file_path}"
        with urllib.request.urlopen(url, timeout=60) as r:
            raw = r.read(calllog.MAX_TOTAL + 1)
        if len(raw) > calllog.MAX_TOTAL:
            raise ValueError("파일이 너무 큽니다 (14MB 이하)")
        return raw

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
                    + f"텔레그램: {self.status}" + (f" · 마지막 확인 {self.last_poll}" if self.last_poll else "")
                    + (f" · 마지막 처리 {self.last_handled}" if self.last_handled else "")
                    + (f"\n최근 오류: {self.last_error}" if self.last_error else "") + "\n"
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
            if self.collector.lock.locked():
                return "이미 수집 중입니다. 잠시 뒤 다시 보내 주세요."
            if not self.sync and self.chat_id:   # 수집은 몇 분 걸릴 수 있어 폴링을 막지 않게 뒤에서
                threading.Thread(target=self._refresh, args=(self.chat_id,), name="telegram-refresh", daemon=True).start()
                return "🔄 지금 수집합니다. 끝나면 알려 드립니다. (그동안에도 다른 명령은 받습니다)"
            return self._refresh_text()
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
        """텔레그램으로 나가는 글의 학생 이름: 설정 '텔레그램에서 학생 이름 가리기' 를 켰을 때만 가림.
        0.5.93 선생님 결정(2026-11-11): 선생님 본인 채팅(연결된 chat_id 하나)이라 기본은 그대로 씀."""
        if not self.config["telegram_mask_names"]:
            return text or ""
        return self.mask_always(text)

    def mask_always(self, text):
        """설정과 관계없이 가림 — 기록(db.log)·오류 문구처럼 텔레그램 밖에도 남는 글."""
        return ai.mask_names(text or "", [s["name"] for s in self.db.students()])

    def student_name(self, name):
        """증 발급 답장의 학생 이름 (설정을 켜면 '김○○')."""
        return masked(name) if self.config["telegram_mask_names"] else (name or "").strip()

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

    def send_collect_photos(self, stats):
        """새 쿨메신저 쪽지에 든 사진을 텔레그램으로도 보냄 (사용자 요청 2026-11-02, 설정 telegram_photos).
        쪽지 안 그림·허용한 폴더의 PC 파일만 (인터넷 주소 사진은 읽지 않고 개수만 알림). 한 번에 최대 10장."""
        ids = list(stats.get("new_ids") or [])
        if not (ids and self.chat_id and self.read_image and self.config["telegram_photos"]):
            return 0
        rows = self.db.query(
            f"SELECT id, title, images FROM items WHERE source = 'cool' AND images IS NOT NULL AND id IN ({','.join('?' * len(ids))}) "
            "ORDER BY received_at", ids)
        sent = left = web = 0
        for r in rows:
            try:
                srcs = json.loads(r["images"] or "[]")
            except ValueError:
                continue
            for i, src in enumerate(srcs, 1):
                if src.lower().startswith(("http://", "https://")):
                    web += 1
                    continue
                if sent >= PHOTO_LIMIT:
                    left += 1
                    continue
                try:
                    found = self.read_image(src)
                    if not found:
                        continue
                    data, ctype = found
                    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(ctype)
                    caption = f"📷 {cut(self.mask(r['title']), 50)} ({i}/{len(srcs)})"
                    if ext and len(data) <= 10 * 1024 * 1024:
                        self.send_file(self.chat_id, f"photo{ext}", data, caption=caption, photo=True)
                    else:   # gif·bmp·10MB 넘는 그림은 파일로
                        self.send_file(self.chat_id, f"photo{ext or '.bin'}", data, caption=caption)
                    sent += 1
                except (urllib.error.URLError, OSError, RuntimeError):
                    left += 1
        if left or web:
            parts = ([f"사진 {left}장" ] if left else []) + ([f"인터넷 주소 사진 {web}장"] if web else [])
            self.send("📷 " + "·".join(parts) + "은 텔레그램으로 보내지 않았습니다. 업무 데스크 수신함에서 보세요.")
        return sent

    def send_collect_texts(self, stats):
        """새 쿨메신저 쪽지의 원문을 텔레그램으로도 보냄 (선생님이 설정 telegram_cool_text 를 켠 경우만, 0.5.61).
        원문은 가리지 않고 그대로 (원문 규칙). 한 번에 최대 5건, 긴 글은 나눠 보냄."""
        ids = list(stats.get("new_ids") or [])
        if not (ids and self.chat_id and self.config["telegram_cool_text"]):
            return 0
        rows = self.db.query(
            f"SELECT id, title, body, received_at FROM items WHERE source = 'cool' AND id IN ({','.join('?' * len(ids))}) "
            "ORDER BY received_at", ids)
        sent = 0
        for r in rows[:5]:
            body = (r["body"] or "").strip()
            if not body:
                continue
            head = f"📨 쿨메신저 쪽지 · {self.mask(r['title'])}"
            for k in range(0, len(body), 3500):
                chunk = body[k:k + 3500]
                self.send((head + "\n\n" if k == 0 else "") + chunk)
            sent += 1
        if len(rows) > 5:
            self.send(f"📨 쪽지 {len(rows) - 5}건의 원문은 업무 데스크 수신함에서 보세요.")
        return sent

    def notify_collect(self, stats, reason):
        try:
            self.send_collect_photos(stats)
        except Exception:
            pass   # 사진 전달이 실패해도 알림은 보냄
        try:
            self.send_collect_texts(stats)
        except Exception:
            pass   # 원문 전달이 실패해도 알림은 보냄
        if reason == "telegram":
            return  # 명령에 대한 답장으로 이미 보냄
        msg = self.collect_message(stats)
        if msg and self.chat_id:
            ids = self.db.get_meta("telegram_refs", []) if stats.get("new_ids") else []
            buttons = self.item_buttons(ids, ids) if self.config["telegram_detail"] != "counts" else None
            self.send(msg, buttons=buttons)

    def _refresh_text(self):
        stats = self.collector.run("telegram")
        if stats.get("busy"):
            return "이미 수집 중입니다. 잠시 뒤 다시 보내 주세요."
        return self.collect_message(stats) or "새로 들어온 자료가 없습니다."

    def _refresh(self, cid):
        try:
            text = self._refresh_text()
        except Exception as e:   # 수집 오류가 폴링·알림을 멈추지 않게
            self.note_error(e, "수집")
            text = "수집하다가 오류가 났습니다. 업무 데스크의 '기록'에서 원인을 볼 수 있습니다."
        try:
            self.send(text, cid)
        except Exception as e:
            self.note_error(e, "수집 결과 보내기")

    def update_buttons(self, version):
        return [[{"text": "⬆ 지금 업데이트", "callback_data": f"up:{version}"}]] if self.updates else None

    def send_update_offer(self, cid):
        """'업데이트' 명령: 새 버전을 확인해 있으면 [지금 업데이트] 단추."""
        if not self.updates:
            return self.reply(cid, "업데이트 기능을 쓸 수 없습니다.")
        st = self.updates.check()
        if st["error"]:
            return self.reply(cid, st["error"])
        if not st["portable"]:
            return self.reply(cid, f"지금 {st['current']} · 개발용 폴더는 텔레그램에서 업데이트할 수 없습니다 (git 으로 받으세요).")
        if not st["available"]:
            return self.reply(cid, f"✅ 최신 버전입니다 ({st['current']}).")
        text = f"⬆ 새 버전 {st['latest']} 이 있습니다 (지금 {st['current']})."
        self.send(text, cid, self.update_buttons(st["latest"]))
        return text

    def update_button(self, cid, kind, version, message_id):
        """up: 확인 묻기 → ua: 바꾸기 → (수집·AI 요약 중이면) uf: 중단하고 바꾸기. uc: 취소."""
        if not self.updates:
            return self.show(cid, message_id, "업데이트 기능을 쓸 수 없습니다.", [])
        if kind == "uc":
            return self.show(cid, message_id, "업데이트를 취소했습니다. 바뀐 것은 없습니다.", [])
        st = self.updates.state()
        if not (st["available"] and st["portable"]) or str(st["latest"]) != version:
            return self.show(cid, message_id, "새 버전 정보가 바뀌었거나 이미 처리했습니다. '업데이트' 를 다시 보내 확인하세요.", [])
        if kind == "up":
            return self.show(cid, message_id,
                             f"⬆ {st['current']} → {version} 으로 업데이트할까요?\n업무 데스크가 꺼졌다가 1~2분 뒤 다시 켜지며, 그동안 텔레그램도 멈춥니다. "
                             "다시 켜지면 알려 드립니다. 자료·설정은 그대로입니다.",
                             [[{"text": "업데이트 시작", "callback_data": f"ua:{version}"},
                               {"text": "취소", "callback_data": "uc:0"}]])
        self.show(cid, message_id, f"⬆ {st['current']} → {version} 업데이트를 시작합니다. 다시 켜지면 알려 드립니다.", [])
        self.db.set_meta("telegram_update_pending", version)
        try:
            self.updates.apply(kind == "uf")
        except Exception as e:
            self.db.set_meta("telegram_update_pending", None)
            status = getattr(e, "status", None)
            message = getattr(e, "message", None) or self.describe_error(e)   # ApiError 의 글은 고정 문구, 그 밖은 예외 이름만
            buttons = ([[{"text": "중단하고 업데이트", "callback_data": f"uf:{version}"},
                         {"text": "취소", "callback_data": "uc:0"}]] if status == 409 else None)
            return self.reply(cid, f"⚠ 업데이트를 시작하지 못했습니다: {message}") if not buttons else \
                self.show(cid, None, f"⚠ {message}\n끝난 뒤 다시 보내거나, 중단해도 되면 아래 단추를 누르세요.", buttons)
        return "업데이트 시작"

    def announce_update_result(self):
        """텔레그램으로 시작한 업데이트가 끝나 다시 켜졌을 때 알림 (밖에서 보고 있어도 끝난 줄 알도록)."""
        target = self.db.get_meta("telegram_update_pending")
        if not target or not self.chat_id:
            return False
        self.db.set_meta("telegram_update_pending", None)
        if VERSION == target:
            return self.send(f"✅ 업무 데스크가 {VERSION} 으로 업데이트되어 다시 켜졌습니다.")
        return self.send(f"⚠ 업데이트가 끝나지 않은 것 같습니다 (목표 {target}, 지금 {VERSION}). PC 의 업데이트 창을 확인하세요.")

    def notify_update(self, info):
        """새 버전이 나왔다고 텔레그램으로 한 번 알림 (같은 버전은 다시 보내지 않음). 바꾸는 것은 화면에서 직접 눌러야 함."""
        version = str(info.get("version", ""))[:20]
        if not version or not self.chat_id or self.db.get_meta("telegram_update_notified") == version:
            return False
        sent = self.send(f"⬆ 업무 데스크 새 버전 {version} 이 나왔습니다 (지금 {VERSION}).\n"
                         "직접 누르기 전에는 바뀌지 않습니다. 업무 데스크 PC 가 켜져 있을 때만 단추가 응답합니다.",
                         buttons=self.update_buttons(version))
        if sent:
            self.db.set_meta("telegram_update_notified", version)
        return sent

    # ---- 폴링 ----
    def alive(self):
        return bool(self.thread and self.thread.is_alive())

    def describe_error(self, e):
        """오류를 화면·텔레그램·기록에 내보내도 되는 한 줄로. 연결 계열(메시지가 안전한 것)만 메시지를 싣고, 나머지는 예외 이름만
        (사용자가 보낸 글·학생 이름·토큰이 든 URL 이 메시지에 섞일 수 있어서). 싣는 메시지도 봇 토큰 모양과 학생 이름은 지움."""
        name = type(e).__name__
        if not isinstance(e, SAFE_ERRORS):
            return name
        text = str(getattr(e, "reason", None) or e)
        for secret in (self.token, ):
            if secret:
                text = text.replace(secret, "***")
        text = P_BOT_TOKEN.sub("/bot***", text)
        text = P_TOKEN_LIKE.sub("***", text)
        try:
            text = self.mask_always(text)   # 기록·상태에도 남으므로 설정과 관계없이 가림
        except Exception:
            return name
        text = " ".join(text.split())[:80]
        return f"{name}: {text}" if text else name

    def note_error(self, e, where="폴링"):
        """마지막 오류를 한 줄로 남기고 같은 오류가 이어져도 기록은 처음 한 번만."""
        line = f"{where}: {self.describe_error(e)}"
        if line != (self.last_error or "")[9:]:
            try:
                self.db.log(f"텔레그램 {line}", "warn")
            except Exception:
                pass   # 기록하지 못해도(DB 잠김 등) 폴링이 멈추지 않게
        self.last_error = f"{datetime.now():%H:%M:%S} {line}"

    @staticmethod
    def update_chat(u):
        msg = u.get("message") or (u.get("callback_query") or {}).get("message") or {}
        return (msg.get("chat") or {}).get("id")

    @staticmethod
    def is_stale(u, backlog, now):
        """PC 가 꺼져·끊겨 있던 동안 쌓인 명령인가. 글은 보낸 시각(date)이 10분 넘게 지났으면, 단추는 보낸 시각을 알 수 없어
        오래 끊겼다 돌아온 첫 묶음 안의 것이면 실행하지 않음 (조퇴·결석계 발급·업데이트가 나중에 뜻하지 않게 실행되지 않게)."""
        if u.get("callback_query"):
            return backlog
        date_ = (u.get("message") or {}).get("date")
        return bool(date_) and now - date_ > STALE_AFTER

    def saved_offset(self, token):
        """다시 켠 뒤에도 이미 처리한 명령·단추를 또 처리하지 않도록 저장해 둔 다음 번호 (같은 봇 토큰일 때만)."""
        saved = self.db.get_meta("telegram_offset") or {}
        return saved.get("offset") if saved.get("key") == hashlib.sha256(token.encode()).hexdigest()[:12] else None

    def save_offset(self, token, offset):
        self.db.set_meta("telegram_offset", {"key": hashlib.sha256(token.encode()).hexdigest()[:12], "offset": offset})

    def loop(self):
        """폴링 루프. 어떤 오류가 나도 스레드가 죽지 않게 한 번 더 감쌈 (예전에는 예상 못 한 오류 하나로 조용히 영영 멈춤)."""
        while not self.stop_event.is_set():
            try:
                self._loop()
                return
            except Exception as e:   # _loop 안에서 못 잡은 오류: 기록하고 잠시 뒤 처음부터
                self.status = f"오류: {type(e).__name__} (다시 시도 중)"   # 이름만 (메시지는 최근 오류에서 정제해 보임)
                self.note_error(e, "폴링 중단")
                self.stop_event.wait(10)

    def _loop(self):
        offset, backoff, menu_set, last_token = None, POLL_BACKOFF, False, None
        while not self.stop_event.is_set():
            if not self.token:
                self.status = "토큰 없음"
                self.stop_event.wait(10)
                continue
            if self.token != last_token:  # 설정에서 토큰을 바꾼 경우
                offset, menu_set, last_token = self.saved_offset(self.token), False, self.token
            try:
                if not menu_set:
                    self.api("setMyCommands", {"commands": BOT_MENU}, timeout=15)
                    menu_set = True
                updates = self.api("getUpdates", {"timeout": 25, "offset": offset,
                                                  "allowed_updates": ["message", "callback_query"]}, timeout=35)
                self.status = "연결됨" if self.chat_id else "연결 대기 (코드를 보내세요)"
                backoff, self.conflicts = POLL_BACKOFF, 0
                self.last_poll = datetime.now().strftime("%H:%M:%S")
                now = time.time()
                backlog = self.polled_at is None or now - self.polled_at > STALE_AFTER   # 켠 뒤 첫 묶음이거나 오래 끊겼다 돌아옴
                self.polled_at = now
                skipped = 0
                for u in updates:
                    offset = u["update_id"] + 1
                    self.save_offset(self.token, offset)   # 처리하기 전에 저장: 업데이트로 앱이 꺼져도 같은 단추가 다시 오지 않음
                    if self.is_stale(u, backlog, now):
                        skipped += self.update_chat(u) == self.chat_id
                        continue
                    try:
                        self.handle_update(u)
                        self.last_handled = datetime.now().strftime("%H:%M:%S")
                    except Exception as e:
                        self.db.log(f"텔레그램 명령 처리 오류: {self.describe_error(e)}", "error")
                        self.note_error(e, "명령 처리")
                        self.tell_error(u)
                if skipped:
                    self.send(f"PC 가 꺼져(또는 인터넷이 끊겨) 있던 동안 받은 명령·단추 {skipped}개는 실행하지 않았습니다. "
                              "필요하면 다시 보내 주세요.")
            except urllib.error.HTTPError as e:
                if e.code == 409:   # 같은 봇을 다른 곳(다른 폴더·PC)에서도 켜 둠: 오래 쉬면 명령에 답이 없으므로 짧게만
                    self.conflicts += 1
                    self.status = "오류: 같은 봇을 다른 곳에서도 쓰는 중 (409 Conflict)"
                    self.last_error = f"{datetime.now():%H:%M:%S} 같은 봇 토큰을 다른 곳에서도 쓰는 중 (409 Conflict)"
                    if self.conflicts in (1, 30):
                        self.db.log("텔레그램 409 Conflict: 같은 봇 토큰을 쓰는 업무 데스크가 다른 곳에서도 켜져 있습니다. "
                                    "한 곳만 남기세요 (개발 폴더에는 토큰을 넣지 않습니다)", "warn")
                    self.stop_event.wait(8)
                    continue
                self.status = f"오류: HTTP {e.code} {str(e.reason)[:40]}"   # 텔레그램의 상태 문구(Bad Request 등)
                self.note_error(e)
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, 300)
            except Exception as e:   # 연결 오류뿐 아니라 응답이 중간에 끊김(IncompleteRead)·DB 오류 등 모두: 스레드가 죽지 않게
                self.status = f"오류: {self.describe_error(e)}"
                self.note_error(e)
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
        if self.thread and self.thread.is_alive():
            return
        try:
            self.announce_update_result()
        except Exception:
            pass   # 알림 실패가 켜는 것을 막지 않음
        self.thread = threading.Thread(target=self.loop, name="telegram", daemon=True)
        self.thread.start()
        if not (self.monitor and self.monitor.is_alive()):
            self.monitor = threading.Thread(target=self.watch, name="telegram-watch", daemon=True)
            self.monitor.start()

    def watch(self):
        """폴링 스레드가 (어떤 이유로든) 끝났으면 다시 켬. loop 가 오류를 다 잡으므로 안전망."""
        while not self.stop_event.wait(WATCH_EVERY):
            if self.token and not self.alive():
                self.db.log("텔레그램 수신이 멈춰 있어 다시 시작합니다", "warn")
                self.thread = threading.Thread(target=self.loop, name="telegram", daemon=True)
                self.thread.start()

    def stop(self):
        self.stop_event.set()
