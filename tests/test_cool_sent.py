"""쿨메신저 보낸 쪽지 표(tbl_send) 읽기 (0.6.19, 선생님 "쿨메신저 보낸 메세지는 조회가 안되는데").
학교 PC .udb 는 받은 쪽지(tbl_recv: Sender·ReceiveDate)와 보낸 쪽지(tbl_send: Receiver·SendDate)가 다른 표 (2026-11-20 확인)."""

import os
import sqlite3
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from desk import coolmsg
from desk.collect import Collector
from tests.test_collect import Env
from tests.test_cool_files import COLS as RECV_COLS

SEND_COLS = ("MessageKey, MessageBody, Title, Receiver, ReceiverKey, ReferenceList, CCList, MessageType, SendDate, FilePath, "
             "FileHost, AnswerBack, CoolFile2SessionID, ScheduledDate, MessageText, MemoID, IsChecked, IsMoved, LinkURL, "
             "MessageCategory, DeletedDate")


def school_udb(path, recv=(), sent=()):
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE tbl_recv ({RECV_COLS})")
    conn.execute(f"CREATE TABLE tbl_send ({SEND_COLS})")
    conn.execute("CREATE TABLE tbl_member (K_MemberID, MemberID, MemberName)")
    add(conn, recv, sent)
    conn.close()


def add(conn, recv=(), sent=()):
    for body, title, sender, when in recv:
        conn.execute("INSERT INTO tbl_recv (MessageBody, Title, Sender, SenderKey, ReceiveDate, MessageText) VALUES (?,?,?,?,?,?)",
                     (body, title, sender, "k1", when, body))
    for body, title, receiver, when, files in sent:
        conn.execute("INSERT INTO tbl_send (MessageBody, Title, Receiver, ReceiverKey, SendDate, FilePath, MessageText) "
                     "VALUES (?,?,?,?,?,?,?)", (body, title, receiver, "k9", when, files, body))
    conn.commit()


class CoolSentTests(unittest.TestCase):
    def test_detect_keeps_recv_and_finds_send(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(f"CREATE TABLE tbl_recv ({RECV_COLS})")
        conn.execute(f"CREATE TABLE tbl_send ({SEND_COLS})")
        now = "2026-11-20 09:00:00"
        add(conn, [("<p>받은 쪽지 내용입니다</p>", "받음", "이영희(이영희)", now)],
            [("<p>보낸 쪽지 내용이 훨씬 더 깁니다 아주 길게 써 봅니다 정말로 깁니다</p>" * 5, "보냄", "김철수(김철수)", now, "")] * 3)
        self.assertEqual(coolmsg.detect(conn)["table"], "tbl_recv")
        s = coolmsg.detect_sent(conn)
        self.assertEqual((s["table"], s["body"], s["date"], s["receiver"]), ("tbl_send", "MessageBody", "SendDate", "Receiver"))

    def test_receivers_line(self):
        self.assertEqual(coolmsg.receivers("가,나;다"), "가, 나, 다")
        self.assertEqual(coolmsg.receivers(",".join("가나다라마바사")), "가, 나, 다, 라, 마 외 2명")

    def test_collect_reads_sent_memos_once(self):
        env = Env()
        local = Path(env.tmp.name) / "local"
        memo = local / "CoolMessenger" / "Memo"
        memo.mkdir(parents=True)
        path = memo / "홍길동.udb"
        now = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        old = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
        school_udb(path, recv=[("<p>협의회는 11월 25일입니다</p>", "협의회", "이영희(이영희)", now)],
                   sent=[("<p>자료 보내 드립니다. 11월 26일까지 확인 부탁드립니다.</p>", "계획 안내", "김철수(김철수)", now, "계획.hwp"),
                         ("<p>오래전에 보낸 쪽지</p>", "옛날", "김철수(김철수)", old, "")])
        env.config.values.update(coolmessenger_name="홍길동", coolmessenger_folder="", edufine_folder="")
        try:
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                s = Collector(env.db, env.config).run("watch")
                self.assertEqual((s["items"], s["errors"], len(s["new_ids"])), (1, [], 1))   # 보낸 것은 새 자료로 세지 않음
                sent = env.db.query("SELECT * FROM items WHERE source = 'cool_sent'")
                self.assertEqual(len(sent), 1)   # 처음에는 최근 30일만
                it = sent[0]
                self.assertEqual((it["title"], it["status"], it["received_at"]), ("계획 안내", "read", now))
                self.assertIn("(받는 사람: 김철수(김철수))", it["body"])
                self.assertIn("계획.hwp", it["body"])
                self.assertNotIn("보낸 사람", it["body"])
                self.assertEqual(env.db.one("SELECT COUNT(*) AS n FROM events WHERE item_id = ?", (it["id"],))["n"], 0)
                # 다시 수집해도 그대로, 새로 보낸 쪽지만 더함
                conn = sqlite3.connect(path)
                add(conn, sent=[("<p>새로 보낸 쪽지입니다</p>", "새것", "박민수(박민수)", now, "")])
                conn.close()
                s = Collector(env.db, env.config).run("watch")
                self.assertEqual((s["items"], s["errors"]), (0, []))
                self.assertEqual([r["title"] for r in env.db.query("SELECT title FROM items WHERE source = 'cool_sent' ORDER BY id")],
                                 ["계획 안내", "새것"])
                r = coolmsg.check(path, me="홍길동")
                self.assertEqual((r["detected"]["table"], r["sent"]["table"], r["sent"]["rows"]), ("tbl_recv", "tbl_send", 3))
        finally:
            env.close()

    def test_no_sent_table_is_fine(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(f"CREATE TABLE tbl_recv ({RECV_COLS})")
        add(conn, [("<p>받은 쪽지 내용입니다</p>", "받음", "이영희(이영희)", "2026-11-20 09:00:00")])
        self.assertIsNone(coolmsg.detect_sent(conn))


if __name__ == "__main__":
    unittest.main()
