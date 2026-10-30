import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desk import updater
from tests import test_server  # ServerTests 를 이름으로 가져오면 그 시험이 여기서 또 돔


def latest_file(version):
    f = Path(tempfile.mkdtemp()) / "latest.json"
    f.write_text(json.dumps({"version": version}), encoding="utf-8")
    return f.as_uri()


class CheckerTests(unittest.TestCase):
    def test_new_same_and_offline(self):
        with patch.dict("os.environ", {"DESK_UPDATE_JSON": latest_file("0.5.40")}):
            c = updater.Checker("0.5.36", Path("."))
            st = c.check()
            self.assertEqual((st["available"], st["latest"], st["error"], st["portable"]), (True, "0.5.40", None, True))
            self.assertTrue(st["checked_at"])
            self.assertEqual(updater.Checker("0.5.40", Path(".")).check()["available"], False)
            self.assertEqual(updater.Checker("0.5.41", Path(".")).check()["available"], False)
        with patch.dict("os.environ", {"DESK_UPDATE_JSON": "file:///없는/주소.json"}):
            st = updater.Checker("0.5.36", Path(".")).check()
            self.assertEqual((st["available"], st["latest"]), (False, None))
            self.assertIn("확인하지 못했습니다", st["error"])

    def test_dev_folder_is_not_portable_and_does_not_poll(self):
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("DESK_UPDATE_JSON", None)
            c = updater.Checker("0.5.36", Path(tempfile.mkdtemp()))
            self.assertFalse(c.state()["portable"])
            c.start()
            self.assertIsNone(c.thread)


class UpdateApiTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def setUp(self):
        self.app.update_checker = None
        self.app.shutdown = None

    def test_requires_token_and_feature(self):
        self.assertEqual(self.req("GET", "/api/update", token=False)[0], 401)
        self.assertEqual(self.req("POST", "/api/update/check", {}, token=False)[0], 401)
        self.assertEqual(self.req("POST", "/api/update/apply", {}, token=False)[0], 401)
        self.assertEqual(self.req("POST", "/api/update/check", {})[0], 503)
        self.assertIsNone(self.req("GET", "/api/state")[1]["update"])

    def test_apply_flow(self):
        with patch.dict("os.environ", {"DESK_UPDATE_JSON": latest_file("0.5.40")}):
            self.app.update_checker = updater.Checker("0.5.36", Path("."))
            self.app.shutdown = lambda: calls.append("shutdown")
            calls = []
            self.assertEqual(self.req("POST", "/api/update/apply", {})[0], 400)           # 먼저 확인해야 함
            st, body = self.req("POST", "/api/update/check", {})
            self.assertEqual((st, body["available"], body["latest"]), (200, True, "0.5.40"))
            self.assertTrue(self.req("GET", "/api/state")[1]["update"]["available"])
            with patch("desk.server.updater.start_update") as start:
                # 수집이 도는 중이면 막음 (force 로는 진행)
                self.app.collector.lock.acquire()
                try:
                    st, body = self.req("POST", "/api/update/apply", {})
                    self.assertEqual(st, 409)
                    self.assertIn("자료 수집", body["error"])
                    start.assert_not_called()
                    st, body = self.req("POST", "/api/update/apply", {"force": True})
                finally:
                    self.app.collector.lock.release()
                self.assertEqual((st, body["from"], body["to"]), (200, "0.5.36", "0.5.40"))
                start.assert_called_once()
            import time
            for _ in range(50):
                if calls:
                    break
                time.sleep(0.05)
            self.assertEqual(calls, ["shutdown"])    # 업데이트 창이 파일을 바꾸도록 프로그램을 끔
            self.assertTrue(self.env.db.one("SELECT 1 AS n FROM logs WHERE message LIKE '화면에서 업데이트 시작%'"))

    def test_dev_folder_cannot_apply(self):
        import os
        os.environ.pop("DESK_UPDATE_JSON", None)
        self.app.update_checker = updater.Checker("0.5.36", Path(tempfile.mkdtemp()))
        self.app.update_checker.latest = {"version": "0.5.40"}
        self.app.shutdown = lambda: None
        st, body = self.req("POST", "/api/update/apply", {})
        self.assertEqual(st, 400)
        self.assertIn("윈도우용 설치본", body["error"])


if __name__ == "__main__":
    unittest.main()
