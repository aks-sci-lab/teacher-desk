"""브라우저 단축키로 보낸 화면(/capture) 시험."""
import http.client
import json
import unittest

from desk import capture
from tests import test_server

EXT = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
PAGE = ("업무 안내\n제목: 2026학년도 학교폭력 예방 교육 실시 안내\n"
        "1. 일시: 2026. 11. 12.(목) 14:00\n2. 제출 기한: 2026. 11. 6.(금)까지 제출\n붙임 1부.  끝.")


class CleanTextTests(unittest.TestCase):
    def test_clean(self):
        raw = "가나다  \r\n\r\n\r\n\r\n라마바\n라마바\n라마바\n \n사아자\x00"
        self.assertEqual(capture.clean_text(raw), "가나다\n\n라마바\n\n사아자")

    def test_extension_origin(self):
        self.assertTrue(capture.is_extension_origin(EXT))
        self.assertTrue(capture.is_extension_origin("moz-extension://1234-abcd"))
        for bad in (None, "", "https://evil.example", "http://127.0.0.1:8770", "chrome-extension://", "file://"):
            self.assertFalse(capture.is_extension_origin(bad), bad)


class CaptureApiTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def setUp(self):
        self.env.config.set_value("capture_key", "")

    def raw(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        h.update(headers or {})
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        data = r.read()
        hdrs = dict(r.getheaders())
        c.close()
        return r.status, (json.loads(data) if data else None), hdrs

    def enable(self):
        status, body = self.req("POST", "/api/capture", {"action": "enable"})
        self.assertEqual(status, 200)
        self.assertTrue(body["enabled"])
        return body["key"]

    def test_off_by_default_rejects_everything(self):
        status, body, _ = self.raw("POST", "/capture", {"text": PAGE}, {"Origin": EXT, "X-Desk-Capture-Key": "x"})
        self.assertEqual(status, 403)
        self.assertIn("연결", body["error"])
        self.assertEqual(self.raw("GET", "/capture/ping", headers={"Origin": EXT})[0], 403)

    def test_key_enable_renew_disable(self):
        self.assertFalse(self.req("GET", "/api/capture")[1]["enabled"])
        key = self.enable()
        self.assertGreaterEqual(len(key), 20)
        self.assertEqual(self.req("POST", "/api/capture", {"action": "enable"})[1]["key"], key)   # 다시 눌러도 그대로
        new = self.req("POST", "/api/capture", {"action": "renew"})[1]["key"]
        self.assertNotEqual(new, key)
        self.assertEqual(self.req("POST", "/api/capture", {"action": "disable"})[1]["enabled"], False)
        self.assertEqual(self.req("POST", "/api/capture", {"action": "bogus"})[0], 400)
        self.assertEqual(self.req("GET", "/api/capture", token=False)[0], 401)   # 설정 화면의 토큰 필요

    def test_key_not_in_public_settings(self):
        key = self.enable()
        status, state = self.req("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertNotIn(key, json.dumps(state))
        self.assertNotIn("capture_key", state["settings"])
        self.assertTrue(state["settings"]["capture_key_set"])

    def test_wrong_key_and_origin(self):
        key = self.enable()
        h = {"Origin": EXT}
        self.assertEqual(self.raw("POST", "/capture", {"text": PAGE}, {**h, "X-Desk-Capture-Key": "wrong-key"})[0], 401)
        self.assertEqual(self.raw("POST", "/capture", {"text": PAGE}, h)[0], 401)
        # 다른 웹사이트(키를 알아도)·다른 Host 는 거절
        self.assertEqual(self.raw("POST", "/capture", {"text": PAGE},
                                  {"Origin": "https://evil.example", "X-Desk-Capture-Key": key})[0], 403)
        self.assertEqual(self.raw("POST", "/capture", {"text": PAGE},
                                  {"Origin": EXT, "X-Desk-Capture-Key": key, "Host": "evil.example"})[0], 403)
        # 확장 프로그램 origin 도 /api 는 못 씀
        self.assertEqual(self.req("GET", "/api/state", headers={"Origin": EXT})[0], 403)

    def test_receive_into_inbox(self):
        key = self.enable()
        h = {"Origin": EXT, "X-Desk-Capture-Key": key}
        status, body, hdrs = self.raw("POST", "/capture",
                                      {"text": PAGE, "title": "K-에듀파인", "url": "https://klef.use.go.kr/keris_ui/main.do?x=비밀"}, h)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertFalse(body["duplicate"])
        self.assertGreaterEqual(body["events"], 1)
        self.assertEqual(hdrs.get("Access-Control-Allow-Origin"), EXT)
        item = self.env.db.item(body["id"])
        self.assertEqual(item["source"], "edufine")
        self.assertIn("학교폭력 예방 교육", item["title"])      # 본문의 '제목' 줄이 제목
        self.assertTrue(any(e["date"] == "2026-11-06" for e in item["events"]))
        logs = " ".join(r["message"] for r in self.env.db.query("SELECT message FROM logs ORDER BY id DESC LIMIT 5"))
        self.assertIn("klef.use.go.kr", logs)
        self.assertNotIn("비밀", logs)                            # 주소의 검색어는 기록하지 않음
        # 같은 화면을 또 보내면 중복
        status, again, _ = self.raw("POST", "/capture", {"text": PAGE}, h)
        self.assertEqual((status, again["duplicate"]), (200, True))

    def test_title_falls_back_to_page_title_and_short_text_rejected(self):
        key = self.enable()
        h = {"Origin": EXT, "X-Desk-Capture-Key": key}
        status, body, _ = self.raw("POST", "/capture",
                                   {"text": "안내문입니다. 아래 내용을 확인하세요 스무자이상.", "title": "받은 문서함"}, h)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.env.db.item(body["id"])["title"], "받은 문서함")
        status, body, _ = self.raw("POST", "/capture", {"text": "  짧음 "}, h)
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])

    def test_preflight(self):
        status, _, hdrs = self.raw("OPTIONS", "/capture", headers={
            "Origin": EXT, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-desk-capture-key"})
        self.assertEqual(status, 204)
        self.assertEqual(hdrs.get("Access-Control-Allow-Origin"), EXT)
        self.assertIn("x-desk-capture-key", hdrs.get("Access-Control-Allow-Headers", ""))
        # 다른 사이트·다른 경로의 사전 확인은 거절 (CORS 허용 머리글 없음)
        status, _, hdrs = self.raw("OPTIONS", "/capture", headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", hdrs)
        self.assertEqual(self.raw("OPTIONS", "/api/state", headers={"Origin": EXT})[0], 403)


if __name__ == "__main__":
    unittest.main()
