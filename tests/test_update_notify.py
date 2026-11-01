import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from desk import updater
from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from desk import VERSION
from tests.test_telegram_more import CHAT, msg, press
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


class FakeBusy(Exception):
    status, message = 409, "자료 수집 중입니다."


class TelegramUpdateApplyTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.env.db.set_meta("telegram_chat_id", CHAT)
        self.state = {"current": VERSION, "latest": "9.9.9", "available": True, "portable": True,
                      "checked_at": "x", "error": None}
        self.apply = Mock()
        self.bot.updates = types.SimpleNamespace(state=lambda: dict(self.state), check=lambda: dict(self.state),
                                                 apply=self.apply)

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def buttons(self, message):
        return {b["text"]: b["callback_data"] for row in message["reply_markup"]["inline_keyboard"] for b in row}

    def test_command_offers_update_then_asks_before_applying(self):
        self.bot.handle_update(msg("업데이트"))
        offer = self.buttons(self.fake.sent[-1])
        self.assertIn("9.9.9", self.fake.sent[-1]["text"])
        self.assertTrue(all(len(v.encode()) <= 64 for v in offer.values()))
        self.assertIsNone(self.bot.handle_update(press(offer["⬆ 지금 업데이트"], chat=6)))   # 다른 사람은 못 누름
        self.bot.handle_update(press(offer["⬆ 지금 업데이트"]))
        self.apply.assert_not_called()                       # 한 번 누른 것만으로는 바꾸지 않음
        confirm = self.buttons(self.fake.edited[-1])
        self.assertIn("1~2분", self.fake.edited[-1]["text"])
        self.bot.handle_update(press(confirm["업데이트 시작"]))
        self.apply.assert_called_once_with(False)
        self.assertEqual(self.env.db.get_meta("telegram_update_pending"), "9.9.9")
        self.assertIn("시작합니다", self.fake.edited[-1]["text"])

    def test_cancel_stale_and_unavailable(self):
        self.bot.handle_update(msg("/update"))
        self.bot.handle_update(press(self.buttons(self.fake.sent[-1])["⬆ 지금 업데이트"]))
        self.bot.handle_update(press(self.buttons(self.fake.edited[-1])["취소"]))
        self.assertIn("취소했습니다", self.fake.edited[-1]["text"])
        self.apply.assert_not_called()
        self.state["latest"] = "9.9.10"                      # 그 사이 더 새 버전이 나옴 → 낡은 단추는 거절
        self.bot.handle_update(press("ua:9.9.9"))
        self.assertIn("바뀌었거나", self.fake.edited[-1]["text"])
        self.apply.assert_not_called()
        self.state.update(available=False)
        self.assertIn("최신 버전", self.bot.handle_update(msg("업데이트")))
        self.state.update(available=True, portable=False)
        self.assertIn("개발용 폴더", self.bot.handle_update(msg("업데이트")))
        self.state.update(portable=True, error="새 버전을 확인하지 못했습니다")
        self.assertIn("확인하지 못했습니다", self.bot.handle_update(msg("업데이트")))
        self.bot.updates = None
        self.assertIn("쓸 수 없습니다", self.bot.handle_update(msg("업데이트")))

    def test_busy_update_needs_explicit_force_button(self):
        self.apply.side_effect = FakeBusy()
        self.bot.handle_update(press("ua:9.9.9"))
        self.assertIsNone(self.env.db.get_meta("telegram_update_pending"))
        force = self.buttons(self.fake.sent[-1])["중단하고 업데이트"]
        self.assertIn("수집 중", self.fake.sent[-1]["text"])
        self.apply.side_effect = None
        self.apply.reset_mock()
        self.bot.handle_update(press(force))
        self.apply.assert_called_once_with(True)

    def test_new_version_push_has_update_button_only_when_connected(self):
        self.bot.updates = None
        self.bot.notify_update({"version": "9.9.9"})
        self.assertNotIn("reply_markup", self.fake.sent[-1])
        self.env.db.set_meta("telegram_update_notified", None)
        self.bot.updates = types.SimpleNamespace(state=None, check=None, apply=None)
        self.bot.notify_update({"version": "9.9.9"})
        self.assertEqual(self.buttons(self.fake.sent[-1]), {"⬆ 지금 업데이트": "up:9.9.9"})

    def test_result_is_announced_after_restart_once(self):
        self.assertFalse(self.bot.announce_update_result())          # 시작한 업데이트가 없으면 조용히
        self.env.db.set_meta("telegram_update_pending", VERSION)
        self.assertTrue(self.bot.announce_update_result())
        self.assertIn("업데이트되어", self.fake.sent[-1]["text"])
        self.assertFalse(self.bot.announce_update_result())          # 한 번만
        self.env.db.set_meta("telegram_update_pending", "9.9.9")
        self.bot.announce_update_result()
        self.assertIn("끝나지 않은", self.fake.sent[-1]["text"])


if __name__ == "__main__":
    unittest.main()
