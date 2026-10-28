"""쿨메신저 쪽지 기록(.udb) 읽기.

쿨메신저는 받은 쪽지를  %LOCALAPPDATA%\\CoolMessenger\\Memo\\<이름>.udb  에 저장합니다.
(%LOCALAPPDATA% = C:\\Users\\<윈도우 사용자>\\AppData\\Local — 사용자마다 폴더 이름이 달라 자동으로 찾음)

.udb 의 내부 구조는 공개 자료가 없어, SQLite 데이터베이스라고 보고 표·열 이름과 값의 모양으로
'쪽지 표', '내용 열', '날짜 열'을 스스로 찾습니다. 찾지 못하면 오류 안내와 함께 구조 진단(내용 없음)을
보여 주므로, 그 결과로 전용 규칙을 추가하면 됩니다.

쿨메신저가 켜져 있으면 파일이 잠겨 있을 수 있어 임시 폴더에 복사한 뒤 읽기 전용으로 엽니다.
"""

import base64
import os
import re
import shutil
import sqlite3
import tempfile
import time
import zlib
from datetime import datetime, timedelta
from pathlib import Path

from .docs import html_text, tidy

SQLITE_MAGIC = b"SQLite format 3\x00"
FIRST_IMPORT_DAYS = 30      # 처음 연결할 때 가져올 기간
FIRST_IMPORT_ROWS = 200     # 날짜 열을 못 찾았을 때 처음 가져올 개수
MAX_ROWS_PER_RUN = 1000

NAME_RE = re.compile(r"^[^\\/:*?\"<>|]{1,60}$")
BODY_HINTS = ("content", "contents", "body", "memo", "msg", "message", "text", "note", "내용")
TITLE_HINTS = ("title", "subject", "제목")
DATE_HINTS = ("date", "time", "reg", "send", "recv", "write", "created", "dt", "일시", "날짜")
SENDER_HINTS = ("sender", "from", "send_name", "sendname", "username", "user_name", "writer", "name", "보낸")
SKIP_TABLE_HINTS = ("sqlite_", "config", "setting", "option", "version", "meta", "attach", "file", "group", "buddy")


class CoolError(Exception):
    """쿨메신저 파일을 읽을 수 없음 (화면에 그대로 보여 줄 한국어 메시지)."""


def memo_dir():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "CoolMessenger" / "Memo"


def memo_path(name):
    name = (name or "").strip()
    if name.lower().endswith(".udb"):
        name = name[:-4]
    if not NAME_RE.match(name):
        raise CoolError("쿨메신저 이름에 \\ / : * ? \" < > | 는 쓸 수 없습니다")
    return memo_dir() / f"{name}.udb"


def resolve(name="", path=""):
    """직접 지정한 파일 경로가 있으면 그것, 없으면 이름으로 찾은 경로."""
    path = (path or "").strip().strip('"')
    if path:
        p = Path(os.path.expandvars(os.path.expanduser(path)))
        if p.is_dir():
            raise CoolError(f"파일이 아니라 폴더입니다: {p}\n.udb 파일까지 지정하세요.")
        return p
    if not (name or "").strip():
        raise CoolError("쿨메신저 이름이나 파일 경로를 입력하세요")
    return memo_path(name)


def candidates():
    """이 PC 에서 찾은 쿨메신저 이름들 (Memo 폴더의 *.udb)."""
    d = memo_dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.udb") if p.is_file())


# ---------------------------------------------------------------- 값 해석
def parse_when(v):
    """여러 모양의 날짜 값을 datetime 으로. 모르면 None."""
    if v is None or v == "":
        return None
    if isinstance(v, bytes):
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r"\d+(\.\d+)?", v.strip())):
        s = str(v).strip()
        if isinstance(v, str) and re.fullmatch(r"\d{14}", s):         # 20261027091233
            return _safe(lambda: datetime.strptime(s, "%Y%m%d%H%M%S"))
        if isinstance(v, str) and re.fullmatch(r"\d{12}", s):         # 202610270912
            return _safe(lambda: datetime.strptime(s, "%Y%m%d%H%M"))
        if isinstance(v, str) and re.fullmatch(r"\d{8}", s):          # 20261027
            return _safe(lambda: datetime.strptime(s, "%Y%m%d"))
        x = float(s)
        if 30000 < x < 80000:            # 윈도우·델파이 날짜(1899-12-30 부터 지난 날 수)
            return datetime(1899, 12, 30) + timedelta(days=x)
        if 1e9 < x < 4e9:                # 유닉스 초
            return _safe(lambda: datetime.fromtimestamp(x))
        if 1e12 < x < 4e12:              # 유닉스 밀리초
            return _safe(lambda: datetime.fromtimestamp(x / 1000))
        if 1e16 < x < 4e17:              # 윈도우 FILETIME (100ns, 1601년부터)
            return _safe(lambda: datetime(1601, 1, 1) + timedelta(microseconds=x / 10) + _local_offset())
        return None
    if isinstance(v, str):
        m = re.match(r"\s*(\d{4})[-./년 ]\s*(\d{1,2})[-./월 ]\s*(\d{1,2})(?:\D*?(\d{1,2}):(\d{2})(?::(\d{2}))?)?", v)
        if m:
            y, mo, d, h, mi, se = (int(g) if g else 0 for g in m.groups())
            if "오후" in v and h < 12:
                h += 12
            return _safe(lambda: datetime(y, mo, d, h, mi, se))
    return None


def _safe(fn):
    try:
        return fn()
    except (ValueError, OverflowError, OSError):
        return None


def _local_offset():
    return timedelta(seconds=-time.timezone if time.localtime().tm_isdst == 0 else -time.altzone)


def _decode(b):
    """바이트를 글자로: 글자 사이에 0 바이트가 많으면 UTF-16(윈도우 유니코드), 아니면 UTF-8·CP949."""
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return b.decode("utf-16", "replace")
    if len(b) >= 4 and b[1::2].count(0) * 3 > len(b) // 2:
        return b.decode("utf-16-le", "replace")
    for enc in ("utf-8", "cp949", "utf-16-le"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


COMP_PREFIX = "{COMP}"


def unpack_comp(s):
    """쿨메신저가 압축해 저장한 내용: '{COMP}' + base64(zlib(UTF-16 HTML)). 풀지 못하면 None."""
    try:
        raw = base64.b64decode(s.strip()[len(COMP_PREFIX):].strip() + "==", validate=False)
        return _decode(zlib.decompress(raw))
    except (ValueError, zlib.error):
        return None


def clean_body(v):
    """쪽지 내용 정리: 바이트·압축({COMP})·RTF·HTML 도 글자로."""
    if v is None:
        return ""
    if isinstance(v, bytes):
        v = _decode(v)
        if v is None:
            return ""
    s = str(v)
    if s.lstrip().startswith(COMP_PREFIX):
        s = unpack_comp(s)
        if s is None:
            return ""  # 깨진 압축: 알 수 없는 글자를 수신함에 넣지 않음
    if s.lstrip().startswith("{\\rtf"):
        s = rtf_text(s)
    elif re.search(r"<(html|body|p|div|br|span|font|img|table)\b", s, re.I):
        s = html_text(s)
    return tidy(s)


def rtf_text(s):
    """RTF 에서 글자만 (\\'xx 는 CP949 2바이트로 이어 붙임, \\uN 은 유니코드)."""
    out, raw = [], bytearray()

    def flush():
        if raw:
            out.append(raw.decode("cp949", "replace"))
            raw.clear()

    # 글꼴표·색표 같은 머리 부분은 건너뜀
    s = re.sub(r"\{\\(fonttbl|colortbl|stylesheet|\*)[^{}]*(\{[^{}]*\}[^{}]*)*\}", "", s)
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            m = re.match(r"\\'([0-9a-fA-F]{2})", s[i:])
            if m:
                raw.append(int(m.group(1), 16))
                i += 4
                continue
            flush()
            m = re.match(r"\\u(-?\d+)\??", s[i:])
            if m:
                out.append(chr(int(m.group(1)) % 65536))
                i += len(m.group(0))
                continue
            m = re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", s[i:])
            if m:
                if m.group(1) in ("par", "line"):
                    out.append("\n")
                elif m.group(1) == "tab":
                    out.append("\t")
                i += len(m.group(0))
                continue
            if i + 1 < len(s) and s[i + 1] in "\\{}":
                out.append(s[i + 1])
                i += 2
                continue
            i += 1
            continue
        flush()
        if c not in "{}\r\n":
            out.append(c)
        i += 1
    flush()
    return "".join(out)


# ---------------------------------------------------------------- 표 고르기
def _hint(name, hints):
    n = name.lower()
    return any(h in n for h in hints)


def detect(conn):
    """쪽지가 들어 있는 표와 열을 찾음. {'table', 'body', 'title', 'date', 'sender', 'rows'} 또는 None."""
    best = None
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    for t in tables:
        if _hint(t, SKIP_TABLE_HINTS):
            continue
        q = _q(t)
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({q})")]
        rows = conn.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]
        if not rows or not cols:
            continue
        sample = conn.execute(f"SELECT * FROM {q} ORDER BY rowid DESC LIMIT 50").fetchall()
        text_len, date_ok = {}, {}
        for ci, c in enumerate(cols):
            vals = [r[ci] for r in sample]
            texts = [clean_body(v) for v in vals if isinstance(v, (str, bytes))]
            text_len[c] = sum(len(x) for x in texts) / max(len(vals), 1)
            date_ok[c] = sum(1 for v in vals if parse_when(v)) / max(len(vals), 1)
        # 내용 열: 이름이 맞으면 우선, 아니면 가장 긴 글
        body = max(cols, key=lambda c: (text_len[c] > 0 and _hint(c, BODY_HINTS), text_len[c]))
        if text_len[body] < 2:
            continue
        date_cands = [c for c in cols if c != body and date_ok[c] >= 0.8]
        date = max(date_cands, key=lambda c: (_hint(c, DATE_HINTS), date_ok[c]), default=None)
        title = next((c for c in cols if c not in (body, date) and _hint(c, TITLE_HINTS)), None)
        sender = next((c for c in cols if c not in (body, date, title) and _hint(c, SENDER_HINTS)
                       and text_len[c] > 0), None)
        score = text_len[body] * (2 if _hint(t, BODY_HINTS) else 1) + (50 if date else 0) + min(rows, 1000) / 100
        cand = {"table": t, "body": body, "title": title, "date": date, "sender": sender, "rows": rows,
                "score": score}
        if best is None or cand["score"] > best["score"]:
            best = cand
    return best


def _q(name):
    return '"' + str(name).replace('"', '""') + '"'


# ---------------------------------------------------------------- 읽기
class Snapshot:
    """잠긴 파일도 읽을 수 있게 임시 폴더에 복사해 엶 (with 문으로 사용)."""

    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        if not self.path.is_file():
            raise CoolError(f"쿨메신저 파일이 없습니다: {self.path}\n"
                            "쿨메신저에 로그인한 이름과 같게 입력했는지 확인하세요.")
        with open(self.path, "rb") as f:
            head = f.read(16)
        if head != SQLITE_MAGIC:
            raise CoolError(f"쿨메신저 파일 형식을 아직 읽을 수 없습니다 (머리 {head[:8].hex()}). "
                            "⚙ 설정의 '쿨메신저 연결 확인' 결과를 개발 담당에게 보내 주세요.")
        self.tmp = tempfile.mkdtemp(prefix="desk-cool-")
        dst = Path(self.tmp) / "memo.db"
        try:
            shutil.copy2(self.path, dst)
            for ext in ("-wal", "-shm", "-journal"):  # 아직 합쳐지지 않은 최근 쪽지
                side = Path(str(self.path) + ext)
                if side.exists():
                    shutil.copy2(side, Path(str(dst) + ext))
        except OSError as e:
            shutil.rmtree(self.tmp, ignore_errors=True)
            raise CoolError(f"쿨메신저 파일을 복사할 수 없습니다 ({e.strerror})")
        try:
            self.conn = sqlite3.connect(str(dst))
            self.conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError as e:
            shutil.rmtree(self.tmp, ignore_errors=True)
            raise CoolError(f"쿨메신저 파일을 열 수 없습니다 ({e}). 암호화되어 있을 수 있습니다.")
        return self.conn

    def __exit__(self, *exc):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


def _rows(conn, info, where="", args=(), order="ASC", limit=MAX_ROWS_PER_RUN):
    q = _q(info["table"])
    cols = [c for c in (info["body"], info["title"], info["date"], info["sender"]) if c]
    sel = ", ".join(["rowid"] + [_q(c) for c in cols])
    out = []
    for r in conn.execute(f"SELECT {sel} FROM {q} {where} ORDER BY rowid {order} LIMIT ?", (*args, limit)):
        rec = dict(zip(["rowid"] + cols, r))
        out.append({
            "rowid": rec["rowid"],
            "body": clean_body(rec[info["body"]]),
            "title": clean_body(rec[info["title"]]) if info["title"] else "",
            "sender": clean_body(rec[info["sender"]]) if info["sender"] else "",
            "when": parse_when(rec[info["date"]]) if info["date"] else None,
        })
    return out


def _detect_or_fail(conn):
    info = detect(conn)
    if not info:
        raise CoolError("쿨메신저 파일에서 쪽지 표를 찾지 못했습니다. "
                        "⚙ 설정의 '쿨메신저 연결 확인' 결과를 개발 담당에게 보내 주세요.")
    return info


def read_new(path, last_rowid=None, now=None):
    """마지막으로 읽은 뒤 새로 온 쪽지들과 새 마지막 rowid.

    처음(last_rowid 없음)에는 최근 FIRST_IMPORT_DAYS 일 (날짜 열이 없으면 최근 FIRST_IMPORT_ROWS 개)만.
    """
    now = now or datetime.now()
    with Snapshot(path) as conn:
        info = _detect_or_fail(conn)
        top_now = conn.execute(f"SELECT MAX(rowid) FROM {_q(info['table'])}").fetchone()[0] or 0
        if last_rowid is not None and last_rowid > top_now:
            last_rowid = None  # 쿨메신저를 다시 설치하는 등으로 파일이 새로 만들어짐
        if last_rowid is None:
            memos = _rows(conn, info, order="DESC")[::-1]
            if info["date"]:
                since = now - timedelta(days=FIRST_IMPORT_DAYS)
                memos = [m for m in memos if m["when"] and m["when"] >= since]
            else:
                memos = memos[-FIRST_IMPORT_ROWS:]
            top = top_now
        else:
            memos = _rows(conn, info, "WHERE rowid > ?", (last_rowid,))
            top = max([last_rowid] + [m["rowid"] for m in memos])
        return info, [m for m in memos if m["body"]], top


def check(path):
    """설정 화면의 '연결 확인': 찾은 표·열과 최근 쪽지 제목 3개 (이 PC 화면에만 표시)."""
    with Snapshot(path) as conn:
        tables = []
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({_q(t)})")]
            tables.append({"name": t, "columns": cols,
                           "rows": conn.execute(f"SELECT COUNT(*) FROM {_q(t)}").fetchone()[0]})
        info = detect(conn)
        out = {"path": str(path), "tables": tables, "detected": None, "recent": []}
        if info:
            out["detected"] = {k: info[k] for k in ("table", "body", "title", "date", "sender", "rows")}
            for m in _rows(conn, info, order="DESC", limit=3):
                first = m["title"] or m["body"].split("\n", 1)[0]
                out["recent"].append({"title": first[:60], "sender": m["sender"][:20],
                                      "when": m["when"].strftime("%Y-%m-%d %H:%M") if m["when"] else None})
    return out
