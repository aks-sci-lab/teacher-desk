import time
import unittest
from datetime import date

from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env


def wait_for(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class BotTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)

    def tearDown(self):
        self.bot.stop()
        if self.bot.thread:
            self.bot.thread.join(5)  # 폴링이 끝난 뒤 DB 를 닫음
        self.fake.close()
        self.env.close()

    def texts(self):
        return [m["text"] for m in self.fake.sent]

    def test_pairing_and_commands(self):
        code = self.bot.pair_code()
        self.assertIsNone(self.bot.handle_update({"message": {"text": "오늘", "chat": {"id": 5, "type": "private"}}}))
        self.bot.handle_update({"message": {"text": "/start 000000" if code != "000000" else "/start 111111",
                                            "chat": {"id": 5, "type": "private"}}})
        self.assertIsNone(self.bot.chat_id)
        self.bot.handle_update({"message": {"text": f"/start {code}", "chat": {"id": 5, "type": "private"}}})
        self.assertEqual(self.bot.chat_id, 5)
        self.assertNotEqual(self.bot.pair_code(), code)  # 한 번 쓴 코드는 버림
        # 다른 사람은 무시
        self.assertIsNone(self.bot.handle_update({"message": {"text": "오늘", "chat": {"id": 6, "type": "private"}}}))
        # 단체방은 무시
        self.assertIsNone(self.bot.handle_update({"message": {"text": "오늘", "chat": {"id": 5, "type": "group"}}}))
        reply = self.bot.handle_update({"message": {"text": "/today@my_bot", "chat": {"id": 5, "type": "private"}}})
        self.assertIn("오늘 일정", reply)
        self.assertIn("도움말", self.bot.handle_update({"message": {"text": "뭐지", "chat": {"id": 5, "type": "private"}}}))

    def test_wrong_codes_rotate(self):
        code = self.bot.pair_code()
        wrong = "/start 999999" if code != "999999" else "/start 888888"
        for _ in range(5):
            self.bot.handle_update({"message": {"text": wrong, "chat": {"id": 7, "type": "private"}}})
        self.assertNotEqual(self.bot.pair_code(), code)

    def test_never_sends_student_names(self):
        db = self.env.db
        db.execute("INSERT INTO passes(serial, kind, student_name, date, created_at) VALUES('1', 'out', '김비밀', ?, 'x')",
                   (date.today().isoformat(),))
        db.execute("INSERT INTO passes(serial, kind, student_name, date, reason, created_at) "
                   "VALUES('2', 'early', '이비밀', ?, '병원', 'x')", (date.today().isoformat(),))
        text = self.bot.answer("today")
        self.assertIn("조퇴 1명 · 외출 1명", text)
        self.assertNotIn("비밀", text)
        self.assertNotIn("병원", text)

    def test_detail_levels(self):
        (self.env.edu / "연수 안내.txt").write_text("신청: 2099년 1월 2일까지\n대상: 전 교원", encoding="utf-8")
        stats = self.env.collector.run("manual")
        self.env.config.values["telegram_detail"] = "counts"
        msg = self.bot.collect_message(stats)
        self.assertIn("새 자료 1건", msg)
        self.assertNotIn("연수 안내", msg)
        self.env.config.values["telegram_detail"] = "titles"
        self.assertIn("연수 안내", self.bot.collect_message(stats))
        self.assertNotIn("대상", self.bot.answer("summary"))
        self.env.config.values["telegram_detail"] = "summary"
        self.assertIn("대상: 전 교원", self.bot.answer("summary"))
        self.assertIsNone(self.bot.collect_message({"items": 0}))

    def test_polling_loop_with_fake_server(self):
        code = self.bot.pair_code()
        self.bot.start()
        self.fake.push(f"/start {code}", chat_id=42)
        self.assertTrue(wait_for(lambda: self.bot.chat_id == 42))
        self.fake.push("새로고침", chat_id=42)
        self.assertTrue(wait_for(lambda: any("새로 들어온 자료가 없습니다" in t for t in self.texts())), self.texts())
        self.assertIn("setMyCommands", self.fake.calls)
        # 수집 알림: 새 자료가 있을 때만 보냄
        before = len(self.fake.sent)
        self.bot.notify_collect({"items": 0}, "schedule")
        self.assertEqual(len(self.fake.sent), before)
        (self.env.cool / "m.txt").write_text("내일 회의", encoding="utf-8")
        self.env.collector.notify = self.bot.notify_collect
        self.env.collector.run("schedule")
        self.assertTrue(wait_for(lambda: len(self.fake.sent) > before))
        self.assertIn("새 자료 1건", self.fake.sent[-1]["text"])
        self.assertEqual(self.fake.sent[-1]["chat_id"], 42)


if __name__ == "__main__":
    unittest.main()
