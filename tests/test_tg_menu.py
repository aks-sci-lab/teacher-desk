"""텔레그램 메뉴 (0.5.78): 웹 화면 메뉴와 같은 묶음·순서, 단추로 끝나는 흐름, 새 조회의 이름 가림."""

import unittest
from datetime import date, timedelta
from unittest.mock import patch

from desk import absence, queries
from desk.collect import add_text_item
from desk.telegram import (ARG_COMMANDS, BOT_MENU, COMMANDS, KEY_TEXT, KEYBOARD, MAIN_MENU, MENU_ACTIONS,
                           SUBMENUS, TEXT_LIMIT, Bot)
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_telegram_more import CHAT, msg, press

NAME = "최별빛"


class TelegramMenuTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.env.db.set_meta("telegram_chat_id", CHAT)
        self.env.db.replace_students([{"number": 7, "name": NAME}])

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def all_text(self):
        return "\n".join(m["text"] for m in self.fake.sent + self.fake.edited)

    @staticmethod
    def labels(message):
        return {b["text"]: b["callback_data"] for row in message["reply_markup"]["inline_keyboard"] for b in row}

    def test_menu_matches_web_order_and_every_button_is_known(self):
        self.assertEqual([k for row in KEYBOARD for k in row], [label for row in MAIN_MENU for label, _ in row][:8] + ["☰ 메뉴"])
        for label in (k for row in KEYBOARD for k in row):
            self.assertIn(KEY_TEXT[label], COMMANDS, label)   # 단추 글은 모두 명령
        self.assertEqual([c["command"] for c in BOT_MENU][:8],
                         ["brief", "week", "inbox", "calendar", "timetable", "attendance", "search", "ask"])
        for c in BOT_MENU:   # '/' 메뉴의 명령은 모두 알아들음
            slash = "/" + c["command"]
            self.assertTrue(slash in COMMANDS or slash in ARG_COMMANDS or c["command"] == "addevent", slash)
            self.assertLessEqual(len(c["description"]), 256)
        rows = MAIN_MENU + [r for _, rs in SUBMENUS.values() for r in rs]
        for row in rows:
            for _, action in row:
                self.assertTrue(action in MENU_ACTIONS or action.startswith("g:"), action)
                self.assertLessEqual(len(f"mn:{action}".encode()), 64)
        for text in MENU_ACTIONS.values():
            self.assertTrue(text in COMMANDS or text.split()[0] in ARG_COMMANDS or text == "일정 추가", text)
        self.assertIsNone(self.bot.handle_update(press("mn:g:없는묶음")))
        self.assertIsNone(self.bot.handle_update(press("mn:위험한명령")))
        self.assertIsNone(self.bot.handle_update(press("mn:early", chat=6)))   # 연결 안 된 사람

    def test_keyboard_buttons_and_groups_navigate_without_typing(self):
        self.assertIn("업무", self.bot.handle_update(msg("📋 브리핑")))
        before = len(self.fake.sent)
        self.bot.handle_update(msg("☑ 출결"))   # 단추 판의 묶음 → 새 메시지로 하위 메뉴
        self.assertEqual(len(self.fake.sent), before + 1)
        sub = self.labels(self.fake.sent[-1])
        self.assertEqual(list(sub), ["🏃 조퇴증", "🚶 외출증", "📝 결석계", "📊 이번 달 통계", "← 메뉴"])
        self.bot.handle_update(press(sub["🏃 조퇴증"], message_id=before + 1))   # 단추로 조퇴증 시작
        self.assertEqual(self.bot.dialog["kind"], "early")
        self.bot.handle_update(press(sub["← 메뉴"], message_id=before + 1))      # 같은 메시지를 메인 메뉴로
        self.assertEqual(self.fake.edited[-1]["message_id"], before + 1)
        self.assertIn("☑ 출결", self.labels(self.fake.edited[-1]))
        self.bot.handle_update(press("mn:g:timetable"))
        self.assertIn("📚 내일 수업", self.labels(self.fake.edited[-1]))
        for word in ("📥 수신함", "📅 달력", "▦ 시간표", "/inbox", "/calendar", "/timetable", "/attendance"):
            self.bot.handle_update(msg(word))
            self.assertEqual(list(self.labels(self.fake.sent[-1]))[-1], "← 메뉴", word)
        self.bot.handle_update(msg("🔍 검색"))
        self.assertEqual(self.bot.dialog["kind"], "search")
        self.bot.handle_update(msg("☰ 메뉴"))
        self.assertIsNone(self.bot.dialog)
        self.assertIn("🗓 주간", self.labels(self.fake.sent[-1]))

    def test_old_commands_still_work(self):
        for word, part in (("오늘", "업무"), ("마감", "마감"), ("요약", "자료"), ("상태", "상태"), ("내 수업", "시간표")):
            self.assertIn(part, self.bot.handle_update(msg(word)), word)
        self.bot.handle_update(msg("조퇴증"))
        self.assertEqual(self.bot.dialog["kind"], "early")
        self.bot.handle_update(msg("취소"))
        self.bot.handle_update(msg("시간표 바꾸는 법"))   # 명령 모양이 아니면 묶음 메뉴로 가로채지 않음 (검색·질문 쪽)
        self.assertNotIn("← 메뉴", self.labels(self.fake.sent[-1]) if "reply_markup" in self.fake.sent[-1]
                         and "inline_keyboard" in self.fake.sent[-1]["reply_markup"] else {})

    def test_week_plan_masks_names_and_pages_to_next_week(self):
        today = date.today()
        queries.insert_event(self.env.db, {"date": today.isoformat(), "kind": "deadline", "text": f"{NAME} 상담 기록 제출",
                                           "category": "제출·마감"})
        nxt = today + timedelta(days=7)
        queries.insert_event(self.env.db, {"date": nxt.isoformat(), "kind": "event", "text": "학년 협의회", "category": "참고"})
        self.env.db.execute("INSERT INTO todos(text, date, created_at) VALUES(?, ?, ?)", (f"{NAME} 학부모 전화", None, "2026-11-01"))
        out = self.bot.handle_update(msg("🗓 주간"))
        self.assertIn("이번 주 계획", out)
        self.assertIn("상담 기록 제출", out)
        self.assertIn("학부모 전화", out)
        self.assertNotIn(NAME, self.all_text())
        week = self.labels(self.fake.sent[-1])
        self.bot.handle_update(press(week["다음 주 ▶"], message_id=len(self.fake.sent)))
        self.assertIn("다음 주 계획", self.fake.edited[-1]["text"])
        self.assertIn("학년 협의회", self.fake.edited[-1]["text"])
        self.assertIn("◀ 이번 주", self.labels(self.fake.edited[-1]))
        self.env.config.values["telegram_detail"] = "counts"
        self.assertNotIn("상담", self.bot.week_text())

    def test_unread_masks_names_pages_and_keeps_reference_numbers(self):
        ids = []
        for n in range(13):
            it = add_text_item(self.env.db, "cool", f"{NAME} 관련 안내 {n}번 내용입니다", title=f"{NAME} 안내 {n}",
                               received_at=f"2026-11-0{1 + n % 5} 0{n % 10}:00:00")
            ids.append(it)
        out = self.bot.handle_update(msg("안 읽은 자료"))
        self.assertIn("안 읽은 자료 13건 (1~10)", out)
        self.assertNotIn(NAME, self.all_text())
        more = self.labels(self.fake.sent[-1])
        self.assertIn("더보기 ▸ (3건 더)", more)
        self.bot.handle_update(press(more["더보기 ▸ (3건 더)"]))
        self.assertIn("(11~13)", self.fake.sent[-1]["text"])
        self.assertIn("\n11. ", self.fake.sent[-1]["text"])
        self.assertEqual(len(self.env.db.get_meta("telegram_refs")), 13)   # '원문 11' 이 11번째를 가리킴
        self.assertIn("11 원문", self.labels(self.fake.sent[-1]))
        self.env.db.execute("UPDATE items SET status = 'read'")
        self.assertIn("없습니다", self.bot.handle_update(msg("/unread")))
        self.env.db.execute("UPDATE items SET status = 'new'")
        self.env.config.values["telegram_detail"] = "counts"
        self.assertNotIn("안내", self.bot.handle_update(msg("/unread")))

    def test_month_stats_are_class_totals_without_names_or_reasons(self):
        today = date.today()
        for kind in ("early", "early", "out"):
            queries.issue_pass(self.env.db, {"kind": kind, "student_number": 7, "student_name": NAME, "date": today.isoformat(),
                                             "time_out": "13:00", "reason": "병원 진료"})
        absence.save(self.env.db, {"student_number": 7, "student_name": NAME, "absence_type": "질병", "reason": "몸살",
                                   "start_date": today.isoformat(), "end_date": today.isoformat()})
        out = self.bot.handle_update(msg("통계"))
        self.assertIn("조퇴 2건 · 🚶 외출 1건", out)
        self.assertIn("결석계 1건", out)
        for secret in (NAME, "병원", "몸살", "7번"):
            self.assertNotIn(secret, self.all_text())
        self.assertEqual(self.bot.handle_update(press("mn:stats")), out)

    def test_ai_question_button_takes_next_text_as_question(self):
        with patch.object(Bot, "offer_general", return_value="동의 묻기") as offer:
            self.bot.handle_update(msg("🤖 AI 질문"))
            self.assertEqual(self.bot.dialog["kind"], "general")
            self.assertIn("업무자료는 붙이지 않습니다", self.fake.sent[-1]["text"])
            self.bot.handle_update(msg("광합성 원리를 쉽게 설명해 줘"))
            offer.assert_called_once_with(CHAT, "광합성 원리를 쉽게 설명해 줘")
            self.assertIsNone(self.bot.dialog)
            self.bot.handle_update(msg("/ask"))            # 질문 없이 /ask 도 단계별로
            self.assertEqual(self.bot.dialog["kind"], "general")

    def test_tomorrow_and_my_class_timetable(self):
        self.assertIn("시간표", self.bot.handle_update(msg("내일 수업")))
        self.env.config.values.update(grade="", class_no="")
        self.assertIn("담임 학급이 설정에 없습니다", self.bot.handle_update(press("mn:myclass")))
        self.env.config.values.update(grade="2", class_no="3")
        self.assertIn("시간표", self.bot.handle_update(msg("우리 반 시간표")))

    def test_long_text_is_split_with_buttons_on_last_part(self):
        text = "\n".join(f"{n}번째 줄 " + "가" * 80 for n in range(120))
        self.assertGreater(len(text), TEXT_LIMIT)
        self.bot.send(text, CHAT, [[{"text": "단추", "callback_data": "mn:home"}]])
        self.assertGreater(len(self.fake.sent), 1)
        self.assertTrue(all(len(m["text"]) <= 4000 for m in self.fake.sent))
        self.assertNotIn("reply_markup", self.fake.sent[0])
        self.assertIn("reply_markup", self.fake.sent[-1])
        self.assertEqual("".join(m["text"] for m in self.fake.sent).count("번째 줄"), 120)   # 잘리지 않음


if __name__ == "__main__":
    unittest.main()
