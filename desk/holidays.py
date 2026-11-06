"""대한민국 공휴일 날짜표와 조회 함수."""

from collections import defaultdict
from datetime import date, timedelta


_FIXED = (
    (1, 1, "신정"),
    (3, 1, "삼일절"),
    (5, 1, "노동절"),
    (5, 5, "어린이날"),
    (6, 6, "현충일"),
    (7, 17, "제헌절"),
    (8, 15, "광복절"),
    (10, 3, "개천절"),
    (10, 9, "한글날"),
    (12, 25, "성탄절"),
)

_LUNAR = {
    2026: ((2, 16, "설날"), (2, 17, "설날"), (2, 18, "설날"),
           (5, 24, "부처님오신날"),
           (9, 24, "추석"), (9, 25, "추석"), (9, 26, "추석")),
    2027: ((2, 6, "설날"), (2, 7, "설날"), (2, 8, "설날"),
           (5, 13, "부처님오신날"),
           (9, 14, "추석"), (9, 15, "추석"), (9, 16, "추석")),
    2028: ((1, 26, "설날"), (1, 27, "설날"), (1, 28, "설날"),
           (5, 2, "부처님오신날"),
           (10, 2, "추석"), (10, 3, "추석"), (10, 4, "추석")),
}

_ELECTIONS = {
    date(2026, 6, 3): "지방선거일",
    date(2028, 4, 12): "국회의원선거일",
}

_SUBSTITUTE_TARGETS = {
    "삼일절", "노동절", "어린이날", "제헌절", "광복절", "개천절",
    "한글날", "부처님오신날", "성탄절", "설날", "추석",
}
_LUNAR_HOLIDAYS = {"설날", "추석"}


def _build_holidays():
    original = defaultdict(list)
    for year in _LUNAR:
        for month, day, name in _FIXED:
            original[date(year, month, day)].append(name)
        for month, day, name in _LUNAR[year]:
            original[date(year, month, day)].append(name)
    for day, name in _ELECTIONS.items():
        original[day].append(name)

    substitutes = {}
    for day, names in sorted(original.items()):
        targets = set()
        for name in names:
            if name not in _SUBSTITUTE_TARGETS:
                continue
            overlaps = day.weekday() < 5 and any(other != name for other in names)
            weekend = day.weekday() == 6 if name in _LUNAR_HOLIDAYS else day.weekday() >= 5
            if weekend or overlaps:
                targets.add(name)
        if not targets:
            continue
        # KASI 2028 달력자료: 같은 날 겹친 추석·개천절은 10/5 하루만 대체.
        next_day = day + timedelta(days=1)
        while next_day.weekday() >= 5 or next_day in original or next_day in substitutes:
            next_day += timedelta(days=1)
        substitutes[next_day] = targets

    result = {day: "·".join(dict.fromkeys(names)) for day, names in original.items()}
    for day, names in substitutes.items():
        result[day] = f"대체공휴일({'·'.join(sorted(names))})"
    return result


_HOLIDAYS = _build_holidays()


def holiday_name(d):
    """공휴일이면 이름을, 아니면 None을 돌려줍니다."""
    if type(d) is not date:
        raise TypeError("날짜는 datetime.date 여야 합니다")
    return _HOLIDAYS.get(d)


def year_range():
    """공휴일 표가 다루는 (처음 해, 마지막 해)."""
    years = [day.year for day in _HOLIDAYS]
    return (min(years), max(years)) if years else (2026, 2028)


def is_weekend(d):
    """토요일이나 일요일인지 돌려줍니다."""
    if type(d) is not date:
        raise TypeError("날짜는 datetime.date 여야 합니다")
    return d.weekday() >= 5


def holidays_between(start, end):
    """시작일과 끝 날짜를 포함해 공휴일을 ISO 날짜와 이름으로 돌려줍니다."""
    if type(start) is not date or type(end) is not date:
        raise TypeError("날짜는 datetime.date 여야 합니다")
    if start > end:
        raise ValueError("시작 날짜가 끝 날짜보다 늦습니다")
    result = {}
    day = start
    while day <= end:
        name = holiday_name(day)
        if name:
            result[day.isoformat()] = name
        if day == end:
            break
        day += timedelta(days=1)
    return result
