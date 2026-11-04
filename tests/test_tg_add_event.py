import time
import unittest
from datetime import date, timedelta

from desk import queries, schoolcal
from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_telegram_more import CHAT, msg, press


class TelegramAddEventTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.env.db.set_meta("telegram_chat_id", CHAT)
        self.env.db.replace_students([{"number": 7, "name": "한가람"}])
        self.day = date.today() + timedelta(days=2)

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def events(self):
        return self.env.db.query("SELECT * FROM events ORDER BY id")

    def buttons(self, message):
        return {b["text"]: b["callback_data"] for row in message["reply_markup"]["inline_keyboard"] for b in row}

    def start(self, text="일정 추가"):
        self.bot.handle_update(msg(text))
        return self.bot.dialog["id"]

    def pick(self, did, day=None):
        self.bot.handle_update(press(f"dlg:{did}:cd:{(day or self.day):%Y%m%d}"))

    def all_text(self):
        return "\n".join(m["text"] for m in self.fake.sent + self.fake.edited)

    def test_full_flow_start_month_date_text_confirm_register_then_undo(self):
        did = self.start()
        calendar = self.fake.sent[-1]
        self.assertIn("날짜를 누르세요", calendar["text"])
        rows = calendar["reply_markup"]["inline_keyboard"]
        self.assertEqual(rows[0][1]["text"], f"{date.today().year}년 {date.today().month}월")
        self.assertTrue(all(len(b["callback_data"].encode()) <= 64 for r in rows for b in r))
        sent_before = len(self.fake.sent)
        self.bot.handle_update(press(rows[0][2]["callback_data"]))                 # ▶ 다음 달
        self.assertEqual(len(self.fake.sent), sent_before)                           # 새 메시지 없이 같은 메시지를 고침
        self.assertNotEqual(self.fake.edited[-1]["reply_markup"]["inline_keyboard"][0][1]["text"], rows[0][1]["text"])
        self.pick(did)
        self.assertIn("일정 내용을 글 한 줄로", self.fake.edited[-1]["text"])
        self.assertEqual(self.bot.dialog["step"], "etext")
        self.bot.handle_update(msg("학년 협의회"))
        confirm = self.fake.sent[-1]
        self.assertIn(f"{self.day.month}/{self.day.day}", confirm["text"])
        self.assertIn("학년 협의회 — 등록할까요?", confirm["text"])
        self.assertEqual(set(self.buttons(confirm)), {"등록", "마감으로 등록", "날짜 바꾸기", "취소"})
        self.assertEqual(self.events(), [])                                          # 확인 전에는 저장하지 않음
        self.bot.handle_update(press(self.buttons(confirm)["등록"]))
        row = self.events()[0]
        self.assertEqual((row["date"], row["kind"], row["text"], row["item_id"]), (self.day.isoformat(), "event", "학년 협의회", None))
        self.assertIn("등록했습니다", self.fake.edited[-1]["text"])
        self.assertEqual(self.buttons(self.fake.edited[-1]), {"방금 등록 취소": f"evu:{row['id']}"})
        # 웹 화면(달력·브리핑)이 읽는 같은 질의에 바로 나옴
        shown = queries.events_between(self.env.db, self.day, self.day)
        self.assertEqual([e["text"] for e in shown], ["학년 협의회"])
        self.assertIn("오래된", self.bot.handle_update(press(self.buttons(confirm)["등록"])))   # 두 번 눌러도 한 번만
        self.assertEqual(len(self.events()), 1)
        undo = f"evu:{row['id']}"
        self.assertIsNone(self.bot.handle_update(press(undo, chat=6)))
        self.bot.handle_update(press(undo))
        self.assertEqual(self.events(), [])
        self.assertIn("취소했습니다", self.fake.edited[-1]["text"])
        self.bot.handle_update(press(undo))                                          # 이미 취소한 뒤 다시 눌러도 안전
        self.assertIn("이미 취소했거나", self.fake.edited[-1]["text"])

    def add_from_telegram(self, text, day=None):
        did = self.start()
        self.pick(did, day)
        self.bot.handle_update(msg(text))
        self.bot.handle_update(press(self.buttons(self.fake.sent[-1])["등록"]))
        return self.events()[-1]

    def test_undo_does_not_delete_a_new_event_that_reused_the_number(self):
        """화면에서 지운 번호를 새 일정이 이어받은 뒤 옛 [방금 등록 취소] 를 눌러도 새 일정이 지워지면 안 됨."""
        row = self.add_from_telegram("학년 협의회")
        undo = f"evu:{row['id']}"
        self.env.db.execute("DELETE FROM events WHERE id = ?", (row["id"],))          # 선생님이 화면에서 지움
        new = queries.insert_event(self.env.db, {"date": self.day.isoformat(), "kind": "event", "text": "교직원 회의", "category": "참고"})
        self.assertEqual(new["id"], row["id"])                                        # 같은 번호를 이어받음 (재현 조건)
        self.bot.handle_update(press(undo))
        self.assertEqual([e["text"] for e in self.events()], ["교직원 회의"])            # 화면에서 만든 새 일정은 그대로
        self.assertIn("이미 지워졌거나 바뀐 일정", self.fake.edited[-1]["text"])
        self.bot.handle_update(press(undo))                                           # 목록에서 빠져 다시 눌러도 안전
        self.assertEqual(len(self.events()), 1)
        self.assertIn("이미 취소했거나", self.fake.edited[-1]["text"])

    def test_undo_checks_date_too_and_long_text_matches_by_first_twenty_chars(self):
        long_text = "  학년   협의회   및   교과   협의회   일정   조율   안건   정리  "
        row = self.add_from_telegram(long_text)
        entry = self.env.db.get_meta("telegram_added_events")[0]
        self.assertEqual(set(entry), {"id", "date", "key"})
        self.assertEqual(len(entry["key"]), 20)
        self.assertNotIn("정리", entry["key"])                                          # 앞 20자만 저장 (meta 는 이 PC 안)
        self.env.db.execute("UPDATE events SET date = ? WHERE id = ?", ((self.day + timedelta(days=1)).isoformat(), row["id"]))
        self.bot.handle_update(press(f"evu:{row['id']}"))                            # 날짜가 바뀌었으면 지우지 않음
        self.assertEqual(len(self.events()), 1)
        self.assertIn("바뀐 일정", self.fake.edited[-1]["text"])
        row2 = self.add_from_telegram(long_text, self.day + timedelta(days=3))        # 정상 취소: 띄어쓰기가 달라도 같은 일정
        self.bot.handle_update(press(f"evu:{row2['id']}"))
        self.assertEqual([e["id"] for e in self.events()], [row["id"]])
        self.assertIn("취소했습니다", self.fake.edited[-1]["text"])

    def test_old_format_number_only_list_is_read_without_error_and_not_deleted(self):
        row = queries.insert_event(self.env.db, {"date": self.day.isoformat(), "kind": "event", "text": "예전 등록", "category": "참고"})
        self.env.db.set_meta("telegram_added_events", [row["id"], 999])               # 예전 형식 (번호만)
        self.bot.handle_update(press(f"evu:{row['id']}"))
        self.assertEqual(len(self.events()), 1)
        self.assertIn("확인할 수 없어 취소하지 않았습니다", self.fake.edited[-1]["text"])
        self.assertEqual(self.env.db.get_meta("telegram_added_events"), [999])
        self.add_from_telegram("새로 등록")                                              # 새 형식과 섞여 있어도 동작
        self.assertEqual(len(self.env.db.get_meta("telegram_added_events")), 2)

    def test_register_as_deadline_and_it_appears_in_deadlines(self):
        did = self.start()
        self.pick(did)
        self.bot.handle_update(msg("연수 신청서 제출"))
        self.bot.handle_update(press(self.buttons(self.fake.sent[-1])["마감으로 등록"]))
        row = self.events()[0]
        self.assertEqual((row["kind"], row["category"]), ("deadline", "제출·마감"))
        self.assertIn("연수 신청서 제출", self.bot.answer("deadlines"))
        self.assertIn("(마감)", self.fake.edited[-1]["text"])

    def test_undo_only_removes_events_added_from_telegram(self):
        web = queries.insert_event(self.env.db, {"date": self.day.isoformat(), "kind": "event", "text": "웹에서 넣음", "category": "참고"})
        self.bot.handle_update(press(f"evu:{web['id']}"))
        self.assertEqual(len(self.events()), 1)                                      # 화면에서 만든 일정은 텔레그램으로 못 지움
        self.assertIn("이미 취소했거나", self.fake.edited[-1]["text"])

    def test_student_name_is_masked_in_replies_but_saved_as_typed(self):
        did = self.start()
        self.pick(did)
        self.bot.handle_update(msg("한가람 학부모 상담"))
        self.bot.handle_update(press(self.buttons(self.fake.sent[-1])["등록"]))
        self.assertEqual(self.events()[0]["text"], "한가람 학부모 상담")                # 저장은 그대로
        self.assertNotIn("한가람", self.all_text())                                   # 텔레그램 글은 모두 가림
        self.assertIn("○○○ 학부모 상담", self.all_text())          # 이름 자리는 ○ 로 가려짐
        self.bot.handle_update(press(f"evu:{self.events()[0]['id']}"))
        self.assertNotIn("한가람", self.all_text())

    def test_duplicate_same_day_same_text_is_warned_but_can_still_register(self):
        queries.insert_event(self.env.db, {"date": self.day.isoformat(), "kind": "event", "text": "학년 협의회", "category": "참고"})
        did = self.start()
        self.pick(did)
        self.bot.handle_update(msg("학년 협의회"))
        confirm = self.fake.sent[-1]
        self.assertIn("이미 있습니다", confirm["text"])
        self.assertIn("그래도 등록", self.buttons(confirm))
        self.bot.handle_update(press(self.buttons(confirm)["그래도 등록"]))
        self.assertEqual(len(self.events()), 2)

    def test_text_rules_and_wrong_step(self):
        for bad in ("", "x" * 101, "/start", "오늘", "검색", "일정 추가", "일정 추가 회의"):
            self.assertTrue(Bot.event_text_error(bad), bad)
        for good in ("오늘 학년 회의", "조퇴 학생 면담", "x" * 100, "일정 안내"):
            self.assertIsNone(Bot.event_text_error(good), good)
        did = self.start()
        self.assertIn("달력에서", self.bot.handle_update(msg("회의")))                  # 날짜 고르기 전의 글
        self.assertIn("현재 단계", self.bot.handle_update(press(f"dlg:{did}:reg")))      # 단계가 아닌 단추
        self.pick(did)
        self.assertIn("너무 깁니다", self.bot.handle_update(msg("가" * 101)))
        self.assertEqual(self.bot.dialog["step"], "etext")
        self.assertIn("명령어", Bot.event_text_error("검색"))
        self.bot.handle_update(msg("회의"))
        self.assertIn("[등록]", self.bot.handle_update(msg("또 글")))                   # 확인 단계의 글
        self.bot.handle_update(press(f"dlg:{did}:ed"))                                # 날짜 바꾸기 → 다시 달력
        self.assertEqual(self.bot.dialog["step"], "edate")

    def test_cancel_stale_button_and_ten_minute_expiry(self):
        did = self.start()
        self.bot.handle_update(msg("취소"))
        self.assertIsNone(self.bot.dialog)
        self.assertIn("오래된", self.bot.handle_update(press(f"dlg:{did}:cd:{self.day:%Y%m%d}")))
        did = self.start()
        self.bot.dialog["expires"] = time.monotonic() - 1
        self.assertIn("10분", self.bot.handle_update(press(f"dlg:{did}:cd:{self.day:%Y%m%d}")))
        did = self.start()
        self.pick(did)
        self.bot.dialog["expires"] = time.monotonic() - 1
        self.assertIn("10분", self.bot.handle_update(msg("회의")))
        self.assertEqual(self.events(), [])

    def test_only_starts_with_add_event_and_does_not_steal_other_commands(self):
        for text in ("일정", "일정 추가해줘", "내일 일정 추가", "오늘 식단표", "일정 알려줘", "이번 주 일정"):
            self.bot.handle_update(msg(text))
            self.assertIsNone(self.bot.dialog, text)
        self.assertIn("업무", self.bot.handle_update(msg("오늘")))                       # 기존 조회 명령은 그대로
        self.assertIn("업무", self.bot.handle_update(msg("오늘 일정")))
        self.assertIsNone(self.bot.dialog)
        for text in ("일정 추가", "일정추가", "/addevent", "/addevent@desk_bot"):
            self.bot.dialog = None
            self.start(text)
            self.assertEqual(self.bot.dialog["kind"], "event", text)

    def test_menu_button_and_preset_text_and_bad_preset(self):
        self.bot.send_menu(CHAT)
        menu = next(m for m in reversed(self.fake.sent) if "inline_keyboard" in m.get("reply_markup", {}))
        self.bot.handle_update(press(self.buttons(menu)["일정 추가"]))                  # 메뉴 단추로 시작
        self.assertEqual(self.bot.dialog["kind"], "event")
        did = self.start("일정 추가 학년 협의회")
        self.assertIn("내용: 학년 협의회", self.fake.sent[-1]["text"])
        self.pick(did)                                                                # 내용이 이미 있으면 바로 확인
        self.assertIn("학년 협의회 — 등록할까요?", self.fake.edited[-1]["text"])
        self.bot.dialog = None
        self.assertIn("명령어", self.bot.handle_update(msg("일정 추가 /today")))
        self.assertIsNone(self.bot.dialog)

    def test_event_calendar_does_not_mark_holidays_yet_and_label_is_in_one_place(self):
        d = date(2026, 11, 10)
        schoolcal.save(self.env.db, 2026, [{"date": "2026-11-10", "end_date": None, "text": "재량휴업일", "kind": "event", "category": "학사"}], "나이스")
        self.assertEqual((Bot.day_label(d, None, set()), Bot.day_label(d, d, set()), Bot.day_label(d, None, {d})), ("10", "▶10", "(10)"))
        did = self.start()
        self.bot.handle_update(press(f"dlg:{did}:cm:202611"))
        labels = [b["text"] for row in self.fake.edited[-1]["reply_markup"]["inline_keyboard"][2:-1] for b in row]
        self.assertIn("10", labels)
        self.assertFalse(any(label.startswith("(") for label in labels))            # 공휴일·휴업일 표시는 아직 넣지 않음
        self.bot.dialog = None
        self.bot.handle_update(msg("결석계"))                                          # 결석계 달력은 예전 그대로 (휴업일 표시)
        self.bot.handle_update(msg("7"))
        self.bot.handle_update(press(self.fake.sent[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]))
        self.bot.handle_update(press(f"dlg:{self.bot.dialog['id']}:cm:202611"))
        labels = [b["text"] for row in self.fake.edited[-1]["reply_markup"]["inline_keyboard"] for b in row]
        self.assertIn("(10)", labels)


if __name__ == "__main__":
    unittest.main()
