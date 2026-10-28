import os
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from desk import extract
from desk.collect import add_text_item, drop_admin_events, edufine_title, retitle_edufine
from tests import test_server  # ServerTests 를 이름으로 가져오면 그 시험이 여기서 또 돔
from tests.test_collect import Env

REF = date(2026, 10, 28)
DOC = """울산광역시교육청
수신 수신자 참조
(경유)
제목 [안내] 2026학년도 학점제형 교과교실제 운영 성과점검 계획
1. 관련: 중등교육과-20951(2026. 10. 27.)
2. 운영 성과점검 계획을 안내하오니 협조하여 주시기 바랍니다.
  가. 대상: 학점제형 교과교실제 운영교(고 42교)
1) 2026학년도 학점제형 교과교실제 운영성과보고서 제출
2) 제출기한: 2026. 11. 26.(목)까지
3) 제출방법: K-에듀파인 교육행정데이터통합관리시스템
울산광역시교육감
시행 중등교육과-21035 (2026. 10. 28.) 접수 천상고등학교-13551 (2026. 10. 28.)
기안 2026. 10. 27. 결재 2026. 10. 28.
오늘이 행복한 울산교육"""
STEM = "(천상고등학교-13551 (본문) 울산광역시교육청 중등교육과) [안내] 2026학년도 학점제형 교과교실제 운영 성과점검 계획"


class ExtractTests(unittest.TestCase):
    def test_official_document(self):
        a = extract.analyze(DOC, ref=REF)
        self.assertEqual(a["title"], "[안내] 2026학년도 학점제형 교과교실제 운영 성과점검 계획")   # '제목' 줄
        # 시행·접수·기안·결재 날짜, 표어의 '오늘', 관련 공문 날짜는 일정이 아님 (사용자 지정)
        self.assertEqual([e["date"] for e in a["events"]], ["2026-11-26"])
        # 이름표뿐인 줄은 앞 줄에서 무엇의 마감인지 가져옴
        self.assertEqual(a["events"][0]["text"], "2026학년도 학점제형 교과교실제 운영성과보고서 제출 (제출기한)")
        self.assertFalse(any("관련" in s for s in a["summary"]))

    def test_admin_dates_only_when_record(self):
        dates = lambda line: [e["date"] for e in extract.analyze(line, ref=REF)["events"]]
        self.assertEqual(dates("결재 후 11월 6일까지 제출"), ["2026-11-06"])          # 낱말만 같은 것은 그대로
        self.assertEqual(dates("11월 5일 시행 예정인 체육대회"), ["2026-11-05"])
        self.assertEqual(dates("접수일: 10월 28일"), [])
        self.assertEqual(dates("시행일: 2026. 11. 1."), [])
        self.assertEqual(dates("내일은 휴업일입니다"), ["2026-10-29"])
        self.assertEqual(dates("내일을 여는 교육"), [])
        # 이름표 앞 줄에도 날짜가 있으면 그 줄 그대로
        a = extract.analyze("신청: 11월 2일\n마감: 11월 3일", ref=REF)
        self.assertEqual([e["text"] for e in a["events"]], ["신청: 11월 2일", "마감: 11월 3일"])

    def test_event_context(self):
        ctx = extract.event_context(DOC, "2026-11-26", REF)
        self.assertEqual(ctx["line"], "2) 제출기한: 2026. 11. 26.(목)까지")
        self.assertTrue(ctx["before"].startswith("1) 2026학년도"))
        self.assertTrue(ctx["after"].startswith("3) 제출방법"))
        self.assertIsNone(extract.event_context(DOC, "2026-10-28", REF))   # 시행·접수·표어뿐
        self.assertEqual(edufine_title("", STEM), "[안내] 2026학년도 학점제형 교과교실제 운영 성과점검 계획")


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def test_old_items_are_tidied_once(self):
        db = self.env.db
        path = str(self.env.edu / f"{STEM}.hwp")
        old = add_text_item(db, "edufine", DOC, title=STEM, received_at="2026-10-28 21:15:00", file_path=path)["id"]
        kept = add_text_item(db, "edufine", DOC + "\n붙임 1부.", title="손으로 고친 제목", received_at="2026-10-28 21:15:00",
                             file_path=str(self.env.edu / "다른.hwp"))["id"]
        reg = add_text_item(db, "cool", "오늘이 행복한 울산교육", title="표어", received_at="2026-10-28 09:00:00")["id"]
        # 예전 규칙으로 들어간 일정 (시행 날짜·표어), 손으로 고친 일정, 달력에 넣은 항목의 일정
        for iid, text in ((old, "오늘이 행복한 울산교육"), (old, "시행 중등교육과-21035 (2026. 10. 28.) 접수 천상고등학교-13551 (2026. 10. 28.)"),
                          (old, "교감 선생님께 보고"), (reg, "오늘이 행복한 울산교육")):
            db.execute("INSERT INTO events(item_id, date, kind, category, text) VALUES(?, '2026-10-28', 'event', '참고', ?)", (iid, text))
        # 날짜만 손으로 고친 일정 (내용은 원문 그대로)
        db.execute("UPDATE events SET date = '2026-11-25' WHERE item_id = ? AND date = '2026-11-26'", (old,))
        db.execute("UPDATE items SET registered = 1 WHERE id = ?", (reg,))

        self.assertEqual(retitle_edufine(db), 1)
        self.assertEqual(db.one("SELECT title FROM items WHERE id = ?", (old,))["title"], "[안내] 2026학년도 학점제형 교과교실제 운영 성과점검 계획")
        self.assertEqual(db.one("SELECT title FROM items WHERE id = ?", (kept,))["title"], "손으로 고친 제목")
        self.assertEqual(drop_admin_events(db), 2)
        texts = [r["text"] for r in db.query("SELECT text FROM events WHERE item_id = ? AND date = '2026-10-28'", (old,))]
        self.assertEqual(texts, ["교감 선생님께 보고"])
        self.assertEqual(db.one("SELECT COUNT(*) AS n FROM events WHERE item_id = ?", (reg,))["n"], 1)
        self.assertEqual(db.one("SELECT COUNT(*) AS n FROM events WHERE item_id = ? AND date = '2026-11-25'", (old,))["n"], 1)
        # 한 번만
        db.execute("UPDATE items SET title = ? WHERE id = ?", (STEM, old))
        self.assertEqual(retitle_edufine(db), 0)
        self.assertEqual(drop_admin_events(db), 0)


class ItemApiTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def test_context_and_original_file(self):
        db = self.env.db
        pdf = self.env.edu / "공문.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
        it = add_text_item(db, "edufine", DOC, title="공문", received_at="2026-10-28 21:15:00", file_path=str(pdf))
        status, body = self.req("GET", f"/api/items/{it['id']}")
        self.assertEqual(status, 200)
        ev = body["events"][0]
        self.assertEqual(ev["context"]["line"], "2) 제출기한: 2026. 11. 26.(목)까지")
        self.assertEqual(body["file"]["name"], "공문.pdf")
        self.assertTrue(body["file"]["pdf"])
        status, data = self.req("GET", f"/api/items/{it['id']}/file")
        self.assertEqual((status, data), (200, b"%PDF-1.4\n%%EOF\n"))
        # 파일 열기: 윈도우의 기본 프로그램으로 (시험에서는 실제로 열지 않음)
        with patch("desk.server.os.startfile", create=True) as start:
            self.assertEqual(self.req("POST", f"/api/items/{it['id']}/open", {})[0], 200)
            start.assert_called_once_with(str(pdf.resolve()))
        if not hasattr(os, "startfile"):
            self.assertEqual(self.req("POST", f"/api/items/{it['id']}/open", {})[0], 400)   # 윈도우가 아니면 안내만

        # 수집 폴더 밖 파일·지워진 파일·쿨메신저 쪽지(#)는 보내지 않음
        outside = Path(self.env.tmp.name) / "밖.pdf"
        outside.write_bytes(b"%PDF-1.4")
        for fp in (str(outside), str(self.env.edu / "없음.pdf"), str(self.env.cool / "a.udb#3")):
            other = add_text_item(db, "edufine", f"다른 공문 {fp}", title="다른", received_at="2026-10-28 21:15:00", file_path=fp)
            self.assertIsNone(self.req("GET", f"/api/items/{other['id']}")[1]["file"])
            self.assertEqual(self.req("GET", f"/api/items/{other['id']}/file")[0], 404)


if __name__ == "__main__":
    unittest.main()
