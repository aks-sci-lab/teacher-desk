import unittest
from datetime import date, datetime

from desk import assist, schoolcal
from desk import timetable as T
from desk.telegram import HELP, Bot
from tests.fake_telegram import FakeTelegram
from tests.test_bell import PERIODS
from tests.test_collect import Env
from tests.test_telegram_more import CHAT, msg, press
from tests.test_timetable import sample_xlsx

MON, SAT = date(2026, 11, 2), date(2026, 11, 7)   # 시간표: 2-3 은 월 1수학(최수학)·2·3과학(정과학), 목 1수학


class IntentTests(unittest.TestCase):
    def test_only_clear_shapes_are_timetable_commands(self):
        yes = {"2-3 시간표": ("class_day", "2-3"), "2-3 오늘": ("class_day", "2-3"), "2-3 내일": ("class_day", "2-3"),
               "2-3 목요일": ("class_day", "2-3"), "2학년 3반 시간표": ("class_day", "2-3"), "2학년3반 오늘": ("class_day", "2-3"),
               "2-3 지금": ("class_now", "2-3"), "지금 2-3 누구": ("class_now", "2-3"), "2-3 지금 몇 교시야?": ("class_now", "2-3")}
        for text, (kind, cls) in yes.items():
            found = assist.timetable_intent(text)
            self.assertEqual((found[0], found[1]["cls"]), (kind, cls), text)
        found = assist.timetable_intent("같이 비는 시간 김철수랑 이영희 다음주")
        self.assertEqual(found, ("free", {"names": ["김철수", "이영희"], "next": True}))
        self.assertEqual(assist.timetable_intent("빈 시간 김철수 이영희")[1]["names"], ["김철수", "이영희"])
        for text in ("오늘 식단표", "오늘", "내일 수업", "금요일 시간표", "2-3", "2-3 공지 보내기", "2-3 오늘 급식 뭐야", "2-3 시간표 교체해줘",
                     "2026-10-29 시간표", "10-29 오늘", "11/3 오늘", "/today", "시간표", "빈칸 채우기", "요즘 빈 시간 없다"):
            self.assertIsNone(assist.timetable_intent(text), text)


class TelegramTimetableTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        db = self.env.db
        db.set_meta("telegram_chat_id", CHAT)
        db.set_meta("timetable", T.parse_file(sample_xlsx())[0])
        db.set_meta("timetable_me", 0)
        db.set_meta("timetable_periods", PERIODS)
        db.replace_students([{"number": 7, "name": "한가람"}])

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def run_cmd(self, text, today=MON, **kw):
        found = assist.timetable_intent(text)
        return self.bot.timetable_command(CHAT, *found, today=today, **kw)

    def test_class_day_lists_period_subject_teacher_with_times(self):
        out = self.run_cmd("2-3 시간표")
        self.assertIn("11/2(월) 오늘 2-3 시간표 3시간", out)
        self.assertIn("1교시 08:50~09:40 수학 최수학", out)
        self.assertIn("2교시 09:50~10:40 과학 정과학", out)
        self.assertIn("3교시 10:50~11:40 과학 정과학", out)
        self.assertIn("목", self.run_cmd("2학년 3반 목요일").split("\n")[0])
        self.assertIn("1교시 08:50~09:40 수학 최수학", self.run_cmd("2-3 목요일"))
        self.assertIn("없음", self.run_cmd("2-3 내일"))                        # 화요일 2-3 수업 없음
        self.assertNotIn("한가람", out)                                      # 학생 이름은 어디에도 없음

    def test_weekend_goes_to_next_monday_and_school_note_is_shown(self):
        out = self.run_cmd("2-3 오늘", today=SAT)
        self.assertIn("다음 월요일", out)
        self.assertIn("11/9(월)", out)
        schoolcal.save(self.env.db, 2026, [{"date": "2026-11-02", "end_date": None, "text": "개교기념일", "kind": "event", "category": "학사"},
                                           {"date": "2026-11-05", "end_date": None, "text": "체육대회", "kind": "event", "category": "학사"}], "나이스")
        self.assertIn("⛔ 수업 없음(개교기념일)", self.run_cmd("2-3 시간표"))
        self.assertIn("⚠ 학사일정 확인(체육대회)", self.run_cmd("2-3 목요일"))

    def test_class_now_uses_period_times(self):
        at = lambda h, m: datetime(2026, 11, 2, h, m)
        cls = lambda now, c="2-3": self.bot.class_now_text(self.env.db.get_meta("timetable"), c, now)
        self.assertIn("아직 수업 전", cls(at(8, 0)))
        self.assertIn("1교시", cls(at(8, 0)))
        self.assertIn("🔔 지금 2-3 1교시 (08:50~09:40)\n  수학 최수학", cls(at(9, 0)))
        rest = cls(at(9, 45))
        self.assertIn("쉬는 시간", rest)
        self.assertIn("2교시", rest)
        self.assertIn("과학 정과학", rest)
        self.assertIn("모두 끝", cls(at(12, 0)))
        self.assertIn("수업 없음", cls(at(9, 0), "2-2"))                       # 2-2 는 월요일 1교시 수업이 없음
        self.assertIn("주말", cls(datetime(2026, 11, 7, 9, 0)))
        self.env.db.set_meta("timetable_periods", None)
        self.assertIn("교시 시간이 정해져 있지 않습니다", cls(at(9, 0)))

    def test_routing_through_messages_and_help(self):
        self.assertIn("📚", self.bot.handle_update(msg("2-3 시간표")))   # 주말에는 앞에 '주말이라 다음 월요일…' 이 붙음
        self.assertIn("2-3", self.bot.handle_update(msg("2학년 3반 시간표")))
        self.assertIn("시간표에 9-9 학급이 없습니다", self.bot.handle_update(msg("9-9 시간표")))
        for text in ("오늘 식단표", "2-3 오늘 급식 뭐야"):
            self.assertIn("알아듣지 못했습니다", self.bot.handle_update(msg(text)), text)
        self.assertIn("업무", self.bot.handle_update(msg("오늘")))               # 기존 명령은 그대로
        self.assertIn("내 수업", self.bot.handle_update(msg("내일 수업")))
        self.bot.handle_update(msg("조퇴증"))
        self.bot.handle_update(msg("7"))                                        # 입력 대화 중에도 뚜렷한 시간표 말은 처리
        self.assertIn("📚", self.bot.handle_update(msg("2-3 시간표")))   # 주말에는 앞에 '주말이라 다음 월요일…' 이 붙음
        self.assertIsNone(self.bot.dialog)
        for word in ("2-3 시간표", "2-3 지금", "빈 시간 김철수 이영희"):
            self.assertIn(word, HELP)
        self.env.db.set_meta("timetable", None)
        self.assertIn("불러온 시간표가 없습니다", self.bot.handle_update(msg("2-3 시간표")))
        self.assertIn("불러온 시간표가 없습니다", self.bot.handle_update(msg("빈 시간 박영어")))

    def test_common_free_time_includes_me_and_lists_days(self):
        out = self.run_cmd("빈 시간 박영어")
        self.assertIn("나 · 박영어 · 이번 주 11/2~11/8", out)
        self.assertIn("화(11/3) 3교시 10:50~11:40", out)
        self.assertIn("수(11/4) 2교시 09:50~10:40, 3교시 10:50~11:40", out)
        self.assertNotIn("월(11/2)", out)                                      # 월요일은 둘 중 누군가 항상 수업
        nxt = self.run_cmd("빈 시간 박영어 다음주")
        self.assertIn("다음 주 11/9~11/15", nxt)
        sat = self.run_cmd("빈 시간 박영어", today=SAT)
        self.assertIn("이번 주는 끝나서 다음 주", sat)
        self.assertIn("11/9~11/15", sat)
        self.assertNotIn("한가람", out)

    def test_most_free_slots_when_nobody_is_free_together_and_missing_me(self):
        out = self.run_cmd("빈 시간 박영어 최수학 정과학")
        self.assertIn("같이 비는 시간", out)
        if "모두 비는 시간이 없습니다" in out:
            self.assertIn("많이 비는 순", out)
            self.assertRegex(out, r"\d명 중 \d명 빔 · 수업: ")
        self.env.db.set_meta("timetable_me", None)
        self.assertIn("'나' 는 빼고", self.run_cmd("빈 시간 박영어"))
        self.assertIn("찾지 못했습니다", self.run_cmd("빈 시간 없는선생"))
        self.assertIn("이름을 함께 보내세요", self.run_cmd("빈 시간"))
        # 학사일정: 수업 없는 날(휴업)은 빼고 '확인 필요' 날은 표시
        schoolcal.save(self.env.db, 2026, [{"date": "2026-11-04", "end_date": None, "text": "개교기념일", "kind": "event", "category": "학사"},
                                           {"date": "2026-11-05", "end_date": None, "text": "체육대회", "kind": "event", "category": "학사"}], "나이스")
        self.env.db.set_meta("timetable_me", 0)
        out = self.run_cmd("빈 시간 박영어")
        self.assertNotIn("수(11/4)", out)
        self.assertIn("목(11/5) 1교시 08:50~09:40, 2교시 09:50~10:40, 3교시 10:50~11:40 ⚠ 학사일정 확인", out)

    def test_several_matches_are_picked_by_button_or_number(self):
        reply = self.bot.handle_update(msg("빈 시간 학"))                       # 최수학·정과학 둘 다 '학'
        self.assertIn("여러 명", reply)
        self.assertIn("1. 최수학 (수학)", reply)
        self.assertIn("2. 정과학 (과학)", reply)
        buttons = {b["text"]: b["callback_data"] for row in self.fake.sent[-1]["reply_markup"]["inline_keyboard"] for b in row}
        self.assertTrue(all(len(v.encode()) <= 64 for v in buttons.values()))
        self.assertIsNone(self.bot.handle_update(press(buttons["2"], chat=6)))
        self.assertIn("번호를", self.bot.handle_update(msg("7")))               # 범위 밖
        self.bot.handle_update(press(buttons["2"]))
        self.assertIn("나 · 정과학", self.fake.edited[-1]["text"])               # 같은 메시지가 결과로 바뀜
        self.assertIsNone(self.bot.dialog)
        self.assertIn("오래된", self.bot.handle_update(press(buttons["2"])))
        self.bot.handle_update(msg("빈 시간 학"))                               # 글 번호로도
        self.assertIn("나 · 최수학", self.bot.handle_update(msg("1")))
        self.bot.handle_update(msg("빈 시간 학 학"))                            # 두 사람 모두 모호하면 차례로
        self.assertIn("여러 명", self.bot.handle_update(msg("1")))
        self.assertIn("최수학 · 정과학", self.bot.handle_update(msg("2")))


if __name__ == "__main__":
    unittest.main()
