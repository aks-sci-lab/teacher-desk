import base64
import json
import unittest
from datetime import date, datetime
from unittest.mock import patch

from desk import assist, printing, schoolcal
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
        self.assertIn("기본 프린터로 인쇄", self.fake.edited[-1]["reply_markup"]["inline_keyboard"][0][0]["text"])
        with patch("desk.telegram.printing.start_silent_pass", return_value=True) as start_print:
            requested = self.bot.handle_update(press(print_choice))
        self.assertIn("기본 프린터", requested)
        start_print.assert_called_once_with(self.env.config, p["id"])
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
        choice = self.fake.edited[-1]["reply_markup"]["inline_keyboard"][1][1]["callback_data"]
        self.assertTrue(choice.endswith(":0"))
        result = self.bot.handle_update(press(choice))
        self.assertIn("발급 완료", result)
        self.assertEqual(self.env.db.get_meta("telegram_print_queue", []), [])

    def issue_and_buttons(self):
        self.bot.start_pass(CHAT, "early", ["7", "13:30"], now=datetime(2026, 10, 30, 9, 2))
        first = self.fake.sent[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        self.bot.handle_update(press(first))
        return {b["text"]: b["callback_data"] for row in self.fake.edited[-1]["reply_markup"]["inline_keyboard"] for b in row}

    def test_pass_web_link_choice(self):
        """웹 링크 받기 (사용자 결정 2026-10-30): 이름은 들어가고 사유는 빠짐. 인쇄는 요청하지 않음."""
        buttons = self.issue_and_buttons()
        self.assertEqual(set(buttons), {"🖨 기본 프린터로 인쇄", "🔗 웹 링크 받기", "인쇄 + 웹 링크", "아무것도 안 함"})
        with patch("desk.telegram.printing.start_silent_pass") as start_print:
            self.bot.handle_update(press(buttons["🔗 웹 링크 받기"]))
            start_print.assert_not_called()
        self.assertEqual(printing.queue(self.env.db), [])
        link = self.fake.sent[-1]["text"]
        self.assertIn("https://aks-sci-lab.github.io/teacher-desk/#", link)
        self.assertIn("학생에게만 전달", link)
        data = json.loads(base64.urlsafe_b64decode(link.split("#")[1].split()[0] + "==").decode("utf-8"))
        self.assertEqual((data["nm"], data["no"], data["d"], data["t"], data["k"]), ("김민준", 7, "2026-10-30", "13:30", "e"))
        self.assertNotIn("why", data)   # 사유는 링크에 넣지 않음
        self.assertNotIn("병원", link)
        self.assertNotIn("김민준", self.fake.edited[-1]["text"])   # 이름은 링크 안에만

    def test_pass_print_and_link_both_and_failure(self):
        buttons = self.issue_and_buttons()
        with patch("desk.telegram.printing.start_silent_pass", return_value=True) as start_print:
            result = self.bot.handle_update(press(buttons["인쇄 + 웹 링크"]))
        start_print.assert_called_once()
        self.assertIn("기본 프린터", result)
        self.assertIn("링크", result)
        self.assertIn("#", self.fake.sent[-1]["text"])
        # 인쇄를 시작하지 못하면 대기열에서 빠져 나중에 되살아나지 않음
        buttons = self.issue_and_buttons()
        with patch("desk.telegram.printing.start_silent_pass", side_effect=printing.PrintError("에지 없음")):
            result = self.bot.handle_update(press(buttons["🖨 기본 프린터로 인쇄"]))
        self.assertIn("시작하지 못했습니다", result)
        self.assertEqual(printing.queue(self.env.db), [1])   # 첫 증의 요청만 (둘째는 실패해서 빠짐)

    def test_pass_link_needs_share_page(self):
        self.env.config.values["pass_page_url"] = ""
        buttons = self.issue_and_buttons()
        before = len(self.fake.sent)
        result = self.bot.handle_update(press(buttons["🔗 웹 링크 받기"]))
        self.assertIn("링크를 만들지 못했습니다", result)
        self.assertEqual(len(self.fake.sent), before)

    def test_print_requests_expire(self):
        """인쇄 요청은 10분 안에 처리되지 않으면 버림 (실패한 요청이 다음 날 다시 나오던 것)."""
        printing.enqueue(self.env.db, 5, now=1000)
        self.assertEqual(printing.queue(self.env.db, now=1000 + 599), [5])
        self.assertEqual(printing.queue(self.env.db, now=1000 + 601), [])
        self.assertEqual(self.env.db.get_meta("telegram_print_queue"), [])
        self.env.db.set_meta("telegram_print_queue", [9])   # 시각 없는 예전 요청
        self.assertEqual(printing.queue(self.env.db), [])

    def test_absence_from_telegram_is_saved_without_printing_or_echoing_personal_details(self):
        command = "결석계 김민준 7 미인정결 2026-10-01~2026-10-09"
        result = self.bot.handle_update(msg(command))
        self.assertIn("학사일정", result)
        self.assertIsNone(self.env.db.one("SELECT * FROM absences"))
        schoolcal.save(self.env.db, 2026, [
            {"date": "2026-10-05", "end_date": None, "text": "대체공휴일", "kind": "event", "category": "학사"},
            {"date": "2026-10-09", "end_date": None, "text": "한글날", "kind": "event", "category": "학사"},
        ], "나이스")
        self.assertIsNone(self.bot.handle_update(msg(command, chat=6)))
        with patch("desk.telegram.printing.start_silent_pass") as print_pass:
            result = self.bot.handle_update(msg(command))
            print_pass.assert_not_called()
        self.assertIn("5일", result)
        saved = self.env.db.one("SELECT * FROM absences")
        self.assertEqual((saved["student_name"], saved["student_number"], saved["absence_type"]),
                         ("김민준", 7, "미인정"))
        self.assertNotIn("김민준", self.sent_text())
        self.assertNotIn("미인정결", result)
        self.assertEqual(self.env.db.get_meta("telegram_print_queue", []), [])
        self.assertIn("명단", self.bot.handle_update(msg("결석계 다른이름 7 병결 2026-10-01")))
        self.assertIn("수업일이 없습니다", self.bot.handle_update(msg("결석계 김민준 7 병결 2026-10-03~2026-10-04")))
        self.assertEqual(self.env.db.one("SELECT COUNT(*) AS n FROM absences")["n"], 1)

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
        self.assertEqual([b["text"] for b in last["reply_markup"]["inline_keyboard"][0]][:2], ["1 원문", "1 원본 파일 받기"])
        self.bot.handle_update(press(f"o:{a}"))
        self.assertIn("신청: 11월 2일까지", self.fake.sent[-1]["text"])
        # AI 를 안 쓰면 질문에는 찾은 자료만
        self.bot.handle_update(msg("성적관리위원회 언제야?"))
        self.bot.handle_update(press(self.fake.sent[-1]["reply_markup"]["inline_keyboard"][-1][0]["callback_data"]))
        self.assertIn("성적관리위원회", self.fake.sent[-1]["text"])
        self.assertIn("AI 를 켜면", self.fake.sent[-1]["text"])

    def test_notifications_summary_and_refresh_offer_original_only_on_request(self):
        from tests.xlsx_fixture import build_xlsx
        path = self.env.cool / "연수 첨부.xlsx"
        original = build_xlsx([("안내", [["연수 신청", "2099년 1월 2일까지"]], [])])
        path.write_bytes(original)
        stats = self.env.collector.run("manual")
        self.assertEqual(stats["items"], 1, stats)
        iid = stats["new_ids"][0]
        self.bot.notify_collect(stats, "schedule")
        notice = self.fake.sent[-1]
        self.assertIn("1. [쿨메신저]", notice["text"])
        self.assertEqual(notice["reply_markup"]["inline_keyboard"][0][1],
                         {"text": "1 원본 파일 받기", "callback_data": f"f:{iid}"})
        self.assertEqual(self.fake.docs, [])
        self.assertIsNone(self.bot.handle_update(press(f"f:{iid}", chat=6)))
        self.assertEqual(self.fake.docs, [])
        self.bot.handle_update(press(f"f:{iid}"))
        self.assertIn(original, self.fake.docs[-1])
        self.bot.handle_update(msg("요약"))
        self.assertEqual(self.fake.sent[-1]["reply_markup"]["inline_keyboard"][0][1]["callback_data"], f"f:{iid}")
        with patch.object(self.env.collector, "run", return_value=stats):
            self.bot.handle_update(msg("수집"))
        self.assertEqual(self.fake.sent[-1]["reply_markup"]["inline_keyboard"][0][1]["callback_data"], f"f:{iid}")
        self.assertEqual(self.bot.ref_item(1), iid)
        self.env.config.values["telegram_detail"] = "counts"
        self.bot.notify_collect(stats, "schedule")
        self.assertNotIn("연수", self.fake.sent[-1]["text"])
        self.assertNotIn("reply_markup", self.fake.sent[-1])
        self.bot.handle_update(msg("요약"))
        self.assertNotIn("reply_markup", self.fake.sent[-1])
        path.unlink()
        self.assertIn("원본 파일이 없습니다", self.bot.handle_update(press(f"f:{iid}")))

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
            self.assertEqual(lm.requests, [])
            self.bot.handle_update(press(self.fake.sent[-1]["reply_markup"]["inline_keyboard"][-1][0]["callback_data"]))
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

    def test_unknown_input_offers_commands_and_ai_without_running_it(self):
        for text in ("자료를 찾아줘", "/unknown"):
            with self.subTest(text=text), patch.object(self.bot, "ask") as ask:
                reply = self.bot.handle_update(msg(text))
                self.assertIn("업무 데스크 명령", reply)
                self.assertIn("AI가 꺼져", reply)
                buttons = self.fake.sent[-1]["reply_markup"]["inline_keyboard"]
                ai_choice = buttons[-1][0]["callback_data"]
                self.assertNotIn(text, ai_choice)
                ask.assert_not_called()
                self.assertIsNone(self.bot.handle_update(press(ai_choice, chat=6)))
                ask.assert_not_called()
                self.bot.handle_update(press(ai_choice))
                ask.assert_called_once_with(CHAT, text)
                self.assertIn("이미 처리", self.bot.handle_update(press(ai_choice)))
                ask.assert_called_once()

    def test_unknown_input_command_choice_and_old_requests(self):
        with patch.object(self.bot, "ask") as ask:
            self.bot.handle_update(msg("무엇을 할까"))
            buttons = self.fake.sent[-1]["reply_markup"]["inline_keyboard"]
            reply = self.bot.handle_update(press(buttons[0][1]["callback_data"]))
            self.assertIn("오늘 일정", reply)
            ask.assert_not_called()
            self.bot.handle_update(msg("다른 양식"))
            buttons = self.fake.sent[-1]["reply_markup"]["inline_keyboard"]
            self.assertIn("학생 번호", self.bot.handle_update(press(buttons[1][2]["callback_data"])))
            self.assertEqual(self.bot.dialog["kind"], "absence")
            self.bot.handle_update(msg("취소"))
            self.bot.handle_update(msg("처음 질문"))
            old = self.fake.sent[-1]["reply_markup"]["inline_keyboard"][-1][0]["callback_data"]
            for n in range(10):
                self.bot.handle_update(msg(f"질문 {n}"))
            self.assertEqual(len(self.bot.choice_requests), 10)
            self.assertIn("오래된 선택", self.bot.handle_update(press(old)))
            ask.assert_not_called()

    def test_ai_choice_is_retained_while_another_question_is_running(self):
        self.bot.handle_update(msg("질문 내용"))
        choice = self.fake.sent[-1]["reply_markup"]["inline_keyboard"][-1][0]["callback_data"]
        self.bot.asking = "다른 질문"
        with patch.object(self.bot, "ask") as ask:
            self.assertIn("답하는 중", self.bot.handle_update(press(choice)))
            ask.assert_not_called()
            self.bot.asking = None
            self.bot.handle_update(press(choice))
            ask.assert_called_once_with(CHAT, "질문 내용")
        self.bot.unpair()
        self.assertEqual(self.bot.choice_requests, {})

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
