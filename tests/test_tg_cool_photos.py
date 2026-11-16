import base64
import unittest
from unittest.mock import Mock, patch

from desk import server
from desk.collect import add_text_item
from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_telegram_more import CHAT

PNG = base64.b64encode(b"\x89PNG fake").decode()
DATA = f"data:image/png;base64,{PNG}"


class TelegramCoolPhotoTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.env.db.set_meta("telegram_chat_id", CHAT)
        self.env.db.replace_students([{"number": 7, "name": "최별빛"}])
        self.read = Mock(side_effect=lambda src: (b"\x89PNG bytes", "image/png") if src.startswith("data:") else None)
        self.bot.read_image = self.read

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def item(self, title, images, source="cool", n=[0]):
        n[0] += 1
        it = add_text_item(self.env.db, source, f"본문 {title} {n[0]}", title=title, images=images)
        return it["id"] if isinstance(it, dict) else it

    def run_notify(self, *ids, reason="scheduled"):
        self.bot.notify_collect({"items": len(ids), "new_ids": list(ids), "deadlines": 0}, reason)

    def test_new_cool_photos_are_sent_with_title_caption(self):
        a = self.item("최별빛 학생 체험학습 안내", [DATA, DATA])
        self.item("사진 없는 쪽지", [])
        self.run_notify(a)
        self.assertEqual(self.fake.calls.count("sendPhoto"), 2)
        raw = b"".join(self.fake.docs)
        self.assertIn("📷".encode(), raw)
        self.assertIn("(1/2)".encode(), raw)
        self.assertIn("최별빛".encode(), raw)          # 0.5.93: 제목 그대로 (설정을 켜면 가림)

    def test_cool_text_is_off_by_default_and_when_on_sends_body_in_chunks(self):
        """0.5.61: 쿨메신저 쪽지 원문은 선생님이 설정에서 켠 경우에만, 원문 그대로 (제목 머리줄은 이름 가림)."""
        self.assertFalse(self.env.config["telegram_cool_text"])
        body = "가" * 4000 + "나" * 10                     # 텔레그램 한 메시지 한도를 넘어 나눠 보내야 함
        it = add_text_item(self.env.db, "cool", body, title="최별빛 체험학습 안내")
        a = it["id"] if isinstance(it, dict) else it
        self.run_notify(a)
        self.assertFalse(any("나" * 10 in m.get("text", "") for m in self.fake.sent))   # 기본은 원문을 보내지 않음
        self.env.config.values["telegram_cool_text"] = True
        self.run_notify(a)
        texts = [m.get("text", "") for m in self.fake.sent]
        joined = "".join(texts)
        self.assertIn("나" * 10, joined)                       # 원문 끝까지 빠짐없이
        self.assertGreaterEqual(len([t for t in texts if "가" in t]), 2)   # 긴 글은 여러 번에 나눠 보냄
        self.assertIn("최별빛", joined)                       # 0.5.93: 머리줄 이름 그대로
        self.env.config.values["telegram_cool_text"] = False

    def test_nothing_sent_when_off_unpaired_not_cool_or_old(self):
        a = self.item("안내", [DATA])
        self.env.config.values["telegram_photos"] = False
        self.run_notify(a)
        self.env.config.values["telegram_photos"] = True
        self.bot.read_image = None
        self.run_notify(a)
        self.bot.read_image = self.read
        self.run_notify(self.item("공문 사진", [DATA], source="edufine"))
        self.run_notify()                                 # 새 자료 없음
        self.env.db.set_meta("telegram_chat_id", None)
        self.run_notify(a)
        self.assertEqual(self.fake.calls.count("sendPhoto"), 0)

    def test_internet_photos_are_sent_and_limit_and_failures_are_reported(self):
        """0.5.101 선생님 요청: 인터넷 주소 사진도 이 PC 가 내려받아 함께 보냄. 못 읽은 사진은 몇 장인지 알림."""
        self.read.side_effect = lambda src: (b"\xff\xd8 jpeg", "image/jpeg") if "ok" in src else None
        web = self.item("인터넷 사진", ["https://example.com/ok.jpg", "https://example.com/gone.jpg"])
        self.run_notify(web)
        self.assertEqual(self.fake.calls.count("sendPhoto"), 1)
        self.assertIn("1장은 읽지 못해", "\n".join(m["text"] for m in self.fake.sent))
        self.read.side_effect = lambda src: (b"\x89PNG bytes", "image/png")
        many = self.item("사진 많음", [DATA + f"{i}" for i in range(10)])
        self.fake.calls.clear()
        self.run_notify(many, self.item("또 사진", [DATA, DATA + "x"]))
        self.assertEqual(self.fake.calls.count("sendPhoto"), 10)
        self.assertIn("사진 2장은 한 번에 10장까지", "\n".join(m["text"] for m in self.fake.sent))

    def test_photo_rejected_by_telegram_is_resent_as_file(self):
        self.fake.fail_photo = True
        self.run_notify(self.item("길쭉한 그림", [DATA]))
        self.assertEqual(self.fake.calls.count("sendDocument"), 1)

    def test_gif_goes_as_file_and_refresh_command_also_sends_photos(self):
        self.read.side_effect = lambda src: (b"GIF89a", "image/gif")
        self.run_notify(self.item("움직이는 그림", [DATA]))
        self.assertEqual(self.fake.calls.count("sendDocument"), 1)
        self.assertEqual(self.fake.calls.count("sendPhoto"), 0)
        self.fake.calls.clear()
        self.run_notify(self.item("새로고침으로 받음", [DATA]), reason="telegram")
        self.assertEqual(len(self.fake.calls), 1)

    def test_server_reader_reads_inline_and_internet_but_never_this_pc(self):
        self.assertEqual(server.read_item_image(None, DATA)[1], "image/png")
        self.assertIsNone(server.read_item_image(None, "data:image/png;base64,###"))
        with patch.object(server, "_open_web_image", return_value=(b"img", "image/jpeg")) as opened, \
                patch.object(server, "_is_loopback", side_effect=lambda h: h in ("localhost", "127.0.0.1")):
            self.assertEqual(server.read_item_image(None, "https://school.example/a.jpg"), (b"img", "image/jpeg"))
            self.assertIsNone(server.read_item_image(None, "http://127.0.0.1:8765/api/x.png"))
            self.assertIsNone(server.read_item_image(None, "http://localhost/a.png"))
            self.assertEqual(opened.call_count, 1)
        with patch.object(server, "_open_web_image", side_effect=OSError("끊김")), \
                patch.object(server, "_is_loopback", return_value=False):
            self.assertIsNone(server.read_item_image(None, "https://school.example/a.jpg"))

    def test_web_image_download_checks_type_and_size(self):
        """실제 내려받기: 그림만, 크기 한도. (시험용으로 이 PC 의 작은 서버를 씀)"""
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body, ctype = {"/a.png": (b"\x89PNG", "image/png"), "/page": (b"<html>", "text/html"),
                               "/big.jpg": (b"x" * (server.WEB_IMAGE_MAX + 10), "image/jpeg"),
                               "/noname.jpg": (b"\xff\xd8", "application/octet-stream")}[self.path]
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            self.assertEqual(server._open_web_image(base + "/a.png"), (b"\x89PNG", "image/png"))
            self.assertIsNone(server._open_web_image(base + "/page"))
            self.assertIsNone(server._open_web_image(base + "/big.jpg"))
            self.assertEqual(server._open_web_image(base + "/noname.jpg"), (b"\xff\xd8", "image/jpeg"))
            self.assertIsNone(server.fetch_web_image(base + "/a.png"))   # 이 PC 자신은 읽지 않음
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
