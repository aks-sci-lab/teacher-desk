import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from desk import ai, queries
from desk.collect import add_text_item
from desk.db import DB
from tests.test_collect import Env
from tests import test_server  # ServerTests 를 이름으로 가져오면 그 시험이 여기서 또 돔


class FakeLMStudio:
    """LM Studio 로컬 서버 흉내 (/v1/models, /v1/chat/completions)."""

    def __init__(self, models=("qwen3-8b", "text-embedding-nomic")):
        self.requests = []
        self.models = list(models)
        self.reply = "<think>생각</think>## 꼭 할 일 (마감 순)\n- 11월 3일 · 협의록 제출 [1]"
        self.reject_extra = False   # True: 모르는 칸(chat_template_kwargs)이 있으면 400 (깐깐한 LM Studio 흉내)
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj, status=200):
                data = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send({"data": [{"id": m} for m in fake.models]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(body)
                if fake.reject_extra and "chat_template_kwargs" in body:
                    return self._send({"error": {"message": "Unrecognized key"}}, 400)
                self._send({"choices": [{"message": {"role": "assistant", "content": fake.reply},
                                         "finish_reason": "length" if not fake.reply else "stop"}]})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def recent(hours=3):
    return (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


class AITests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeLMStudio()
        self.env.config.values.update(ai_enabled=True, ai_base=self.fake.base, ai_model="")

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def test_only_this_pc(self):
        for base in ("https://api.openai.com/v1", "http://192.168.0.10:1234/v1", "http://evil.example/v1"):
            with self.assertRaises(ai.AIError):
                ai.check_local(base)
        ai.check_local("http://localhost:1234/v1")
        with self.assertRaises(ValueError):           # 설정 화면에서도 막음
            self.env.config.update({"ai_base": "https://api.openai.com/v1"})

    def test_weekly_summary(self):
        db = self.env.db
        db.replace_students([{"number": 3, "name": "도윤서"}])
        add_text_item(db, "cool", "협의록을 11월 3일까지 제출\n도윤서 학생 상담 포함", title="성적관리위원회", received_at=recent())
        add_text_item(db, "edufine", "연수 신청 안내 11월 2일까지", title="연수 안내", received_at=recent(30))
        add_text_item(db, "cool", "지난달 쪽지", title="오래된 것", received_at=recent(24 * 10))   # 1주일 전
        add_text_item(db, "paste", "직접 붙여넣은 글", received_at=recent())                       # 출처가 달라 뺌
        r = ai.weekly_summary(db, self.env.config)
        self.assertEqual((r["count"], r["used"], r["model"]), (2, 2, "qwen3-8b"))   # 임베딩 모델은 고르지 않음
        req = self.fake.requests[-1]
        self.assertEqual(req["chat_template_kwargs"], {"enable_thinking": False})   # 생각 단계 끄기
        self.assertTrue(req["messages"][1]["content"].endswith("/no_think"))       # Qwen 은 글로도
        self.assertEqual(r["text"], "## 꼭 할 일 (마감 순)\n- 11월 3일 · 협의록 제출 [1]")  # 생각 과정 뺌
        prompt = self.fake.requests[-1]["messages"][1]["content"]
        self.assertIn("[1] 쿨메신저", prompt)
        self.assertIn("업무포털", prompt)
        self.assertNotIn("도윤서", prompt)                                           # 학생 이름 가림
        self.assertNotIn("오래된 것", prompt)
        self.assertEqual(db.get_meta("ai_weekly")["text"], r["text"])
        self.assertEqual(len(r["ids"]), 2)

    def test_empty_week_and_offline(self):
        r = ai.weekly_summary(self.env.db, self.env.config)
        self.assertIn("없습니다", r["text"])
        self.assertEqual(self.fake.requests, [])                                     # 보낼 것이 없으면 부르지 않음
        add_text_item(self.env.db, "cool", "회의 11월 5일", received_at=recent())
        self.env.config.values["ai_base"] = "http://127.0.0.1:1/v1"
        with self.assertRaises(ai.AIError) as cm:
            ai.weekly_summary(self.env.db, self.env.config)
        self.assertIn("Start Server", str(cm.exception))

    def test_thinking_only_and_strict_server(self):
        add_text_item(self.env.db, "cool", "회의 11월 5일", received_at=recent())
        self.fake.reject_extra = True                                              # 모르는 칸 → 400 → 빼고 다시
        self.assertIn("협의록", ai.weekly_summary(self.env.db, self.env.config)["text"])
        self.assertNotIn("chat_template_kwargs", self.fake.requests[-1])
        self.fake.reply = "<think>생각만 길게"                                       # 생각만 하다 끝남
        with self.assertRaises(ai.AIError) as cm:
            ai.weekly_summary(self.env.db, self.env.config)
        self.assertIn("Reasoning Budget", str(cm.exception))

    def test_job_runs_in_background(self):
        add_text_item(self.env.db, "cool", "회의 11월 5일", received_at=recent())
        job = ai.Job(self.env.db, self.env.config)
        self.assertTrue(job.start())
        job.thread.join(10)
        st = job.state()
        self.assertEqual((st["running"], st["error"], st["timeout"]), (False, None, 900))
        self.assertIn("협의록", st["result"]["text"])
        self.env.config.values["ai_base"] = "http://127.0.0.1:1/v1"                # 꺼져 있으면 오류만 남김
        job.start()
        job.thread.join(10)
        self.assertIn("Start Server", job.state()["error"])

    def test_prompt_budget(self):
        items = [{"source": "cool", "received_at": "2026-10-27 09:00", "title": f"쪽지{i}", "body": "가" * 5000}
                 for i in range(40)]
        prompt, used = ai.build_prompt(items)
        self.assertLess(len(prompt), ai.TOTAL_CHARS + 1000)
        self.assertLess(used, 40)
        p, _ = ai.build_prompt(items[:1], today=date(2026, 10, 28))
        self.assertIn("오늘은 2026-10-28(수)", p)                              # 지난 마감을 빼고 D-day 를 맞추게
        self.assertIn("35자", p)
        short, used_short = ai.build_prompt(items, size="short")
        self.assertLess(len(short), 6000 + 1000)
        self.assertLess(used_short, used)
        self.assertEqual(ai.mask_names("김가온과 김가", ["김가", "김가온"]), "○○○과 ○○")


class RegisterTests(unittest.TestCase):
    def test_only_checked_items_reach_calendar(self):
        env = Env()
        try:
            db = env.db
            today = date.today()
            day = (today + timedelta(days=3))
            text = f"협의록을 {day.month}월 {day.day}일까지 제출해 주세요"
            cool = add_text_item(db, "cool", text, received_at=recent())["id"]
            paste = add_text_item(db, "paste", "회의 " + text, received_at=recent())["id"]
            evs = queries.events_between(db, today, today + timedelta(days=7))
            self.assertEqual({e["item_id"] for e in evs}, {paste})        # 자동 수집은 후보, 직접 입력은 바로
            self.assertEqual(len(queries.today_summary(db, today)["week_deadlines"]), 1)
            db.execute("UPDATE items SET registered = 1 WHERE id = ?", (cool,))
            evs = queries.events_between(db, today, today + timedelta(days=7))
            self.assertEqual({e["item_id"] for e in evs}, {paste, cool})
            db.execute("INSERT INTO events(date, kind, category, text) VALUES(?, 'event', '참고', '따로 넣은 일정')",
                       (today.isoformat(),))
            self.assertEqual(len(queries.events_between(db, today, today)), 1)  # 항목 없는 일정은 그대로
        finally:
            env.close()

    def test_old_database_gets_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "desk.db"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL, "
                         "body TEXT NOT NULL, received_at TEXT NOT NULL, created_at TEXT NOT NULL, content_hash TEXT, "
                         "summary TEXT, category TEXT, status TEXT DEFAULT 'new', doc_numbers TEXT, file_path TEXT)")
            for src in ("cool", "edufine", "paste", "school"):
                conn.execute("INSERT INTO items(source, title, body, received_at, created_at) VALUES(?, 't', 'b', 'x', 'x')", (src,))
            conn.commit()
            conn.close()
            db = DB(path)
            try:
                got = {r["source"]: r["registered"] for r in db.query("SELECT source, registered FROM items")}
                self.assertEqual(got, {"cool": 0, "edufine": 0, "paste": 1, "school": 1})
            finally:
                db.close()


class RegisterApiTests(unittest.TestCase):
    """수신함 체크 API·AI 요약 API (ServerTests 의 서버 준비를 그대로 씀, 그 시험들은 다시 돌리지 않음)."""
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def test_register_api_and_ai(self):
        cool = add_text_item(self.env.db, "cool", "체육대회 11월 20일", received_at=recent())["id"]
        st, items = self.req("GET", "/api/items?status=all")
        self.assertEqual([i["registered"] for i in items if i["id"] == cool], [0])
        self.assertEqual(self.req("PATCH", f"/api/items/{cool}", {"registered": "yes"})[0], 400)
        st, it = self.req("PATCH", f"/api/items/{cool}", {"registered": True})
        self.assertEqual((st, it["registered"]), (200, 1))
        other = add_text_item(self.env.db, "edufine", "공문 본문", received_at=recent())["id"]
        self.req("POST", "/api/events", {"item_id": other, "date": date.today().isoformat(), "text": "직접 넣은 일정"})
        self.assertEqual(self.env.db.one("SELECT registered FROM items WHERE id = ?", (other,))["registered"], 1)
        # AI 요약: 꺼져 있으면 안내, 켜면 LM Studio 로
        self.assertEqual(self.req("POST", "/api/ai/weekly", {})[0], 400)
        fake = FakeLMStudio()
        try:
            self.env.config.values.update(ai_enabled=True, ai_base=fake.base)
            st, r = self.req("POST", "/api/ai/check", {"base": fake.base})
            self.assertEqual((st, r["models"]), (200, ["qwen3-8b"]))
            self.assertEqual(self.req("POST", "/api/ai/check", {"base": "https://api.openai.com/v1"})[0], 400)
            st, r = self.req("POST", "/api/ai/weekly", {})                 # 시작만 하고 바로 돌아옴
            self.assertEqual(st, 200)
            self.app.ai_job.thread.join(10)
            st, r = self.req("GET", "/api/ai/weekly")
            self.assertEqual((r["enabled"], r["running"], r["error"], r["result"]["model"]), (True, False, None, "qwen3-8b"))
            self.assertIn("협의록", r["result"]["text"])
            self.assertEqual(self.req("PUT", "/api/settings", {"ai_input_size": "huge"})[0], 400)
            # 요약 줄 지우기 → 상태에 남고, 되돌리면 빠짐
            key = "todo|10/30(금)|가족수당서류제출"
            self.assertEqual(self.req("POST", "/api/ai/hidden", {"key": key})[1]["hidden"], [key])
            self.assertEqual(self.req("GET", "/api/ai/weekly")[1]["hidden"], [key])
            self.assertEqual(self.req("POST", "/api/ai/hidden", {"key": key, "hide": False})[1]["hidden"], [])
            self.assertEqual(self.req("POST", "/api/ai/hidden", {"key": ""})[0], 400)
        finally:
            self.env.config.values.update(ai_enabled=False)
            fake.close()


if __name__ == "__main__":
    unittest.main()
