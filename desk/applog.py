"""사용 기록 파일 (0.6.15, 선생님 요청 2026-11-20 "모든 로그를 저장해서 앱 전반적인 사용성 점검").

앱이 무엇을 언제 했는지(화면에서 연 메뉴·누른 단추, 텔레그램 명령·보낸 글, 쿨메신저 수집, AI 호출, 오류)를
이 PC 의 data/logs/desk.log 에 한 줄씩 남깁니다. 선생님이 텔레그램 '로그' 로 받아 Claude 에게 점검을 맡기는 용도라,
**내용은 남기지 않고 동작만** 남깁니다.

- 남기는 것: 시각, 종류, 기능 이름(명령 낱말·단추 이름·화면 이름), 걸린 시간, 성공/실패, 글자 수, 오류 종류와 코드 위치.
- 남기지 않는 것: 쪽지·공문 내용과 제목, 조퇴·외출 사유, 텔레그램에 보낸 글 내용, AI 에 보낸 글·AI 답.
  같은 글이 두 번 나갔는지는 글 내용 대신 짧은 지문(h=…, 글의 해시 앞 8자)으로만 알 수 있게 함.
- 학생 이름(명단)·전화번호·전자우편·봇 토큰 모양은 쓸 때와 내보낼 때 두 번 가림.
- 파일 크기: 1MB 가 차면 desk.log.1 … desk.log.9 로 넘겨 모두 10MB 를 넘지 않음 (오래된 것부터 지워짐).
- setup() 전(시험·명령 줄 도구)에는 아무것도 쓰지 않음.
"""

import hashlib
import json
import logging
import logging.handlers
import platform
import re
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

LOG_FILE = "desk.log"
MAX_BYTES = 1_000_000
BACKUPS = 9
NAME_CACHE_SEC = 60
VALUE_LIMIT = 400

P_PHONE = re.compile(r"(?<!\d)0\d{1,2}[-. ]?\d{3,4}[-. ]?\d{4}(?!\d)")
P_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
P_BOT = re.compile(r"/bot[^/\s]*|\d{5,}:[A-Za-z0-9_-]{10,}")
P_KEY = re.compile(r"\b(?:sk|AIza|ya29)[A-Za-z0-9_\-.]{12,}")
P_LINE_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")

_lock = threading.Lock()
_logger = None
_dir = None
_names_fn = None
_enabled_fn = None
_names = ([], 0.0)


def setup(log_dir, names=None, enabled=None):
    """기록 파일 준비. names: 학생 이름 목록을 돌려주는 함수, enabled: 설정 '사용 기록 남기기' 를 돌려주는 함수."""
    global _logger, _dir, _names_fn, _enabled_fn
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("desk.usage")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    handler = logging.handlers.RotatingFileHandler(log_dir / LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUPS,
                                                   encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    _logger, _dir, _names_fn, _enabled_fn = logger, log_dir, names, enabled
    return logger


def shutdown():
    """시험에서 다시 쓰거나 끌 때."""
    global _logger, _dir
    if _logger:
        for h in list(_logger.handlers):
            _logger.removeHandler(h)
            h.close()
    _logger, _dir = None, None


def active():
    if _logger is None:
        return False
    try:
        return bool(_enabled_fn()) if _enabled_fn else True
    except Exception:
        return True


def student_names():
    global _names
    names, at = _names
    if time.monotonic() - at < NAME_CACHE_SEC and at:
        return names
    try:
        names = sorted({n.strip() for n in (_names_fn() if _names_fn else []) if n and len(n.strip()) >= 2},
                       key=len, reverse=True)
    except Exception:
        pass   # DB 가 잠겨 있으면 예전 목록으로
    _names = (names, time.monotonic())
    return names


def clean(text, names=None, collapse=True):
    """기록에 남겨도 되는 글로: 학생 이름·전화번호·전자우편·토큰 모양을 가림, 줄 바꿈은 한 칸으로."""
    text = str(text if text is not None else "")
    for n in (student_names() if names is None else names):
        text = text.replace(n, "○" * len(n))
    text = P_BOT.sub("/bot***", text)
    text = P_KEY.sub("***", text)
    text = P_EMAIL.sub("***@***", text)
    text = P_PHONE.sub("***-****-****", text)
    return " ".join(text.split()) if collapse else text


def fingerprint(text):
    """글 내용 대신 남기는 짧은 지문 (같은 글이 두 번 나갔는지 알아보는 용도)."""
    return hashlib.sha1(str(text or "").encode("utf-8")).hexdigest()[:8]


def _value(v):
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, float):
        return f"{v:.0f}" if v >= 10 else f"{v:.1f}"
    v = clean(v)[:VALUE_LIMIT]
    return json.dumps(v, ensure_ascii=False) if (" " in v or "=" in v or not v) else v


def event(cat, what, level="info", **fields):
    """한 줄 남기기. cat: 종류(web·tg.in·tg.out·ai·collect·server·기록·오류…), what: 무엇을."""
    if not active():
        return
    try:
        now = datetime.now()
        head = f"{now:%Y-%m-%d %H:%M:%S}.{now.microsecond // 1000:03d} {level.upper():5} {cat:8} {clean(what)[:200].replace(' | ', ' / ')}"
        kv = " ".join(f"{k}={_value(v)}" for k, v in fields.items() if v is not None and v != "")
        line = f"{head} | {kv}" if kv else head
        with _lock:
            _logger.log(logging.ERROR if level == "error" else logging.WARNING if level == "warn" else logging.INFO, line)
    except Exception:
        pass   # 기록 실패가 앱을 멈추지 않게


def exception(cat, what, exc=None, **fields):
    """오류 한 줄 + 코드 위치(파일:줄 함수)만. 오류 글은 가려서 150자까지."""
    if not active():
        return
    exc = exc or sys.exc_info()[1]
    if exc is None:
        return event(cat, what, "error", **fields)
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if Path(f.filename).name != "applog.py"][-6:]
    where = " < ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in reversed(frames))
    event(cat, what, "error", error=type(exc).__name__, msg=str(exc)[:150], at=where, **fields)


@contextmanager
def timed(cat, what, slow=None, **fields):
    """걸린 시간을 재서 끝날 때 한 줄. 오류면 오류 줄 (오류는 그대로 다시 던짐)."""
    start = time.monotonic()
    extra = {}
    try:
        yield extra
    except Exception as e:
        exception(cat, what, e, ms=(time.monotonic() - start) * 1000, **fields, **extra)
        raise
    ms = (time.monotonic() - start) * 1000
    if slow is None or ms >= slow:
        event(cat, what, "warn" if slow and ms >= slow * 5 else "info", ms=ms, **fields, **extra)


def caller(skip=()):
    """누가 불렀는지: 보내기 같은 공통 함수(skip) 바깥의 첫 함수 이름 — 같은 글이 두 번 나갈 때 어디서인지 알아봄."""
    f = sys._getframe(2)
    for _ in range(15):
        if f is None:
            return ""
        if f.f_code.co_name not in skip:
            return f.f_code.co_name
        f = f.f_back
    return ""


def install_hooks():
    """잡지 못한 오류(메인·뒤 스레드)도 기록. 원래 동작(명령 창에 출력)은 그대로."""
    old_sys, old_thread = sys.excepthook, threading.excepthook

    def sys_hook(tp, value, tb):
        exception("오류", "잡지 못한 오류", value)
        old_sys(tp, value, tb)

    def thread_hook(args):
        if args.exc_value is not None and not isinstance(args.exc_value, SystemExit):
            exception("오류", f"뒤 작업 오류 ({args.thread.name if args.thread else '?'})", args.exc_value)
        old_thread(args)

    sys.excepthook, threading.excepthook = sys_hook, thread_hook


def start_line(version, config):
    """켤 때 한 줄: 버전·환경·켜 둔 기능 (비밀값·경로·이름은 넣지 않고 켬/끔만)."""
    from .config import BOOL_KEYS
    on = sorted(k for k in BOOL_KEYS if config[k])
    extra = {
        "cool": bool(config["coolmessenger_name"] or config["coolmessenger_path"]),
        "edufine": bool(config["edufine_folder"]), "folderdoc": bool(config["folderdoc_folder"]),
        "telegram": bool(config["telegram_token"]), "neis": bool(config["neis_key"]),
    }
    event("시작", f"업무 데스크 {version}", python=platform.python_version(), os=f"{platform.system()} {platform.release()}",
          켬=",".join(on), 연결=",".join(k for k, v in extra.items() if v), ai_provider=config["ai_provider"],
          collect_times=",".join(config["collect_times"] or []))


# ---- 내보내기 (텔레그램 '로그'·화면 내려받기) ----
def files():
    """오래된 것부터."""
    if not _dir:
        return []
    out = [_dir / f"{LOG_FILE}.{i}" for i in range(BACKUPS, 0, -1)] + [_dir / LOG_FILE]
    return [p for p in out if p.exists()]


def read_lines(days=3, now=None):
    now = now or datetime.now()
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M") if days else ""
    lines = []
    with _lock:
        for p in files():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                m = P_LINE_TIME.match(line)
                if m and m.group(1) >= since:
                    lines.append(line)
    return lines


def summary(lines):
    """맨 위에 붙이는 짧은 집계: 종류별 줄 수, 오류·경고, 자주 쓴 기능, 느린 일."""
    cats, levels, uses, slow, errors = {}, {}, {}, [], {}
    for line in lines:
        head, _, kv = line.partition(" | ")
        rest = head[24:].split(None, 2)
        if len(rest) < 3:
            continue
        level, cat, what = rest
        cats[cat] = cats.get(cat, 0) + 1
        levels[level] = levels.get(level, 0) + 1
        if cat in ("web", "tg.in"):
            uses[f"{cat} {what[:50]}"] = uses.get(f"{cat} {what[:50]}", 0) + 1
        if level in ("ERROR", "WARN"):
            m = re.search(r"(?:^| )error=(\S+)", kv)
            key = f"{cat} {what[:70]}" + (f" ({m.group(1)})" if m else "")
            errors[key] = errors.get(key, 0) + 1
        m = re.search(r"(?:^| )ms=(\d+)", kv)
        if m and int(m.group(1)) >= 5000:
            slow.append((int(m.group(1)), f"{cat} {what[:60]}"))

    def top(d, n):
        return [f"  {v:5} × {k}" for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:n]]

    out = ["## 집계", "종류별 줄 수: " + ", ".join(f"{k} {v}" for k, v in sorted(cats.items())),
           "수준별: " + ", ".join(f"{k} {v}" for k, v in sorted(levels.items())),
           "", "자주 쓴 기능 (화면 web·텔레그램 tg.in, 위 20개):", *top(uses, 20),
           "", "오류·경고 (같은 것 묶음, 위 20개):", *(top(errors, 20) or ["  없음"]),
           "", "5초 넘게 걸린 일 (긴 것 위 15개):",
           *([f"  {ms / 1000:6.1f}초  {w}" for ms, w in sorted(slow, reverse=True)[:15]] or ["  없음"])]
    return out


def export_text(version, days=3, limit=40 * 1024 * 1024, now=None):
    """텔레그램·화면으로 내보낼 글. 내보낼 때 한 번 더 가림 (그 뒤에 명단에 들어온 이름도)."""
    now = now or datetime.now()
    names = student_names()
    lines = [clean(line, names, collapse=False) for line in read_lines(days, now)]
    head = [f"# 업무 데스크 사용 기록 · 버전 {version} · {now:%Y-%m-%d %H:%M} 에 만듦",
            f"# 기간: {'전체' if not days else f'최근 {days}일'} · {len(lines)}줄",
            "# 학생 이름·전화번호는 ○·*** 로 가렸고, 쪽지·공문 내용, 조퇴·외출 사유, 보낸 글 내용은 넣지 않았습니다.",
            "# 줄 모양: 시각 수준 종류 무엇을 | 칸=값 … (ms=걸린 밀리초, h=글 지문, from=부른 곳, chars=글자 수)",
            ""] + summary(lines) + ["", "## 기록", ""]
    text = "\n".join(head + lines) + "\n"
    data = text.encode("utf-8")
    if len(data) > limit:   # 너무 크면 앞(오래된 것)을 잘라 냄
        tail = data[-(limit - 64 * 1024):]
        tail = tail[tail.find(b"\n") + 1:]
        data = ("\n".join(head) + "\n...(앞부분 생략: 파일이 너무 커서 최근 것만)\n").encode("utf-8") + tail
    return data, len(lines)
