"""텔레그램 문서 작성 (0.6.3): '/색인' 은 보낼 때만, '/문서 할 작업' → 관련 파일 고르기 → 새 docx 또는 보낸 서식에 채우기 → 파일 돌려받기.
학생 이름: AI 에 보내는 글·답·후보 목록에서 가리고, 만든 문서에 이름이 남아 있으면 텔레그램으로 보내지 않음."""
import unittest
from pathlib import Path

from desk import folderdoc, formfill
from desk.docwrite import docx_text
from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_folderdoc import FAKE, make_folder
from tests.test_formfill import ROWS, build_hwp, section
from tests.test_telegram_more import CHAT


class TelegramDocWriteTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.env.db.replace_students(FAKE)
        self.env.db.set_meta("telegram_chat_id", CHAT)
        root = Path(self.env.tmp.name)
        self.folder = root / "work"
        self.folder.mkdir()
        make_folder(self.folder)
        self.out = root / "documents"
        self.job = folderdoc.Job(self.env.db, self.env.config, self.out)
        self.prompts, self.answer = [], "# 2학기 과학과 협의회 회의록\n- 일시: 2026. 10. 15.\n- 가짜두리 학생 발표"

        def ask(prompt, system, max_tokens):
            self.prompts.append(prompt)
            return self.answer
        self.job._ask_factory = lambda: (ask, None, None)   # 진짜 LM Studio 대신
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.bot.folderdoc = self.job

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def say(self, text, chat=CHAT):
        return self.bot.handle_update({"update_id": 1, "message": {"message_id": 9, "chat": {"id": chat, "type": "private"},
                                                                   "text": text}})

    def press(self, data):
        return self.bot.handle_button({"id": "q", "data": data, "from": {"id": CHAT},
                                       "message": {"message_id": 3, "chat": {"id": CHAT, "type": "private"}}})

    def send_doc(self, name, data, fid="f1", chat=CHAT):
        self.fake.files[fid] = data
        return self.bot.handle_update({"update_id": 2, "message": {"message_id": 10, "chat": {"id": chat, "type": "private"},
                                                                   "document": {"file_id": fid, "file_name": name,
                                                                                "file_size": len(data)}}})

    def texts(self):
        return " ".join(m.get("text", "") for m in self.fake.sent + self.fake.edited)

    def index(self):
        self.env.config.values["folderdoc_folder"] = str(self.folder)
        self.say("/색인")
        self.assertIn("색인 완료: 파일 4개", self.texts())

    def buttons(self):
        return [b["callback_data"] for row in self.fake.sent[-1]["reply_markup"]["inline_keyboard"] for b in row]

    def test_index_only_when_sent_then_new_docx(self):
        self.assertEqual(folderdoc.counts(self.env.db)["files"], 0)
        self.assertIn("업무 폴더를 고르지 않았습니다", self.say("/문서 회의록"))
        self.env.config.values["folderdoc_folder"] = str(self.folder)
        self.assertIn("색인이 없습니다", self.say("/문서 회의록"))      # 색인을 저절로 만들지 않음
        self.assertEqual(folderdoc.counts(self.env.db)["files"], 0)
        self.index()
        view = self.say("/문서 2학기 과학과 협의회 회의록")
        self.assertIn("✅ 1.", view)
        self.assertIn("협의회", view)
        data = self.buttons()
        did = data[0].split(":")[1]
        self.assertIn(f"dlg:{did}:dn", data)
        self.press(f"dlg:{did}:dt:0")                                   # 1번 빼기 → 같은 메시지를 고침
        self.assertIn("▫️ 1.", self.fake.edited[-1]["text"])
        self.press(f"dlg:{did}:dt:0")
        self.press(f"dlg:{did}:dn")
        self.assertIn("sendDocument", self.fake.calls)
        self.assertNotIn("가짜하나", self.prompts[0])                   # AI 에 보내는 글의 이름 가림
        made = folderdoc.list_outputs(self.out)[0]["name"]
        self.assertTrue(made.endswith(".docx"))
        text = docx_text((self.out / made).read_bytes())
        self.assertNotIn("가짜두리", text)                              # AI 답의 이름도 가림
        self.assertIn("참고 파일".encode(), self.fake.docs[-1])          # 설명(caption)
        self.assertIsNone(self.bot.dialog)
        self.assertIn("오래된 입력 단추", self.press(f"dlg:{did}:dn"))   # 두 번 눌러도 한 번만

    def test_form_file_sent_in_chat_is_filled(self):
        self.index()
        self.say("/문서 2학기 과학과 협의회")
        form = build_hwp(section(ROWS))
        ids = {x["label"]: x["id"] for x in formfill.Form("서식.hwp", form).fields()}
        self.answer = f"{ids['일시']}: 2026. 10. 15.(목) 15:00\n{ids['장소']}: 과학실 2\n{ids['협의내용']}: 수행평가 기준 확정"
        self.assertIn("어떻게 쓸까요", self.send_doc("서식1 교과협의록.hwp", form))   # 서식인지 참고자료인지 물음 (0.6.11)
        did = self.bot.dialog["id"]
        self.assertEqual(self.buttons(), [f"dlg:{did}:pf", f"dlg:{did}:pr"])
        self.press(f"dlg:{did}:pf")
        self.assertIn("칸 4개를 채우는", self.texts())   # 제목 + 표 3칸
        self.assertIn("채운 칸 3개".encode(), self.fake.docs[-1])
        made = folderdoc.list_outputs(self.out)[0]["name"]
        self.assertTrue(made.endswith(".hwp"))
        self.assertIn("과학실 2", formfill.Form(made, (self.out / made).read_bytes()).text())

    def test_pc_form_button(self):
        self.index()
        (self.out / "forms").mkdir(parents=True)
        (self.out / "forms" / "current.hwp").write_bytes(build_hwp(section(ROWS)))
        self.env.db.set_meta(folderdoc.FORM_META, {"name": "학교서식.hwp"})
        self.say("/문서 과학과 협의회")
        did = self.buttons()[0].split(":")[1]
        self.assertIn(f"dlg:{did}:dp", self.buttons())
        self.answer = "P1: 2026학년도 과학과 협의록"
        self.press(f"dlg:{did}:dp")
        self.assertIn("sendDocument", self.fake.calls)

    def test_names_in_result_block_sending(self):
        (self.folder / "협의회" / "가짜하나 발표 협의회 메모.txt").write_text("발표 협의회 10월 20일 과학실", encoding="utf-8")
        self.env.config.values["folderdoc_folder"] = str(self.folder)
        self.say("/색인")
        view = self.say("/문서 발표 협의회 메모")
        self.assertIn("○○○○ 발표", view)                               # 후보 목록의 이름도 가림
        self.assertNotIn("가짜하나", self.texts())
        did = self.buttons()[0].split(":")[1]
        self.press(f"dlg:{did}:dn")                                      # 초안 끝 '참고한 파일' 에 이름이 남음
        self.assertNotIn("sendDocument", self.fake.calls)
        self.assertIn("학생 이름 1명이 들어 있어 텔레그램으로 보내지 않았습니다", self.texts())
        self.assertTrue(folderdoc.list_outputs(self.out))               # PC 에는 남음

    def test_off_and_guards(self):
        self.env.config.values["telegram_docwrite"] = False
        self.assertIn("꺼져 있습니다", self.say("/문서 회의록"))
        self.assertIn("꺼져 있습니다", self.say("/색인"))
        self.assertIn("받지 않았습니다", self.send_doc("서식.hwp", b"x"))
        self.env.config.values["telegram_docwrite"] = True
        self.assertIn("먼저 '/문서", self.send_doc("서식.hwp", b"x"))  # 고르기 전 서식은 받지 않음
        self.assertNotIn("getFile", self.fake.calls)
        self.assertIsNone(self.send_doc("서식.hwp", b"x", chat=999))    # 다른 사람
        self.assertIsNone(self.say("/문서 회의록", chat=999))
        self.bot.folderdoc = None
        self.assertIn("앱이 켜져 있을 때만", self.say("/문서 회의록"))

    def test_bad_form_and_ai_off(self):
        self.index()
        self.say("/문서 과학과 협의회")
        self.assertIn("파일을 읽지 못했습니다", self.send_doc("서식.hwp", b"not hwp"))
        self.assertEqual(self.bot.dialog["kind"], "doc")                # 다시 보낼 수 있게 그대로
        self.job._ask_factory = folderdoc.Job._ask_factory.__get__(self.job)
        did = self.bot.dialog["id"]
        self.assertIn("AI 요약을 먼저 켜세요", self.press(f"dlg:{did}:dn"))

    def test_words_and_menu(self):
        self.assertTrue(self.say("/문서").startswith("📝 만들 문서"))
        self.assertEqual(self.bot.dialog["step"], "task")
        self.assertIn("폴더를 고르지 않았습니다", self.say("과학과 협의회"))   # 대화로 받은 작업
        self.assertEqual(self.bot.dialog["step"], "pick")              # 참고자료만으로도 만들 수 있게 대화는 이어짐 (0.6.11)
        self.say("취소")
        self.assertIn("알아듣지 못했습니다", self.say("문서 작성 어떻게 해?"))   # 질문은 가로채지 않음
        self.assertTrue(self.say("문서 작성").startswith("📝"))
        self.bot.menu_button(CHAT, "doc", None)
        self.assertEqual(self.bot.dialog["kind"], "doc")
        self.assertIn("doc", [c["command"] for c in __import__("desk.telegram", fromlist=["BOT_MENU"]).BOT_MENU])


if __name__ == "__main__":
    unittest.main()
