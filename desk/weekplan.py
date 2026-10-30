"""주간 계획 (읽기 전용, 0.5.48): 한 주·두 주의 수업 있는 날별 일정·마감·꼭 할 일·학사일정.

내 일정(events)과 꼭 할 일(todos)만 쓴다. 조퇴·외출·결석(학생 이름)과 시간표 API 는 쓰지 않는다."""

from datetime import date, timedelta

from . import queries, timetable


def _my_grades(db):
    """내 시간표에 나오는 학년들 (시간표가 없으면 {None}: 학년을 모르는 채로 판정)."""
    tt = db.get_meta("timetable")
    me = db.get_meta("timetable_me")
    if not tt or me is None or me >= len(tt["teachers"]):
        return {None}
    grades = {timetable.grade_of(l.get("cls")) for l in tt["teachers"][me]["lessons"].values()}
    return grades or {None}


def _day_status(evs, d, grades):
    """수업 있는 날이면 None 또는 {'text'} (확인 필요), 수업 없는 날이면 {'off': True, 'text'}."""
    notes = [timetable.day_note(evs, d, g) for g in grades]
    if all(n and n["level"] == "off" for n in notes):
        return {"off": True, "text": notes[0]["text"]}
    hit = next((n for n in notes if n), None)
    return {"off": False, "text": hit["text"]} if hit else None


def week_plan(db, start, days):
    """start 가 든 주의 월요일부터 days(7|14)일. 월~금만 칸으로, 수업 없는 날과 토·일은 `other` 로."""
    mon = timetable.monday(start)
    end = mon + timedelta(days=days - 1)
    evs = queries.school_day_events(db, mon, end)
    grades = _my_grades(db)
    cols = {}      # 수업 있는 날 칸
    hidden = {}    # 칸에서 뺀 날 (수업 없는 날·토·일): 그날 일정이 사라지지 않게 따로 모음
    for i in range(days):
        d = mon + timedelta(days=i)
        key = d.isoformat()
        st = _day_status(evs, d, grades) if d.weekday() < 5 else None
        if d.weekday() < 5 and not (st and st["off"]):
            cols[key] = {"date": key, "check": st["text"] if st else None, "school": evs.get(key, []),
                         "events": [], "todos": []}
        else:
            hidden[key] = {"date": key, "reason": st["text"] if st else None, "school": evs.get(key, []),
                           "events": [], "todos": []}

    def put(key, kind, row):
        (cols.get(key) or hidden[key])[kind].append(row)

    for e in queries.events_between(db, mon, end):
        if e["item_source"] == "school" or e["category"] == "학사":
            continue   # 학사일정은 위 school 줄로
        last = date.fromisoformat(e["end_date"] or e["date"])
        if e["kind"] == "deadline":
            span = [min(last, end)] if last >= mon else []   # 마감은 끝나는 날에만 (D-day)
        else:
            span = [mon + timedelta(days=n) for n in range(days)
                    if date.fromisoformat(e["date"]) <= mon + timedelta(days=n) <= last]
        for d in span:
            put(d.isoformat(), "events", e)

    undated, overdue = [], []
    today = date.today()
    for t in db.query("SELECT * FROM todos WHERE done = 0 ORDER BY sort IS NULL, sort, id"):
        row = {k: t[k] for k in ("id", "text", "date", "time")}
        if not t["date"]:
            undated.append(row)
        elif mon.isoformat() <= t["date"] <= end.isoformat():
            put(t["date"], "todos", row)
        elif t["date"] < mon.isoformat() and mon <= today <= end:
            overdue.append(row)   # 이번 주를 보고 있을 때만 '지난 할 일'
    return {"start": mon.isoformat(), "end": end.isoformat(), "days": days,
            "cols": list(cols.values()), "other": [h for h in hidden.values() if h["events"] or h["todos"] or h["reason"]],
            "undated": undated, "overdue": overdue, "today": today.isoformat()}
