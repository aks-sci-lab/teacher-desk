"""LM Studio 가 ttl(자동 해제) 칸을 거부할 때의 대응 시험."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from desk import ai
from desk.db import DB


class Fake:
    def __init__(self, reject=(), other_400=False):
        self.requests, self.reject, self.other_400 = [], set(reject), other_400
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(body)
                bad = fake.other_400 or any(k in body for k in fake.reject)
                data = json.dumps({"error": {"message": "model not found" if fake.other_400 else "unknown field"}} if bad
                                  else {"choices": [{"message": {"content": "답"}, "finish_reason": "stop"}]}).encode()
                self.send_response(400 if bad else 200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class TtlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(Path(self.tmp.name) / "d.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def logs(self):
        return [r for r in self.db.query("SELECT message FROM logs") if "ttl" in r["message"]]

    def test_supported_server_gets_ttl(self):
        f = Fake()
        self.addCleanup(f.close)
        self.assertEqual(ai.ask(f.base, "m", "q", db=self.db), "답")
        self.assertEqual(f.requests[0]["ttl"], 300)
        self.assertFalse(ai.ttl_unsupported(f.base, self.db))

    def test_ttl_refused_then_remembered(self):
        f = Fake(reject=["ttl"])
        self.addCleanup(f.close)
        self.assertEqual(ai.ask(f.base, "m", "q", db=self.db), "답")
        self.assertNotIn("ttl", f.requests[-1])
        self.assertTrue(ai.ttl_unsupported(f.base, self.db))
        n = len(f.requests)
        self.assertEqual(ai.ask(f.base, "m", "q", db=self.db), "답")
        self.assertEqual(len(f.requests), n + 1)          # 다음엔 한 번에 성공
        self.assertNotIn("ttl", f.requests[-1])
        self.assertEqual(len(self.logs()), 1)             # 기록은 한 번만
        self.assertEqual(self.db.get_meta(ai.TTL_META), f.base.rstrip("/"))

    def test_both_extra_fields_refused(self):
        f = Fake(reject=["ttl", "chat_template_kwargs"])
        self.addCleanup(f.close)
        self.assertEqual(ai.ask(f.base, "m", "q", db=self.db), "답")
        self.assertTrue(ai.ttl_unsupported(f.base, self.db))

    def test_only_think_refused_keeps_ttl(self):
        f = Fake(reject=["chat_template_kwargs"])
        self.addCleanup(f.close)
        self.assertEqual(ai.ask(f.base, "m", "q", db=self.db), "답")
        self.assertEqual(f.requests[-1]["ttl"], 300)
        self.assertFalse(ai.ttl_unsupported(f.base, self.db))

    def test_other_400_is_shown(self):
        f = Fake(other_400=True)
        self.addCleanup(f.close)
        with self.assertRaises(ai.AIError) as cm:
            ai.ask(f.base, "m", "q", db=self.db)
        self.assertIn("model not found", str(cm.exception))
        self.assertFalse(ai.ttl_unsupported(f.base, self.db))
        self.assertEqual(self.logs(), [])


if __name__ == "__main__":
    unittest.main()
