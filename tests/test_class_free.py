"""학급 시간표와 같이 비는 시간 (0.5.64): 순수 함수."""
import unittest
from datetime import date

from desk import timetable as T
from tests import test_server
from tests.test_timetable import sample_xlsx

MON = date(2026, 11, 2)   # 월요일


class ClassTests(unittest.TestCase):
    def setUp(self):
        self.tt, _ = T.parse_file(sample_xlsx())

    def test_class_list_sorted(self):
        self.assertEqual(T.class_list(self.tt), ["2-1", "2-2", "2-3"])

    def test_find_class_many_spellings(self):
        for q in ("2-3", "2학년 3반", "203", "2 3", "2-03", " 2학년3반 ", "2-3반"):
            self.assertEqual(T.find_class(self.tt, q), "2-3", q)
        for q in ("3-1", "", "abc", "9", "2학년", None):
            self.assertIsNone(T.find_class(self.tt, q), q)

    def test_class_lessons_on_sorted_with_teacher(self):
        ls = T.class_lessons_on(self.tt, "2-1", MON)   # 월: 1교시 김국어, 3교시 박영어
        self.assertEqual([(l["period"], l["subj"], l["teacher"]) for l in ls], [(1, "국어", "김국어"), (3, "영어", "박영어")])
        self.assertEqual(set(ls[0]), {"period", "subj", "tid", "teacher", "moved", "locked"})
        self.assertEqual(T.class_lessons_on(self.tt, "2-1", date(2026, 11, 7)), [])   # 토요일
        fri = T.class_lessons_on(self.tt, "2-1", date(2026, 11, 6))
        self.assertTrue(fri[0]["locked"])   # 자율

    def test_class_week_keys_and_swap(self):
        week = T.class_week(self.tt, "2-1", MON)
        self.assertEqual(list(week), ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"])
        cand = T.find_candidates(self.tt, 0, "0-1", MON, today=date(2026, 10, 28))[0]
        swaps = [{"moves": cand["moves"]}]
        moved = T.class_week(self.tt, "2-1", MON, swaps)
        self.assertTrue(any(l["moved"] for ls in moved.values() for l in ls))
        self.assertNotEqual(week, moved)
        # 다음 주에는 교체가 없음
        self.assertEqual(T.class_week(self.tt, "2-1", date(2026, 11, 9), swaps), T.class_week(self.tt, "2-1", date(2026, 11, 9)))


class FreeTests(unittest.TestCase):
    def setUp(self):
        self.tt, _ = T.parse_file(sample_xlsx())

    def cell(self, rows, day, p):
        return next(r for r in rows if r["day"] == day and r["period"] == p)

    def test_free_and_busy(self):
        rows = T.common_free(self.tt, [0, 1], MON)   # 김국어·박영어
        self.assertEqual(len(rows), 15)
        c = self.cell(rows, "월", 1)
        self.assertEqual((c["busy"], c["free"], c["date"]), ([0], [1], "2026-11-02"))
        self.assertEqual(self.cell(rows, "월", 2)["free"], [1])   # 김국어 2-2
        self.assertEqual(self.cell(rows, "월", 3)["busy"], [1])
        self.assertEqual(self.cell(rows, "수", 3)["free"], [0, 1])   # 둘 다 빔
        self.assertIsNone(c["note"])

    def test_duplicate_tids_and_order(self):
        rows = T.common_free(self.tt, [1, 1, 0], MON)
        self.assertEqual(self.cell(rows, "수", 3)["free"], [1, 0])
        self.assertEqual([(r["day"], r["period"]) for r in rows][:4], [("월", 1), ("월", 2), ("월", 3), ("화", 1)])

    def test_swap_applied(self):
        cand = T.find_candidates(self.tt, 0, "0-1", MON, today=date(2026, 10, 28))[0]
        moves = cand["moves"]
        swaps = [{"moves": moves}]
        before = T.common_free(self.tt, [0], MON)
        after = T.common_free(self.tt, [0], MON, swaps)
        self.assertNotEqual(before, after)
        self.assertEqual(T.common_free(self.tt, [0], date(2026, 11, 9), swaps), T.common_free(self.tt, [0], date(2026, 11, 9)))

    def test_day_notes_all_grades_only(self):
        ev = {"2026-11-03": ["개교기념일 휴업일"], "2026-11-04": ["2학년 현장체험학습"], "2026-11-05": ["전교 체육대회"]}
        rows = T.common_free(self.tt, [0], MON, day_events=ev)
        self.assertEqual(self.cell(rows, "화", 1)["note"]["level"], "off")    # 전 학년 휴업
        self.assertEqual(self.cell(rows, "수", 1)["note"]["level"], "maybe")  # 2학년만: 모든 학년 off 아님
        self.assertEqual(self.cell(rows, "목", 1)["note"]["level"], "maybe")
        self.assertIsNone(self.cell(rows, "월", 1)["note"])


class ApiTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def setUp(self):
        db = self.env.db
        db.set_meta("timetable", T.parse_file(sample_xlsx())[0])
        db.set_meta("timetable_me", 0)
        db.set_meta("timetable_swaps", [])
        db.set_meta("timetable_periods", {"1": {"start": "09:00", "end": "09:45"}})

    def test_class_api(self):
        s, r = self.req("GET", "/api/timetable/class?cls=2%ED%95%99%EB%85%84%201%EB%B0%98&week=2026-11-04")
        self.assertEqual(s, 200, r)
        self.assertEqual((r["cls"], r["monday"], r["classes"]), ("2-1", "2026-11-02", ["2-1", "2-2", "2-3"]))
        self.assertEqual([(l["period"], l["teacher"]) for l in r["days"][0]["lessons"]], [(1, "김국어"), (3, "박영어")])
        self.assertEqual(self.req("GET", "/api/timetable/class?cls=9-9")[0], 404)
        self.assertEqual(self.req("GET", "/api/timetable/class?week=2026-02-30")[0], 400)

    def test_class_default_is_homeroom(self):
        self.env.config.values["grade"], self.env.config.values["class_no"] = "2", "3"
        try:
            self.assertEqual(self.req("GET", "/api/timetable/class?week=2026-11-02")[1]["cls"], "2-3")
            self.env.config.values["class_no"] = "9"   # 시간표에 없으면 첫 학급
            self.assertEqual(self.req("GET", "/api/timetable/class?week=2026-11-02")[1]["cls"], "2-1")
        finally:
            self.env.config.values["grade"] = self.env.config.values["class_no"] = ""

    def test_free_api(self):
        s, r = self.req("GET", "/api/timetable/free?tids=0,1&week=2026-11-02")
        self.assertEqual(s, 200, r)
        self.assertEqual([t["name"] for t in r["teachers"]], ["김국어", "박영어"])
        c = r["cells"][0]
        self.assertEqual((c["day"], c["period"], c["busy"], c["free"], c["start"], c["end"]), ("월", 1, [0], [1], "09:00", "09:45"))
        self.assertEqual(self.req("GET", "/api/timetable/free?week=2026-11-02")[1]["teachers"][0]["id"], 0)   # 기본은 '나'
        for q in ("tids=x", "tids=0,", "tids=99"):
            self.assertIn(self.req("GET", f"/api/timetable/free?{q}")[0], (400, 404), q)

    def test_no_student_info(self):
        self.assertNotIn("student", str(self.req("GET", "/api/timetable/free?tids=0")[1]))


if __name__ == "__main__":
    unittest.main()
