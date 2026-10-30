"""주간 계획 API (0.5.48): 수업 있는 날만, 학사일정 반영, 읽기 전용."""
import unittest
from datetime import date

from desk import timetable as T
from tests import test_server
from tests.test_timetable import sample_xlsx


class WeekTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def event(self, day, text, kind="event", category="행사", end=None, source=None):
        status, e = self.req("POST", "/api/events", {"date": day, "end_date": end, "text": text, "kind": kind, "category": category})
        self.assertEqual(status, 200, e)
        return e

    def week(self, start, days=7):
        status, w = self.req("GET", f"/api/week?start={start}&days={days}")
        self.assertEqual(status, 200, w)
        return w

    def test_default_all_weekdays_when_no_school_calendar(self):
        w = self.week("2032-03-03")   # 수요일 → 그 주 월요일(03-01)부터
        self.assertEqual(w["start"], "2032-03-01")
        self.assertEqual([c["date"] for c in w["cols"]], [f"2032-03-0{n}" for n in range(1, 6)])

    def test_two_weeks_and_navigation(self):
        self.assertEqual(len(self.week("2032-03-01", 14)["cols"]), 10)
        self.assertEqual(self.week("2032-03-08")["start"], "2032-03-08")
        self.assertEqual(self.week("2032-02-29")["start"], "2032-02-23")   # 월 경계 (윤년)

    def test_events_deadlines_and_weekend(self):
        self.event("2032-05-04", "학부모 상담")
        self.event("2032-05-06", "과제 제출", kind="deadline", category="제출·마감", end="2032-05-07")
        self.event("2032-05-08", "토요 행사")
        w = self.week("2032-05-03")
        cols = {c["date"]: c for c in w["cols"]}
        self.assertEqual([e["text"] for e in cols["2032-05-04"]["events"]], ["학부모 상담"])
        self.assertEqual(cols["2032-05-06"]["events"], [])            # 마감은 끝나는 날에만
        self.assertEqual([e["text"] for e in cols["2032-05-07"]["events"]], ["과제 제출"])
        self.assertEqual([e["text"] for o in w["other"] for e in o["events"]], ["토요 행사"])   # 사라지지 않음

    def test_todos_dated_undated_overdue(self):
        self.req("POST", "/api/todos", {"text": "날짜 있음", "date": "2032-06-02"})
        self.req("POST", "/api/todos", {"text": "날짜 없음"})
        w = self.week("2032-06-01")
        cols = {c["date"]: c for c in w["cols"]}
        self.assertEqual([t["text"] for t in cols["2032-06-02"]["todos"]], ["날짜 있음"])
        self.assertIn("날짜 없음", [t["text"] for t in w["undated"]])
        self.assertEqual(w["overdue"], [])   # 이번 주를 보고 있지 않으면 지난 할 일 칸은 없음

    def test_school_calendar_off_and_maybe(self):
        self.event("2032-09-07", "개교기념일 휴업일", category="학사")
        self.event("2032-09-08", "전교 체육대회", category="학사")
        self.event("2032-09-09", "3학년 수학여행", category="학사")   # 학년 모르면 off 로 판정되는 행사
        w = self.week("2032-09-06")
        cols = {c["date"]: c for c in w["cols"]}
        self.assertNotIn("2032-09-07", cols)                     # 휴업일은 칸에서 빠짐
        self.assertIn("개교기념일", [o["reason"] for o in w["other"] if o["date"] == "2032-09-07"][0])
        self.assertIn("전교 체육대회", cols["2032-09-08"]["check"])  # maybe 는 '확인 필요'로 남김
        self.assertEqual(cols["2032-09-08"]["school"], ["전교 체육대회"])

    def test_grade_event_hides_only_my_grade(self):
        self.env.db.set_meta("timetable", T.parse_file(sample_xlsx())[0])
        self.env.db.set_meta("timetable_me", 0)   # 김국어: 2학년만
        self.event("2032-10-12", "3학년 현장체험학습", category="학사")
        self.event("2032-10-13", "2학년 현장체험학습", category="학사")
        cols = {c["date"] for c in self.week("2032-10-11")["cols"]}
        self.assertIn("2032-10-12", cols)
        self.assertNotIn("2032-10-13", cols)
        self.env.db.set_meta("timetable", None)
        self.env.db.set_meta("timetable_me", None)

    def test_student_records_not_included(self):
        self.req("POST", "/api/passes", {"kind": "early", "student_name": "홍길동", "date": "2032-11-02", "time_out": "13:00"})
        self.assertNotIn("홍길동", str(self.week("2032-11-01", 14)))

    def test_bad_input_400(self):
        for q in ("start=2032-02-30", "start=abc", "start=2032-03-01&days=5", "start=2032-03-01&days=x"):
            self.assertEqual(self.req("GET", f"/api/week?{q}")[0], 400, q)


if __name__ == "__main__":
    unittest.main()
