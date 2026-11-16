"""자동 AI 요약은 '내 수업 시간'에만 (0.5.98, 선생님 요청 2026-11-14).

선생님 PC(내장 그래픽이 메모리를 같이 씀)에서 LM Studio 가 요약하는 동안 메모리가 모자라 다른 일을 못 하므로,
선생님이 누르지 않은 AI 작업(수신 항목 자동 요약, 브리핑을 열 때 저절로 하는 요약)은
선생님이 교실에 가 있는 '수업 중' 교시에만 돌림.

- 수업 중 = 오늘 내 시간표의 수업(교체 반영, queries.my_lessons) 교시의 시작~끝 시각.
  교시 시각은 시간표 화면에서 정한 것(meta.timetable_periods), 없으면 bell.DEFAULT_PERIODS.
- 주말·공휴일, 학사일정상 그 학년 수업이 없는 날(현장체험학습·휴업 등, note level 'off')은 수업이 아님.
- 시간표를 불러오지 않았거나 내 이름을 안 골랐으면 예전처럼 언제든 (화면에 그 사실을 알림).
- 설정 'ai_auto_class_only' 를 끄면 언제든.
- 선생님이 직접 누른 요약·텔레그램 질문 답 등은 이 규칙과 상관없이 바로.
- 시간표 파일은 읽기만 함.
"""

from datetime import datetime, timedelta

from . import holidays, queries
from .bell import DEFAULT_PERIODS

LOOK_DAYS = 14   # '다음 자동 요약' 을 찾는 날 수 (방학이면 못 찾을 수 있음)


def _at(day, hhmm):
    try:
        h, m = map(int, str(hhmm).split(":"))
        return datetime(day.year, day.month, day.day, h, m)
    except (TypeError, ValueError):
        return None


def slots(db, day):
    """그날 자동 요약을 돌려도 되는 수업 시간 [(교시, 시작, 끝)]. 시간표가 없으면 None."""
    lessons = queries.my_lessons(db, None, day)
    if lessons is None:
        return None
    if holidays.is_weekend(day) or holidays.holiday_name(day) or not lessons:
        return []
    queries.lesson_notes(db, day, lessons)
    periods = db.get_meta("timetable_periods") or {}
    out = []
    for l in lessons:
        if l.get("note") and l["note"]["level"] == "off":
            continue   # 그 학년 수업이 없는 날 (학사일정)
        t = periods.get(str(l["p"])) or DEFAULT_PERIODS.get(str(l["p"])) or {}
        start, end = _at(day, t.get("start")), _at(day, t.get("end"))
        if start and end and start < end:
            out.append((l["p"], start, end))
    return out


def window(db, config, now=None):
    """지금 자동 요약을 돌려도 되는지와 화면에 보일 상태.
    {mode: off|always|no_timetable|class, allowed, current, next, text}"""
    now = now or datetime.now()
    if not config["ai_auto_class_only"]:
        return {"mode": "always", "allowed": True, "current": None, "next": None,
                "text": "자동 요약은 언제든 합니다"}
    today = slots(db, now.date())
    if today is None:
        return {"mode": "no_timetable", "allowed": True, "current": None, "next": None,
                "text": "시간표를 불러오지 않아 자동 요약을 언제든 합니다 (시간표 화면에서 불러오고 내 이름을 고르면 수업 시간에만)"}
    for p, start, end in today:
        if start <= now < end:
            return {"mode": "class", "allowed": True, "next": None,
                    "current": {"p": p, "start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")},
                    "text": f"지금 {p}교시 수업 중이라 자동 요약을 합니다 ({end.strftime('%H:%M')}까지)"}
    nxt = None
    for i in range(LOOK_DAYS + 1):
        day = now.date() + timedelta(days=i)
        cand = [(p, s) for p, s, _ in (today if i == 0 else (slots(db, day) or [])) if s > now]
        if cand:
            p, s = min(cand, key=lambda x: x[1])
            nxt = {"date": day.isoformat(), "p": p, "start": s.strftime("%H:%M"),
                   "label": ("오늘" if i == 0 else "내일" if i == 1 else f"{s.month}/{s.day}({'월화수목금토일'[s.weekday()]})")}
            break
    text = (f"다음 자동 요약: {nxt['label']} {nxt['p']}교시 ({nxt['start']})" if nxt
            else f"앞으로 {LOOK_DAYS}일 안에 내 수업이 없어 자동 요약을 쉽니다")
    return {"mode": "class", "allowed": False, "current": None, "next": nxt, "text": text}


def allowed(db, config, now=None):
    try:
        return window(db, config, now)["allowed"]
    except Exception as e:   # 시간표 자료가 이상해도 요약 기능 전체가 멈추지 않게 (예전처럼 언제든)
        db.log(f"수업 시간 확인 오류: {type(e).__name__}: {e}", "error")
        return True
