import unittest
from datetime import date

from desk.collect import add_text_item
from tests import test_server  # ServerTests 를 이름으로 가져오면 그 시험이 여기서 또 돔


class TodoApiTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def test_move_from_other_cards_and_back(self):
        db = self.env.db
        it = add_text_item(db, "cool", "운영위원회 11월 4일 15:30 교무실", title="운영위원회", received_at="2026-10-28 09:00:00")
        ev = db.one("SELECT id FROM events WHERE item_id = ?", (it["id"],))["id"]
        # 일정에서 옮겨 옴 (같은 일정은 한 번만)
        status, t1 = self.req("POST", "/api/todos", {"text": "운영위원회", "date": "2026-11-04", "time": "15:30",
                                                     "item_id": it["id"], "event_id": ev, "origin": "event"})
        self.assertEqual(status, 200, t1)
        self.assertEqual(self.req("POST", "/api/todos", {"text": "운영위원회", "event_id": ev, "origin": "event"})[1]["id"], t1["id"])
        # AI 요약의 '알아 둘 내용' 줄에서 옮겨 옴 → 원래 칸에서는 숨김, 지우면 되돌아감
        key = "note||교과서주문확인"
        status, t2 = self.req("POST", "/api/todos", {"text": "교과서 주문 확인", "origin": "ai", "hide_key": key})
        self.assertEqual(status, 200, t2)
        self.assertIn(key, db.get_meta("ai_hidden"))
        # 직접 쓰기
        t3 = self.req("POST", "/api/todos", {"text": "학부모 상담 일지 정리"})[1]
        self.assertEqual(t3["origin"], "manual")
        # 브리핑: 날짜 있는 것 먼저
        todos = self.req("GET", "/api/today")[1]["todos"]
        self.assertEqual([t["text"] for t in todos], ["운영위원회", "교과서 주문 확인", "학부모 상담 일지 정리"])
        # 끝냄 → 오늘은 아래에 줄 그어 보임
        self.assertEqual(self.req("PATCH", f"/api/todos/{t1['id']}", {"done": True})[1]["done"], 1)
        self.assertEqual([t["done"] for t in self.req("GET", "/api/todos")[1]], [0, 0, 1])
        self.assertEqual(self.req("DELETE", f"/api/todos/{t2['id']}")[0], 200)
        self.assertNotIn(key, db.get_meta("ai_hidden"))
        # 잘못된 값
        self.assertEqual(self.req("POST", "/api/todos", {"text": ""})[0], 400)
        self.assertEqual(self.req("POST", "/api/todos", {"text": "x", "date": "11/4"})[0], 400)
        self.assertEqual(self.req("POST", "/api/todos", {"text": "x", "origin": "evil"})[0], 400)
        self.assertEqual(self.req("PATCH", "/api/todos/9999", {"done": True})[0], 404)
        # 텔레그램 브리핑에도
        from desk.telegram import Bot
        text = Bot(db, self.env.config).brief_text(date.today())
        self.assertIn("■ 꼭 할 일 (직접 넣은 것 1)", text)
        self.assertIn("학부모 상담 일지 정리", text)


if __name__ == "__main__":
    unittest.main()
