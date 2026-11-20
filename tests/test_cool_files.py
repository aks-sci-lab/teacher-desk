"""쿨메신저 첨부 목록 열(FilePath) 읽기 (0.6.17, 선생님 "첨부파일이 있는데 없음으로 표시하는 경우가 있어").
학교 PC .udb 의 표·열 이름은 선생님이 보낸 '파일 구조'(2026-11-20)와 같게. FilePath 값의 실제 모양은 아직 몰라 여러 모양으로 시험."""

import os
import sqlite3
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from desk import ai, coolmsg
from desk.collect import Collector
from tests.test_collect import Env

COLS = ("MessageKey, MessageBody, Title, Sender, SenderKey, ReferenceList, CCList, MessageType, ReceiveDate, FilePath, "
        "CoolFile2SessionID, LinkURL, FileHost, IsUnRead, MessageText, MemoID, IsChecked, IsMoved, MessageCategory, DeletedDate")


def school_udb(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE tbl_recv ({COLS})")
    conn.execute("CREATE TABLE tbl_member (K_MemberID, MemberID, MemberName)")
    for body, title, sender, when, files in rows:
        conn.execute("INSERT INTO tbl_recv (MessageBody, Title, Sender, ReceiveDate, FilePath, FileHost, MessageText) "
                     "VALUES (?,?,?,?,?,?,?)", (body, title, sender, when, files, "10.1.2.3" if files else "", body))
    conn.commit()
    conn.close()


class CoolFilesTests(unittest.TestCase):
    def test_file_names_shapes(self):
        self.assertEqual(coolmsg.file_names("계획서.hwp|사진.jpg"), ["계획서.hwp", "사진.jpg"])
        self.assertEqual(coolmsg.file_names(r"C:\Users\a.b\Down\가 나.hwpx;x.png:12345"), ["가 나.hwpx", "x.png"])
        self.assertEqual(coolmsg.file_names("보고서 v1.2.pdf"), ["보고서 v1.2.pdf"])
        self.assertEqual(coolmsg.file_names("1.2.3.4"), [])
        self.assertEqual(coolmsg.file_names(None), [])

    def test_detect_school_columns(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(f"CREATE TABLE tbl_recv ({COLS})")
        conn.execute("INSERT INTO tbl_recv (MessageBody, Title, Sender, ReceiveDate, FilePath, FileHost) VALUES "
                     "('협의회 자료를 보내 드립니다 확인 부탁', '협의회', '이영희(이영희)', '2026-11-20 09:00:00', '안내.hwp', '10.1.2.3')")
        info = coolmsg.detect(conn)
        self.assertEqual((info["table"], info["body"], info["files"]), ("tbl_recv", "MessageBody", "FilePath"))

    def test_collect_adds_files_line_and_summary_uses_it(self):
        env = Env()
        local = Path(env.tmp.name) / "local"
        memo = local / "CoolMessenger" / "Memo"
        memo.mkdir(parents=True)
        now = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        school_udb(memo / "홍길동.udb", [
            ("<p>협의회 자료를 보내 드립니다. 확인 부탁드립니다.</p>", "협의회 자료", "이영희(이영희)", now, "회의록.hwp|현장 사진.jpg"),
            ("<p>내일 회의는 3시입니다</p>", "회의 시간", "김철수(김철수)", now, "")])
        env.config.values.update(coolmessenger_name="홍길동", coolmessenger_folder="", edufine_folder="")
        try:
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                s = Collector(env.db, env.config).run("watch")
            self.assertEqual(s["items"], 2)
            body = env.db.one("SELECT body FROM items WHERE title LIKE '협의회%'")["body"]
            self.assertIn("(첨부 파일: 회의록.hwp, 현장 사진.jpg)", body)
            self.assertTrue(body.endswith("(보낸 사람: 이영희(이영희))"))   # 보낸 사람 줄은 늘 끝
            self.assertEqual(coolmsg.files_in_text(body), ["회의록.hwp", "현장 사진.jpg"])
            other = env.db.one("SELECT body FROM items WHERE title LIKE '회의%' AND title NOT LIKE '협의회%'")["body"]
            self.assertNotIn("첨부 파일", other)
            # AI 가 '없음' 이라 해도 실제 첨부 목록으로
            f = ai.cool_fields("보낸 사람: 이영희\n업무 내용: 자료 확인\n마감 기한: 없음\n첨부파일: 없음\n긴급 여부: 보통", body)
            self.assertEqual(f["files"], "회의록.hwp, 현장 사진.jpg")
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
