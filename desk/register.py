"""출석부 (0.5.95): 학생 × 수업일 표에서 결석·지각·조퇴·결과를 체크.

자료의 원천은 하나씩만 둔다 (두 화면이 어긋나지 않게):
- 결석 → 결석계(absences). 출석부에서 결석을 체크하면 결석계 초안(status='draft')이 생기고,
  수업일로 이어지는 날(주말·휴업일 건너)은 한 결석계로 묶는다. 결석계를 고치거나 지우면 출석부도 그대로 바뀐다.
- 조퇴·외출 → 발급한 증(passes). 증 없이 출석부에서 조퇴를 체크하면 attendance_marks 에만 남긴다('증 없음').
- 지각·결과 → attendance_marks.
학생 이름·사유는 이 PC 의 desk.db 에만 둔다 (텔레그램·인터넷으로 보내지 않음).
"""

from datetime import date, timedelta

from . import absence
from .db import now_str
from .queries import bump_attendance_rev

MARK_KINDS = {"late": "지각", "early": "조퇴", "result": "결과"}
TYPES = absence.TYPES + ("미정",)        # 종류는 나중에 정해도 됨 (미정)
SHORT = {"질병": "질", "출석인정": "인", "기타": "기", "미인정": "미", "미정": "?"}


class RegisterError(ValueError):
    pass


class NeedConfirm(Exception):
    """작성 완료·인쇄한 결석계를 바꾸려 할 때. 화면이 확인 창을 띄우고 force 로 다시 보냄."""


def _same_student(number, name):
    return "student_number IS ? AND student_name = ?", (number, name)


def is_school_day(db, day, grade=""):
    return bool(absence.school_dates(db, day, day, grade))


def _neighbor_school_day(db, day, step, grade=""):
    """day 의 바로 앞(step=-1)·뒤(+1) 수업일. 3주 안에 없으면 None (방학 등)."""
    for n in range(1, 22):
        cand = day + timedelta(days=step * n)
        if is_school_day(db, cand, grade):
            return cand
    return None


def absence_on(db, number, name, day, grade=""):
    """그 학생의 그 날(수업일)을 덮는 결석계 하나 (없으면 None)."""
    where, args = _same_student(number, name)
    rows = db.query(f"SELECT * FROM absences WHERE {where} AND start_date <= ? AND end_date >= ? ORDER BY id",
                    (*args, day.isoformat(), day.isoformat()))
    for row in rows:
        if day.isoformat() in absence.school_dates(db, date.fromisoformat(row["start_date"]),
                                                    date.fromisoformat(row["end_date"]), grade):
            return row
    return None


def is_locked(row):
    """작성 완료(종류·사유를 채워 저장)했거나 인쇄한 결석계 → 바꾸기 전에 확인."""
    return (row.get("status") or "done") != "draft" or bool(row.get("printed_at"))


def _compatible(row_type, new_type):
    return row_type == new_type or "미정" in (row_type, new_type)


def _merged_type(row_type, new_type):
    return new_type if new_type != "미정" else row_type


def mark_absent(db, number, name, day, kind_type="미정", grade="", force=False):
    """그 날을 결석으로. 앞뒤 수업일의 초안 결석계가 있으면 기간을 늘려 하나로 묶음."""
    if kind_type not in TYPES:
        raise RegisterError("결석 종류가 잘못되었습니다")
    if not is_school_day(db, day, grade):
        raise RegisterError("주말·휴업일은 결석으로 체크할 수 없습니다")
    current = absence_on(db, number, name, day, grade)
    if current:
        if current["absence_type"] != kind_type and kind_type != "미정":
            if is_locked(current) and not force:
                raise NeedConfirm(f"작성 완료된 결석계({current['serial']})의 결석 종류를 바꿉니다.")
            db.execute("UPDATE absences SET absence_type = ? WHERE id = ?", (kind_type, current["id"]))
            bump_attendance_rev(db)
        return
    _clear_marks(db, number, name, day)            # 결석인 날은 지각·조퇴·결과 기록을 지움
    prev_day = _neighbor_school_day(db, day, -1, grade)
    next_day = _neighbor_school_day(db, day, 1, grade)
    before = prev_day and absence_on(db, number, name, prev_day, grade)
    after = next_day and absence_on(db, number, name, next_day, grade)
    # 묶기는 초안끼리만 (작성 완료 결석계는 건드리지 않고 새 초안을 만듦)
    if before and (is_locked(before) or not _compatible(before["absence_type"], kind_type)):
        before = None
    if after and (is_locked(after) or not _compatible(after["absence_type"], kind_type)):
        after = None
    if before and after and before["id"] != after["id"]:
        new_type = _merged_type(_merged_type(before["absence_type"], after["absence_type"]), kind_type)
        db.execute("UPDATE absences SET end_date = ?, absence_type = ? WHERE id = ?",
                   (after["end_date"], new_type, before["id"]))
        db.execute("DELETE FROM absences WHERE id = ?", (after["id"],))
    elif before:
        db.execute("UPDATE absences SET end_date = ?, absence_type = ? WHERE id = ?",
                   (day.isoformat(), _merged_type(before["absence_type"], kind_type), before["id"]))
    elif after:
        db.execute("UPDATE absences SET start_date = ?, absence_type = ? WHERE id = ?",
                   (day.isoformat(), _merged_type(after["absence_type"], kind_type), after["id"]))
    else:
        absence.save(db, {"student_number": number, "student_name": name, "start_date": day.isoformat(),
                          "end_date": day.isoformat(), "absence_type": kind_type, "reason": "",
                          "guardian_name": "", "note": "", "status": "draft"})
    bump_attendance_rev(db)


def unmark_absent(db, number, name, day, grade="", force=False):
    """그 날을 결석계에서 뺌. 남는 날이 없으면 결석계를 지우고, 가운데 날이면 둘로 나눔."""
    row = absence_on(db, number, name, day, grade)
    if not row:
        return False
    if is_locked(row) and not force:
        what = "인쇄한" if row.get("printed_at") else "작성 완료된"
        raise NeedConfirm(f"{what} 결석계({row['serial']})에서 {day.month}/{day.day} 을 뺍니다.")
    dates = absence.school_dates(db, date.fromisoformat(row["start_date"]), date.fromisoformat(row["end_date"]), grade)
    before = [d for d in dates if d < day.isoformat()]
    after = [d for d in dates if d > day.isoformat()]
    if not before and not after:
        db.execute("DELETE FROM absences WHERE id = ?", (row["id"],))
    else:
        first = before or after
        db.execute("UPDATE absences SET start_date = ?, end_date = ? WHERE id = ?", (first[0], first[-1], row["id"]))
        if before and after:   # 가운데 날을 빼면 뒤쪽은 새 결석계로 (같은 내용)
            keep = {k: row[k] for k in ("student_number", "student_name", "absence_type", "reason",
                                        "guardian_name", "note", "status")}
            absence.save(db, {**keep, "start_date": after[0], "end_date": after[-1]})
    bump_attendance_rev(db)
    return True


def _clear_marks(db, number, name, day, kind=None):
    where, args = _same_student(number, name)
    cond = f"{where} AND date = ?"
    params = [*args, day.isoformat()]
    if kind:
        cond += " AND kind = ?"
        params.append(kind)
    n = db.one(f"SELECT COUNT(*) AS n FROM attendance_marks WHERE {cond}", params)["n"]
    if n:
        db.execute(f"DELETE FROM attendance_marks WHERE {cond}", params)
    return n


def early_pass_on(db, number, name, day):
    where, args = _same_student(number, name)
    return db.one(f"SELECT * FROM passes WHERE {where} AND date = ? AND kind = 'early' AND status != 'cancelled' "
                  "ORDER BY id LIMIT 1", (*args, day.isoformat()))


def set_mark(db, number, name, day, kind, kind_type="미정", periods="", note="", grade="", force=False):
    """지각·조퇴·결과 기록 (그 학생·그 날·그 종류에 하나). 결석으로 체크된 날이면 결석을 먼저 풂."""
    if kind not in MARK_KINDS:
        raise RegisterError("출결 종류가 잘못되었습니다")
    if kind_type not in TYPES:
        raise RegisterError("출결 사유 종류가 잘못되었습니다")
    if not is_school_day(db, day, grade):
        raise RegisterError("주말·휴업일에는 체크할 수 없습니다")
    unmark_absent(db, number, name, day, grade, force)
    _clear_marks(db, number, name, day, kind)
    db.execute("INSERT INTO attendance_marks(student_number, student_name, date, kind, mark_type, periods, note, "
               "created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
               (number, name, day.isoformat(), kind, kind_type, (periods or "").strip()[:20] or None,
                (note or "").strip()[:200] or None, now_str()))
    bump_attendance_rev(db)


def clear_day(db, number, name, day, kind=None, grade="", force=False):
    """체크 풀기. kind 가 없으면 그 날의 결석·지각·조퇴·결과를 모두 (증은 그대로 둠)."""
    changed = False
    if kind in (None, "absent"):
        changed = unmark_absent(db, number, name, day, grade, force) or changed
    if kind != "absent":
        changed = bool(_clear_marks(db, number, name, day, None if kind is None else kind)) or changed
    bump_attendance_rev(db)
    return changed


def month_register(db, month_start, month_end, grade=""):
    """출석부 한 달: 수업일, 학생 줄, 칸마다 기록 목록."""
    days = absence.school_dates(db, month_start, month_end, grade)
    students = {}
    for s in db.students():
        students.setdefault((s["number"], s["name"]), {"student_number": s["number"], "student_name": s["name"]})
    cells = {}

    def add(number, name, day, entry):
        students.setdefault((number, name), {"student_number": number, "student_name": name})
        cells.setdefault(f"{'' if number is None else number}|{name}", {}).setdefault(day, []).append(entry)

    for row in db.query("SELECT * FROM absences WHERE start_date <= ? AND end_date >= ?",
                        (month_end.isoformat(), month_start.isoformat())):
        for day in absence.school_dates(db, max(date.fromisoformat(row["start_date"]), month_start),
                                        min(date.fromisoformat(row["end_date"]), month_end), grade):
            add(row["student_number"], row["student_name"], day, {
                "src": "absence", "kind": "absent", "id": row["id"], "type": row["absence_type"],
                "serial": row["serial"], "draft": not is_locked(row),
                "status": "printed" if row.get("printed_at") else (row.get("status") or "done")})
    for row in db.query("SELECT * FROM passes WHERE date >= ? AND date <= ? AND status != 'cancelled'",
                        (month_start.isoformat(), month_end.isoformat())):
        add(row["student_number"], row["student_name"], row["date"], {
            "src": "pass", "kind": row["kind"], "id": row["id"], "time": row["time_out"], "serial": row["serial"]})
    for row in db.query("SELECT * FROM attendance_marks WHERE date >= ? AND date <= ?",
                        (month_start.isoformat(), month_end.isoformat())):
        entry = {"src": "mark", "kind": row["kind"], "id": row["id"], "type": row["mark_type"],
                 "periods": row["periods"], "note": row["note"]}
        if row["kind"] == "early":
            entry["no_pass"] = not early_pass_on(db, row["student_number"], row["student_name"],
                                                  date.fromisoformat(row["date"]))
        add(row["student_number"], row["student_name"], row["date"], entry)
    rows = sorted(students.values(), key=lambda r: (r["student_number"] is None, r["student_number"] or 0,
                                                    r["student_name"]))
    return {"days": days, "students": rows, "cells": cells, "types": list(TYPES), "kinds": MARK_KINDS}
