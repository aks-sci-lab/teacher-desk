import base64
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from desk import ai, localai
from desk.config import Config
from tests.test_ai_register import FakeLMStudio
from tests import test_server


class LocalAISetupTests(unittest.TestCase):
    def test_exact_recommendation_thresholds(self):
        for ram, free, disk, expected in [
            (11.9, 6, 7, None), (12, 5.9, 7, None), (12, 6, 6.9, None),
            (12, 6, 7, "qwen3-4b"), (23.9, 12, 12, "qwen3-4b"),
            (24, 11.9, 12, "qwen3-4b"), (24, 12, 11.9, "qwen3-4b"),
            (24, 12, 12, "qwen3-8b"),
        ]:
            with self.subTest(ram=ram, free=free, disk=disk):
                result = localai.recommend({"ram_gb": ram, "free_gb": free, "disk_gb": disk})
                self.assertEqual(result["selected"], expected)
                self.assertEqual(len(result["models"]), 2)
                for model in result["models"]:
                    self.assertEqual(model["eligible"], not model["reasons"])

    def test_hardware_uses_fixed_local_query_and_no_identity(self):
        data = {"cpu": "Test CPU", "cores": 8, "ram_gb": 24, "free_gb": 13, "gpus": ["Test GPU"]}
        result = subprocess.CompletedProcess([], 0, json.dumps(data).encode(), b"")
        with patch("desk.localai.platform.system", return_value="Windows"), \
                patch("desk.localai.subprocess.run", return_value=result) as run, \
                patch("desk.localai.shutil.disk_usage", return_value=shutil_usage(20)), \
                patch("desk.localai.shutil.which", return_value="lms.exe"):
            spec = localai.hardware(Path("."))
            self.assertEqual(spec["recommendation"]["selected"], "qwen3-8b")
            args = run.call_args.args[0]
            script = base64.b64decode(args[-1]).decode("utf-16-le")
            self.assertEqual(script, localai._HARDWARE)
            for key in ("SerialNumber", "UserName", "ComputerName"):
                self.assertNotIn(key, script)
            self.assertEqual(run.call_args.kwargs["timeout"], 20)
            self.assertEqual(set(spec), {*data, "disk_gb", "lms_installed", "recommendation"})

    def test_hardware_errors_are_not_success_shaped_defaults(self):
        with patch("desk.localai.platform.system", return_value="Linux"):
            with self.assertRaisesRegex(localai.SetupError, "Windows"):
                localai.hardware(Path("."))
        with patch("desk.localai.platform.system", return_value="Windows"):
            for result in (subprocess.CompletedProcess([], 1, b"", b"error"),
                           subprocess.CompletedProcess([], 0, b"not-json", b""),
                           subprocess.CompletedProcess([], 0, b"{}", b"")):
                with patch("desk.localai.subprocess.run", return_value=result):
                    with self.assertRaises(localai.SetupError):
                        localai.hardware(Path("."))
            with patch("desk.localai.subprocess.run", side_effect=subprocess.TimeoutExpired("ps", 20)):
                with self.assertRaises(localai.SetupError):
                    localai.hardware(Path("."))

    def test_batch_whitelist_encoding_fixed_loopback_and_length(self):
        for model in localai.MODELS:
            data = localai.setup_batch(model, 23456)
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
            text = data.decode("cp949")
            line = text.splitlines()[2]
            self.assertLess(len(line), 8000)
            script = base64.b64decode(line.rsplit(" ", 1)[1]).decode("utf-16-le")
            self.assertIn("server start --bind 127.0.0.1 --port 23456", script)
            self.assertIn("https://huggingface.co/" + localai.MODELS[model]["repo"], script)
            self.assertIn("ps --json", script)
            self.assertIn("if (-not ($loaded", script)
            self.assertIn("Read-Host", script)
            self.assertNotIn("--cors", script)
            self.assertNotIn("0.0.0.0", script)
            self.assertNotIn("config.json", script)
        for model in ("bad", "qwen3-4b';& calc", ""):
            with self.assertRaises(localai.SetupError):
                localai.setup_batch(model, 1234)
        for port in (True, "1234", 0, 1023, 65536, 1.5):
            with self.assertRaises(localai.SetupError):
                localai.setup_batch("qwen3-4b", port)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell 구문 검사")
    def test_powershell_scripts_parse_without_executing_downloads(self):
        scripts = [localai._HARDWARE]
        for model in localai.MODELS:
            line = localai.setup_batch(model, 23456).decode("cp949").splitlines()[2]
            scripts.append(base64.b64decode(line.rsplit(" ", 1)[1]).decode("utf-16-le"))
        for script in scripts:
            source = base64.b64encode(script.encode("utf-16-le")).decode()
            parser = ("$tokens=$null; $errors=$null; "
                      "[void][Management.Automation.Language.Parser]::ParseInput("
                      "[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('" + source
                      + "')), [ref]$tokens, [ref]$errors); if ($errors.Count) { exit 1 }")
            result = subprocess.run(localai._powershell(parser), capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(os.name == "nt", "Windows 배치 명령 흐름 검사")
    def test_setup_script_with_fake_cli_never_downloads_or_touches_running_ai(self):
        line = localai.setup_batch("qwen3-4b", 23456).decode("cp949").splitlines()[2]
        source = base64.b64decode(line.rsplit(" ", 1)[1]).decode("utf-16-le")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli = root / "lms.cmd"
            cli.write_bytes(
                b'@echo off\r\necho %*>>"%DESK_TEST_LOG%"\r\n'
                b'if "%1"=="ps" echo %DESK_TEST_LOADED%\r\n'
                b'if "%1"=="server" if "%2"=="status" echo %DESK_TEST_SERVER%\r\n'
                b'if "%1"=="load" if "%DESK_TEST_FAIL%"=="1" exit /b 1\r\nexit /b 0\r\n')
            prelude = (
                "function Get-Command { param($Name,$ErrorAction) "
                "if ($Name -ne 'lms') { throw 'Unexpected command discovery' }; "
                "[pscustomobject]@{Source=$env:DESK_TEST_CLI} }\n"
                "function Read-Host { param($Prompt) 'Y' }\n"
                "function Get-NetTCPConnection { param($LocalPort,$State,$ErrorAction) "
                "if ($env:DESK_TEST_EXPOSED -eq '1') { [pscustomobject]@{LocalAddress='0.0.0.0';LocalPort=23456} } }\n"
                "function Invoke-RestMethod { param($Uri,$TimeoutSec) "
                "if ($Uri -ne 'http://127.0.0.1:23456/v1/models') { throw 'Unexpected network request' }; "
                "[pscustomobject]@{data=@([pscustomobject]@{id='teacher-desk-qwen3-4b'})} }\n")
            for loaded, fail, exposed, server in [
                ("[]", False, False, '{"running":false}'),
                ('[{"identifier":"teacher-desk-qwen3-4b"}]', False, False, '{"running":true,"port":23456}'),
                ("[]", True, False, '{"running":false}'), ("[]", False, True, '{"running":false}'),
                ("[]", False, False, '{"running":true,"port":23457}'),
            ]:
                log = root / "commands.txt"
                log.unlink(missing_ok=True)
                env = os.environ.copy()
                env.update(DESK_TEST_CLI=str(cli), DESK_TEST_LOG=str(log), DESK_TEST_LOADED=loaded,
                           DESK_TEST_FAIL="1" if fail else "0", DESK_TEST_EXPOSED="1" if exposed else "0")
                env["DESK_TEST_SERVER"] = server
                result = subprocess.run(localai._powershell(prelude + source), capture_output=True, timeout=30, env=env)
                other_port = "23457" in server
                self.assertEqual(result.returncode, 1 if fail or exposed or other_port else 0, result.stderr)
                commands = log.read_text().splitlines() if log.exists() else []
                if exposed:
                    self.assertEqual(commands, [])
                elif other_port:
                    self.assertEqual(commands, ["daemon up", "server status --json"])
                elif fail:
                    self.assertFalse(any(c.startswith("server start") for c in commands))
                elif loaded != "[]":
                    self.assertEqual(commands, ["daemon up", "server status --json", "ps --json"])
                else:
                    self.assertEqual([c.split()[0] for c in commands], ["daemon", "server", "ps", "get", "load", "server"])
                    self.assertIn("Qwen3-4B-Q4_K_M.gguf", commands[3])

    def test_apply_checks_real_fake_completion_before_persisting(self):
        fake = FakeLMStudio(models=["teacher-desk-qwen3-4b"])
        try:
            with tempfile.TemporaryDirectory() as tmp:
                config = Config(Path(tmp) / "config.json")
                port = urlparse(fake.base).port
                before = config.public()
                fake.models = []
                with self.assertRaises(localai.SetupError):
                    localai.apply(config, "qwen3-4b", port)
                self.assertEqual(config.public(), before)
                self.assertFalse(config.path.exists())
                fake.models = ["teacher-desk-qwen3-4b"]
                fake.reply = ""
                with self.assertRaises(ai.AIError):
                    localai.apply(config, "qwen3-4b", port)
                self.assertEqual(config.public(), before)
                fake.reply = "준비됐습니다"
                result = localai.apply(config, "qwen3-4b", port)
                self.assertTrue(result["ok"])
                restored = Config(config.path)
                self.assertTrue(restored["ai_enabled"])
                self.assertEqual(restored["ai_model"], "teacher-desk-qwen3-4b")
                self.assertEqual(restored["ai_base"], fake.base)
                self.assertEqual(restored["ai_input_size"], "short")
                self.assertEqual(fake.requests[-1]["max_tokens"], 64)
                self.assertNotIn("자료:", fake.requests[-1]["messages"][1]["content"])
                with self.assertRaises(localai.SetupError):
                    localai.apply(config, "qwen3-4b", config["port"])
        finally:
            fake.close()


def shutil_usage(gb):
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    return usage(100 * 1024**3, (100 - gb) * 1024**3, gb * 1024**3)


class LocalAISetupApiTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def test_api_authentication_validation_and_no_install_side_effect(self):
        with patch("desk.server.localai.hardware", return_value={"cpu": "test"}) as hardware:
            self.assertEqual(self.req("POST", "/api/ai/setup/spec", {}, token=False)[0], 401)
            hardware.assert_not_called()
            self.assertEqual(self.req("POST", "/api/ai/setup/spec", {}), (200, {"cpu": "test"}))
        with patch("desk.localai.subprocess.run") as run:
            status, data = self.req("POST", "/api/ai/setup/batch", {"model": "qwen3-4b", "port": 23456})
            self.assertEqual(status, 200)
            self.assertTrue(data.startswith(b"@echo off\r\n"))
            run.assert_not_called()
        for body in ({"model": "bad"}, {"model": "qwen3-4b", "port": self.port},
                     {"model": "qwen3-4b", "port": "1234"}):
            self.assertEqual(self.req("POST", "/api/ai/setup/batch", body)[0], 400)
        with patch("desk.server.localai.apply") as apply:
            self.assertEqual(self.req("POST", "/api/ai/setup/apply", {"model": "qwen3-4b"}, token=False)[0], 401)
            apply.assert_not_called()
