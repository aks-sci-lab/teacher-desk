import base64
import os
import sqlite3
import tempfile
import unittest
import zlib
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from desk import coolmsg
from desk.collect import Collector, add_text_item
from tests.test_collect import Env


def ole(dt):
    """델파이·윈도우 날짜 값 (1899-12-30 부터 지난 날 수)."""
    return (dt - datetime(1899, 12, 30)).total_seconds() / 86400


def make_udb(path, schema="a", rows=()):
    """실제 구조를 모르므로 흔한 모양 두 가지로 시험."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tbl_config (k TEXT, v TEXT)")
    conn.execute("INSERT INTO tbl_config VALUES ('version', '1.0 아주 긴 설정 값이 들어 있는 줄입니다 아주 긴 설정 값')")
    if schema == "a":   # 이름이 분명한 표
        conn.execute("CREATE TABLE tbl_memo (idx INTEGER PRIMARY KEY, sender_name TEXT, title TEXT, content TEXT, reg_date TEXT)")
        conn.executemany("INSERT INTO tbl_memo(sender_name, title, content, reg_date) VALUES (?,?,?,?)", rows)
    else:               # 이름이 애매하고 날짜가 숫자(델파이 날짜), 내용이 RTF
        conn.execute("CREATE TABLE M1 (NO INTEGER, FROMID TEXT, DATA BLOB, T REAL)")
        conn.executemany("INSERT INTO M1 VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()


def comp(html):
    """쿨메신저가 내용을 압축해 저장하는 모양: '{COMP}' + base64(zlib(UTF-16 HTML))."""
    return "{COMP}" + base64.b64encode(zlib.compress(html.encode("utf-16-le"))).decode()


class ParseTests(unittest.TestCase):
    def test_parse_when(self):
        self.assertEqual(coolmsg.parse_when("2026-10-27 09:12:33"), datetime(2026, 10, 27, 9, 12, 33))
        self.assertEqual(coolmsg.parse_when("2026.10.27 오후 3:05"), datetime(2026, 10, 27, 15, 5))
        self.assertEqual(coolmsg.parse_when("20261027091233"), datetime(2026, 10, 27, 9, 12, 33))
        self.assertEqual(coolmsg.parse_when(ole(datetime(2026, 10, 27, 12, 0))).replace(microsecond=0),
                         datetime(2026, 10, 27, 12, 0))
        self.assertIsNotNone(coolmsg.parse_when(1793000000))
        self.assertIsNone(coolmsg.parse_when("안녕하세요"))
        self.assertIsNone(coolmsg.parse_when(42))

    def test_clean_body(self):
        rtf = r"{\rtf1\ansi{\fonttbl{\f0 Gulim;}}\f0 \'c8\'b8\'c0\'c7 \'be\'c8\'b3\'bb\par 11\'bf\'f9 3\'c0\'cf\'b1\'ee\'c1\'f6}"
        self.assertEqual(coolmsg.clean_body(rtf), "회의 안내\n11월 3일까지")
        self.assertEqual(coolmsg.clean_body(r"{\rtf1 \u54617?\u44368? ok}"), "학교 ok")
        self.assertEqual(coolmsg.clean_body("<p>첫 줄</p><br>둘째"), "첫 줄\n\n둘째")
        self.assertEqual(coolmsg.clean_body("회의".encode("cp949")), "회의")
        html = '<div style="line-height: 1.35;"><font face="굴림" size="2">협의록을 11월 3일까지<br>제출해 주세요</font></div>'
        self.assertEqual(coolmsg.clean_body(comp(html)), "협의록을 11월 3일까지\n제출해 주세요")
        self.assertEqual(coolmsg.clean_body(comp('<img src="https://sticker.coolmessenger.com/a.png">')), "")  # 스티커만
        self.assertEqual(coolmsg.clean_body("{COMP}eJzdkL1OAkEU깨짐"), "")                                  # 풀 수 없으면 비움
        self.assertEqual(coolmsg.clean_body(html.encode("utf-16-le")), "협의록을 11월 3일까지\n제출해 주세요")

    def test_name_validation(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\kim\AppData\Local"}):
            p = coolmsg.memo_path("홍길동")
            self.assertEqual(p.name, "홍길동.udb")
            self.assertIn("CoolMessenger", str(p))
            self.assertEqual(coolmsg.memo_path("홍길동.udb").name, "홍길동.udb")
        for bad in ("../x", "a\\b", "", "a:b"):
            with self.assertRaises(coolmsg.CoolError):
                coolmsg.memo_path(bad)


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.local = Path(self.tmp.name)
        self.memo = self.local / "CoolMessenger" / "Memo"
        self.memo.mkdir(parents=True)
        self.env_patch = patch.dict(os.environ, {"LOCALAPPDATA": str(self.local)})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.tmp.cleanup()

    def test_schema_a_first_import_and_incremental(self):
        now = datetime.now()
        old = (now - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
        recent = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        path = self.memo / "홍길동.udb"
        make_udb(path, "a", [
            ("교무부", "오래된 안내", "지난 학기 안내입니다", old),
            ("교무부", "성적관리위원회", "협의록을 11월 3일(화)까지 제출해 주세요", recent),
        ])
        self.assertEqual(coolmsg.candidates(), ["홍길동"])
        info, memos, top = coolmsg.read_new(coolmsg.memo_path("홍길동"))
        self.assertEqual((info["table"], info["body"], info["title"], info["date"], info["sender"]),
                         ("tbl_memo", "content", "title", "reg_date", "sender_name"))
        self.assertEqual([m["title"] for m in memos], ["성적관리위원회"])  # 처음에는 최근 30일만
        self.assertEqual(top, 2)
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO tbl_memo(sender_name, title, content, reg_date) VALUES ('행정실', '연말정산', '서류 제출', ?)", (recent,))
        conn.commit()
        conn.close()
        _, memos, top = coolmsg.read_new(path, top)
        self.assertEqual([m["sender"] for m in memos], ["행정실"])
        self.assertEqual(top, 3)
        _, memos, _ = coolmsg.read_new(path, 999)   # 파일이 새로 만들어진 경우 처음처럼
        self.assertEqual(len(memos), 2)

    def test_schema_b_rtf_and_ole_dates(self):
        now = datetime.now()
        rtf = r"{\rtf1\ansi \'c7\'d0\'b3\'e2 \'c7\'f9\'c0\'c7\'c8\'b8\par \'b3\'bb\'c0\'cf \'bf\'c0\'c8\'c4 4\'bd\'c3}"
        make_udb(self.memo / "kim.udb", "b", [
            (1, "teacher01", rtf.encode("ascii"), ole(now - timedelta(hours=2))),
            (2, "teacher02", r"{\rtf1 \'bf\'ac\'bc\'f6 \'be\'c8\'b3\'bb}".encode("ascii"), ole(now - timedelta(hours=1))),
        ])
        info, memos, _ = coolmsg.read_new(coolmsg.memo_path("kim"))
        self.assertEqual((info["table"], info["body"], info["date"]), ("M1", "DATA", "T"))
        self.assertEqual(memos[0]["body"], "학년 협의회\n내일 오후 4시")
        self.assertEqual(memos[0]["sender"], "teacher01")
        self.assertLess(abs((memos[0]["when"] - (now - timedelta(hours=2))).total_seconds()), 2)

    def test_missing_and_not_sqlite(self):
        with self.assertRaises(coolmsg.CoolError) as cm:
            coolmsg.read_new(coolmsg.memo_path("없는사람"))
        self.assertIn("파일이 없습니다", str(cm.exception))
        (self.memo / "x.udb").write_bytes(b"\x01\x02notsqlite" * 10)
        with self.assertRaises(coolmsg.CoolError) as cm:
            coolmsg.read_new(coolmsg.memo_path("x"))
        self.assertIn("형식", str(cm.exception))

    def test_reads_while_open_with_wal(self):
        path = self.memo / "lee.udb"
        make_udb(path, "a", [])
        writer = sqlite3.connect(path)  # 쿨메신저가 켜져 있는 것처럼 열어 둠
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO tbl_memo(sender_name, title, content, reg_date) VALUES ('a', '방금 온 쪽지', '내용입니다', ?)",
                       (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
        writer.commit()
        try:
            _, memos, _ = coolmsg.read_new(path)
            self.assertEqual([m["title"] for m in memos], ["방금 온 쪽지"])  # -wal 안의 쪽지도 읽음
        finally:
            writer.close()

    def test_check_reports_structure(self):
        make_udb(self.memo / "hong.udb", "a", [("교무부", "안내", "내용", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))])
        r = coolmsg.check(coolmsg.memo_path("hong"))
        self.assertEqual(r["detected"]["table"], "tbl_memo")
        self.assertEqual(r["recent"][0]["title"], "안내")
        self.assertIn({"name": "tbl_config", "columns": ["k", "v"], "rows": 1}, r["tables"])


class CollectorCoolTests(unittest.TestCase):
    def test_collector_reads_memos_once(self):
        env = Env()
        local = Path(env.tmp.name) / "local"
        memo = local / "CoolMessenger" / "Memo"
        memo.mkdir(parents=True)
        recent = (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        make_udb(memo / "홍길동.udb", "a", [("교무부", "성적관리위원회", "협의록을 11월 3일(화)까지 제출해 주세요", recent)])
        env.config.values.update(coolmessenger_name="홍길동", coolmessenger_folder="", edufine_folder="")
        try:
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                s = Collector(env.db, env.config).run("manual")
                self.assertEqual((s["items"], s["errors"], s["sources"]), (1, [], ["cool"]))
                it = env.db.one("SELECT * FROM items")
                self.assertEqual((it["source"], it["title"], it["received_at"]), ("cool", "성적관리위원회", recent))
                self.assertIn("보낸 사람: 교무부", it["body"])
                self.assertEqual(Collector(env.db, env.config).run("manual")["items"], 0)
                env.config.values["coolmessenger_name"] = "없는사람"
                s = Collector(env.db, env.config).run("manual")
                self.assertTrue(any("파일이 없습니다" in e for e in s["errors"]))
        finally:
            env.close()

    def test_repairs_items_saved_before_comp_support(self):
        env = Env()
        local = Path(env.tmp.name) / "local"
        memo = local / "CoolMessenger" / "Memo"
        memo.mkdir(parents=True)
        recent = (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        body = comp("<div>협의록을 11월 3일(화)까지 제출해 주세요</div>")
        make_udb(memo / "홍길동.udb", "a", [("교무부", "성적관리위원회", body, recent),
                                           ("교무부", "연수 안내", "신청은 11월 2일까지", recent)])
        env.config.values.update(coolmessenger_name="홍길동", coolmessenger_folder="", edufine_folder="")
        try:
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                path = coolmsg.resolve("홍길동")
                # 0.4.0 까지: 압축된 내용이 그대로 들어가 있음
                sender = "\n\n(보낸 사람: 교무부)"
                add_text_item(env.db, "cool", body + sender, title="성적관리위원회", received_at=recent, file_path=f"{path}#1")
                add_text_item(env.db, "cool", "신청은 11월 2일까지" + sender, title="연수 안내", received_at=recent,
                              file_path=f"{path}#2")
                env.db.set_meta(f"cool_last_rowid:{path}", 2)
                s = Collector(env.db, env.config).run("manual")
                self.assertEqual((s["items"], s["errors"]), (1, []))
                rows = env.db.query("SELECT title, body FROM items ORDER BY id")
                self.assertEqual([r["title"] for r in rows], ["연수 안내", "성적관리위원회"])
                self.assertNotIn("{COMP}", rows[1]["body"])
                self.assertIn("11월 3일(화)까지", rows[1]["body"])
                self.assertEqual(env.db.one("SELECT COUNT(*) AS n FROM events WHERE kind = 'deadline'")["n"], 2)
                self.assertEqual(Collector(env.db, env.config).run("manual")["items"], 0)  # 한 번만
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
