"""설정 개편·첫 실행 설정 도우미 (0.5.79): 키 형식 그대로, 도우미 상태 저장, 비밀값은 화면에 안 돌려줌."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from desk.config import DEFAULTS, EDITABLE_KEYS, SECRET_KEYS, Config
from tests import test_server  # ServerTests 를 이름으로 가져오면 그 시험이 여기서 또 돔

WEB = Path(__file__).resolve().parent.parent / "web" / "js"


class SetupConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_key_only_and_states(self):
        self.assertEqual(DEFAULTS["setup_wizard"], "")
        c = Config(self.path)
        for v in ("later", "done", "notified", ""):
            c.update({"setup_wizard": v})
            self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["setup_wizard"], v)
        for bad in ("yes", True, 1, None):
            with self.assertRaises(ValueError):
                c.update({"setup_wizard": bad})

    def test_old_config_keeps_working(self):
        # 도우미 이전 config.json (setup_wizard 없음) → 그대로 읽고, 다른 키는 그대로 저장
        old = {"school_name": "가나고", "grade": "2", "class_no": "3", "telegram_token": "T:1", "collect_times": ["09:00"]}
        self.path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        c = Config(self.path)
        self.assertEqual(c["setup_wizard"], "")
        c.update({"setup_wizard": "notified"})
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        for k, v in old.items():
            self.assertEqual(saved[k], v, k)
        pub = c.public()
        self.assertNotIn("telegram_token", pub)
        self.assertTrue(pub["telegram_token_set"])

    def test_telegram_defaults_unchanged(self):
        self.assertEqual((DEFAULTS["telegram_detail"], DEFAULTS["telegram_photos"], DEFAULTS["telegram_cool_text"]),
                         ("titles", True, False))


class SetupServerTests(test_server.ServerTests):
    """서버를 띄운 ServerTests 를 이어받되 여기 시험만 돌림."""
    def test_wizard_done_saved_through_api(self):
        st, r = self.req("PUT", "/api/settings", {"school_name": "도우미고", "grade": "1", "class_no": "4"})
        self.assertEqual(st, 200)
        st, r = self.req("PUT", "/api/settings", {"setup_wizard": "done"})
        self.assertEqual((st, r["settings"]["setup_wizard"]), (200, "done"))
        self.assertEqual(self.req("GET", "/api/state")[1]["settings"]["setup_wizard"], "done")
        self.assertEqual(self.req("PUT", "/api/settings", {"setup_wizard": "아무거나"})[0], 400)

    def test_secret_is_never_returned(self):
        st, r = self.req("PUT", "/api/settings", {"neis_key": "SECRET-NEIS-123"})
        self.assertEqual(st, 200)
        self.assertNotIn("SECRET-NEIS-123", json.dumps(r, ensure_ascii=False))
        self.assertTrue(r["settings"]["neis_key_set"])
        self.assertNotIn("SECRET-NEIS-123", json.dumps(self.req("GET", "/api/state")[1], ensure_ascii=False))


# 부모의 시험이 여기서 다시 돌지 않게
for name in [n for n in dir(test_server.ServerTests) if n.startswith("test_")]:
    if not hasattr(SetupServerTests, name) or getattr(SetupServerTests, name) is getattr(test_server.ServerTests, name):
        setattr(SetupServerTests, name, None)


class SettingsScreenTests(unittest.TestCase):
    """화면이 모든 설정 키를 다루는지 (빠뜨린 칸이 없게) — 실제 모양은 브라우저 캡처로 확인."""
    def test_every_editable_key_has_a_place(self):
        js = "".join((WEB / "views" / f).read_text(encoding="utf-8") for f in ("settings.js", "setfields.js", "setup.js"))
        # 일반 질문 AI(외부 API 키·모델)는 AI 화면(ai.js), 화면 배치는 배치 모드에서 저장
        elsewhere = {"layout", "ai_provider", "ai_openai_model", "ai_anthropic_model", "ai_gemini_model",
                     "ai_openai_key", "ai_anthropic_key", "ai_gemini_key", "neis_school"}
        for key in sorted(EDITABLE_KEYS - elsewhere):
            self.assertRegex(js, rf"['\"]{key}['\"]|\b{key}:", key)

    def test_secret_fields_are_password_and_not_prefilled(self):
        js = (WEB / "views" / "setfields.js").read_text(encoding="utf-8")
        block = js[js.index("export function secretField"):js.index("export function fieldValue")]
        self.assertIn("type: 'password'", block)
        self.assertNotRegex(block, r"value:\s*store\.settings")
        for key in ("telegram_token", "neis_key"):
            self.assertIn(key, SECRET_KEYS)

    def test_first_run_hook_in_main(self):
        main = (WEB / "main.js").read_text(encoding="utf-8")
        self.assertIn("maybeStartSetup(s.settings)", main)
        self.assertRegex(main, r"if \(!silentPrintId\) guarded\(\(\) => maybeStartSetup")


if __name__ == "__main__":
    unittest.main()
