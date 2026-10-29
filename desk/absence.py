"""결석계 작성과 학사일정 기준 수업일 계산 (화면·텔레그램 공용)."""

import re
from datetime import date, timedelta

from .db import now_str
from .timetable import P_GRADES

TYPES = ("질병", "출석인정", "기타", "미인정")
TYPE_WORDS = {"병결": "질병", "인정결": "출석인정", "기타결": "기타", "미인정결": "미인정"}
P_CLOSED = re.compile(
    r"공휴일|휴업|휴일|방학(?!식)|설날|추석|어린이날|부처님\s*오신\s*날|석가탄신일|"
    r"삼일절|3[.·]1절|현충일|광복절|개천절|한글날|성탄절|크리스마스|신정|개교기념일|선거일")
P_DATE = re.compile(
    r"(?<!\d)(?:(?P<year>\d{4})\s*[-./년]\s*)?"
    r"(?P<month>\d{1,2})\s*[-./월]\s*(?P<day>\d{1,2})(?:일)?(?!\d)")


class AbsenceError(ValueError):
    pass


def school_dates(db, start, end, grade="", require_calendar=False):
    if end < start:
        raise AbsenceError("끝 날짜가 시작 날짜보다 빠릅니다.")
    if require_calendar:
        years = {d.year - (d.month < 3) for d in (start, end)}
        for year in years:
            if not db.one("SELECT id FROM items WHERE source = 'school' AND title LIKE ?",
                          (f"{year}학년도 학사일정%",)):
                raise AbsenceError(
                    f"{year}학년도 학사일정을 앱의 달력에서 먼저 가져오세요. 공휴일·휴업일을 확인해야 저장할 수 있습니다.")
    rows = db.query(
        "SELECT e.date, COALESCE(e.end_date, e.date) AS end_date, e.text "
        "FROM events e JOIN items i ON i.id = e.item_id "
        "WHERE i.source = 'school' AND i.status != 'archived' AND e.date <= ? "
        "AND COALESCE(e.end_date, e.date) >= ?", (end.isoformat(), start.isoformat()))
    closed = set()
    for row in rows:
        text = row["text"] or ""
        if not P_CLOSED.search(text):
            continue
        grades = P_GRADES.search(text)
        if grades and str(grade).strip() and str(grade) not in re.split(r"\s*[,.·]\s*", grades.group(1)):
            continue
        first = max(start, date.fromisoformat(row["date"]))
        last = min(end, date.fromisoformat(row["end_date"]))
        while first <= last:
            closed.add(first)
            first += timedelta(days=1)
    return [(start + timedelta(days=n)).isoformat() for n in range((end - start).days + 1)
            if (start + timedelta(days=n)).weekday() < 5 and start + timedelta(days=n) not in closed]


def record(db, row, grade=""):
    return {**row, "dates": school_dates(
        db, date.fromisoformat(row["start_date"]), date.fromisoformat(row["end_date"]), grade)}


def save(db, fields):
    with db.lock:
        n = db.one("SELECT COUNT(*) AS n FROM absences WHERE start_date = ?", (fields["start_date"],))["n"] + 1
        fields = {**fields, "serial": fields["start_date"].replace("-", "") + f"-{n:02d}", "created_at": now_str()}
        absence_id = db.execute(
            f"INSERT INTO absences({', '.join(fields)}) VALUES({', '.join('?' * len(fields))})", tuple(fields.values()))
    db.log(f"결석계 작성 {fields['serial']}")
    return db.one("SELECT * FROM absences WHERE id = ?", (absence_id,))


def parse_command(text, today=None):
    today = today or date.today()
    types = [word for word in TYPE_WORDS if re.search(rf"(?<!\S){word}(?!\S)", text)]
    if len(types) != 1:
        raise AbsenceError("결석 종류는 병결·인정결·기타결·미인정결 중 하나를 입력하세요.")
    text = re.sub(rf"(?<!\S){types[0]}(?!\S)", " ", text)
    matches = list(P_DATE.finditer(text))
    if not 1 <= len(matches) <= 2:
        raise AbsenceError("날짜를 입력하세요. 예: 결석계 김가온 7 병결 10/29~11/3")
    dates = []
    try:
        for match in matches:
            year = int(match["year"] or (dates[0].year if dates else today.year))
            parsed = date(year, int(match["month"]), int(match["day"]))
            if dates and not match["year"] and parsed < dates[0] and parsed.month < dates[0].month:
                parsed = date(year + 1, parsed.month, parsed.day)
            dates.append(parsed)
    except ValueError as e:
        raise AbsenceError("달력에 없는 날짜입니다. 날짜를 다시 확인하세요.") from e
    rest = P_DATE.sub(" ", text)
    rest = re.sub(r"부터|까지|[~∼～–—-]", " ", rest)
    numbers = list(re.finditer(r"(?<!\S)(\d{1,3})(?:번)?(?!\S)", rest))
    if len(numbers) != 1:
        raise AbsenceError("학생 번호와 이름을 함께 입력하세요. 예: 결석계 김가온 7 병결 10/29~11/3")
    number = int(numbers[0][1])
    name = (rest[:numbers[0].start()] + rest[numbers[0].end():]).strip()
    if not name or len(name) > 20 or re.search(r"\s|\d|[~/-]", name):
        raise AbsenceError("학생 번호와 이름을 확인하세요. 예: 결석계 김가온 7 병결 10/29~11/3")
    start, end = dates[0], dates[-1]
    if end < start or (end - start).days > 366:
        raise AbsenceError("결석 기간은 시작일부터 끝일까지 1년 이내로 입력하세요.")
    return {"student_number": number, "student_name": name, "absence_type": TYPE_WORDS[types[0]],
            "reason": types[0], "start_date": start.isoformat(), "end_date": end.isoformat()}
