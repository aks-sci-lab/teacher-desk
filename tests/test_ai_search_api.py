"""화면 'AI 로 찾기' API (0.6.20): AI 꺼짐이면 막고, 켜지면 assist.answer 결과와 자료 번호를 돌려줌."""
import unittest
from unittest.mock import patch

from desk import ai
from desk.extract import analyze
from tests import test_server as _ts


class AiSearchApiTests(unittest.TestCase):
    # 서버 띄우기는 test_server 의 것을 그대로 (그쪽 시험들은 다시 돌리지 않음)
    setUpClass = classmethod(_ts.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(_ts.ServerTests.tearDownClass.__func__)
    req = _ts.ServerTests.req

    def test_off_blocks(self):
        self.env.config.values["ai_enabled"] = False
        status, r = self.req("POST", "/api/ai/search", {"q": "연말정산"})
        self.assertEqual(status, 400)
        self.assertIn("AI 가 꺼져", r["error"])

    def test_answer_with_items(self):
        iid = self.env.db.add_item("cool", "연말정산 서류 제출", "12월 15일까지", "2026-11-20 08:00:00", analyze("12월 15일까지"))
        self.env.config.values["ai_enabled"] = True
        try:
            with patch("desk.assist.answer", return_value=("12월 15일까지 [1]", [iid, 999999], [iid])) as m:
                status, r = self.req("POST", "/api/ai/search", {"q": "연말정산 언제까지"})
            self.assertEqual(status, 200)
            self.assertEqual(m.call_args[0][2], "연말정산 언제까지")
            self.assertEqual(r["text"], "12월 15일까지 [1]")
            self.assertEqual([(x["n"], x["id"]) for x in r["items"]], [(1, iid)])   # 없는 자료는 뺌
            with patch("desk.assist.answer", side_effect=ai.AIError("연결 안 됨")):
                status, r = self.req("POST", "/api/ai/search", {"q": "연말정산"})
            self.assertEqual(status, 400)
            self.assertIn("연결 안 됨", r["error"])
        finally:
            self.env.config.values["ai_enabled"] = False

    def test_needs_question(self):
        self.assertEqual(self.req("POST", "/api/ai/search", {"q": ""})[0], 400)


if __name__ == "__main__":
    unittest.main()
