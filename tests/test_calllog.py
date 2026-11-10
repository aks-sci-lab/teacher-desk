"""통화·문자 내역 → 상담 초안 (0.5.83): XML·CSV 읽기, 학부모 번호 맞추기, 맞지 않는 번호 버림, 중복 저장 막기, AI 요약은 이 PC 만."""
import base64
import unittest
from datetime import datetime

from desk import calllog
from tests.test_classroom_api import ApiBase

MS = lambda y, mo, d, h, mi: str(int(datetime(y, mo, d, h, mi).timestamp() * 1000))

CALLS_XML = f"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<calls count="4">
  <call number="+82 10-1111-0003" duration="125" date="{MS(2026, 11, 3, 15, 30)}" type="1" contact_name="가짜3 어머니" />
  <call number="010-1111-0003" duration="0" date="{MS(2026, 11, 4, 9, 5)}" type="3" />
  <call number="010-9999-8888" duration="300" date="{MS(2026, 11, 4, 10, 0)}" type="2" contact_name="모르는 사람" />
  <call number="01011110005" duration="40" date="{MS(2026, 11, 5, 18, 0)}" type="2" />
</calls>""".encode("utf-8")

SMS_XML = f"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<smses count="3">
  <sms address="010-1111-0003" date="{MS(2026, 11, 6, 8, 1)}" type="1" body="오늘 가짜3 병원 들렀다 갑니다" />
  <sms address="010-1111-0003" date="{MS(2026, 11, 6, 8, 3)}" type="2" body="네 알겠습니다" />
  <sms address="010-7777-6666" date="{MS(2026, 11, 6, 9, 0)}" type="1" body="광고 비밀문자" />
</smses>""".encode("utf-8")

CSV_KO = "이름,전화번호,통화일시,통화시간,유형\n가짜5 아버님,010-1111-0005,2026-11-07 오후 4:10,2분 5초,발신\n모름,02-123-4567,2026-11-07 16:20,10,수신\n".encode("cp949")


def b64(raw):
    return base64.b64encode(raw).decode("ascii")


class ParseTests(unittest.TestCase):
    def test_helpers(self):
        self.assertEqual(calllog.digits("+82 10-1234-5678"), "01012345678")
        self.assertEqual(calllog.digits("12"), "")
        self.assertEqual(calllog.parse_when("2026.11.07 오후 4:10"), datetime(2026, 11, 7, 16, 10))
        self.assertEqual(calllog.parse_when("11/9/2026 14:03"), datetime(2026, 11, 9, 14, 3))
        self.assertIsNone(calllog.parse_when("어제"))
        for v, s in (("65", 65), ("01:05", 65), ("1:00:05", 3605), ("1분 5초", 65), ("", None)):
            self.assertEqual(calllog.parse_duration(v), s, v)
        self.assertEqual(calllog.direction("부재중"), "missed")
        self.assertEqual(calllog.direction("2"), "out")
        self.assertEqual(calllog.duration_text(125), "2분 5초")

    def test_doctype_rejected(self):
        evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><calls><call number="1" date="1"/></calls>'
        with self.assertRaises(ValueError):
            calllog.read_file("evil.xml", evil)

    def test_utf16_and_entity_xml_refused(self):   # 검토 2026-11-10: UTF-16 이면 바이트 검사를 빠져나가던 것
        bomb = '<?xml version="1.0"?><!DOCTYPE c [<!ENTITY a "' + "x" * 100 + '">]><calls><call number="010-1111-0003" date="1" body="&a;&a;"/></calls>'
        for raw in (bomb.encode("utf-16-le"), bomb.encode("utf-16"), bomb.encode("utf-32"),
                    '<calls><call number="1"/></calls>'.encode("utf-16-le")):
            with self.assertRaises(ValueError):
                calllog.read_file("x.xml", raw)
        # 바이트 검사가 없어도 expat 단계에서 DOCTYPE·ENTITY 를 거부
        old = calllog.P_DOCTYPE
        calllog.P_DOCTYPE = __import__("re").compile(rb"(?!)")
        try:
            with self.assertRaises(ValueError) as e:
                calllog.safe_xml_check(bomb.encode("utf-8"))
            self.assertIn("DOCTYPE", str(e.exception))
        finally:
            calllog.P_DOCTYPE = old
        self.assertTrue(calllog.read_file("ok.xml", "\ufeff<calls><call number=\"010\" date=\"1\"/></calls>".encode("utf-8")) is not None)

    def test_csv_needs_headers(self):
        with self.assertRaises(ValueError):
            calllog.read_file("x.csv", "a,b\n1,2\n".encode("utf-8"))


class ApiTests(ApiBase):
    def setUp(self):
        super().setUp()
        rows = [{"number": n, "name": f"가짜{n}", "parent_phone": f"010-1111-{n:04d}"} for n in range(1, 13)]
        self.env.db.replace_students(rows)

    def parse(self, *files, since=None):
        return self.req("POST", "/api/class/calllog/parse",
                        {"files": [{"name": n, "data": b64(r)} for n, r in files], "since": since})

    def test_match_discard_and_group(self):
        st, r = self.parse(("calls.xml", CALLS_XML), ("sms.xml", SMS_XML), ("log.csv", CSV_KO))
        self.assertEqual(st, 200, r)
        s = r["stats"]
        self.assertEqual((s["calls"], s["sms"], s["discarded"]), (6, 3, 3))   # 명단에 없는 번호 3개는 버림
        text = str(r)
        for secret in ("010-9999-8888", "01099998888", "모르는 사람", "광고 비밀문자", "02-123-4567", "021234567"):
            self.assertNotIn(secret, text)                                     # 버린 번호·내용은 화면에도 안 감
        by = {(d["number"], d["method"], d["date"]): d for d in r["drafts"]}
        call = by[(3, "전화", "2026-11-03")]
        self.assertEqual((call["time"], call["duration"], call["pick"], call["with_whom"]), ("15:30", 125, True, "학부모"))
        self.assertIn("2분 5초", call["content"])
        self.assertFalse(by[(3, "전화", "2026-11-04")]["pick"])                # 부재중은 기본으로 안 고름
        sms = by[(3, "문자", "2026-11-06")]
        self.assertIn("[08:01 받음] 오늘 가짜3 병원", sms["content"])
        self.assertIn("[08:03 보냄] 네 알겠습니다", sms["content"])
        self.assertEqual(by[(5, "전화", "2026-11-07")]["duration"], 125)        # CSV(CP949, 오후 4:10, 2분 5초)
        # 아무것도 저장되지 않음
        self.assertEqual(self.env.db.one("SELECT COUNT(*) AS n FROM class_counsel")["n"], 0)

    def test_utf16_bomb_refused_by_api(self):
        bomb = '<?xml version="1.0"?><!DOCTYPE c [<!ENTITY a "' + "x" * 1000 + '">]><smses><sms address="010-1111-0003" date="1" type="1" body="&a;"/></smses>'
        st, r = self.parse(("sms.xml", bomb.encode("utf-16-le")))
        self.assertEqual(st, 400)
        self.assertNotIn("xxxxxxxx", str(r))

    def test_total_size_limit(self):
        old = calllog.MAX_TOTAL
        calllog.MAX_TOTAL = len(CALLS_XML) + 10
        try:
            st, r = self.parse(("a.xml", CALLS_XML), ("b.xml", CALLS_XML))
        finally:
            calllog.MAX_TOTAL = old
        self.assertEqual(st, 400)
        self.assertIn("14MB", r["error"])

    def test_since_filter(self):
        st, r = self.parse(("calls.xml", CALLS_XML), since="2026-11-05")
        self.assertEqual({d["date"] for d in r["drafts"]}, {"2026-11-05"})
        self.assertEqual(r["stats"]["old"], 2)

    def test_save_once_and_privacy(self):
        st, r = self.parse(("calls.xml", CALLS_XML), ("sms.xml", SMS_XML))
        picked = [d for d in r["drafts"] if d["pick"]]
        st, s = self.req("POST", "/api/class/calllog/save", {"items": picked})
        self.assertEqual((st, s["saved"]), (200, len(picked)))
        rows = self.env.db.query("SELECT * FROM class_counsel")
        self.assertEqual({x["source"] for x in rows}, {"import"})
        st, s = self.req("POST", "/api/class/calllog/save", {"items": picked})   # 두 번 눌러도 한 번만
        self.assertEqual(s["saved"], 0)
        st, r2 = self.parse(("calls.xml", CALLS_XML))
        self.assertTrue(all(d.get("already") for d in r2["drafts"] if d["method"] == "전화" and d["duration"]))
        logs = str(self.env.db.query("SELECT message FROM logs"))
        self.assertNotIn("가짜3", logs)
        self.assertNotIn("010-1111", logs)
        self.assertNotIn("병원", logs)
        bad = dict(picked[0], number=99)
        self.assertEqual(self.req("POST", "/api/class/calllog/save", {"items": [dict(bad, import_key=None)]})[0], 400)

    def test_needs_parent_phone_and_ai_off(self):
        self.env.db.replace_students([{"number": 1, "name": "다른1"}])   # 같은 번호·이름이면 예전 연락처를 이어 받음
        st, r = self.parse(("calls.xml", CALLS_XML))
        self.assertEqual(st, 400)
        self.assertIn("학부모 연락처", r["error"])
        self.env.config.values["ai_enabled"] = False
        st, r = self.req("POST", "/api/class/calllog/summarize", {"text": "가짜1 오늘 결석"})
        self.assertEqual(st, 400)

    def test_summary_masks_names_and_phones(self):
        sent = {}

        def fake_ask(base, model, prompt, **kw):
            sent["prompt"] = prompt
            return "가짜3 학부모가 010-1111-0003 으로 연락함"
        self.env.config.values.update({"ai_enabled": True, "ai_base": "http://127.0.0.1:1234/v1"})
        old_ask, old_pick = calllog.ai.ask, calllog.ai.pick_model
        calllog.ai.ask, calllog.ai.pick_model = fake_ask, lambda c: "m"
        try:
            st, r = self.req("POST", "/api/class/calllog/summarize", {"text": "[08:01 받음] 가짜3 병원 010-1111-0003"})
        finally:
            calllog.ai.ask, calllog.ai.pick_model = old_ask, old_pick
        self.assertEqual(st, 200, r)
        self.assertNotIn("가짜3", sent["prompt"])
        self.assertNotIn("010-1111-0003", sent["prompt"])
        self.assertNotIn("가짜3", r["summary"])
        self.assertNotIn("010-1111-0003", r["summary"])
        self.env.config.values["ai_base"] = "http://example.com/v1"
        self.assertEqual(self.req("POST", "/api/class/calllog/summarize", {"text": "x"})[0], 400)   # 이 PC 가 아닌 AI 는 거부
