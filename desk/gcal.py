"""선생님 본인 구글 캘린더와 일정 공유 (0.5.91, 사용자 결정 2026-11-10: "내 개인 클라우드에 올라가는건 허용해").

- 가져오기(구글 → 업무 데스크): 구글 캘린더의 '비공개 주소(iCal 형식)'를 켤 때·30분마다 읽어
  `gcal_events` 표에 넣고 달력에 '구글' 표시로 보여 줌 (읽기 전용). 업무 데스크가 올린 일정은 다시 들이지 않음.
- 올리기(업무 데스크 → 구글): 선생님 계정의 Google Apps Script 웹 앱으로 보냄. 스크립트는 선생님이
  안내대로 붙여 넣고 배포하며, 업무 데스크가 만든 비밀 키가 맞을 때만, 업무 데스크가 만든 일정(표시 태그)만 고치거나 지움.
  보낼 일은 따로 대기열에 쌓지 않고 '지금 DB 의 일정'과 '구글에 올린 기록(`gcal_sync`)'을 비교해 정함 →
  어느 화면·텔레그램·수집이 일정을 바꿔도 같은 길로 반영되고, 실패하면 다음 차례에 저절로 다시 보냄.
- 주소·비밀 키는 config.json 에만 (화면에 다시 보이지 않고 기록(로그)·오류 글에 넣지 않음).
- 서버는 그대로 127.0.0.1 전용. 여기서는 밖으로 HTTPS 요청만 보냄.
"""

import calendar
import hashlib
import ipaddress
import json
import re
import secrets
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

from . import ai
from .db import now_str

KST = timezone(timedelta(hours=9))
TAG = "teacher_desk"
MARK = "업무 데스크(teacher-desk)에서 올린 일정"
SCRIPT_VERSION = 1

# 올릴 일정 종류 (설정에서 고름). 한 일정이 여러 종류에 들 수 있고, 하나라도 켜져 있으면 올림
TYPES = [
    ("manual", "직접 추가한 일정"),
    ("deadline", "마감"),
    ("school", "학사일정"),
    ("meeting", "회의"),
    ("inbox", "받은 자료(쪽지·공문)의 일정 모두"),
    ("lesson", "시간표 교체"),
]
TYPE_KEYS = [k for k, _ in TYPES]
DEFAULT_TYPES = ["manual", "deadline", "school"]

PAST_DAYS = 30        # 새로 올리는 범위: 30일 전 ~ 1년 뒤 (이미 올린 것은 범위 밖이어도 고침·지움)
FUTURE_DAYS = 365
IMPORT_PAST = 60      # 가져오는 범위
IMPORT_FUTURE = 400
CHUNK = 40            # 한 번에 보내는 일 수 (Apps Script 실행 시간 안에 끝나게)
MAX_OPS = 400         # 한 차례에 보내는 최대 일 수 (나머지는 다음 차례)
MAX_ICS = 8 * 1024 * 1024
BACKOFF = [2, 5, 10, 30, 60]   # 실패하면 몇 분 뒤 다시 (연속 실패마다 늘어남)

SCHEMA = """
CREATE TABLE IF NOT EXISTS gcal_events (
    uid TEXT PRIMARY KEY,            -- 구글 일정 UID + 반복 일정이면 그 날짜
    date TEXT NOT NULL,
    end_date TEXT,
    time TEXT,
    text TEXT
);
CREATE INDEX IF NOT EXISTS gcal_events_date ON gcal_events(date);
CREATE TABLE IF NOT EXISTS gcal_sync (
    event_id INTEGER PRIMARY KEY,    -- 업무 데스크 일정 번호
    gid TEXT,                        -- 구글 일정 id (iCalUID)
    hash TEXT,                       -- 마지막으로 보낸 내용
    updated_at TEXT
);
"""


class GcalError(Exception):
    pass


# ---- 주소 검사 ----
def check_ical_url(url):
    """구글 캘린더 비공개 iCal 주소만 받음 (다른 서버로 요청을 보내지 않게)."""
    u = urlparse((url or "").strip())
    if u.scheme != "https" or u.hostname != "calendar.google.com" or not u.path.startswith("/calendar/ical/") \
            or not u.path.endswith(".ics"):
        raise ValueError("구글 캘린더의 '비공개 주소(iCal 형식)'를 붙여 넣으세요 "
                         "(https://calendar.google.com/calendar/ical/… 로 시작하고 .ics 로 끝남)")
    if "/public/" in u.path:
        raise ValueError("'공개 주소'가 아니라 '비공개 주소(iCal 형식)'를 붙여 넣으세요")
    return url.strip()


PUSH_PATH = re.compile(r"^/(?:macros|a/macros/[A-Za-z0-9.-]+)/s/[A-Za-z0-9_-]{20,}/exec$")


def check_push_url(url):
    u = urlparse((url or "").strip())
    if u.scheme != "https" or u.hostname != "script.google.com" or not PUSH_PATH.match(u.path):
        raise ValueError("Apps Script '웹 앱 URL'을 붙여 넣으세요 (https://script.google.com/macros/s/…/exec)")
    return url.strip()


def _opener(url):
    host = urlparse(url).hostname or ""
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host == "localhost"
    # 시험용 가짜 서버(127.0.0.1)는 프록시를 거치지 않음. 구글은 PC 의 보통 인터넷 설정 그대로
    return urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()


def _fetch(url, data=None, timeout=30, limit=MAX_ICS):
    """주소는 오류 글에 넣지 않음 (비밀 주소)."""
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": "TeacherDesk", **({"Content-Type": "application/json"} if data is not None else {})})
    try:
        with _opener(url).open(req, timeout=timeout) as r:
            body = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise GcalError(f"구글이 거절했습니다 (HTTP {e.code}). 주소가 바뀌었거나 권한 설정을 확인하세요")
        if e.code == 404:
            raise GcalError("주소를 찾을 수 없습니다 (HTTP 404). 주소를 다시 복사해 넣으세요")
        raise GcalError(f"구글 서버 오류 (HTTP {e.code})")
    except (urllib.error.URLError, OSError, ValueError):
        raise GcalError("구글에 연결하지 못했습니다. 인터넷 연결을 확인하세요")
    if len(body) > limit:
        raise GcalError("받은 자료가 너무 큽니다")
    return body


# ---- iCal 읽기 (구글 → 업무 데스크) ----
def _unfold(text):
    out = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        elif line:
            out.append(line)
    return out


def _unesc(v):
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def _prop(line):
    head, _, value = line.partition(":")
    name, *params = head.split(";")
    p = {}
    for x in params:
        k, _, v = x.partition("=")
        p[k.upper()] = v.strip('"')
    return name.upper(), p, value


def _dt(value, params):
    """→ (date, 'HH:MM' 또는 None) 한국 시각 기준."""
    v = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", v):
        return date(int(v[:4]), int(v[4:6]), int(v[6:8])), None
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)", v)
    if not m:
        raise ValueError(v)
    d = datetime(*map(int, m.groups()[:6]))
    if m.group(7):
        d = d.replace(tzinfo=timezone.utc).astimezone(KST).replace(tzinfo=None)
    else:
        tz = params.get("TZID")
        if tz and tz not in ("Asia/Seoul", "Asia/Tokyo"):
            try:   # 윈도우용 ZIP 의 Python 에는 시간대 자료가 없을 수 있음 → 없으면 적힌 시각 그대로
                from zoneinfo import ZoneInfo
                d = d.replace(tzinfo=ZoneInfo(tz)).astimezone(KST).replace(tzinfo=None)
            except Exception:
                pass
    return d.date(), d.strftime("%H:%M")


def _dt_full(value, params):
    d, t = _dt(value, params)
    if t is None:
        return datetime(d.year, d.month, d.day), True
    return datetime(d.year, d.month, d.day, int(t[:2]), int(t[3:])), False


WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _nth_weekday(y, m, wd, n):
    days = [d for d in range(1, calendar.monthrange(y, m)[1] + 1) if date(y, m, d).weekday() == wd]
    try:
        return days[n - 1] if n > 0 else days[n]
    except IndexError:
        return None


def _expand(start, rule, win_end, limit=800):
    """반복 규칙(RRULE)의 시작 시각들. 흔한 것만: DAILY·WEEKLY(BYDAY)·MONTHLY(날짜·n번째 요일)·YEARLY."""
    r = dict(x.partition("=")[::2] for x in rule.split(";") if "=" in x)
    freq = r.get("FREQ", "").upper()
    interval = max(1, int(r.get("INTERVAL", "1") or 1))
    count = int(r["COUNT"]) if r.get("COUNT", "").isdigit() else None
    until = None
    if r.get("UNTIL"):
        try:
            ud, ut = _dt(r["UNTIL"], {})
            until = datetime(ud.year, ud.month, ud.day, *(map(int, ut.split(":")) if ut else (23, 59)))
        except ValueError:
            until = None
    byday = [x.strip().upper() for x in r.get("BYDAY", "").split(",") if x.strip()]
    out, n = [], 0

    def emit(d):
        nonlocal n
        if d < start:
            return True
        if (until and d > until) or (count is not None and n >= count) or d > win_end or len(out) >= limit:
            return False
        out.append(d)
        n += 1
        return True

    if freq == "DAILY":
        d = start
        while emit(d):
            d += timedelta(days=interval)
    elif freq == "WEEKLY":
        wds = sorted({WEEKDAYS[x[-2:]] for x in byday if x[-2:] in WEEKDAYS}) or [start.weekday()]
        week = start - timedelta(days=start.weekday())
        go = True
        while go:
            for wd in wds:
                if not emit(week + timedelta(days=wd)):
                    go = False
                    break
            week += timedelta(weeks=interval)
    elif freq == "MONTHLY":
        k = 0
        go = True
        while go and k < 2000:
            base = start.replace(day=1)
            y, m = divmod(base.month - 1 + k * interval, 12)
            y, m = base.year + y, m + 1
            cands = []
            if byday:
                for x in byday:
                    mm = re.fullmatch(r"([+-]?\d+)?(MO|TU|WE|TH|FR|SA|SU)", x)
                    if mm and mm.group(1):
                        dd = _nth_weekday(y, m, WEEKDAYS[mm.group(2)], int(mm.group(1)))
                        if dd:
                            cands.append(dd)
            elif start.day <= calendar.monthrange(y, m)[1]:   # 31일 같은 날이 없는 달은 건너뜀
                cands.append(start.day)
            for dd in sorted(cands):
                if not emit(start.replace(year=y, month=m, day=dd)):
                    go = False
                    break
            k += 1
    elif freq == "YEARLY":
        k = 0
        while k < 200:
            try:
                d = start.replace(year=start.year + k * interval)
            except ValueError:   # 2월 29일
                k += 1
                continue
            if not emit(d):
                break
            k += 1
    else:
        emit(start)
    return out


def parse_ics(text, today=None, skip_uids=()):
    """iCal 글 → [{uid, date, end_date, time, text}] (가져오는 범위 안, 업무 데스크가 올린 것 빼고)."""
    today = today or date.today()
    win_start = datetime.combine(today - timedelta(days=IMPORT_PAST), datetime.min.time())
    win_end = datetime.combine(today + timedelta(days=IMPORT_FUTURE), datetime.min.time())
    skip = set(skip_uids)
    events, cur = [], None
    for line in _unfold(text):
        name, params, value = _prop(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            cur = {"exdate": set()}
        elif name == "END" and value.upper() == "VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif cur is not None:
            try:
                if name in ("DTSTART", "DTEND", "RECURRENCE-ID"):
                    cur[name] = _dt_full(value, params)
                elif name == "EXDATE":
                    for v in value.split(","):
                        cur["exdate"].add(_dt_full(v, params)[0])
                elif name in ("UID", "RRULE", "STATUS"):
                    cur[name] = value.strip()
                elif name in ("SUMMARY", "DESCRIPTION"):
                    cur[name] = _unesc(value)
            except (ValueError, IndexError):
                cur["bad"] = True
    overrides = {}
    for e in events:
        if e.get("UID") and e.get("RECURRENCE-ID"):
            overrides.setdefault(e["UID"], set()).add(e["RECURRENCE-ID"][0])
    out = {}
    for e in events:
        uid = e.get("UID")
        if not uid or e.get("bad") or "DTSTART" not in e or uid in skip or MARK in (e.get("DESCRIPTION") or ""):
            continue
        if (e.get("STATUS") or "").upper() == "CANCELLED":
            continue
        start, all_day = e["DTSTART"]
        end = e.get("DTEND", (start + (timedelta(days=1) if all_day else timedelta(0)), all_day))[0]
        length = max(end - start, timedelta(0))
        if e.get("RRULE") and not e.get("RECURRENCE-ID"):
            starts = [s for s in _expand(start, e["RRULE"], win_end)
                      if s not in e["exdate"] and s not in overrides.get(uid, ())]
        else:
            starts = [start]
        for s in starts:
            fin = s + length
            if fin < win_start and s < win_start or s > win_end:
                continue
            first = s.date()
            if all_day:
                last = (fin - timedelta(days=1)).date() if fin > s else first
                t = None
            else:
                last = (fin - timedelta(minutes=1)).date() if fin > s else first
                t = s.strftime("%H:%M")
            key = f"{uid}/{s:%Y%m%dT%H%M}" if e.get("RRULE") or e.get("RECURRENCE-ID") else uid
            out[key] = {"uid": key[:300], "date": first.isoformat(), "end_date": last.isoformat() if last > first else None,
                        "time": t, "text": (e.get("SUMMARY") or "(제목 없음)").strip()[:200]}
    return sorted(out.values(), key=lambda x: (x["date"], x["time"] or "99", x["uid"]))


# ---- 올리기 (업무 데스크 → 구글) ----
def event_tags(e):
    """일정 하나가 드는 종류들."""
    tags = set()
    src = e.get("item_source")
    if src == "school":
        tags.add("school")
    if e.get("kind") == "deadline":
        tags.add("deadline")
    if e.get("category") == "회의":
        tags.add("meeting")
    if e.get("item_id") is None:
        tags.add("lesson" if (e.get("text") or "").startswith("시간표 교체:") else "manual")
    elif src != "school":
        tags.add("inbox")
    return tags


def payload(e, names=None):
    """구글에 보낼 내용 (이 내용의 해시가 바뀌면 고침)."""
    d = date.fromisoformat(e["date"])
    title = ("[마감] " if e["kind"] == "deadline" else "") + (e.get("item_title") or e.get("text") or "일정")
    if e.get("done"):
        title = "✔ " + title
    desc = []
    if e.get("item_title") and e.get("text") and e["text"] != e["item_title"]:
        desc.append(e["text"])
    if e.get("category"):
        desc.append(f"분류: {e['category']}")
    if names:
        title = ai.mask_names(title, names)
        desc = [ai.mask_names(x, names) for x in desc]
    desc.append(MARK)
    p = {"ref": f"desk-{e['id']}", "title": title[:200], "description": "\n".join(desc)[:2000]}
    if e.get("time") and not e.get("end_date"):
        h, m = map(int, e["time"].split(":"))
        s = datetime(d.year, d.month, d.day, h, m)
        p.update(allDay=False, start=s.strftime("%Y-%m-%dT%H:%M:00+09:00"),
                 end=(s + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:00+09:00"))
    else:
        last = date.fromisoformat(e["end_date"]) if e.get("end_date") else d
        p.update(allDay=True, start=d.isoformat(), end=(max(last, d) + timedelta(days=1)).isoformat())
    return p


def payload_hash(p):
    return hashlib.sha256(json.dumps(p, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:32]


SCRIPT = r"""// 업무 데스크 → 내 구글 캘린더 (이 스크립트는 선생님 구글 계정 안에서만 실행됩니다)
// 업무 데스크가 만든 비밀 키(DESK_KEY)가 맞는 요청만 받고, 업무 데스크가 만든 일정만 고치거나 지웁니다.
// 이 글을 다른 사람에게 보내지 마세요. 키를 새로 만들면 이 스크립트도 새로 붙여 넣고 '새 버전'으로 배포해야 합니다.
const DESK_KEY = '__KEY__';
const CALENDAR_ID = 'primary';   // 다른 캘린더에 넣으려면 그 캘린더의 '캘린더 ID'를 따옴표 안에 넣으세요
const TAG = 'teacher_desk';

function doPost(e) {
  let req;
  try { req = JSON.parse(e.postData.contents); } catch (err) { return out_({ ok: false, error: 'bad_json' }); }
  if (!req || req.key !== DESK_KEY) return out_({ ok: false, error: 'bad_key' });
  const cal = CALENDAR_ID === 'primary' ? CalendarApp.getDefaultCalendar() : CalendarApp.getCalendarById(CALENDAR_ID);
  if (!cal) return out_({ ok: false, error: 'no_calendar' });
  if (req.op === 'ping') return out_({ ok: true, calendar: cal.getName(), version: __VERSION__ });
  const results = (req.ops || []).slice(0, 50).map(function (op) {
    try { return run_(cal, op); } catch (err) { return { ref: op.ref, ok: false, error: String(err).slice(0, 200) }; }
  });
  return out_({ ok: true, results: results });
}

function doGet() { return out_({ ok: true, app: 'teacher-desk' }); }

function find_(cal, gid) {
  if (!gid) return null;
  const ev = cal.getEventById(gid);
  return ev && ev.getTag(TAG) ? ev : null;   // 업무 데스크가 만든 일정만
}

function day_(s) { const p = s.split('-'); return new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2])); }

function run_(cal, op) {
  if (op.op === 'delete') {
    const old = find_(cal, op.gid);
    if (old) old.deleteEvent();
    return { ref: op.ref, ok: true };
  }
  if (op.op !== 'upsert') return { ref: op.ref, ok: false, error: 'bad_op' };
  let ev = find_(cal, op.gid);
  if (ev && ev.isAllDayEvent() !== !!op.allDay) { ev.deleteEvent(); ev = null; }
  if (op.allDay) {
    if (ev) ev.setAllDayDates(day_(op.start), day_(op.end));
    else ev = cal.createAllDayEvent(op.title, day_(op.start), day_(op.end));
  } else {
    if (ev) ev.setTime(new Date(op.start), new Date(op.end));
    else ev = cal.createEvent(op.title, new Date(op.start), new Date(op.end));
  }
  ev.setTitle(op.title);
  ev.setDescription(op.description || '');
  ev.setTag(TAG, String(op.ref));
  return { ref: op.ref, ok: true, gid: ev.getId() };
}

function out_(o) { return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON); }
"""


def script_text(key):
    return SCRIPT.replace("__KEY__", key).replace("__VERSION__", str(SCRIPT_VERSION))


REMOTE_ERRORS = {
    "bad_key": "비밀 키가 맞지 않습니다. 설정에서 만든 최신 스크립트를 붙여 넣고 '새 버전'으로 다시 배포하세요",
    "bad_json": "스크립트가 요청을 읽지 못했습니다",
    "no_calendar": "스크립트의 CALENDAR_ID 에 맞는 캘린더가 없습니다",
}


class Sync:
    """가져오기·올리기를 뒤에서 (켤 때와 정해진 간격마다). 실패해도 업무 데스크의 다른 동작에는 영향 없음."""

    def __init__(self, db, config, interval=60):
        self.db, self.config, self.interval = db, config, interval
        with db.lock:
            db.conn.executescript(SCHEMA)
            db.conn.commit()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.next_import = None
        self.fail = {"push": 0, "import": 0}
        self.retry_at = {"push": None, "import": None}

    # -- 상태 --
    def status(self):
        return self.db.get_meta("gcal_status") or {}

    def _set_status(self, part, **kw):
        with self.db.lock:
            st = self.status()
            st[part] = {**(st.get(part) or {}), **kw}
            self.db.set_meta("gcal_status", st)

    def _failed(self, part, msg):
        self.fail[part] += 1
        wait = BACKOFF[min(self.fail[part], len(BACKOFF)) - 1]
        self.retry_at[part] = datetime.now() + timedelta(minutes=wait)
        self._set_status(part, ok=False, error=msg, at=now_str(), retry=f"{wait}분 뒤 다시 시도")
        self.db.log(f"구글 캘린더 {'올리기' if part == 'push' else '가져오기'} 실패: {msg}", "error")

    def types(self):
        t = self.config["gcal_push_types"]
        return [x for x in (t if isinstance(t, list) else DEFAULT_TYPES) if x in TYPE_KEYS]

    # -- 가져오기 --
    def import_now(self):
        url = self.config["gcal_ical_url"]
        if not (self.config["gcal_import"] and url):
            raise GcalError("구글 캘린더 가져오기가 꺼져 있거나 주소가 없습니다")
        with self.lock:
            try:
                body = _fetch(url)
                text = body.decode("utf-8", "replace")
                if "BEGIN:VCALENDAR" not in text[:2000]:
                    raise GcalError("달력 자료가 아닙니다. '비공개 주소(iCal 형식)'를 다시 복사해 넣으세요")
                mine = {r["gid"] for r in self.db.query("SELECT gid FROM gcal_sync WHERE gid IS NOT NULL")}
                rows = parse_ics(text, skip_uids=mine)
            except GcalError as e:
                self._failed("import", str(e))
                raise
            with self.db.lock:
                self.db.conn.execute("BEGIN IMMEDIATE")
                try:
                    self.db.conn.execute("DELETE FROM gcal_events")
                    self.db.conn.executemany(
                        "INSERT OR REPLACE INTO gcal_events(uid, date, end_date, time, text) VALUES(?,?,?,?,?)",
                        [(r["uid"], r["date"], r["end_date"], r["time"], r["text"]) for r in rows])
                    self.db.conn.commit()
                except Exception:
                    self.db.conn.rollback()
                    raise
            self.fail["import"] = 0
            self.retry_at["import"] = None
            self._set_status("import", ok=True, error="", at=now_str(), count=len(rows), retry="")
            return {"count": len(rows)}

    def clear_import(self):
        self.db.execute("DELETE FROM gcal_events")

    def events_between(self, start, end):
        if not self.config["gcal_import"]:
            return []
        return self.db.query("SELECT uid, date, end_date, time, text FROM gcal_events "
                             "WHERE date <= ? AND COALESCE(end_date, date) >= ? ORDER BY date, COALESCE(time, '99'), uid",
                             (end.isoformat(), start.isoformat()))

    # -- 올리기 --
    def _call(self, body):
        url, key = self.config["gcal_push_url"], self.config["gcal_push_key"]
        if not (url and key):
            raise GcalError("Apps Script 웹 앱 주소 또는 비밀 키가 없습니다")
        raw = _fetch(url, json.dumps({**body, "key": key}, ensure_ascii=False).encode("utf-8"), timeout=60, limit=2_000_000)
        try:
            res = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise GcalError("스크립트의 답을 읽지 못했습니다. 배포할 때 '액세스 권한이 있는 사용자'를 '모든 사용자'로 했는지 확인하세요")
        if not isinstance(res, dict):
            raise GcalError("스크립트의 답이 잘못되었습니다")
        if not res.get("ok"):
            raise GcalError(REMOTE_ERRORS.get(res.get("error"), "스크립트가 요청을 거절했습니다"))
        return res

    def ping(self):
        res = self._call({"op": "ping"})
        return {"calendar": str(res.get("calendar") or "")[:100], "version": res.get("version")}

    def plan(self, today=None):
        """보낼 일: (upsert 목록, delete 목록). 업무 데스크 DB 와 올린 기록을 비교."""
        today = today or date.today()
        types = set(self.types())
        names = [s["name"] for s in self.db.students()] if self.config["gcal_mask"] else None
        rows = self.db.query(
            "SELECT e.id, e.item_id, e.date, e.end_date, e.time, e.kind, e.category, e.text, e.done, "
            "CASE WHEN i.source = 'school' THEN NULL ELSE i.title END AS item_title, i.source AS item_source, "
            "i.status AS item_status, i.registered AS item_registered "
            "FROM events e LEFT JOIN items i ON i.id = e.item_id")
        synced = {r["event_id"]: r for r in self.db.query("SELECT * FROM gcal_sync")}
        lo, hi = (today - timedelta(days=PAST_DAYS)).isoformat(), (today + timedelta(days=FUTURE_DAYS)).isoformat()
        ups, keep = [], set()
        for e in rows:
            if e["item_id"] is not None and (e["item_status"] == "archived" or not e["item_registered"]):
                continue   # 수신함에서 체크하지 않은(후보) 일정·보관한 항목은 올리지 않음
            if not (event_tags(e) & types):
                continue
            mapped = synced.get(e["id"])
            if not mapped and not (lo <= (e["end_date"] or e["date"]) and e["date"] <= hi):
                continue
            keep.add(e["id"])
            p = payload(e, names)
            h = payload_hash(p)
            if not mapped or mapped["hash"] != h or not mapped["gid"]:
                ups.append((e["id"], {"op": "upsert", "gid": (mapped or {}).get("gid"), **p}, h))
        dels = [(eid, {"op": "delete", "ref": f"desk-{eid}", "gid": r["gid"]})
                for eid, r in synced.items() if eid not in keep]
        return ups, dels

    def pending(self):
        try:
            ups, dels = self.plan()
            return len(ups) + len(dels)
        except Exception:
            return None

    def push_now(self):
        if not (self.config["gcal_push"] and self.config["gcal_push_url"] and self.config["gcal_push_key"]):
            raise GcalError("구글 캘린더 올리기가 꺼져 있거나 연결이 끝나지 않았습니다")
        with self.lock:
            ups, dels = self.plan()
            work = (dels + ups)[:MAX_OPS]
            sent = errors = 0
            last_err = ""
            try:
                for i in range(0, len(work), CHUNK):
                    part = work[i:i + CHUNK]
                    res = self._call({"op": "batch", "ops": [op for _, op, *_ in part]})
                    results = {r.get("ref"): r for r in res.get("results") or [] if isinstance(r, dict)}
                    for w in part:
                        eid, op = w[0], w[1]
                        r = results.get(op["ref"]) or {}
                        if not r.get("ok"):
                            errors += 1
                            last_err = "일부 일정을 구글에 쓰지 못했습니다"
                            continue
                        sent += 1
                        if op["op"] == "delete":
                            self.db.execute("DELETE FROM gcal_sync WHERE event_id = ?", (eid,))
                        else:
                            self.db.execute(
                                "INSERT INTO gcal_sync(event_id, gid, hash, updated_at) VALUES(?,?,?,?) "
                                "ON CONFLICT(event_id) DO UPDATE SET gid = excluded.gid, hash = excluded.hash, "
                                "updated_at = excluded.updated_at",
                                (eid, str(r.get("gid") or op.get("gid") or "")[:300] or None, w[2], now_str()))
            except GcalError as e:
                if sent:
                    self.db.log(f"구글 캘린더: {sent}건 반영 뒤 멈춤")
                self._failed("push", str(e))
                raise
            left = len(ups) + len(dels) - sent
            if errors:
                self._failed("push", last_err)
            else:
                self.fail["push"] = 0
                self.retry_at["push"] = None
                self._set_status("push", ok=True, error="", at=now_str(), retry="")
            self._set_status("push", sent=sent, pending=left)
            if sent:
                self.db.log(f"구글 캘린더에 일정 {sent}건 반영" + (f" (남은 것 {left}건)" if left else ""))
            return {"sent": sent, "errors": errors, "pending": left}

    def new_key(self):
        key = secrets.token_urlsafe(24)
        self.config.set_value("gcal_push_key", key)
        return key

    # -- 뒤에서 돌기 --
    def tick(self, now=None):
        now = now or datetime.now()
        cfg = self.config
        if cfg["gcal_import"] and cfg["gcal_ical_url"]:
            ra = self.retry_at["import"]
            if (ra is None or now >= ra) and (self.next_import is None or now >= self.next_import):
                self.next_import = now + timedelta(minutes=max(5, int(cfg["gcal_import_minutes"] or 30)))
                try:
                    self.import_now()
                except GcalError:
                    pass
        if cfg["gcal_push"] and cfg["gcal_push_url"] and cfg["gcal_push_key"]:
            ra = self.retry_at["push"]
            if ra is None or now >= ra:
                ups, dels = self.plan()
                if ups or dels:
                    try:
                        self.push_now()
                    except GcalError:
                        pass

    def loop(self):
        self.stop_event.wait(15)   # 켠 직후 다른 일(수집·텔레그램)이 먼저 자리 잡게
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as e:   # 뒤의 반복이 멈추지 않게 기록만
                self.db.log(f"구글 캘린더 동기화 오류: {type(e).__name__}", "error")
            self.wake.wait(self.interval)
            self.wake.clear()

    def poke(self):
        """일정이 바뀌었을 때 곧 보내도록 깨움."""
        self.wake.set()

    def start(self):
        self.thread = threading.Thread(target=self.loop, name="gcal", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
