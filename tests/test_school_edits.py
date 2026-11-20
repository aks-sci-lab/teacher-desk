"""학사일정 고치기·기록 (0.6.18): 고친 것은 기록에 남고, 다시 가져와도 고친 대로 유지."""
import unittest

from tests import test_server


class SchoolEditTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    EVENTS = [{"date": "2033-11-17", "text": "대학수학능력시험", "category": "학사"},
              {"date": "2033-11-21", "end_date": "2033-11-22", "text": "현장체험학습", "category": "행사"},
              {"date": "2033-11-23", "text": "학부모 공개수업", "category": "학사"}]

    def save(self):
        status, res = self.req("POST", "/api/schoolcal/save", {"school_year": 2033, "events": self.EVENTS, "origin": "시험"})
        self.assertEqual(status, 200, res)

    def week(self):
        status, w = self.req("GET", "/api/week?start=2033-11-21&days=7")
        self.assertEqual(status, 200, w)
        return {c["date"]: c for c in w["cols"] + w["other"]}

    def test_edit_delete_logged_and_kept_after_reimport(self):
        self.save()
        cols = self.week()
        trip = cols["2033-11-21"]["school_ev"][0]
        self.assertEqual(trip["text"], "현장체험학습")
        self.assertTrue(trip["school"])
        self.assertFalse(trip["edited"])
        open_class = cols["2033-11-23"]["school_ev"][0]

        status, e = self.req("PATCH", f"/api/events/{trip['id']}",
                             {"date": "2033-11-24", "end_date": "2033-11-25", "text": "현장체험학습(변경)", "note": "날짜 바뀜"})
        self.assertEqual(status, 200, e)
        status, r = self.req("DELETE", f"/api/events/{open_class['id']}?note=%EC%B7%A8%EC%86%8C")
        self.assertEqual(status, 200, r)

        status, log = self.req("GET", "/api/schoolcal/edits")
        self.assertEqual(status, 200, log)
        self.assertEqual([x["action"] for x in log[:2]], ["delete", "edit"])
        self.assertEqual(log[1]["before"]["date"], "2033-11-21")
        self.assertEqual(log[1]["after"]["text"], "현장체험학습(변경)")
        self.assertEqual(log[1]["note"], "날짜 바뀜")
        self.assertIsNone(log[0]["after"])

        self.save()   # 나이스·파일에서 다시 가져와도
        cols = self.week()
        self.assertEqual(cols["2033-11-21"]["school_ev"], [])
        moved = cols["2033-11-24"]["school_ev"]
        self.assertEqual([x["text"] for x in moved], ["현장체험학습(변경)"])
        self.assertTrue(moved[0]["edited"])
        self.assertEqual(cols["2033-11-23"]["school_ev"], [])   # 지운 것은 다시 생기지 않음

        # 한 번 더 고쳐도 원래 줄 하나로 이어짐 (다시 가져올 때 두 줄이 되지 않게)
        status, e = self.req("PATCH", f"/api/events/{moved[0]['id']}", {"text": "현장체험학습(최종)"})
        self.assertEqual(status, 200, e)
        self.save()
        texts = [x["text"] for c in self.week().values() for x in c["school_ev"]]
        self.assertEqual(texts.count("현장체험학습(최종)"), 2)   # 24·25일 이틀 칸
        self.assertNotIn("현장체험학습", texts)

    def test_manual_event_edit_not_logged(self):
        status, e = self.req("POST", "/api/events", {"date": "2033-12-05", "text": "내 일정", "kind": "event", "category": "참고"})
        self.assertEqual(status, 200, e)
        _, before = self.req("GET", "/api/schoolcal/edits")
        self.req("PATCH", f"/api/events/{e['id']}", {"text": "내 일정 2"})
        _, after = self.req("GET", "/api/schoolcal/edits")
        self.assertEqual(len(before), len(after))


if __name__ == "__main__":
    unittest.main()
