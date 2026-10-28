import base64
import io
import json
import os
import threading
import unittest
import zipfile
from datetime import date
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from desk import coolmsg, neis, picker, queries, schoolcal
from tests.test_collect import Env
from tests.xlsx_fixture import build_xlsx

MONTHS = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2]


def ev(r):
    return [(e["date"], e["end_date"], e["text"]) for e in r["events"]]


def serial(d):
    return (d - date(1899, 12, 30)).days


class TextTests(unittest.TestCase):
    def test_lines(self):
        r = schoolcal.parse(2026, text="2026학년도 학사일정\n3. 2.(월) 입학식\n4월 22일 ~ 24일 1학기 중간고사\n"
                                       "5. 5.(화) 어린이날 / 5. 6.(수) 재량휴업일\n3월 3일(화요일) 학부모 상담 주간 ~ 3월 7일\n"
                                       "2월 12일 졸업식\n2027. 1. 8. 겨울방학식\n다음 주 화요일 협의회")
        self.assertEqual(r["mode"], "text")
        self.assertEqual(ev(r), [
            ("2026-03-02", None, "입학식"),
            ("2026-03-03", "2026-03-07", "학부모 상담 주간"),
            ("2026-04-22", "2026-04-24", "1학기 중간고사"),
            ("2026-05-05", None, "어린이날"),
            ("2026-05-06", None, "재량휴업일"),
            ("2027-01-08", None, "겨울방학식"),
            ("2027-02-12", None, "졸업식"),     # 1~2월은 다음 해
        ])
        cats = {e["text"]: e["category"] for e in r["events"]}
        self.assertEqual((cats["1학기 중간고사"], cats["입학식"], cats["재량휴업일"]), ("수업·평가", "행사", "학사"))

    def test_out_of_year_dropped(self):
        r = schoolcal.parse(2026, text="2025. 12. 1. 지난 학년도\n3월 2일 입학식")
        self.assertEqual(ev(r), [("2026-03-02", None, "입학식")])
        self.assertEqual(r["skipped"], 1)

    def test_pasted_excel_table(self):
        r = schoolcal.parse(2026, text="날짜\t행사\n3월 2일\t입학식\n4월 22일\t중간고사\n5월 5일\t어린이날")
        self.assertEqual(r["mode"], "list")
        self.assertEqual(len(r["events"]), 3)


class TableTests(unittest.TestCase):
    def grid(self):
        head = ["일"]
        for m in MONTHS:
            head += [f"{m}월", ""]
        rows = [["2026학년도 학사일정"], head] + [[d] + [""] * 24 for d in range(1, 32)]

        def put(m, d, text, wd="월"):
            c = 1 + MONTHS.index(m) * 2
            rows[1 + d][c] = wd
            rows[1 + d][c + 1] = text
        return rows, put

    def test_calendar_grid_with_merged_range(self):
        rows, put = self.grid()
        put(3, 2, "입학식")
        put(3, 5, "학부모총회\n동아리 조직")
        put(7, 20, "여름방학")
        put(2, 12, "졸업식")
        r = schoolcal.parse(2026, "학사일정.xlsx", build_xlsx([("학사일정", rows, ["K22:K31"])]))
        self.assertEqual((r["mode"], r["sheet"]), ("grid", "학사일정"))
        self.assertEqual(ev(r), [
            ("2026-03-02", None, "입학식"),
            ("2026-03-05", None, "동아리 조직"),
            ("2026-03-05", None, "학부모총회"),
            ("2026-07-20", "2026-07-29", "여름방학"),
            ("2027-02-12", None, "졸업식"),
        ])

    def test_transposed_grid(self):
        rows = [["월"] + list(range(1, 32))] + [[f"{m}월"] + [""] * 31 for m in (3, 4, 5)]
        rows[1][2] = "입학식"
        rows[3][5] = "어린이날"
        r = schoolcal.parse(2026, "t.xlsx", build_xlsx([("s", rows, [])]))
        self.assertEqual(ev(r), [("2026-03-02", None, "입학식"), ("2026-05-05", None, "어린이날")])

    def test_list_with_excel_dates_and_end_column(self):
        rows = [["날짜", "끝", "행사명", "비고"],
                [serial(date(2026, 3, 2)), "", "입학식", ""],
                [serial(date(2026, 4, 22)), serial(date(2026, 4, 24)), "1학기 중간고사", "1~3학년"],
                ["2026.5.5", "", "어린이날", "공휴일"],
                ["7. 20.(월)", "8. 16.(일)", "여름방학", ""]]
        # 빈 시트가 있어도 일정이 가장 많은 시트를 고름
        r = schoolcal.parse(2026, "list.xlsx", build_xlsx([("표지", [["학사일정"]], []), ("목록", rows, [])]))
        self.assertEqual((r["mode"], r["sheet"]), ("list", "목록"))
        self.assertEqual(ev(r), [("2026-03-02", None, "입학식"), ("2026-04-22", "2026-04-24", "1학기 중간고사"),
                                 ("2026-05-05", None, "어린이날"), ("2026-07-20", "2026-08-16", "여름방학")])

    def test_csv_and_documents(self):
        csv = "일자,행사\n2026-03-02,입학식\n2026-04-22,중간고사\n2026-05-05,어린이날\n".encode("cp949")
        self.assertEqual(len(schoolcal.parse(2026, "a.csv", csv)["events"]), 3)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Contents/section0.xml", '<s xmlns:hp="urn:hp"><hp:p><hp:run><hp:t>3. 2.(월) 입학식</hp:t></hp:run></hp:p>'
                                                '<hp:p><hp:run><hp:t>7월 20일 ~ 8월 16일 여름방학</hp:t></hp:run></hp:p></s>')
        self.assertEqual(ev(schoolcal.parse(2026, "학사.hwpx", buf.getvalue())),
                         [("2026-03-02", None, "입학식"), ("2026-07-20", "2026-08-16", "여름방학")])
        for name, msg in (("a.xls", ".xlsx"), ("a.docx", "읽을 수 없습니다"), ("a.xlsx", ".xlsx")):
            with self.assertRaises(schoolcal.CalError) as cm:
                schoolcal.parse(2026, name, b"not a real file")
            self.assertIn(msg, str(cm.exception))

    def test_save_replaces_same_year(self):
        env = Env()
        try:
            evs = schoolcal.parse(2026, text="3월 2일 입학식\n4월 22일 중간고사")["events"]
            schoolcal.save(env.db, 2026, evs, "a.xlsx")
            res = schoolcal.save(env.db, 2026, evs[:1], "b.xlsx")
            self.assertEqual(res["replaced"], 1)
            self.assertEqual(env.db.one("SELECT COUNT(*) AS n FROM events")["n"], 1)
            it = env.db.one("SELECT * FROM items")
            self.assertEqual((it["source"], it["status"], it["category"]), ("school", "done", "학사"))
            schoolcal.save(env.db, 2027, evs, "c.xlsx")          # 다른 학년도는 그대로
            self.assertEqual(env.db.one("SELECT COUNT(*) AS n FROM items")["n"], 2)
            # 달력·브리핑에 보임 (처리 완료 항목이어도)
            march = queries.events_between(env.db, date(2026, 3, 1), date(2026, 3, 31))
            self.assertEqual(len(march), 2)
            self.assertEqual({e["item_title"] for e in march}, {None})   # 달력에는 행사 이름이 보임
        finally:
            env.close()


class FakeNeis:
    def __init__(self):
        fake = self
        self.calls = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                fake.calls.append((u.path, q))
                if q.get("KEY") == "bad":
                    body = {"RESULT": {"CODE": "ERROR-290", "MESSAGE": "인증키가 유효하지 않습니다."}}
                elif u.path.endswith("/schoolInfo"):
                    body = {"schoolInfo": [{"head": [{"list_total_count": 1}]}, {"row": [
                        {"SCHUL_NM": "한빛고등학교", "ATPT_OFCDC_SC_CODE": "N10", "ATPT_OFCDC_SC_NM": "충청남도교육청",
                         "SD_SCHUL_CODE": "8140000", "ORG_RDNMA": "충남 어딘가", "SCHUL_KND_SC_NM": "고등학교"}]}]} \
                        if "한빛" in q.get("SCHUL_NM", "") else {"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}
                else:
                    # 방학은 평일마다 한 줄씩 (토·일 25~26일은 없음)
                    days = ["20260302:입학식", "20260720:여름방학", "20260721:여름방학", "20260722:여름방학",
                            "20260723:여름방학", "20260724:여름방학", "20260727:여름방학",
                            "20260307:토요휴업일", "20270212:졸업식"]
                    mine = {"ATPT_OFCDC_SC_CODE": q.get("ATPT_OFCDC_SC_CODE"), "SD_SCHUL_CODE": q.get("SD_SCHUL_CODE")}
                    other = {"ATPT_OFCDC_SC_CODE": "B10", "SD_SCHUL_CODE": "7010000"}
                    rows = [{"AA_YMD": d.split(":")[0], "EVENT_NM": d.split(":")[1], **mine} for d in days]
                    rows.append({"AA_YMD": "20260515", "EVENT_NM": "다른 학교 체육대회", **other})
                    if not q.get("KEY"):  # 인증키가 없으면 나이스가 아무 학교 것이나 몇 건 보냄
                        rows = [{**r, **other} for r in rows[:5]]
                    body = {"SchoolSchedule": [{"head": [{"list_total_count": len(rows)}]}, {"row": rows}]}
                data = json.dumps(body, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/hub"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class NeisTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeNeis()

    def tearDown(self):
        self.fake.close()

    def test_search_and_schedule(self):
        s = neis.search_schools("한빛고", "KEY1", self.fake.base)
        self.assertEqual((s[0]["atpt"], s[0]["code"], s[0]["office"]), ("N10", "8140000", "충청남도교육청"))
        self.assertEqual(neis.search_schools("없는학교", "KEY1", self.fake.base), [])
        r = neis.schedule("N10", "8140000", 2026, "KEY1", self.fake.base)
        self.assertEqual(ev(r), [("2026-03-02", None, "입학식"), ("2026-07-20", "2026-07-27", "여름방학"),
                                 ("2027-02-12", None, "졸업식")])   # 토요휴업일·다른 학교 것 빼고, 주말 건너 이어진 방학은 한 기간
        path, q = self.fake.calls[-1]
        self.assertEqual((q["AA_FROM_YMD"], q["AA_TO_YMD"], q["SD_SCHUL_CODE"], q["KEY"]), ("20260301", "20270228", "8140000", "KEY1"))
        with self.assertRaises(neis.NeisError) as cm:                                    # 인증키 없음 → 다른 학교 것뿐
            neis.schedule("N10", "1", 2027, "", self.fake.base)
        self.assertIn("다른 학교", str(cm.exception))
        self.assertEqual(self.fake.calls[-1][1].get("AA_TO_YMD"), "20280229")          # 윤년

    def test_errors(self):
        with self.assertRaises(neis.NeisError) as cm:
            neis.search_schools("한빛", "bad", self.fake.base)
        self.assertIn("인증키", str(cm.exception))
        with self.assertRaises(neis.NeisError):
            neis.search_schools("한빛", "", "http://127.0.0.1:1/hub")


class SearchTests(unittest.TestCase):
    def test_search_everything(self):
        env = Env()
        try:
            db = env.db
            from desk.collect import add_text_item
            add_text_item(db, "cool", "교무부 안내\n협의록은 학교혁신과-12345 공문 참고\n마감은 11월 3일까지")
            db.replace_students([{"number": 3, "name": "도윤서"}])
            db.execute("INSERT INTO passes(serial, kind, student_name, date, reason, note, created_at) "
                       "VALUES('20261027-01', 'out', '김가온', '2026-10-27', '병원 진료', '치과', 'x')")
            self.assertEqual(len(queries.search(db, "학교혁신과-12345")["items"]), 1)   # 본문·문서번호
            self.assertEqual(len(queries.search(db, "제출·마감")["items"]), 1)          # 분류
            self.assertEqual(len(queries.search(db, "교무부 협의록")["items"]), 1)       # 여러 낱말 AND
            self.assertEqual(len(queries.search(db, "교무부 없는말")["items"]), 0)
            self.assertEqual(len(queries.search(db, "치과")["passes"]), 1)              # 메모
            self.assertEqual(len(queries.search(db, "외출 김가온")["passes"]), 1)
            self.assertEqual(queries.search(db, "도윤서")["students"][0]["number"], 3)  # 학생 명단
            self.assertEqual(len(queries.search(db, "11월 3일")["events"]), 0)   # 체크(등록) 전에는 일정 아님
            db.execute("UPDATE items SET registered = 1")
            self.assertEqual(len(queries.search(db, "11월 3일")["events"]), 1)
            self.assertEqual(queries.search(db, "  ")["students"], [])
            self.assertEqual(queries.search(db, "50%")["items"], [])                     # % 는 글자 그대로
        finally:
            env.close()


class CoolPathTests(unittest.TestCase):
    def test_resolve_prefers_direct_path(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": "/x/Local"}):
            # 경로 구분 기호(/ 와 \\)는 윈도우·리눅스가 달라 Path 로 비교
            self.assertEqual(coolmsg.resolve("홍길동", ""), Path("/x/Local") / "CoolMessenger" / "Memo" / "홍길동.udb")
            self.assertEqual(coolmsg.resolve("홍길동", '"/d/memo/kim.udb"'), Path("/d/memo/kim.udb"))
        with self.assertRaises(coolmsg.CoolError):
            coolmsg.resolve("", "")
        with self.assertRaises(coolmsg.CoolError):
            coolmsg.resolve("", "/")      # 폴더

    def test_picker(self):
        s = picker.build_script("file", "고르기", "C:\\it's", "쪽지 (*.udb)|*.udb")
        self.assertIn("OpenFileDialog", s)
        self.assertIn("'C:\\it''s'", s)       # 작은따옴표 이스케이프
        self.assertIn("FolderBrowserDialog", picker.build_script("folder", "폴더"))
        if os.name != "nt":
            with self.assertRaises(picker.PickerError):
                picker.pick("file", "x")


class ServerSchoolCalTests(unittest.TestCase):
    def test_endpoints(self):
        from tests.test_server import ServerTests
        ServerTests.setUpClass()
        try:
            t = ServerTests("test_bad_json")
            data = base64.b64encode(build_xlsx([("s", [["날짜", "행사"], ["3월 2일", "입학식"], ["4월 22일", "중간고사"],
                                                       ["5월 5일", "어린이날"]], [])])).decode()
            st, r = t.req("POST", "/api/schoolcal/preview", {"school_year": 2026, "filename": "a.xlsx", "data": data})
            self.assertEqual((st, len(r["events"])), (200, 3), r)
            self.assertEqual(t.req("POST", "/api/schoolcal/preview", {"school_year": 2026, "filename": "a.xlsx", "data": "@@"})[0], 400)
            self.assertEqual(t.req("POST", "/api/schoolcal/preview", {"school_year": 1, "text": "x"})[0], 400)
            st, saved = t.req("POST", "/api/schoolcal/save", {"school_year": 2026, "events": r["events"], "origin": "a.xlsx"})
            self.assertEqual((st, saved["count"]), (200, 3), saved)
            bad = [{**r["events"][0], "category": "없는분류"}]
            self.assertEqual(t.req("POST", "/api/schoolcal/save", {"school_year": 2026, "events": bad})[0], 400)
            self.assertEqual(t.req("POST", "/api/schoolcal/neis", {"school_year": 2026})[0], 400)   # 학교 미선택
            st, s = t.req("GET", "/api/state")
            self.assertNotIn("neis_key\"", json.dumps(s["settings"]))
        finally:
            ServerTests.tearDownClass()


if __name__ == "__main__":
    unittest.main()
