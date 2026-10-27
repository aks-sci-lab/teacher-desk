import http.client
import json
import threading
import unittest
from datetime import date
from urllib.parse import quote

from desk.server import App, make_handler, parse_roster
from desk.telegram import Bot
from http.server import ThreadingHTTPServer
from tests.test_collect import Env


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Env()
        bot = Bot(cls.env.db, cls.env.config, cls.env.collector)
        cls.app = App(cls.env.db, cls.env.config, cls.env.collector, bot)
        # 빈 포트를 먼저 잡은 뒤 그 포트로 Host 검사 설정
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
        cls.port = cls.httpd.server_address[1]
        cls.env.config.values["port"] = cls.port
        cls.httpd.RequestHandlerClass = make_handler(cls.app)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.env.close()

    def req(self, method, path, body=None, token=True, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}"}
        if token:
            h["X-Desk-Token"] = self.app.token
        if body is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data

    # ---- 보안 ----
    def test_token_host_origin(self):
        self.assertEqual(self.req("GET", "/api/state", token=False)[0], 401)
        self.assertEqual(self.req("GET", "/api/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.req("POST", "/api/collect", {}, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.req("GET", "/api/state")[0], 200)

    def test_static_files_and_traversal(self):
        status, body = self.req("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.app.token.encode(), body)
        self.assertEqual(self.req("GET", "/js/main.js", token=False)[0], 200)
        self.assertEqual(self.req("GET", "/pass/pass-codec.js", token=False)[0], 200)
        self.assertEqual(self.req("GET", "/../desk.py", token=False)[0], 404)
        self.assertEqual(self.req("GET", "/pass/../teacher-desk/config.json", token=False)[0], 404)
        self.assertEqual(self.req("GET", "/%2e%2e/desk.py", token=False)[0], 404)

    def test_state_hides_secrets(self):
        self.env.config.values["telegram_token"] = "SECRET:123"
        try:
            status, s = self.req("GET", "/api/state")
            self.assertNotIn("SECRET", json.dumps(s))
            self.assertTrue(s["settings"]["telegram_token_set"])
        finally:
            self.env.config.values["telegram_token"] = ""

    # ---- 조퇴증·외출증 ----
    def test_pass_flow(self):
        today = date.today().isoformat()
        st, p1 = self.req("POST", "/api/passes", {"kind": "early", "student_name": "김가온", "student_number": 1,
                                                    "date": today, "time_out": "13:20", "reason": "병원"})
        self.assertEqual(st, 200, p1)
        st, p2 = self.req("POST", "/api/passes", {"kind": "out", "student_name": "나하늘", "date": today,
                                                    "time_out": "12:00", "time_return": "13:00"})
        self.assertEqual(p2["serial"][-2:], str(int(p1["serial"][-2:]) + 1).zfill(2))
        st, p2 = self.req("PATCH", f"/api/passes/{p2['id']}", {"status": "returned"})
        self.assertEqual(p2["status"], "returned")
        self.assertTrue(p2["returned_at"])
        self.assertEqual(self.req("POST", "/api/passes", {"kind": "x", "student_name": "a", "date": today})[0], 400)
        self.assertEqual(self.req("POST", "/api/passes", {"kind": "early", "student_name": "", "date": today})[0], 400)
        self.assertEqual(self.req("POST", "/api/passes", {"kind": "early", "student_name": "a", "date": "2026-02-30"})[0], 400)
        self.assertEqual(self.req("POST", "/api/passes", {"kind": "early", "student_name": "a", "date": today,
                                                           "time_out": "25:00"})[0], 400)
        st, rows = self.req("GET", f"/api/passes?date={today}")
        self.assertGreaterEqual(len(rows), 2)

    def test_roster(self):
        rows = parse_roster("번호\t이름\n1\t김가온\n2\t나하늘\n\n3  도윤서\n박지민")
        self.assertEqual(rows, [{"number": 1, "name": "김가온"}, {"number": 2, "name": "나하늘"},
                                {"number": 3, "name": "도윤서"}, {"number": 4, "name": "박지민"}])
        st, saved = self.req("PUT", "/api/students", {"rows": rows})
        self.assertEqual(len(saved), 4)
        self.assertEqual(self.req("PUT", "/api/students", {"rows": [{"number": "1", "name": "x"}]})[0], 400)

    # ---- 수신함·일정 ----
    def test_items_events_ics(self):
        st, it = self.req("POST", "/api/items", {"text": "협의록 제출\n2099년 1월 5일(월)까지 제출 바랍니다", "source": "paste"})
        self.assertEqual(st, 200, it)
        self.assertEqual(it["events"][0]["kind"], "deadline")
        self.assertEqual(self.req("POST", "/api/items", {"text": "협의록 제출\n2099년 1월 5일(월)까지 제출 바랍니다"})[0], 409)
        self.assertEqual(self.req("POST", "/api/items", {"text": "x", "source": "evil"})[0], 400)
        ev = it["events"][0]
        st, ev2 = self.req("PATCH", f"/api/events/{ev['id']}", {"done": True, "time": "17:00"})
        self.assertEqual((ev2["done"], ev2["time"]), (1, "17:00"))
        self.assertEqual(self.req("PATCH", f"/api/events/{ev['id']}", {"date": "2099-01-05", "end_date": "2099-01-01"})[0], 400)
        st, ics = self.req("GET", "/api/calendar.ics")
        self.assertEqual(st, 200)
        self.assertIn(b"BEGIN:VCALENDAR", ics)
        st, found = self.req("GET", "/api/search?q=" + quote("협의록"))
        self.assertTrue(found["items"])
        st, item = self.req("GET", f"/api/items/{it['id']}")
        self.assertEqual(item["status"], "read")
        self.assertEqual(self.req("PATCH", f"/api/items/{it['id']}", {"category": "없는분류"})[0], 400)
        self.assertEqual(self.req("DELETE", f"/api/items/{it['id']}")[0], 200)
        self.assertEqual(self.req("GET", f"/api/items/{it['id']}")[0], 404)
        # 항목을 지우면 일정도 함께 지워짐
        self.assertIsNone(self.env.db.one("SELECT id FROM events WHERE id = ?", (ev["id"],)))

    def test_settings_validation(self):
        self.assertEqual(self.req("PUT", "/api/settings", {"collect_times": ["25:00"]})[0], 400)
        st, r = self.req("PUT", "/api/settings", {"collect_times": ["9:00", "15:00", "12:00"], "port": 1})
        self.assertEqual(r["settings"]["collect_times"], ["09:00", "12:00", "15:00"])
        self.assertEqual(r["settings"]["port"], self.port)  # 포트는 화면에서 못 바꿈

    def test_bad_json(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/api/items", body=b"{not json", headers={
            "Host": f"127.0.0.1:{self.port}", "X-Desk-Token": self.app.token, "Content-Type": "application/json"})
        self.assertEqual(c.getresponse().status, 400)
        c.close()


if __name__ == "__main__":
    unittest.main()
