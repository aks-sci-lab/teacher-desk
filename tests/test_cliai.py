"""0.6.24 일반 질문을 이 PC 의 CLI AI(Claude Code·Gemini·Codex)로 묻기."""

import subprocess
import unittest
from unittest.mock import patch

from desk import ai, cliai, providers
from desk.collect import add_text_item
from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_telegram_more import CHAT, msg


def done(out="CLI 답", code=0, err=""):
    return subprocess.CompletedProcess([], code, out, err)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.env.db.replace_students([{"number": 7, "name": "최별빛"}])

    def tearDown(self):
        self.env.close()

    def ask(self, provider="claude_cli", question="달이 빛나는 원리를 설명해 줘", confirmed=True):
        return providers.ask(self.env.db, self.env.config, question, provider=provider, confirmed=confirmed)

    def test_question_goes_only_by_stdin_in_empty_folder(self):
        add_text_item(self.env.db, "cool", "내부 공문 내용", title="비공개 학교 자료")
        self.env.config.values["ai_claude_cli_model"] = "sonnet"
        with patch("desk.cliai.find", return_value="/usr/bin/claude"), \
                patch("desk.cliai.subprocess.run", return_value=done("\x1b[1m공개 답\x1b[0m\n")) as run:
            self.assertEqual(self.ask(), "공개 답")
        cmd = run.call_args.args[0]
        self.assertEqual(cmd, ["/usr/bin/claude", "-p", "--output-format", "text", "--model", "sonnet"])
        kw = run.call_args.kwargs
        self.assertIn("달이 빛나는 원리", kw["input"])
        for private in ("최별빛", "비공개 학교 자료", "내부 공문"):
            self.assertNotIn(private, kw["input"])
        self.assertNotIn("달이", " ".join(cmd))   # 질문은 명령줄에 넣지 않음
        self.assertTrue(kw["cwd"])
        self.assertEqual(kw["timeout"], cliai.TIMEOUT)

    def test_commands_for_each_cli_and_default_model(self):
        self.assertEqual(cliai.command("gemini_cli", "g", ""), ["g"])
        self.assertEqual(cliai.command("gemini_cli", "g", "gemini-2.5-pro"), ["g", "-m", "gemini-2.5-pro"])
        codex = cliai.command("codex_cli", "c", "gpt-5")
        self.assertEqual(codex[:2], ["c", "exec"])
        self.assertIn("read-only", codex)
        self.assertEqual(codex[-1], "-")
        with self.assertRaises(ai.AIError):
            cliai.command("claude_cli", "c", "sonnet; del *")

    def test_consent_and_privacy_checks_before_running(self):
        with patch("desk.cliai.find", return_value="claude"), patch("desk.cliai.subprocess.run") as run:
            for question in ("최별빛의 성적", "010-1234-5678"):
                with self.assertRaises(ai.AIError):
                    self.ask(question=question)
            with self.assertRaises(ai.AIError):
                self.ask(confirmed=False)
            run.assert_not_called()

    def test_not_installed_failure_and_timeout_messages(self):
        with patch("desk.cliai.find", return_value=None):
            with self.assertRaisesRegex(ai.AIError, "찾지 못했습니다"):
                self.ask("codex_cli")
            with self.assertRaises(ai.AIError):
                providers.list_models(self.env.config, "gemini_cli")
        with patch("desk.cliai.find", return_value="claude"):
            with patch("desk.cliai.subprocess.run", return_value=done("", 1, "Please run /login")):
                with self.assertRaisesRegex(ai.AIError, "로그인"):
                    self.ask()
            with patch("desk.cliai.subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 1)):
                with self.assertRaisesRegex(ai.AIError, "오지 않아"):
                    self.ask()
            self.assertEqual(providers.list_models(self.env.config, "claude_cli"), ["sonnet", "opus", "haiku"])

    def test_config_accepts_cli_provider_and_model(self):
        self.env.config.update({"ai_provider": "gemini_cli", "ai_gemini_cli_model": "gemini-2.5-flash"})
        self.assertEqual(self.env.config["ai_provider"], "gemini_cli")
        with self.assertRaises(ValueError):
            self.env.config.update({"ai_codex_cli_model": "bad name!"})


class CliWorkTests(unittest.TestCase):
    """업무자료 AI 를 CLI 로 (선생님 허용 2026-11-27): 이름은 가려서 보냄, 기본은 LM Studio 그대로."""

    def setUp(self):
        self.env = Env()
        self.env.db.replace_students([{"number": 7, "name": "최별빛"}])

    def tearDown(self):
        self.env.close()

    def test_default_stays_local(self):
        self.env.config.values["ai_model"] = "gemma"
        self.assertEqual(ai.pick_model(self.env.config), "gemma")

    def test_cli_work_masks_names_and_skips_lm_studio(self):
        self.env.config.update({"ai_work_provider": "claude_cli", "ai_claude_cli_model": "opus"})
        model = ai.pick_model(self.env.config)
        self.assertEqual(model, "cli:claude_cli:opus")
        self.assertEqual(ai.model_label(model), "Claude Code CLI · opus")
        with patch("desk.cliai.find", return_value="claude"), \
                patch("desk.cliai.subprocess.run", return_value=done("요약")) as run, \
                patch("desk.ai._call") as lm:
            self.assertEqual(ai.ask(self.env.config["ai_base"], model, "최별빛 학생 결석 안내", db=self.env.db), "요약")
            lm.assert_not_called()
        sent = run.call_args.kwargs["input"]
        self.assertNotIn("최별빛", sent)
        self.assertIn("결석 안내", sent)
        self.assertIn("--model", run.call_args.args[0])

    def test_item_summary_goes_through_cli(self):
        self.env.config.update({"ai_work_provider": "gemini_cli"})
        it = {"source": "cool", "title": "회의 안내", "id": 1}
        with patch("desk.cliai.find", return_value="gemini"), \
                patch("desk.cliai.subprocess.run", return_value=done("- 11/30(월) · 회의")) as run:
            text = ai.summarize_item("http://127.0.0.1:1234/v1", ai.pick_model(self.env.config), it,
                                     "최별빛 관련 회의", ["최별빛"], db=self.env.db)
        self.assertTrue(text)
        self.assertEqual(run.call_args.args[0], ["gemini"])
        self.assertNotIn("최별빛", run.call_args.kwargs["input"])

    def test_images_refused_and_bad_value_rejected(self):
        with self.assertRaisesRegex(ai.AIError, "사진"):
            ai.ask("", "cli:claude_cli:", "글", images=[b"x"])
        with self.assertRaises(ValueError):
            self.env.config.update({"ai_work_provider": "openai"})


class CliTelegramTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base,
                                      ai_provider="claude_cli")
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.env.db.set_meta("telegram_chat_id", CHAT)

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def test_cli_asks_consent_with_cli_wording(self):
        with patch("desk.providers.ask") as ask:
            self.bot.handle_update(msg("/ask 달은 왜 빛나?"))
            text = self.fake.sent[-1]["text"]
            self.assertIn("Claude Code CLI로", text)
            self.assertIn("구독 이용 한도", text)
            ask.assert_not_called()


if __name__ == "__main__":
    unittest.main()
