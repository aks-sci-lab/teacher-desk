"""학교 공통 설정 파일 (0.5.92): 다른 선생님께 업무 데스크를 나눠 줄 때 학교 공통값만 한 파일로 주고받기.

파일(`학교이름-공통설정.deskshare.json`)에 담는 것 — 학교 공통값만, **고른 것만**:
- school: 학교 이름, 나이스 학교 정보(교육청·학교 코드·이름, 인증키는 빼고)
- periods: 교시 시간 (수업 알림 켜기는 선생님마다 달라서 뺌)
- schoolcal: 학사일정 (학년도별, '학사일정' 항목의 일정 줄만)
- timetable: 전체 시간표(교사별 수업, 학급 구성은 여기서 나옴)

**절대 담지 않음**: 학생 명단·학부모 연락처, 출결·상담·학급 자료, 쪽지·공문·수신함, 모든 비밀값
(텔레그램 토큰·나이스 인증키·AI 키·구글 캘린더 주소·키·연결 키), 쿨메신저 이름·경로·폴더, 담임 이름·학년·반.
담을 값은 아래 '허용 목록'으로만 골라 만들고(설정 전체를 복사하지 않음), 불러올 때도 같은 목록과 형식만 받아
모르는 키·금지 항목은 무시합니다.
"""

import json
import re
from datetime import date, datetime

from . import VERSION, extract, schoolcal, timetable

FORMAT = "teacher-desk-school-share"
FORMAT_VERSION = 1
MAX_BYTES = 5 * 1024 * 1024
PARTS = ("school", "periods", "schoolcal", "timetable")
PART_LABELS = {
    "school": "학교 이름·나이스 학교 정보 (인증키 제외)",
    "periods": "교시 시간",
    "schoolcal": "학사일정",
    "timetable": "전체 시간표 (교사별 수업·학급 구성)",
}
# 화면에 '담기지 않는 것' 으로 보여 줄 목록
NEVER = [
    "학생 명단·학부모 연락처",
    "출결(조퇴·외출·결석계)·상담·학급 자료",
    "쿨메신저 쪽지·에듀파인 공문·수신함",
    "비밀값: 텔레그램 토큰·나이스 인증키·AI 키·구글 캘린더 주소·키·브라우저 연결 키",
    "선생님 개인 값: 담임 이름·학년·반·쿨메신저 이름·경로·폴더",
]
SCHOOL_TITLE_RE = re.compile(r"^(\d{4})학년도 학사일정")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SLOT_RE = re.compile(r"^([0-5])-(\d{1,2})$")
CATEGORIES = set(extract.CATEGORIES)


class ShareError(Exception):
    pass


def _s(v, limit):
    """글자 칸: 글자가 아니면 빈칸, 줄바꿈·제어 문자는 띄어쓰기로, 길이 제한."""
    if not isinstance(v, str):
        return ""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", v).strip()[:limit]


# ---- 지금 이 PC 에 있는 것 ----
def _school_years(db):
    """학사일정 항목 {학년도: item_id} (같은 학년도는 가장 최근 것)."""
    out = {}
    for r in db.query("SELECT id, title FROM items WHERE source = 'school' ORDER BY id"):
        m = SCHOOL_TITLE_RE.match(r["title"] or "")
        if m:
            out[int(m.group(1))] = r["id"]
    return out


def _school_events(db, item_id):
    return [{"date": e["date"], "end_date": e["end_date"], "text": e["text"], "category": e["category"]}
            for e in db.query("SELECT date, end_date, text, category FROM events WHERE item_id = ? ORDER BY date, id", (item_id,))]


def status(db, config):
    """각 항목이 이 PC 에 있는지와 개수 (내보내기 목록·도우미 표시용)."""
    tt = db.get_meta("timetable")
    periods = db.get_meta("timetable_periods") or {}
    years = _school_years(db)
    sch = config["neis_school"] or {}
    return {
        "school": {"has": bool((config["school_name"] or "").strip() or sch.get("code")),
                   "name": config["school_name"] or "", "neis": sch.get("name") or ""},
        "periods": {"has": bool(periods), "count": len(periods)},
        "schoolcal": {"has": bool(years), "years": sorted(years),
                      "count": sum(len(_school_events(db, i)) for i in years.values())},
        "timetable": {"has": bool(tt), "teachers": len(tt["teachers"]) if tt else 0,
                      "classes": len(timetable.class_list(tt)) if tt else 0},
        "labels": PART_LABELS, "never": NEVER,
    }


# ---- 내보내기 ----
def build(db, config, parts):
    """고른 항목만 허용 목록으로 담은 파일 내용(dict)."""
    parts = [p for p in PARTS if p in (parts or [])]
    if not parts:
        raise ShareError("담을 항목을 하나 이상 고르세요")
    out = {"format": FORMAT, "version": FORMAT_VERSION, "app_version": VERSION,
           "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "school_name": _s(config["school_name"], 80), "parts": {}}
    p = out["parts"]
    if "school" in parts:
        sch = config["neis_school"] or {}
        p["school"] = {"name": _s(config["school_name"], 80),
                       "neis_school": {k: _s(sch.get(k), 80) for k in ("name", "atpt", "code", "office")} if sch.get("code") else None}
    if "periods" in parts:
        periods = db.get_meta("timetable_periods") or {}
        if not periods:
            raise ShareError("교시 시간이 없습니다 (시간표 → 교시 시간에서 넣으세요)")
        p["periods"] = {k: {"start": v.get("start"), "end": v.get("end")} for k, v in periods.items()}
    if "schoolcal" in parts:
        years = _school_years(db)
        if not years:
            raise ShareError("학사일정이 없습니다 (달력 → 학사일정 가져오기)")
        p["schoolcal"] = [{"school_year": y, "events": _school_events(db, i)} for y, i in sorted(years.items())]
    if "timetable" in parts:
        tt = db.get_meta("timetable")
        if not tt:
            raise ShareError("시간표가 없습니다 (시간표 → 시간표 엑셀 불러오기)")
        p["timetable"] = {"days": tt["days"], "slots": tt["slots"], "lock_words": tt.get("lock_words") or [],
                          "file": _s(tt.get("file"), 200),
                          "teachers": [{"id": t["id"], "name": t["name"], "dept": t.get("dept") or "",
                                        "lessons": {k: {f: l.get(f) for f in ("id", "cls", "subj", "locked", "why", "color")}
                                                    for k, l in t["lessons"].items()}} for t in tt["teachers"]]}
    return out


def filename(config):
    name = re.sub(r'[\\/:*?"<>|\s]+', "", config["school_name"] or "") or "학교"
    return f"{name[:40]}-공통설정.deskshare.json"


# ---- 불러오기: 검사 ----
def _periods(v):
    if not isinstance(v, dict) or len(v) > 12:
        raise ShareError("교시 시간 형식이 잘못되었습니다")
    out = {}
    for k, t in v.items():
        if not (isinstance(k, str) and k.isdigit() and 1 <= int(k) <= 12 and isinstance(t, dict)):
            raise ShareError("교시 시간 형식이 잘못되었습니다")
        start, end = t.get("start"), t.get("end")
        if not (isinstance(start, str) and TIME_RE.match(start)):
            raise ShareError(f"{k}교시 시작 시각이 잘못되었습니다")
        if end not in (None, "") and not (isinstance(end, str) and TIME_RE.match(end) and end > start):
            raise ShareError(f"{k}교시 끝 시각이 잘못되었습니다")
        out[str(int(k))] = {"start": start, "end": end or None}
    return out


def _date(v):
    if not (isinstance(v, str) and DATE_RE.match(v)):
        raise ShareError("학사일정 날짜가 잘못되었습니다")
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ShareError("학사일정 날짜가 달력에 없습니다")
    return v


def _schoolcal(v):
    if not isinstance(v, list) or not 0 < len(v) <= 5:
        raise ShareError("학사일정 형식이 잘못되었습니다")
    out = []
    for y in v:
        if not isinstance(y, dict) or not isinstance(y.get("school_year"), int) or not 2000 <= y["school_year"] <= 2100:
            raise ShareError("학사일정 학년도가 잘못되었습니다")
        evs = y.get("events")
        if not isinstance(evs, list) or len(evs) > 2000:
            raise ShareError("학사일정 일정이 너무 많거나 형식이 잘못되었습니다")
        clean = []
        for e in evs:
            if not isinstance(e, dict):
                raise ShareError("학사일정 일정 형식이 잘못되었습니다")
            text = _s(e.get("text"), 200)
            if not text:
                continue
            end = _date(e["end_date"]) if e.get("end_date") else None
            clean.append({"date": _date(e.get("date")), "end_date": end, "time": None, "kind": "event",
                          "category": e.get("category") if e.get("category") in CATEGORIES else schoolcal.CATEGORY, "text": text})
        if clean:
            out.append({"school_year": y["school_year"], "events": clean})
    if not out:
        raise ShareError("학사일정에 일정이 없습니다")
    return out


def _timetable(v):
    if not isinstance(v, dict):
        raise ShareError("시간표 형식이 잘못되었습니다")
    days = v.get("days")
    if not (isinstance(days, list) and 0 < len(days) <= len(timetable.DAYNAMES) and days == timetable.DAYNAMES[:len(days)]):
        raise ShareError("시간표 요일이 잘못되었습니다")
    slots = v.get("slots")
    if not isinstance(slots, list) or not 0 < len(slots) <= 6 * 12:
        raise ShareError("시간표 교시 칸이 잘못되었습니다")
    clean_slots, keys = [], set()
    for s in slots:
        m = SLOT_RE.match(s.get("key", "")) if isinstance(s, dict) and isinstance(s.get("key"), str) else None
        if not m or s.get("d") != int(m.group(1)) or s.get("p") != int(m.group(2)) or not 1 <= s["p"] <= 12 or s["d"] >= len(days):
            raise ShareError("시간표 교시 칸이 잘못되었습니다")
        clean_slots.append({"key": s["key"], "d": s["d"], "p": s["p"]})
        keys.add(s["key"])
    teachers = v.get("teachers")
    if not isinstance(teachers, list) or not 0 < len(teachers) <= 400:
        raise ShareError("시간표 선생님 목록이 잘못되었습니다")
    clean_t, lid = [], 0
    for t in teachers:
        if not isinstance(t, dict) or not isinstance(t.get("lessons"), dict) or len(t["lessons"]) > len(keys):
            raise ShareError("시간표 선생님 수업이 잘못되었습니다")
        name = _s(t.get("name"), 40)
        if not name:
            raise ShareError("시간표에 이름 없는 선생님이 있습니다")
        lessons = {}
        for k, l in t["lessons"].items():
            if k not in keys or not isinstance(l, dict):
                raise ShareError("시간표 수업 칸이 잘못되었습니다")
            lessons[k] = {"id": lid, "cls": _s(l.get("cls"), 20), "subj": _s(l.get("subj"), 60), "locked": l.get("locked") is True,
                          "why": _s(l.get("why"), 20), "color": _s(l.get("color"), 20) or None}
            lid += 1
        clean_t.append({"id": len(clean_t), "name": name, "dept": _s(t.get("dept"), 40), "lessons": lessons})
    words = v.get("lock_words")
    words = [_s(w, 20) for w in words[:30] if _s(w, 20)] if isinstance(words, list) else list(timetable.LOCK_WORDS)
    return {"days": list(days), "slots": clean_slots, "teachers": clean_t, "lock_words": words, "file": _s(v.get("file"), 200)}


def _school(v):
    if not isinstance(v, dict):
        raise ShareError("학교 정보 형식이 잘못되었습니다")
    sch = v.get("neis_school")
    clean = None
    if isinstance(sch, dict) and _s(sch.get("code"), 80) and _s(sch.get("atpt"), 80):
        clean = {k: _s(sch.get(k), 80) for k in ("name", "atpt", "code", "office")}
    name = _s(v.get("name"), 80)
    if not name and not clean:
        raise ShareError("학교 정보가 비어 있습니다")
    return {"name": name, "neis_school": clean}


CHECKERS = {"school": _school, "periods": _periods, "schoolcal": _schoolcal, "timetable": _timetable}


def parse(text):
    """파일 글자 → 검사한 내용 {format, version, school_name, created_at, parts}. 잘못되면 ShareError."""
    if not isinstance(text, str) or not text.strip():
        raise ShareError("빈 파일입니다")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ShareError("파일이 너무 큽니다 (5MB 까지)")
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        raise ShareError("학교 공통 설정 파일이 아닙니다 (JSON 형식이 아님)")
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ShareError("학교 공통 설정 파일이 아닙니다")
    ver = data.get("version")
    if not isinstance(ver, int) or isinstance(ver, bool) or ver < 1:
        raise ShareError("파일 버전이 잘못되었습니다")
    if ver > FORMAT_VERSION:
        raise ShareError("더 새 버전의 업무 데스크에서 만든 파일입니다. 업무 데스크를 업데이트한 뒤 불러오세요")
    raw = data.get("parts")
    if not isinstance(raw, dict):
        raise ShareError("파일에 담긴 항목이 없습니다")
    parts = {}
    for key in PARTS:   # 허용 목록에 있는 것만 — 모르는 키·금지 항목은 그냥 무시
        if raw.get(key) is not None:
            parts[key] = CHECKERS[key](raw[key])
    if not parts:
        raise ShareError("파일에 불러올 항목이 없습니다")
    return {"format": FORMAT, "version": ver, "school_name": _s(data.get("school_name"), 80),
            "created_at": _s(data.get("created_at"), 20), "app_version": _s(data.get("app_version"), 20), "parts": parts}


def preview(db, config, text):
    """불러오기 전 미리보기: 항목마다 무엇이 채워지는지, 이미 값이 있는지."""
    share = parse(text)
    cur = status(db, config)
    items = []
    for key, v in share["parts"].items():
        if key == "school":
            what = " · ".join(x for x in (v["name"], v["neis_school"] and f"나이스: {v['neis_school']['name'] or v['neis_school']['code']}") if x)
            now = cur["school"]["name"] or cur["school"]["neis"]
        elif key == "periods":
            what = f"{len(v)}교시 ({v[min(v, key=int)]['start']}부터)"
            now = cur["periods"]["has"] and f"{cur['periods']['count']}교시"
        elif key == "schoolcal":
            what = ", ".join(f"{y['school_year']}학년도 {len(y['events'])}건" for y in v)
            exist = [y["school_year"] for y in v if y["school_year"] in cur["schoolcal"]["years"]]
            now = exist and ", ".join(f"{y}학년도" for y in exist)
        else:
            what = f"선생님 {len(v['teachers'])}명 · 학급 {len(timetable.class_list(v))}개"
            now = cur["timetable"]["has"] and f"선생님 {cur['timetable']['teachers']}명"
        items.append({"key": key, "label": PART_LABELS[key], "what": what, "exists": bool(now), "now": now or ""})
    return {"school_name": share["school_name"], "created_at": share["created_at"], "items": items}


# ---- 불러오기: 적용 ----
def apply(db, config, text, parts, overwrite):
    """고른 항목만 적용. 이미 값이 있는 항목은 overwrite 에 있을 때만 바꿈. {applied, skipped}."""
    share = parse(text)
    cur = status(db, config)
    applied, skipped = [], []
    for key in PARTS:
        if key not in share["parts"] or key not in (parts or []):
            continue
        v = share["parts"][key]
        if key == "school":
            changes = {}
            if v["name"] and (key in overwrite or not (config["school_name"] or "").strip()):
                changes["school_name"] = v["name"]
            if v["neis_school"] and (key in overwrite or not (config["neis_school"] or {}).get("code")):
                changes["neis_school"] = v["neis_school"]
            if not changes:
                skipped.append(key)
                continue
            config.update(changes)
        elif key == "periods":
            if cur["periods"]["has"] and key not in overwrite:
                skipped.append(key)
                continue
            db.set_meta("timetable_periods", v)
            if not db.get_meta("timetable_bell"):
                db.set_meta("timetable_bell", {"enabled": False, "lead": 5})   # 수업 알림은 선생님이 직접 켬
        elif key == "schoolcal":
            done = False
            for y in v:
                if y["school_year"] in cur["schoolcal"]["years"] and key not in overwrite:
                    continue
                schoolcal.save(db, y["school_year"], y["events"], "학교 공통 설정 파일")
                done = True
            if not done:
                skipped.append(key)
                continue
        elif key == "timetable":
            if cur["timetable"]["has"] and key not in overwrite:
                skipped.append(key)
                continue
            store_timetable(db, config, {**v, "file": v["file"] or "학교 공통 설정 파일", "sheets": [],
                                         "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        applied.append(key)
    if applied:
        db.log("학교 공통 설정 불러옴: " + ", ".join(PART_LABELS[k] for k in applied))
    return {"applied": applied, "skipped": skipped}


def store_timetable(db, config, tt):
    """시간표 저장 — 시간표 엑셀 불러오기(server.timetable_import)와 같은 뒤처리: 교체 기록 비우기, 내 이름 찾기."""
    db.set_meta("timetable", tt)
    for sw in db.get_meta("timetable_swaps") or []:
        for eid in sw.get("event_ids") or []:
            db.execute("DELETE FROM events WHERE id = ?", (eid,))
    db.set_meta("timetable_swaps", [])
    db.set_meta("timetable_me", timetable.find_teacher(tt, config["teacher_name"]))


def find_me(db, config):
    """시간표에서 '나'를 아직 못 정했으면 담임 이름으로 찾음 (직접 고른 것은 그대로)."""
    tt = db.get_meta("timetable")
    if tt and db.get_meta("timetable_me") is None:
        me = timetable.find_teacher(tt, config["teacher_name"])
        if me is not None:
            db.set_meta("timetable_me", me)
