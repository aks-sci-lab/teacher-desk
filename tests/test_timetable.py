import unittest
from datetime import date

from desk import timetable as T
from tests.xlsx_fixture import build_xlsx

DAYS = ["월", "화", "수", "목", "금"]


def sample_xlsx(extra_fill=None):
    """교사마다 2줄(학급·과목), 이름은 세로 병합. 요일 머리는 첫 칸에만 (나머지는 병합 흉내로 빈칸)."""
    head = ["이름", "교과"]
    per = ["", ""]
    for d in DAYS:
        head += [d, "", ""]
        per += [1, 2, 3]
    col = lambda d, p: 2 + DAYS.index(d) * 3 + (p - 1)
    rows = [head, per]
    merges = []
    fills = {}
    teachers = [
        ("김국어", "국어", {("월", 1): ("2-1", "국어"), ("월", 2): ("2-2", "국어"), ("화", 1): ("2-1", "국어"),
                         ("금", 3): ("2-1", "자율")}),
        ("박영어", "영어", {("월", 3): ("2-1", "영어"), ("화", 2): ("2-1", "영어"), ("수", 1): ("2-2", "영어")}),
        ("최수학", "수학", {("월", 1): ("2-3", "수학"), ("수", 2): ("2-1", "수학"), ("목", 1): ("2-3", "수학")}),
        ("정과학", "과학", {("수", 3): ("2-3", "과학"), ("월", 2): ("2-3", "과학"), ("월", 3): ("2-3", "과학"),
                         ("월", 4 - 3): None}),
    ]
    for i, (name, dept, lessons) in enumerate(teachers):
        r1 = [name, dept] + [""] * 15
        r2 = ["", ""] + [""] * 15
        for k, v in lessons.items():
            if v:
                r1[col(*k)], r2[col(*k)] = v
        rows += [r1, r2]
        top = 3 + i * 2
        merges += [f"A{top}:A{top + 1}", f"B{top}:B{top + 1}"]
    letter = lambda c: chr(65 + c)
    fills[f"{letter(col('화', 2))}5"] = "FFF2A0B6"          # 박영어 화2: 배경색 → 잠금
    if extra_fill:
        fills.update(extra_fill)
    return build_xlsx([("2학기 시간표", rows, merges, fills)])


class ParseTests(unittest.TestCase):
    def test_reads_teachers_lessons_and_locks(self):
        tt, sheets = T.parse_file(sample_xlsx())
        self.assertEqual(sheets, ["2학기 시간표"])
        self.assertEqual(tt["days"], DAYS)
        self.assertEqual(len(tt["slots"]), 15)
        names = [t["name"] for t in tt["teachers"]]
        self.assertEqual(names, ["김국어", "박영어", "최수학", "정과학"])
        kim = tt["teachers"][0]
        self.assertEqual(kim["dept"], "국어")
        self.assertEqual(kim["lessons"]["0-1"]["cls"], "2-1")
        self.assertEqual(kim["lessons"]["0-1"]["subj"], "국어")
        self.assertEqual((kim["lessons"]["4-3"]["locked"], kim["lessons"]["4-3"]["why"]), (True, "고정 과목"))
        park = tt["teachers"][1]["lessons"]
        self.assertEqual((park["1-2"]["locked"], park["1-2"]["why"], park["1-2"]["color"]), (True, "배경색", "#F2A0B6"))
        self.assertFalse(park["0-3"]["locked"])
        self.assertEqual(T.find_teacher(tt, "김 국어"), 0)

    def test_bad_sheet(self):
        with self.assertRaises(T.TimetableError):
            T.parse_file(build_xlsx([("빈 시트", [["아무것도"], ["없음"]], [])]))


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.tt, _ = T.parse_file(sample_xlsx())
        self.mon = date(2026, 11, 2)   # 월요일

    def test_swap_and_two_step(self):
        cands = T.find_candidates(self.tt, 0, "0-1", self.mon, today=date(2026, 10, 28))
        kinds = {(c["kind"], c["moves"][0]["to"]) for c in cands}
        # 맞교환: 박영어 월3 2-1 (박영어는 월1 이 비어 있음)
        self.assertIn(("swap", "0-3"), kinds)
        # 박영어 화2 는 배경색으로 잠겨 후보가 아님
        self.assertNotIn(("swap", "1-2"), kinds)
        first = cands[0]
        self.assertEqual(first["kind"], "swap")
        self.assertEqual([(m["name"], m["from_label"], m["to_label"]) for m in first["moves"]],
                         [("김국어", "11/2(월) 1교시", "11/2(월) 3교시"), ("박영어", "11/2(월) 3교시", "11/2(월) 1교시")])
        # 2단계: 최수학 수2 2-1 ↔ 내 월1, 최수학의 월1(2-3)은 2-3 수업이 있는 정과학과 한 번 더
        two = [c for c in cands if c["kind"] == "two"]
        self.assertTrue(two)
        self.assertEqual({m["name"] for m in two[0]["moves"]}, {"김국어", "최수학", "정과학"})
        text = T.request_text(first, "김국어")
        self.assertIn("박영어 선생님", text)
        self.assertIn("11/2(월) 1교시 2-1 국어 수업을 11/2(월) 3교시", text)

    def test_runs_blocks_and_locked(self):
        with self.assertRaises(T.TimetableError):
            T.find_candidates(self.tt, 0, "4-3", self.mon)            # 잠긴 수업 (자율)
        with self.assertRaises(T.TimetableError):
            T.find_candidates(self.tt, 0, "2-3", self.mon)            # 수업 없는 칸
        # 학사일정: 그날 행사가 있으면 막음 (학년이 맞을 때만), 지난 날도
        ev = {"2026-11-02": ["2학년 현장체험학습"]}
        cands = T.find_candidates(self.tt, 0, "0-1", self.mon, day_events=ev, today=date(2026, 10, 28))
        self.assertTrue(all(c["block"] for c in cands))
        ev = {"2026-11-02": ["1학년 현장체험학습"]}
        self.assertFalse(T.find_candidates(self.tt, 0, "0-1", self.mon, day_events=ev, today=date(2026, 10, 28))[0]["block"])
        self.assertEqual(T.find_candidates(self.tt, 0, "0-1", self.mon, today=date(2026, 11, 3))[0]["block"], "지난 날짜")
        # 연속 수업: 정과학 월2·3 이 있어 월1 이 들어가면 3교시 연속 (기준 4 미만이면 경고 없음)
        two = [c for c in T.find_candidates(self.tt, 0, "0-1", self.mon, today=date(2026, 10, 28))
               if c["kind"] == "two" and c["moves"][3]["from"] == "2-3"]    # 정과학 수3 → 월1
        board = next(b for b in two[0]["boards"] if b["name"] == "정과학" and b["day"] == "월")
        self.assertEqual((board["run_before"], board["run_after"]), (2, 3))
        self.assertFalse(two[0]["worse"])
        self.assertEqual([c["in"] for c in board["cells"]][:3], [True, False, False])

    def test_swaps_change_week_and_today(self):
        cand = T.find_candidates(self.tt, 0, "0-1", self.mon, today=date(2026, 10, 28))[0]
        swaps = [{"moves": cand["moves"]}]
        today_lessons = T.lessons_on(self.tt, 0, self.mon, swaps)
        self.assertEqual([(l["p"], l["cls"], l["moved"]) for l in today_lessons], [(2, "2-2", False), (3, "2-1", True)])
        self.assertEqual([l["p"] for l in T.lessons_on(self.tt, 1, self.mon, swaps)], [1])
        # 다음 주에는 교체가 없음
        self.assertEqual([l["p"] for l in T.lessons_on(self.tt, 0, date(2026, 11, 9), swaps)], [1, 2])
        self.assertEqual(T.lessons_on(self.tt, 0, date(2026, 11, 7), swaps), [])   # 토요일


if __name__ == "__main__":
    unittest.main()


from tests import test_server  # noqa: E402  (ServerTests 를 이름으로 가져오면 그 시험이 여기서 또 돔)


class TimetableApiTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def test_import_week_candidates_and_swap(self):
        import base64
        from datetime import timedelta
        self.assertEqual(self.req("GET", "/api/timetable")[1], {"loaded": False})
        self.assertEqual(self.req("GET", "/api/timetable/week?teacher=0")[0], 404)
        self.env.config.values["teacher_name"] = "김국어"
        status, tt = self.req("POST", "/api/timetable", {"filename": "시간표.xlsx",
                                                          "data": base64.b64encode(sample_xlsx()).decode()})
        self.assertEqual(status, 200, tt)
        self.assertEqual((tt["me"], len(tt["teachers"]), tt["locked"]), (0, 4, 2))   # 설정의 담임 이름으로 나를 찾음
        self.assertEqual(self.req("POST", "/api/timetable", {"filename": "x.xlsx", "data": "!!"})[0], 400)
        # 다음 주 월요일
        today = date.today()
        mon = today - timedelta(days=today.weekday()) + timedelta(days=7)
        # 학사일정: 그 주 수요일 2학년 체험학습
        self.env.db.execute("INSERT INTO events(item_id, date, kind, category, text) VALUES(NULL, ?, 'event', '학사', '2학년 현장체험학습')",
                            ((mon + timedelta(days=2)).isoformat(),))
        status, wk = self.req("GET", f"/api/timetable/week?teacher=0&date={mon.isoformat()}")
        self.assertEqual(status, 200, wk)
        self.assertEqual(wk["grid"]["0-1"]["cls"], "2-1")
        self.assertEqual(wk["days"][2]["events"], ["2학년 현장체험학습"])
        self.assertEqual(self.req("GET", f"/api/timetable/candidates?teacher=0&key=0-1&date={(mon + timedelta(days=1)).isoformat()}")[0], 400)  # 요일이 다름
        status, res = self.req("GET", f"/api/timetable/candidates?teacher=0&key=0-1&date={mon.isoformat()}")
        self.assertEqual(status, 200, res)
        cands = res["candidates"]
        self.assertTrue(any(c["block"] == "2학년 현장체험학습" for c in cands))   # 수요일로 옮기는 2단계는 막힘
        first = cands[0]
        self.assertIn("박영어 선생님", first["request"])
        status, sw = self.req("POST", "/api/timetable/swaps", {"moves": first["moves"]})
        self.assertEqual(status, 200, sw)
        self.assertEqual(len(sw["event_ids"]), 1)                       # 내 수업이 옮겨진 날만 달력에
        ev = self.env.db.one("SELECT * FROM events WHERE id = ?", (sw["event_ids"][0],))
        self.assertIn("시간표 교체: 3교시 2-1 국어", ev["text"])
        self.assertEqual(self.req("POST", "/api/timetable/swaps", {"moves": first["moves"]})[0], 400)   # 같은 교체 두 번
        wk = self.req("GET", f"/api/timetable/week?teacher=0&date={mon.isoformat()}")[1]
        self.assertNotIn("0-1", wk["grid"])
        self.assertTrue(wk["grid"]["0-3"]["moved"])
        self.assertEqual(self.req("DELETE", f"/api/timetable/swaps/{sw['id']}")[0], 200)
        self.assertIsNone(self.env.db.one("SELECT * FROM events WHERE id = ?", (sw["event_ids"][0],)))
        # 오늘 내 수업 (브리핑)
        lessons = self.req("GET", "/api/today")[1]["lessons"]
        self.assertEqual(lessons, T.lessons_on(T.parse_file(sample_xlsx())[0], 0, today))
        self.assertEqual(self.req("PUT", "/api/timetable/me", {"teacher": 9})[0], 404)
        self.assertEqual(self.req("PUT", "/api/timetable/me", {"teacher": 1})[0], 200)
        self.assertEqual(self.req("DELETE", "/api/timetable")[0], 200)
        self.assertIsNone(self.req("GET", "/api/today")[1]["lessons"])


class TelegramLessonTests(unittest.TestCase):
    def test_lessons_reply_and_question_context(self):
        from desk import assist
        from desk.telegram import Bot
        from tests.test_collect import Env
        env = Env()
        try:
            tt, _ = T.parse_file(sample_xlsx())
            bot = Bot(env.db, env.config)
            self.assertIn("불러온 시간표가 없습니다", bot.lessons_text("내일 수업"))
            env.db.set_meta("timetable", tt)
            env.db.set_meta("timetable_me", 0)
            mon = date(2026, 11, 2)
            text = bot.lessons_text("월요일 수업", today=date(2026, 10, 28))
            self.assertIn("11/2(월) 내 수업 2시간", text)
            self.assertIn("1교시 2-1 국어", text)
            self.assertIn("📚 내 수업", bot.answer("today", mon))
            self.assertEqual(assist.intent("내일 수업 뭐야"), ("lessons", ["내일 수업 뭐야"]))
            found = assist.gather(env.db, "다음 주 월요일 빈 시간 있어?", date(2026, 10, 28))
            self.assertEqual(list(found["lessons"]), ["2026-11-02"])
            prompt, _ = assist.build_prompt("다음 주 월요일 빈 시간 있어?", found, [], date(2026, 10, 28))
            self.assertIn("내 수업 (시간표):\n- 11/2(월) 1교시 2-1 국어, 2교시 2-2 국어", prompt)
        finally:
            env.close()
