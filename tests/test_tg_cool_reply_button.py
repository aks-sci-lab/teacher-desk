"""원문에서 바로 답장 (0.6.9): 쿨메신저 쪽지 [원문 보기]·'원문 n'·요약 글에 [↩ 답장 보내기] 단추.
누르면 답장 내용을 받고, 받은 뒤에는 '답장 n 내용' 과 같은 확인 글([보내기]를 눌러야 나감)로 이어짐.
창 조작은 가짜(FakeUI). 실제 쿨메신저 창에서 되는지는 학교 PC 에서만 알 수 있음."""

import unittest

from desk import telegram
from desk.collect import add_text_item
from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_coolsend import FakeUI
from tests.test_telegram_more import CHAT, msg, press


class CoolReplyButtonTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.ui = self.bot.cool_ui = FakeUI()
        self.env.db.set_meta("telegram_chat_id", CHAT)
        self.item = add_text_item(self.env.db, "cool", "회의 자료 확인 부탁드립니다\n\n(보낸 사람: 이영희)",
                                  title="협의회 안내")["id"]

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def buttons(self, k=-1):
        return [b for row in (self.fake.sent[k].get("reply_markup") or {}).get("inline_keyboard", []) for b in row]

    def test_original_button_asks_body_then_confirms_before_sending(self):
        self.bot.handle_update(press(f"o:{self.item}"))
        self.assertIn("회의 자료 확인", self.fake.sent[-1]["text"])
        reply = [b for b in self.buttons() if b["text"] == "↩ 답장 보내기"]
        self.assertEqual(reply[0]["callback_data"], f"rp:{self.item}")
        self.bot.handle_update(press(reply[0]["callback_data"]))
        self.assertIn("이영희 님께 보낼 답장 내용", self.fake.sent[-1]["text"])
        self.assertIn("제목: RE: 협의회 안내", self.fake.sent[-1]["text"])
        # 명령 낱말('오늘')이 들어 있어도 답장 내용으로, 아직 보내지 않음
        self.bot.handle_update(msg("오늘 오후에 확인하겠습니다"))
        card = self.fake.sent[-1]["text"]
        self.assertIn("받는 사람: 이영희", card)
        self.assertIn("오늘 오후에 확인하겠습니다", card)
        self.assertEqual(self.ui.sent, [])
        self.bot.handle_update(press(self.buttons()[0]["callback_data"]))
        self.assertEqual(self.ui.sent[0]["title"], "RE: 협의회 안내")
        self.assertEqual(self.ui.sent[0]["body"], "오늘 오후에 확인하겠습니다")

    def test_text_command_original_has_button_and_cancel_stops(self):
        self.env.db.set_meta("telegram_refs", [self.item])
        self.bot.handle_update(msg("원문 1"))
        self.bot.handle_update(press(self.buttons()[-1]["callback_data"]))
        self.bot.handle_update(msg("취소"))
        self.assertIn("취소했습니다", self.fake.sent[-1]["text"])
        self.bot.handle_update(msg("네 알겠습니다"))   # 취소 뒤 글은 답장이 아니라 보통 검색으로
        self.assertNotIn("받는 사람", self.fake.sent[-1]["text"])
        self.assertEqual(self.ui.sent, [])

    def test_long_original_sent_as_file_gets_button_message(self):
        long_item = add_text_item(self.env.db, "cool", "가" * (telegram.TEXT_AS_FILE + 10) + "\n\n(보낸 사람: 이영희)",
                                  title="긴 안내")["id"]
        self.bot.handle_update(press(f"o:{long_item}"))
        self.assertEqual(self.buttons()[0]["callback_data"], f"rp:{long_item}")

    def test_no_button_when_off_or_not_cool_or_no_sender(self):
        other = add_text_item(self.env.db, "edufine", "공문 본문", title="공문")["id"]
        nosender = add_text_item(self.env.db, "cool", "보낸 사람 줄 없음", title="쪽지")["id"]
        for iid in (other, nosender):
            self.bot.handle_update(press(f"o:{iid}"))
            self.assertEqual(self.buttons(), [])
        self.env.config.values["telegram_cool_send"] = False
        self.bot.handle_update(press(f"o:{self.item}"))
        self.assertEqual(self.buttons(), [])
        self.bot.handle_update(press(f"rp:{self.item}"))   # 예전 단추를 눌러도 꺼져 있으면 안내만
        self.assertIn("꺼져 있습니다", self.fake.sent[-1]["text"])
        self.assertIsNone(self.bot.dialog)

    def test_summary_message_has_reply_next_to_original(self):
        self.bot.cool_summary_wait = 0
        self.bot._send_cool_summary(self.item, "시험")
        labels = [b["text"] for b in self.buttons()]
        self.assertEqual(labels, ["원문 보기", "↩ 답장 보내기"])


if __name__ == "__main__":
    unittest.main()
