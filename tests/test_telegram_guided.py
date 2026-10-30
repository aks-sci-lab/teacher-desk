import time
import unittest
from unittest.mock import patch

from desk import ai, schoolcal
from desk.collect import add_text_item
from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_telegram_more import CHAT, msg, press


class TelegramGuidedTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.env.db.set_meta("telegram_chat_id", CHAT)
        self.env.db.replace_students([{"number": 7, "name": "최별빛"}])

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def texts(self):
        return "\n".join(m["text"] for m in self.fake.sent)

    def last_button(self, row=0, col=0):
        return self.fake.sent[-1]["reply_markup"]["inline_keyboard"][row][col]["callback_data"]

    def school_calendar(self):
        schoolcal.save(self.env.db, 2026, [
            {"date": "2026-10-29", "end_date": None, "text": "수업", "kind": "event", "category": "학사"},
            {"date": "2026-11-02", "end_date": None, "text": "재량휴업일", "kind": "event", "category": "학사"},
        ], "나이스")

    def test_persistent_menu_and_help_only_for_paired_account(self):
        self.assertIsNone(self.bot.handle_update(msg("메뉴", chat=6)))
        self.assertEqual(self.fake.sent, [])
        self.bot.handle_update(msg("/menu"))
        markup = next(m["reply_markup"] for m in reversed(self.fake.sent) if "keyboard" in m["reply_markup"])
        self.assertTrue(markup["is_persistent"])
        self.assertTrue(markup["resize_keyboard"])
        self.assertEqual(markup["keyboard"], [["브리핑", "오늘", "마감", "내 수업"], ["조퇴증", "외출증", "결석계", "메뉴"]])   # 화면을 덜 가리게 2줄
        # 아래 판에 없는 명령은 '메뉴' 아래의 단추로 (누르면 같은 이름의 글을 보낸 것처럼 처리)
        more = self.fake.sent[-1]["reply_markup"]["inline_keyboard"]
        self.assertEqual([b["text"] for row in more for b in row], ["자료 검색", "요약", "새로고침", "상태", "취소"])
        before = len(self.fake.sent)
        self.bot.handle_update(press(more[1][0]["callback_data"]))      # 상태
        self.assertGreater(len(self.fake.sent), before)
        self.assertIsNone(self.bot.handle_update(press("mn:위험한명령")))
        self.bot.handle_update(msg("/help"))
        self.assertIn("단계별", self.texts())
        self.assertIn("취소", self.texts())
        self.assertNotIn("최별빛", self.texts())

    def test_search_dialog_and_rules_only_choice_never_call_ai(self):
        add_text_item(self.env.db, "cool", "연수 신청 안내 11월 3일까지", title="연수 신청")
        with patch.object(self.bot, "ask") as ask:
            self.bot.handle_update(msg("자료 검색"))
            self.assertEqual(self.bot.dialog["kind"], "search")
            self.assertIn("100자", self.bot.handle_update(msg("가" * 101)))
            self.bot.handle_update(msg("연수"))
            self.assertIsNone(self.bot.dialog)
            self.assertIn("연수 신청", self.fake.sent[-1]["text"])
            self.bot.handle_update(msg("/unknown 연수"))
            button = self.last_button(-1, 1)
            self.assertIsNone(self.bot.handle_update(press(button, chat=6)))
            self.bot.handle_update(press(button))
            ask.assert_not_called()
            self.assertIn("이미 처리", self.bot.handle_update(press(button)))

    def test_guided_pass_checks_number_times_then_requires_reason(self):
        self.bot.handle_update(msg("외출증"))
        self.assertIn("번호만", self.bot.handle_update(msg("잘못된 번호")))
        self.assertIn("찾지 못", self.bot.handle_update(msg("99")))
        self.bot.handle_update(msg("7"))
        self.assertEqual(self.bot.dialog["step"], "time")
        self.assertIn("13:30 14:30", self.bot.handle_update(msg("25:10 26:10")))
        self.assertIn("복귀 시각", self.bot.handle_update(msg("14:30 13:30")))
        self.assertEqual(self.bot.dialog["step"], "time")
        self.bot.handle_update(msg("13:30 14:30"))
        self.assertIsNone(self.bot.dialog)
        self.assertIsNone(self.env.db.one("SELECT * FROM passes"))
        reason = self.last_button()
        self.bot.handle_update(press(reason))
        record = self.env.db.one("SELECT * FROM passes")
        self.assertEqual((record["student_number"], record["time_out"], record["time_return"]), (7, "13:30", "14:30"))
        self.assertNotIn("최별빛", self.texts())

    def test_now_button_auth_expiry_and_stale_step(self):
        self.bot.handle_update(msg("조퇴증"))
        self.bot.handle_update(msg("7"))
        button = self.last_button()
        self.assertIsNone(self.bot.handle_update(press(button, chat=6)))
        self.assertEqual(self.bot.dialog["step"], "time")
        with patch("desk.telegram.rounded_now", return_value="14:05"):
            self.bot.handle_update(press(button))
        self.assertIn("14:05", self.fake.sent[-1]["text"])
        self.assertIn("오래된", self.bot.handle_update(press(button)))
        self.bot.handle_update(msg("조퇴증"))
        self.bot.handle_update(msg("7"))
        button = self.last_button()
        self.bot.dialog["expires"] = time.monotonic() - 1
        self.assertIn("10분", self.bot.handle_update(press(button)))
        self.assertIsNone(self.bot.dialog)

    def test_cancel_invalidates_pending_pass_and_old_dialog(self):
        self.bot.handle_update(msg("조퇴 7"))
        old_reason = self.last_button()
        self.bot.handle_update(msg("결석계"))
        self.bot.handle_update(msg("7"))
        old_type = self.last_button()
        self.bot.handle_update(msg("취소"))
        self.assertEqual(self.env.db.get_meta("telegram_pending"), {})
        self.assertIsNone(self.bot.dialog)
        self.bot.handle_update(press(old_reason))
        self.assertIsNone(self.env.db.one("SELECT * FROM passes"))
        self.assertIn("오래된", self.bot.handle_update(press(old_type)))
        self.bot.handle_update(msg("검색"))
        self.bot.dialog["expires"] = time.monotonic() - 1
        self.assertIn("10분", self.bot.handle_update(msg("연수")))
        self.assertIsNone(self.bot.dialog)

    def test_absence_guided_uses_local_roster_and_requires_confirm_once(self):
        self.school_calendar()
        self.bot.handle_update(msg("/absence"))
        self.bot.handle_update(msg("7"))
        type_button = self.last_button(0)
        self.bot.handle_update(press(type_button))
        self.assertIn("현재 단계", self.bot.handle_update(press(type_button)))
        self.assertIn("날짜", self.bot.handle_update(msg("틀린 날짜")))
        result = self.bot.handle_update(msg("2026-10-29~2026-11-03"))
        self.assertIn("수업일 3일", result)
        self.assertIsNone(self.env.db.one("SELECT * FROM absences"))
        save = self.last_button()
        self.assertIsNone(self.bot.handle_update(press(save, chat=6)))
        with patch("desk.telegram.printing.start_silent_pass") as print_pass:
            self.bot.handle_update(press(save))
            print_pass.assert_not_called()
        self.assertEqual(len(self.env.db.query("SELECT * FROM absences")), 1)
        self.assertIn("오래된", self.bot.handle_update(press(save)))
        self.assertEqual(len(self.env.db.query("SELECT * FROM absences")), 1)
        self.assertNotIn("최별빛", self.texts())
        record = self.env.db.one("SELECT * FROM absences")
        self.assertEqual(record["student_name"], "최별빛")
        self.assertEqual(record["absence_type"], "질병")

    def test_absence_does_not_save_without_school_calendar(self):
        self.bot.handle_update(msg("결석계"))
        self.bot.handle_update(msg("7"))
        self.bot.handle_update(press(self.last_button()))
        self.assertIn("학사일정", self.bot.handle_update(msg("2026-10-29")))
        self.assertEqual(self.bot.dialog["step"], "date")
        self.assertIsNone(self.env.db.one("SELECT * FROM absences"))
        self.bot.handle_update(msg("/cancel"))
        self.assertIsNone(self.bot.dialog)

    def test_absence_revalidates_current_roster_before_confirming(self):
        self.school_calendar()
        self.bot.handle_update(msg("결석계"))
        self.bot.handle_update(msg("7"))
        self.bot.handle_update(press(self.last_button()))
        self.bot.handle_update(msg("2026-10-29"))
        save = self.last_button()
        self.env.db.replace_students([])
        self.assertIn("명단", self.bot.handle_update(press(save)))
        self.assertIsNone(self.env.db.one("SELECT * FROM absences"))

    def test_absence_roster_name_change_cannot_save_as_different_student(self):
        self.school_calendar()
        self.bot.handle_update(msg("결석계"))
        self.bot.handle_update(msg("7"))
        self.bot.handle_update(press(self.last_button()))
        self.bot.handle_update(msg("2026-10-29"))
        save = self.last_button()
        self.env.db.replace_students([{"number": 7, "name": "이가람"}])
        self.assertIn("명단이 바뀌", self.bot.handle_update(press(save)))
        self.assertIsNone(self.env.db.one("SELECT * FROM absences"))
        self.assertNotIn("이가람", self.texts())

    def test_status_reports_connection_error_and_never_uses_external_ai(self):
        self.bot.handle_update(msg("상태"))
        self.assertIn("AI 꺼짐", self.fake.sent[-1]["text"])
        self.env.config.values["ai_enabled"] = True
        with patch("desk.telegram.ai.models", side_effect=ai.AIError("연결 안 됨")):
            self.bot.handle_update(msg("/status"))
        self.assertIn("AI 연결 오류", self.fake.sent[-1]["text"])
        self.assertNotIn("TEST:TOKEN", self.texts())
        self.bot.handle_update(msg("조퇴증"))
        self.bot.unpair()
        self.assertIsNone(self.bot.dialog)
