"""0.5.99 긴 자료 나눠 요약(조각 요약 → 합치기) 시험. 가짜 LM Studio(127.0.0.1)로만."""

import unittest

from desk import ai
from desk.collect import add_text_item
from tests.test_ai_register import FakeLMStudio
from tests.test_collect import Env


def long_body(paras=30, name="김민준"):
    # 문단 하나 약 230자 → 30문단이면 약 7,000자
    return "\n\n".join(f"{i}번 문단: {name} 학생 관련 안내입니다. 11월 {i % 28 + 1}일까지 협의록을 제출해 주세요. " + "세부 내용 " * 30
                       for i in range(1, paras + 1))


class SplitTests(unittest.TestCase):
    def test_short_text_is_one_piece(self):
        self.assertEqual(ai.split_chunks("짧은 글", 100), ["짧은 글"])
        self.assertEqual(ai.split_chunks("   ", 100), [])

    def test_pieces_fit_and_keep_everything(self):
        text = long_body()
        parts = ai.split_chunks(text, 3500)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 3500 for p in parts))
        self.assertEqual("".join(parts).replace(" ", "").replace("\n", ""), text.replace(" ", "").replace("\n", ""))
        self.assertTrue(all(p.startswith(tuple(f"{i}번" for i in range(1, 31))) for p in parts))   # 문단 경계에서 끊음

    def test_no_spaces_falls_back_to_hard_cut(self):
        parts = ai.split_chunks("가" * 8000, 3000)
        self.assertEqual([len(p) for p in parts], [3000, 3000, 2000])

    def test_size_follows_input_setting(self):
        self.assertEqual(ai.chunk_size({"ai_input_size": "normal"}), 3500)
        self.assertEqual(ai.chunk_size({"ai_input_size": "short"}), 2000)
        self.assertEqual(ai.chunk_size({}), ai.ITEM_CHARS)


class ChunkSummaryTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.lm = FakeLMStudio()
        self.lm.reply = "한 줄 요약: 협의록 제출\n- 해야 할 일: 김민준 협의록 제출"
        self.env.config.values.update(ai_enabled=True, ai_base=self.lm.base)
        self.env.db.execute("INSERT INTO students(number, name) VALUES (7, '김민준')")
        self.job = ai.ItemJob(self.env.db, self.env.config)

    def tearDown(self):
        self.lm.close()
        self.env.close()

    def run_item(self, body):
        iid = add_text_item(self.env.db, "edufine", body, title="긴 공문")["id"]
        self.assertTrue(self.job.start(iid))
        self.job.thread.join(20)
        return self.env.db.one("SELECT * FROM items WHERE id = ?", (iid,))

    def test_short_item_is_one_request_as_before(self):
        row = self.run_item("김민준 학생 협의록을 11월 3일까지 제출해 주세요")
        self.assertEqual(len(self.lm.requests), 1)
        self.assertNotIn("조각", row["ai_summary"])

    def test_long_item_is_read_in_pieces_and_merged(self):
        body = long_body()
        row = self.run_item(body)
        pieces = len(ai.split_chunks(body, 3500))
        self.assertEqual(len(self.lm.requests), pieces + 1)            # 조각마다 1번 + 합치기 1번
        prompts = [r["messages"][1]["content"] for r in self.lm.requests]
        for p in prompts:
            self.assertNotIn("김민준", p)                               # 학생 이름은 모든 요청에서 가림
            self.assertLessEqual(len(p), 3500 + 600)                   # 한 요청은 한 조각 크기 안쪽
        self.assertIn("30번 문단", "".join(prompts))                    # 뒷부분까지 읽음 (예전에는 앞 3,500자만)
        self.assertIn("조각으로 나눠 읽은 메모", prompts[-1])
        self.assertIn("(1/", prompts[-1])                               # 마지막 요청은 조각 메모를 모음
        self.assertNotIn("김민준", row["ai_summary"])                   # AI 답에 이름이 나와도 가림
        self.assertIn(f"{pieces}조각으로 나눠", row["ai_summary"])
        self.assertNotIn("읽지 못함", row["ai_summary"])
        self.assertTrue(all(r["max_tokens"] <= 500 for r in self.lm.requests))

    def test_short_setting_uses_smaller_pieces(self):
        self.env.config.values["ai_input_size"] = "short"
        body = long_body(12)
        self.run_item(body)
        self.assertEqual(len(self.lm.requests), len(ai.split_chunks(body, 2000)) + 1)
        self.assertTrue(all(len(r["messages"][1]["content"]) <= 2000 + 600 for r in self.lm.requests))

    def test_very_long_item_stops_at_limit_and_says_so(self):
        row = self.run_item(long_body(200))                             # 약 46,000자
        self.assertEqual(len(self.lm.requests), ai.CHUNK_MAX + 1)
        self.assertIn("읽지 못함", row["ai_summary"])

    def test_long_notes_are_merged_again(self):
        self.lm.reply = "- " + "메모 " * 600                               # 조각 메모가 길어 한 번에 안 들어감
        body = long_body()
        self.run_item(body)
        pieces = len(ai.split_chunks(body, 3500))
        self.assertGreater(len(self.lm.requests), pieces + 1)           # 메모끼리 다시 줄이는 요청이 더 있음
        self.assertTrue(all(len(r["messages"][1]["content"]) <= 3500 + 600 for r in self.lm.requests))

    def test_progress_is_shown_while_running(self):
        seen = []
        it = {"source": "edufine", "title": "긴 공문"}
        ai.summarize_item(self.lm.base, "qwen3-8b", it, long_body(), ["김민준"], 3500,
                          progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(seen[0][0], 0)
        self.assertEqual(seen[-1][0], seen[-1][1] - 1)                  # 마지막은 합치기 단계

    def test_stop_while_app_closes(self):
        it = {"source": "edufine", "title": "긴 공문"}
        with self.assertRaises(ai.AIError):
            ai.summarize_item(self.lm.base, "qwen3-8b", it, long_body(), [], 3500, stop=lambda: True)
        self.assertEqual(self.lm.requests, [])


if __name__ == "__main__":
    unittest.main()
