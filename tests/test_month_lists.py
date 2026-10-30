"""조퇴·외출 발급 기록과 결석계를 월별로 불러오기 (0.5.42)."""
import unittest

from tests import test_server


class MonthListTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def pass_(self, day, name, kind="early", time_out="13:00"):
        status, p = self.req("POST", "/api/passes", {"kind": kind, "student_name": name, "date": day, "time_out": time_out})
        self.assertEqual(status, 200, p)
        return p

    def absence(self, start, end, name):
        status, a = self.req("POST", "/api/absences", {
            "student_name": name, "start_date": start, "end_date": end,
            "absence_type": "질병", "reason": "감기", "guardian_name": "보호자"})
        self.assertEqual(status, 200, a)
        return a

    def test_passes_by_month_newest_day_first_then_time(self):
        for day, name, t in [("2031-03-02", "가", "10:00"), ("2031-03-15", "나", "14:00"), ("2031-03-15", "다", "09:30"),
                             ("2031-03-31", "라", "11:00"), ("2031-04-01", "마", "11:00"), ("2031-02-28", "바", "11:00")]:
            self.pass_(day, name, time_out=t)
        status, rows = self.req("GET", "/api/passes?month=2031-03")
        self.assertEqual(status, 200)
        self.assertEqual([(r["date"], r["student_name"]) for r in rows],
                         [("2031-03-31", "라"), ("2031-03-15", "다"), ("2031-03-15", "나"), ("2031-03-02", "가")])
        self.assertEqual([r["student_name"] for r in self.req("GET", "/api/passes?month=2031-04")[1]], ["마"])
        self.assertEqual(self.req("GET", "/api/passes?month=2031-05")[1], [])
        # 하루 보기는 그대로
        self.assertEqual([r["student_name"] for r in self.req("GET", "/api/passes?date=2031-03-15")[1]], ["다", "나"])

    def test_passes_cancelled_still_listed_for_the_month(self):
        p = self.pass_("2031-06-10", "사")
        self.req("PATCH", f"/api/passes/{p['id']}", {"status": "cancelled"})
        rows = self.req("GET", "/api/passes?month=2031-06")[1]
        self.assertEqual([(r["student_name"], r["status"]) for r in rows], [("사", "cancelled")])

    def test_absences_by_month_include_those_continuing_from_before(self):
        self.absence("2031-07-30", "2031-08-02", "아")     # 7월 말~8월 초
        self.absence("2031-08-10", "2031-08-10", "자")
        self.absence("2031-09-01", "2031-09-01", "차")
        aug = self.req("GET", "/api/absences?month=2031-08")[1]
        self.assertEqual([r["student_name"] for r in aug], ["자", "아"])            # 시작 날짜가 늦은 것이 위
        self.assertEqual([r["student_name"] for r in self.req("GET", "/api/absences?month=2031-07")[1]], ["아"])
        self.assertEqual([r["student_name"] for r in self.req("GET", "/api/absences?month=2031-09")[1]], ["차"])
        self.assertEqual(self.req("GET", "/api/absences?month=2031-10")[1], [])

    def test_bad_month_rejected(self):
        for bad in ("2031-13", "2031-00", "2031", "abc", "2031-3", "2031-03-01"):
            self.assertEqual(self.req("GET", f"/api/passes?month={bad}")[0], 400, bad)
            self.assertEqual(self.req("GET", f"/api/absences?month={bad}")[0], 400, bad)


if __name__ == "__main__":
    unittest.main()
