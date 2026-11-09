"""학급 제출물 확인·상담 기록 (0.5.82).

제출물: 가정통신문 회신·서류 등 항목마다 학생별 제출·면제 표시(표시가 없으면 미제출), 마감·미제출 목록.
상담 기록: 학생·학부모 상담(날짜·방법·상대·주제·내용·후속 조치). **특히 민감한 자료**라
- 이 PC 의 data/desk.db 에만 두고, 검색·브리핑·AI·텔레그램·수집에 쓰지 않습니다 (화면에서만).
- 기록(로그)에는 이름·내용을 남기지 않습니다.
- 학생 이름은 기록할 때의 이름을 함께 저장합니다 (해가 바뀌어 명단이 바뀌어도 기록이 남게).
"""

from datetime import date

from .classroom import need, numbers_of, roster, _date, _text
from .db import now_str

MARKS = ("done", "exempt")                 # 제출 / 면제 (표시 없음 = 미제출)
METHODS = ("대면", "전화", "문자", "온라인", "기타")
WITH = ("학생", "학부모", "학생·학부모", "기타")


def _int(v, name):
    need(isinstance(v, int) and not isinstance(v, bool), f"{name}이(가) 잘못되었습니다")
    return v


# ---------------------------------------------------------------- 제출물
def _task(db, tid):
    t = db.one("SELECT * FROM class_tasks WHERE id = ?", (int(tid),))
    need(t, "없는 제출물 항목입니다")
    return t


def task_fields(body, partial=False):
    f = {}
    if "title" in body or not partial:
        f["title"] = _text(body.get("title"), "항목 이름", 60)
    if "due" in body:
        f["due"] = _date(body["due"], "마감 날짜") if body["due"] else None
    if "note" in body:
        f["note"] = _text(body.get("note") or "", "메모", 300, required=False)
    if "closed" in body:
        f["closed"] = int(bool(body["closed"]))
    return f


def marks_of(db, tid):
    return {r["number"]: {"status": r["status"], "at": r["at"]}
            for r in db.query("SELECT number, status, at FROM class_task_marks WHERE task_id = ?", (tid,))}


def task_summary(db, t, nums):
    marks = marks_of(db, t["id"])
    done = [n for n in nums if marks.get(n, {}).get("status") == "done"]
    exempt = [n for n in nums if marks.get(n, {}).get("status") == "exempt"]
    missing = [n for n in nums if n not in marks]
    late = [n for n in done if t["due"] and marks[n]["at"][:10] > t["due"]]
    return {**t, "closed": bool(t["closed"]), "done": len(done), "exempt": len(exempt), "missing": missing, "late": late,
            "total": len(nums)}


def tasks_state(db, include_closed=False):
    nums = numbers_of(db)
    rows = db.query("SELECT * FROM class_tasks " + ("" if include_closed else "WHERE closed = 0 ")
                    + "ORDER BY closed, due IS NULL, due, id DESC")
    tasks = [task_summary(db, t, nums) for t in rows]
    return {"tasks": tasks, "marks": {t["id"]: marks_of(db, t["id"]) for t in rows}, "students": roster(db),
            "closed_count": db.one("SELECT COUNT(*) AS n FROM class_tasks WHERE closed = 1")["n"]}


def task_create(db, body):
    f = task_fields(body)
    f.setdefault("due", None)
    f.setdefault("note", "")
    tid = db.execute("INSERT INTO class_tasks(title, due, note, closed, created_at) VALUES(?, ?, ?, 0, ?)",
                     (f["title"], f["due"], f["note"], now_str()))
    return task_summary(db, _task(db, tid), numbers_of(db))


def task_update(db, tid, body):
    t = _task(db, tid)
    f = task_fields(body, partial=True)
    if f:
        db.execute(f"UPDATE class_tasks SET {', '.join(k + ' = ?' for k in f)} WHERE id = ?", (*f.values(), t["id"]))
    return task_summary(db, _task(db, t["id"]), numbers_of(db))


def task_delete(db, tid):
    t = _task(db, tid)
    with db.lock, db.conn:
        db.conn.execute("DELETE FROM class_task_marks WHERE task_id = ?", (t["id"],))
        db.conn.execute("DELETE FROM class_tasks WHERE id = ?", (t["id"],))
    return {"ok": True}


def task_mark(db, tid, body):
    """{numbers: [..], status: 'done'|'exempt'|null}  null 이면 미제출로 되돌림."""
    t = _task(db, tid)
    status = body.get("status")
    need(status is None or status in MARKS, "제출 표시가 잘못되었습니다")
    nums = body.get("numbers")
    need(isinstance(nums, list) and 0 < len(nums) <= 100, "학생 번호가 잘못되었습니다")
    valid = set(numbers_of(db))
    nums = [_int(n, "학생 번호") for n in nums]
    need(all(n in valid for n in nums), "학생 명단에 없는 번호가 있습니다")
    with db.lock, db.conn:
        for n in nums:
            if status is None:
                db.conn.execute("DELETE FROM class_task_marks WHERE task_id = ? AND number = ?", (t["id"], n))
            else:
                db.conn.execute("INSERT INTO class_task_marks(task_id, number, status, at) VALUES(?, ?, ?, ?) "
                                "ON CONFLICT(task_id, number) DO UPDATE SET status = excluded.status, at = excluded.at",
                                (t["id"], n, status, now_str()))
    return task_summary(db, t, numbers_of(db))


# ---------------------------------------------------------------- 상담 기록
def counsel_fields(db, body, partial=False):
    f = {}
    if "number" in body or not partial:
        n = _int(body.get("number"), "학생 번호")
        names = {r["number"]: r["name"] for r in roster(db)}
        need(n in names, "학생 명단에 없는 번호입니다")
        f["number"], f["student_name"] = n, names[n]
    if "date" in body or not partial:
        f["date"] = _date(body.get("date") or date.today().isoformat(), "상담 날짜")
    if "time" in body:
        tm = body.get("time") or None
        need(tm is None or (isinstance(tm, str) and len(tm) == 5 and tm[2] == ":" and tm.replace(":", "").isdigit()),
             "시각 형식이 잘못되었습니다 (예: 15:30)")
        f["time"] = tm
    if "method" in body or not partial:
        need(body.get("method", "대면") in METHODS, "상담 방법이 잘못되었습니다")
        f["method"] = body.get("method", "대면")
    if "with_whom" in body or not partial:
        need(body.get("with_whom", "학생") in WITH, "상담 상대가 잘못되었습니다")
        f["with_whom"] = body.get("with_whom", "학생")
    for k, name, limit, req in (("topic", "주제", 80, True), ("content", "내용", 5000, False),
                                ("followup", "후속 조치", 1000, False)):
        if k in body or not partial:
            f[k] = _text(body.get(k) or "", name, limit, required=req)
    if "followup_date" in body:
        f["followup_date"] = _date(body["followup_date"], "후속 조치 날짜") if body["followup_date"] else None
    if "followup_done" in body:
        f["followup_done"] = int(bool(body["followup_done"]))
    if "duration" in body:   # 통화 길이(초) — 통화 내역 불러오기(다음 단계)에서 씀
        d = body.get("duration")
        need(d is None or (isinstance(d, int) and 0 <= d <= 86400), "통화 길이가 잘못되었습니다")
        f["duration"] = d
    return f


def _counsel(db, cid):
    c = db.one("SELECT * FROM class_counsel WHERE id = ?", (int(cid),))
    need(c, "없는 상담 기록입니다")
    return c


def counsel_list(db, q):
    """number 가 있으면 그 학생의 기록 전부(학생별 모아 보기), 없으면 최근 100개."""
    if q.get("number"):
        need(q["number"].isdigit(), "학생 번호가 잘못되었습니다")
        rows = db.query("SELECT * FROM class_counsel WHERE number = ? ORDER BY date DESC, time DESC, id DESC",
                        (int(q["number"]),))
    else:
        rows = db.query("SELECT * FROM class_counsel ORDER BY date DESC, time DESC, id DESC LIMIT 100")
    counts = {r["number"]: r["n"] for r in db.query("SELECT number, COUNT(*) AS n FROM class_counsel GROUP BY number")}
    last = {r["number"]: r["d"] for r in db.query("SELECT number, MAX(date) AS d FROM class_counsel GROUP BY number")}
    pending = db.query("SELECT id, number, student_name, date, topic, followup, followup_date FROM class_counsel "
                       "WHERE followup != '' AND followup_done = 0 ORDER BY followup_date IS NULL, followup_date, date")
    return {"records": rows, "counts": counts, "last": last, "pending": pending, "students": roster(db)}


def counsel_create(db, body):
    f = counsel_fields(db, body)
    for k, v in (("time", None), ("followup_date", None), ("followup_done", 0), ("duration", None)):
        f.setdefault(k, v)
    f["source"] = "manual"
    f["created_at"] = f["updated_at"] = now_str()
    cid = db.execute(f"INSERT INTO class_counsel({', '.join(f)}) VALUES({', '.join('?' * len(f))})", tuple(f.values()))
    db.log("학급 상담 기록 1건 저장")   # 이름·내용은 남기지 않음
    return _counsel(db, cid)


def counsel_update(db, cid, body):
    c = _counsel(db, cid)
    f = counsel_fields(db, body, partial=True)
    if f:
        f["updated_at"] = now_str()
        db.execute(f"UPDATE class_counsel SET {', '.join(k + ' = ?' for k in f)} WHERE id = ?", (*f.values(), c["id"]))
    return _counsel(db, c["id"])


def counsel_delete(db, cid):
    c = _counsel(db, cid)
    db.execute("DELETE FROM class_counsel WHERE id = ?", (c["id"],))
    return {"ok": True}


def student_overview(db, number):
    """학생별 모아 보기: 상담 기록 + 그 학생의 미제출 항목."""
    need(number.isdigit(), "학생 번호가 잘못되었습니다")
    n = int(number)
    names = {r["number"]: r["name"] for r in roster(db)}
    open_tasks = db.query("SELECT * FROM class_tasks WHERE closed = 0 ORDER BY due IS NULL, due")
    missing = [t for t in open_tasks if not db.one("SELECT 1 AS x FROM class_task_marks WHERE task_id = ? AND number = ?",
                                                   (t["id"], n))] if n in names else []
    return {"number": n, "name": names.get(n), "missing_tasks": missing,
            "records": counsel_list(db, {"number": str(n)})["records"]}


# ---------------------------------------------------------------- API (server.py 가 등록)
ROUTES = [
    ("GET", "/api/class/tasks", lambda db, q, b: tasks_state(db, q.get("all") == "1")),
    ("POST", "/api/class/tasks", lambda db, q, b: task_create(db, b)),
    ("PATCH", r"/api/class/tasks/(\d+)", lambda db, q, b, tid: task_update(db, tid, b)),
    ("DELETE", r"/api/class/tasks/(\d+)", lambda db, q, b, tid: task_delete(db, tid)),
    ("POST", r"/api/class/tasks/(\d+)/marks", lambda db, q, b, tid: task_mark(db, tid, b)),
    ("GET", "/api/class/counsel", lambda db, q, b: counsel_list(db, q)),
    ("POST", "/api/class/counsel", lambda db, q, b: counsel_create(db, b)),
    ("PATCH", r"/api/class/counsel/(\d+)", lambda db, q, b, cid: counsel_update(db, cid, b)),
    ("DELETE", r"/api/class/counsel/(\d+)", lambda db, q, b, cid: counsel_delete(db, cid)),
    ("GET", r"/api/class/student/(\d+)", lambda db, q, b, n: student_overview(db, n)),
]
