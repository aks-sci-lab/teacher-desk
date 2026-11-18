"""텔레그램 메뉴 현행화 (0.6.10): 새 기능이 메뉴에 있고, 메뉴가 바뀐 버전으로 켜지면 아래 단추 판을 새로 보냄."""

import unittest
from unittest.mock import patch

from desk.collect import add_text_item
from desk.telegram import BOT_MENU, HELP, MENU_SIG, SUBMENUS, Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_telegram_more import CHAT, msg, press


class TelegramMenuCurrentTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.env.db.set_meta("telegram_chat_id", CHAT)

    def tearDown(self):
        self.fake.close()
        self.env.close()

    @staticmethod
    def labels(message):
        return {b["text"]: b["callback_data"] for row in message["reply_markup"]["inline_keyboard"] for b in row}

    def test_new_features_are_in_every_menu(self):
        commands = [c["command"] for c in BOT_MENU]
        for c in ("aisearch", "cool", "memo", "reply", "attach", "free", "tomorrow", "myclass", "stats", "doc", "index", "update"):
            self.assertIn(c, commands)
        self.assertEqual(len(commands), len(set(commands)))
        actions = [a for _, rows in SUBMENUS.values() for row in rows for _, a in row]
        for a in ("cool", "memo", "aisearch", "free", "doc", "index", "update"):
            self.assertIn(a, actions)
        for word in ("AI 검색", "쪽지", "답장 2", "첨부 2", "빈 시간", "/문서", "/색인", "업데이트", "통화·문자"):
            self.assertIn(word, HELP)

    def test_cool_list_numbers_recent_messages_for_reply_and_attach(self):
        add_text_item(self.env.db, "cool", "내일 회의 자료 보내 주세요\n(보낸 사람: 김철수)", title="회의 자료",
                      received_at="2026-11-17 09:00:00")
        add_text_item(self.env.db, "neis", "공문 내용", title="공문", received_at="2026-11-17 10:00:00")
        for word in ("쪽지", "/cool", "💬", "첨부"):
            if word == "💬":
                self.bot.handle_update(press("mn:cool"))
            else:
                self.bot.handle_update(msg(word))
            text = self.fake.sent[-1]["text"]
            self.assertIn("최근 쿨메신저 쪽지 1건", text, word)
            self.assertIn("김철수", text)
            self.assertNotIn("공문", text)
            self.assertIn("✉ 새 쪽지 보내기", self.labels(self.fake.sent[-1]))
        with patch.object(Bot, "begin_cool_reply", return_value="확인") as reply:
            self.bot.handle_update(msg("답장 1"))          # 내용 없이 번호만 → 내용을 물음
            self.assertEqual(self.bot.dialog["kind"], "coolreply")
            self.bot.handle_update(msg("자료는 오후에 보내겠습니다"))
            reply.assert_called_once_with(CHAT, 1, "자료는 오후에 보내겠습니다")

    def test_memo_and_free_dialogs_from_menu(self):
        with patch.object(Bot, "begin_cool_memo", return_value="확인") as memo:
            self.bot.handle_update(press("mn:memo"))
            self.assertEqual(self.bot.dialog["kind"], "coolmemo")
            self.bot.handle_update(msg("김철수 내일 협의회는 3시입니다"))
            memo.assert_called_once_with(CHAT, "김철수", "내일 협의회는 3시입니다")
            self.assertIsNone(self.bot.dialog)
            self.bot.handle_update(msg("/memo"))
            self.bot.handle_update(msg("취소"))
            self.assertIsNone(self.bot.dialog)
        self.env.config.values["telegram_cool_send"] = False
        self.bot.handle_update(msg("쪽지 보내기"))
        self.assertIsNone(self.bot.dialog)
        self.assertIn("꺼져 있습니다", self.fake.sent[-1]["text"])
        with patch.object(Bot, "timetable_command", return_value="빈 시간") as tt:
            self.bot.handle_update(press("mn:free"))
            self.assertEqual(self.bot.dialog["kind"], "freenames")
            self.bot.handle_update(msg("김철수 이영희 다음주"))
            kind, args = tt.call_args.args[1:3]
            self.assertEqual((kind, args["names"], args["next"]), ("free", ["김철수", "이영희"], True))
        self.bot.handle_update(press("mn:aisearch"))
        self.assertEqual(self.bot.dialog["kind"], "aisearch")

    def test_changed_menu_resends_keyboard_once(self):
        self.assertTrue(self.bot.announce_menu())   # 예전 버전에서 올라옴: 표시가 없음
        sent = self.fake.sent[-1]
        self.assertIn("메뉴를", sent["text"])
        self.assertIn("keyboard", sent["reply_markup"])
        self.assertEqual(self.env.db.get_meta("telegram_menu_sig"), MENU_SIG)
        before = len(self.fake.sent)
        self.assertFalse(self.bot.announce_menu())   # 같은 메뉴로 다시 켜면 보내지 않음
        self.assertEqual(len(self.fake.sent), before)
        self.env.db.set_meta("telegram_menu_sig", "old")
        self.env.db.set_meta("telegram_chat_id", None)
        self.assertFalse(self.bot.announce_menu())   # 연결 전에는 보내지 않음


if __name__ == "__main__":
    unittest.main()
