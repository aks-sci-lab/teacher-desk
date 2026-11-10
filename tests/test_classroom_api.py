"""학급 경영 API 시험의 공용 준비 (가짜 학생 명단으로 서버를 띄움)."""
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

from datetime import date

from desk import classrecords
from desk.server import App, make_handler
from tests.test_collect import Env

FAKE = [{"number": n, "name": f"가짜{n}"} for n in range(1, 13)]


class ApiBase(unittest.TestCase):
    # 상담 기록은 학년도(3월 1일)마다 나뉘므로, 날짜를 박아 둔 시험이 해가 바뀌어도 그대로 돌게 '오늘'을 고정
    TODAY = date(2026, 11, 10)

    def setUp(self):
        self._today = classrecords.today
        classrecords.today = lambda: self.TODAY
        self.env = Env()
        self.app = App(self.env.db, self.env.config, self.env.collector, None)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
        self.port = self.httpd.server_address[1]
        self.env.config.values["port"] = self.port
        self.httpd.RequestHandlerClass = make_handler(self.app)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.env.db.replace_students(FAKE)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.env.close()
        classrecords.today = self._today

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}", "X-Desk-Token": self.app.token}
        if body is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, json.loads(data)
