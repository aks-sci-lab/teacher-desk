"""수신 항목 AI 요약 자동화 (0.5.44): 새 항목은 뒤에서 자동으로, 연 항목은 맨 앞으로, LM Studio 가 꺼져 있으면 조용히 쉼."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from desk import ai
from desk.collect import add_text_item
from tests.test_ai_register import FakeLMStudio
from tests.test_collect import Env


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def when(days_ago=0):
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def text(n):
    return f"{n}번 안내: 학생 협의록을 11월 3일까지 담임에게 제출해 주시기 바랍니다. 자세한 내용은 붙임을 참고하세요."


class AutoSummaryTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.lm = FakeLMStudio()
        self.lm.reply = "한 줄 요약: 협의록 제출\n- 해야 할 일: 협의록 제출"
        self.env.config.values.update(ai_enabled=True, ai_base=self.lm.base)
        self.clock = Clock()
        self.busy = [False]
        self.job = ai.ItemJob(self.env.db, self.env.config, busy=lambda: self.busy[0], clock=self.clock)
        self.patch = patch.object(ai, "BUSY_WAIT", 0.02)
        self.patch.start()

    def tearDown(self):
        self.job.stop()
        self.patch.stop()
        self.lm.close()
        self.env.close()

    def add(self, n, source="cool", days_ago=0, body=None, title=None):
        return add_text_item(self.env.db, source, body or text(n), title=title or f"안내{n}", received_at=when(days_ago))["id"]

    def idle(self):
        t = self.job.thread
        if t:
            t.join(10)
            self.assertFalse(t.is_alive())

    def row(self, iid):
        return self.env.db.one("SELECT * FROM items WHERE id = ?", (iid,))


    # ---- 자동으로 찾아 요약
    def test_new_items_are_summarized_automatically_newest_first(self):
        old = self.add(1, days_ago=3)
        new = self.add(2, days_ago=0)
        mid = self.add(3, days_ago=1)
        self.assertEqual(self.job.scan(), 3)
        self.idle()
        for iid in (old, new, mid):
            self.assertIn("협의록", self.row(iid)["ai_summary"])
        titles = [r["messages"][1]["content"] for r in self.lm.requests]
        self.assertEqual([("안내2" in t, "안내3" in t, "안내1" in t) for t in titles],
                         [(True, False, False), (False, True, False), (False, False, True)])   # 최신 순
        self.assertEqual(self.job.scan(), 0)             # 다 만들었으면 더 줄 세울 것이 없음
        self.assertEqual(self.job.summary(), {"running": None, "queued": 0, "paused": False})

    def test_what_is_skipped(self):
        self.add(1, source="cool_sent")                                   # 내가 보낸 쪽지
        self.add(2, days_ago=10)                                          # 7일보다 오래됨
        self.add(3, body="짧은 글입니다")                                  # 요약할 것이 없음
        archived = self.add(4)
        self.env.db.execute("UPDATE items SET status = 'archived' WHERE id = ?", (archived,))
        done = self.add(5)
        self.job.start(done)
        self.idle()
        calls = len(self.lm.requests)
        self.assertEqual(self.job.scan(), 0)                              # 이미 요약된 것도 건너뜀
        self.assertEqual(len(self.lm.requests), calls)
        self.env.db.execute("UPDATE items SET body = body || ' 11월 5일로 변경되었습니다.' WHERE id = ?", (done,))
        self.assertEqual(self.job.scan(), 1)                              # 원문이 바뀐 것은 다시
        self.idle()

    def test_off_switches(self):
        self.add(1)
        self.env.config.values["ai_enabled"] = False
        self.assertEqual(self.job.scan(), 0)
        self.env.config.values.update(ai_enabled=True, ai_auto_item=False)
        self.assertEqual(self.job.scan(), 0)
        self.assertEqual(self.lm.requests, [])
        self.env.config.values["ai_auto_item"] = True
        self.assertEqual(self.job.scan(), 1)
        self.idle()

    def test_turning_off_drops_waiting_auto_items_but_keeps_pressed_ones(self):
        self.busy[0] = True                                               # 브리핑 요약이 도는 중이라 줄만 서 있음
        ids = [self.add(n) for n in (1, 2, 3)]
        self.job.scan()
        self.job.start(ids[0])
        self.assertEqual(self.job.summary()["queued"], 3)
        self.env.config.values["ai_auto_item"] = False
        self.job.scan()
        self.assertEqual(self.job.summary()["queued"], 1)                 # 눌러서 시킨 것만 남음
        self.busy[0] = False
        self.idle()
        self.assertTrue(self.row(ids[0])["ai_summary"])
        self.assertIsNone(self.row(ids[1])["ai_summary"])

    # ---- 눌렀거나 연 항목이 먼저
    def test_pressed_item_jumps_the_queue_and_waits_for_briefing(self):
        self.busy[0] = True
        ids = [self.add(n, days_ago=n) for n in (1, 2, 3)]
        self.job.scan()
        self.assertEqual([self.job.status(i)["queued"] for i in ids], [1, 2, 3])
        self.assertTrue(self.job.start(ids[2]))                           # 맨 뒤 항목을 열었다
        self.assertEqual([self.job.status(i)["queued"] for i in ids], [2, 3, 1])
        self.assertEqual(self.lm.requests, [])                            # 브리핑 요약이 끝나기 전에는 시작하지 않음
        self.busy[0] = False
        self.idle()
        first = self.lm.requests[0]["messages"][1]["content"]
        self.assertIn("안내3", first)
        self.assertEqual(len(self.lm.requests), 3)
        self.assertFalse(self.job.start(ids[2]))                          # 이미 최신이면 다시 만들지 않음

    # ---- LM Studio 가 꺼져 있을 때
    def test_lm_studio_down_pauses_quietly_then_resumes(self):
        ids = [self.add(n) for n in (1, 2)]
        good_base = self.env.config.values["ai_base"]
        self.env.config.values["ai_base"] = "http://127.0.0.1:1/v1"       # 아무도 안 듣는 포트
        self.assertEqual(self.job.scan(), 2)
        self.idle()
        self.assertEqual([self.job.status(i)["error"] for i in ids], [None, None])   # 오류를 쌓지 않음
        self.assertTrue(self.job.summary()["paused"])
        self.assertEqual(self.job.summary()["queued"], 0)
        self.assertEqual(self.job.scan(), 0)                              # 쉬는 동안은 다시 시도하지 않음
        self.env.config.values["ai_base"] = good_base
        self.clock.now += ai.PAUSE_SECONDS + 1
        self.assertEqual(self.job.scan(), 2)
        self.idle()
        self.assertTrue(all(self.row(i)["ai_summary"] for i in ids))

    def test_pressed_item_shows_the_error_even_when_down(self):
        iid = self.add(1)
        self.env.config.values["ai_base"] = "http://127.0.0.1:1/v1"
        self.job.start(iid)
        self.idle()
        self.assertIn("연결할 수 없습니다", self.job.status(iid)["error"])
        self.assertFalse(self.job.summary()["paused"])                    # 눌러서 시킨 것은 쉬게 하지 않음

    def test_failed_item_is_not_retried_for_an_hour(self):
        iid = self.add(1)
        self.lm.reply = ""                                                # 생각만 하고 답이 없음
        self.job.scan()
        self.idle()
        self.assertIn("요약", self.job.status(iid)["error"])
        self.assertEqual(self.job.scan(), 0)
        self.clock.now += ai.RETRY_FAILED_SECONDS + 1
        self.lm.reply = "한 줄 요약: 다시 성공"
        self.assertEqual(self.job.scan(), 1)
        self.idle()
        self.assertIn("다시 성공", self.row(iid)["ai_summary"])

    def test_batch_limit(self):
        self.busy[0] = True
        for n in range(ai.AUTO_BATCH + 5):
            self.add(n, body=text(n) + f" 구분 {n}")
        self.assertEqual(self.job.scan(), ai.AUTO_BATCH)
        self.assertEqual(self.job.scan(), 0)                              # 이미 가득 참
        self.busy[0] = False
        self.job.stop()


class ListLineTests(unittest.TestCase):
    def test_summary_line(self):
        self.assertEqual(ai.summary_line("한 줄 요약: 협의록 제출\n- 해야 할 일: x"), "협의록 제출")
        self.assertEqual(ai.summary_line("- 한 줄 요약：전각 쌍점도 됨"), "전각 쌍점도 됨")
        self.assertEqual(ai.summary_line("\n\n- 해야 할 일: 제출"), "해야 할 일: 제출")
        self.assertEqual(ai.summary_line(""), "")
        self.assertEqual(ai.summary_line(None), "")
        self.assertEqual(len(ai.summary_line("한 줄 요약: " + "가" * 200)), 80)


class ListApiTests(unittest.TestCase):
    from tests import test_server
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def test_list_shows_one_line_only_when_ai_is_on(self):
        iid = add_text_item(self.env.db, "cool", text(9) + " 목록 한 줄 시험", title="목록시험")["id"]
        self.env.db.execute("UPDATE items SET ai_summary = ? WHERE id = ?", ("한 줄 요약: 목록 한 줄 보이기\n- 자세히: 많이", iid))
        self.env.config.values["ai_enabled"] = True
        rows = self.req("GET", "/api/items?status=all")[1]
        row = next(r for r in rows if r["id"] == iid)
        self.assertEqual(row["ai_line"], "목록 한 줄 보이기")
        self.assertNotIn("ai_summary", row)                               # 전체 요약은 목록에 싣지 않음
        self.env.config.values["ai_enabled"] = False
        row = next(r for r in self.req("GET", "/api/items?status=all")[1] if r["id"] == iid)
        self.assertEqual(row["ai_line"], "")
        self.assertIn("queued", self.req("GET", f"/api/items/{iid}")[1]["ai"])

    def test_setting_is_a_switch(self):
        self.assertTrue(self.env.config["ai_auto_item"])                  # 기본은 자동
        self.assertEqual(self.req("PUT", "/api/settings", {"ai_auto_item": False})[0], 200)
        self.assertFalse(self.env.config["ai_auto_item"])
        self.assertEqual(self.req("PUT", "/api/settings", {"ai_auto_item": 1})[0], 400)
        self.req("PUT", "/api/settings", {"ai_auto_item": True})


if __name__ == "__main__":
    unittest.main()
