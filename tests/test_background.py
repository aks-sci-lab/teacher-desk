import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from desk import background


class _Cfg(dict):
    data_dir = "data"


class BackgroundTests(unittest.TestCase):
    def test_existing_app_is_reused_without_another_process(self):
        with patch.object(background.os, "name", "nt"), \
                patch.object(background, "is_running", return_value=True), \
                patch.object(background.subprocess, "Popen") as start, \
                patch.object(background.window, "show_ui") as show:
            config = _Cfg({"port": 8770, "app_window": True})
            self.assertEqual(background.launch(config, ["--background"]), 0)
            start.assert_not_called()   # 두 번째 프로세스를 만들지 않고, 열린 창을 앞으로(없으면 전용 창을 새로)
            show.assert_called_once_with(8770, "data", True)

    @unittest.skipUnless(os.name == "nt", "Windows 실행 방식 검사")
    def test_new_process_has_no_console_and_startup_failure_is_reported(self):
        with patch.object(background, "is_running", return_value=False), \
                patch.object(background.subprocess, "Popen") as start:
            start.return_value.poll.return_value = 1
            with self.assertRaises(background.BackgroundError):
                background.launch({"port": 18774}, ["--background", "--no-browser", "--config", "example.json"])
            args, kwargs = start.call_args
            self.assertNotIn("--background", args[0])
            self.assertIn("--no-browser", args[0])
            self.assertEqual(kwargs["creationflags"],
                             subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)

    @unittest.skipUnless(os.name == "nt", "Windows 실행 방식 검사")
    def test_single_instance_lock_is_released(self):
        with background.single_instance(18773) as first:
            self.assertTrue(first)
            with background.single_instance(18773) as second:
                self.assertFalse(second)
        with background.single_instance(18773) as again:
            self.assertTrue(again)

    @unittest.skipUnless(os.name == "nt", "Windows 백그라운드 실제 실행 검사")
    def test_background_server_survives_launcher_and_shuts_down(self):
        with tempfile.TemporaryDirectory() as tmp:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({
                "data_dir": str(Path(tmp) / "data"), "port": port,
                "auto_update": False, "watch_enabled": False,
            }), encoding="utf-8")
            command = [sys.executable, str(background.APP_DIR / "desk.py"),
                       "--background", "--no-browser", "--config", str(config)]
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                result = subprocess.run(command, capture_output=True, timeout=40)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertTrue(background.is_running(port))
                with opener.open(f"http://127.0.0.1:{port}/") as response:
                    html = response.read().decode("utf-8")
                token = re.search(r'name="desk-token"\s+content="([^"]+)"', html)[1]
                result = subprocess.run(command, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout)
            finally:
                if background.is_running(port):
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/shutdown", data=b"{}",
                        headers={"X-Desk-Token": token, "Content-Type": "application/json"})
                    with opener.open(request, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                    deadline = time.monotonic() + 10
                    while background.is_running(port) and time.monotonic() < deadline:
                        time.sleep(0.1)
                    self.assertFalse(background.is_running(port))
