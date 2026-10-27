import json
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from desk import updater
from desk.collect import Collector
from desk.watcher import Watcher, signature
from tests.test_collect import Env
from tests.test_coolmsg import make_udb


def json_url(tmp, data):
    p = Path(tmp) / "latest.json"
    p.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return p.resolve().as_uri()


class UpdaterTests(unittest.TestCase):
    def test_versions(self):
        self.assertGreater(updater.parse_version("0.3.10"), updater.parse_version("0.3.9"))
        self.assertEqual(updater.parse_version("v1.2"), (1, 2))
        self.assertEqual(updater.parse_version(None), (0,))

    def test_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(updater.check("0.3.1", json_url(tmp, {"version": "0.4.0"}))["version"], "0.4.0")
            self.assertIsNone(updater.check("0.4.0", json_url(tmp, {"version": "0.4.0"})))   # 같은 버전
            self.assertIsNone(updater.check("0.5.0", json_url(tmp, {"version": "0.4.0"})))   # 예전 버전
            self.assertIsNone(updater.check("0.1.0", json_url(tmp, "깨진 내용")))
            self.assertIsNone(updater.check("0.1.0", "http://127.0.0.1:1/latest.json", timeout=1))  # 인터넷 안 됨

    def test_script_and_portable(self):
        s = updater.build_script(r"C:\Users\it's\TeacherDesk\teacher-desk", 1234, ["https://a/x.zip", "https://b/y.zip"])
        self.assertIn("Wait-Process -Id 1234", s)
        self.assertIn("'C:\\Users\\it''s\\TeacherDesk\\teacher-desk'", s)     # 작은따옴표 이스케이프
        self.assertIn("@('https://a/x.zip', 'https://b/y.zip')", s)
        self.assertIn("/XD data /XF config.json", s)                          # 자료·설정은 그대로
        self.assertIn("--no-update-check", s)                                  # 다시 켤 때 또 확인하지 않음
        self.assertNotIn("Start-Process", updater.build_script("C:\\x", 1, ["u"], relaunch=False))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(updater.is_portable(tmp))
            (Path(tmp) / "python").mkdir()
            (Path(tmp) / "python" / "python.exe").write_bytes(b"")
            self.assertTrue(updater.is_portable(tmp))

    def test_desk_main_exits_for_update(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("desk_main", Path(__file__).resolve().parents[1] / "desk.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            cfg.write_text(json.dumps({"data_dir": str(Path(tmp) / "data"), "port": 1}), encoding="utf-8")
            with patch.dict(os.environ, {"DESK_UPDATE_JSON": json_url(tmp, {"version": "99.0.0"})}), \
                    patch.object(mod.updater, "start_update") as start:
                self.assertEqual(mod.main(["--config", str(cfg), "--no-browser"]), updater.EXIT_UPDATING)
                start.assert_called_once()
            cfg.write_text(json.dumps({"data_dir": str(Path(tmp) / "data"), "port": 1, "auto_update": False}), encoding="utf-8")
            with patch.dict(os.environ, {"DESK_UPDATE_JSON": json_url(tmp, {"version": "99.0.0"})}), \
                    patch.object(mod.updater, "start_update") as start, patch.object(mod, "serve", side_effect=OSError):
                self.assertEqual(mod.main(["--config", str(cfg), "--no-browser"]), 1)   # 업데이트 없이 켜려다 포트 오류
                start.assert_not_called()


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.local = Path(self.env.tmp.name) / "local"
        self.memo = self.local / "CoolMessenger" / "Memo"
        self.memo.mkdir(parents=True)
        self.udb = self.memo / "홍길동.udb"
        make_udb(self.udb, "a", [])
        self.env.config.values.update(coolmessenger_name="홍길동", coolmessenger_folder="")
        self.p = patch.dict(os.environ, {"LOCALAPPDATA": str(self.local)})
        self.p.start()
        self.w = Watcher(self.env.db, self.env.config, Collector(self.env.db, self.env.config), interval=1)

    def tearDown(self):
        self.p.stop()
        self.env.close()

    def add_memo(self, title):
        conn = sqlite3.connect(self.udb)
        conn.execute("INSERT INTO tbl_memo(sender_name, title, content, reg_date) VALUES ('교무부', ?, '11월 3일까지 제출', ?)",
                     (title, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
        t = time.time() + 2
        os.utime(self.udb, (t, t))  # 파일 시각이 바뀐 것이 분명하게

    def test_collects_after_change_is_stable(self):
        self.assertIsNone(self.w.tick())               # 켠 직후: 기준만 잡음
        self.assertIsNone(self.w.tick())               # 바뀐 것 없음
        self.add_memo("성적관리위원회")
        self.assertIsNone(self.w.tick())               # 처음 봄 → 저장이 끝나길 한 번 기다림
        stats = self.w.tick()                          # 그대로면 수집
        self.assertEqual(stats["items"], 1)
        self.assertIsNone(self.w.tick())
        # 에듀파인 폴더에 새 공문 저장
        self.env.config.values["edufine_folder"] = str(self.env.edu)
        self.w.last = None
        self.w.tick()
        (self.env.edu / "연수 안내.txt").write_text("신청: 11월 2일까지", encoding="utf-8")
        (self.env.edu / "그림.png").write_bytes(b"x")  # 읽지 않는 형식은 감시하지 않음
        self.assertIsNone(self.w.tick())
        self.assertEqual(self.w.tick()["items"], 1)

    def test_disabled_and_quiet_logs(self):
        self.env.config.values["watch_enabled"] = False
        self.add_memo("a")
        self.assertIsNone(self.w.tick())
        self.assertIsNone(self.w.tick())
        self.env.config.values["watch_enabled"] = True
        before = self.env.db.one("SELECT COUNT(*) AS n FROM logs")["n"]
        self.env.db.execute("DELETE FROM files_seen")
        self.w.collector.run("watch")                  # 새 자료 있음 → 기록
        self.w.collector.run("watch")                  # 새 자료 없음 → 기록하지 않음
        self.assertEqual(self.env.db.one("SELECT COUNT(*) AS n FROM logs")["n"], before + 1)

    def test_signature_is_cheap(self):
        folder = Path(self.env.tmp.name) / "big"
        folder.mkdir()
        for i in range(3000):
            (folder / f"문서{i}.hwp").write_bytes(b"x")
        self.env.config.values["edufine_folder"] = str(folder)
        t = time.perf_counter()
        sig = signature(self.env.config)
        took = time.perf_counter() - t
        self.assertEqual(len(sig), 3000 + 2)
        self.assertLess(took, 2.0, f"파일 3000개 살펴보는 데 {took:.2f}초")


if __name__ == "__main__":
    unittest.main()
