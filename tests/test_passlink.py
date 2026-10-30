import json
import shutil
import subprocess
import unittest
from pathlib import Path

from desk import passlink

ROOT = Path(__file__).resolve().parent.parent
# 저장소에서는 teacher-desk/pass, 윈도우용 ZIP 에서는 teacher-desk 옆의 pass
CODEC = next((c for c in (ROOT / "pass" / "pass-codec.js", ROOT.parent / "pass" / "pass-codec.js") if c.is_file()), None)
PASS = {"kind": "out", "serial": "2026-0007", "student_name": "김민준", "student_number": 7, "date": "2026-10-30",
        "time_out": "13:30", "time_return": "14:30", "reason": "병원 진료", "share_reason": 0}
SCHOOL = {"school_name": "천상고등학교", "grade": "2", "class_no": "3", "teacher_name": "홍길동"}
BASE = "https://aks-sci-lab.github.io/teacher-desk/"


class PassLinkTests(unittest.TestCase):
    def test_reason_only_when_shared(self):
        self.assertNotIn("why", passlink.pack(PASS, SCHOOL))
        self.assertEqual(passlink.pack({**PASS, "share_reason": 1}, SCHOOL)["why"], "병원 진료")
        self.assertEqual(passlink.pack({**PASS, "kind": "early"}, SCHOOL).get("r"), None)   # 조퇴는 복귀 시각 없음

    def test_empty_base_gives_none(self):
        self.assertIsNone(passlink.share_url("", PASS, SCHOOL))
        self.assertTrue(passlink.share_url(BASE + "#old", PASS, SCHOOL).startswith(BASE + "#ey"))

    @unittest.skipUnless(shutil.which("node") and CODEC, "node 나 pass-codec.js 가 없으면 건너뜀")
    def test_js_page_can_read_python_link(self):
        """학생 폰 페이지(pass/pass-codec.js)가 이 링크를 그대로 읽는지."""
        url = passlink.share_url(BASE, PASS, SCHOOL)
        js = "console.log(JSON.stringify(require(process.argv[2]).decode(process.argv[1].split('#')[1])))"
        out = subprocess.run(["node", "-e", js, url, str(CODEC)], capture_output=True, text=True, check=True).stdout
        got = json.loads(out)
        self.assertEqual((got["name"], got["number"], got["kind"], got["timeOut"], got["timeReturn"], got["reason"]),
                         ("김민준", 7, "out", "13:30", "14:30", ""))
        self.assertEqual((got["school"], got["className"], got["teacher"]), ("천상고등학교", "2학년 3반", "홍길동"))
