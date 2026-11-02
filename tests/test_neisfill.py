"""나이스 입력 칸 채우기 (0.5.56, 시험 단계): 구조 받기(값 없음) → 채우기 요청 → 확장 프로그램이 가져가 결과 알림."""
import http.client
import json
import threading
import time
import unittest

from desk import neisfill
from tests import test_server
from tests.test_collect import Env

EXT = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
FIELDS = [
    {"key": "#stdSel", "label": "학생", "type": "select", "options": ["1번 김가온", "2번 나하늘"], "value": "몰래 온 값"},
    {"key": "#absDate", "label": "날짜", "type": "date"},
    {"key": "#reason", "label": "사유", "type": "textarea"},
    {"key": "#confirm", "label": "확인", "type": "checkbox"},
    {"key": "", "label": "찾는 법이 없는 칸"},
    "칸이 아님",
]


class NeisFillTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.nf = neisfill.NeisFill(self.env.db)

    def tearDown(self):
        self.env.close()

    def form(self):
        return self.nf.save_form("use.neis.go.kr", "/edu/att/form.do", "나이스 · 출결", FIELDS)

    def test_structure_is_saved_without_values(self):
        f = self.form()
        self.assertEqual([x["key"] for x in f["fields"]], ["#stdSel", "#absDate", "#reason", "#confirm"])
        self.assertNotIn("몰래 온 값", json.dumps(self.nf.forms(), ensure_ascii=False))   # 값은 저장하지 않음
        again = self.nf.save_form("use.neis.go.kr", "/edu/att/form.do", "나이스 · 출결", FIELDS[:2])
        self.assertEqual((again["id"], len(self.nf.forms())), (f["id"], 1))                # 같은 화면은 새로 고침
        self.nf.save_form("use.neis.go.kr", "/edu/other.do", "다른 화면", FIELDS[:1])
        self.assertEqual(len(self.nf.forms()), 2)

    def test_only_neis_and_needs_fields(self):
        for host in ("klef.use.go.kr", "neis.go.kr.evil.example", "evil.example"):
            with self.assertRaises(neisfill.FillError):
                self.nf.save_form(host, "/x", "x", FIELDS)
        with self.assertRaises(neisfill.FillError):
            self.nf.save_form("use.neis.go.kr", "/x", "x", [{"label": "칸 없음"}])

    def test_request_take_report(self):
        f = self.form()
        with self.assertRaises(neisfill.FillError):
            self.nf.request(f["id"], {"#reason": "  "})                                     # 채울 값이 없음
        job = self.nf.request(f["id"], {"#stdSel": "1번 김가온", "#reason": "감기", "#confirm": "선택", "#absDate": "", "#없는칸": "x"})
        self.assertEqual([(e["key"], e["value"]) for e in job["entries"]],
                         [("#stdSel", "1번 김가온"), ("#reason", "감기"), ("#confirm", True)])   # 비워 둔 칸·없는 칸은 빼고
        taken = self.nf.take()
        self.assertEqual((taken["id"], taken["status"]), (job["id"], "taken"))
        self.assertIsNone(self.nf.take())                                                    # 한 번만 가져감
        done = self.nf.report(job["id"], {"filled": 2, "missing": ["확인"]})
        self.assertEqual((done["status"], done["result"]["missing"]), ("done", ["확인"]))
        cancelled = self.nf.request(f["id"], {"#reason": "두통"})
        self.nf.take()
        self.assertEqual(self.nf.report(cancelled["id"], {"filled": 0, "error": "선생님이 취소함"})["status"], "failed")

    def test_new_request_replaces_waiting_one_and_cancel(self):
        f = self.form()
        a = self.nf.request(f["id"], {"#reason": "가"})
        b = self.nf.request(f["id"], {"#reason": "나"})
        self.assertEqual([j["id"] for j in self.nf.jobs()], [b["id"]])                   # 기다리던 예전 요청은 바뀜
        self.nf.cancel(b["id"])
        self.assertIsNone(self.nf.take())
        self.assertNotEqual(a["id"], b["id"])

    def test_take_waits_for_a_request(self):
        f = self.form()
        got = {}
        t = threading.Thread(target=lambda: got.update(job=self.nf.take(wait=5)))
        t.start()
        time.sleep(0.3)
        job = self.nf.request(f["id"], {"#reason": "감기"})
        t.join(5)
        self.assertEqual(got["job"]["id"], job["id"])                                       # 기다리다가 바로 받음
        start = time.monotonic()
        self.assertIsNone(self.nf.take(wait=0.3))
        self.assertGreaterEqual(time.monotonic() - start, 0.25)


class NeisFillApiTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def ext(self, method, path, body=None, key=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}", "Origin": EXT, "Content-Type": "application/json"}
        if key:
            h["X-Desk-Capture-Key"] = self.env.config["capture_key"]
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        data = json.loads(r.read() or b"{}")
        c.close()
        return r.status, data

    def test_flow_through_api(self):
        self.req("POST", "/api/capture", {"action": "enable"})
        self.assertEqual(self.ext("POST", "/capture/form", {"host": "use.neis.go.kr", "frame_path": "/a.do", "title": "출결", "fields": FIELDS}, key=False)[0], 401)
        st, r = self.ext("POST", "/capture/form", {"host": "use.neis.go.kr", "frame_path": "/a.do", "title": "출결", "fields": FIELDS})
        self.assertEqual((st, r["fields"]), (200, 4))
        self.assertEqual(self.ext("POST", "/capture/form", {"host": "klef.use.go.kr", "frame_path": "/a.do", "fields": FIELDS})[0], 400)
        state = self.req("GET", "/api/neis-fill")[1]
        form = next(f for f in state["forms"] if f["frame_path"] == "/a.do")
        self.assertEqual(self.req("GET", "/api/neis-fill", token=False)[0], 401)
        self.assertEqual(self.ext("GET", "/capture/fill")[1], {"job": None})
        st, job = self.req("POST", "/api/neis-fill/jobs", {"form_id": form["id"], "values": {"#reason": "감기"}})
        self.assertEqual(st, 200, job)
        st, got = self.ext("GET", "/capture/fill?wait=1")
        self.assertEqual(got["job"]["entries"], [{"key": "#reason", "label": "사유", "type": "textarea", "value": "감기"}])
        self.assertEqual(self.ext("POST", "/capture/fill/result", {"id": job["id"], "filled": 1, "missing": []})[0], 200)
        self.assertEqual(self.req("GET", "/api/neis-fill")[1]["jobs"][0]["status"], "done")
        self.assertEqual(self.ext("POST", "/capture/fill/result", {"id": "nope", "filled": 1})[0], 400)
        self.assertEqual(self.req("POST", "/api/neis-fill/jobs", {"form_id": "nope", "values": {"#reason": "x"}})[0], 400)
        self.assertEqual(self.req("DELETE", f"/api/neis-fill/forms/{form['id']}")[0], 200)
        self.req("POST", "/api/capture", {"action": "disable"})


if __name__ == "__main__":
    unittest.main()
