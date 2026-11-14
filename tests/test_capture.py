"""브라우저 단축키로 보낸 화면(/capture) 시험."""
import http.client
import json
import unittest
from datetime import datetime

from desk import capture
from tests import test_server

EXT = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
PAGE = ("업무 안내\n제목: 2026학년도 학교폭력 예방 교육 실시 안내\n"
        "1. 일시: 2026. 11. 12.(목) 14:00\n2. 제출 기한: 2026. 11. 6.(금)까지 제출\n붙임 1부.  끝.")


class CleanTextTests(unittest.TestCase):
    def test_clean(self):
        raw = "가나다  \r\n\r\n\r\n\r\n라마바\n라마바\n라마바\n \n사아자\x00"
        self.assertEqual(capture.clean_text(raw), "가나다\n\n라마바\n\n사아자")

    def test_boilerplate_and_list_titles(self):
        self.assertEqual(capture.clean_text("접근성모드로 전환을 원하시면 컨트롤 + 스페이스 키를 입력하세요.\n결재 대기"), "결재 대기")
        when = datetime(2026, 11, 2, 9, 10)
        self.assertEqual(capture.list_title("https://klef.use.go.kr/bms/cz/cb/viw/retrieveSanctnWaitDocList.do", when),
                         "결재 대기 문서 목록 (11/02 09:10)")
        self.assertEqual(capture.list_title("https://klef.use.go.kr/bms/cz/cb/dbx/retrieveDocRegistRegstrList.do?x=1", when),
                         "문서등록대장 목록 (11/02 09:10)")
        self.assertEqual(capture.list_title("https://klef.use.go.kr/a/retrieveSomethingList.do", when), "에듀파인 목록 (11/02 09:10)")
        self.assertIsNone(capture.list_title("https://klef.use.go.kr/keris_ui/main.do", when))
        self.assertIsNone(capture.list_title("", when))

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

    def test_read_only_pc_does_not_accept_captures(self):   # 0.5.96: 다른 PC 가 쓰는 중이면 받아도 버려지므로 받지 않음
        from desk import cloudsync
        key = self.enable()
        h = {"Origin": EXT, "X-Desk-Capture-Key": key}
        cloudsync._paused.set()
        try:
            status, body, _ = self.raw("POST", "/capture", {"text": PAGE}, h)
            self.assertEqual(status, 423)
            self.assertIn("다른 PC 에서 사용 중", body["error"])
            status, body, _ = self.raw("GET", "/capture/ping", headers=h)
            self.assertEqual((status, body["read_only"]), (200, True))   # 확장 프로그램 연결 확인은 됨
        finally:
            cloudsync._paused.clear()
        self.assertFalse(self.raw("GET", "/capture/ping", headers=h)[1]["read_only"])   # 풀리면 다시 받음

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

    def test_list_capture_gets_list_title(self):
        key = self.enable()
        h = {"Origin": EXT, "X-Desk-Capture-Key": key}
        status, body, _ = self.raw("POST", "/capture", {
            "text": "열람\n여부\n게시일\n제목\n2026-11-02 현장체험학습 결과 보고 (목록 제목 시험)", "title": "K-에듀파인",
            "frame_url": "https://klef.use.go.kr/bms/cz/cb/viw/retrieveSanctnWaitDocList.do"}, h)
        self.assertEqual(status, 200, body)
        self.assertTrue(self.env.db.item(body["id"])["title"].startswith("결재 대기 문서 목록 ("))

    def test_neis_screen_goes_to_direct_input_with_neis_title(self):
        key = self.enable()
        h = {"Origin": EXT, "X-Desk-Capture-Key": key}
        status, body, _ = self.raw("POST", "/capture", {
            "text": "학급 출결 현황 화면입니다. 11월 3일 결석 처리 확인 필요 (나이스 시험)", "title": "출결관리",
            "url": "https://use.neis.go.kr/jsp/main.jsp"}, h)
        self.assertEqual(status, 200, body)
        it = self.env.db.item(body["id"])
        self.assertEqual(it["source"], "paste")
        self.assertTrue(it["title"].startswith("나이스 · 출결관리 ("))
        self.assertTrue(capture.is_neis("https://neis.go.kr/x"))
        self.assertFalse(capture.is_neis("https://neis.go.kr.evil.example/x"))
        self.assertFalse(capture.is_neis("https://klef.use.go.kr/x"))

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
