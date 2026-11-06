"""브리핑 '지난 접속 이후' (0.5.53): 화면을 실제로 쓴 기록으로 접속을 나누고, 그 뒤 받은 자료만 분석."""
import unittest
import unittest.mock
from datetime import datetime, timedelta

from desk import ai, visits
from desk.collect import add_text_item
from desk.server import body_preview
from tests import test_server
from tests.test_ai_register import FakeLMStudio
from tests.test_collect import Env

CID = "cccccccc-1111-4222-8333-444444444444"
T0 = datetime(2026, 10, 30, 16, 20)   # 금요일 오후


class VisitTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.db = self.env.db

    def tearDown(self):
        self.env.close()

    def test_first_use_looks_back_seven_days(self):
        self.assertTrue(visits.record_active(self.db, T0))
        info = visits.info(self.db, T0)
        self.assertTrue(info["first"])
        self.assertEqual(info["since"], (T0 - timedelta(days=7)).strftime(visits.FMT))

    def test_new_visit_after_three_hours_starts_from_last_use(self):
        visits.record_active(self.db, T0 - timedelta(hours=8))           # 금요일 아침에 열고
        for m in range(0, 8 * 60, 15):                                     # 하루 종일 쓰다가
            self.assertFalse(visits.record_active(self.db, T0 - timedelta(hours=8) + timedelta(minutes=m)))
        visits.record_active(self.db, T0)                                  # 16:20 이 마지막
        monday = datetime(2026, 11, 2, 8, 40)
        self.assertTrue(visits.record_active(self.db, monday))             # 월요일 아침 = 새 접속
        info = visits.info(self.db, monday)
        self.assertEqual((info["prev_end"], info["since"], info["start"], info["first"]),
                         ("2026-10-30 16:20", "2026-10-30 16:20", "2026-11-02 08:40", False))
        # 같은 접속 안에서는 기준이 그대로 (점심 1시간 쉬어도 같은 접속)
        visits.record_active(self.db, monday + timedelta(hours=3))
        self.assertTrue(visits.record_active(self.db, monday + timedelta(hours=6, minutes=1)))   # 3시간 넘게 쉼 → 새 접속
        self.assertEqual(visits.info(self.db)["prev_end"], "2026-11-02 11:40")

    def test_long_break_is_capped_at_fourteen_days(self):
        visits.record_active(self.db, T0)
        later = T0 + timedelta(days=40)                                    # 방학
        visits.record_active(self.db, later)
        self.assertEqual(visits.since(self.db, later), (later - timedelta(days=14)).strftime(visits.FMT))

    def test_saves_once_a_minute(self):
        visits.record_active(self.db, T0)
        writes = []
        real = self.db.set_meta
        self.db.set_meta = lambda *a: writes.append(a) or real(*a)
        for s in range(0, 59, 5):
            visits.record_active(self.db, T0.replace(second=s))
        self.assertEqual(writes, [])                                       # 같은 분이면 저장하지 않음
        visits.record_active(self.db, T0 + timedelta(minutes=1))
        self.assertEqual(len(writes), 1)


class LeaveTests(unittest.TestCase):
    """0.5.77: '마지막 로그아웃' = 업무 데스크를 마지막으로 닫은(떠난) 시각부터."""
    def setUp(self):
        self.env = Env()
        self.db = self.env.db
        visits.record_active(self.db, T0 - timedelta(hours=8))           # 금요일 아침부터
        visits.record_active(self.db, T0)                                  # 16:20 까지 씀

    def tearDown(self):
        self.env.close()

    def test_closing_starts_new_visit_from_close_time(self):
        visits.record_leave(self.db, T0 + timedelta(minutes=5))            # 16:25 에 창을 닫고
        back = T0 + timedelta(hours=1)                                     # 1시간 뒤(3시간 안) 다시 열면
        self.assertTrue(visits.record_active(self.db, back))               # 새 접속
        info = visits.info(self.db, back)
        self.assertEqual((info["prev_end"], info["since"]), ("2026-10-30 16:25", "2026-10-30 16:25"))
        self.assertNotIn("left_at", visits.current(self.db))

    def test_refresh_does_not_shorten_the_range(self):
        before = visits.info(self.db, T0)
        visits.record_leave(self.db, T0)                                   # 새로 고침 = 닫혔다가
        self.assertFalse(visits.record_active(self.db, T0 + timedelta(minutes=1)))   # 곧 다시 열림
        self.assertNotIn("left_at", visits.current(self.db))               # 떠난 것이 아님
        self.assertEqual(visits.info(self.db, T0)["since"], before["since"])
        # 그 뒤 1시간 더 써도 같은 접속 (닫은 기록이 지워졌으므로)
        self.assertFalse(visits.record_active(self.db, T0 + timedelta(hours=1)))
        self.assertEqual(visits.info(self.db, T0)["since"], before["since"])

    def test_return_within_ten_minutes_is_same_visit(self):
        visits.record_leave(self.db, T0)
        self.assertFalse(visits.record_active(self.db, T0 + timedelta(minutes=9)))
        visits.record_leave(self.db, T0 + timedelta(minutes=9))
        self.assertTrue(visits.record_active(self.db, T0 + timedelta(minutes=19)))
        self.assertEqual(visits.info(self.db)["prev_end"], "2026-10-30 16:29")

    def test_app_restart_counts_as_leaving_at_last_use(self):
        visits.app_started(self.db)                                        # 프로그램을 끄고 다시 켬
        self.assertEqual(visits.current(self.db)["left_at"], "2026-10-30 16:20")
        self.assertTrue(visits.record_active(self.db, T0 + timedelta(minutes=40)))
        self.assertEqual(visits.info(self.db)["prev_end"], "2026-10-30 16:20")

    def test_quick_restart_after_update_is_same_visit(self):
        visits.app_started(self.db)
        self.assertFalse(visits.record_active(self.db, T0 + timedelta(minutes=2)))

    def test_app_start_keeps_close_time(self):
        visits.record_leave(self.db, T0 + timedelta(minutes=3))
        visits.app_started(self.db)
        self.assertEqual(visits.current(self.db)["left_at"], "2026-10-30 16:23")

    def test_no_record_no_leave(self):
        self.db.set_meta("visit", None)
        visits.record_leave(self.db, T0)
        visits.app_started(self.db)
        self.assertEqual(visits.current(self.db), {})


class VisitSummaryTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.lm = FakeLMStudio()
        self.env.config.values.update(ai_enabled=True, ai_base=self.lm.base)

    def tearDown(self):
        self.lm.close()
        self.env.close()

    def test_summary_covers_only_items_since_last_visit(self):
        now = datetime.now()
        old = (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        new = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        add_text_item(self.env.db, "cool", "예전 쪽지: 지난주 회의록을 정리해 주세요 부탁드립니다", title="예전", received_at=old)
        add_text_item(self.env.db, "cool", "새 쪽지: 11월 3일까지 협의록 제출 부탁드립니다 감사합니다", title="새것", received_at=new)
        since = (now - timedelta(days=1)).strftime(visits.FMT)
        res = ai.weekly_summary(self.env.db, self.env.config, since=since)
        prompt = self.lm.requests[-1]["messages"][1]["content"]
        self.assertIn("새것", prompt)
        self.assertNotIn("예전", prompt)                                    # 지난 접속 전 자료는 빼고
        self.assertEqual((res["range"], res["since"], res["used"]), ("visit", since, 1))
        res = ai.weekly_summary(self.env.db, self.env.config)              # [지난 1주일로 요약]
        self.assertIn("예전", self.lm.requests[-1]["messages"][1]["content"])
        self.assertEqual(res["range"], "week")

    def test_nothing_new_since_last_visit(self):
        since = datetime.now().strftime(visits.FMT)
        res = ai.weekly_summary(self.env.db, self.env.config, since=since)
        self.assertIn("이후 새로 받은", res["text"])
        self.assertEqual(self.lm.requests, [])                             # AI 를 부르지 않음


class VisitApiTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def test_only_active_screens_count_as_a_visit(self):
        self.env.db.set_meta("visit", None)
        self.req("GET", "/api/state", headers={"X-Desk-Client": CID})      # 켜 두기만 한 창
        self.assertEqual(visits.current(self.env.db), {})
        self.req("GET", "/api/state", headers={"X-Desk-Active": "1"})      # 화면 번호 없이 온 신호도 무시
        self.assertEqual(visits.current(self.env.db), {})
        self.req("GET", "/api/state", headers={"X-Desk-Client": CID, "X-Desk-Active": "1"})
        self.assertTrue(visits.current(self.env.db).get("last_active"))

    def test_closing_last_screen_records_leave(self):
        other = "dddddddd-1111-4222-8333-444444444444"
        self.env.db.set_meta("visit", None)
        self.req("GET", "/api/state", headers={"X-Desk-Client": CID, "X-Desk-Active": "1"})
        self.req("GET", "/api/state", headers={"X-Desk-Client": other})
        self.req("POST", "/api/ui/bye", {"id": CID}, headers={"X-Desk-Client": CID})
        self.assertNotIn("left_at", visits.current(self.env.db))           # 다른 화면이 아직 열려 있음
        self.req("POST", "/api/ui/bye", {"id": other}, headers={"X-Desk-Client": other})
        self.assertTrue(visits.current(self.env.db).get("left_at"))        # 마지막 화면을 닫음

    def test_today_lists_items_since_last_visit(self):
        self.env.db.set_meta("visit", {"start": "2026-11-02 08:40", "prev_end": "2000-01-01 00:00", "last_active": "2026-11-02 08:40"})
        iid = add_text_item(self.env.db, "edufine", "제목: 접속 시험 공문\n11월 20일까지 제출해 주시기 바랍니다.", title="접속 시험 공문")["id"]
        st, s = self.req("GET", "/api/today")
        self.assertEqual(st, 200)
        sv = s["since_visit"]
        self.assertFalse(sv["first"])
        self.assertIn(iid, [it["id"] for it in sv["items"]])
        self.assertNotIn("ai_summary", sv["items"][0])

    def test_weekly_api_range(self):
        self.env.config.values["ai_enabled"] = True
        started = []
        with unittest.mock.patch.object(self.app.ai_job, "start", lambda kind, since=None: started.append((kind, since))):
            self.req("POST", "/api/ai/weekly", {"kind": "full"})
            self.req("POST", "/api/ai/weekly", {"kind": "full", "range": "week"})
            self.req("POST", "/api/ai/weekly", {"kind": "new"})
        self.env.config.values["ai_enabled"] = False
        self.assertEqual(started[0], ("full", visits.since(self.env.db)))  # 기본은 지난 접속 이후
        self.assertEqual(started[1], ("full", None))                       # 지난 1주일
        self.assertEqual(started[2][0], "new")
        self.assertIn("visit", self.req("GET", "/api/ai/weekly")[1])


class PreviewTests(unittest.TestCase):
    def test_body_preview(self):
        self.assertEqual(body_preview("협의록 제출\n\n  11월 3일까지   제출해 주세요", "협의록 제출"), "11월 3일까지 제출해 주세요")
        self.assertEqual(body_preview("첫 줄\n둘째 줄", "다른 제목"), "첫 줄 둘째 줄")
        self.assertEqual(body_preview(None, "x"), "")
        self.assertEqual(len(body_preview("가" * 500, "x")), 70)


if __name__ == "__main__":
    import unittest.mock  # noqa: F401
    unittest.main()
