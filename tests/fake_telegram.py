"""시험용 가짜 텔레그램 서버 (진짜 텔레그램에 연결하지 않고 봇을 시험)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeTelegram:
    def __init__(self, port=0):
        self.updates, self.sent, self.calls = [], [], []
        self.next_id = 1
        self.cond = threading.Condition()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                method = self.path.rsplit("/", 1)[-1]
                if self.path == "/_push":
                    fake.push(body["text"], body.get("chat_id", 111))
                    return self.reply(True)
                fake.calls.append(method)
                if method == "getUpdates":
                    offset = body.get("offset") or 0
                    with fake.cond:
                        fake.cond.wait_for(lambda: any(u["update_id"] >= offset for u in fake.updates),
                                           timeout=min(body.get("timeout", 0), 1))
                        res = [u for u in fake.updates if u["update_id"] >= offset]
                    return self.reply(res)
                if method == "sendMessage":
                    fake.sent.append(body)
                    return self.reply({"message_id": len(fake.sent)})
                return self.reply(True)

            def do_GET(self):
                if self.path == "/_sent":
                    return self.reply(fake.sent)
                self.send_error(404)

            def reply(self, result):
                data = json.dumps({"ok": True, "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def push(self, text, chat_id=111, chat_type="private"):
        with self.cond:
            self.updates.append({"update_id": self.next_id, "message": {
                "message_id": self.next_id, "text": text, "chat": {"id": chat_id, "type": chat_type}}})
            self.next_id += 1
            self.cond.notify_all()

    def close(self):
        self.httpd.shutdown()


if __name__ == "__main__":
    import sys
    import time
    f = FakeTelegram(int(sys.argv[1]) if len(sys.argv) > 1 else 8799)
    print("fake telegram on", f.base, flush=True)
    while True:
        time.sleep(3600)
