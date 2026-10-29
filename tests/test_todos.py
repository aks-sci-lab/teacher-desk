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

    def test_order_by_drag(self):
        db = self.env.db
        db.execute("DELETE FROM todos")
        ids = [self.req("POST", "/api/todos", {"text": t, "date": d})[1]["id"]
               for t, d in (("가", "2026-11-09"), ("나", "2026-11-02"), ("다", None))]
        # 새 것은 맨 아래 (날짜와 상관없이 넣은 순서)
        self.assertEqual([t["text"] for t in self.req("GET", "/api/todos")[1]], ["가", "나", "다"])
        # 끌어서 '다' 를 맨 위로
        status, rows = self.req("PUT", "/api/todos/order", {"ids": [ids[2], ids[0], ids[1]]})
        self.assertEqual(status, 200, rows)
        self.assertEqual([t["text"] for t in rows], ["다", "가", "나"])
        # 일부만 보내면 나머지는 그 뒤에 원래 순서대로, 끝낸 것은 아래
        self.req("PATCH", f"/api/todos/{ids[2]}", {"done": True})
        rows = self.req("PUT", "/api/todos/order", {"ids": [ids[1]]})[1]
        self.assertEqual([t["text"] for t in rows], ["나", "가", "다"])
        new = self.req("POST", "/api/todos", {"text": "라"})[1]
        self.assertEqual([t["text"] for t in self.req("GET", "/api/todos")[1]], ["나", "가", "라", "다"])
        self.assertEqual(self.req("PUT", "/api/todos/order", {"ids": ["x"]})[0], 400)
        self.assertGreater(new["sort"], 0)

    def test_old_db_gets_date_order(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        from desk.db import DB
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            DB(path).close()
            conn = sqlite3.connect(path)   # 0.5.18 모양: sort 열이 없음
            conn.executescript("ALTER TABLE todos DROP COLUMN sort;"
                               "INSERT INTO todos(text, date, created_at) VALUES ('늦은 것', '2026-11-09', 'x'), ('빠른 것', '2026-11-02', 'x'), ('날짜 없음', NULL, 'x');")
            conn.commit()
            conn.close()
            db = DB(path)
            from desk import queries
            self.assertEqual([t["text"] for t in queries.todos(db)], ["빠른 것", "늦은 것", "날짜 없음"])
            db.close()


if __name__ == "__main__":
    unittest.main()
