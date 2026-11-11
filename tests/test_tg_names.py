"""텔레그램 학생 이름 (0.5.93 선생님 결정 2026-11-11): 선생님 본인 채팅이라 기본은 이름 그대로,
설정 '텔레그램에서 학생 이름 가리기' 를 켜면 김○○. 조퇴·외출 사유는 설정과 관계없이 보내지 않음."""

import unittest

from desk.collect import add_text_item
from desk.config import BOOL_KEYS, DEFAULTS
from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_telegram_more import CHAT, msg, press

NAME = "최별빛"


class TelegramNameTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.env.db.set_meta("telegram_chat_id", CHAT)
        self.env.db.replace_students([{"number": 7, "name": NAME, "parent_phone": "010-1111-2222"}])

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def texts(self):
        return "\n".join(m.get("text", "") for m in self.fake.sent + self.fake.edited)

    def issue(self):
        """조퇴증 신청 → 사유 단추 → 학부모 확인 → 발급."""
        self.bot.handle_update(msg("조퇴 7 13:30"))
        reason = self.fake.sent[-1]["reply_markup"]["inline_keyboard"][0]
        self.bot.handle_update(press(reason[0]["callback_data"]))
        confirm = self.fake.edited[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        self.bot.handle_update(press(confirm))
        return reason[0]["text"]

    def test_setting_defaults_off_and_is_a_saved_switch(self):
        self.assertFalse(DEFAULTS["telegram_mask_names"])
        self.assertIn("telegram_mask_names", BOOL_KEYS)

    def test_names_shown_by_default_but_reasons_never(self):
        reason = self.issue()
        text = self.texts()
        self.assertIn(f"7번 {NAME} 조퇴증", text)
        self.assertNotIn(reason, text)        # 사유는 단추 글로만, 답장·확인·발급 글에는 없음
        self.assertNotIn("○○", text)

    def test_setting_on_masks_everything_like_before(self):
        self.env.config.values["telegram_mask_names"] = True
        reason = self.issue()
        add_text_item(self.env.db, "cool", f"{NAME} 학생 체험학습 안내입니다", title=f"{NAME} 체험학습 안내")
        self.bot.handle_update(msg("안 읽은 자료"))
        text = self.texts()
        self.assertIn("7번 최○○ 조퇴증", text)
        self.assertIn("○○○ 체험학습 안내", text)
        self.assertNotIn(NAME, text)
        self.assertNotIn(reason, text)

    def test_logs_and_error_lines_always_mask(self):
        err = OSError(f"{NAME} 때문에 연결 실패")
        self.assertNotIn(NAME, self.bot.describe_error(err))   # 기록·상태 화면에도 남는 글은 설정과 관계없이 가림
        self.env.config.values["telegram_mask_names"] = True
        self.assertNotIn(NAME, self.bot.describe_error(err))
        self.assertEqual(self.bot.mask(f"{NAME} 상담"), "○○○ 상담")
        self.env.config.values["telegram_mask_names"] = False
        self.assertEqual(self.bot.mask(f"{NAME} 상담"), f"{NAME} 상담")


if __name__ == "__main__":
    unittest.main()
