"""정해진 시각(기본 9시·12시·15시)에 수집을 실행합니다.

PC 가 꺼져 있어 시각을 놓쳤으면, 켜진 뒤 그날 밀린 수집을 한 번만 실행합니다.
"""

import json
import threading
from datetime import datetime


def slot_key(day, hhmm):
    return f"{day.isoformat()} {hhmm}"


def due_slots(now, times, done_keys):
    """지금까지 지났지만 아직 실행하지 않은 오늘의 시각들."""
    today = now.date()
    cur = now.strftime("%H:%M")
    return [slot_key(today, t) for t in times if t <= cur and slot_key(today, t) not in done_keys]


def next_slot(now, times):
    cur = now.strftime("%H:%M")
    for t in sorted(times):
        if t > cur:
            return f"오늘 {t}"
    return f"내일 {sorted(times)[0]}" if times else None


class Scheduler:
    def __init__(self, db, config, collector, interval=20):
        self.db, self.config, self.collector, self.interval = db, config, collector, interval
        self.stop_event = threading.Event()
        self.thread = None

    def done_keys(self, day):
        keys = set()
        for r in self.db.query("SELECT slot_keys FROM runs WHERE started_at >= ?", (day.isoformat(),)):
            keys.update(json.loads(r["slot_keys"] or "[]"))
        return keys

    def tick(self, now=None):
        now = now or datetime.now()
        due = due_slots(now, self.config["collect_times"], self.done_keys(now.date()))
        if due:
            # 밀린 시각이 여러 개여도 한 번만 수집 (모두 처리한 것으로 기록)
            return self.collector.run("schedule", due)
        return None

    def loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as e:  # 예약 실행이 멈추지 않게 기록만 남김
                self.db.log(f"예약 수집 오류: {e}", "error")
            self.stop_event.wait(self.interval)

    def start(self):
        self.thread = threading.Thread(target=self.loop, name="scheduler", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
