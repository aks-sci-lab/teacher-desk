"""통화·문자 내역 → 상담 기록 초안 (0.5.83).

휴대폰 백업 앱이 내보낸 파일(SMS Backup & Restore 의 calls.xml·sms.xml, 통화 기록 CSV)을 선생님이 PC 에서 골라 올리면
학생 명단의 **학부모 연락처**와 번호를 맞춰 '상담 기록 초안'을 만듭니다.

- 파일은 이 PC 의 서버가 메모리에서만 읽고 저장하지 않습니다. 초안도 선생님이 [저장]을 누른 것만 상담 기록이 됩니다.
- 명단의 학부모 번호와 맞지 않는 번호는 **버립니다** (화면에 보내지도, 저장하지도, 기록에 남기지도 않음 — 건수만 셈).
- 텔레그램으로 받기(0.5.84, 사용자 결정 2026-11-10): 설정 `telegram_calllog`(기본 끔)을 켠 경우, 연결된 선생님 채팅의
  파일만. 초안은 바로 저장하지 않고 class_call_drafts 에 쌓아 두었다가 PC 에서 확인해야 저장, 14일 뒤 자동 삭제.
- 문자 요약은 선택이며 이 PC 의 LM Studio(127.0.0.1)로만, 학생 이름·전화번호는 가려서 보냄.
"""

import base64
import binascii
import csv
import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
from xml.parsers import expat
from datetime import date, datetime, timedelta

from . import ai
from .classroom import need, roster
from . import classrecords
from .classrecords import counsel_fields
from .db import now_str

MAX_TOTAL = 14 * 1024 * 1024   # 파일들을 합친 크기. base64(4/3배)+JSON 이 서버 요청 한도 20MB 안에 들게 (검토 2026-11-10)
MAX_FILES = 5
MAX_DRAFTS = 500
P_DOCTYPE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)


# ---------------------------------------------------------------- 번호·날짜·길이
def digits(phone):
    """'+82 10-1234-5678' → '01012345678'. 번호가 아니면 ''."""
    d = re.sub(r"\D", "", str(phone or ""))
    if d.startswith("82") and len(d) >= 11:
        d = "0" + d[2:]
    return d if 9 <= len(d) <= 11 else ""


def parent_map(db):
    """학부모 번호(숫자만) → [학생 번호] (형제가 같은 번호를 쓸 수 있음)."""
    out = {}
    for s in db.students():
        d = digits(s.get("parent_phone"))
        if d:
            out.setdefault(d, []).append(s["number"])
    return out


P_AMPM = re.compile(r"(오전|오후|AM|PM|am|pm)\s*")


def parse_when(v):
    """여러 모양의 날짜·시각 → datetime. 못 읽으면 None."""
    s = str(v or "").strip()
    if not s:
        return None
    if s.isdigit() and len(s) in (10, 13):   # 유닉스 시각 (초 / 밀리초)
        try:
            return datetime.fromtimestamp(int(s) / (1000 if len(s) == 13 else 1))
        except (OverflowError, OSError, ValueError):
            return None
    pm = None
    m = P_AMPM.search(s)
    if m:
        pm = m.group(1).lower() in ("오후", "pm")
        s = P_AMPM.sub("", s)
    s = re.sub(r"(\d{1,2})\s*시\s*(?:(\d{1,2})\s*분)?(?:\s*(\d{1,2})\s*초)?",
               lambda m: f"{m[1]}:{int(m[2] or 0):02d}:{int(m[3] or 0):02d}", s)   # '오후 3시 10분' → '3:10:00'
    s = re.sub(r"\s+", " ", s.replace("년", "-").replace("월", "-").replace("일", " ")).strip()
    # '2026. 11. 4. 9:00' (삼성 모양) → '2026-11-4 9:00' — 날짜 부분만 (시각과 붙지 않게)
    s = re.sub(r"^(\d{4})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})\s*\.?(?=\s|$|T)", r"\1-\2-\3", s)
    m = re.match(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})\.?(?:[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)   # 11/9/2026 14:03 (미국식)
        if not m:
            return None
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    h, mi, se = int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0)
    if pm is True and h < 12:
        h += 12
    elif pm is False and h == 12:
        h = 0
    try:
        return datetime(y, mo, d, h, mi, se)
    except ValueError:
        return None


def parse_duration(v):
    """초(65) · 'mm:ss' · 'hh:mm:ss' · '1분 5초' · '1시간 2분' → 초. 못 읽으면 None."""
    s = str(v or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if re.fullmatch(r"\d{1,2}(:\d{2}){1,2}", s):
        parts = [int(x) for x in s.split(":")]
        return sum(p * 60 ** k for k, p in enumerate(reversed(parts)))
    total, found = 0, False
    for num, unit in re.findall(r"(\d+)\s*(시간|분|초|h|m|s)", s):
        found = True
        total += int(num) * {"시간": 3600, "h": 3600, "분": 60, "m": 60, "초": 1, "s": 1}[unit]
    return total if found else None


def duration_text(sec):
    if not sec:
        return "0초"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return " ".join(x for x in (f"{h}시간" if h else "", f"{m}분" if m else "", f"{s}초" if s else "") if x)


CALL_TYPES = {"1": "in", "2": "out", "3": "missed", "4": "missed", "5": "rejected", "6": "rejected", "10": "rejected"}
WORD_TYPES = [("부재", "missed"), ("missed", "missed"), ("거절", "rejected"), ("reject", "rejected"), ("block", "rejected"),
              ("차단", "rejected"), ("수신", "in"), ("받", "in"), ("incoming", "in"), ("received", "in"), ("inbox", "in"),
              ("발신", "out"), ("건 ", "out"), ("보낸", "out"), ("outgoing", "out"), ("sent", "out"), ("dialed", "out")]


def direction(v):
    s = str(v or "").strip().lower()
    if s in CALL_TYPES:
        return CALL_TYPES[s]
    for word, d in WORD_TYPES:
        if word in s + " ":
            return d
    return "in"


DIR_TEXT = {"in": "받은 전화", "out": "건 전화", "missed": "부재중 전화", "rejected": "거절한 전화"}


# ---------------------------------------------------------------- 파일 읽기 → [{kind, phone, when, dir, duration, body}]
def decode_text(raw):
    for enc in ("utf-8-sig", "cp949", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("글자를 읽지 못했습니다 (UTF-8·CP949 가 아닌 파일)")


class _Refused(Exception):
    pass


def _refuse(*_args):
    raise _Refused()


def safe_xml_check(raw):
    """엔티티 폭탄 등을 막는 검사 (검토 2026-11-10: 바이트 정규식만으로는 UTF-16 XML 이 빠져나감).
    ① UTF-8(BOM 허용)이 아닌 XML 은 받지 않음 — NUL 바이트·UTF-16/32 BOM 이 있으면 거부.
    ② expat 으로 먼저 훑어 DOCTYPE·ENTITY 선언을 만나면 인코딩과 상관없이 거부."""
    need(not raw.startswith((b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")) and b"\x00" not in raw,
         "UTF-8 이 아닌 XML 파일은 읽지 않습니다 (백업 앱에서 UTF-8 로 내보내세요)")
    need(not P_DOCTYPE.search(raw), "이 XML 파일은 읽을 수 없는 형식입니다 (DOCTYPE·ENTITY 포함)")
    p = expat.ParserCreate()
    p.StartDoctypeDeclHandler = _refuse
    p.EntityDeclHandler = _refuse
    p.UnparsedEntityDeclHandler = _refuse
    p.ExternalEntityRefHandler = lambda *a: _refuse()
    try:
        p.Parse(raw, True)
    except _Refused:
        need(False, "이 XML 파일은 읽을 수 없는 형식입니다 (DOCTYPE·ENTITY 포함)")
    except expat.ExpatError:
        need(False, "XML 파일이 깨져 있습니다")


P_CHARREF = re.compile(rb"&#(x[0-9a-fA-F]+|[0-9]+);")


def fix_surrogate_refs(raw):
    """SMS Backup & Restore 는 이모지를 UTF-16 서로게이트 두 개의 숫자 참조(&#55357;&#56842;)로 적는데,
    XML 규칙상 잘못된 글자라 파서가 파일 전체를 거부함 (가짜 학급 끝까지 시험 2026-11-10). 짝이 맞으면 한 글자로,
    짝이 없으면 '�' 로 바꿈."""
    if b"&#" not in raw:
        return raw

    def val(m):
        g = m.group(1)
        try:
            return int(g[1:], 16) if g[:1] in (b"x", b"X") else int(g)
        except ValueError:
            return None
    refs = [(m, val(m)) for m in P_CHARREF.finditer(raw)]
    refs = [(m, v) for m, v in refs if v is not None and 0xD800 <= v <= 0xDFFF]
    out, pos, k = [], 0, 0
    while k < len(refs):
        m, v = refs[k]
        out.append(raw[pos:m.start()])
        nxt = refs[k + 1] if k + 1 < len(refs) else None
        if v <= 0xDBFF and nxt and nxt[0].start() == m.end() and 0xDC00 <= nxt[1] <= 0xDFFF:   # 높은 쪽+낮은 쪽 → 한 글자
            out.append(f"&#{0x10000 + ((v - 0xD800) << 10) + (nxt[1] - 0xDC00)};".encode())
            pos, k = nxt[0].end(), k + 2
            continue
        out.append(b"&#65533;")   # 짝 없는 반쪽
        pos, k = m.end(), k + 1
    out.append(raw[pos:])
    return b"".join(out)


def read_xml(raw):
    """SMS Backup & Restore 형식: <calls><call .../></calls>, <smses><sms .../><mms ...>…</mms></smses>."""
    raw = fix_surrogate_refs(raw)   # 숫자 참조만 고침 (엔티티를 펼치지 않음) — 그다음 안전 검사
    safe_xml_check(raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ValueError("XML 파일이 깨져 있습니다")
    out = []
    for el in root.iter():
        a = el.attrib
        if el.tag == "call":
            out.append({"kind": "call", "phone": a.get("number"), "when": parse_when(a.get("date")),
                        "dir": direction(a.get("type")), "duration": parse_duration(a.get("duration")) or 0})
        elif el.tag == "sms":
            out.append({"kind": "sms", "phone": a.get("address"), "when": parse_when(a.get("date")),
                        "dir": "out" if a.get("type") == "2" else "in", "body": a.get("body") or ""})
        elif el.tag == "mms":
            text = " ".join(p.get("text") or "" for p in el.iter("part") if (p.get("ct") or "").startswith("text/plain")).strip()
            phone = a.get("address") or next((x.get("address") for x in el.iter("addr")), "")
            # 그룹 문자는 'a~b~c' — 받는 사람마다 (학부모 번호와 맞는 것만 남음)
            for one in dict.fromkeys(p.strip() for p in (phone or "").split("~") if p.strip()):
                out.append({"kind": "sms", "phone": one, "when": parse_when(a.get("date")),
                            "dir": "out" if a.get("msg_box") == "2" else "in", "body": text or "(사진·첨부 문자)"})
    return out


HEAD = {
    "phone": ("전화번호", "상대방번호", "상대방 번호", "연락처", "번호", "phone number", "phone", "number", "address", "전화"),
    "when": ("통화일시", "일시", "날짜", "date", "datetime", "date time", "call date"),
    "time": ("시각", "통화시각", "시간", "time"),   # 날짜와 따로 있는 시각 칸 (삼성 모양: 날짜 · 시간 · 통화 시간)
    "duration": ("통화시간", "통화 시간", "통화길이", "길이", "duration", "call duration"),
    "type": ("유형", "종류", "구분", "통화유형", "type", "call type", "direction"),
    "body": ("내용", "메시지", "문자내용", "body", "message", "text", "content"),
}


def read_csv(text):
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    need(rows, "빈 파일입니다")
    head = [h.strip().lower() for h in rows[0]]
    col = {}
    for key, names in HEAD.items():
        for name in names:   # 앞에 둔 이름일수록 먼저 (예: '전화번호'가 '번호'보다 먼저)
            if name in head and head.index(name) not in col.values():
                col[key] = head.index(name)
                break
    if "when" not in col and "time" in col:   # '시간' 한 칸에 날짜까지 들어 있는 모양
        col["when"] = col.pop("time")
    need("phone" in col and "when" in col, "전화번호·날짜 칸을 찾지 못했습니다. 첫 줄에 '전화번호', '날짜' 같은 제목이 있는 CSV 여야 합니다")
    out = []
    for r in rows[1:]:
        get = lambda k: r[col[k]].strip() if k in col and col[k] < len(r) else ""
        when = get("when") + (" " + get("time") if get("time") and ":" not in get("when") else "")
        body = get("body")
        kind = "sms" if "body" in col and body else "call"
        item = {"kind": kind, "phone": get("phone"), "when": parse_when(when), "dir": direction(get("type"))}
        if kind == "call":
            item["duration"] = parse_duration(get("duration")) or 0
        else:
            item["body"] = body
        out.append(item)
    return out


def read_file(name, raw):
    need(len(raw) <= MAX_TOTAL, f"'{name}' 파일이 너무 큽니다 (14MB 이하)")
    head = raw.lstrip(b" \t\r\n\x00")[:200]
    # '<' 로 시작하면 XML (UTF-16 처럼 '<' 뒤에 NUL 이 오는 것도 XML 로 보고 safe_xml_check 에서 거부)
    if head.startswith((b"<", b"\xef\xbb\xbf<", b"\xff\xfe<", b"\xfe\xff\x00<")):
        return read_xml(raw)
    return read_csv(decode_text(raw))


# ---------------------------------------------------------------- 초안 만들기
def _key(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:32]


def make_drafts(db, entries, since=None):
    """명단의 학부모 번호와 맞는 것만 초안으로. 맞지 않는 번호는 건수만 세고 버림.
    통화는 한 통마다, 문자는 학생·날짜마다 한 초안으로 묶음."""
    floor = date.fromisoformat(classrecords.year_range(classrecords.current_year())[0])
    if since and since > floor:
        floor = since
    pmap = parent_map(db)
    names = {r["number"]: r["name"] for r in roster(db)}
    stats = {"calls": 0, "sms": 0, "matched": 0, "discarded": 0, "unreadable": 0, "already": 0, "old": 0}
    calls, sms = [], {}
    for e in entries:
        stats["calls" if e["kind"] == "call" else "sms"] += 1
        if not e["when"]:
            stats["unreadable"] += 1
            continue
        d = digits(e["phone"])
        nums = pmap.get(d) if d else None
        if not nums:
            stats["discarded"] += 1   # 명단에 없는 번호: 내용·번호 모두 버림
            continue
        if e["when"].date() < floor:   # 시작 날짜 전, 또는 지난 학년도(3월 1일 전) — 상담 기록은 이번 학년도만
            stats["old"] += 1
            continue
        stats["matched"] += 1
        for n in nums:
            if e["kind"] == "call":
                calls.append((n, d, e))
            else:
                sms.setdefault((n, e["when"].date()), []).append((d, e))
    drafts = []
    for n, d, e in calls:
        w = e["when"]
        dur = e.get("duration") or 0
        talked = e["dir"] in ("in", "out") and dur > 0
        drafts.append({"number": n, "name": names.get(n, ""), "method": "전화", "with_whom": "학부모",
                       "date": w.date().isoformat(), "time": w.strftime("%H:%M"), "duration": dur, "dir": e["dir"],
                       "topic": "학부모 전화 상담" if talked else DIR_TEXT[e["dir"]],
                       "content": f"{DIR_TEXT[e['dir']]}" + (f" · {duration_text(dur)}" if talked else ""),
                       "pick": talked, "import_key": _key("call", d, w.isoformat(), e["dir"], n)})
    for (n, day), msgs in sms.items():
        msgs.sort(key=lambda x: x[1]["when"])
        lines = [f"[{e['when'].strftime('%H:%M')} {'보냄' if e['dir'] == 'out' else '받음'}] {e.get('body') or ''}".strip()
                 for _, e in msgs]
        drafts.append({"number": n, "name": names.get(n, ""), "method": "문자", "with_whom": "학부모",
                       "date": day.isoformat(), "time": msgs[0][1]["when"].strftime("%H:%M"), "duration": None, "dir": None,
                       "topic": f"학부모 문자 ({len(msgs)}건)", "content": "\n".join(lines)[:5000], "pick": True,
                       "import_key": _key("sms", n, day.isoformat(), *[f"{d}{e['when'].isoformat()}{e['dir']}" for d, e in msgs])})
    # 같은 통화가 여러 파일(XML·CSV)이나 같은 파일 두 번으로 들어오면 한 초안만 (가짜 학급 끝까지 시험 2026-11-10)
    drafts = list({d["import_key"]: d for d in reversed(drafts)}.values())[::-1]
    have = {r["import_key"] for r in db.query("SELECT import_key FROM class_counsel WHERE import_key IS NOT NULL")}
    for dr in drafts:
        if dr["import_key"] in have:
            dr["already"] = True
            dr["pick"] = False
            stats["already"] += 1
    drafts.sort(key=lambda x: (x["date"], x["time"]), reverse=True)
    return drafts[:MAX_DRAFTS], stats


def parse_upload(db, body):
    files = body.get("files")
    need(isinstance(files, list) and 0 < len(files) <= MAX_FILES, f"파일을 1~{MAX_FILES}개 고르세요")
    since = None
    if body.get("since"):
        try:
            since = date.fromisoformat(body["since"])
        except (TypeError, ValueError):
            need(False, "시작 날짜 형식이 잘못되었습니다")
    need(any(digits(s.get("parent_phone")) for s in db.students()),
         "학생 명단에 학부모 연락처가 없습니다. 출결 메뉴의 학생 명단에 학부모 연락처를 먼저 넣으세요")
    entries, total = [], 0
    for f in files:
        need(isinstance(f, dict) and isinstance(f.get("data"), str), "파일 자료가 잘못되었습니다")
        name = str(f.get("name") or "파일")[:80]
        try:
            raw = base64.b64decode(f["data"], validate=True)
        except (binascii.Error, ValueError):
            need(False, f"'{name}' 파일을 읽지 못했습니다")
        total += len(raw)
        need(total <= MAX_TOTAL, "파일을 합쳐서 14MB 이하로 골라 주세요")
        try:
            entries += read_file(name, raw)
        except ValueError as e:
            need(False, f"'{name}': {e}")
    drafts, stats = make_drafts(db, entries, since)
    return {"drafts": drafts, "stats": stats}


# ---------------------------------------------------------------- 텔레그램으로 받은 초안 (0.5.84)
PENDING_DAYS = 14          # 이 날수가 지나면 확인하지 않은 초안을 지움
TELEGRAM_SINCE_DAYS = 60   # 텔레그램으로 받은 파일은 최근 60일 것만 (전체 백업에 몇 년 치가 들어 있어도)


def expire_pending(db):
    cut = (datetime.now() - timedelta(days=PENDING_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("DELETE FROM class_call_drafts WHERE created_at < ?", (cut,))


def receive_file(db, raw, today=None):
    """텔레그램으로 받은 파일 하나 → 학부모 번호와 맞는 초안만 쌓아 둠. 돌려줌: 건수만 (이름·번호·내용 없음)."""
    expire_pending(db)
    need(any(digits(s.get("parent_phone")) for s in db.students()),
         "학생 명단에 학부모 연락처가 없습니다. 업무 데스크의 학생 명단에 학부모 연락처를 먼저 넣으세요")
    entries = read_file("보낸 파일", raw)   # 파일 이름(학생 이름이 들어 있을 수 있음)은 쓰지 않음
    since = (today or date.today()) - timedelta(days=TELEGRAM_SINCE_DAYS)
    drafts, stats = make_drafts(db, entries, since)
    waiting = {d["import_key"] for d in pending_drafts(db)}
    drafts = [d for d in drafts if not d.get("already") and d["import_key"] not in waiting]
    if drafts:
        db.execute("INSERT INTO class_call_drafts(drafts, stats, created_at) VALUES(?, ?, ?)",
                   (json.dumps(drafts, ensure_ascii=False), json.dumps(stats), now_str()))
    db.log(f"텔레그램으로 받은 통화·문자 파일에서 상담 초안 {len(drafts)}건 만듦")   # 이름·번호·내용 없음
    return {"drafts": len(drafts), "discarded": stats["discarded"], "already": stats["already"], "old": stats["old"]}


def pending_drafts(db):
    out = []
    for row in db.query("SELECT drafts FROM class_call_drafts ORDER BY id"):
        out += json.loads(row["drafts"])
    return out


def pending_state(db):
    """상담 화면의 '텔레그램으로 받은 초안': 이미 저장한 것은 빼고 보여 줌."""
    expire_pending(db)
    have = {r["import_key"] for r in db.query("SELECT import_key FROM class_counsel WHERE import_key IS NOT NULL")}
    seen, drafts = set(), []
    for d in pending_drafts(db):
        if d["import_key"] in have or d["import_key"] in seen:
            continue
        seen.add(d["import_key"])
        drafts.append(d)
    drafts.sort(key=lambda x: (x["date"], x["time"]), reverse=True)
    first = db.one("SELECT MIN(created_at) AS t FROM class_call_drafts")["t"]
    return {"drafts": drafts, "oldest": first, "keep_days": PENDING_DAYS}


def pending_drop(db, keys=None):
    """keys 가 없으면 모두 지움, 있으면 그 초안만 뺌 (저장했거나 버린 것)."""
    if keys is None:
        db.execute("DELETE FROM class_call_drafts")
        return {"ok": True}
    keys = set(keys)
    with db.lock, db.conn:
        for row in db.conn.execute("SELECT id, drafts FROM class_call_drafts").fetchall():
            rest = [d for d in json.loads(row["drafts"]) if d["import_key"] not in keys]
            if rest:
                db.conn.execute("UPDATE class_call_drafts SET drafts = ? WHERE id = ?", (json.dumps(rest, ensure_ascii=False), row["id"]))
            else:
                db.conn.execute("DELETE FROM class_call_drafts WHERE id = ?", (row["id"],))
    return {"ok": True}


def _drop_body(db, body):
    keys = body.get("keys")
    need(keys is None or (isinstance(keys, list) and all(isinstance(k, str) for k in keys)), "초안 자료가 잘못되었습니다")
    return pending_drop(db, keys)


def save_drafts(db, body):
    """선생님이 확인·보충한 초안만 상담 기록으로 저장 (source='import')."""
    items = body.get("items")
    need(isinstance(items, list) and 0 < len(items) <= MAX_DRAFTS, "저장할 초안을 고르세요")
    have = {r["import_key"] for r in db.query("SELECT import_key FROM class_counsel WHERE import_key IS NOT NULL")}
    rows = []
    for it in items:
        need(isinstance(it, dict), "초안 자료가 잘못되었습니다")
        key = it.get("import_key")
        need(key is None or (isinstance(key, str) and re.fullmatch(r"[0-9a-f]{32}", key)), "초안 자료가 잘못되었습니다")
        if key in have:
            continue   # 이미 저장한 것 (두 번 누름)
        f = counsel_fields(db, {k: it.get(k) for k in ("number", "date", "time", "method", "with_whom", "topic", "content",
                                                        "followup", "followup_date", "duration") if k in it})
        f.setdefault("followup_date", None)
        f.update({"followup_done": 0, "source": "import", "import_key": key, "created_at": now_str(), "updated_at": now_str()})
        rows.append(f)
        have.add(key)
    with db.lock, db.conn:
        for f in rows:
            db.conn.execute(f"INSERT INTO class_counsel({', '.join(f)}) VALUES({', '.join('?' * len(f))})", tuple(f.values()))
    pending_drop(db, [f["import_key"] for f in rows if f["import_key"]])   # 텔레그램으로 받은 초안이었으면 목록에서 뺌
    db.log(f"학급 상담 기록 {len(rows)}건 저장 (통화·문자 내역에서)")   # 이름·번호·내용 없음
    return {"saved": len(rows), "skipped": len(items) - len(rows)}


P_PHONE = re.compile(r"(?:\+?82[- ]?|0)1\d[- .]?\d{3,4}[- .]?\d{4}")
SUMMARY_SYSTEM = "너는 담임 교사의 상담 기록을 돕는 도우미다. 한국어로 짧고 사실대로 쓴다."
SUMMARY_PROMPT = ("아래는 학부모와 주고받은 문자다. 상담 기록에 쓸 수 있게 핵심만 2~4줄로 요약해 줘. "
                  "추측하지 말고, 해야 할 일이 있으면 마지막 줄에 '후속: ...' 으로 써 줘.\n\n")


def summarize(db, config, body):
    """문자 내용 요약: 이 PC 의 LM Studio 로만, 학생 이름·전화번호는 가려서 보내고 답에서도 가림."""
    need(config["ai_enabled"], "AI 요약이 꺼져 있습니다 (설정 → AI 에서 LM Studio 를 켜세요)")
    text = body.get("text")
    need(isinstance(text, str) and text.strip(), "요약할 내용이 없습니다")
    names = [s["name"] for s in db.students()]
    clean = P_PHONE.sub("[전화번호]", ai.mask_names(text[:4000], names))
    try:
        ai.check_local(config["ai_base"])   # 이 PC 가 아닌 AI 주소는 거부
        model = ai.pick_model(config)
        out = ai.ask(config["ai_base"], model, SUMMARY_PROMPT + clean, timeout=120, system=SUMMARY_SYSTEM, max_tokens=400, db=db)
    except ai.AIError as e:
        need(False, str(e))
    return {"summary": P_PHONE.sub("[전화번호]", ai.mask_names(out, names)).strip()}


def _needs_config(fn):
    fn.with_config = True
    return fn


ROUTES = [
    ("POST", "/api/class/calllog/parse", lambda db, q, b: parse_upload(db, b)),
    ("POST", "/api/class/calllog/save", lambda db, q, b: save_drafts(db, b)),
    ("GET", "/api/class/calllog/pending", lambda db, q, b: pending_state(db)),
    ("POST", "/api/class/calllog/pending/drop", lambda db, q, b: _drop_body(db, b)),
    ("POST", "/api/class/calllog/summarize", _needs_config(lambda db, config, q, b: summarize(db, config, b))),
]
