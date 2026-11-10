"""가짜 학급 30명 + 실제 앱 모양 그대로의 가짜 백업 파일로 끝까지 (2026-11-10 선생님 요청 '가상 학부모·가상 번호로 시험').
파일: tests/fixtures/calllog (번호는 실제로 안 쓰는 010-0000-xxxx, 이름은 '○가짜n'). 다시 만들기: make_fixtures.py.
여기서 찾아 고친 것: SMS Backup & Restore 의 이모지(서로게이트 숫자 참조) 때문에 sms.xml 전체 거부, 삼성식 '2026. 11. 4.'·따로 있는
'시간' 칸 날짜 못 읽음, 그룹 문자(a~b) 첫 사람만, 같은 통화가 여러 파일에 있으면 초안 중복."""
import base64
import json
import os
import time
import unittest
from pathlib import Path

from desk import calllog
from tests.test_classroom_api import ApiBase

FIX = Path(__file__).resolve().parent / "fixtures" / "calllog"
FILES = ["calls.xml", "sms.xml", "samsung_ko_utf8.csv", "generic_en.csv", "ko_cp949.csv"]
NOT_PARENTS = ["010-0000-9001", "01000009001", "010-0000-9002", "010-0000-9003", "650-555-0100", "1588-0000",
               "[광고] 비밀특가 할인", "스팸문자 대출", "Hello from abroad", "(Unknown)", "모르는 번호"]


def b64(name):
    return base64.b64encode((FIX / name).read_bytes()).decode()


class FixtureFileTests(unittest.TestCase):
    def setUp(self):   # 날짜(밀리초)를 만든 곳과 같은 시간대로 읽음
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Seoul"
        if hasattr(time, "tzset"):
            time.tzset()

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        if hasattr(time, "tzset"):
            time.tzset()

    def test_real_app_sms_with_emoji_is_read(self):
        e = calllog.read_file("sms.xml", (FIX / "sms.xml").read_bytes())
        bodies = [x["body"] for x in e]
        self.assertIn("감사합니다 😊", bodies)
        self.assertIn("사진 보내드려요 📷", bodies)
        self.assertTrue(any("안녕하세요\n오늘" in b for b in bodies))       # &#10; 줄바꿈
        group = [x["phone"] for x in e if "현장체험학습" in x["body"]]
        self.assertEqual(len(group), 2)                                    # 그룹 문자는 받는 사람마다

    def test_surrogate_refs(self):
        self.assertEqual(calllog.fix_surrogate_refs(b"&#55357;&#56842;"), b"&#128522;")
        self.assertEqual(calllog.fix_surrogate_refs(b"a&#55357;b&#56842;"), b"a&#65533;b&#65533;")
        self.assertEqual(calllog.fix_surrogate_refs(b"&#10;&#xD83D;&#xDE0A;"), b"&#10;&#128522;")
        bomb = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY b "x">]><calls>&#55357;&#56842;</calls>'
        with self.assertRaises(ValueError):                                # 고친 뒤에도 DOCTYPE 은 거부
            calllog.read_file("x.xml", bomb)

    def test_samsung_style_csv_dates(self):
        e = calllog.read_file("s.csv", (FIX / "samsung_ko_utf8.csv").read_bytes())
        self.assertEqual([x["when"].strftime("%m-%d %H:%M") for x in e[:3]], ["11-04 09:00", "11-04 10:00", "11-04 11:00"])
        self.assertEqual([x["dir"] for x in e[:3]], ["in", "out", "missed"])
        self.assertEqual(calllog.parse_when("2026. 11. 4. 오후 3:10").strftime("%H:%M"), "15:10")
        self.assertEqual(calllog.parse_when("2026년 11월 4일 오후 3시 5분").strftime("%H:%M"), "15:05")


class ButtonTests(unittest.TestCase):
    def test_pc_import_button_does_not_pass_click_event(self):
        # 0.5.84 에서 [📱 통화·문자 불러오기] 가 클릭 이벤트를 '받은 초안'으로 넘겨 창이 안 열리던 것
        js = (Path(__file__).resolve().parent.parent / "web" / "js" / "views" / "classrecords.js").read_text(encoding="utf-8")
        self.assertNotIn("onclick: openCallImport,", js)
        self.assertIn("onclick: () => openCallImport()", js)


class FixtureApiTests(ApiBase):
    def setUp(self):
        super().setUp()
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Seoul"
        if hasattr(time, "tzset"):
            time.tzset()
        rows = json.loads((FIX / "roster.json").read_text(encoding="utf-8"))
        st, saved = self.req("PUT", "/api/students", {"rows": rows})
        self.assertEqual(st, 200, saved)

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        if hasattr(time, "tzset"):
            time.tzset()
        super().tearDown()

    def test_end_to_end(self):
        st, r = self.req("POST", "/api/class/calllog/parse",
                         {"files": [{"name": f, "data": b64(f)} for f in FILES], "since": "2026-09-11"})
        self.assertEqual(st, 200, r)
        drafts = r["drafts"]
        text = json.dumps(r, ensure_ascii=False)
        for s in NOT_PARENTS:
            self.assertNotIn(s, text)                                      # 버린 것은 화면 응답에 없음
        keys = [d["import_key"] for d in drafts]
        self.assertEqual(len(keys), len(set(keys)))                        # 같은 통화가 여러 파일에 있어도 한 초안
        nov4 = sorted((d["number"], d["time"]) for d in drafts if d["date"] == "2026-11-04" and d["method"] == "전화")
        self.assertEqual(nov4, [(17, "09:00"), (18, "10:00"), (19, "11:00")])
        sib = {d["number"] for d in drafts if d["method"] == "전화" and d["import_key"]} & {5, 6}
        self.assertEqual(sib, {5, 6})                                      # 같은 번호를 쓰는 형제
        s1 = next(d for d in drafts if d["number"] == 1 and d["method"] == "문자")
        self.assertEqual(s1["topic"], "학부모 문자 (3건)")
        self.assertIn("[10:00 받음] 감사합니다 😊", s1["content"])
        self.assertEqual(sorted(d["number"] for d in drafts if "현장체험학습" in d["content"]), [11, 12])
        self.assertEqual(r["stats"]["old"], 1)
        st, s = self.req("POST", "/api/class/calllog/save", {"items": [d for d in drafts if d["pick"]]})
        self.assertEqual(s["skipped"], 0)
        dump = "\n".join(self.env.db.conn.iterdump())
        for x in NOT_PARENTS:
            self.assertNotIn(x, dump)                                      # DB 어디에도 없음


if __name__ == "__main__":
    unittest.main()
