"""텔레그램 단추 반응 속도 (0.5.87): 연결을 다시 써서 단추 한 번에 새 HTTPS 연결을 열지 않음."""

import time
import unittest
import urllib.error

from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_telegram_more import CHAT, msg, press


class TelegramSpeedTests(unittest.TestCase):
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

    def timed(self, update):
        before, start = self.fake.connections, time.perf_counter()
        self.bot.handle_update(update)
        return time.perf_counter() - start, self.fake.connections - before

    def test_calendar_buttons_reuse_one_connection_and_answer_first(self):
        self.bot.api("getMe")               # 폴링 중이면 이미 열려 있는 연결
        self.fake.connect_delay = 0.3       # 새 연결 하나에 0.3초 (실제 TLS 연결 흉내)
        took, opened = self.timed(msg("일정 추가"))
        did = self.bot.dialog["id"]
        self.assertEqual(opened, 0)
        for update in (press(f"dlg:{did}:cm:202612"), press(f"dlg:{did}:cd:20261215")):
            self.fake.calls.clear()
            took, opened = self.timed(update)
            self.assertEqual(opened, 0)          # 예전에는 단추 한 번에 새 연결 2개 (0.6초+)
            self.assertLess(took, 1.0)           # 목표: 누르고 1초 안에
            self.assertEqual(self.fake.calls[0], "answerCallbackQuery")   # 단추의 '기다리는 중' 표시를 먼저 끔
            self.assertEqual(self.fake.calls[1:], ["editMessageText"])   # 새 메시지가 아니라 같은 메시지를 고침

    def test_reconnects_once_when_server_closed_idle_connection(self):
        self.fake.drop_next = True
        self.bot.api("getMe")                    # 답을 보낸 뒤 서버가 연결을 닫음 (오래 쉬는 동안 닫힌 연결)
        self.assertTrue(self.bot.api("getMe"))   # 새 연결로 다시 보내서 성공
        self.assertEqual(self.fake.connections, 2)

    def test_http_errors_are_still_http_errors(self):
        self.fake.fail_delete = True
        with self.assertRaises(urllib.error.HTTPError) as caught:   # 409 Conflict 처리·오류 문구가 예전처럼 동작
            self.bot.api("deleteMessage", {"chat_id": CHAT, "message_id": 1})
        self.assertEqual(caught.exception.code, 400)
        self.assertNotIn("TEST:TOKEN", self.bot.describe_error(caught.exception))
        self.assertTrue(self.bot.api("getMe"))   # 오류 뒤에도 다음 요청은 됨

    def test_proxy_pc_keeps_old_urllib_way(self):
        self.bot.direct = lambda base: False
        self.bot.api("getMe")
        self.bot.api("getMe")
        self.assertEqual(self.fake.connections, 2)   # 프록시 PC: 예전처럼 urllib (프록시 설정을 따름)
        self.assertFalse(getattr(self.bot.local, "conns", None))


if __name__ == "__main__":
    unittest.main()
