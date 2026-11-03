import socket
import threading
import time
import types
import unittest
import urllib.error
from unittest.mock import patch

from desk import VERSION, telegram
from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_telegram_more import CHAT, msg, press


def wait_for(cond, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class FlakyServer:
    """처음 몇 번은 응답을 중간에 끊고(IncompleteRead), 그 뒤에는 정상 응답."""

    def __init__(self, broken):
        self.broken, self.hits = broken, 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            c.recv(65536)
            self.hits += 1
            if self.hits <= self.broken:
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{\"ok\": tr")
            else:
                body = b'{"ok": true, "result": []}'
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
            c.close()

    def close(self):
        self.sock.close()


class PollingResilienceTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.bots = []

    def tearDown(self):
        for bot in self.bots:    # DB 를 닫기 전에 폴링 스레드부터 멈춤
            bot.stop()
            if bot.thread:
                bot.thread.join(3)
        self.env.close()

    def test_polling_thread_survives_cut_off_responses_and_recovers(self):
        """응답이 중간에 끊겨(IncompleteRead) 스레드가 조용히 죽던 것 (선생님 신고: 앱은 켜져 있는데 명령에 답이 없음)."""
        server = FlakyServer(broken=2)
        self.addCleanup(server.close)
        self.env.config.values.update(telegram_token="T:T", telegram_api_base=f"http://127.0.0.1:{server.port}")
        bot = Bot(self.env.db, self.env.config, self.env.collector)
        with patch.object(telegram, "POLL_BACKOFF", 0.05):
            bot.start()
            self.bots.append(bot)
            self.assertTrue(wait_for(lambda: bot.last_poll is not None))        # 끊긴 응답 두 번 뒤에 정상으로 돌아옴
        self.assertTrue(bot.alive())
        self.assertIn("연결", bot.status)
        self.assertIn("IncompleteRead", bot.last_error)                          # 원인이 화면·'상태' 에 남음
        warns = [r["message"] for r in self.env.db.query("SELECT message FROM logs WHERE level = 'warn'") if "IncompleteRead" in r["message"]]
        self.assertEqual(len(warns), 1)                                          # 같은 오류는 기록에 한 번만

    def test_unexpected_error_in_loop_does_not_kill_thread(self):
        fake = FakeTelegram()
        self.addCleanup(fake.close)
        self.env.config.values.update(telegram_token="T:T", telegram_api_base=fake.base)
        bot = Bot(self.env.db, self.env.config, self.env.collector)
        calls = {"n": 0}
        real = bot.api

        def api(method, params=None, timeout=35):
            if method == "getUpdates":
                calls["n"] += 1
                if calls["n"] == 1:
                    raise KeyError("예상 못 한 오류")            # 연결 오류가 아닌 오류
            return real(method, params, timeout)
        bot.api = api
        with patch.object(telegram, "POLL_BACKOFF", 0.05):
            bot.start()
            self.bots.append(bot)
            self.assertTrue(wait_for(lambda: calls["n"] >= 2 and bot.last_poll))
        self.assertTrue(bot.alive())
        self.assertIn("KeyError", bot.last_error)

    def test_command_error_is_noted_and_loop_goes_on(self):
        fake = FakeTelegram()
        self.addCleanup(fake.close)
        self.env.config.values.update(telegram_token="T:T", telegram_api_base=fake.base)
        self.env.db.set_meta("telegram_chat_id", 111)
        bot = Bot(self.env.db, self.env.config, self.env.collector)
        boom = {"n": 0}
        real = bot.handle_update

        def handle(u):
            boom["n"] += 1
            if boom["n"] == 1:
                raise RuntimeError("처리 중 오류")
            return real(u)
        bot.handle_update = handle
        bot.start()
        self.bots.append(bot)
        fake.push("상태")
        fake.push("상태")
        self.assertTrue(wait_for(lambda: bot.last_handled))
        self.assertIn("명령 처리", bot.last_error)
        self.assertTrue(any("처리 중 오류가 났습니다" in m["text"] for m in fake.sent))   # 선생님께도 알림
        self.assertIn("최근 오류", bot.answer("status"))

    def test_offset_is_saved_before_handling_and_kept_for_same_token_only(self):
        fake = FakeTelegram()
        self.addCleanup(fake.close)
        self.env.config.values.update(telegram_token="T:T", telegram_api_base=fake.base)
        self.env.db.set_meta("telegram_chat_id", 111)
        bot = Bot(self.env.db, self.env.config, self.env.collector)
        seen = []
        real = bot.handle_update

        def handle(u):
            seen.append(self.env.db.get_meta("telegram_offset")["offset"])   # 처리하는 순간 이미 저장돼 있음
            return real(u)
        bot.handle_update = handle
        bot.start()
        fake.push("상태")
        self.assertTrue(wait_for(lambda: bot.last_handled))
        bot.stop()
        self.assertEqual(seen, [self.env.db.get_meta("telegram_offset")["offset"]])
        saved = self.env.db.get_meta("telegram_offset")["offset"]
        self.assertEqual(Bot(self.env.db, self.env.config).saved_offset("T:T"), saved)   # 다시 켜면 이어서
        self.assertIsNone(Bot(self.env.db, self.env.config).saved_offset("OTHER:TOKEN"))  # 토큰이 바뀌면 처음부터


class BacklogAndWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="T:T", telegram_api_base=self.fake.base)
        self.env.db.set_meta("telegram_chat_id", 111)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)

    def tearDown(self):
        self.bot.stop()
        if self.bot.thread:
            self.bot.thread.join(3)
        self.fake.close()
        self.env.close()

    def test_commands_piled_up_while_pc_was_off_are_not_run(self):
        """PC 가 꺼져 있던 동안 쌓인 명령(텔레그램은 24시간 보관)이 켜지자마자 한꺼번에 실행되던 것 — 발급·업데이트 명령이 뜻하지 않게 실행될 수 있음."""
        self.env.db.replace_students([{"number": 7, "name": "한가람"}])
        now = time.time()
        self.fake.push("조퇴 7 13:30", date=now - 3600)          # 1시간 전 (실행하지 않음)
        self.fake.push_button("pr:abc123:0")                      # 보낸 시각을 알 수 없는 단추: 켠 뒤 첫 묶음이라 실행하지 않음
        self.fake.push("상태", date=now - 5)                      # 방금 보낸 명령은 처리
        self.bot.start()
        self.assertTrue(wait_for(lambda: self.bot.last_handled))
        self.assertTrue(wait_for(lambda: any("실행하지 않았습니다" in m["text"] for m in self.fake.sent)))
        texts = "\n".join(m["text"] for m in self.fake.sent)
        self.assertIn("업무 데스크 상태", texts)                    # '상태' 는 답함
        self.assertNotIn("사유를 고르면", texts)                    # 1시간 전 '조퇴' 는 시작하지 않음
        self.assertIn("2개는 실행하지 않았습니다", texts)
        self.assertEqual(self.fake.edited, [])                    # 오래된 단추도 처리하지 않음 (낡은 글 편집 없음)
        self.assertEqual(len(self.env.db.get_meta("telegram_pending") or {}), 0)

    def test_stale_rule(self):
        now = 1_000_000
        fresh = {"message": {"date": now - 30, "chat": {"id": 1}}}
        old = {"message": {"date": now - 700, "chat": {"id": 1}}}
        undated = {"message": {"chat": {"id": 1}}}
        button = {"callback_query": {"data": "x", "message": {"chat": {"id": 1}}}}
        stale = lambda u, backlog: Bot.is_stale(u, backlog, now)
        self.assertFalse(stale(fresh, True))
        self.assertTrue(stale(old, False))                      # 글은 오래되면 언제나
        self.assertFalse(stale(undated, True))
        self.assertTrue(stale(button, True))                    # 단추는 오래 끊겼다 돌아온 첫 묶음에서만
        self.assertFalse(stale(button, False))
        self.assertEqual(Bot.update_chat(button), 1)

    def test_live_button_is_still_handled_after_start(self):
        self.bot.start()
        self.assertTrue(wait_for(lambda: self.bot.last_poll))
        self.fake.push_button("uc:0")                             # 앱이 켜져 있는 동안 누른 단추는 처리
        self.assertTrue(wait_for(lambda: self.fake.edited))
        self.assertIn("업데이트", self.fake.edited[-1]["text"])

    def test_watchdog_restarts_a_dead_polling_thread(self):
        class Kill(BaseException):
            pass
        calls = []
        real = self.bot._loop

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise Kill()                      # 어떤 예외로도 스레드가 끝나는 경우를 흉내
            return real()
        self.bot._loop = flaky
        with patch.object(telegram, "WATCH_EVERY", 0.05), patch("threading.excepthook", lambda args: None):
            self.bot.start()
            self.assertTrue(wait_for(lambda: len(calls) >= 2 and self.bot.alive() and self.bot.last_poll))
        warns = [r["message"] for r in self.env.db.query("SELECT message FROM logs") if "수신이 멈춰 있어" in r["message"]]
        self.assertEqual(len(warns), 1)


class RefreshInBackgroundTests(unittest.TestCase):
    def test_refresh_does_not_block_polling_thread(self):
        env = Env()
        fake = FakeTelegram()
        self.addCleanup(fake.close)
        self.addCleanup(env.close)
        env.config.values.update(telegram_token="T:T", telegram_api_base=fake.base)
        env.db.set_meta("telegram_chat_id", CHAT)
        release = threading.Event()

        class SlowCollector:
            lock = threading.Lock()

            def run(self, reason="manual", slot_keys=()):
                with self.lock:
                    release.wait(5)
                return {"items": 0, "events": 0, "deadlines": 0, "errors": [], "new_ids": [], "sources": []}
        bot = Bot(env.db, env.config, SlowCollector())
        started = time.time()
        reply = bot.handle_update(msg("새로고침"))
        self.assertLess(time.time() - started, 1.5)                    # 수집이 끝나길 기다리지 않고 바로 답함
        self.assertIn("지금 수집합니다", reply)
        self.assertTrue(wait_for(lambda: SlowCollector.lock.locked()))
        self.assertIn("이미 수집 중", bot.handle_update(msg("새로고침")))
        release.set()
        self.assertTrue(wait_for(lambda: any("새로 들어온 자료가 없습니다" in m["text"] for m in fake.sent)))


class UpdateButtonTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="T:T", telegram_api_base=self.fake.base)
        self.env.db.set_meta("telegram_chat_id", CHAT)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.applied = []
        self.state = {"current": VERSION, "latest": "9.9.9", "available": True, "portable": True, "checked_at": "x", "error": None}
        self.bot.updates = types.SimpleNamespace(state=lambda: dict(self.state), check=lambda: dict(self.state),
                                                 apply=lambda force: self.applied.append(force))

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def test_old_version_button_gets_visible_reject_and_latest_gets_confirm(self):
        self.bot.handle_update(press("up:9.9.8"))                       # 낡은 버전 단추
        self.assertIn("바뀌었거나 이미 처리했습니다", self.fake.edited[-1]["text"])
        self.assertEqual(self.fake.edited[-1]["reply_markup"], {"inline_keyboard": []})
        self.bot.handle_update(press("ua:9.9.8"))                       # 낡은 '시작' 단추도 못 바꿈
        self.assertEqual(self.applied, [])
        self.assertIn("바뀌었거나", self.fake.edited[-1]["text"])
        self.bot.handle_update(press("up:9.9.9"))                       # 지금 새 버전의 단추 → 확인 단계
        self.assertIn("업데이트할까요", self.fake.edited[-1]["text"])
        self.assertEqual(self.applied, [])
        self.state.update(available=False, latest=VERSION)              # 이미 업데이트된 뒤 낡은 단추
        self.bot.handle_update(press("ua:9.9.9"))
        self.assertEqual(self.applied, [])
        self.assertIn("바뀌었거나", self.fake.edited[-1]["text"])

    def test_reject_is_sent_as_new_message_when_editing_fails(self):
        real = self.bot.api

        def api(method, params=None, timeout=35):
            if method == "editMessageText":
                raise urllib.error.HTTPError("u", 400, "Bad Request: message can't be edited", {}, None)
            return real(method, params, timeout)
        self.bot.api = api
        self.bot.handle_update(press("up:9.9.8"))
        self.assertIn("바뀌었거나 이미 처리했습니다", self.fake.sent[-1]["text"])   # 고치지 못해도 새 글로 알림

    def test_button_without_message_id_still_answers(self):
        update = press("up:9.9.8")
        del update["callback_query"]["message"]["message_id"]
        self.bot.handle_update(update)
        self.assertIn("바뀌었거나 이미 처리했습니다", self.fake.sent[-1]["text"])


if __name__ == "__main__":
    unittest.main()
