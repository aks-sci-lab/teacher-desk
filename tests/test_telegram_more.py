import unittest
from datetime import date, datetime

from desk import assist
from desk.collect import add_text_item
from desk.telegram import Bot, masked
from tests.fake_telegram import FakeTelegram
from tests.test_ai_register import FakeLMStudio
from tests.test_collect import Env

CHAT = 5


def msg(text, chat=CHAT):
    return {"message": {"text": text, "chat": {"id": chat, "type": "private"}}}


def press(data, chat=CHAT, message_id=1):
    return {"callback_query": {"id": "q1", "data": data, "from": {"id": chat},
                               "message": {"message_id": message_id, "chat": {"id": chat, "type": "private"}}}}


class TelegramMoreTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.env.db.set_meta("telegram_chat_id", CHAT)
        self.env.db.replace_students([{"number": 7, "name": "김민준"}, {"number": 12, "name": "이서연"}])

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def sent_text(self):
        return "\n".join(m["text"] for m in self.fake.sent) + "\n".join(m["text"] for m in self.fake.edited)

    def test_issue_pass_by_number_and_button(self):
        self.assertEqual(masked("김민준"), "김○○")
        reply = self.bot.handle_update(msg("조퇴 7 13:30"))
        self.assertIn("7번 김○○ 조퇴증 13:30", reply)
        buttons = self.fake.sent[-1]["reply_markup"]["inline_keyboard"]
        self.assertEqual(buttons[0][0]["text"], "병원 진료")
        data = buttons[0][0]["callback_data"]
        self.assertIsNone(self.bot.handle_update(press(data, chat=6)))     # 다른 사람의 단추는 무시
        done = self.bot.handle_update(press(data))
        self.assertIn("발급 번호", done)
        p = self.env.db.one("SELECT * FROM passes")
        self.assertEqual((p["student_name"], p["student_number"], p["kind"], p["time_out"], p["reason"]),
                         ("김민준", 7, "early", "13:30", "병원 진료"))
        self.assertIn(p["serial"], self.fake.edited[-1]["text"])
        # 학생 이름·사유는 텔레그램으로 나가지 않음
        self.assertNotIn("김민준", self.sent_text())
        self.assertNotIn("병원 진료", self.fake.edited[-1]["text"])
        print_choice = self.fake.edited[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        self.assertIn("PC에서 인쇄 요청", self.fake.edited[-1]["reply_markup"]["inline_keyboard"][0][0]["text"])
        requested = self.bot.handle_update(press(print_choice))
        self.assertIn("인쇄 요청", requested)
        self.assertEqual(self.env.db.get_meta("telegram_print_queue"), [p["id"]])
        self.assertNotIn("김민준", self.sent_text())
        self.assertNotIn("병원 진료", self.sent_text())
        self.assertIn("이미 처리", self.bot.handle_update(press(data)))     # 두 번 누르면

    def test_natural_pass_and_cancel(self):
        reply = self.bot.handle_update(msg("12번 학생 외출 2시~3시"))
        self.assertIn("12번 이○○ 외출증 14:00~15:00", reply)
        cancel = self.fake.sent[-1]["reply_markup"]["inline_keyboard"][-1][1]["callback_data"]
        self.assertIn("취소", self.bot.handle_update(press(cancel)))
        self.assertIsNone(self.env.db.one("SELECT * FROM passes"))
        self.assertIn("명단에 3번이 없습니다", self.bot.handle_update(msg("조퇴 3")))
        self.assertIn("번호를 함께", self.bot.handle_update(msg("/early")))
        # 시각이 없으면 지금(5분 올림), 외출 복귀는 1시간 뒤
        self.bot.start_pass(CHAT, "out", ["7"], now=datetime(2026, 10, 28, 9, 2))
        self.assertIn("09:05~10:05", self.fake.sent[-1]["text"])

    def test_pass_can_be_issued_without_print_request(self):
        self.bot.start_pass(CHAT, "out", ["12"], now=datetime(2026, 10, 29, 9, 2))
        buttons = self.fake.sent[-1]["reply_markup"]["inline_keyboard"]
        issued = self.bot.handle_update(press(buttons[-1][0]["callback_data"]))
        self.assertIn("발급 번호", issued)
        choice = self.fake.edited[-1]["reply_markup"]["inline_keyboard"][0][1]["callback_data"]
        result = self.bot.handle_update(press(choice))
        self.assertIn("인쇄하지 않음", result)
        self.assertEqual(self.env.db.get_meta("telegram_print_queue", []), [])

    def test_brief_search_and_originals(self):
        db = self.env.db
        pdf = self.env.edu / "공문.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        a = add_text_item(db, "edufine", "제목 연수 신청 안내\n신청: 11월 2일까지\n대상: 김민준 학생 지도 교사",
                          title="연수 신청 안내", received_at="2026-10-28 09:00:00", file_path=str(pdf))["id"]
        b = add_text_item(db, "cool", "학업성적관리위원회 11월 4일 15:30 교무실", title="성적관리위원회",
                          received_at="2026-10-28 10:00:00")["id"]
        # ai.weekly_summary 가 저장하는 모양 그대로 (0.5.15 에서는 {"result": …} 로 잘못 읽어 브리핑에 요약이 빠졌음)
        db.set_meta("ai_weekly", {"at": "2026-10-28 11:00", "ids": [b, a], "text":
            "## 꼭 할 일 (마감 순)\n- 11/02(월) · 연수 신청 [2]\n- 상시 · 지운 줄 [1]\n## 다가오는 행사·회의\n- 11/04(수) 15:30 · 성적관리위원회 [1]"})
        db.set_meta("ai_hidden", ["todo|상시|지운줄"])
        text = self.bot.brief_text(date(2026, 10, 28))
        self.assertIn("■ 꼭 할 일", text)
        self.assertIn("- 11/02(월) · 연수 신청 [1]", text)        # 이 메시지 안에서 번호를 다시 매김
        self.assertIn("- 11/04(수) 15:30 · 성적관리위원회 [2]", text)
        self.assertNotIn("지운 줄", text)
        # 원문·파일은 달라고 할 때만
        self.bot.handle_update(msg("원문 1"))
        self.assertIn("대상: 김민준 학생 지도 교사", self.fake.sent[-1]["text"])   # 원문은 그대로 (사용자 결정)
        self.bot.handle_update(msg("파일 1"))
        self.assertIn(b"%PDF-1.4 fake", self.fake.docs[-1])
        self.assertIn("원본 파일이 없습니다", self.bot.handle_update(msg("파일 2")))
        # 검색: 제목·요약 + 단추
        self.bot.handle_update(msg("검색 연수"))
        last = self.fake.sent[-1]
        self.assertIn("연수 신청 안내", last["text"])
        self.assertEqual([b["text"] for b in last["reply_markup"]["inline_keyboard"][0]], ["1 원문", "1 파일"])
        self.bot.handle_update(press(f"o:{a}"))
        self.assertIn("신청: 11월 2일까지", self.fake.sent[-1]["text"])
        # AI 를 안 쓰면 질문에는 찾은 자료만
        self.bot.handle_update(msg("성적관리위원회 언제야?"))
        self.assertIn("성적관리위원회", self.fake.sent[-1]["text"])
        self.assertIn("AI 를 켜면", self.fake.sent[-1]["text"])

    def test_question_answered_by_local_ai(self):
        lm = FakeLMStudio()
        try:
            self.env.config.values.update(ai_enabled=True, ai_base=lm.base)
            db = self.env.db
            add_text_item(db, "cool", "학업성적관리위원회를 11월 4일(수) 15:30 교무실에서 엽니다. 김민준 학생 성적 이의 신청 건 포함",
                          title="성적관리위원회 개최", received_at="2026-10-28 10:00:00")
            add_text_item(db, "cool", "급식 메뉴 변경", title="급식", received_at="2026-10-28 10:00:00")
            lm.reply = "11/4(수) 15:30 교무실에서 열립니다 [1]\n할 일: 이의 신청 건 검토 (김민준) [1]"
            self.bot.handle_update(msg("학업성적관리위원회 언제 열려?"))
            req = lm.requests[-1]
            self.assertEqual(req["messages"][0]["content"], assist.SYSTEM_QA)
            prompt = req["messages"][1]["content"]
            self.assertIn("성적관리위원회 개최", prompt)
            self.assertNotIn("급식", prompt)                      # 맞는 자료만
            self.assertNotIn("김민준", prompt)                    # 학생 이름은 가림
            last = self.fake.sent[-1]
            self.assertIn("11/4(수) 15:30", last["text"])
            self.assertNotIn("김민준", last["text"])              # AI 답에 이름이 나와도 가림
            self.assertEqual(last["reply_markup"]["inline_keyboard"][0][0]["text"], "1 원문")
            self.assertIsNone(self.bot.asking)
        finally:
            lm.close()

    def test_intent_and_period(self):
        self.assertEqual(assist.intent("7번 조퇴 1시 30분"), ("early", ["7", "13:30"]))
        self.assertEqual(assist.intent("외출 12번 10시"), ("out", ["12", "10:00"]))
        self.assertEqual(assist.intent("3번 원문"), ("text", ["3"]))
        self.assertEqual(assist.intent("브리핑 보여줘"), ("brief", []))
        self.assertIsNone(assist.intent("조퇴증 양식이 바뀌었나요"))
        self.assertEqual(assist.keywords("다음 주 연수 일정 알려줘"), ["연수"])
        wed = date(2026, 10, 28)
        self.assertEqual(assist.period("이번 주 마감", wed), (wed, date(2026, 11, 1)))
        self.assertEqual(assist.period("다음 주", wed), (date(2026, 11, 2), date(2026, 11, 8)))
        self.assertEqual(assist.period("내일 뭐 있어", wed), (date(2026, 10, 29),) * 2)


if __name__ == "__main__":
    unittest.main()


class BriefMergeTests(unittest.TestCase):
    def test_updates_merge_into_one_summary(self):
        env = Env()
        try:
            db = env.db
            db.set_meta("ai_weekly", {"at": "2026-10-29 08:00", "ids": [11, 12], "text":
                "## 꼭 할 일\n- 11/06(금) · 계획서 제출 [1]\n## 알아 둘 내용\n- 상시 · 급식 메뉴 변경 [2]",
                "updates": [
                    {"at": "2026-10-29 10:38", "ids": [13], "text": "## 꼭 할 일\n- 11/02(월) · 연수 신청 [1]\n- 11/06(금) · 계획서 제출 [1]"},
                    {"at": "2026-10-29 10:20", "ids": [14], "text": "## 알아 둘 내용\n- 상시 · 지운 줄 [1]"},
                ]})
            db.set_meta("ai_hidden", ["note|상시|지운줄"])
            text = Bot(db, env.config).brief_text(date(2026, 10, 29))
            self.assertEqual(text.count("━━"), 2)                          # 머리는 하나 (예전엔 묶음마다)
            self.assertIn("(+새 자료 2번)", text)
            todo = text[text.index("■ 꼭 할 일"):text.index("■ 알아 둘 내용")]
            self.assertLess(todo.index("연수 신청"), todo.index("계획서 제출"))   # 날짜 순으로 합침
            self.assertEqual(text.count("계획서 제출"), 1)                     # 같은 줄은 하나만
            self.assertIn("- (새) 11/02(월) · 연수 신청 [1]", text)
            self.assertNotIn("지운 줄", text)
            # 다 지웠으면 빈 머리 대신 한 줄
            db.set_meta("ai_hidden", ["todo|11/06(금)|계획서제출", "todo|11/02(월)|연수신청", "note|상시|급식메뉴변경", "note|상시|지운줄"])
            self.assertIn("(AI 요약은 모두 지웠습니다)", Bot(db, env.config).brief_text(date(2026, 10, 29)))
        finally:
            env.close()
