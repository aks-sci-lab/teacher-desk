import unittest
from datetime import date, datetime

from desk import bell, queries
from desk import timetable as T
from desk.telegram import Bot
from tests.test_bell import FakeBot, PERIODS
from tests.test_collect import Env
from tests.test_timetable import sample_xlsx

MON = date(2026, 11, 2)   # 시간표: 김국어 월 1·2교시 (2-1, 2-2)


class DayNoteTests(unittest.TestCase):
    def evs(self, *texts):
        return {MON.isoformat(): list(texts)}

    def test_levels(self):
        note = lambda *t, g=2: T.day_note(self.evs(*t), MON, g)
        self.assertEqual(note("개교기념일")["level"], "off")                      # 휴일: 전교 수업 없음
        self.assertEqual(note("2학년 현장체험학습")["level"], "off")               # 그 학년이 적힌 행사
        self.assertIsNone(note("1학년 현장체험학습"))                              # 다른 학년
        self.assertEqual(note("체육대회")["level"], "maybe")                      # 학년이 없는 행사: 확인 필요
        self.assertEqual(note("체육대회", "개교기념일")["level"], "off")           # off 가 우선
        self.assertIsNone(note("학부모 상담주간"))
        self.assertIsNone(T.day_note({}, MON, 2))
        self.assertEqual(T.block_reason(self.evs("체육대회"), MON, 2, MON), "체육대회")   # 수업 교체는 예전처럼 막음


class LessonNoteOutputTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        db = self.env.db
        db.set_meta("timetable", T.parse_file(sample_xlsx())[0])
        db.set_meta("timetable_me", 0)
        db.set_meta("timetable_periods", PERIODS)
        db.set_meta("timetable_bell", {"enabled": True, "lead": 0})
        self.bot = FakeBot()
        self.bell = bell.LessonBell(db, self.env.config, self.bot)
        self.tg = Bot(db, self.env.config)

    def tearDown(self):
        self.env.close()

    def add(self, text):
        self.env.db.execute("INSERT INTO events(item_id, date, kind, category, text) VALUES(NULL, ?, 'event', '학사', ?)",
                            (MON.isoformat(), text))

    def test_telegram_and_screen_use_the_same_judgment(self):
        self.add("체육대회")
        ls = queries.day_lessons(self.env.db, self.env.config, MON)
        self.assertEqual([l["note"]["level"] for l in ls], ["maybe", "maybe"])
        text = self.tg.lessons_text("월요일 수업", today=date(2026, 10, 28))
        self.assertIn("2-1 국어 ⚠ 학사일정 확인(체육대회)", text)
        self.add("개교기념일")
        text = self.tg.lessons_text("월요일 수업", today=date(2026, 10, 28))
        self.assertIn("⛔ 수업 없음(개교기념일)", text)
        self.assertIn("⛔ 수업 없음", self.tg.answer("today", MON))
        from desk import assist
        found = assist.gather(self.env.db, "다음 주 월요일 빈 시간 있어?", date(2026, 10, 28))
        prompt, _ = assist.build_prompt("다음 주 월요일 빈 시간 있어?", found, [], date(2026, 10, 28))
        self.assertIn("수업 없음(개교기념일)", prompt)

    def test_bell_skips_off_days_but_warns_on_uncertain_ones(self):
        self.add("체육대회")
        sent = self.bell.tick(datetime(2026, 11, 2, 8, 50))
        self.assertEqual(sent, ["1"])
        self.assertIn("⚠ 학사일정 확인: 체육대회", self.bot.sent[-1])                    # 수업이 있을 수도 있으니 보내되 확인하라고
        self.add("개교기념일")
        self.assertEqual(self.bell.tick(datetime(2026, 11, 2, 9, 50)), [])             # 수업 없는 날은 보내지 않음


if __name__ == "__main__":
    unittest.main()
