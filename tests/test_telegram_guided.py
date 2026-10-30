import time
import unittest
from datetime import date
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

    def test_words_mixed_with_command_words_go_to_search_choices_not_commands(self):
        """'오늘 식단표' 가 오늘 브리핑이 되던 것 (명령 낱말이 섞인 자연스러운 말은 검색·질문으로)."""
        for text in ("오늘 식단표", "마감 언제야", "결석 인원 알려줘", "조퇴 인원 알려줘", "브리핑 자료 어디 있어", "요약 좀 길게 해줘"):
            before = len(self.fake.sent)
            reply = self.bot.handle_update(msg(text))
            self.assertIn("알아듣지 못했습니다", reply, text)
            self.assertEqual(len(self.fake.sent), before + 1, text)
            data = [b["callback_data"] for row in self.fake.sent[-1]["reply_markup"]["inline_keyboard"] for b in row]
            self.assertTrue(any(d.endswith(":search") for d in data), text)   # AI 없이 자료 검색 단추
            self.assertIsNone(self.env.db.one("SELECT * FROM passes"))
            self.assertIsNone(self.bot.dialog, text)

    def test_plain_commands_and_polite_tails_still_run(self):
        add_text_item(self.env.db, "cool", "연수 신청 안내 11월 3일까지", title="연수 신청")
        for text in ("오늘", "오늘 일정", "오늘 업무", "마감", "마감 일정", "브리핑 보여줘", "/today", "/today 식단표", "/deadlines@bot"):
            reply = self.bot.handle_update(msg(text))
            self.assertNotIn("알아듣지 못했습니다", reply, text)
        self.assertIn("업무", self.bot.handle_update(msg("오늘")))
        self.assertIn("마감", self.bot.handle_update(msg("마감 보여줘")))
        self.assertIn("알아듣지 못했습니다", self.bot.handle_update(msg("/모르는명령")))

    def test_arg_commands_only_when_arguments_fit(self):
        self.school_calendar()
        self.assertIn("7번 최○○ 조퇴증", self.bot.handle_update(msg("7번 조퇴 1시 반")) or "")   # 자연스러운 말은 그대로
        self.bot.handle_update(msg("/cancel"))
        self.assertIn("최○○", self.bot.handle_update(msg("조퇴 7 13:30")))
        self.assertIn("번호를 함께", self.bot.handle_update(msg("조퇴 1시 반")))      # 숫자로 시작하면 명령이 안내
        self.assertIn("최○○", self.bot.handle_update(msg("외출 7 13:30 14:30")))
        text = self.bot.handle_update(msg("결석계 최별빛 7 병결 2026-10-29~2026-11-03"))
        self.assertIn("저장", text)
        self.assertEqual(self.bot.handle_update(msg("결석계")) is None, False)        # 인자 없으면 단계별 입력
        self.assertEqual(self.bot.dialog["kind"], "absence")
        self.assertIn("알아듣지 못했습니다", self.bot.handle_update(msg("/그만")))

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
        self.assertIsNone(self.env.db.one("SELECT * FROM passes"))   # 학부모 확인 전
        self.bot.handle_update(press(self.fake.edited[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]))
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

    def calendar_flow(self):
        self.school_calendar()
        self.bot.handle_update(msg("결석계"))
        self.bot.handle_update(msg("7"))
        self.bot.handle_update(press(self.last_button(0)))   # 종류 단추 → 같은 메시지가 달력으로
        return self.fake.edited[-1]["reply_markup"]["inline_keyboard"]

    def find(self, keyboard, label):
        return next(b["callback_data"] for row in keyboard for b in row if b["text"] == label)

    def test_absence_calendar_edits_same_message_and_picks_range(self):
        sent_before = len(self.fake.sent)
        kb = self.calendar_flow()
        self.assertEqual(len(self.fake.sent), sent_before + 2)   # 번호 안내·종류 단추만 새로 보냄
        today = date.today()
        self.assertEqual(kb[0][1]["text"], f"{today.year}년 {today.month}월")
        self.assertEqual([b["text"] for b in kb[1]], list("일월화수목금토"))
        self.assertTrue(all(len(b["callback_data"].encode()) <= 64 for row in kb for b in row))
        kb = self.bot_press_edit(self.find(kb, "▶"))   # 다음 달로
        kb = self.bot_press_edit(self.find(kb, "◀"))
        self.assertEqual(self.fake.edited[-1]["message_id"], 1)

    def bot_press_edit(self, data):
        self.bot.handle_update(press(data))
        return self.fake.edited[-1]["reply_markup"]["inline_keyboard"]

    def go_month(self, kb, year, month):
        while True:
            title = kb[0][1]["text"]
            if title == f"{year}년 {month}월":
                return kb
            kb = self.bot_press_edit(kb[0][2]["callback_data"] if title < f"{year}년 {month}월" else kb[0][0]["callback_data"])

    def test_absence_calendar_range_marks_closed_day_and_saves(self):
        kb = self.calendar_flow()
        kb = self.go_month(kb, 2026, 10)
        kb = self.bot_press_edit(self.find(kb, "29"))
        self.assertIn("▶29", [b["text"] for row in kb for b in row])
        self.assertIn("시작일 10/29", self.fake.edited[-1]["text"])
        kb = self.go_month(kb, 2026, 11)
        self.assertIn("(2)", [b["text"] for row in kb for b in row])   # 재량휴업일 표시
        self.bot.handle_update(press(self.find(kb, "3")))
        self.assertIn("수업일 3일", self.fake.edited[-1]["text"])   # 10/29·10/30·11/3 (11/2 휴업, 주말 제외)
        self.assertIsNone(self.env.db.one("SELECT * FROM absences"))
        self.bot.handle_update(press(self.fake.edited[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]))
        record = self.env.db.one("SELECT * FROM absences")
        self.assertEqual((record["start_date"], record["end_date"]), ("2026-10-29", "2026-11-03"))
        self.assertNotIn("최별빛", self.texts())
        self.assertNotIn("최별빛", "\n".join(m["text"] for m in self.fake.edited))

    def test_absence_calendar_one_day_buttons_and_earlier_pick_restarts(self):
        kb = self.go_month(self.calendar_flow(), 2026, 10)
        kb = self.bot_press_edit(self.find(kb, "30"))
        kb = self.bot_press_edit(self.find(kb, "29"))     # 시작일보다 앞 → 새 시작일
        self.assertIn("시작일 10/29", self.fake.edited[-1]["text"])
        self.bot.handle_update(press(self.find(kb, "하루만")))
        self.assertIn("2026-10-29~2026-10-29 · 수업일 1일", self.fake.edited[-1]["text"])
        self.bot.handle_update(msg("/cancel"))
        kb = self.go_month(self.calendar_flow(), 2026, 10)
        kb = self.bot_press_edit(self.find(kb, "30"))
        self.bot.handle_update(press(self.find(kb, "▶30")))   # 같은 날 두 번
        self.assertIn("2026-10-30~2026-10-30", self.fake.edited[-1]["text"])

    def test_absence_calendar_text_input_still_works_and_bad_buttons_rejected(self):
        self.calendar_flow()
        did = self.bot.dialog["id"]
        self.assertIn("달력에 없는", self.bot.handle_update(press(f"dlg:{did}:cd:20261340")))
        self.assertIn("달력에 없는", self.bot.handle_update(press(f"dlg:{did}:cm:209913")))
        self.assertIn("수업일 3일", self.bot.handle_update(msg("2026-10-29~2026-11-03")))

    def test_absence_calendar_without_school_calendar_keeps_calendar(self):
        self.bot.handle_update(msg("결석계"))
        self.bot.handle_update(msg("7"))
        self.bot.handle_update(press(self.last_button(0)))
        kb = self.go_month(self.fake.edited[-1]["reply_markup"]["inline_keyboard"], 2026, 10)
        kb = self.bot_press_edit(self.find(kb, "29"))
        self.bot.handle_update(press(self.find(kb, "하루만")))
        self.assertIn("학사일정", self.texts())
        self.assertEqual(self.bot.dialog["step"], "date")
        self.assertIsNone(self.env.db.one("SELECT * FROM absences"))

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
