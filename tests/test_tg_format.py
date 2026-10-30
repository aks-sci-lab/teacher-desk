import json
import unittest
import urllib.error
from datetime import date, timedelta
from unittest.mock import patch

from desk import extract
from desk.collect import add_text_item, rescan_events
from desk.telegram import BATCH_LIMIT, Bot, cut, sender_of
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env

CHAT = 5
TODAY = date(2026, 10, 30)


def msg(text, chat=CHAT):
    return {"message": {"text": text, "chat": {"id": chat, "type": "private"}}}


def press(data, chat=CHAT):
    return {"callback_query": {"id": "q1", "data": data, "from": {"id": chat},
                               "message": {"message_id": 1, "chat": {"id": chat, "type": "private"}}}}


class HelperTests(unittest.TestCase):
    def test_cut_and_sender(self):
        self.assertEqual(cut("짧은 제목", 40), "짧은 제목")
        self.assertEqual(cut("2027. 3. 1.자 개방형 및 내부형 교장공모제 공모 계획 안내입니다 자세한 내용", 20), "2027. 3. 1.자 개방형 및…")
        self.assertTrue(len(cut("가" * 100, 10)) <= 11)
        self.assertEqual(sender_of("안내드립니다\n\n(보낸 사람: 이창헌(이창헌))"), "이창헌")
        self.assertEqual(sender_of("본문\n\n(보낸 사람: 교무부)"), "교무부")
        self.assertEqual(sender_of("보낸 사람이 없는 글"), "")

    def test_ja_dates_are_not_events(self):
        """'2027. 3. 1.자 개방형 교장공모제'·'10월 30일자 공문' 의 날짜는 기준일이라 일정이 아님."""
        ref = date(2026, 10, 29)
        self.assertEqual(extract.extract_events("2027. 3. 1.자 개방형 및 내부형 교장공모제 공모 안내", ref), [])
        self.assertEqual(extract.extract_events("10월 30일자 공문을 참고하세요", ref), [])
        got = extract.extract_events("11월 3일 자료 제출", ref)       # '자료' 의 '자' 는 날짜 뒤 띄어쓰기가 있어 일정
        self.assertEqual([e["date"] for e in got], ["2026-11-03"])
        self.assertEqual(extract.event_context("2027. 3. 1.자 공모\n11월 3일까지 제출", "2027-03-01", ref), None)


class CollectNoticeTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.env.db.set_meta("telegram_chat_id", CHAT)
        self.env.db.replace_students([{"number": 7, "name": "김민준"}])

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def add(self, source, title, body, received="2026-10-30 09:00:00"):
        return add_text_item(self.env.db, source, body, title=title, received_at=received, ref=TODAY)["id"]

    def stats(self, ids):
        n = self.env.db.query(f"SELECT COUNT(*) AS n FROM events WHERE kind = 'deadline' AND item_id IN ({','.join('?' * len(ids))})", ids)[0]["n"]
        return {"items": len(ids), "events": n, "deadlines": n, "new_ids": ids}

    def test_small_batch_puts_deadlines_first_with_sender_and_what(self):
        a = self.add("cool", "헌혈 행사 주차 안내", "차량 등록 후 주차해 주세요\n11월 3일(화)까지 차량 등록\n\n(보낸 사람: 안광수(안광수))", "2026-10-30 08:00:00")
        b = self.add("cool", "대강 교실 안내", "대강 교실이 402호입니다.\n\n(보낸 사람: 교무부)", "2026-10-30 10:00:00")
        c = self.add("edufine", "학점제 운영 계획 안내", "운영 계획을 안내합니다. 협조 바랍니다.", "2026-10-30 07:00:00")
        text = self.bot.collect_message(self.stats([a, b, c]), TODAY)
        lines = text.split("\n")
        self.assertEqual(lines[0], "📥 새 자료 3건 (쿨메신저 2 · 에듀파인 1)")
        self.assertIn("1. [쿨메신저] 헌혈 행사 주차 안내 · 안광수", lines)       # 마감이 있는 것이 맨 위, 보낸 사람 포함
        self.assertTrue(any(l.startswith("   ⏰ 11/3(화)") and "(D-4)" in l and "차량 등록" in l for l in lines), text)
        self.assertLess(text.index("학점제"), text.index("대강 교실"))             # 그다음 에듀파인 공문, 맨 뒤 일반 쪽지
        self.assertNotIn("일정 후보", text)
        self.assertEqual(self.env.db.get_meta("telegram_refs"), [a, c, b])

    def test_big_batch_is_one_summary_with_only_near_deadlines(self):
        ids = [self.add("cool", f"쪽지 {i}", f"일상 안내 {i}번 쪽지입니다") for i in range(BATCH_LIMIT + 5)]
        near = self.add("cool", "연수 신청", "연수 신청은 11월 6일까지 해 주세요\n\n(보낸 사람: 연수부)")
        text = self.bot.collect_message(self.stats(ids + [near]), TODAY)
        lines = text.split("\n")
        self.assertEqual(lines[0], f"📥 새 자료 {len(ids) + 1}건 (쿨메신저 {len(ids) + 1})")
        self.assertIn("목록은 줄였습니다", text)
        self.assertEqual([l for l in lines if l[:2] in ("1.", "2.", "3.", "4.")], ["1. [쿨메신저] 연수 신청 · 연수부"])   # 마감 있는 것만
        self.assertNotIn("쪽지 3", text)

    def test_counts_only_and_student_names_masked(self):
        a = self.add("cool", "김민준 학생 상담 안내", "김민준 학생 상담이 11월 4일까지입니다\n\n(보낸 사람: 담임)")
        self.env.config.values["telegram_detail"] = "counts"
        self.assertEqual(self.bot.collect_message(self.stats([a]), TODAY), "📥 새 자료 1건 · ⏰ 마감 후보 1건")
        self.env.config.values["telegram_detail"] = "titles"
        text = self.bot.collect_message(self.stats([a]), TODAY)
        self.assertNotIn("김민준", text)
        self.assertIn("○○○ 학생 상담 안내", text)

    def test_register_button_puts_events_on_calendar(self):
        a = self.add("cool", "제출 안내", "서류는 11월 6일까지 제출\n\n(보낸 사람: 교무부)")
        self.bot.collect_message(self.stats([a]), TODAY)
        buttons = self.bot.item_buttons([a], [a])[0]
        self.assertEqual([b["text"] for b in buttons], ["1 원문", "📅 1 일정 등록"])
        self.assertEqual(self.env.db.one("SELECT registered FROM items WHERE id = ?", (a,))["registered"], 0)
        self.bot.handle_update(press(buttons[1]["callback_data"]))
        self.assertEqual(self.env.db.one("SELECT registered FROM items WHERE id = ?", (a,))["registered"], 1)
        self.assertIn("달력에 넣었습니다", self.fake.sent[-1]["text"])
        self.assertEqual([b["text"] for b in self.bot.item_buttons([a], [a])[0]], ["1 원문"])    # 이미 넣은 것은 단추 없음
        self.bot.handle_update(press(f"rg:{a}"))
        self.assertIn("이미 달력", self.fake.sent[-1]["text"])
        self.assertIn("없는 자료", self.bot.handle_update(press("rg:99999")))

    def test_rescan_removes_old_ja_candidates(self):
        item = self.add("cool", "교장공모제", "2027. 3. 1.자 개방형 교장공모제 공모 안내")
        self.env.db.execute("DELETE FROM events WHERE item_id = ?", (item,))
        self.env.db.execute("INSERT INTO events(item_id, date, kind, category, text) VALUES(?, '2027-03-01', 'deadline', '제출·마감', '2027. 3. 1.자 개방형 교장공모제 공모 안내')", (item,))
        self.assertEqual(rescan_events(self.env.db), 1)
        self.assertEqual(self.env.db.one("SELECT COUNT(*) AS n FROM events WHERE item_id = ?", (item,))["n"], 0)


class PollingTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.env.db.set_meta("telegram_chat_id", CHAT)

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def run_loop(self, api, rounds=3):
        waits = []

        def fake_wait(seconds=None):
            waits.append(seconds)
            if len(waits) >= rounds:
                self.bot.stop_event.set()
            return self.bot.stop_event.is_set()
        with patch.object(self.bot, "api", api), patch.object(self.bot.stop_event, "wait", fake_wait):
            self.bot.loop()
        return waits

    def test_conflict_is_retried_quickly_and_explained(self):
        def api(method, params=None, timeout=35):
            if method == "getUpdates":
                raise urllib.error.HTTPError("u", 409, "Conflict", {}, None)
            return True
        waits = self.run_loop(api, rounds=6)
        self.assertEqual(waits, [8] * 6)                       # 점점 길게 쉬어 답이 없어 보이던 것 방지
        self.assertIn("409", self.bot.status)
        self.assertIn("다른 곳", self.bot.status)
        warns = self.env.db.query("SELECT message FROM logs WHERE message LIKE '텔레그램 409%'")
        self.assertEqual(len(warns), 1)                        # 기록은 한 번만
        self.assertIn("텔레그램: 오류: 같은 봇", self.bot.answer("status"))
        self.assertIn("버전", self.bot.answer("status"))

    def test_other_http_errors_back_off(self):
        def api(method, params=None, timeout=35):
            if method == "getUpdates":
                raise urllib.error.HTTPError("u", 502, "Bad Gateway", {}, None)
            return True
        self.assertEqual(self.run_loop(api, rounds=4), [5, 10, 20, 40])
        self.assertIn("502", self.bot.status)

    def test_command_error_is_reported_not_silent(self):
        calls = {"n": 0}

        def api(method, params=None, timeout=35):
            if method == "getUpdates":
                calls["n"] += 1
                if calls["n"] >= 2:
                    self.bot.stop_event.set()   # 정상일 때는 기다림 없이 계속 도므로 직접 멈춤
                return [{"update_id": 1, **msg("오늘")}] if calls["n"] == 1 else []
            return True
        sent = []
        with patch.object(self.bot, "handle_update", side_effect=RuntimeError("폭발")), \
                patch.object(self.bot, "send", lambda text, chat_id=None, buttons=None, keyboard=False: sent.append(text)):
            self.run_loop(api, rounds=2)
        self.assertTrue(any("처리 중 오류" in t for t in sent), sent)
        self.assertTrue(self.env.db.one("SELECT 1 AS n FROM logs WHERE message LIKE '텔레그램 명령 처리 오류: RuntimeError%'"))


if __name__ == "__main__":
    unittest.main()
