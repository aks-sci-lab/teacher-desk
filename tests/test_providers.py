import io
import json
import time
import unittest
import urllib.error
from unittest.mock import Mock, patch

from desk import ai, providers
from desk.collect import add_text_item
from desk.config import Config
from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests import test_server
from tests.test_telegram_more import CHAT, msg, press


RESPONSES = {
    "openai": {"status": "completed", "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": "공개 답변"}]}]},
    "anthropic": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "공개 답변"}]},
    "gemini": {"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"text": "숨길 생각", "thought": True}, {"text": "공개 답변"}]}}]},
}


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.env.db.replace_students([{"number": 7, "name": "최별빛"}])
        self.env.config.update({f"ai_{p}_{field}": "test-key" if field == "key" else "test-model"
                                for p in providers.EXTERNAL for field in ("key", "model")})

    def tearDown(self):
        self.env.close()

    def ask(self, provider="openai", question="달이 빛나는 원리를 설명해 줘", confirmed=True):
        return providers.ask(self.env.db, self.env.config, question, provider=provider, confirmed=confirmed)

    def test_external_payloads_have_only_explicit_question_not_private_records(self):
        add_text_item(self.env.db, "cool", "내부 공문 내용은 전송하지 않습니다", title="비공개 학교 자료")
        for provider in providers.EXTERNAL:
            with self.subTest(provider=provider), patch("desk.providers._request", return_value=RESPONSES[provider]) as request:
                self.assertEqual(self.ask(provider), "공개 답변")
                url, headers, payload = request.call_args.args
                self.assertTrue(url.startswith("https://"))
                self.assertNotIn("test-key", url)
                if provider == "openai":
                    self.assertEqual(headers["Authorization"], "Bearer " + "test-key")
                else:
                    self.assertIn("test-key", headers.values())
                encoded = json.dumps(payload, ensure_ascii=False)
                for private in ("최별빛", "비공개 학교 자료", "내부 공문"):
                    self.assertNotIn(private, encoded)
                self.assertIn("달이 빛나는 원리", encoded)
                self.assertIn("test-model", url if provider == "gemini" else payload["model"])
                if provider == "openai":
                    self.assertFalse(payload["store"])

    def test_consent_privacy_and_validation_fail_before_network(self):
        add_text_item(self.env.db, "edufine", "부서별 보고서 제출 대상 목록입니다", title="비공개 회의 계획 안내")
        with patch("desk.providers._request") as request:
            for question in ("최별빛의 성적", "연락처 test@example.org", "010-1234-5678",
                             "주민번호 061029-3000000", "비공개 회의 계획 안내를 설명",
                             "부서별 보고서 제출 대상 목록입니다"):
                with self.subTest(question=question), self.assertRaises(ai.AIError):
                    self.ask(question=question)
            for consent in (False, None, "true", 1):
                with self.assertRaises(ai.AIError):
                    self.ask(confirmed=consent)
            for question in ("", "가" * 4001, None):
                with self.assertRaises(ai.AIError):
                    self.ask(question=question)
            for provider in ("bad", "browser"):
                with self.assertRaises(ai.AIError):
                    self.ask(provider)
            request.assert_not_called()

    def test_local_reuses_existing_ai_without_external_consent(self):
        self.env.config.update({"ai_model": "local-model", "ai_provider": "gemini"})
        with patch("desk.providers.ai.ask", return_value="로컬 답") as ask, patch("desk.providers._request") as request:
            self.assertEqual(self.ask("local", "최별빛의 질문", confirmed=False), "로컬 답")
            self.assertEqual(ask.call_args.args[:3],
                             (self.env.config["ai_base"], "local-model", "최별빛의 질문"))
            request.assert_not_called()

    def test_missing_keys_invalid_models_and_incomplete_responses(self):
        for provider in providers.EXTERNAL:
            self.env.config.values[f"ai_{provider}_key"] = ""
            with self.assertRaises(ai.AIError):
                self.ask(provider)
            self.env.config.values[f"ai_{provider}_key"] = "test-key"
            for response in ({}, [], {"candidates": []}, {"output": [], "status": "incomplete"},
                             {"content": [{"type": "text", "text": "일부 답"}], "stop_reason": "max_tokens"}):
                with patch("desk.providers._request", return_value=response):
                    with self.assertRaises(ai.AIError):
                        self.ask(provider)
        self.env.config.values["ai_gemini_model"] = "../bad?key=oops"
        with patch("desk.providers._request") as request:
            with self.assertRaises(ai.AIError):
                self.ask("gemini")
            request.assert_not_called()

    def test_transport_no_proxy_no_redirect_timeout_and_sanitized_errors(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"ok": true}')
        with patch("desk.providers.urllib.request.build_opener", return_value=opener) as build:
            self.assertEqual(providers._request("https://api.openai.com/v1/responses", {"Authorization": "Bearer fake"},
                                                {"input": "public"}), {"ok": True})
            self.assertEqual(build.call_args.args[0].proxies, {})
            self.assertIsInstance(build.call_args.args[1], providers.NoRedirect)
            self.assertEqual(opener.open.call_args.kwargs["timeout"], 90)
            req = opener.open.call_args.args[0]
            self.assertEqual(json.loads(req.data), {"input": "public"})
        with self.assertRaises(ai.AIError):
            providers.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.org")
        for body in (b"not-json", b"x" * (providers.MAX_RESPONSE + 1)):
            opener.open.return_value = io.BytesIO(body)
            with patch("desk.providers.urllib.request.build_opener", return_value=opener):
                with self.assertRaises(ai.AIError):
                    providers._request("https://api.openai.com/v1/responses", {}, {})
        for error in (urllib.error.HTTPError("https://test", 401, "PRIVATE test-key", {}, io.BytesIO(b"PRIVATE")),
                      urllib.error.URLError("PRIVATE test-key")):
            opener.open.side_effect = error
            with patch("desk.providers.urllib.request.build_opener", return_value=opener):
                with self.assertRaises(ai.AIError) as caught:
                    providers._request("https://api.openai.com/v1/responses", {}, {})
                self.assertNotIn("PRIVATE", str(caught.exception))
                self.assertNotIn("test-key", str(caught.exception))

    def test_config_persistence_secret_masking_deletion_and_atomic_validation(self):
        config = self.env.config
        config.update({"ai_provider": "gemini"})
        restored = Config(config.path)
        self.assertEqual(restored["ai_provider"], "gemini")
        self.assertNotIn("test-key", json.dumps(restored.public()))
        for provider in providers.EXTERNAL:
            self.assertTrue(restored.public()[f"ai_{provider}_key_set"])
        for changes in ({"ai_provider": "bad"}, {"ai_openai_model": "bad/model?"},
                        {"ai_openai_key": "newline\nkey"}, {"ai_gemini_key": "한글"},
                        {"ai_anthropic_model": True}, {"ai_openai_key": "a" * 513}):
            before = config.public()
            with self.assertRaises(ValueError):
                config.update({**changes, "school_name": "저장하지 않을 값"})
            self.assertEqual(config.public(), before)
        config.update({"ai_gemini_key": ""})
        self.assertFalse(Config(config.path).public()["ai_gemini_key_set"])


class ProviderApiTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def test_api_authentication_validation_and_no_private_context(self):
        body = {"provider": "openai", "question": "공개 질문", "confirmed": True}
        with patch("desk.server.providers.ask", return_value="답변") as ask:
            self.assertEqual(self.req("POST", "/api/ai/question", body, token=False)[0], 401)
            ask.assert_not_called()
            self.assertEqual(self.req("POST", "/api/ai/question", body),
                             (200, {"text": "답변", "provider": "openai"}))
            self.assertEqual(ask.call_args.args[2], "공개 질문")
            self.assertTrue(ask.call_args.kwargs["confirmed"])
        for body in ({}, {"provider": "local", "question": ["bad"]},
                     {"provider": "local", "question": "x" * 4001},
                     {"provider": "browser", "question": "hello"},
                     {"provider": "openai", "question": "hello"}):
            self.assertEqual(self.req("POST", "/api/ai/question", body)[0], 400)
        self.env.config.update({"ai_openai_key": "SECRET-KEY", "ai_provider": "openai"})
        self.assertNotIn("SECRET-KEY", json.dumps(self.req("GET", "/api/state")[1]))


class TelegramProviderTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base,
                                      ai_provider="openai")
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.env.db.set_meta("telegram_chat_id", CHAT)

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def button(self):
        return self.fake.sent[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

    def test_external_requires_explicit_one_time_confirmation(self):
        with patch("desk.providers.ask", return_value="일반 답변") as ask:
            self.bot.handle_update(msg("/ask 달은 왜 빛나?"))
            button = self.button()
            self.assertIn("API 요금", self.fake.sent[-1]["text"])
            ask.assert_not_called()
            self.assertIsNone(self.bot.handle_update(press(button, chat=6)))
            self.bot.handle_update(press(button))
            self.assertEqual(ask.call_args.args[2], "달은 왜 빛나?")
            self.assertEqual(ask.call_args.kwargs, {"provider": "openai", "confirmed": True})
            self.bot.handle_update(press(button))
            self.assertEqual(ask.call_count, 1)

    def test_cancel_expiry_provider_change_and_browser_never_send(self):
        with patch("desk.providers.ask") as ask:
            self.bot.handle_update(msg("/ask 공개 질문"))
            button = self.button()
            self.bot.handle_update(msg("/cancel"))
            self.bot.handle_update(press(button))
            self.bot.handle_update(msg("/ask 공개 질문"))
            button = self.button()
            next(iter(self.bot.choice_requests.values()))["expires"] = time.monotonic() - 1
            self.bot.handle_update(press(button))
            self.bot.handle_update(msg("/ask 공개 질문"))
            button = self.button()
            self.env.config.values["ai_provider"] = "gemini"
            self.bot.handle_update(press(button))
            self.env.config.values["ai_provider"] = "browser"
            self.assertIn("앱의 'AI 질문'", self.bot.handle_update(msg("/ask hello")))
            ask.assert_not_called()

    def test_existing_work_search_is_still_local(self):
        with patch.object(self.bot, "ask") as work_ask, patch("desk.providers.ask") as generic:
            self.bot.handle_update(msg("내일 회의는 언제야?"))
            button = self.fake.sent[-1]["reply_markup"]["inline_keyboard"][-1][0]["callback_data"]
            self.bot.handle_update(press(button))
            work_ask.assert_called_once()
            generic.assert_not_called()
        with patch("desk.telegram.ai.models") as models:
            status = self.bot.answer("status")
            self.assertIn("업무자료 요약·검색 (로컬)", status)
            self.assertIn("일반 질문: OpenAI", status)
            models.assert_not_called()
