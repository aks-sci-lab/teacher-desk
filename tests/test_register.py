"""출석부 ↔ 결석계 연동, 지각·조퇴·결과 (0.5.95, 가짜 자료).

2099-03-02(월)~03-06(금), 03-09(월)~ 로 주말 건너뛰기를 시험한다.
"""
from desk import register
from tests.test_classroom_api import ApiBase

TYPES = ("질병", "출석인정", "기타", "미인정", "미정")


class RegisterTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.env.db.replace_students([{"number": 1, "name": "가짜하나"}, {"number": 2, "name": "가짜둘"}])

    def set(self, day, kind, number=1, name="가짜하나", **extra):
        return self.req("POST", "/api/attendance/register",
                        {"student_number": number, "student_name": name, "date": day, "kind": kind, **extra})

    def absences(self, number=1):
        return self.env.db.query("SELECT * FROM absences WHERE student_number = ? ORDER BY start_date", (number,))

    def stats(self, month="2099-03"):
        st, report = self.req("GET", f"/api/attendance/month?month={month}")
        self.assertEqual(st, 200, report)
        for row in report["rows"]:            # 표 숫자 = 숫자를 누르면 나오는 목록 건수
            for metric in ("absence_forms", "early_count", "out_count", "late_count", "result_count"):
                self.assertEqual(row[metric], len(row["details"][metric]), (row["student_name"], metric))
            for kind in TYPES:
                self.assertEqual(row["absence_days"][kind],
                                 sum(d["kind"] == kind for d in row["details"]["absence_days"]))
        for metric in ("absence_forms", "early_count", "out_count", "late_count", "result_count"):
            self.assertEqual(report["totals"][metric], sum(r[metric] for r in report["rows"]))
        return report

    def test_absent_checks_make_one_draft_across_weekend(self):
        for day in ("2099-03-05", "2099-03-06", "2099-03-09"):        # 목·금·(주말)·월
            self.assertEqual(self.set(day, "absent")[0], 200)
        rows = self.absences()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["start_date"], rows[0]["end_date"], rows[0]["status"], rows[0]["absence_type"]),
                         ("2099-03-05", "2099-03-09", "draft", "미정"))
        st, err = self.set("2099-03-07", "absent")                     # 토요일
        self.assertEqual(st, 400)
        # 가운데 날(금)을 풀면 둘로 나뉨, 끝날을 풀면 줄어듦, 다 풀면 초안이 없어짐
        self.assertEqual(self.set("2099-03-06", "clear")[0], 200)
        self.assertEqual([(r["start_date"], r["end_date"]) for r in self.absences()],
                         [("2099-03-05", "2099-03-05"), ("2099-03-09", "2099-03-09")])
        self.set("2099-03-05", "clear")
        self.set("2099-03-09", "clear")
        self.assertEqual(self.absences(), [])

    def test_fill_in_bridges_two_drafts_and_type(self):
        self.set("2099-03-02", "absent", type="질병")
        self.set("2099-03-04", "absent")
        self.set("2099-03-03", "absent")                               # 사이를 채우면 하나로
        rows = self.absences()
        self.assertEqual([(r["start_date"], r["end_date"], r["absence_type"]) for r in rows],
                         [("2099-03-02", "2099-03-04", "질병")])
        report = self.stats()
        one = next(r for r in report["rows"] if r["student_number"] == 1)
        self.assertEqual((one["absence_forms"], one["absence_days"]["질병"]), (1, 3))

    def test_absence_form_shows_on_register_and_delete_clears(self):
        st, a = self.req("POST", "/api/absences", {"student_name": "가짜둘", "student_number": 2,
                                                   "start_date": "2099-03-06", "end_date": "2099-03-09",
                                                   "absence_type": "출석인정", "reason": "가짜 사유"})
        self.assertEqual(st, 200, a)
        st, reg = self.req("GET", "/api/attendance/register?month=2099-03")
        cells = reg["cells"]["2|가짜둘"]
        self.assertEqual(sorted(cells), ["2099-03-06", "2099-03-09"])  # 주말은 칸이 없음
        self.assertEqual(cells["2099-03-06"][0]["type"], "출석인정")
        self.assertFalse(cells["2099-03-06"][0]["draft"])
        # 작성 완료 결석계는 출석부에서 풀 때 확인을 받음
        st, err = self.set("2099-03-06", "clear", number=2, name="가짜둘")
        self.assertEqual(st, 409)
        self.assertEqual(len(self.absences(2)), 1)
        self.assertEqual(self.set("2099-03-06", "clear", number=2, name="가짜둘", force=True)[0], 200)
        self.assertEqual([(r["start_date"], r["end_date"]) for r in self.absences(2)], [("2099-03-09", "2099-03-09")])
        # 인쇄한 초안도 보호 / 결석계를 지우면 출석부 결석도 없어짐
        self.req("DELETE", f"/api/absences/{a['id']}")
        st, reg = self.req("GET", "/api/attendance/register?month=2099-03")
        self.assertNotIn("2|가짜둘", reg["cells"])

    def test_printed_or_done_draft_not_merged_or_cleared(self):
        self.set("2099-03-02", "absent")
        draft = self.absences()[0]
        self.req("POST", f"/api/absences/{draft['id']}/printed", {})
        self.assertEqual(self.set("2099-03-02", "clear")[0], 409)
        self.set("2099-03-03", "absent")                               # 인쇄한 것은 늘리지 않고 새 초안
        self.assertEqual(len(self.absences()), 2)
        # 초안을 양식으로 채워 저장하면 작성 완료
        st, saved = self.req("PATCH", f"/api/absences/{self.absences()[1]['id']}",
                             {"absence_type": "질병", "reason": "가짜 사유"})
        self.assertEqual(self.env.db.one("SELECT status FROM absences WHERE id = ?", (saved["id"],))["status"], "done")

    def test_late_early_result_and_stats(self):
        self.assertEqual(self.set("2099-03-02", "late", type="질병", note="병원")[0], 200)
        self.assertEqual(self.set("2099-03-03", "result", type="미인정", periods="3,4")[0], 200)
        self.assertEqual(self.set("2099-03-04", "early", type="기타")[0], 200)   # 증 없음
        st, p = self.req("POST", "/api/passes", {"kind": "early", "student_name": "가짜하나", "student_number": 1,
                                                 "date": "2099-03-05", "time_out": "13:00"})
        self.set("2099-03-05", "early", type="질병")                   # 증이 있는 날의 체크는 두 번 세지 않음
        st, reg = self.req("GET", "/api/attendance/register?month=2099-03")
        cells = reg["cells"]["1|가짜하나"]
        self.assertTrue(next(e for e in cells["2099-03-04"] if e["kind"] == "early")["no_pass"])
        self.assertEqual({e["src"] for e in cells["2099-03-05"]}, {"pass", "mark"})
        report = self.stats()
        one = next(r for r in report["rows"] if r["student_number"] == 1)
        self.assertEqual((one["late_count"], one["result_count"], one["early_count"]), (1, 1, 2))
        self.assertIn("3,4교시", one["details"]["result_count"][0]["time"])
        # 결석으로 바꾸면 그 날의 지각 기록은 지워짐 (결석이 우선)
        self.set("2099-03-02", "absent")
        self.assertEqual(self.stats()["totals"]["late_count"], 0)
        # 결석인 날에 지각을 체크하면 결석을 풂 (초안이므로 확인 없이)
        self.set("2099-03-02", "late")
        self.assertEqual(self.absences(), [])
        # 초안 결석은 '미정' 칸으로 셈
        self.set("2099-03-10", "absent")
        self.assertEqual(self.stats()["totals"]["absence_days"]["미정"], 1)

    def test_state_signal_changes(self):
        sig = lambda: self.req("GET", "/api/state")[1]["counts"]["passes"]
        before = sig()
        self.set("2099-03-02", "late")
        self.assertNotEqual(sig(), before)

    def test_unit_neighbor(self):
        from datetime import date
        self.assertEqual(register._neighbor_school_day(self.env.db, date(2099, 3, 9), -1), date(2099, 3, 6))
