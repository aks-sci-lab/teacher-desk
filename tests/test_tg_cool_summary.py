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

    def test_values_only_five_lines_are_read_in_order(self):
        """0.6.14 학교 PC Gemma 4 가 칸 이름 없이 값만 다섯 줄로 답함 (선생님 화면 2026-11-20)."""
        text = "문신혜(문신혜)\n오늘 6, 7교시 동아리 시간, 1, 2학년만 실시함.\n없음\n없음\n보통"
        f = ai.cool_fields(text, "동아리 안내\n\n(보낸 사람: 문신혜(문신혜))")
        self.assertEqual(f["sender"], "문신혜")
        self.assertEqual(f["task"], "오늘 6, 7교시 동아리 시간, 1, 2학년만 실시함.")
        self.assertEqual((f["due"], f["files"], f["urgent"]), ("", "", "보통"))
        self.assertIsNone(ai.cool_fields("첫 줄\n둘째 줄\n셋째 줄"))   # 다섯 줄이 아니면 억지로 맞추지 않음

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

    def test_new_memo_summary_is_sent_once_with_original_button(self):
        a = self.item("최별빛 학생 체험학습 보고서를 11월 20일 16시까지 내 주세요. 급합니다.\n\n(보낸 사람: 홍길동)")
        self.notify(a)
        texts = self.texts()
        summary = [t for t in texts if t.startswith("🤖")]
        self.assertEqual(len(summary), 1)
        self.assertFalse([t for t in texts if t.startswith("📥")])   # 0.6.12 같은 쪽지를 알림 목록에 또 싣지 않음
        s = summary[0]
        for label in ("보낸 사람: 홍길동", "업무 내용:", "마감 기한: 11월 20일(금) 16시", "첨부파일: 없음", "🚨 긴급 여부: 긴급"):
            self.assertIn(label, s)
        self.assertNotIn("최별빛", self.lm.requests[-1]["messages"][1]["content"])   # AI 에는 이름을 가려 보냄
        self.assertNotIn("급합니다", s)                                             # 원문은 보내지 않음
        last = [m for m in self.fake.sent if m.get("text", "").startswith("🤖")][-1]
        self.assertIn("o:", str(last.get("reply_markup")))                         # [원문 보기] 단추
        row = self.env.db.one("SELECT ai_summary FROM items WHERE id = ?", (a,))
        self.assertIn("업무 내용", row["ai_summary"])                               # 수신함에도 같은 요약이 남음

    def test_real_attachments_fill_files_and_add_pdf_button(self):
        """0.6.17 AI 가 '첨부파일: 없음' 이라 해도 쿨메신저 첨부 목록 줄로 채우고 [📄 첨부 PDF] 단추를 붙임."""
        self.env.config.values.update(telegram_cool_send=True)
        a = self.item("체험학습 보고서를 11월 20일 16시까지 내 주세요. 급합니다. 양식은 첨부와 같습니다 아래를 봐 주세요.\n\n"
                      "(첨부 파일: 보고서 양식.hwp, 현장.jpg)\n\n(보낸 사람: 홍길동(홍길동))")
        self.notify(a)
        last = [m for m in self.fake.sent if m.get("text", "").startswith("🤖")][-1]
        self.assertIn("첨부파일: 보고서 양식.hwp, 현장.jpg", last["text"])
        self.assertIn(f"pdf:{a}", str(last.get("reply_markup")))

    def test_notice_lists_only_other_items_and_numbers_continue(self):
        """0.6.12 (선생님 2026-11-20 '비슷한 내용이 중복 전달'): 공문은 알림에, 쪽지는 요약 글에만 한 번씩."""
        e = self.item("공문 내용입니다. 11월 25일까지 제출 바랍니다.", title="연수 공문", source="edufine")
        c = self.item("회의 자료를 11월 21일까지 확인해 주세요.\n\n(보낸 사람: 홍길동)", title="회의 안내")
        self.notify(e, c)
        texts = self.texts()
        notice = [t for t in texts if t.startswith("📥")]
        self.assertEqual(len(notice), 1)
        self.assertIn("새 자료 1건", notice[0])
        self.assertIn("연수 공문", notice[0])
        self.assertNotIn("회의 안내", notice[0])
        self.assertIn("AI 요약으로 따로", notice[0])
        summary = [t for t in texts if t.startswith("🤖")]
        self.assertEqual(len(summary), 1)
        self.assertIn("쪽지 2번", summary[0])                                      # 알림의 1번 다음 번호
        self.assertEqual(self.bot.ref_item(2), c)
        marks = [str(m.get("reply_markup")) for m in self.fake.sent]
        self.assertNotIn(f"o:{c}", marks[texts.index(notice[0])])                  # 쪽지 단추는 요약 글에만
        self.assertIn(f"rg:{c}", marks[texts.index(summary[0])])                   # 일정 등록 단추도 요약 글에

    def test_notice_keeps_memos_when_summary_off(self):
        a = self.item("회의 자료를 확인해 주세요.", title="회의 안내")
        self.env.config.values["telegram_cool_summary"] = False
        self.notify(a)
        notice = [t for t in self.texts() if t.startswith("📥")]
        self.assertEqual(len(notice), 1)
        self.assertIn("회의 안내", notice[0])

    def test_summary_only_even_with_original_forwarding(self):
        """0.6.16 (선생님 2026-11-20 '여전히 요약과 원문이 같이 와'): 요약이 가면 요약만, 원문은 [원문 보기] 로."""
        self.env.config.values["telegram_cool_text"] = True
        body = "1교무실에 핫도그가 배달되어 왔습니다~~~\n\n45분까지 오세요" + " 자세한 안내입니다." * 10 + "\n\n(보낸 사람: 민은영(민은영))"
        a = self.item(body, title="1교무실에 핫도그가 배달되어 왔습니다~~~")
        self.notify(a)
        texts = self.texts()
        self.assertEqual(len(texts), 1)
        self.assertTrue(texts[0].startswith("🤖"))
        self.assertIn("업무 내용:", texts[0])
        self.assertNotIn("📨", texts[0])
        self.assertNotIn("45분까지 오세요", texts[0])
        marks = str(self.fake.sent[0].get("reply_markup"))
        self.assertIn(f"o:{a}", marks)                                            # [원문 보기]
        self.assertIn(f"rp:{a}", marks)                                           # [↩ 답장 보내기]: '이름(이름)' 도 읽음

    def test_original_attached_when_summary_fails(self):
        self.env.config.values["telegram_cool_text"] = True
        body = "회의 자료를 확인해 주세요." + " 안건은 붙임과 같습니다." * 8 + "\n\n(보낸 사람: 홍길동)"
        a = self.item(body, title="회의 자료를 확인해 주세요.")
        self.lm.close()
        self.notify(a)
        texts = self.texts()
        self.assertEqual(len(texts), 1)
        self.assertIn("AI 요약 없음", texts[0])
        self.assertIn("📨 원문\n" + body, texts[0])

    def test_short_memo_goes_as_one_original_without_ai(self):
        """0.6.16 (선생님 '요약 조건에 내용 중복 검토'): 짧은 쪽지는 요약해도 원문과 같으니 원문 한 글 (원문 전달을 켠 경우만)."""
        self.env.config.values["telegram_cool_text"] = True
        a = self.item("핫도그가 1교무실에 왔습니다~\n\n(보낸 사람: 민은영(민은영))", title="핫도그가 1교무실에 왔습니다~")
        self.notify(a)
        texts = self.texts()
        self.assertEqual(texts, ["📨 쿨메신저 쪽지 1번 · 👤 민은영\n핫도그가 1교무실에 왔습니다~"])
        self.assertEqual(self.lm.requests, [])                                    # AI 를 부르지 않음
        self.assertIn(f"rp:{a}", str(self.fake.sent[0].get("reply_markup")))

    def test_short_memo_still_summarized_when_original_forwarding_off(self):
        a = self.item("핫도그가 1교무실에 왔습니다~\n\n(보낸 사람: 민은영)", title="핫도그")
        self.notify(a)
        texts = self.texts()
        self.assertEqual(len(texts), 1)
        self.assertTrue(texts[0].startswith("🤖 쿨메신저 쪽지 1번\n👤"))          # 머리줄에 제목을 되풀이하지 않음
        self.assertNotIn("핫도그가 1교무실", texts[0])                              # 원문은 보내지 않음 (원문 규칙)

    def test_reply_target_reads_name_with_parentheses(self):
        a = self.item("확인 부탁드립니다.\n\n(보낸 사람: 민은영(민은영))", title="확인")
        it, sender, err = self.bot.cool_reply_target(a)
        self.assertIsNone(err)
        self.assertEqual(sender, "민은영")

    def test_original_forwarding_head_does_not_repeat_title(self):
        self.env.config.values["telegram_cool_text"] = True
        self.env.config.values["telegram_cool_summary"] = False
        a = self.item("회의 안내입니다.\n3시에 과학실", title="회의 안내입니다.")
        self.notify(a)
        orig = [t for t in self.texts() if t.startswith("📨")]
        self.assertEqual(len(orig), 1)
        self.assertTrue(orig[0].startswith("📨 쿨메신저 쪽지 원문\n\n회의 안내입니다."))

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
