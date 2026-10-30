import unittest
from datetime import date, timedelta

from desk import queries
from desk.collect import add_text_item
from tests import test_server  # ServerTests 를 이름으로 가져오면 그 시험이 여기서 또 돔


class BriefingReadTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def add_event(self, text, start, end=None, kind="deadline", done=0):
        db = self.env.db
        item = add_text_item(db, "paste", f"{text} 시험", title=text)["id"]   # 직접 입력은 바로 등록됨
        db.execute("DELETE FROM events WHERE item_id = ?", (item,))
        db.execute("INSERT INTO events(item_id, date, end_date, kind, category, text, done) VALUES(?,?,?,?,?,?,?)",
                   (item, start.isoformat(), end.isoformat() if end else None, kind, "제출·마감", text, done))
        return item

    def test_due_today_uses_end_date_and_is_not_repeated(self):
        today = date.today()
        self.add_event("오늘 끝나는 신청", today - timedelta(days=5), today)       # 기간 마감: 끝나는 날이 오늘
        self.add_event("오늘 하루 마감", today)
        self.add_event("아직 진행 중인 신청", today - timedelta(days=2), today + timedelta(days=4))
        self.add_event("끝낸 마감", today, done=1)
        self.add_event("오늘 행사", today, kind="event")
        s = queries.today_summary(self.env.db, today)
        self.assertEqual({e["text"] for e in s["due_today"]}, {"오늘 끝나는 신청", "오늘 하루 마감"})
        self.assertEqual({e["text"] for e in s["today"]} & {"오늘 끝나는 신청", "오늘 하루 마감"}, set())   # 중복 없음
        self.assertIn("오늘 행사", {e["text"] for e in s["today"]})
        self.assertEqual([e["text"] for e in s["week_deadlines"]], ["아직 진행 중인 신청"])   # 끝 날짜가 7일 안
        self.assertNotIn("아직 진행 중인 신청", {e["text"] for e in s["overdue"]})
        self.assertNotIn("끝낸 마감", {e["text"] for e in s["due_today"]})
        # 어제 끝난 마감은 '지난 마감'
        self.add_event("어제 끝난 마감", today - timedelta(days=3), today - timedelta(days=1))
        self.assertIn("어제 끝난 마감", {e["text"] for e in queries.today_summary(self.env.db, today)["overdue"]})

    def test_mark_read_only_listed_unread_items(self):
        db = self.env.db
        a = add_text_item(db, "cool", "읽음 시험 하나 쪽지입니다 안내", title="하나")["id"]
        b = add_text_item(db, "cool", "읽음 시험 둘 쪽지입니다 안내", title="둘")["id"]
        late = add_text_item(db, "cool", "그 사이 새로 온 쪽지입니다 안내", title="나중")["id"]
        db.execute("UPDATE items SET registered = 0 WHERE id IN (?, ?, ?)", (a, b, late))
        db.execute("UPDATE items SET status = 'done' WHERE id = ?", (b,))
        status, r = self.req("POST", "/api/items/mark-read", {"ids": [a, b]})
        self.assertEqual((status, r["marked"]), (200, 1))                   # 안 읽은 것(a)만
        state = {i["id"]: i["status"] for i in db.query("SELECT id, status FROM items WHERE id IN (?, ?, ?)", (a, b, late))}
        self.assertEqual(state, {a: "read", b: "done", late: "new"})        # 완료·새로 도착한 것은 그대로
        self.assertEqual(db.one("SELECT registered FROM items WHERE id = ?", (a,))["registered"], 0)   # 일정 등록은 바뀌지 않음
        self.assertEqual(self.req("POST", "/api/items/mark-read", {"ids": []})[0], 400)
        self.assertEqual(self.req("POST", "/api/items/mark-read", {"ids": ["1"]})[0], 400)
        self.assertEqual(self.req("POST", "/api/items/mark-read", {"ids": [a]}, token=False)[0], 401)


if __name__ == "__main__":
    unittest.main()
