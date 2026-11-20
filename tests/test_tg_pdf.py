"""텔레그램 [📄 PDF] (0.6.17, 선생님 요청 2026-11-20 "첨부파일에서 추출한 글자만 나와서 보기 힘들어 … pdf로 볼 수 있게").
실제 한글·엑셀 변환(preview.pdf_bytes)은 학교 PC 에서만 되므로 가짜 to_pdf 로 흐름만 시험."""

import tempfile
import unittest
import urllib.parse
from pathlib import Path

from desk import coolsend, preview
from desk.collect import add_text_item
from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests import test_coolsend
from tests.test_telegram_more import CHAT, msg, press


class HwpAttachUI(test_coolsend.FakeAttachUI):
    def save_all(self, manager):
        (Path(self.folder) / "안내문.hwp").write_bytes(b"HWP")
        (Path(self.folder) / "사진.jpg").write_bytes(b"\xff\xd8jpg")
        return True


class TelegramPdfTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.env.db.set_meta("telegram_chat_id", CHAT)
        self.converted = []

        def to_pdf(p):
            self.converted.append(Path(p).suffix)
            return b"%PDF-1.7 fake"
        self.bot.to_pdf = to_pdf

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def file_item(self, name, data=b"x"):
        p = self.env.edu / name
        p.write_bytes(data)
        iid = add_text_item(self.env.db, "edufine", "뽑은 글자 " + name, title="공문 " + name, file_path=str(p))["id"]
        self.env.db.set_meta("telegram_refs", [iid])
        return iid

    def test_button_and_command_convert_hwp(self):
        iid = self.file_item("계획.hwp")
        rows = self.bot.item_buttons([iid], [iid])
        self.assertIn({"text": "📄 1 PDF", "callback_data": f"pdf:{iid}"}, rows[0])
        self.bot.handle_update(press(f"pdf:{iid}"))
        self.assertEqual(self.converted, [".hwp"])
        self.assertIn(urllib.parse.quote("계획.pdf"), self.fake.docs[-1].decode("utf-8", "replace"))
        self.bot.handle_update(msg("PDF 1"))
        self.assertEqual(len(self.converted), 2)

    def test_original_text_has_pdf_button(self):
        iid = self.file_item("계획.hwpx")
        self.bot.handle_update(msg("원문 1"))
        last = self.fake.sent[-1]
        self.assertIn(f"pdf:{iid}", str(last.get("reply_markup")))

    def test_pdf_sent_as_is_and_unsupported(self):
        iid = self.file_item("안내.pdf", b"%PDF-1.4 x")
        self.bot.handle_update(press(f"pdf:{iid}"))
        self.assertEqual(self.converted, [])
        self.assertIn(b"%PDF-1.4 x", self.fake.docs[-1])
        iid = self.file_item("안내.docx")
        self.assertEqual(self.bot.item_buttons([iid], [iid])[0][-1]["text"], "1 원본 파일 받기")
        self.bot.handle_update(msg("PDF 1"))
        self.assertIn("PDF 로 바꿀 수 없습니다", self.fake.sent[-1]["text"])

    def test_conversion_failure_message(self):
        def fail(p):
            raise preview.PreviewError("한글이 없습니다")
        self.bot.to_pdf = fail
        iid = self.file_item("계획.hwp")
        self.bot.handle_update(press(f"pdf:{iid}"))
        self.assertIn("PDF 로 바꾸지 못했습니다: 한글이 없습니다", self.fake.sent[-1]["text"])

    def test_cool_memo_attachments_to_pdf(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        coolsend.SETTLE = 0.5
        self.env.config.values.update(telegram_cool_send=True)
        self.bot.cool_attach_ui = HwpAttachUI(tmp.name, test_coolsend.CoolAttachTests.ROWS)
        iid = add_text_item(self.env.db, "cool", "자료 첨부합니다\n\n(보낸 사람: 이영희)", title="협의회 안내 자료입니다",
                            received_at="2026-11-17 13:45:00")["id"]
        self.env.db.set_meta("telegram_refs", [iid])
        self.bot.handle_update(msg("PDF 1"))
        self.assertEqual(self.converted, [".hwp"])   # 그림은 그대로
        names = b"".join(self.fake.docs).decode("utf-8", "replace")
        self.assertIn(urllib.parse.quote("안내문.pdf"), names)
        self.assertIn("첨부 2개를 보냈습니다", self.fake.sent[-1]["text"])


class CoolAttachedPhotoTests(unittest.TestCase):
    """그림 파일이 첨부된 새 쪽지 → 쿨메신저로 받아 그림만 자동으로 (0.6.17, 선생님 "여전히 그림이 전송이 안돼")."""

    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.tmp = tempfile.TemporaryDirectory()
        coolsend.SETTLE = 0.5
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base, telegram_cool_send=True)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.bot.cool_attach_ui = HwpAttachUI(self.tmp.name, test_coolsend.CoolAttachTests.ROWS)
        self.env.db.set_meta("telegram_chat_id", CHAT)

    def tearDown(self):
        self.fake.close()
        self.tmp.cleanup()
        self.env.close()

    def memo(self, files):
        line = f"\n\n(첨부 파일: {files})" if files else ""
        return add_text_item(self.env.db, "cool", f"자료 첨부합니다{line}\n\n(보낸 사람: 이영희(이영희))",
                             title="협의회 안내 자료입니다", received_at="2026-11-17 13:45:00")["id"]

    def test_fetches_and_sends_only_pictures(self):
        iid = self.memo("안내문.hwp, 사진.jpg")
        self.assertEqual(self.bot.send_cool_attached_photos({"new_ids": [iid]}), 1)
        self.assertEqual(self.fake.calls.count("sendPhoto"), 1)
        self.assertEqual(self.fake.calls.count("sendDocument"), 0)   # hwp 는 달라고 할 때만
        self.assertIn("다른 첨부 1개", self.fake.sent[-1]["text"])
        self.assertIn(f"pdf:{iid}", str(self.fake.sent[-1].get("reply_markup")))

    def test_skips_without_picture_or_when_off(self):
        self.assertEqual(self.bot.cool_photo_targets({"new_ids": [self.memo("안내문.hwp")]}), [])
        iid = self.memo("사진.jpg")
        self.env.config.values.update(telegram_cool_photo_fetch=False)
        self.assertEqual(self.bot.cool_photo_targets({"new_ids": [iid]}), [])
        self.env.config.values.update(telegram_cool_photo_fetch=True, telegram_cool_send=False)
        self.assertEqual(self.bot.cool_photo_targets({"new_ids": [iid]}), [])

    def test_failure_tells_reason_with_button(self):
        self.bot.cool_attach_ui.list = [["(박민수)", "다른 쪽지", "2026/11/17 09:00:00 (화)", ""]]
        iid = self.memo("사진.jpg")
        self.bot.send_cool_attached_photos({"new_ids": [iid]})
        self.assertIn("그림 첨부를 받지 못했습니다", self.fake.sent[-1]["text"])
        self.assertIn(f"ca:{iid}", str(self.fake.sent[-1].get("reply_markup")))


if __name__ == "__main__":
    unittest.main()
