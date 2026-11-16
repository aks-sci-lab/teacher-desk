"""문서 작성(desk/folderdoc.py) 시험: 폴더 색인(누를 때만)·관련 파일 찾기·참고 자료 줄이기·문서 만들기·API."""
import base64
import time
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import quote

from desk import folderdoc, formfill
from desk.docwrite import docx_bytes, docx_text
from tests.test_classroom_api import ApiBase
from tests.test_collect import Env
from tests.test_formfill import ROWS, build_hwp, section

FAKE = [{"number": 1, "name": "가짜하나"}, {"number": 2, "name": "가짜두리"}]


def make_folder(root):
    (root / "협의회").mkdir()
    (root / "협의회" / "1학기 과학과 협의회 회의록.docx").write_bytes(docx_bytes(
        "# 1학기 과학과 협의회 회의록\n- 일시: 2026. 4. 8. 15:00\n- 장소: 과학실 1\n"
        "- 수행평가 비율 40% 로 결정\n- 가짜하나 학생 실험 보조 선정\n그 밖의 잡담 줄입니다"))
    (root / "협의회" / "2학기 과학과 협의회 계획.txt").write_text(
        "2학기 과학과 협의회는 10월 15일 과학실 2 에서 연다.\n참석 대상: 과학과 교사 5명\n안건: 수행평가 기준", encoding="utf-8")
    (root / "급식 안내.txt").write_text("11월 급식 식단표 안내입니다. 알레르기 표시 확인", encoding="utf-8")
    (root / "~$잠김.docx").write_bytes(b"x")
    (root / "깨진.hwp").write_bytes(b"not hwp")


class FolderDocTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.env.db.replace_students(FAKE)
        self.folder = Path(self.env.tmp.name) / "work"
        self.folder.mkdir()
        make_folder(self.folder)
        self.out = Path(self.env.tmp.name) / "out"

    def tearDown(self):
        self.env.close()

    def test_index_skips_unchanged_and_removes_deleted(self):
        db = self.env.db
        st = folderdoc.index_folder(db, self.folder)
        self.assertEqual((st["files"], st["new"], st["errors"]), (4, 4, 1))   # 잠금 파일은 건너뜀
        st = folderdoc.index_folder(db, self.folder)
        self.assertEqual((st["new"], st["unchanged"]), (0, 4))
        (self.folder / "급식 안내.txt").unlink()
        st = folderdoc.index_folder(db, self.folder)
        self.assertEqual(st["removed"], 1)
        self.assertEqual(folderdoc.counts(db)["files"], 3)

    def test_index_with_summary_and_stop(self):
        calls = []
        st = folderdoc.index_folder(self.env.db, self.folder, summarize=lambda rel, t: calls.append(rel) or f"요약 {rel}")
        self.assertEqual(st["summarized"], 3)      # 못 읽은 파일은 요약 안 함
        st = folderdoc.index_folder(self.env.db, self.folder, summarize=lambda rel, t: calls.append(rel))
        self.assertEqual(st["summarized"], 0)      # 이미 요약된 파일은 다시 안 함
        st = folderdoc.index_folder(self.env.db, self.folder, stop=lambda: True)
        self.assertTrue(st["stopped"])

    def test_search_ranks_related_files(self):
        folderdoc.index_folder(self.env.db, self.folder)
        found = folderdoc.search(self.env.db, "2학기 과학과 협의회 회의록 작성")
        self.assertEqual({Path(f["path"]).stem for f in found[:2]},
                         {"1학기 과학과 협의회 회의록", "2학기 과학과 협의회 계획"})
        self.assertNotIn("급식 안내.txt", [f["path"] for f in found[:2]])
        self.assertEqual(folderdoc.search(self.env.db, ""), [])

    def test_compact_materials_short_relevant_masked(self):
        db = self.env.db
        folderdoc.index_folder(db, self.folder)
        paths = [f["path"] for f in folderdoc.search(db, "과학과 협의회")[:2]]
        m = folderdoc.compact_materials(db, paths, "과학과 협의회 수행평가", ["가짜하나"])
        self.assertLessEqual(len(m), folderdoc.COMPACT_CHARS + 40)
        self.assertIn("40%", m)
        self.assertIn("10월 15일", m)
        self.assertNotIn("가짜하나", m)
        self.assertNotIn("잡담", m)

    def test_make_document_new_docx_masks_names(self):
        db = self.env.db
        folderdoc.index_folder(db, self.folder)
        sent = []

        def ask(prompt, system, max_tokens):
            sent.append(prompt)
            return "# 2학기 과학과 협의회 회의록\n- 일시: 2026. 10. 15.\n- 가짜두리 학생 발표"
        paths = ["협의회/2학기 과학과 협의회 계획.txt", "협의회/1학기 과학과 협의회 회의록.docx"]
        res = folderdoc.make_document(db, self.env.config, "2학기 과학과 협의회 회의록", paths,
                                      out_dir=self.out, ask=ask, today=date(2026, 11, 16))
        self.assertNotIn("가짜하나", sent[0])
        text = docx_text((self.out / res["file"]).read_bytes())
        self.assertIn("2026. 10. 15.", text)
        self.assertNotIn("가짜두리", text)           # AI 답에 이름이 나와도 가림
        self.assertIn("참고한 파일", text)
        self.assertEqual(folderdoc.list_outputs(self.out)[0]["name"], res["file"])

    def test_make_document_fills_hwp_form(self):
        db = self.env.db
        folderdoc.index_folder(db, self.folder)
        form = ("서식.hwp", build_hwp(section(ROWS)))
        ids = {x["label"]: x["id"] for x in formfill.Form(*form).fields()}
        ans = f"{ids['일시']}: 2026. 10. 15.(목) 15:00\n{ids['장소']}: 과학실 2\n{ids['협의내용']}: 수행평가 기준 확정"
        res = folderdoc.make_document(db, self.env.config, "2학기 협의록", ["협의회/2학기 과학과 협의회 계획.txt"],
                                      form=form, out_dir=self.out, ask=lambda *a: ans)
        self.assertTrue(res["file"].endswith(".hwp"))
        self.assertEqual(len(res["filled"]), 3)
        text = formfill.Form(res["file"], (self.out / res["file"]).read_bytes()).text()
        self.assertIn("과학실 2", text)
        self.assertIn("2025학년도 과학과 협의록", text)    # 고르지 않은 제목은 그대로
        with self.assertRaises(folderdoc.FolderDocError):
            folderdoc.make_document(db, self.env.config, "x", ["a"], form=form, out_dir=self.out, ask=lambda *a: "모르겠어요")

    def test_output_path_blocks_escape(self):
        self.out.mkdir()
        (self.out / "a.docx").write_bytes(b"x")
        self.assertEqual(folderdoc.output_path(self.out, "a.docx").name, "a.docx")
        for bad in ("../desk.db", "..\\x.docx", ".hidden.docx", "", "없음.docx"):
            with self.assertRaises(folderdoc.FolderDocError):
                folderdoc.output_path(self.out, bad)


class FolderDocApiTests(ApiBase):
    def wait_idle(self):
        for _ in range(100):
            st = self.req("GET", "/api/folderdoc")[1]
            if not st["job"]["running"]:
                return st
            time.sleep(0.05)
        self.fail("작업이 끝나지 않음")

    def test_index_only_when_pressed_then_search_and_form(self):
        folder = Path(self.env.tmp.name) / "work"
        folder.mkdir()
        make_folder(folder)
        st = self.req("GET", "/api/folderdoc")[1]
        self.assertEqual(st["index"]["files"], 0)          # 폴더를 골라도 자동으로 돌지 않음
        self.assertEqual(self.req("POST", "/api/folderdoc/index", {})[0], 400)   # 폴더 없음
        self.env.config.values["folderdoc_folder"] = str(folder)
        self.assertEqual(self.req("GET", "/api/folderdoc")[1]["index"]["files"], 0)
        self.assertEqual(self.req("POST", "/api/folderdoc/index", {"ai": True})[0], 400)   # AI 꺼져 있음
        self.assertEqual(self.req("POST", "/api/folderdoc/index", {})[0], 200)
        st = self.wait_idle()
        self.assertEqual(st["index"]["files"], 4)
        self.assertEqual(st["job"]["result"]["kind"], "index")
        code, r = self.req("GET", "/api/folderdoc/search?q=" + quote("과학과 협의회"))
        self.assertEqual(code, 200)
        self.assertTrue(r["files"])
        # 서식 올리기 → 미리보기 → 빼기
        data = base64.b64encode(build_hwp(section(ROWS))).decode()
        code, meta = self.req("POST", "/api/folderdoc/form", {"name": "서식.hwp", "data": data})
        self.assertEqual(code, 200)
        self.assertTrue(meta["preview"])
        self.assertEqual(self.req("GET", "/api/folderdoc")[1]["form"]["name"], "서식.hwp")
        self.assertEqual(self.req("POST", "/api/folderdoc/form", {"name": "x.hwp", "data": "!!"})[0], 400)
        self.assertEqual(self.req("DELETE", "/api/folderdoc/form")[0], 200)
        self.assertIsNone(self.req("GET", "/api/folderdoc")[1]["form"])
        # AI 가 꺼져 있으면 문서 만들기도 안 됨, 받기는 경로를 벗어나지 못함
        self.assertEqual(self.req("POST", "/api/folderdoc/draft", {"task": "회의록", "files": ["a"]})[0], 400)
        self.assertEqual(self.req("POST", "/api/folderdoc/draft", {"task": "회의록", "files": []})[0], 400)
        self.assertEqual(self.req("GET", "/api/folderdoc/output?name=..%2Fdesk.db")[0], 404)


if __name__ == "__main__":
    unittest.main()
