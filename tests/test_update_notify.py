import unittest
from pathlib import Path
from unittest.mock import patch

from desk import updater
from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_telegram_more import CHAT
from tests.test_update_ui import latest_file


class UpdateNotifyTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def checker(self, latest, current="0.5.36"):
        return updater.Checker(current, Path("."), on_new=self.bot.notify_update)

    def test_new_version_is_pushed_once_per_version(self):
        self.env.db.set_meta("telegram_chat_id", CHAT)
        with patch.dict("os.environ", {"DESK_UPDATE_JSON": latest_file("0.5.40")}):
            c = self.checker("0.5.40")
            c.check()
            c.check()                                  # 같은 버전은 다시 보내지 않음
            self.checker("0.5.40").check()            # 다시 켜도(새 확인기) 다시 보내지 않음
        self.assertEqual(len(self.fake.sent), 1)
        text = self.fake.sent[0]["text"]
        self.assertIn("0.5.40", text)
        self.assertIn("직접 누르기 전에는", text)
        with patch.dict("os.environ", {"DESK_UPDATE_JSON": latest_file("0.5.41")}):
            self.checker("0.5.41").check()            # 더 새 버전은 또 알림
        self.assertEqual(len(self.fake.sent), 2)

    def test_no_push_when_up_to_date_unpaired_or_offline(self):
        with patch.dict("os.environ", {"DESK_UPDATE_JSON": latest_file("0.5.40")}):
            self.checker("0.5.40").check()            # 연결 안 된 텔레그램: 보내지 않고 기록도 안 함
            self.assertIsNone(self.env.db.get_meta("telegram_update_notified"))
            self.env.db.set_meta("telegram_chat_id", CHAT)
            self.checker("0.5.40", current="0.5.40").check()   # 이미 최신
        with patch.dict("os.environ", {"DESK_UPDATE_JSON": "file:///없는/주소.json"}):
            self.checker("0.5.40").check()
        self.assertEqual(self.fake.sent, [])

    def test_failed_send_does_not_break_check_and_retries_next_time(self):
        self.env.db.set_meta("telegram_chat_id", CHAT)
        self.env.config.values["telegram_api_base"] = "http://127.0.0.1:1"   # 텔레그램에 닿지 않음
        with patch.dict("os.environ", {"DESK_UPDATE_JSON": latest_file("0.5.40")}):
            st = self.checker("0.5.40").check()
            self.assertTrue(st["available"])
            self.assertIsNone(self.env.db.get_meta("telegram_update_notified"))
            self.env.config.values["telegram_api_base"] = self.fake.base
            self.checker("0.5.40").check()
        self.assertEqual(len(self.fake.sent), 1)


if __name__ == "__main__":
    unittest.main()
