import unittest

from desk import ai
from desk.collect import add_text_item
from tests.test_ai_register import FakeLMStudio
from tests.test_collect import Env


class ItemAiTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.lm = FakeLMStudio()
        self.lm.reply = "<think>생각</think>한 줄 요약: 협의록 제출\n- 해야 할 일: 협의록 제출"
        self.env.config.values.update(ai_enabled=True, ai_base=self.lm.base)
        self.env.db.execute("INSERT INTO students(number, name) VALUES (7, '김민준')")
        self.job = ai.ItemJob(self.env.db, self.env.config)
        self.item = add_text_item(self.env.db, "cool", "김민준 학생 협의록을 11월 3일까지 제출해 주세요", title="협의록")["id"]

    def tearDown(self):
        self.lm.close()
        self.env.close()

    def wait(self):
        self.job.thread.join(10)

    def row(self):
        return self.env.db.one("SELECT * FROM items WHERE id = ?", (self.item,))

    def test_summary_is_saved_apart_from_the_original_and_reused(self):
        original = self.row()["body"]
        self.assertTrue(self.job.start(self.item))
        self.wait()
        row = self.row()
        self.assertEqual(row["body"], original)                              # 원문은 그대로
        self.assertIn("협의록 제출", row["ai_summary"])
        self.assertNotIn("생각", row["ai_summary"])
        self.assertNotIn("김민준", self.lm.requests[-1]["messages"][1]["content"])   # 학생 이름은 가려서 보냄
        state = ai.item_ai(self.env.db, row, self.job)
        self.assertEqual((state["stale"], state["running"], state["error"]), (False, False, None))
        calls = len(self.lm.requests)
        self.assertFalse(self.job.start(self.item))                           # 원문이 같으면 다시 만들지 않음
        self.assertEqual(len(self.lm.requests), calls)
        self.assertTrue(self.job.start(self.item, force=True))                # [다시 요약] 은 만듦
        self.wait()
        self.assertEqual(len(self.lm.requests), calls + 1)

    def test_changed_body_is_marked_stale(self):
        self.job.start(self.item)
        self.wait()
        self.env.db.execute("UPDATE items SET body = body || ' 11월 5일로 변경' WHERE id = ?", (self.item,))
        state = ai.item_ai(self.env.db, self.row(), self.job)
        self.assertTrue(state["stale"])
        self.assertTrue(self.job.start(self.item))                            # 바뀐 원문은 다시 요약

    def test_failure_is_shown_and_keeps_old_summary(self):
        self.job.start(self.item)
        self.wait()
        old = self.row()["ai_summary"]
        self.lm.reply = ""                                                    # 생각만 하고 답이 없는 경우
        self.assertTrue(self.job.start(self.item, force=True))
        self.wait()
        state = ai.item_ai(self.env.db, self.row(), self.job)
        self.assertIn("요약", state["error"])
        self.assertEqual(self.row()["ai_summary"], old)                       # 실패해도 예전 요약은 남김

    def test_only_one_at_a_time_and_missing_item(self):
        with self.assertRaises(ai.AIError):
            self.job.start(99999)


class ItemAiApiTests(unittest.TestCase):
    from tests import test_server
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def test_api_requires_ai_on_and_hides_raw_columns(self):
        it = add_text_item(self.env.db, "cool", "항목 AI 요약 API 시험 쪽지입니다", title="API")["id"]
        self.env.config.values["ai_enabled"] = False
        self.assertEqual(self.req("POST", f"/api/items/{it}/ai-summary", {})[0], 400)
        st, body = self.req("GET", f"/api/items/{it}")
        self.assertEqual(st, 200)
        self.assertEqual(body["ai"], {"text": "", "at": None, "model": None, "stale": False, "running": False, "error": None})
        self.assertNotIn("ai_summary_hash", body)
        self.assertEqual(self.req("POST", "/api/items/99999/ai-summary", {})[0], 404)


if __name__ == "__main__":
    unittest.main()
