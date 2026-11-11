"""학교 공통 설정 파일 (0.5.92): 공통값만 담고, 비밀값·학생 자료는 절대 담지 않으며, 깨진·악성 파일은 거부."""
import json
import unittest

from desk import absence, queries, schoolcal, schoolshare
from desk.collect import add_text_item
from tests import test_server  # ServerTests 를 이름으로 가져오면 그 시험이 여기서 또 돔
from tests.test_collect import Env

SECRETS = {
    "telegram_token": "TG:SECRET-TOKEN-1111", "neis_key": "NEIS-SECRET-2222", "capture_key": "CAPTURE-SECRET-3333",
    "ai_openai_key": "sk-OPENAI-SECRET-4444", "ai_anthropic_key": "sk-ant-SECRET-5555", "ai_gemini_key": "GEMINI-SECRET-6666",
    "gcal_ical_url": "https://calendar.google.com/calendar/ical/SECRET-7777/basic.ics",
    "gcal_push_url": "https://script.google.com/macros/s/SECRET-8888/exec", "gcal_push_key": "GCAL-KEY-SECRET-9999",
}
PERSONAL = {"teacher_name": "홍담임", "grade": "2", "class_no": "3", "coolmessenger_name": "쿨메신저이름비밀",
            "coolmessenger_path": "C:\\\\Users\\\\비밀\\\\Memo\\\\a.udb", "coolmessenger_folder": "D:\\\\받은파일비밀"}
STUDENT, PARENT, REASON = "최가짜학생", "010-9999-8888", "가짜조퇴사유병원"
TT = {"days": ["월", "화"], "slots": [{"key": "0-1", "d": 0, "p": 1}, {"key": "1-1", "d": 1, "p": 1}],
      "teachers": [{"id": 0, "name": "김선생", "dept": "과학", "lessons": {"0-1": {"id": 0, "cls": "2-3", "subj": "물리", "locked": False, "why": "", "color": None}}},
                   {"id": 1, "name": "홍담임", "dept": "국어", "lessons": {"1-1": {"id": 1, "cls": "2-1", "subj": "국어", "locked": True, "why": "배경색", "color": "FFFF00"}}}],
      "lock_words": ["창체"], "file": "시간표.xlsx", "sheets": ["Sheet1"], "at": "2026-03-01 08:00:00"}
PERIODS = {"1": {"start": "08:40", "end": "09:30"}, "2": {"start": "09:40", "end": "10:30"}}


def fill(env):
    env.config.values.update(school_name="가짜고등학교", neis_school={"name": "가짜고등학교", "atpt": "B10", "code": "7010001", "office": "서울"},
                             **SECRETS, **PERSONAL)
    env.db.replace_students([{"number": 7, "name": STUDENT, "phone": PARENT}])
    queries.issue_pass(env.db, {"kind": "early", "student_number": 7, "student_name": STUDENT, "date": "2026-11-03",
                                "time_out": "13:00", "reason": REASON})
    add_text_item(env.db, "cool", f"{STUDENT} 쪽지 원문 비밀", title="가짜 쪽지 비밀 제목")
    env.db.set_meta("timetable", TT)
    env.db.set_meta("timetable_periods", PERIODS)
    env.db.set_meta("timetable_bell", {"enabled": True, "lead": 5})
    schoolcal.save(env.db, 2026, [{"date": "2026-03-02", "end_date": None, "time": None, "kind": "event", "category": "학사", "text": "입학식"},
                                  {"date": "2026-07-20", "end_date": "2026-08-16", "time": None, "kind": "event", "category": "학사", "text": "여름방학"}], "파일")


class ShareTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        fill(self.env)

    def tearDown(self):
        self.env.close()

    def export(self, parts=schoolshare.PARTS):
        return json.dumps(schoolshare.build(self.env.db, self.env.config, list(parts)), ensure_ascii=False)

    def test_export_has_no_forbidden_values(self):
        text = self.export()
        # 담임 이름은 학교 전체 시간표(교사별)에는 들어 있으므로 시간표를 뺀 파일에서 따로 확인
        personal = [v for k, v in PERSONAL.items() if k not in ("teacher_name", "grade", "class_no")]
        for v in [*SECRETS.values(), *personal, STUDENT, PARENT, REASON, "쪽지 원문 비밀", "가짜 쪽지 비밀 제목",
                  self.env.config["edufine_folder"]]:
            self.assertNotIn(v.replace("\\\\", "\\"), text, v)
        for key in [*SECRETS, *PERSONAL, "students", "passes", "absences", "items", "telegram", "bell"]:
            self.assertNotIn(f'"{key}"', text, key)
        self.assertNotIn("홍담임", self.export(["school", "periods", "schoolcal"]))
        data = json.loads(text)
        self.assertEqual(data["format"], schoolshare.FORMAT)
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["parts"]["school"]["neis_school"]["code"], "7010001")
        self.assertEqual(data["parts"]["periods"], PERIODS)
        self.assertEqual([e["text"] for e in data["parts"]["schoolcal"][0]["events"]], ["입학식", "여름방학"])
        self.assertEqual(len(data["parts"]["timetable"]["teachers"]), 2)
        self.assertEqual(schoolshare.filename(self.env.config), "가짜고등학교-공통설정.deskshare.json")

    def test_export_only_chosen_parts(self):
        data = json.loads(self.export(["periods"]))
        self.assertEqual(list(data["parts"]), ["periods"])
        with self.assertRaises(schoolshare.ShareError):
            schoolshare.build(self.env.db, self.env.config, [])

    def test_import_into_new_pc_then_name_finds_my_timetable(self):
        text = self.export()
        new = Env()
        try:
            new.config.values.update(edufine_folder="", coolmessenger_folder="")
            pv = schoolshare.preview(new.db, new.config, text)
            self.assertEqual([i["key"] for i in pv["items"]], list(schoolshare.PARTS))
            self.assertFalse(any(i["exists"] for i in pv["items"]))
            res = schoolshare.apply(new.db, new.config, text, list(schoolshare.PARTS), [])
            self.assertEqual(res["applied"], list(schoolshare.PARTS))
            self.assertEqual(new.config["school_name"], "가짜고등학교")
            self.assertEqual(new.config["neis_school"]["code"], "7010001")
            self.assertEqual(new.config["neis_key"], "")                      # 인증키는 오지 않음
            self.assertEqual(new.db.get_meta("timetable_periods"), PERIODS)
            self.assertEqual(new.db.get_meta("timetable_bell"), {"enabled": False, "lead": 5})   # 알림은 꺼진 채로
            self.assertEqual(len(new.db.get_meta("timetable")["teachers"]), 2)
            self.assertIsNone(new.db.get_meta("timetable_me"))
            self.assertEqual(new.db.one("SELECT COUNT(*) AS n FROM students")["n"], 0)
            self.assertEqual(len(schoolshare._school_events(new.db, schoolshare._school_years(new.db)[2026])), 2)
            new.config.update({"teacher_name": "홍담임"})
            schoolshare.find_me(new.db, new.config)
            self.assertEqual(new.db.get_meta("timetable_me"), 1)
            st = schoolshare.status(new.db, new.config)
            self.assertTrue(all(st[k]["has"] for k in schoolshare.PARTS))
        finally:
            new.close()

    def test_existing_values_kept_unless_overwrite(self):
        text = self.export()
        self.env.config.values["school_name"] = "원래고"
        self.env.db.set_meta("timetable_periods", {"1": {"start": "09:00", "end": "09:45"}})
        pv = schoolshare.preview(self.env.db, self.env.config, text)
        self.assertTrue(all(i["exists"] for i in pv["items"]))
        res = schoolshare.apply(self.env.db, self.env.config, text, list(schoolshare.PARTS), [])
        self.assertEqual(sorted(res["skipped"]), sorted(schoolshare.PARTS))
        self.assertEqual(self.env.config["school_name"], "원래고")
        self.assertEqual(self.env.db.get_meta("timetable_periods")["1"]["start"], "09:00")
        res = schoolshare.apply(self.env.db, self.env.config, text, ["periods", "school"], ["periods", "school"])
        self.assertEqual(sorted(res["applied"]), ["periods", "school"])
        self.assertEqual(self.env.config["school_name"], "가짜고등학교")
        self.assertEqual(self.env.db.get_meta("timetable_periods"), PERIODS)

    def test_forbidden_and_unknown_keys_in_file_are_ignored(self):
        data = json.loads(self.export(["school", "periods"]))
        data["parts"]["school"]["neis_key"] = "EVIL-KEY"
        data["parts"]["school"]["teacher_name"] = "가짜이름"
        data["parts"]["students"] = [{"number": 1, "name": "해커학생"}]
        data["parts"]["settings"] = {"telegram_token": "EVIL:TOKEN", "pass_page_url": "https://evil.example"}
        data["telegram_token"] = "EVIL:TOKEN2"
        new = Env()
        try:
            new.config.values.update(edufine_folder="", coolmessenger_folder="")
            schoolshare.apply(new.db, new.config, json.dumps(data), ["school", "periods", "students", "settings"], [])
            self.assertEqual(new.config["neis_key"], "")
            self.assertEqual(new.config["teacher_name"], "")
            self.assertEqual(new.config["telegram_token"], "")
            self.assertEqual(new.config["pass_page_url"], "https://aks-sci-lab.github.io/teacher-desk/")
            self.assertEqual(new.db.one("SELECT COUNT(*) AS n FROM students")["n"], 0)
        finally:
            new.close()

    def test_broken_and_malicious_files_rejected(self):
        good = json.loads(self.export())
        bad = [
            "", "   ", "not json", "[]", "{" * 100000, json.dumps({"format": "other", "version": 1, "parts": {}}),
            json.dumps({**good, "version": 2}), json.dumps({**good, "version": "1"}), json.dumps({**good, "version": True}),
            json.dumps({**good, "parts": {}}), json.dumps({**good, "parts": {"unknown": 1}}),
            json.dumps({**good, "parts": {"periods": {"1": {"start": "25:00"}}}}),
            json.dumps({**good, "parts": {"periods": {"x": {"start": "08:00"}}}}),
            json.dumps({**good, "parts": {"schoolcal": [{"school_year": 2026, "events": [{"date": "2026-02-30", "text": "x"}]}]}}),
            json.dumps({**good, "parts": {"schoolcal": [{"school_year": "2026", "events": []}]}}),
            json.dumps({**good, "parts": {"timetable": {**good["parts"]["timetable"], "days": ["일"]}}}),
            json.dumps({**good, "parts": {"timetable": {**good["parts"]["timetable"], "slots": [{"key": "0-1;DROP", "d": 0, "p": 1}]}}}),
            json.dumps({**good, "parts": {"timetable": {**good["parts"]["timetable"],
                                                        "teachers": [{"name": "x", "lessons": {"9-9": {}}}]}}}),
            json.dumps({**good, "pad": "x" * (schoolshare.MAX_BYTES + 1)}),
        ]
        for text in bad:
            with self.assertRaises(schoolshare.ShareError, msg=text[:80]):
                schoolshare.parse(text)

    def test_text_fields_are_cleaned(self):
        data = json.loads(self.export(["schoolcal", "timetable"]))
        data["parts"]["schoolcal"][0]["events"][0]["text"] = "입학식\n<script>alert(1)</script>" + "가" * 500
        data["parts"]["schoolcal"][0]["events"][0]["category"] = "해킹"
        data["parts"]["timetable"]["teachers"][0]["name"] = "김\x00선생"
        share = schoolshare.parse(json.dumps(data))
        ev = share["parts"]["schoolcal"][0]["events"][0]
        self.assertNotIn("\n", ev["text"])
        self.assertLessEqual(len(ev["text"]), 200)
        self.assertEqual(ev["category"], "학사")
        self.assertEqual(share["parts"]["timetable"]["teachers"][0]["name"], "김 선생")


class ShareServerTests(test_server.ServerTests):
    def test_share_api_flow(self):
        fill(self.env)
        self.env.config.values["port"] = self.port
        st, s = self.req("GET", "/api/share")
        self.assertEqual(st, 200)
        self.assertTrue(s["timetable"]["has"])
        st, r = self.req("POST", "/api/share/export", {"parts": ["school", "periods", "schoolcal", "timetable"]})
        self.assertEqual(st, 200)
        text = json.dumps(r["data"], ensure_ascii=False)
        for v in SECRETS.values():
            self.assertNotIn(v, text)
        self.assertNotIn(STUDENT, text)
        self.assertEqual(self.req("POST", "/api/share/export", {"parts": ["students"]})[0], 400)
        self.assertEqual(self.req("POST", "/api/share/preview", {"text": "깨진 파일"})[0], 400)
        st, pv = self.req("POST", "/api/share/preview", {"text": text})
        self.assertEqual((st, len(pv["items"])), (200, 4))
        st, res = self.req("POST", "/api/share/apply", {"text": text, "parts": ["periods"], "overwrite": ["periods"]})
        self.assertEqual((st, res["applied"]), (200, ["periods"]))
        self.assertNotIn(SECRETS["telegram_token"], json.dumps(res["settings"]))


for name in [n for n in dir(test_server.ServerTests) if n.startswith("test_")]:
    if getattr(ShareServerTests, name) is getattr(test_server.ServerTests, name):
        setattr(ShareServerTests, name, None)


if __name__ == "__main__":
    unittest.main()
