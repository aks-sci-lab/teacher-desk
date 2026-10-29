import unittest
from datetime import datetime

from desk import bell
from desk import timetable as T
from tests import test_server  # ServerTests 를 이름으로 가져오면 그 시험이 여기서 또 돔
from tests.test_collect import Env
from tests.test_timetable import sample_xlsx

PERIODS = {"1": {"start": "08:50", "end": "09:40"}, "2": {"start": "09:50", "end": "10:40"}, "3": {"start": "10:50", "end": "11:40"}}


class FakeBot:
    token, chat_id = "T", 1

    def __init__(self):
        self.sent = []

    def send(self, text, chat_id=None, buttons=None):
        self.sent.append(text)
        return True


class BellTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        db = self.env.db
        db.set_meta("timetable", T.parse_file(sample_xlsx())[0])
        db.set_meta("timetable_me", 0)            # 김국어: 월 1·2교시
        db.set_meta("timetable_periods", PERIODS)
        db.set_meta("timetable_bell", {"enabled": True, "lead": 5})
        self.bot = FakeBot()
        self.bell = bell.LessonBell(db, self.env.config, self.bot)

    def tearDown(self):
        self.env.close()

    def test_sends_once_before_each_lesson(self):
        mon = lambda h, m: datetime(2026, 11, 2, h, m)
        self.assertEqual(self.bell.tick(mon(8, 40)), [])            # 아직 이름
        self.assertEqual(self.bell.tick(mon(8, 45)), ["1"])         # 5분 전
        self.assertIn("🔔 5분 뒤 1교시 (08:50~09:40) 2-1 국어", self.bot.sent[-1])
        self.assertIn("다음: 2교시 (09:50~10:40) 2-2 국어", self.bot.sent[-1])
        self.assertEqual(self.bell.tick(mon(8, 47)), [])            # 한 번만
        self.assertEqual(self.bell.tick(mon(9, 58)), [])            # 2교시 시작 뒤 5분이 지나 늦은 알림은 안 보냄
        self.assertEqual(len(self.bot.sent), 1)
        # 다음 날 화요일: 1교시 2-1
        self.assertEqual(self.bell.tick(datetime(2026, 11, 3, 8, 50)), ["1"])
        self.assertIn("🔔 지금 1교시", self.bot.sent[-1])
        self.assertIn("오늘 마지막 수업입니다", self.bot.sent[-1])

    def test_off_blocked_and_unlinked(self):
        db = self.env.db
        db.execute("INSERT INTO events(item_id, date, kind, category, text) VALUES(NULL, '2026-11-02', 'event', '학사', '2학년 현장체험학습')")
        self.assertEqual(self.bell.tick(datetime(2026, 11, 2, 8, 45)), [])     # 그 학년 행사 날
        db.set_meta("timetable_bell", {"enabled": False, "lead": 5})
        self.assertEqual(bell.due(db, datetime(2026, 11, 3, 8, 45)), [])
        db.set_meta("timetable_bell", {"enabled": True, "lead": 5})
        self.bot.chat_id = None
        self.assertEqual(self.bell.tick(datetime(2026, 11, 3, 8, 45)), [])     # 텔레그램 연결 안 됨
        # 보내다 실패하면 한 번만 기록하고 다시 보내지 않음
        self.bot.chat_id = 1
        self.bot.send = lambda *a, **k: (_ for _ in ()).throw(OSError("network"))
        self.assertEqual(self.bell.tick(datetime(2026, 11, 3, 8, 46)), ["1"])
        self.assertEqual(self.bell.tick(datetime(2026, 11, 3, 8, 47)), [])
        self.assertEqual(db.one("SELECT COUNT(*) AS n FROM logs WHERE message LIKE '수업 알림을 보내지 못했습니다%'")["n"], 1)


class PeriodApiTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def test_save_periods_and_bell(self):
        self.env.db.set_meta("timetable", T.parse_file(sample_xlsx())[0])
        self.env.db.set_meta("timetable_me", 0)
        info = self.req("GET", "/api/timetable")[1]
        self.assertIsNone(info["periods"])
        self.assertEqual(info["default_periods"]["1"]["start"], "08:50")
        self.assertEqual(self.req("PUT", "/api/timetable/periods", {"periods": {}, "bell": {"enabled": True, "lead": 5}})[0], 400)
        self.assertEqual(self.req("PUT", "/api/timetable/periods", {"periods": {"1": {"start": "09:40", "end": "08:50"}}})[0], 400)
        self.assertEqual(self.req("PUT", "/api/timetable/periods", {"periods": {"1": {"start": "8시"}}})[0], 400)
        self.assertEqual(self.req("PUT", "/api/timetable/periods", {"periods": PERIODS, "bell": {"enabled": True, "lead": 7}})[0], 400)
        status, info = self.req("PUT", "/api/timetable/periods", {"periods": PERIODS, "bell": {"enabled": True, "lead": 10}})
        self.assertEqual(status, 200, info)
        self.assertEqual((info["periods"]["2"], info["bell"]), ({"start": "09:50", "end": "10:40"}, {"enabled": True, "lead": 10}))
        # 오늘 내 수업에 시각이 붙음
        from desk import queries
        lessons = queries.my_lessons(self.env.db, None, datetime(2026, 11, 2).date())
        self.assertEqual((lessons[0]["p"], lessons[0]["start"], lessons[0]["end"]), (1, "08:50", "09:40"))


if __name__ == "__main__":
    unittest.main()
