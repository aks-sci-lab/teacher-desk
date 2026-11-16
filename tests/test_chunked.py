"""긴 파일 나눠 요약 (0.5.99): 나누는 자리·짧은 글은 한 번·규칙 날짜 붙이기·멈추기."""
import unittest
from unittest.mock import patch

from desk import ai
from desk.collect import add_text_item
from tests.test_ai_register import FakeLMStudio
from tests.test_collect import Env

SENT = "학생들은 정해진 기간 안에 신청서를 작성하여 담임교사에게 제출하여야 합니다. "


def long_doc(sections=6, per=12):
    """제목 번호(1. 가. □)와 문단이 있는 긴 공문 (한 절에 약 600자 × per/12)."""
    out = ["2026학년도 겨울방학 방과후학교 운영 계획", ""]
    marks = ["1.", "2.", "가.", "나.", "□", "○"]
    for s in range(sections):
        out.append(f"{marks[s % len(marks)]} {s + 1}번째 항목 안내")
        out.append(SENT * per)
        out.append("")
    out.append("신청서는 2026. 12. 4.(금)까지 제출해 주시기 바랍니다.")
    return "\n".join(out)


class SplitTests(unittest.TestCase):
    def test_chunks_fit_and_cut_before_headings(self):
        text = long_doc(sections=8)
        chunks = ai.split_chunks(text, 3500)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 3500 for c in chunks))
        for c in chunks[1:]:                                        # 둘째 조각부터는 제목·번호 줄로 시작
            self.assertRegex(c, r"^(?:\d\.|[가-하]\.|[□○])")
        self.assertEqual("".join(c.replace("\n", "") for c in chunks).replace(" ", ""),
                         text.replace("\n", "").replace(" ", ""))   # 글자를 잃거나 겹치지 않음

    def test_long_paragraph_is_cut_at_sentence_end(self):
        text = SENT * 120                                            # 빈 줄·제목 없이 약 5천 자
        chunks = ai.split_chunks(text, 2000)
        self.assertGreater(len(chunks), 2)
        for c in chunks:
            self.assertLessEqual(len(c), 2000)
            self.assertTrue(c.endswith("합니다."), c[-20:])          # 문장 중간에서 자르지 않음

    def test_pdf_page_boundary_is_a_cut_point(self):
        page = SENT * 30                                             # 한 쪽 약 1,200자, 쪽 사이는 빈 줄 (docs.pdf_text)
        chunks = ai.split_chunks("\n\n".join([page] * 4), 3000)
        one = len(page.strip())
        self.assertEqual([len(c) for c in chunks], [one * 2 + 1, one * 2 + 1])

    def test_no_sentence_end_falls_back_to_space(self):
        chunks = ai.split_chunks("가나다 " * 1000, 1000)
        self.assertTrue(all(len(c) <= 1000 for c in chunks))
        self.assertTrue(all(not c.endswith("가나") for c in chunks))   # 낱말 중간도 피함


class LongSummaryTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.lm = FakeLMStudio()
        self.lm.reply = "한 줄 요약: 방과후학교 신청\n- 해야 할 일: 신청서 제출"
        self.env.config.values.update(ai_enabled=True, ai_base=self.lm.base)
        self.env.db.execute("INSERT INTO students(number, name) VALUES (7, '김민준')")
        self.job = ai.ItemJob(self.env.db, self.env.config)
        self.patch = patch.object(ai, "CHUNK_PAUSE", 0)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.job.stop()
        self.lm.close()
        self.env.close()

    def add(self, body):
        return add_text_item(self.env.db, "edufine", body, title="방과후학교 운영 계획")["id"]

    def run_item(self, iid):
        self.job.start(iid)
        self.job.thread.join(20)
        return self.env.db.one("SELECT ai_summary FROM items WHERE id = ?", (iid,))["ai_summary"]

    def test_short_item_is_summarized_in_one_go(self):
        iid = self.add("김민준 학생 협의록을 11월 3일까지 제출해 주세요. " * 20)   # 약 600자
        text = self.run_item(iid)
        self.assertEqual(len(self.lm.requests), 1)
        self.assertNotIn("조각", text)
        self.assertNotIn("규칙으로 찾은 날짜", text)

    def test_long_item_is_chunked_then_merged_with_rule_dates(self):
        body = "김민준 학생 포함 " + long_doc(sections=14)               # 약 7,500자
        iid = self.add(body)
        n = len(ai.split_chunks(body, 3500))
        text = self.run_item(iid)
        prompts = [r["messages"][1]["content"] for r in self.lm.requests]
        self.assertEqual(len(prompts), n + 1)                        # 조각마다 + 모아서 한 번
        self.assertIn(f"{n}조각으로 나눈 것 중 1번째", prompts[0])
        self.assertIn("조각 요약:", prompts[-1])
        self.assertTrue(all("김민준" not in p for p in prompts))     # 학생 이름은 가려서 보냄
        self.assertTrue(text.startswith("한 줄 요약: 방과후학교 신청"))
        self.assertIn(f"(긴 자료 {n}조각으로 나눠 요약)", text)
        self.assertIn("규칙으로 찾은 날짜·마감 (AI 아님, 원문 확인):", text)
        self.assertIn("12/4(금) 마감", text)                          # 규칙(extract)이 찾은 마감

    def test_very_long_item_uses_first_chunks_only(self):
        with patch.object(ai, "MAX_CHUNKS", 2):
            text = self.run_item(self.add(long_doc(sections=20)))
        self.assertEqual(len(self.lm.requests), 3)
        self.assertRegex(text, r"긴 자료 \d+조각으로 나눠 요약, 앞 2조각까지만")

    def test_short_setting_cuts_smaller(self):
        self.env.config.values["ai_input_size"] = "short"
        body = long_doc(sections=8)                                  # 약 4,300자: 보통이면 한 번, 짧게면 나눔
        self.assertLess(len(body), ai.LONG_SIZES["normal"][0])
        self.run_item(self.add(body))
        self.assertGreater(len(self.lm.requests), 2)

    def test_progress_and_cancel(self):
        iid = self.add(long_doc(sections=14))
        seen = []
        real = ai.ask

        def ask(*a, **k):
            seen.append(self.job.status(iid)["progress"])
            if len(seen) == 1:
                self.assertTrue(self.job.cancel(iid))                # 첫 조각을 만드는 동안 [요약 멈추기]
            return real(*a, **k)

        with patch.object(ai, "ask", ask):
            text = self.run_item(iid)
        self.assertEqual(seen[0]["done"], 0)
        self.assertGreater(seen[0]["total"], 1)
        self.assertEqual(len(self.lm.requests), 1)                   # 다음 조각은 시작하지 않음
        self.assertIsNone(text)                                      # 저장하지 않음
        st = self.job.status(iid)
        self.assertEqual((st["error"], st["progress"], st["running"]), (None, None, False))
        self.assertIn(iid, self.job.failed)                          # 자동 요약이 곧바로 다시 하지 않음


if __name__ == "__main__":
    unittest.main()
