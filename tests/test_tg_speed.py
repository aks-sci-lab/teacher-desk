"""텔레그램 단추 반응 속도 (0.5.87): 연결을 다시 써서 단추 한 번에 새 HTTPS 연결을 열지 않음."""

import http.client
import ssl
import time
import unittest
import urllib.error
from unittest.mock import patch

from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_telegram_more import CHAT, msg, press


class DeadConnection:
    """이미 닫힌 연결 흉내: 보내자마자 정해 둔 오류 (타이밍에 기대지 않는 결정적 시험)."""

    def __init__(self, error, *args, **kwargs):
        self.error, self.closed, self.sock, self.timeout = error, False, None, None

    def request(self, *args, **kwargs):
        raise self.error

    def close(self):
        self.closed = True


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

    def plant(self, error):
        dead = DeadConnection(error)
        self.bot.local.__dict__.setdefault("conns", {})[self.fake.base] = dead   # 쉬는 동안 닫힌 '다시 쓰는 연결'
        return dead

    def test_reconnects_once_for_every_closed_connection_error(self):
        # 0.5.88: 윈도우는 닫힌 연결에 ConnectionAbortedError(WinError 10053) — 0.5.87 은 이것을 잡지 않아 첫 동작이 실패
        errors = [ConnectionAbortedError(10053, "현재 연결은 사용자의 호스트 시스템의 소프트웨어의 의해 중단되었습니다"),
                  ConnectionResetError(10054, "reset"), BrokenPipeError(32, "pipe"),
                  http.client.RemoteDisconnected("closed"), http.client.CannotSendRequest("idle"),
                  ssl.SSLEOFError(8, "EOF occurred in violation of protocol")]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.bot.local.__dict__.pop("conns", None)
                self.fake.connections = 0
                dead = self.plant(error)
                self.assertTrue(self.bot.api("getMe"))        # 새 연결로 다시 보내서 성공
                self.assertTrue(dead.closed)
                self.assertEqual(self.fake.connections, 1)    # 새 연결은 한 번만
                self.assertIsNot(self.bot.local.conns[self.fake.base], dead)

    def test_retries_only_once_and_not_for_other_errors(self):
        self.plant(ConnectionAbortedError(10053, "aborted"))
        made = []

        def new_conn(*args, **kwargs):   # 새 연결도 끊기면 더 다시 보내지 않음
            made.append(1)
            return DeadConnection(ConnectionAbortedError(10053, "aborted"))
        with patch.object(http.client, "HTTPConnection", new_conn):
            with self.assertRaises(urllib.error.URLError):
                self.bot.api("getMe")
        self.assertEqual(len(made), 1)
        self.assertFalse(self.bot.local.conns)
        for error in (TimeoutError("timed out"), ValueError("bad")):   # 시간 초과 등은 보낸 글이 갔을 수 있어 다시 보내지 않음
            with self.subTest(error=type(error).__name__):
                self.plant(error)
                with self.assertRaises(type(error)):
                    self.bot.api("getMe")
                self.assertFalse(self.bot.local.conns)
        self.assertEqual(self.fake.connections, 0)

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
