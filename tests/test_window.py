"""업무 데스크 화면을 전용 창 하나로 (0.5.43)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desk import updater, window
from tests import test_server

CID_A = "aaaaaaaa-1111-4222-8333-444444444444"
CID_B = "bbbbbbbb-1111-4222-8333-444444444444"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TrackerTests(unittest.TestCase):
    def test_newest_claim_wins_and_bye_frees_it(self):
        clock = Clock()
        t = window.UiTracker(clock)
        self.assertEqual(t.state(), {"open": False, "active": None, "count": 0})
        t.claim(CID_A)
        t.claim(CID_B)                       # 나중에 연 화면이 쓰는 화면
        self.assertEqual(t.state(), {"open": True, "active": CID_B, "count": 2})
        t.bye(CID_B)                         # 쓰던 화면이 닫히면 비어 있음 → 남은 화면이 이어받을 수 있음
        self.assertEqual(t.state(), {"open": True, "active": None, "count": 1})
        t.bye(CID_A)
        self.assertFalse(t.state()["open"])

    def test_screens_expire_without_signals(self):
        clock = Clock()
        t = window.UiTracker(clock)
        t.claim(CID_A)
        clock.now += window.ALIVE_SECONDS - 1
        t.touch(CID_B)
        self.assertEqual(t.state()["count"], 2)
        clock.now += 2                       # A 만 신호가 끊김 (브라우저가 갑자기 꺼짐)
        self.assertEqual(t.state(), {"open": True, "active": None, "count": 1})

    def test_client_id_format(self):
        self.assertTrue(window.CLIENT_ID_RE.match(CID_A))
        for bad in ("", "short", "x" * 65, "has space 12345678", "../../etc/passwd1"):
            self.assertFalse(window.CLIENT_ID_RE.match(bad), bad)


class OpenTests(unittest.TestCase):
    def fake_install(self, root, *parts):
        p = Path(root).joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
        return str(p)

    def test_find_browser_prefers_edge_then_chrome(self):
        with tempfile.TemporaryDirectory() as pf, tempfile.TemporaryDirectory() as empty:
            env = {"ProgramFiles": pf, "ProgramFiles(x86)": empty, "LOCALAPPDATA": empty}
            chrome = self.fake_install(pf, "Google", "Chrome", "Application", "chrome.exe")
            with patch.dict("os.environ", env, clear=True), patch("shutil.which", return_value=None):
                self.assertEqual(window.find_browser(), chrome)
                edge = self.fake_install(pf, "Microsoft", "Edge", "Application", "msedge.exe")
                self.assertEqual(window.find_browser(), edge)
        with patch.dict("os.environ", {}, clear=True), patch("shutil.which", return_value=None):
            self.assertIsNone(window.find_browser())

    def test_app_command_uses_own_profile_and_app_mode(self):
        cmd = window.app_command("edge.exe", "http://127.0.0.1:8770/", "P")
        self.assertEqual(cmd[0], "edge.exe")
        self.assertIn("--app=http://127.0.0.1:8770/", cmd)
        self.assertIn("--user-data-dir=P", cmd)        # 다른 브라우저 창과 따로 도는 설정 폴더
        self.assertIn("--no-first-run", cmd)

    def test_open_ui_window_tab_and_fallbacks(self):
        with tempfile.TemporaryDirectory() as data:
            with patch.object(window, "find_browser", return_value="/b/edge"), \
                 patch.object(window.subprocess, "Popen") as popen, patch.object(window.webbrowser, "open") as web:
                self.assertEqual(window.open_ui(8770, data, True), "window")
                self.assertIn("--app=http://127.0.0.1:8770/", popen.call_args[0][0])
                self.assertTrue((Path(data) / "app-window").is_dir())
                web.assert_not_called()
                popen.reset_mock()
                self.assertEqual(window.open_ui(8770, data, False), "tab")      # 설정에서 끔
                popen.assert_not_called()
                web.assert_called_once_with("http://127.0.0.1:8770/")
            with patch.object(window, "find_browser", return_value=None), \
                 patch.object(window.webbrowser, "open") as web:
                self.assertEqual(window.open_ui(8770, data, True), "tab")       # 엣지·크롬이 없음
                web.assert_called_once()
            with patch.object(window, "find_browser", return_value="/b/edge"), \
                 patch.object(window.subprocess, "Popen", side_effect=OSError("no")), \
                 patch.object(window.webbrowser, "open") as web:
                self.assertEqual(window.open_ui(8770, data, True), "tab")       # 실행 실패
                web.assert_called_once()


class ShowUiTests(unittest.TestCase):
    def run_show(self, state, focus, windows):
        calls = []
        result = window.show_ui(8770, "d", True, fetch=lambda port: state, focus=lambda: focus,
                                opener=lambda *a: calls.append(a) or "window", is_windows=windows)
        return result, calls

    def test_open_screen_is_brought_to_front_not_reopened(self):
        self.assertEqual(self.run_show({"open": True}, True, True), ("focused", []))

    def test_open_but_window_not_found(self):
        result, calls = self.run_show({"open": True}, False, True)
        self.assertEqual((result, len(calls)), ("window", 1))           # 윈도우: 못 찾으면 새로 엶
        self.assertEqual(self.run_show({"open": True}, False, False), ("already-open", []))   # 개발용: 다시 열지 않음

    def test_not_open_or_unknown_opens_new(self):
        for state in ({"open": False}, None):
            result, calls = self.run_show(state, True, True)
            self.assertEqual((result, len(calls)), ("window", 1))


class LauncherAndUpdaterTests(unittest.TestCase):
    def test_update_restart_does_not_open_a_second_window(self):
        self.assertNotIn("--no-browser", updater.build_script("C:/d", 1, ["u"]))                      # 켤 때 업데이트는 화면을 엶
        s = updater.build_script("C:/d", 1, ["u"], open_browser=False)                                # 화면에서 업데이트하면 열려 있던 화면이 새로 고침
        self.assertIn("'--no-update-check --no-browser'", s)


class ServerUiTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def test_client_header_claim_bye_and_state(self):
        self.req("POST", "/api/ui/bye", {"id": CID_A})
        self.req("POST", "/api/ui/bye", {"id": CID_B})
        st, s = self.req("GET", "/api/state", headers={"X-Desk-Client": CID_A})      # 신호만으로 '열려 있음'
        self.assertEqual((st, s["ui"]["open"], s["ui"]["active"]), (200, True, None))
        self.assertEqual(self.req("POST", "/api/ui/claim", {"id": CID_A})[1]["active"], CID_A)
        self.assertEqual(self.req("POST", "/api/ui/claim", {"id": CID_B})[1]["active"], CID_B)   # 나중에 연 창이 이김
        self.assertEqual(self.req("GET", "/api/state", headers={"X-Desk-Client": CID_A})[1]["ui"]["active"], CID_B)
        self.assertEqual(self.req("POST", "/api/ui/bye", {"id": CID_B})[0], 200)
        self.assertIsNone(self.req("GET", "/api/ui")[1]["active"])
        self.req("POST", "/api/ui/bye", {"id": CID_A})
        self.assertFalse(self.req("GET", "/api/ui")[1]["open"])

    def test_bad_ids_and_token(self):
        self.assertEqual(self.req("POST", "/api/ui/claim", {"id": "x"})[0], 400)
        self.assertEqual(self.req("POST", "/api/ui/claim", {})[0], 400)
        self.assertEqual(self.req("POST", "/api/ui/bye", {"id": 5})[0], 400)
        self.assertEqual(self.req("GET", "/api/ui", token=False)[0], 401)
        # 이상한 번호의 머리글은 무시 (오류 없이 지나감)
        self.assertEqual(self.req("GET", "/api/state", headers={"X-Desk-Client": "../x"})[0], 200)

    def test_launcher_can_ask_a_running_desk(self):
        self.req("POST", "/api/ui/claim", {"id": CID_A})
        state = window.fetch_ui_state(self.port)                    # 화면을 열 때와 같은 방법으로 토큰을 받아 물어봄
        self.assertEqual((state["open"], state["active"]), (True, CID_A))
        self.req("POST", "/api/ui/bye", {"id": CID_A})
        self.assertFalse(window.fetch_ui_state(self.port)["open"])
        self.assertIsNone(window.fetch_ui_state(1))                  # 꺼져 있으면 None

    def test_app_window_setting(self):
        self.assertTrue(self.env.config["app_window"])               # 기본으로 전용 창
        self.assertEqual(self.req("PUT", "/api/settings", {"app_window": False})[0], 200)
        self.assertFalse(self.env.config["app_window"])
        self.assertEqual(self.req("PUT", "/api/settings", {"app_window": "예"})[0], 400)
        self.req("PUT", "/api/settings", {"app_window": True})


if __name__ == "__main__":
    unittest.main()
