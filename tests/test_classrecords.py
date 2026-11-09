"""학급 제출물 확인·상담 기록 (0.5.82): 제출·면제·미제출·늦게 냄, 상담 저장·고치기·학생별 모아 보기, 검색·로그에 안 나옴."""
from tests.test_classroom_api import ApiBase


class TaskTests(ApiBase):
    def test_marks_missing_and_late(self):
        st, t = self.req("POST", "/api/class/tasks", {"title": "현장체험 동의서", "due": "2026-01-02"})
        self.assertEqual(st, 200, t)
        self.assertEqual(len(t["missing"]), 12)
        st, t = self.req("POST", f"/api/class/tasks/{t['id']}/marks", {"numbers": [1, 2, 3], "status": "done"})
        self.assertEqual((t["done"], len(t["missing"])), (3, 9))
        self.assertEqual(t["late"], [1, 2, 3])                  # 마감 뒤에 냄
        st, t = self.req("POST", f"/api/class/tasks/{t['id']}/marks", {"numbers": [4], "status": "exempt"})
        st, t = self.req("POST", f"/api/class/tasks/{t['id']}/marks", {"numbers": [2], "status": None})
        self.assertEqual((t["done"], t["exempt"]), (2, 1))
        self.assertIn(2, t["missing"])
        self.assertEqual(self.req("POST", f"/api/class/tasks/{t['id']}/marks", {"numbers": [99], "status": "done"})[0], 400)
        self.assertEqual(self.req("POST", f"/api/class/tasks/{t['id']}/marks", {"numbers": [1], "status": "x"})[0], 400)
        st, s = self.req("GET", "/api/class/tasks")
        self.assertEqual(s["marks"][str(t["id"])]["1"]["status"], "done")

    def test_close_and_list(self):
        st, t = self.req("POST", "/api/class/tasks", {"title": "가정통신문 회신"})
        self.req("PATCH", f"/api/class/tasks/{t['id']}", {"closed": True})
        st, s = self.req("GET", "/api/class/tasks")
        self.assertEqual((s["tasks"], s["closed_count"]), ([], 1))
        st, s = self.req("GET", "/api/class/tasks?all=1")
        self.assertEqual(len(s["tasks"]), 1)
        self.assertEqual(self.req("POST", "/api/class/tasks", {"title": ""})[0], 400)
        self.assertEqual(self.req("DELETE", f"/api/class/tasks/{t['id']}")[0], 200)


class CounselTests(ApiBase):
    def test_counsel_crud_overview_and_privacy(self):
        body = {"number": 3, "date": "2026-11-09", "time": "15:30", "method": "전화", "with_whom": "학부모",
                "topic": "교우 관계", "content": "비밀상담내용", "followup": "다음 주 다시 통화", "followup_date": "2026-11-16"}
        st, c = self.req("POST", "/api/class/counsel", body)
        self.assertEqual(st, 200, c)
        self.assertEqual(c["student_name"], "가짜3")
        st, c2 = self.req("PATCH", f"/api/class/counsel/{c['id']}", {"followup_done": True})
        self.assertEqual(c2["followup_done"], 1)
        st, lst = self.req("GET", "/api/class/counsel")
        self.assertEqual(lst["counts"], {"3": 1})
        self.assertEqual(lst["pending"], [])
        st, t = self.req("POST", "/api/class/tasks", {"title": "서류"})
        st, ov = self.req("GET", "/api/class/student/3")
        self.assertEqual((ov["name"], len(ov["records"]), [x["title"] for x in ov["missing_tasks"]]), ("가짜3", 1, ["서류"]))
        # 검색·로그·브리핑에 상담 내용이 나오지 않음
        st, found = self.req("GET", "/api/search?q=%EB%B9%84%EB%B0%80%EC%83%81%EB%8B%B4%EB%82%B4%EC%9A%A9")
        self.assertNotIn("비밀상담내용", str(found))
        self.assertNotIn("가짜3", str(self.env.db.query("SELECT message FROM logs")))
        st, today = self.req("GET", "/api/today")
        self.assertNotIn("비밀상담내용", str(today))
        # 검사
        for bad in ({**body, "number": 99}, {**body, "method": "편지"}, {**body, "topic": ""}, {**body, "time": "3시"}):
            self.assertEqual(self.req("POST", "/api/class/counsel", bad)[0], 400, bad)
        self.assertEqual(self.req("PATCH", f"/api/class/counsel/{c['id']}", {"topic": ""})[0], 400)
        self.assertEqual(self.req("DELETE", f"/api/class/counsel/{c['id']}")[0], 200)
        self.assertEqual(self.req("GET", "/api/class/counsel")[1]["records"], [])
