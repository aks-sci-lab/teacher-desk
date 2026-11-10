"""시험용 가짜 구글: 비공개 iCal 주소와 Apps Script 웹 앱(desk/gcal.py 의 SCRIPT 와 같은 약속)을 흉내 냄.

진짜 Apps Script 처럼 POST 에 302 로 다른 주소를 알려 주고, 그 주소를 GET 하면 답이 나옴.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import count


class FakeGoogle:
    def __init__(self, key="k"):
        self.key = key
        self.ics = "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
        self.events = {}        # gid → {title, start, end, allDay, description, tag}
        self.others = {"other@google.com": {"title": "선생님 개인 일정", "tag": None}}   # 업무 데스크가 만들지 않은 일정
        self.requests = []
        self.fail = None        # 'http' / 'html' / None
        self.answers = {}
        self.ids = count(1)
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, code, body, ctype="application/json", extra=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith("/calendar/ical/"):
                    if fake.fail == "http":
                        return self.send(404, b"no")
                    return self.send(200, fake.ics.encode("utf-8"), "text/calendar")
                if self.path.startswith("/echo/"):
                    return self.send(200, fake.answers.pop(self.path, b"{}"))
                self.send(404, b"")

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(data)
                if fake.fail == "http":
                    return self.send(500, b"err")
                if fake.fail == "html":
                    return self.send(200, b"<html>login</html>", "text/html")
                path = f"/echo/{next(fake.ids)}"
                fake.answers[path] = json.dumps(fake.handle(data), ensure_ascii=False).encode("utf-8")
                self.send(302, b"", extra={"Location": f"http://127.0.0.1:{fake.port}{path}"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def ical_url(self):
        return f"http://127.0.0.1:{self.port}/calendar/ical/me/private-x/basic.ics"

    @property
    def push_url(self):
        return f"http://127.0.0.1:{self.port}/macros/s/abc/exec"

    def handle(self, req):
        if req.get("key") != self.key:
            return {"ok": False, "error": "bad_key"}
        if req.get("op") == "ping":
            return {"ok": True, "calendar": "선생님", "version": 1}
        results = []
        for op in req.get("ops", []):
            gid = op.get("gid")
            mine = gid in self.events
            if op["op"] == "delete":
                if mine:
                    del self.events[gid]
                results.append({"ref": op["ref"], "ok": True})
                continue
            if op.get("title", "").startswith("FAIL"):
                results.append({"ref": op["ref"], "ok": False, "error": "x"})
                continue
            if not mine:   # 없거나 업무 데스크가 만든 것이 아니면 새로 만듦 (다른 일정은 건드리지 않음)
                gid = f"g{next(self.ids)}@google.com"
            self.events[gid] = {k: op.get(k) for k in ("title", "start", "end", "allDay", "description")} | {"tag": op["ref"]}
            results.append({"ref": op["ref"], "ok": True, "gid": gid})
        return {"ok": True, "results": results}

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
