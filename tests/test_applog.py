"""사용 기록 파일 (0.6.13): 동작만 남기고 학생 이름·전화번호·토큰은 가림, 텔레그램 '로그' 로 파일 받기."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desk import applog
from desk.telegram import BOT_MENU, HELP, MENU_ACTIONS, P_LOG, SUBMENUS, Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_telegram_more import CHAT, msg, press


class AppLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "logs"
        self.names = ["김가온"]
        self.on = True
        applog._names = ([], 0.0)
        applog.setup(self.dir, names=lambda: self.names, enabled=lambda: self.on)

    def tearDown(self):
        applog.shutdown()
        applog._names = ([], 0.0)
        self.tmp.cleanup()

    def text(self):
        return "\n".join(p.read_text(encoding="utf-8") for p in applog.files())

    def test_masks_names_phone_token_email(self):
        applog.event("기록", "김가온 조퇴증 발급", note="010-1234-5678 a@b.kr /bot123456:ABCDEFGHIJKLMNOP")
        t = self.text()
        self.assertNotIn("김가온", t)
        self.assertIn("○○○ 조퇴증 발급", t)
        for secret in ("1234-5678", "a@b.kr", "ABCDEFGHIJKLMNOP"):
            self.assertNotIn(secret, t)

    def test_off_writes_nothing_and_no_setup_is_safe(self):
        self.on = False
        applog.event("web", "click 브리핑")
        self.assertEqual(applog.files(), [])
        applog.shutdown()
        applog.event("web", "click 브리핑")   # setup 전: 아무 일 없음
        self.assertEqual(applog.read_lines(), [])

    def test_timed_and_exception_lines(self):
        with applog.timed("ai", "LM Studio 요청", model="gemma") as extra:
            extra["chars_out"] = 12
        with self.assertRaises(ValueError):
            with applog.timed("ai", "LM Studio 요청"):
                raise ValueError("김가온 때문에")
        t = self.text()
        self.assertRegex(t, r"INFO  ai +LM Studio 요청 \| ms=\S+ model=gemma chars_out=12")
        self.assertIn("error=ValueError", t)
        self.assertIn("test_applog.py:", t)   # 코드 위치
        self.assertNotIn("김가온", t)

    def test_export_has_summary_and_masks_names_added_later(self):
        applog.event("web", "click 브리핑")
        applog.event("web", "click 브리핑")
        applog.event("tg.in", "글 (2낱말)", chars=8)
        applog.event("기록", "새로온학생 결석계", "warn")
        self.names = ["김가온", "새로온학생"]
        applog._names = ([], 0.0)
        data, n = applog.export_text("9.9.9", days=3)
        text = data.decode("utf-8")
        self.assertEqual(n, 4)
        self.assertIn("버전 9.9.9", text)
        self.assertIn("2 × web click 브리핑", text)
        self.assertIn("오류·경고", text)
        self.assertNotIn("새로온학생", text)

    def test_rotation_keeps_total_small(self):
        applog.shutdown()
        with patch.object(applog, "MAX_BYTES", 2000), patch.object(applog, "BACKUPS", 2):
            applog.setup(self.dir, names=lambda: [])
            for i in range(200):
                applog.event("web", f"click 단추 {i}", view="today")
        self.assertLessEqual(len(applog.files()), 3)
        self.assertLess(sum(p.stat().st_size for p in applog.files()), 3 * 2000 + 500)

    def test_db_log_goes_to_file(self):
        env = Env()
        try:
            env.db.log("김가온 조퇴증 발급 1")
        finally:
            env.close()
        self.assertRegex(self.text(), r"기록 +○○○ 조퇴증 발급 1")


class TelegramLogTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.env.db.replace_students([{"number": 7, "name": "김가온"}])
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.env.db.set_meta("telegram_chat_id", CHAT)
        applog._names = ([], 0.0)
        applog.setup(Path(self.env.tmp.name) / "logs", names=lambda: [s["name"] for s in self.env.db.students()],
                     enabled=lambda: self.env.config["usage_log"])

    def tearDown(self):
        applog.shutdown()
        applog._names = ([], 0.0)
        self.fake.close()
        self.env.close()

    def test_log_words(self):
        for word in ("로그", "/log", "로그 7", "로그 7일", "로그 전체", "사용 기록 보내줘", "/log@desk_bot"):
            self.assertTrue(P_LOG.match(word), word)
        for word in ("로그인 방법", "로그 보는 법", "로그인"):
            self.assertFalse(P_LOG.match(word), word)
        self.assertIn("log", [c["command"] for c in BOT_MENU])
        self.assertIn("log", [a for _, rows in SUBMENUS.values() for row in rows for _, a in row])
        self.assertEqual(MENU_ACTIONS["log"], "로그")
        self.assertIn("로그 7", HELP)

    def test_sends_file_without_content(self):
        self.bot.send("김가온 조퇴 사유: 병원 진료", CHAT)   # 보낸 글은 지문만 남아야 함
        what, fields = self.bot.describe_update(msg("김가온 오늘 몇 시에 나가?"))
        applog.event("tg.in", what, **fields)
        self.bot.handle_update(msg("로그"))
        self.assertEqual(self.fake.calls[-1], "sendDocument")
        doc = self.fake.docs[-1].decode("utf-8", "replace")
        self.assertIn("업무 데스크 사용 기록", doc)
        self.assertIn("tg.out", doc)
        self.assertIn("from=", doc)
        self.assertIn("글 (5낱말)", doc)
        for secret in ("김가온", "병원", "몇 시"):
            self.assertNotIn(secret, doc)

    def test_button_and_file_descriptions(self):
        what, _ = self.bot.describe_update(press("choice:abcdef123456:ai"))
        self.assertEqual(what, "단추 choice:#:ai")
        what, f = self.bot.describe_update({"message": {"chat": {"id": CHAT, "type": "private"},
                                                        "document": {"file_name": "김가온 상담.hwp", "file_size": 2048}}})
        self.assertEqual(what, "파일 .hwp")
        self.assertEqual(f["kb"], 2.0)

    def test_off_setting(self):
        self.env.config.values["usage_log"] = False
        self.bot.handle_update(msg("로그"))
        self.assertIn("꺼져 있습니다", self.fake.sent[-1]["text"])


if __name__ == "__main__":
    unittest.main()
