"""상담 기록 학년도 나누기 (0.5.90, 선생님 결정 2026-11-10: 학년도가 바뀌면 보관만, 이관 없음).
학년도는 3월 1일 시작. 새 학년도 상담 화면은 빈 상태, 지난 학년도는 보관함에서 읽기·인쇄만(고치기·지우기·후속 알림 없음)."""
from datetime import date

from desk import classrecords
from tests.test_classroom_api import ApiBase


def body(**kw):
    return {"number": 3, "method": "전화", "with_whom": "학부모", "topic": "교우 관계", "content": "가짜 상담 내용",
            "followup": "다시 통화", **kw}


class SchoolYearTests(ApiBase):
    def at(self, d):
        self.TODAY = d

    def test_boundary(self):
        self.assertEqual(classrecords.school_year("2026-02-28"), 2025)
        self.assertEqual(classrecords.school_year("2026-03-01"), 2026)
        self.assertEqual(classrecords.school_year(date(2027, 1, 5)), 2026)
        self.assertEqual(classrecords.year_range(2026), ("2026-03-01", "2027-03-01"))

    def test_new_year_starts_empty_and_old_is_read_only(self):
        self.at(date(2027, 2, 28))   # 2026학년도 마지막 날
        st, a = self.req("POST", "/api/class/counsel", body(date="2027-02-28"))
        self.assertEqual(st, 200, a)
        st, lst = self.req("GET", "/api/class/counsel")
        self.assertEqual((lst["year"], len(lst["records"]), len(lst["pending"])), (2026, 1, 1))

        self.at(date(2027, 3, 1))    # 새 학년도 첫날
        st, lst = self.req("GET", "/api/class/counsel")
        self.assertEqual((lst["year"], lst["records"], lst["pending"], lst["counts"]), (2027, [], [], {}))   # 빈 상태
        self.assertFalse(lst["readonly"])
        self.assertEqual(lst["years"], [2027, 2026])
        st, old = self.req("GET", "/api/class/counsel?year=2026")      # 보관함
        self.assertTrue(old["readonly"])
        self.assertEqual(len(old["records"]), 1)
        self.assertEqual(old["pending"], [])                           # 지난 학년도 후속 조치는 알리지 않음
        self.assertEqual(old["students"], [{"number": 3, "name": "가짜3"}])   # 그때 적힌 이름 (새 명단과 잇지 않음)
        # 읽기 전용: 고치기·지우기·지난 학년도 날짜로 새로 쓰기 막힘
        self.assertEqual(self.req("PATCH", f"/api/class/counsel/{a['id']}", {"topic": "고침"})[0], 400)
        self.assertEqual(self.req("DELETE", f"/api/class/counsel/{a['id']}")[0], 400)
        st, r = self.req("POST", "/api/class/counsel", body(date="2027-02-28"))
        self.assertEqual(st, 400)
        self.assertIn("보관함", r["error"])
        self.assertEqual(self.req("POST", "/api/class/counsel", body(date="2027-03-01"))[0], 200)
        # 학생별 모아 보기도 학년도별
        st, s_now = self.req("GET", "/api/class/student/3")
        st, s_old = self.req("GET", "/api/class/student/3?year=2026")
        self.assertEqual((len(s_now["records"]), s_now["readonly"]), (1, False))
        self.assertEqual((len(s_old["records"]), s_old["readonly"], s_old["missing_tasks"]), (1, True, []))
        self.assertEqual(self.req("GET", "/api/class/counsel?year=abc")[0], 400)

    def test_new_roster_not_linked(self):
        self.at(date(2027, 2, 1))
        self.req("POST", "/api/class/counsel", body(date="2027-02-01"))
        self.at(date(2027, 3, 2))
        self.env.db.replace_students([{"number": 3, "name": "새학생3"}])   # 새 학년도 명단 (같은 번호, 다른 학생)
        st, now = self.req("GET", "/api/class/counsel?number=3")
        self.assertEqual(now["records"], [])                           # 같은 번호라도 지난 기록이 따라오지 않음
        st, old = self.req("GET", "/api/class/counsel?year=2026")
        self.assertEqual(old["records"][0]["student_name"], "가짜3")
        self.assertEqual(old["students"][0]["name"], "가짜3")

    def test_archive_not_in_search_or_today(self):
        self.at(date(2027, 2, 1))
        self.req("POST", "/api/class/counsel", body(date="2027-02-01", content="보관비밀내용"))
        self.at(date(2027, 3, 2))
        st, found = self.req("GET", "/api/search?q=%EB%B3%B4%EA%B4%80%EB%B9%84%EB%B0%80%EB%82%B4%EC%9A%A9")
        self.assertNotIn("보관비밀내용", str(found))
        st, today = self.req("GET", "/api/today")
        self.assertNotIn("보관비밀내용", str(today))

    def test_call_drafts_only_this_school_year(self):
        from desk import calllog
        self.env.db.replace_students([{"number": 3, "name": "가짜3", "parent_phone": "010-0000-0003"}])
        self.at(date(2027, 3, 3))
        entries = [{"kind": "call", "phone": "010-0000-0003", "when": calllog.parse_when("2027-02-27 10:00"), "dir": "in", "duration": 60},
                   {"kind": "call", "phone": "010-0000-0003", "when": calllog.parse_when("2027-03-02 10:00"), "dir": "in", "duration": 60}]
        drafts, stats = calllog.make_drafts(self.env.db, entries, since=date(2027, 1, 1))
        self.assertEqual([d["date"] for d in drafts], ["2027-03-02"])  # 지난 학년도 통화는 초안으로 안 만듦
        self.assertEqual(stats["old"], 1)
