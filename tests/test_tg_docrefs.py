"""텔레그램 문서 작성 참고자료 (0.6.11): 문서 작성 중에 보낸 파일·사진·글을 참고자료로 함께 씀.
참고자료는 이 PC 메모리에만 두고, AI 에 보내는 글·답에서 학생 이름을 가리며, 결과에 이름이 남으면 보내지 않는 보호는 그대로."""
import unittest
from unittest import mock

from desk import ai, docs, folderdoc
from desk.docwrite import docx_bytes, docx_text
from tests.test_ai_ttl import Fake
from tests.test_formfill import ROWS, build_hwp, section
from tests.test_telegram_more import CHAT
from tests import test_tg_docwrite


class TelegramDocRefTests(test_tg_docwrite.TelegramDocWriteTests):
    test_index_only_when_sent_then_new_docx = test_form_file_sent_in_chat_is_filled = None
    test_pc_form_button = test_names_in_result_block_sending = test_off_and_guards = None
    test_bad_form_and_ai_off = test_words_and_menu = None

    def say_msg(self, **msg):
        return self.bot.handle_update({"update_id": 3, "message": {"message_id": 11,
                                                                   "chat": {"id": CHAT, "type": "private"}, **msg}})

    def send_doc(self, name, data, fid="f1", chat=CHAT, caption=None):
        self.fake.files[fid] = data
        doc = {"file_id": fid, "file_name": name, "file_size": len(data)}
        return self.say_msg(document=doc, **({"caption": caption} if caption else {}))

    def send_photo(self, data, fid="p1"):
        self.fake.files[fid] = data
        return self.say_msg(photo=[{"file_id": "small", "file_size": 10}, {"file_id": fid, "file_size": len(data)}])

    def make(self):
        did = self.bot.dialog["id"]
        self.press(f"dlg:{did}:dn")
        self.assertIn("sendDocument", self.fake.calls)
        made = folderdoc.list_outputs(self.out)[0]["name"]
        return docx_text((self.out / made).read_bytes())

    def test_text_refs_without_index(self):
        """색인이 없어도 텔레그램 참고자료만으로 문서를 만듦. '참고 …'·전달한 글·긴 글."""
        view = self.say("/문서 3학년 진로 체험 안내")
        self.assertIn("폴더를 고르지 않았습니다", view)
        self.assertIn("'참고 내용'", view)
        self.assertIn("참고자료를 보내 주세요", self.press(f"dlg:{self.bot.dialog['id']}:dn"))
        view = self.say("참고: 11월 25일 9시 대학 탐방, 가짜하나 학생이 대표")
        self.assertIn("텔레그램 참고자료 1개", view)
        self.assertNotIn("가짜하나", self.texts())
        self.say_msg(text="버스 2대, 출발 장소는 정문", forward_origin={"type": "user"})   # 다른 대화에서 전달한 글
        self.say("가" * 301)                                                              # 긴 글도 참고자료
        self.assertEqual(len(self.bot.dialog["refs"]), 3)
        self.say("취소 아님 진로 체험")      # 짧은 글은 예전처럼 할 작업으로 다시 찾기 (참고자료는 그대로)
        self.assertEqual(len(self.bot.dialog["refs"]), 3)
        text = self.make()
        self.assertIn("11월 25일 9시", self.prompts[0])
        self.assertIn("정문", self.prompts[0])
        self.assertNotIn("가짜하나", self.prompts[0])                    # AI 에 보내는 글에서 이름 가림
        self.assertIn("(텔레그램) 텔레그램 글 1", text)
        self.assertIn("텔레그램 참고자료 3개".encode(), self.fake.docs[-1])

    def test_file_refs_with_index_and_clear(self):
        self.index()
        self.say("/문서 2학기 과학과 협의회 회의록")
        view = self.send_doc("공문.txt", "협의회 결과는 11월 30일까지 제출".encode("utf-8"))
        self.assertIn("📎 공문.txt", view)
        self.send_doc("메모.docx", docx_bytes("- 다음 협의회 안건: 실험실 안전 점검"))
        did = self.bot.dialog["id"]
        self.assertIn(f"dlg:{did}:dx", self.buttons())
        self.assertIn("읽지 못하는 형식", self.send_doc("묶음.zip", b"PK"))
        self.press(f"dlg:{did}:dx")                                       # 비우기
        self.assertEqual(self.bot.dialog["refs"], [])
        self.send_doc("공문.txt", "협의회 결과는 11월 30일까지 제출".encode("utf-8"))
        self.make()
        self.assertIn("11월 30일까지 제출", self.prompts[0])
        self.assertIn("[자료 1]", self.prompts[0])                        # 폴더 파일도 함께

    def test_form_like_file_caption_decides(self):
        self.index()
        self.say("/문서 2학기 과학과 협의회")
        form = build_hwp(section(ROWS))
        self.send_doc("작년 협의록.hwp", form, caption="참고")             # 빈칸이 있어도 '참고' 면 참고자료
        self.assertEqual(len(self.bot.dialog["refs"]), 1)
        self.send_doc("서식.hwp", form, fid="f2")                           # 묻기 → [참고자료로]
        self.press(f"dlg:{self.bot.dialog['id']}:pr")
        self.assertEqual(len(self.bot.dialog["refs"]), 2)
        self.answer = "P1: 2026학년도 과학과 협의록"
        self.send_doc("서식.hwp", form, fid="f3", caption="서식")           # '서식' 이면 바로 채움
        self.assertIn("칸 4개를 채우는", self.texts())
        self.assertIn("참고자료 2개", self.texts())
        self.assertIn("sendDocument", self.fake.calls)

    def test_refs_before_task_and_limit(self):
        self.env.config.values["folderdoc_folder"] = str(self.folder)
        self.say("/문서")
        form = build_hwp(section(ROWS))
        self.assertIn("할 작업)를 보내세요", self.send_doc("서식.hwp", form))   # 작업 전 파일은 참고자료로
        for i in range(4):
            self.say(f"참고 메모 {i} 내용")
        self.assertIn("5개까지", self.say("참고 하나 더"))
        self.say("2학기 과학과 협의회")
        self.assertEqual(self.bot.dialog["step"], "pick")
        self.assertEqual(len(self.bot.dialog["refs"]), 5)

    def test_photo_read_by_local_ai(self):
        self.say("/문서 체험학습 결과 보고서")
        self.env.config.values["ai_enabled"] = False
        self.assertIn("AI 가 꺼져 있어", self.send_photo(b"\xff\xd8jpg"))
        self.env.config.values["ai_enabled"] = True
        with mock.patch.object(ai, "pick_model", return_value="gemma"), \
                mock.patch.object(ai, "read_image", return_value="10월 2일 과학관 견학 가짜두리") as read:
            view = self.send_photo(b"\xff\xd8jpg")
        self.assertEqual(read.call_args[0][2], b"\xff\xd8jpg")              # 큰 사진을 받음
        self.assertIn("📎 사진 1", view)
        self.assertIn("과학관", self.bot.dialog["refs"][0]["text"])
        with mock.patch.object(ai, "pick_model", return_value="gemma"), \
                mock.patch.object(ai, "read_image", side_effect=ai.AIError("모델이 사진을 읽지 못합니다")):
            self.assertIn("사진을 읽지 못했습니다", self.send_photo(b"\xff\xd8jpg"))
        self.make()
        self.assertIn("과학관 견학", self.prompts[0])
        self.assertNotIn("가짜두리", self.prompts[0])

    def test_photo_outside_doc_is_ignored(self):
        self.assertIsNone(self.send_photo(b"\xff\xd8jpg"))
        self.assertNotIn("getFile", self.fake.calls)

    def test_other_chat_cannot_add(self):
        self.say("/문서 회의록")
        self.fake.files["x"] = b"hello"
        r = self.bot.handle_update({"update_id": 4, "message": {"message_id": 1, "chat": {"id": 999, "type": "private"},
                                                                "document": {"file_id": "x", "file_name": "a.txt"}}})
        self.assertIsNone(r)
        self.assertEqual(self.bot.dialog["refs"], [])


class RefPartsTests(unittest.TestCase):
    def test_read_bytes(self):
        self.assertEqual(docs.read_bytes("a.txt", "가나다".encode("cp949")), "가나다")
        self.assertIn("안건", folderdoc.read_bytes("a.docx", docx_bytes("- 안건: 안전")))
        with self.assertRaises(docs.DocError):
            docs.read_bytes("a.zip", b"x")

    def test_compact_keeps_short_extra_whole(self):
        class NoDB:
            def one(self, *a):
                return None
        out = folderdoc.compact_materials(NoDB(), [], "회의록", ["가짜하나"],
                                          extras=[{"name": "텔레그램 글 1", "text": "가짜하나 발표\n버스 2대"}])
        self.assertIn("버스 2대", out)                                       # 작업 낱말과 겹치지 않아도 통째로
        self.assertIn("○○○○ 발표", out)

    def test_ask_with_image(self):
        f = Fake()
        self.addCleanup(f.close)
        self.assertEqual(ai.read_image(f.base, "m", b"\x89PNG\r\n\x1a\nxx"), "답")
        content = f.requests[0]["messages"][1]["content"]
        self.assertEqual(content[1]["image_url"]["url"][:22], "data:image/png;base64,")
        bad = Fake(other_400=True)
        self.addCleanup(bad.close)
        with self.assertRaises(ai.AIError) as e:
            ai.read_image(bad.base, "m", b"jpg")
        self.assertIn("사진을 읽지 못합니다", str(e.exception))
        with self.assertRaises(ai.AIError):
            ai.read_image("http://example.com/v1", "m", b"jpg")             # 이 PC 밖으로는 보내지 않음


if __name__ == "__main__":
    unittest.main()
