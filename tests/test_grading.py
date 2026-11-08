"""손글씨 채점 (0.5.80): 평가·답안지·손글씨 읽기·점수 제안·내보내기. LM Studio 는 가짜로 대신함."""

import base64
import io
import json
import unittest
from unittest.mock import patch

from desk import ai, grading
from tests.test_collect import Env

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def b64(data):
    return base64.b64encode(data).decode("ascii")


def scanned_pdf(pages):
    """쪽마다 JPEG 한 장이 든 PDF (복사기 스캔과 같은 모양)."""
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, NumberObject, StreamObject
    w = PdfWriter()
    for data in pages:
        page = w.add_blank_page(100, 100)
        img = StreamObject()
        img._data = data
        img.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                    NameObject("/Filter"): NameObject("/DCTDecode"), NameObject("/Width"): NumberObject(10),
                    NameObject("/Height"): NumberObject(10), NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                    NameObject("/BitsPerComponent"): NumberObject(8)})
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): w._add_object(img)})})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


QUESTIONS = [
    {"number": 1, "max_score": 5, "prompt": "광합성의 뜻을 쓰시오", "answer": "빛에너지로 양분을 만드는 과정",
     "rubric": "빛에너지 2점, 양분(포도당) 3점"},
    {"number": 2, "max_score": 3, "prompt": "엽록체의 역할", "answer": "광합성이 일어나는 곳", "rubric": ""},
]


class GradingTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.db, self.config = self.env.db, self.env.config
        self.db.replace_students([{"number": 1, "name": "김민준", "parent_phone": None},
                                  {"number": 2, "name": "이서연", "parent_phone": None},
                                  {"number": 4, "name": "박지후", "parent_phone": None}])
        self.config.values.update(ai_model="text-model", grade_ocr_model="vision-model")
        self.task = grading.create_task(self.db, {"title": "과학 서술형", "kind": "essay", "questions": QUESTIONS})

    def tearDown(self):
        self.env.close()

    def upload(self, files, **kw):
        return grading.add_sheets(self.db, self.config.data_dir, self.task["id"], {"files": files, **kw})

    # ---- 평가 ----
    def test_questions_validated(self):
        with self.assertRaises(grading.GradeError):
            grading.clean_questions([])
        with self.assertRaises(grading.GradeError):
            grading.clean_questions([{"number": 1}, {"number": 1}])
        with self.assertRaises(grading.GradeError):
            grading.clean_questions([{"number": 1, "max_score": -1}])
        with self.assertRaises(grading.GradeError):
            grading.create_task(self.db, {"title": "", "questions": QUESTIONS})

    def test_update_keeps_answers_by_number_and_drops_removed(self):
        self.upload([{"name": "a.jpg", "data": b64(JPEG)}])
        t = grading.task(self.db, self.task["id"])
        sheet, q1, q2 = t["sheets"][0], t["questions"][0], t["questions"][1]
        grading.update_answer(self.db, {"score": 4}, None, sheet["id"], q1["id"])
        grading.update_answer(self.db, {"score": 2}, None, sheet["id"], q2["id"])
        t = grading.update_task(self.db, self.task["id"], {"title": "고침", "kind": "performance",
                                                           "questions": [{**QUESTIONS[0], "max_score": 10}]})
        self.assertEqual([q["max_score"] for q in t["questions"]], [10.0])
        self.assertEqual(t["questions"][0]["id"], q1["id"])   # 번호가 같으면 같은 문항
        self.assertEqual(t["sheets"][0]["total"], 4.0)

    # ---- 답안지 ----
    def test_upload_assigns_roster_in_order_and_groups_pages(self):
        r = self.upload([{"name": f"{i}.jpg", "data": b64(JPEG)} for i in range(6)], pages_per_sheet=2, start_number=1)
        self.assertEqual(len(r["created"]), 3)
        sheets = grading.task(self.db, self.task["id"])["sheets"]
        # 명단은 1, 2, 4번 → 3번은 건너뜀
        self.assertEqual([(s["student_number"], s["student_name"], s["pages"]) for s in sheets],
                         [(1, "김민준", 2), (2, "이서연", 2), (4, "박지후", 2)])
        self.assertEqual(r["next_number"], 5)
        r = self.upload([{"name": "x.png", "data": b64(PNG)}], start_number=5)
        self.assertEqual(grading.sheet_row(self.db, r["created"][0])["student_name"], None)   # 명단 밖은 번호만
        data, ctype = grading.sheet_image(self.db, self.config.data_dir, r["created"][0], 1)
        self.assertEqual((data, ctype), (PNG, "image/png"))

    def test_upload_rejects_bad_files(self):
        with self.assertRaises(grading.GradeError):
            self.upload([{"name": "a.txt", "data": b64(b"hello")}])
        with self.assertRaises(grading.GradeError):
            self.upload([{"name": "a.jpg", "data": "!!!"}])
        with self.assertRaises(grading.GradeError):
            self.upload([{"name": "a.jpg", "data": b64(JPEG)}] * 3, pages_per_sheet=2)

    def test_scanned_pdf_pages(self):
        p1, p2 = JPEG + b"1", JPEG + b"2"
        r = self.upload([{"name": "scan.pdf", "data": b64(scanned_pdf([p1, p2]))}], pages_per_sheet=1)
        self.assertEqual(len(r["created"]), 2)
        self.assertEqual(grading.sheet_image(self.db, self.config.data_dir, r["created"][1], 1)[0], p2)

    def test_delete_task_removes_images(self):
        self.upload([{"name": "a.jpg", "data": b64(JPEG)}])
        folder = self.config.data_dir / "grading" / str(self.task["id"])
        self.assertTrue(any(folder.iterdir()))
        grading.delete_task(self.db, self.config.data_dir, self.task["id"])
        self.assertFalse(folder.exists())
        self.assertEqual(self.db.query("SELECT * FROM grade_sheets"), [])

    # ---- 손글씨 읽기 ----
    def test_parse_ocr(self):
        self.assertEqual(grading.parse_ocr("[1] 빛 에너지\n둘째 줄\n[2] [빈칸]", [1, 2]), {1: "빛 에너지\n둘째 줄", 2: ""})
        self.assertEqual(grading.parse_ocr("그냥 답", [3]), {3: "그냥 답"})
        self.assertEqual(grading.parse_ocr("[9] 없는 번호", [1]), {})

    def test_read_sheet_sends_image_to_local_vision_model_and_keeps_teacher_edits(self):
        sid = self.upload([{"name": "a.jpg", "data": b64(JPEG)}])["created"][0]
        calls = []

        def fake(base, model, prompt, timeout=0, system="", max_tokens=0, db=None, images=()):
            calls.append((base, model, images))
            return "[1] 빛에너지로 포도당을 만듬\n[2] 광합성 장소"
        with patch.object(ai, "ask", fake):
            grading.read_sheet(self.db, self.config, self.config.data_dir, sid)
        base, model, images = calls[0]
        self.assertTrue(base.startswith("http://127.0.0.1"))
        self.assertEqual(model, "vision-model")
        self.assertEqual(images, ["data:image/jpeg;base64," + b64(JPEG)])
        t = grading.task(self.db, self.task["id"])
        answers = {a["question_id"]: a for a in t["sheets"][0]["answers"].values()}
        q1 = t["questions"][0]["id"]
        self.assertEqual(answers[q1]["text"], "빛에너지로 포도당을 만듬")
        self.assertEqual(t["sheets"][0]["ocr_status"], "done")
        # 선생님이 고친 답은 다시 읽어도 그대로, AI 원래 글만 바뀜
        grading.update_answer(self.db, {"text": "빛에너지로 포도당을 만듦"}, answers[q1]["id"])
        with patch.object(ai, "ask", lambda *a, **k: "[1] 다시 읽은 글\n[2] 장소"):
            grading.read_sheet(self.db, self.config, self.config.data_dir, sid)
        a = self.db.one("SELECT * FROM grade_answers WHERE id = ?", (answers[q1]["id"],))
        self.assertEqual((a["text"], a["ocr_text"], a["edited"]), ("빛에너지로 포도당을 만듦", "다시 읽은 글", 1))

    def test_remote_ai_address_refused(self):
        sid = self.upload([{"name": "a.jpg", "data": b64(JPEG)}])["created"][0]
        self.config.values["ai_base"] = "https://api.example.com/v1"
        with patch.object(ai, "ask", side_effect=AssertionError("보내면 안 됨")):
            with self.assertRaises(ai.AIError):
                grading.read_sheet(self.db, self.config, self.config.data_dir, sid)

    # ---- 점수 ----
    def test_parse_score_clamps(self):
        self.assertEqual(grading.parse_score("점수: 7\n이유: 다 맞음", 5), (5.0, "다 맞음"))
        self.assertEqual(grading.parse_score("점수 2.5 이유: 일부", 5)[0], 2.5)
        with self.assertRaises(grading.GradeError):
            grading.parse_score("모르겠음", 5)

    def test_suggest_masks_names_and_does_not_set_final_score(self):
        sid = self.upload([{"name": "a.jpg", "data": b64(JPEG)}])["created"][0]
        q1 = grading.questions(self.db, self.task["id"])[0]
        a = grading.update_answer(self.db, {"text": "김민준: 빛에너지로 포도당"}, None, sid, q1["id"])
        prompts = []

        def fake(base, model, prompt, *args, **kw):
            prompts.append((model, prompt))
            return "점수: 4\n이유: 김민준 학생은 양분을 썼음"
        with patch.object(ai, "ask", fake):
            r = grading.suggest_score(self.db, self.config, a["id"])
        self.assertEqual(prompts[0][0], "text-model")
        self.assertNotIn("김민준", prompts[0][1])
        self.assertIn("채점 기준", prompts[0][1])
        self.assertNotIn("김민준", r["ai_reason"])
        row = self.db.one("SELECT * FROM grade_answers WHERE id = ?", (a["id"],))
        self.assertEqual((row["ai_score"], row["score"]), (4.0, None))
        grading.update_answer(self.db, {"accept_ai": True}, a["id"])
        self.assertEqual(self.db.one("SELECT score FROM grade_answers WHERE id = ?", (a["id"],))["score"], 4.0)
        with self.assertRaises(grading.GradeError):
            grading.update_answer(self.db, {"score": 6}, a["id"])   # 만점 넘음

    def test_empty_answer_gets_zero_without_ai(self):
        sid = self.upload([{"name": "a.jpg", "data": b64(JPEG)}])["created"][0]
        q2 = grading.questions(self.db, self.task["id"])[1]
        a = grading.update_answer(self.db, {"text": ""}, None, sid, q2["id"])
        with patch.object(ai, "ask", side_effect=AssertionError("부르면 안 됨")):
            self.assertEqual(grading.suggest_score(self.db, self.config, a["id"])["ai_score"], 0.0)

    def test_export_csv(self):
        sid = self.upload([{"name": "a.jpg", "data": b64(JPEG)}])["created"][0]
        q1 = grading.questions(self.db, self.task["id"])[0]
        grading.update_answer(self.db, {"score": 4.5, "comment": "양분 용어 정확히"}, None, sid, q1["id"])
        data, title = grading.export_csv(self.db, self.task["id"])
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        lines = data.decode("utf-8-sig").splitlines()
        self.assertEqual(lines[0], "번호,이름,1번(5),2번(3),합계(8),채점 안 한 문항,1번 메모,2번 메모")
        self.assertEqual(lines[1], "1,김민준,4.5,,4.5,2,양분 용어 정확히,")

    def test_job_runs_queue_and_records_errors(self):
        sid = self.upload([{"name": "a.jpg", "data": b64(JPEG)}])["created"][0]
        job = grading.Job(self.db, self.config)
        with patch.object(ai, "ask", side_effect=ai.AIError("LM Studio 에 연결할 수 없습니다")):
            job.add("ocr", [sid])
            job.thread.join(5)
        row = grading.sheet_row(self.db, sid)
        self.assertEqual((row["ocr_status"], row["ocr_error"]), ("error", "LM Studio 에 연결할 수 없습니다"))


class GradingServerTests(unittest.TestCase):
    def setUp(self):
        from tests.test_server import ServerTests
        ServerTests.setUpClass()
        self.s = ServerTests
        self.req = lambda *a, **k: ServerTests.req(ServerTests, *a, **k)

    def tearDown(self):
        self.s.tearDownClass()

    def test_api_flow(self):
        st, t = self.req("POST", "/api/grading/tasks", {"title": "수행평가", "kind": "performance", "questions": QUESTIONS})
        self.assertEqual(st, 200, t)
        st, r = self.req("POST", f"/api/grading/tasks/{t['id']}/sheets", {"files": [{"name": "a.jpg", "data": b64(JPEG)}]})
        self.assertEqual(st, 200, r)
        sid = r["created"][0]
        st, data = self.req("GET", f"/api/grading/sheets/{sid}/image/1")
        self.assertEqual((st, data), (200, JPEG))
        self.assertEqual(self.req("GET", f"/api/grading/sheets/{sid}/image/1", token=False)[0], 401)
        st, full = self.req("GET", f"/api/grading/tasks/{t['id']}")
        q1 = full["questions"][0]["id"]
        st, a = self.req("PATCH", f"/api/grading/sheets/{sid}/questions/{q1}", {"text": "답", "score": 3})
        self.assertEqual((st, a["score"]), (200, 3.0))
        st, bad = self.req("PATCH", f"/api/grading/answers/{a['id']}", {"score": 99})
        self.assertEqual(st, 400)
        st, csv_data = self.req("GET", f"/api/grading/tasks/{t['id']}/export")
        self.assertEqual(st, 200)
        self.assertIn("3", csv_data.decode("utf-8-sig").splitlines()[1])
        st, lst = self.req("GET", "/api/grading/tasks")
        self.assertEqual(lst["tasks"][0]["sheets"], 1)
        self.assertEqual(self.req("DELETE", f"/api/grading/tasks/{t['id']}")[0], 200)
        self.assertEqual(self.req("GET", f"/api/grading/tasks/{t['id']}")[0], 400)

    def test_ocr_refuses_remote_ai(self):
        st, t = self.req("POST", "/api/grading/tasks", {"title": "x", "questions": QUESTIONS})
        self.s.env.config.values["ai_base"] = "https://api.example.com/v1"
        st, r = self.req("POST", f"/api/grading/tasks/{t['id']}/ocr", {})
        self.assertEqual(st, 400)
        self.assertIn("127.0.0.1", r["error"])


if __name__ == "__main__":
    unittest.main()
