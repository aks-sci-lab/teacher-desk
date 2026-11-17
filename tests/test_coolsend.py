"""쿨메신저 쪽지 보내기 (0.6.5): 창 조작은 가짜(FakeUI)로 바꿔 순서·안전 확인만 시험.
실제 쿨메신저 창에서 되는지는 학교 PC 에서만 알 수 있음 (조직도 두 번 클릭·[보내기] 마우스 클릭 시험은 2026-11-17 확인)."""

import unittest

from desk import coolsend
from desk.collect import add_text_item
from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_telegram_more import CHAT, msg, press


class FakeUI:
    def __init__(self, people=None, locked=False):
        self.tree = people if people is not None else [
            (11, "김철수 1234", "과학과"), (12, "이영희 2345 (2-3, 담임)", "2학년"), (13, "김철수 5555", "행정실")]
        self.locked = locked
        self.open = []          # 열린 쓰기 창
        self.chips = {}
        self.calls = []
        self.sent = []
        self.fail = set()       # 'title'·'body'·'send'·'chips'

    def require(self):
        self.calls.append("require")
        if self.locked:
            raise coolsend.SendError("PC 화면이 잠겨 있어 보낼 수 없습니다.")

    def main_window(self):
        return 1

    def compose_windows(self):
        return list(self.open)

    def people(self, main):
        return list(self.tree)

    def open_compose(self, main, handle, label):
        self.calls.append(("open", handle))
        h = 100 + len(self.calls)
        self.open.append(h)
        self.chips[h] = [] if "chips" in self.fail else [coolsend.label_name(label) + " 선생님"]
        self.draft = {"to": label}
        return h

    def recipients(self, compose):
        return self.chips[compose]

    def set_title(self, compose, title):
        self.draft["title"] = title
        return "title" not in self.fail

    def paste_body(self, compose, body):
        self.draft["body"] = body
        return "body" not in self.fail

    def press_send(self, compose):
        if "send" in self.fail:
            return False
        self.open.remove(compose)
        self.sent.append(self.draft)
        return True


class CoolSendTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(coolsend.label_name("이영희 2345 (2-3, 담임)"), "이영희")
        self.assertEqual(coolsend.clean_name("김철수(과학과)"), "김철수")
        self.assertEqual(coolsend.clean_name("김철수 선생님"), "김철수")
        self.assertEqual(coolsend.reply_title("회의 안내"), "RE: 회의 안내")
        self.assertEqual(coolsend.reply_title("RE: 회의 안내"), "RE: 회의 안내")

    def test_find_people_by_department(self):
        ui = FakeUI()
        found = coolsend.find_people("김철수 선생님", ui=ui)
        self.assertEqual([p["dept"] for p in found], ["과학과", "행정실"])
        self.assertEqual(coolsend.find_people("박없음", ui=ui), [])

    def test_send_checks_and_sends_once(self):
        ui = FakeUI()
        person = coolsend.find_people("이영희", ui=ui)[0]
        self.assertIn("보냈습니다", coolsend.send(person, "RE: 안내", "확인했습니다", ui=ui))
        self.assertEqual(ui.sent, [{"to": "이영희 2345 (2-3, 담임)", "title": "RE: 안내", "body": "확인했습니다"}])

    def test_stops_without_sending(self):
        for fail, left_open in (("chips", True), ("title", True), ("body", True), ("send", True)):
            ui = FakeUI()
            ui.fail.add(fail)
            person = coolsend.find_people("이영희", ui=ui)[0]
            with self.assertRaises(coolsend.SendError) as e:
                coolsend.send(person, "제목", "내용", ui=ui)
            self.assertEqual(e.exception.left_open, left_open, fail)
            self.assertEqual(ui.sent, [], fail)

    def test_refuses_when_locked_or_draft_open_or_tree_changed(self):
        person = {"label": "이영희 2345 (2-3, 담임)", "dept": "2학년", "name": "이영희"}
        with self.assertRaises(coolsend.SendError):
            coolsend.send(person, "t", "b", ui=FakeUI(locked=True))
        ui = FakeUI()
        ui.open.append(99)   # 선생님이 쓰던 창이 있으면 손대지 않음
        with self.assertRaises(coolsend.SendError):
            coolsend.send(person, "t", "b", ui=ui)
        self.assertNotIn(("open", 12), ui.calls)
        with self.assertRaises(coolsend.SendError):
            coolsend.send(dict(person, dept="3학년"), "t", "b", ui=FakeUI())
        with self.assertRaises(coolsend.SendError):
            coolsend.send(person, "t", "  ", ui=FakeUI())


class TelegramCoolSendTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.ui = self.bot.cool_ui = FakeUI()
        self.env.db.set_meta("telegram_chat_id", CHAT)

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def buttons(self):
        return [b for row in self.fake.sent[-1]["reply_markup"]["inline_keyboard"] for b in row]

    def test_new_memo_needs_button(self):
        self.bot.handle_update(msg("쪽지 이영희 내일 회의 3시로 바뀌었어요\n둘째 줄"))
        card = self.fake.sent[-1]["text"]
        self.assertIn("받는 사람: 이영희 (2학년)", card)
        self.assertIn("둘째 줄", card)
        self.assertEqual(self.ui.sent, [])          # 확인 전에는 보내지 않음
        send = self.buttons()[0]["callback_data"]
        self.bot.handle_update(press(send))
        self.assertEqual(self.ui.sent[0]["body"], "내일 회의 3시로 바뀌었어요\n둘째 줄")
        self.assertEqual(self.ui.sent[0]["title"], "내일 회의 3시로 바뀌었어요")
        self.assertIn("보냈습니다", self.fake.sent[-1]["text"])
        self.bot.handle_update(press(send))          # 두 번 눌러도 한 번만
        self.assertEqual(len(self.ui.sent), 1)

    def test_same_name_asks_department_and_cancel(self):
        self.bot.handle_update(msg("/쪽지 김철수 자료 보냈습니다"))
        labels = [b["text"] for b in self.buttons()]
        self.assertEqual(labels, ["📨 과학과", "📨 행정실", "✖ 취소"])
        self.bot.handle_update(press(self.buttons()[-1]["callback_data"]))
        self.assertEqual(self.ui.sent, [])
        self.bot.handle_update(msg("쪽지 김철수 자료 보냈습니다"))
        self.bot.handle_update(press(self.buttons()[1]["callback_data"]))
        self.assertEqual(self.ui.sent[0]["to"], "김철수 5555")

    def test_reply_to_numbered_cool_item(self):
        item = add_text_item(self.env.db, "cool", "회의 자료 확인 부탁드립니다\n\n(보낸 사람: 이영희)", title="협의회 안내")["id"]
        self.env.db.set_meta("telegram_refs", [item])
        self.bot.handle_update(msg("답장 1 확인했습니다"))
        self.assertIn("제목: RE: 협의회 안내", self.fake.sent[-1]["text"])
        self.bot.handle_update(press(self.buttons()[0]["callback_data"]))
        self.assertEqual(self.ui.sent[0]["title"], "RE: 협의회 안내")
        self.bot.handle_update(msg("답장 2 확인"))
        self.assertIn("2번 자료가 없습니다", self.fake.sent[-1]["text"])

    def test_other_text_drops_pending_and_setting_off(self):
        self.bot.handle_update(msg("쪽지 이영희 안녕하세요"))
        send = self.buttons()[0]["callback_data"]
        self.bot.handle_update(msg("오늘"))
        self.bot.handle_update(press(send))
        self.assertEqual(self.ui.sent, [])
        self.env.config.values["telegram_cool_send"] = False
        self.bot.handle_update(msg("쪽지 이영희 안녕하세요"))
        self.assertIn("꺼져 있습니다", self.fake.sent[-1]["text"])

    def test_not_found_and_failure_message(self):
        self.bot.handle_update(msg("쪽지 박없음 안녕하세요"))
        self.assertIn("찾지 못했습니다", self.fake.sent[-1]["text"])
        self.ui.fail.add("send")
        self.bot.handle_update(msg("쪽지 이영희 안녕하세요"))
        self.bot.handle_update(press(self.buttons()[0]["callback_data"]))
        self.assertIn("보내지 못했습니다", self.fake.sent[-1]["text"])
        self.assertIn("열어 두었습니다", self.fake.sent[-1]["text"])


if __name__ == "__main__":
    unittest.main()
