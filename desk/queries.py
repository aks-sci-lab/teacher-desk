"""화면(대시보드·검색)과 텔레그램이 함께 쓰는 조회."""

from datetime import date, timedelta

from .db import decode_item, now_str

EVENT_COLS = ("e.id, e.item_id, e.date, e.end_date, e.time, e.kind, e.category, e.text, e.done, "
              # 학사일정은 묶음 제목 대신 행사 이름(e.text)이 보이게
              "CASE WHEN i.source = 'school' THEN NULL ELSE i.title END AS item_title, i.source AS item_source")
# 달력·브리핑·텔레그램에는 수신함에서 체크한(등록한) 항목의 일정과 따로 넣은 일정만
REGISTERED = "(i.id IS NULL OR i.registered = 1)"


def events_between(db, start, end, include_done=True):
    """start~end 와 겹치는 일정 (기간 일정은 끝 날짜까지 포함)."""
    sql = (f"SELECT {EVENT_COLS} FROM events e LEFT JOIN items i ON i.id = e.item_id "
           "WHERE e.date <= ? AND COALESCE(e.end_date, e.date) >= ? "
           f"AND (i.status IS NULL OR i.status != 'archived') AND {REGISTERED}")
    if not include_done:
        sql += " AND e.done = 0"
    return db.query(sql + " ORDER BY e.date, COALESCE(e.time, '99'), e.id", (end.isoformat(), start.isoformat()))


def today_summary(db, today=None):
    today = today or date.today()
    week_end = today + timedelta(days=7)
    overdue = db.query(
        f"SELECT {EVENT_COLS} FROM events e LEFT JOIN items i ON i.id = e.item_id "
        "WHERE e.kind = 'deadline' AND e.done = 0 AND e.date < ? AND e.date >= ? "
        f"AND (i.status IS NULL OR i.status NOT IN ('archived', 'done')) AND {REGISTERED} ORDER BY e.date",
        (today.isoformat(), (today - timedelta(days=14)).isoformat()))
    todays = events_between(db, today, today)
    upcoming = events_between(db, today + timedelta(days=1), week_end, include_done=False)
    week = [e for e in upcoming if e["kind"] == "deadline"]
    week_events = [e for e in upcoming if e["kind"] != "deadline"]
    passes = db.query("SELECT * FROM passes WHERE date = ? AND status != 'cancelled' ORDER BY time_out, id",
                      (today.isoformat(),))
    new_items = [decode_item(r) for r in db.query(
        "SELECT * FROM items WHERE status = 'new' ORDER BY received_at DESC LIMIT 10")]
    last_run = db.one("SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1")
    return {"date": today.isoformat(), "overdue": overdue, "today": todays, "week_deadlines": week,
            "week_events": week_events,
            "passes": passes, "new_items": new_items, "last_run": last_run}


def school_day_events(db, start, end):
    """학사일정(및 '학사' 분류) 일정 글을 날짜별로 {'YYYY-MM-DD': [글...]} — 시간표 교체에서 막을 날을 찾는 데 씀."""
    rows = db.query("SELECT e.date, COALESCE(e.end_date, e.date) AS end_date, e.text FROM events e "
                    "LEFT JOIN items i ON i.id = e.item_id "
                    "WHERE (i.source = 'school' OR e.category = '학사') AND e.date <= ? AND COALESCE(e.end_date, e.date) >= ?",
                    (end.isoformat(), start.isoformat()))
    out = {}
    for r in rows:
        d = max(date.fromisoformat(r["date"]), start)
        last = min(date.fromisoformat(r["end_date"]), end)
        while d <= last:
            out.setdefault(d.isoformat(), []).append(r["text"] or "")
            d += timedelta(days=1)
    return out


def my_lessons(db, config, day):
    """내 시간표에서 그날 수업 (불러온 시간표가 없거나 내 이름을 모르면 None)."""
    from . import timetable
    tt = db.get_meta("timetable")
    me = db.get_meta("timetable_me")
    if not tt or me is None or me >= len(tt["teachers"]):
        return None
    return timetable.lessons_on(tt, me, day, db.get_meta("timetable_swaps") or [])


def issue_pass(db, f):
    """조퇴증·외출증 발급 기록 (화면·텔레그램 공용). f 는 검사한 칸들. 발급 번호는 날짜-순번."""
    f = dict(f)
    with db.lock:
        n = db.one("SELECT COUNT(*) AS n FROM passes WHERE date = ?", (f["date"],))["n"] + 1
        f["serial"] = f["date"].replace("-", "") + f"-{n:02d}"
        f["created_at"] = now_str()
        pid = db.execute(f"INSERT INTO passes({', '.join(f)}) VALUES({', '.join('?' * len(f))})", tuple(f.values()))
    db.log(f"{'조퇴증' if f['kind'] == 'early' else '외출증'} 발급 {f['serial']}")
    return db.one("SELECT * FROM passes WHERE id = ?", (pid,))


def _terms_where(fields, terms):
    """검색어를 띄어쓰기로 나눠, 낱말마다 어느 칸에든 들어 있으면 (모든 낱말 AND)."""
    parts, args = [], []
    for t in terms:
        like = "%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        parts.append("(" + " OR ".join(f"COALESCE({f}, '') LIKE ? ESCAPE '\\'" for f in fields) + ")")
        args += [like] * len(fields)
    return " AND ".join(parts), args


def search(db, q, limit=30):
    """모든 자료에서 찾기: 수신 항목(제목·본문·요약·문서번호·분류·파일), 일정, 조퇴·외출, 학생 명단."""
    terms = q.split()[:5]
    if not terms:
        return {"items": [], "events": [], "passes": [], "students": []}
    w, a = _terms_where(["title", "body", "summary", "doc_numbers", "category", "file_path"], terms)
    items = db.query("SELECT id, source, title, received_at, category, status FROM items "
                     f"WHERE {w} ORDER BY received_at DESC LIMIT ?", (*a, limit))
    w, a = _terms_where(["e.text", "e.category", "e.date", "i.title"], terms)
    events = db.query(f"SELECT {EVENT_COLS} FROM events e LEFT JOIN items i ON i.id = e.item_id "
                      f"WHERE {w} AND {REGISTERED} ORDER BY e.date DESC LIMIT ?", (*a, limit))
    w, a = _terms_where(["student_name", "CAST(student_number AS TEXT)", "reason", "note", "serial", "date",
                         "CASE kind WHEN 'out' THEN '외출' ELSE '조퇴' END"], terms)
    passes = db.query(f"SELECT * FROM passes WHERE {w} ORDER BY date DESC, id DESC LIMIT ?", (*a, limit))
    w, a = _terms_where(["name", "CAST(number AS TEXT)"], terms)
    students = db.query(f"SELECT * FROM students WHERE {w} ORDER BY number LIMIT ?", (*a, limit))
    return {"items": items, "events": events, "passes": passes, "students": students}
