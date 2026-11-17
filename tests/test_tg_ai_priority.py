"""명령 낱말이 든 질문이 AI 검색을 가로채지 않기 (0.6.6).
'쪽지 보내는 방법 알려줘'·'7번 조퇴 관련 공문 있어?' 는 명령이 아니라 질문, AI 질문을 받는 중에는 '취소'·'/…' 만 명령."""
import unittest

from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_coolsend import FakeUI
from tests.test_telegram_more import CHAT, msg, press


class AIPriorityTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.ui = self.bot.cool_ui = FakeUI()
        self.env.db.set_meta("telegram_chat_id", CHAT)
        self.env.db.replace_students([{"number": 7, "name": "김민준"}])
        self.asked = []
        self.bot.ask = lambda cid, q: self.asked.append(q) or "asked"

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def last(self):
        return self.fake.sent[-1]

    def actions(self):
        return [b["callback_data"].rsplit(":", 1)[-1] for row in self.last()["reply_markup"]["inline_keyboard"] for b in row]

    def test_memo_question_is_not_memo(self):
        self.bot.handle_update(msg("쪽지 보내는 방법 알려줘"))
        self.assertIn("ai", self.actions())                  # 보통의 '알아듣지 못함' 선택지 (AI 검색 단추)
        self.assertNotIn("cool", self.actions())
        self.assertEqual(self.ui.calls, [])   # 쿨메신저 창을 건드리지 않음
        self.assertIsNone(self.bot.dialog)

    def test_ambiguous_memo_asks_first_and_both_ways_work(self):
        self.bot.handle_update(msg("쪽지 이영희 선생님이 보낸 회의 내용 찾아줘"))
        self.assertIn("쪽지로 보낼 글인가요", self.last()["text"])
        self.assertEqual(self.actions(), ["cool", "ai", "search"])
        self.assertEqual(self.ui.calls, [])                   # 쿨메신저 창은 아직 건드리지 않음
        ai = self.last()["reply_markup"]["inline_keyboard"][1][0]["callback_data"]
        self.bot.handle_update(press(ai))
        self.assertEqual(self.asked, ["쪽지 이영희 선생님이 보낸 회의 내용 찾아줘"])
        self.bot.handle_update(msg("쪽지 이영희 내일 회의 몇 시예요?"))
        cool = self.last()["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        self.bot.handle_update(press(cool))
        self.assertIn("받는 사람: 이영희", self.last()["text"])
        self.assertEqual(self.ui.sent, [])                   # 보내기는 그다음 [보내기] 단추

    def test_plain_memo_and_slash_unchanged(self):
        self.bot.handle_update(msg("쪽지 이영희 안녕하세요"))
        self.assertIn("받는 사람: 이영희", self.last()["text"])
        self.bot.handle_update(msg("/쪽지 이영희 회의 몇 시예요?"))   # '/' 로 시작하면 늘 명령
        self.assertIn("받는 사람: 이영희", self.last()["text"])

    def test_pass_question_is_not_pass(self):
        self.bot.handle_update(msg("7번 조퇴 관련 공문 있어?"))
        self.assertIn("ai", self.actions())
        self.assertEqual(self.env.db.get_meta("telegram_pending") or {}, {})
        self.bot.handle_update(msg("조퇴 7 사유 알려줘"))
        self.assertIn("ai", self.actions())
        self.bot.handle_update(msg("조퇴 7 13:30"))           # 명령 모양은 그대로
        self.assertEqual(len(self.env.db.get_meta("telegram_pending")), 1)

    def test_ai_dialog_takes_command_words_as_question(self):
        asked = []
        self.bot.offer_general = lambda cid, q: asked.append(q) or "general"
        self.bot.handle_update(msg("AI 질문"))
        self.bot.handle_update(msg("쪽지 김철수 회의 내용 정리해줘"))
        self.assertEqual(asked, ["쪽지 김철수 회의 내용 정리해줘"])
        self.bot.handle_update(msg("AI 질문"))
        self.bot.handle_update(msg("취소"))                    # '취소' 는 그대로 명령
        self.assertIsNone(self.bot.dialog)
        self.assertEqual(len(asked), 1)

    def test_ai_search_prefix(self):
        self.bot.handle_update(msg("AI 검색 쪽지 보내는 방법"))
        self.bot.handle_update(msg("ai검색 7번 조퇴 관련 공문"))
        self.assertEqual(self.asked, ["쪽지 보내는 방법", "7번 조퇴 관련 공문"])
        self.bot.handle_update(msg("AI 검색"))
        self.bot.handle_update(msg("답장 3 내용 정리해줘"))
        self.assertEqual(self.asked[-1], "답장 3 내용 정리해줘")
        self.assertEqual(self.ui.sent, [])


if __name__ == "__main__":
    unittest.main()
