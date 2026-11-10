"""월별 출결 통계가 실제 발급 기록과 맞는지 (가짜 자료).

- 표의 숫자 = 숫자를 누르면 나오는 목록의 건수, 합계 줄 = 학생 줄의 합 = 발급 기록 수
- 명단이 바뀐 뒤(번호 변경·전학)에도 예전 기록이 다른 학생 줄에 붙지 않음
- 웹·다른 창에서 증을 취소·고치면 화면이 알아챌 수 있게 /api/state 값이 바뀜
"""
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

from desk.server import App, make_handler
from desk.telegram import Bot
from tests.test_collect import Env

TYPES = ("질병", "출석인정", "기타", "미인정")


class AttendanceStatsTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.app = App(self.env.db, self.env.config, self.env.collector, Bot(self.env.db, self.env.config, self.env.collector))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
        self.port = self.httpd.server_address[1]
        self.env.config.values["port"] = self.port
        self.httpd.RequestHandlerClass = make_handler(self.app)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.env.close()

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}", "X-Desk-Token": self.app.token}
        if body is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        self.assertEqual(r.status, 200, data)
        return data

    def issue(self, kind, number, name, day):
        return self.req("POST", "/api/passes", {"kind": kind, "student_name": name, "student_number": number,
                                                 "date": day, "time_out": "13:00"})

    def assert_consistent(self, report):
        """표 숫자 = 팝업 목록 건수, 합계 줄 = 학생 줄의 합."""
        for row in report["rows"]:
            for metric in ("absence_forms", "early_count", "out_count"):
                self.assertEqual(row[metric], len(row["details"][metric]), (row["student_name"], metric))
            for kind in TYPES:
                self.assertEqual(row["absence_days"][kind],
                                 sum(d["kind"] == kind for d in row["details"]["absence_days"]))
            self.assertEqual(row["total_absence_days"], len(row["details"]["absence_days"]))
        totals = report["totals"]
        for metric in ("absence_forms", "early_count", "out_count", "total_absence_days"):
            self.assertEqual(totals[metric], sum(row[metric] for row in report["rows"]), metric)
        for kind in TYPES:
            self.assertEqual(totals["absence_days"][kind], sum(row["absence_days"][kind] for row in report["rows"]))

    def test_totals_match_issued_passes_and_details(self):
        self.env.db.replace_students([{"number": 1, "name": "가짜하나"}, {"number": 2, "name": "가짜둘"}])
        self.issue("early", 1, "가짜하나", "2099-03-02")
        self.issue("early", 1, "가짜하나", "2099-03-02")      # 같은 학생 같은 날 두 번도 두 건
        self.issue("out", 2, "가짜둘", "2099-03-03")
        self.issue("early", None, "가짜둘", "2099-03-04")     # 번호 없이 써도 명단의 그 학생으로
        self.issue("early", 2, "가짜둘", "2099-03-31")        # 월말
        self.issue("early", 2, "가짜둘", "2099-04-01")        # 다음 달 → 빠짐
        cancelled = self.issue("out", 1, "가짜하나", "2099-03-05")
        self.req("PATCH", f"/api/passes/{cancelled['id']}", {"status": "cancelled"})
        self.req("POST", "/api/absences", {"student_name": "가짜하나", "student_number": 1,
                                           "start_date": "2099-02-26", "end_date": "2099-03-03",
                                           "absence_type": "질병", "reason": "가짜 사유"})

        report = self.req("GET", "/api/attendance/month?month=2099-03")
        self.assert_consistent(report)
        listed = [p for p in self.req("GET", "/api/passes?month=2099-03") if p["status"] != "cancelled"]
        self.assertEqual(report["totals"]["early_count"], sum(p["kind"] == "early" for p in listed))
        self.assertEqual(report["totals"]["out_count"], sum(p["kind"] == "out" for p in listed))
        self.assertEqual((report["totals"]["early_count"], report["totals"]["out_count"]), (4, 1))
        self.assertEqual(report["totals"]["absence_forms"], len(self.req("GET", "/api/absences?month=2099-03")))
        by_name = {row["student_name"]: row for row in report["rows"]}
        self.assertEqual(by_name["가짜둘"]["early_count"], 2)
        # 텔레그램 통계도 같은 숫자 (2099-03 기준)
        from datetime import date
        text = self.app.bot.month_stats_text(date(2099, 3, 31))
        self.assertIn("조퇴 4건 · 🚶 외출 1건", text)
        self.assertIn("결석계 1건", text)

    def test_roster_change_keeps_old_records_on_their_own_row(self):
        self.env.db.replace_students([{"number": 5, "name": "가짜옛"}])
        self.issue("early", 5, "가짜옛", "2099-05-04")
        # 전학으로 5번이 다른 학생이 되고, 같은 번호가 잘못 두 번 들어간 명단
        self.env.db.replace_students([{"number": 5, "name": "가짜새"}, {"number": 6, "name": "가짜갑"},
                                      {"number": 6, "name": "가짜을"}])
        self.issue("out", 5, "가짜새", "2099-05-05")

        report = self.req("GET", "/api/attendance/month?month=2099-05")
        self.assert_consistent(report)
        by_name = {row["student_name"]: row for row in report["rows"]}
        self.assertEqual((by_name["가짜옛"]["early_count"], by_name["가짜옛"]["out_count"]), (1, 0))
        self.assertEqual((by_name["가짜새"]["early_count"], by_name["가짜새"]["out_count"]), (0, 1))
        self.assertIn("가짜갑", by_name)      # 같은 번호여도 둘 다 표에 남음
        self.assertIn("가짜을", by_name)

    def test_state_changes_when_pass_or_absence_is_cancelled_or_edited(self):
        p = self.issue("early", 1, "가짜하나", "2099-06-01")
        a = self.req("POST", "/api/absences", {"student_name": "가짜하나", "student_number": 1,
                                               "start_date": "2099-06-02", "end_date": "2099-06-02",
                                               "absence_type": "기타", "reason": "가짜"})
        before = self.req("GET", "/api/state")["counts"]
        self.req("PATCH", f"/api/passes/{p['id']}", {"status": "cancelled"})
        middle = self.req("GET", "/api/state")["counts"]
        self.assertNotEqual(middle["passes"], before["passes"])
        self.req("PATCH", f"/api/absences/{a['id']}", {"end_date": "2099-06-03"})
        after = self.req("GET", "/api/state")["counts"]
        self.assertNotEqual(after["absences"], middle["absences"])
        self.assertEqual(self.req("GET", "/api/attendance/month?month=2099-06")["totals"]["early_count"], 0)


if __name__ == "__main__":
    unittest.main()
