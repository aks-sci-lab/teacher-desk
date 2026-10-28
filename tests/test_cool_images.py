import base64
import os
import unittest
import zlib
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from desk import coolmsg
from desk.collect import Collector, add_text_item
from tests import test_server  # ServerTests 를 이름으로 가져오면 그 시험이 여기서 또 돔
from tests.test_collect import Env
from tests.test_coolmsg import make_udb

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def comp(html):
    return "{COMP}" + base64.b64encode(zlib.compress(html.encode("utf-16-le"))).decode()


def recent():
    return (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")


class ImageParseTests(unittest.TestCase):
    def test_html_rtf_and_comp(self):
        html = ('<div>사진 보냅니다<img src="https://sticker.coolmessenger.com/s.png">'
                "<IMG SRC='file:///C:/Users/t/AppData/Local/CoolMessenger/Image/a.jpg'> <img src=b.gif>"
                '<img src="https://sticker.coolmessenger.com/s.png"></div>')
        self.assertEqual(coolmsg.body_images(comp(html)), [
            "https://sticker.coolmessenger.com/s.png", "file:///C:/Users/t/AppData/Local/CoolMessenger/Image/a.jpg", "b.gif"])
        self.assertEqual(coolmsg.clean_body(comp(html)), "사진 보냅니다")
        rtf = r"{\rtf1 그림 {\pict\pngblip\picw1\pich1 " + PNG.hex() + "}}"
        self.assertEqual(coolmsg.body_images(rtf), ["data:image/png;base64," + base64.b64encode(PNG).decode()])
        self.assertEqual(coolmsg.body_images("그냥 글"), [])
        self.assertEqual([coolmsg.image_kind(s) for s in ("https://x/a.png", "data:image/png;base64,AA", "C:\\a.jpg",
                                                          "file:///C:/a.jpg", "a.jpg", "cid:1")],
                         ["인터넷", "쪽지 안", "PC 파일", "PC 파일", "이름만", "첨부(cid)"])


class CollectImageTests(unittest.TestCase):
    def test_saved_and_backfilled(self):
        env = Env()
        local = Path(env.tmp.name) / "local"
        memo = local / "CoolMessenger" / "Memo"
        memo.mkdir(parents=True)
        body = comp('<div>체육대회 사진<img src="https://sticker.coolmessenger.com/s.png"></div>')
        make_udb(memo / "홍길동.udb", "a", [("교무부", "사진", body, recent()), ("교무부", "옛 쪽지", comp("<img src=old.png>회의"), recent())])
        env.config.values.update(coolmessenger_name="홍길동", coolmessenger_folder="", edufine_folder="")
        try:
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                path = coolmsg.resolve("홍길동")
                # 0.5.2 전에 받은 쪽지 (사진 칸 비어 있음)
                add_text_item(env.db, "cool", "회의\n\n(보낸 사람: 교무부)", title="옛 쪽지", received_at=recent(),
                              file_path=f"{path}#2")
                env.db.set_meta(f"cool_last_rowid:{path}", 0)
                env.db.execute("UPDATE items SET images = NULL")
                Collector(env.db, env.config).run("manual")
                got = {r["title"]: r["images"] for r in env.db.query("SELECT title, images FROM items")}
                self.assertEqual(got, {"옛 쪽지": '["old.png"]', "사진": '["https://sticker.coolmessenger.com/s.png"]'})
        finally:
            env.close()


class ImageApiTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def test_serves_only_allowed_images(self):
        root = Path(self.env.tmp.name) / "local"
        cool_dir = root / "CoolMessenger"
        (cool_dir / "Memo").mkdir(parents=True)
        (cool_dir / "Image").mkdir()
        (cool_dir / "Image" / "a.png").write_bytes(PNG)
        (cool_dir / "Memo" / "b.png").write_bytes(PNG)
        outside = Path(self.env.tmp.name) / "secret.png"
        outside.write_bytes(PNG)
        (cool_dir / "Image" / "note.txt").write_text("글")
        srcs = ["data:image/png;base64," + base64.b64encode(PNG).decode(),
                (cool_dir / "Image" / "a.png").as_uri(), "b.png", str(outside), "../secret.png",
                str(cool_dir / "Image" / "note.txt"), "https://sticker.coolmessenger.com/s.png"]
        item = add_text_item(self.env.db, "cool", "사진 쪽지", received_at=recent(), images=srcs)["id"]
        with patch.dict(os.environ, {"LOCALAPPDATA": str(root)}):
            self.env.config.values.update(coolmessenger_name="홍길동", coolmessenger_path="")
            try:
                st, it = self.req("GET", f"/api/items/{item}")
                self.assertEqual([i["url"] for i in it["images"]], [None] * 6 + ["https://sticker.coolmessenger.com/s.png"])
                for n, want in [(0, 200), (1, 200), (2, 200), (3, 404), (4, 404), (5, 404), (9, 404)]:
                    st, data = self.req("GET", f"/api/items/{item}/images/{n}")
                    self.assertEqual(st, want, (n, data))
                    if want == 200:
                        self.assertEqual(data, PNG)
                self.assertEqual(self.req("GET", f"/api/items/{item}/images/0", token=False)[0], 401)
            finally:
                self.env.config.values.update(coolmessenger_name="")


if __name__ == "__main__":
    unittest.main()
