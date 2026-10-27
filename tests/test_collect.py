import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from desk.collect import Collector, diagnose
from desk.config import Config
from desk.db import DB
from desk.scheduler import Scheduler, due_slots, next_slot
from tests.hwp_fixture import build_hwp


class Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.edu, self.cool = root / "edu", root / "cool"
        self.edu.mkdir()
        self.cool.mkdir()
        self.config = Config(root / "config.json")
        self.config.values.update(edufine_folder=str(self.edu), coolmessenger_folder=str(self.cool), data_dir=str(root / "data"))
        self.db = DB(self.config.data_dir / "desk.db")
        self.notified = []
        self.collector = Collector(self.db, self.config, notify=lambda s, r: self.notified.append((s, r)))

    def close(self):
        self.db.conn.close()
        self.tmp.cleanup()


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def test_collects_new_files_once(self):
        e = self.env
        (e.edu / "학업성적관리위원회 개최 안내.hwp").write_bytes(
            build_hwp(["관련: 학교혁신과-12345", "협의록을 10월 30일(금)까지 제출"]))
        (e.cool / "msg.txt").write_text("학교혁신과-12345 공문 확인 부탁드립니다.\n내일 오후 4시 협의회", encoding="utf-8")
        (e.cool / "~$잠김.hwp").write_bytes(b"x")  # 한글 임시 파일은 건너뜀
        (e.cool / "그림.png").write_bytes(b"x")    # 지원하지 않는 형식도 건너뜀
        s = e.collector.run("manual")
        self.assertEqual(s["items"], 2, s)
        self.assertEqual(s["errors"], [])
        self.assertEqual(s["deadlines"], 1)
        edu = e.db.one("SELECT * FROM items WHERE source = 'edufine'")
        self.assertEqual(edu["title"], "학업성적관리위원회 개최 안내")  # 공문은 파일 이름이 제목
        # 같은 문서번호로 쿨메신저 안내와 연결
        self.assertEqual([r["source"] for r in e.db.item(edu["id"])["related"]], ["cool"])
        # 다시 수집해도 그대로
        self.assertEqual(e.collector.run("manual")["items"], 0)
        self.assertEqual(len(e.notified), 2)

    def test_modified_file_with_new_content_is_collected_again(self):
        e = self.env
        p = e.cool / "a.txt"
        p.write_text("첫 안내", encoding="utf-8")
        e.collector.run("manual")
        p.write_text("고친 안내 11월 5일까지", encoding="utf-8")
        os.utime(p, (time.time() + 5, time.time() + 5))
        s = e.collector.run("manual")
        self.assertEqual(s["items"], 1)
        # 같은 내용을 다른 이름으로 저장하면 중복으로 보지 않음
        (e.cool / "b.txt").write_text("고친 안내 11월 5일까지", encoding="utf-8")
        self.assertEqual(e.collector.run("manual")["items"], 0)

    def test_bad_file_reports_error_once(self):
        e = self.env
        (e.edu / "깨진.hwp").write_bytes(b"broken")
        s = e.collector.run("manual")
        self.assertEqual(len(s["errors"]), 1)
        self.assertIn("깨진.hwp", s["errors"][0])
        self.assertEqual(e.collector.run("manual")["errors"], [])  # 바뀌지 않은 파일은 다시 읽지 않음

    def test_missing_folder(self):
        e = self.env
        e.config.values["edufine_folder"] = str(Path(e.tmp.name) / "없음")
        s = e.collector.run("manual")
        self.assertTrue(any("찾을 수 없습니다" in x for x in s["errors"]))

    def test_diagnose_lists_structure_only(self):
        e = self.env
        import sqlite3
        conn = sqlite3.connect(e.cool / "msg.db")
        conn.execute('CREATE TABLE "message" (id INTEGER, body TEXT)')
        conn.execute("INSERT INTO message VALUES (1, '비밀 내용')")
        conn.commit()
        conn.close()
        r = diagnose(e.cool)
        self.assertEqual(r["databases"][0]["tables"], [{"name": "message", "columns": ["id", "body"], "rows": 1}])
        self.assertNotIn("비밀 내용", str(r))


class SchedulerTests(unittest.TestCase):
    def test_due_slots(self):
        times = ["09:00", "12:00", "15:00"]
        now = datetime(2026, 10, 26, 12, 30)
        self.assertEqual(due_slots(now, times, set()), ["2026-10-26 09:00", "2026-10-26 12:00"])
        self.assertEqual(due_slots(now, times, {"2026-10-26 09:00", "2026-10-26 12:00"}), [])
        self.assertEqual(next_slot(now, times), "오늘 15:00")
        self.assertEqual(next_slot(datetime(2026, 10, 26, 16, 0), times), "내일 09:00")

    def test_catch_up_runs_once(self):
        env = Env()
        try:
            sched = Scheduler(env.db, env.config, env.collector)
            # 14시에 처음 켜짐 → 9시·12시를 한 번에 처리
            self.assertIsNotNone(sched.tick(datetime(2026, 10, 26, 14, 0)))
            self.assertIsNone(sched.tick(datetime(2026, 10, 26, 14, 1)))
            # 15시에 한 번 더
            self.assertIsNotNone(sched.tick(datetime(2026, 10, 26, 15, 0)))
            self.assertIsNone(sched.tick(datetime(2026, 10, 26, 15, 30)))
            runs = env.db.query("SELECT reason, slot_keys FROM runs")
            self.assertEqual(len(runs), 2)
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
