"""자동 AI 요약은 내 수업 시간에만 (0.5.98): 가짜 시간·시간표로 수업 중/아님/시간표 없음/주말·공휴일·학사일정."""
import time
import unittest
from datetime import datetime
from unittest.mock import patch

from desk import ai, aiclass
from desk import timetable as T
from desk.collect import add_text_item
from tests import test_server
from tests.test_ai_register import FakeLMStudio
from tests.test_collect import Env
from tests.test_item_ai_auto import Clock, text, when
from tests.test_timetable import sample_xlsx

# 김국어(0번): 월 1·2교시, 화 1교시, 금 3교시
PERIODS = {"1": {"start": "08:50", "end": "09:40"}, "2": {"start": "09:50", "end": "10:40"}, "3": {"start": "10:50", "end": "11:40"}}
MON = lambda h, m: datetime(2026, 11, 2, h, m)


def with_timetable(db, periods=PERIODS):
    db.set_meta("timetable", T.parse_file(sample_xlsx())[0])
    db.set_meta("timetable_me", 0)
    if periods:
        db.set_meta("timetable_periods", periods)


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.db, self.config = self.env.db, self.env.config
        with_timetable(self.db)

    def tearDown(self):
        self.env.close()

    def win(self, now):
        return aiclass.window(self.db, self.config, now)

    def test_in_class(self):
        w = self.win(MON(9, 0))
        self.assertTrue(w["allowed"])
        self.assertEqual(w["current"], {"p": 1, "start": "08:50", "end": "09:40"})
        self.assertIn("1교시 수업 중", w["text"])
        self.assertTrue(self.win(MON(10, 39))["allowed"])               # 2교시 끝나기 직전

    def test_not_in_class_shows_next(self):
        w = self.win(MON(9, 45))                                         # 쉬는 시간
        self.assertFalse(w["allowed"])
        self.assertEqual((w["next"]["label"], w["next"]["p"], w["next"]["start"]), ("오늘", 2, "09:50"))
        self.assertEqual(w["text"], "다음 자동 요약: 오늘 2교시 (09:50)")
        w = self.win(MON(10, 40))                                        # 2교시가 끝남 = 수업 아님
        self.assertFalse(w["allowed"])
        self.assertEqual((w["next"]["label"], w["next"]["p"]), ("내일", 1))
        w = self.win(datetime(2026, 11, 3, 12, 0))                       # 화요일 오후 → 금요일 3교시
        self.assertEqual((w["next"]["label"], w["next"]["p"], w["next"]["start"]), ("11/6(금)", 3, "10:50"))

    def test_weekend_and_holiday(self):
        sat = self.win(datetime(2026, 11, 7, 9, 0))
        self.assertFalse(sat["allowed"])
        self.assertEqual(sat["next"]["date"], "2026-11-09")              # 다음 월요일
        xmas = self.win(datetime(2026, 12, 25, 11, 0))                   # 금요일 3교시지만 성탄절
        self.assertFalse(xmas["allowed"])

    def test_school_event_with_no_lessons(self):
        self.db.execute("INSERT INTO events(item_id, date, kind, category, text) VALUES(NULL, '2026-11-02', 'event', '학사', '2학년 현장체험학습')")
        w = self.win(MON(9, 0))
        self.assertFalse(w["allowed"])
        self.assertEqual(w["next"]["date"], "2026-11-03")

    def test_default_periods_when_not_set(self):
        self.db.set_meta("timetable_periods", None)
        self.assertTrue(self.win(MON(8, 55))["allowed"])                 # bell.DEFAULT_PERIODS 1교시 08:50~09:40

    def test_no_timetable_or_setting_off_means_anytime(self):
        self.db.set_meta("timetable_me", None)
        w = self.win(MON(9, 45))
        self.assertEqual((w["mode"], w["allowed"]), ("no_timetable", True))
        self.assertIn("시간표를 불러오지 않아", w["text"])
        with_timetable(self.db)
        self.config.values["ai_auto_class_only"] = False
        self.assertEqual((self.win(MON(9, 45))["mode"], self.win(MON(9, 45))["allowed"]), ("always", True))

    def test_broken_timetable_does_not_stop_summaries(self):
        self.db.set_meta("timetable", {"teachers": [{}], "days": "x"})
        self.assertTrue(aiclass.allowed(self.db, self.config, MON(9, 45)))


class GatedQueueTests(unittest.TestCase):
    """대기열: 수업이 아닐 때는 자동 요약을 줄 세우지 않고, 눌러서 시킨 것은 바로."""
    def setUp(self):
        self.env = Env()
        self.lm = FakeLMStudio()
        self.lm.reply = "한 줄 요약: 협의록 제출"
        self.env.config.values.update(ai_enabled=True, ai_base=self.lm.base)
        self.open = [False]
        self.job = ai.ItemJob(self.env.db, self.env.config, clock=Clock(), gate=lambda: self.open[0])
        self.ids = [add_text_item(self.env.db, "cool", text(n), title=f"쪽지 {n}", received_at=when())["id"] for n in range(3)]

    def tearDown(self):
        self.job.stop()
        self.lm.close()
        self.env.close()

    def wait_idle(self):
        for _ in range(200):
            if not self.job.worker_alive:
                return
            time.sleep(0.02)

    def summarized(self):
        return [r["id"] for r in self.env.db.query("SELECT id FROM items WHERE ai_summary IS NOT NULL")]

    def test_waits_for_class_then_catches_up(self):
        self.assertEqual(self.job.scan(), 0)                             # 쉬는 시간: 줄 세우지 않음
        self.assertEqual(self.job.pending(), 3)                          # 모아 둔 것
        self.assertEqual(self.lm.requests, [])
        self.open[0] = True                                              # 수업 시작
        self.assertEqual(self.job.scan(), 3)
        self.wait_idle()
        self.assertEqual(sorted(self.summarized()), sorted(self.ids))
        self.assertEqual(self.job.pending(), 0)

    def test_pressed_summary_runs_any_time(self):
        self.job.start(self.ids[0])
        self.wait_idle()
        self.assertEqual(self.summarized(), [self.ids[0]])

    def test_class_end_stops_new_auto_work(self):
        self.open[0] = True
        with patch.object(self.job, "_ensure_worker"):                   # 줄만 세우고 아직 시작 안 함
            self.assertEqual(self.job.scan(), 3)
        self.open[0] = False                                             # 그 사이 수업이 끝남
        with self.job.lock:
            self.job._ensure_worker()
        self.wait_idle()
        self.assertEqual(self.summarized(), [])                          # 새 자동 요약은 시작하지 않음
        self.assertEqual(self.job.queue, [])


class WindowApiTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def test_auto_window_and_briefing_auto_gate(self):
        st, w = self.req("GET", "/api/ai/auto-window")
        self.assertEqual(st, 200)
        self.assertIn(w["mode"], ("no_timetable", "always", "class"))
        self.assertIn("pending", w)
        self.assertIn("auto_window", self.req("GET", "/api/ai/weekly")[1])
        self.env.config.values["ai_enabled"] = True
        lm = FakeLMStudio()
        self.env.config.values["ai_base"] = lm.base
        started = []
        closed = {"mode": "class", "allowed": False, "current": None, "next": None, "text": "다음 자동 요약: 내일 1교시 (08:50)"}
        try:
            with patch.object(self.app.ai_job, "start", lambda kind, since=None: started.append(kind)), \
                    patch.object(aiclass, "window", lambda db, config, now=None: closed):
                st, r = self.req("POST", "/api/ai/weekly", {"kind": "full", "auto": True})   # 브리핑을 열 때 저절로
                self.assertEqual((st, started, r["auto_window"]["allowed"]), (200, [], False))
                self.req("POST", "/api/ai/weekly", {"kind": "full"})                          # 직접 누름
                self.assertEqual(started, ["full"])
        finally:
            lm.close()
            self.env.config.values["ai_enabled"] = False


if __name__ == "__main__":
    unittest.main()
