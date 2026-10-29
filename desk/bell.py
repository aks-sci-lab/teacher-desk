"""수업 시작 알림: 시간표의 교시 시간에 맞춰 내 수업이 시작할 때(또는 몇 분 전) 텔레그램으로 알림.

- 교시 시간은 시간표 화면에서 정함 (`meta.timetable_periods`), 알림 켜기·몇 분 전은 `meta.timetable_bell`.
- 오늘 내 수업은 교체 기록을 반영한 것 (timetable.lessons_on). 학사일정상 그 학년 수업이 없는 날은 보내지 않음.
- 업무 데스크가 꺼져 있던 사이 지난 알림은 보내지 않음 (시작 뒤 5분까지만).
- 보내는 내용은 교시·시각·학급·과목뿐 (학생 정보 없음).
"""

import threading
from datetime import datetime, timedelta

from . import queries, timetable

DEFAULT_PERIODS = {
    "1": {"start": "08:50", "end": "09:40"}, "2": {"start": "09:50", "end": "10:40"},
    "3": {"start": "10:50", "end": "11:40"}, "4": {"start": "11:50", "end": "12:40"},
    "5": {"start": "13:30", "end": "14:20"}, "6": {"start": "14:30", "end": "15:20"},
    "7": {"start": "15:30", "end": "16:20"},
}
LEADS = (0, 5, 10, 15)
LATE = timedelta(minutes=5)   # 시작 뒤 이만큼 지나면 보내지 않음


def _at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m)


def lesson_text(l, periods):
    t = periods.get(str(l["p"])) or {}
    span = f" ({t['start']}~{t.get('end', '')})" if t.get("start") else ""
    return f"{l['p']}교시{span} {l['cls'] or ''} {l['subj'] or ''}".replace("  ", " ").strip()


def due(db, now):
    """지금 보내야 할 알림 [(키, 글)]."""
    bell = db.get_meta("timetable_bell") or {}
    periods = db.get_meta("timetable_periods") or {}
    if not bell.get("enabled") or not periods:
        return []
    today = now.date()
    lessons = queries.my_lessons(db, None, today)
    if not lessons:
        return []
    lead = timedelta(minutes=int(bell.get("lead") or 0))
    sent = set((db.get_meta("timetable_bell_sent") or {}).get(today.isoformat(), []))
    evs = queries.school_day_events(db, today, today)
    out = []
    for i, l in enumerate(lessons):
        start = (periods.get(str(l["p"])) or {}).get("start")
        key = f"{l['p']}"
        if not start or key in sent:
            continue
        begin = _at(today, start)
        if not (begin - lead <= now < begin + LATE):
            continue
        if timetable.block_reason(evs, today, timetable.grade_of(l["cls"]), today):
            continue   # 현장체험학습·휴업 등으로 그 학년 수업이 없는 날
        head = "🔔 지금 " if now >= begin or not lead else f"🔔 {round((begin - now).total_seconds() / 60)}분 뒤 "
        text = head + lesson_text(l, periods) + (" (교체된 수업)" if l.get("moved") else "")
        nxt = lessons[i + 1] if i + 1 < len(lessons) else None
        text += f"\n다음: {lesson_text(nxt, periods)}" if nxt else "\n오늘 마지막 수업입니다."
        out.append((key, text))
    return out


def mark_sent(db, day, keys):
    sent = db.get_meta("timetable_bell_sent") or {}
    sent = {day.isoformat(): sorted(set(sent.get(day.isoformat(), [])) | set(keys))}   # 오늘 것만 남김
    db.set_meta("timetable_bell_sent", sent)


class LessonBell:
    def __init__(self, db, config, bot, interval=20):
        self.db, self.config, self.bot, self.interval = db, config, bot, interval
        self.stop_event = threading.Event()
        self.thread = None

    def tick(self, now=None):
        now = now or datetime.now()
        if not (self.bot and self.bot.token and self.bot.chat_id):
            return []
        sent = []
        for key, text in due(self.db, now):
            try:
                self.bot.send(text)
            except Exception as e:  # 인터넷이 끊겼으면 한 번만 기록하고 그 알림은 넘김 (20초마다 쌓이지 않게)
                self.db.log(f"수업 알림을 보내지 못했습니다 ({key}교시): {type(e).__name__}", "warn")
            sent.append(key)
        if sent:
            mark_sent(self.db, now.date(), sent)
        return sent

    def loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as e:  # 알림이 멈추지 않게 기록만
                self.db.log(f"수업 알림 오류: {type(e).__name__}: {e}", "warn")
            self.stop_event.wait(self.interval)

    def start(self):
        self.thread = threading.Thread(target=self.loop, name="lesson-bell", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
