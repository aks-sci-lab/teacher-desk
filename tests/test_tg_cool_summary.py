"""0.6.8 새 쿨메신저 쪽지를 이 PC 의 AI 로 다섯 칸 요약해 텔레그램으로 (선생님 요청 2026-11-17)."""
import unittest

from desk import ai
from desk.collect import add_text_item
from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_ai_register import FakeLMStudio
from tests.test_collect import Env
from tests.test_telegram_more import CHAT

FIVE = ("보낸 사람: 교무부 홍길동\n업무 내용: ○○○ 학생 체험학습 보고서 제출\n마감 기한: 11월 20일(금) 16시\n"
        "첨부파일: 없음\n긴급 여부: 긴급")


class CoolFieldsTests(unittest.TestCase):
    def test_reads_five_fields_and_prefers_known_sender_and_file_names(self):
        body = "보고서를 내 주세요. 붙임: 결과보고서양식.hwp, 명단.xlsx\n\n(보낸 사람: 이창헌(이창헌))"
        f = ai.cool_fields(FIVE, body)
        self.assertEqual(f["sender"], "이창헌")                      # 쪽지에 적힌 보낸 사람이 우선
        self.assertEqual(f["due"], "11월 20일(금) 16시")
        self.assertEqual(f["files"], "결과보고서양식.hwp, 명단.xlsx")   # AI 가 '없음' 이라 해도 본문의 파일 이름
        self.assertEqual(f["urgent"], "긴급")

    def test_markdown_bullets_none_words_and_not_urgent(self):
        text = "- **보낸 사람**: 모름\n- **업무 내용**: 회의 안내\n- 마감 기한: 없음\n- 긴급 여부: 긴급하지 않음(보통)"
        f = ai.cool_fields(text, "회의가 있습니다")
        self.assertEqual((f["sender"], f["task"], f["due"], f["files"], f["urgent"]), ("", "회의 안내", "", "", "보통"))

    def test_other_shapes_are_not_forced(self):
        self.assertIsNone(ai.cool_fields("한 줄 요약: 협의록 제출\n- 해야 할 일: 협의록 제출"))

    def test_list_line_uses_task_field(self):
        self.assertEqual(ai.summary_line(FIVE), "○○○ 학생 체험학습 보고서 제출")
        self.assertEqual(ai.summary_line("한 줄 요약: 협의록 제출\n- 해야 할 일: 제출"), "협의록 제출")


class TelegramCoolSummaryTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.lm = FakeLMStudio()
        self.lm.reply = FIVE
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base,
                                      ai_enabled=True, ai_base=self.lm.base, ai_auto_class_only=True)
        self.env.db.replace_students([{"number": 7, "name": "최별빛"}])
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.env.db.set_meta("telegram_chat_id", CHAT)
        self.job = ai.ItemJob(self.env.db, self.env.config, gate=lambda: False)   # 수업 시간이 아니어도 바로
        self.bot.item_ai = self.job

    def tearDown(self):
        self.job.stop()
        self.lm.close()
        self.fake.close()
        self.env.close()

    def item(self, body, title="체험학습 보고서", source="cool"):
        it = add_text_item(self.env.db, source, body, title=title)
        return it["id"] if isinstance(it, dict) else it

    def notify(self, *ids):
        self.bot.notify_collect({"items": len(ids), "new_ids": list(ids), "deadlines": 0}, "schedule")

    def texts(self):
        return [m.get("text", "") for m in self.fake.sent]

    def test_new_memo_summary_is_sent_after_notice_with_original_button(self):
        a = self.item("최별빛 학생 체험학습 보고서를 11월 20일 16시까지 내 주세요. 급합니다.\n\n(보낸 사람: 홍길동)")
        self.notify(a)
        texts = self.texts()
        summary = [t for t in texts if t.startswith("🤖")]
        self.assertEqual(len(summary), 1)
        self.assertLess(texts.index(next(t for t in texts if t.startswith("📥"))), texts.index(summary[0]))  # 알림이 먼저
        s = summary[0]
        for label in ("보낸 사람: 홍길동", "업무 내용:", "마감 기한: 11월 20일(금) 16시", "첨부파일: 없음", "🚨 긴급 여부: 긴급"):
            self.assertIn(label, s)
        self.assertNotIn("최별빛", self.lm.requests[-1]["messages"][1]["content"])   # AI 에는 이름을 가려 보냄
        self.assertNotIn("급합니다", s)                                             # 원문은 보내지 않음
        last = [m for m in self.fake.sent if m.get("text", "").startswith("🤖")][-1]
        self.assertIn("o:", str(last.get("reply_markup")))                         # [원문 보기] 단추
        row = self.env.db.one("SELECT ai_summary FROM items WHERE id = ?", (a,))
        self.assertIn("업무 내용", row["ai_summary"])                               # 수신함에도 같은 요약이 남음

    def test_failure_sends_reason_and_button_only(self):
        a = self.item("회의 자료를 확인해 주세요. 내일까지 회신 바랍니다.")
        self.lm.close()                                                           # LM Studio 꺼짐
        self.notify(a)
        s = [t for t in self.texts() if t.startswith("🤖")]
        self.assertEqual(len(s), 1)
        self.assertIn("AI 요약 없음", s[0])
        self.assertIn("LM Studio", s[0])

    def test_late_summary_does_not_block(self):
        a = self.item("회의 자료를 확인해 주세요.")
        self.bot.cool_summary_wait = 0
        self.job.busy = lambda: True                                              # 브리핑 요약이 오래 도는 중
        self.notify(a)
        s = [t for t in self.texts() if t.startswith("🤖")]
        self.assertIn("요약이 늦어", s[0])

    def test_off_ai_off_and_other_sources_send_nothing(self):
        a = self.item("회의 자료를 확인해 주세요.")
        self.env.config.values["telegram_cool_summary"] = False
        self.notify(a)
        self.env.config.values["telegram_cool_summary"] = True
        self.env.config.values["ai_enabled"] = False
        self.notify(a)
        self.env.config.values["ai_enabled"] = True
        self.notify(self.item("공문 내용입니다. 제출 바랍니다.", source="edufine"))
        self.assertFalse(any(t.startswith("🤖") for t in self.texts()))
        self.assertEqual(self.lm.requests, [])

    def test_at_most_three_memos(self):
        ids = [self.item(f"쪽지 {i} 내용을 확인해 주세요.", title=f"쪽지 {i}") for i in range(5)]
        self.notify(*ids)
        s = [t for t in self.texts() if t.startswith("🤖")]
        self.assertEqual(len(s), 4)                                               # 세 건 + '2건은 수신함에서'
        self.assertIn("2건", s[-1])
        self.assertEqual(len(self.lm.requests), 3)


if __name__ == "__main__":
    unittest.main()
