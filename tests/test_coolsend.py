"""쿨메신저 쪽지 보내기 (0.6.5): 창 조작은 가짜(FakeUI)로 바꿔 순서·안전 확인만 시험.
실제 쿨메신저 창에서 되는지는 학교 PC 에서만 알 수 있음 (조직도 두 번 클릭·[보내기] 마우스 클릭 시험은 2026-11-17 확인)."""

import tempfile
import unittest
from pathlib import Path

from desk import coolsend
from desk.collect import add_text_item
from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_telegram_more import CHAT, msg, press


class FakeUI:
    def __init__(self, people=None, locked=False):
        self.tree = people if people is not None else [
            (11, "(김철수)1234", "과학과"), (12, "(이영희)2345(2-3, 담임)", "2학년"), (13, "(김철수)5555", "행정실")]
        self.locked = locked
        self.open = []          # 열린 쓰기 창
        self.chips = {}
        self.calls = []
        self.sent = []
        self.fail = set()       # 'title'·'body'·'send'·'chips'

    def require(self, allow_locked=False):
        self.calls.append("require")
        if self.locked and not allow_locked:
            raise coolsend.Locked("PC 화면이 잠겨 있어 보낼 수 없습니다.")

    def is_locked(self):
        return self.locked

    def ready(self):
        return not self.locked and not getattr(self, "busy_hands", False)

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

    def close_compose(self, compose):
        self.calls.append(("close", compose))
        if "close" in self.fail:
            return False
        self.open.remove(compose)
        return True

    def press_send(self, compose):
        if "send" in self.fail:
            return False
        self.open.remove(compose)
        self.sent.append(self.draft)
        return True


class FakeAttachUI(FakeUI):
    def __init__(self, folder, rows):
        super().__init__()
        self.folder, self.list, self.picked = folder, rows, []

    def download_dir(self):
        return self.folder

    def manager_window(self, main):
        return 2

    def show_inbox(self, manager):
        self.calls.append("inbox")

    def rows(self, manager):
        return [list(r) for r in self.list]

    def select_row(self, manager, i):
        self.picked.append(i)
        return True

    def save_all(self, manager):
        (Path(self.folder) / "안내문.pdf").write_bytes(b"%PDF-1.4 test")
        return True


class CoolSendTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(coolsend.label_name("이영희 2345 (2-3, 담임)"), "이영희")
        self.assertEqual(coolsend.label_name("(이영희)8605(교무기획2)"), "이영희")   # 학교 PC 조직도 모양
        self.assertEqual(coolsend.label_name("(이영희)"), "이영희")
        self.assertEqual(coolsend.clean_name("(이영희)8605"), "이영희")
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
        self.assertEqual(ui.sent, [{"to": "(이영희)2345(2-3, 담임)", "title": "RE: 안내", "body": "확인했습니다"}])

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
        person = {"label": "(이영희)2345(2-3, 담임)", "dept": "2학년", "name": "이영희"}
        with self.assertRaises(coolsend.SendError):
            coolsend.send(person, "t", "b", ui=FakeUI(locked=True))
        ui = FakeUI()
        ui.open.append(99)   # 열려 있던 쪽지 창은 닫고 보냄 (선생님 결정)
        coolsend.send(person, "t", "b", ui=ui)
        self.assertEqual(ui.calls[1:3], [("close", 99), ("open", 12)])
        self.assertEqual(len(ui.sent), 1)
        ui = FakeUI()
        ui.open.append(99)
        ui.fail.add("close")   # 닫지 못하면 시작하지 않음
        with self.assertRaises(coolsend.SendError):
            coolsend.send(person, "t", "b", ui=ui)
        self.assertNotIn(("open", 12), ui.calls)
        with self.assertRaises(coolsend.SendError):
            coolsend.send(dict(person, dept="3학년"), "t", "b", ui=FakeUI())
        with self.assertRaises(coolsend.SendError):
            coolsend.send(person, "t", "  ", ui=FakeUI())


class CoolAttachTests(unittest.TestCase):
    ROWS = [["(박민수)", "다른 쪽지", "2026/11/17 09:00:00 (화)", ""],
            ["(이영희)", "협의회 안내 자료입니다", "2026/11/17 13:45:10 (화)", "안내문.pdf"]]

    def setUp(self):
        coolsend.SETTLE = 0.5
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_match_rows(self):
        self.assertEqual(coolsend.match_rows(self.ROWS, "협의회 안내 자료입니다 첨부 확인", "2026-11-17 13:45:00", "이영희"), [1])
        self.assertEqual(coolsend.match_rows(self.ROWS, "협의회 안내 자료입니다", "2026-11-17 13:50:00"), [])   # 시각이 다름
        self.assertEqual(coolsend.match_rows(self.ROWS, "협의회 안내 자료입니다", "2026-11-17 13:45:00", "김철수"), [])

    def test_fetch_saves_and_returns_new_files(self):
        ui = FakeAttachUI(self.tmp.name, self.ROWS)
        (Path(self.tmp.name) / "예전.hwp").write_bytes(b"old")
        files = coolsend.fetch_attachments("협의회 안내 자료입니다", "2026-11-17 13:45:00", "이영희", ui=ui)
        self.assertEqual([p.name for p in files], ["안내문.pdf"])
        self.assertEqual(ui.picked, [1])
        ui.save_all = lambda m: (Path(self.tmp.name) / "안내문(1).pdf").write_bytes(b"%PDF again") or True
        files = coolsend.fetch_attachments("협의회 안내 자료입니다", "2026-11-17 13:45:00", "이영희", ui=ui)
        self.assertEqual([p.name for p in files], ["안내문(1).pdf"])   # 같은 이름이 있어도 새로 받음 (번호 붙은 새 파일)
        self.assertEqual(ui.picked, [1, 1])

    def test_no_attachment_or_not_found(self):
        ui = FakeAttachUI(self.tmp.name, self.ROWS)
        with self.assertRaises(coolsend.SendError) as e:
            coolsend.fetch_attachments("다른 쪽지", "2026-11-17 09:00:00", ui=ui)
        self.assertIn("첨부 파일이 없습니다", str(e.exception))
        with self.assertRaises(coolsend.SendError):
            coolsend.fetch_attachments("없는 쪽지", "2026-11-17 09:00:00", ui=ui)
        self.assertEqual(ui.picked, [])


class TelegramBase(unittest.TestCase):
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


class TelegramCoolSendTests(TelegramBase):

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
        self.assertEqual(self.ui.sent[0]["to"], "(김철수)5555")

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

    def test_attach_command_sends_files(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        coolsend.SETTLE = 0.5
        self.bot.cool_attach_ui = FakeAttachUI(tmp.name, CoolAttachTests.ROWS)
        item = add_text_item(self.env.db, "cool", "자료 첨부합니다\n\n(보낸 사람: 이영희)", title="협의회 안내 자료입니다",
                             received_at="2026-11-17 13:45:00")["id"]
        self.env.db.set_meta("telegram_refs", [item])
        self.bot.handle_update(msg("첨부 1"))
        self.assertIn("첨부 1개를 보냈습니다", self.fake.sent[-1]["text"])
        self.assertEqual(self.bot.cool_attach_ui.picked, [1])
        self.bot.handle_update(msg("파일 1"))   # '파일 n' 은 그대로, 첨부 안내만
        self.assertIn("'첨부 1'", self.fake.sent[-1]["text"])

    def test_not_found_and_failure_message(self):
        self.bot.handle_update(msg("쪽지 박없음 안녕하세요"))
        self.assertIn("찾지 못했습니다", self.fake.sent[-1]["text"])
        self.ui.fail.add("send")
        self.bot.handle_update(msg("쪽지 이영희 안녕하세요"))
        self.bot.handle_update(press(self.buttons()[0]["callback_data"]))
        self.assertIn("보내지 못했습니다", self.fake.sent[-1]["text"])
        self.assertIn("열어 두었습니다", self.fake.sent[-1]["text"])


class CoolQueueTests(TelegramBase):
    """화면 잠금 중 [보내기]: 대기해 두었다가 잠금이 풀리면 보냄 (0.6.25)."""

    def queue_one(self, text="쪽지 이영희 내일 회의 3시"):
        self.ui.locked = True
        self.bot.handle_update(msg(text))
        self.assertIn("🔒 지금 PC 화면이 잠겨 있습니다", self.fake.sent[-1]["text"])   # 잠금 중에도 받는 사람은 찾음
        self.bot.handle_update(press(self.buttons()[0]["callback_data"]))
        self.assertIn("대기해 두었습니다", self.fake.sent[-1]["text"])
        return self.buttons()[0]["callback_data"]

    def test_locked_send_waits_then_sends_when_unlocked_and_idle(self):
        self.queue_one()
        self.assertEqual(self.ui.sent, [])
        self.assertEqual(self.bot.flush_cool_queue(), 1)   # 아직 잠김: 그대로
        self.ui.locked = False
        self.ui.busy_hands = True                          # 잠금을 풀고 바로 쓰는 중: 창을 뺏지 않음
        self.assertEqual(self.bot.flush_cool_queue(), 1)
        self.assertEqual(self.ui.sent, [])
        self.ui.busy_hands = False
        self.assertEqual(self.bot.flush_cool_queue(), 0)
        self.assertEqual(self.ui.sent[0]["body"], "내일 회의 3시")
        self.assertIn("대기해 둔 쪽지를 이영희 님께 보냈습니다", self.fake.sent[-1]["text"])
        self.assertEqual(self.bot.flush_cool_queue(), 0)   # 한 번만
        self.assertEqual(len(self.ui.sent), 1)

    def test_relocked_between_check_and_send_keeps_waiting(self):
        self.queue_one()
        self.ui.locked = False
        orig = self.ui.ready
        self.ui.ready = lambda: (orig(), setattr(self.ui, "locked", True))[0]   # 보내려는 순간 다시 잠김
        self.assertEqual(self.bot.flush_cool_queue(), 1)
        self.assertEqual(self.ui.sent, [])
        self.ui.ready = orig
        self.ui.locked = False
        self.assertEqual(self.bot.flush_cool_queue(), 0)
        self.assertEqual(len(self.ui.sent), 1)

    def test_cancel_and_failure_and_limit(self):
        cancel = self.queue_one()
        self.bot.handle_update(press(cancel))
        self.assertIn("취소했습니다", self.fake.edited[-1]["text"])
        self.ui.locked = False
        self.assertEqual(self.bot.flush_cool_queue(), 0)
        self.assertEqual(self.ui.sent, [])
        self.queue_one()
        self.ui.fail.add("send")
        self.ui.locked = False
        self.assertEqual(self.bot.flush_cool_queue(), 0)   # 실패하면 알리고 대기에서 뺌 (다시 누르지 않음)
        self.assertIn("보내지 못했습니다", self.fake.sent[-1]["text"])
        self.ui.fail.clear()
        for _ in range(5):
            self.queue_one()
        self.ui.locked = True
        self.bot.handle_update(msg("쪽지 이영희 여섯째"))
        self.bot.handle_update(press(self.buttons()[0]["callback_data"]))
        self.assertIn("더 받지 않았습니다", self.fake.sent[-1]["text"])
        self.assertEqual(len(self.bot.cool_queue), 5)

    def test_background_loop_sends_and_ends(self):
        import time
        from desk import telegram
        old = telegram.COOL_QUEUE_EVERY
        telegram.COOL_QUEUE_EVERY = 0.05
        self.addCleanup(setattr, telegram, "COOL_QUEUE_EVERY", old)
        self.queue_one()
        self.bot.sync = False
        self.bot.start_cool_queue()
        self.ui.locked = False
        end = time.monotonic() + 5
        while (self.ui.sent == [] or self.bot.cool_queue_thread) and time.monotonic() < end:
            time.sleep(0.05)
        self.assertEqual(len(self.ui.sent), 1)
        self.assertIsNone(self.bot.cool_queue_thread)   # 대기가 비면 스레드도 끝

    def test_old_day_is_dropped(self):
        self.queue_one()
        self.bot.cool_queue[0]["day"] = "2000-01-01"
        self.ui.locked = False
        self.assertEqual(self.bot.flush_cool_queue(), 0)
        self.assertEqual(self.ui.sent, [])
        self.assertIn("버렸습니다", self.fake.sent[-1]["text"])


class KeepAwakeTests(unittest.TestCase):
    def test_follows_setting(self):
        from desk import power
        calls = []
        orig = power.nudge
        power.nudge = lambda: calls.append(1) or True
        try:
            cfg = {"keep_awake": True}
            k = power.KeepAwake(cfg)
            self.assertTrue(k.tick())
            cfg["keep_awake"] = False
            self.assertFalse(k.tick())
            self.assertEqual(len(calls), 1)
        finally:
            power.nudge = orig


if __name__ == "__main__":
    unittest.main()
