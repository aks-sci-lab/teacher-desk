"""선생님 구글 캘린더 공유 (0.5.91): 가짜 구글 서버로 가져오기·올리기·고치기·지우기·다시 보내기."""

import unittest
from datetime import date, datetime, timedelta

from desk import gcal
from desk.server import App
from desk.telegram import Bot
from tests.fake_google import FakeGoogle
from tests.test_collect import Env

TODAY = date.today()


def d(n):
    return (TODAY + timedelta(days=n)).isoformat()


def compact(iso):
    return iso.replace("-", "")


class UrlCheckTests(unittest.TestCase):
    def test_ical_url_only_google_private(self):
        ok = "https://calendar.google.com/calendar/ical/me%40gmail.com/private-abc123/basic.ics"
        self.assertEqual(gcal.check_ical_url(" " + ok + " "), ok)
        for bad in ("http://calendar.google.com/calendar/ical/x/private-a/basic.ics",
                    "https://evil.example/calendar/ical/x/basic.ics",
                    "https://calendar.google.com/calendar/ical/me%40gmail.com/public/basic.ics",
                    "https://calendar.google.com/calendar/embed?src=x"):
            with self.assertRaises(ValueError):
                gcal.check_ical_url(bad)

    def test_push_url_only_apps_script(self):
        ok = "https://script.google.com/macros/s/AKfycbx" + "a" * 40 + "/exec"
        self.assertEqual(gcal.check_push_url(ok), ok)
        gcal.check_push_url("https://script.google.com/a/macros/school.kr/s/AKfycbx" + "b" * 30 + "/exec")
        for bad in ("https://script.google.com/macros/s/short/exec", "https://evil.example/macros/s/" + "a" * 30 + "/exec",
                    "https://script.google.com/macros/s/" + "a" * 30 + "/dev"):
            with self.assertRaises(ValueError):
                gcal.check_push_url(bad)

    def test_config_keeps_urls_secret(self):
        env = Env()
        try:
            ok = "https://calendar.google.com/calendar/ical/me%40gmail.com/private-abc123/basic.ics"
            env.config.update({"gcal_ical_url": ok, "gcal_push_types": ["school", "manual"]})
            pub = env.config.public()
            self.assertNotIn("gcal_ical_url", pub)
            self.assertTrue(pub["gcal_ical_url_set"])
            self.assertNotIn("gcal_push_key", pub)
            self.assertEqual(env.config["gcal_push_types"], ["manual", "school"])
            with self.assertRaises(ValueError):
                env.config.update({"gcal_ical_url": "https://evil.example/a.ics"})
            with self.assertRaises(ValueError):
                env.config.update({"gcal_push_types": ["absence"]})
            self.assertFalse(env.config["gcal_import"])   # 기본 끔
            self.assertFalse(env.config["gcal_push"])
            self.assertFalse(env.config["gcal_mask"])
        finally:
            env.close()


class IcsParseTests(unittest.TestCase):
    def test_single_allday_timed_and_skip(self):
        day = TODAY + timedelta(days=3)
        utc = datetime(day.year, day.month, day.day, 5, 30)   # 한국 14:30
        text = "\r\n".join([
            "BEGIN:VCALENDAR",
            "BEGIN:VEVENT", "UID:a@google.com", f"DTSTART;VALUE=DATE:{compact(d(1))}", f"DTEND;VALUE=DATE:{compact(d(3))}",
            "SUMMARY:가족 여행\\, 2박", "END:VEVENT",
            "BEGIN:VEVENT", "UID:b@google.com", f"DTSTART:{utc:%Y%m%dT%H%M%S}Z", f"DTEND:{utc + timedelta(hours=1):%Y%m%dT%H%M%S}Z",
            "SUMMARY:치과 예", " 약", "END:VEVENT",
            "BEGIN:VEVENT", "UID:mine@google.com", f"DTSTART;VALUE=DATE:{compact(d(2))}", "SUMMARY:올린 것", "END:VEVENT",
            "BEGIN:VEVENT", "UID:c@google.com", f"DTSTART;VALUE=DATE:{compact(d(2))}", "SUMMARY:표시",
            "DESCRIPTION:" + gcal.MARK, "END:VEVENT",
            "BEGIN:VEVENT", "UID:x@google.com", f"DTSTART;VALUE=DATE:{compact(d(2))}", "STATUS:CANCELLED", "END:VEVENT",
            "BEGIN:VEVENT", "UID:old@google.com", "DTSTART;VALUE=DATE:20000101", "SUMMARY:옛날", "END:VEVENT",
            "END:VCALENDAR"])
        rows = gcal.parse_ics(text, TODAY, skip_uids={"mine@google.com"})
        self.assertEqual([r["text"] for r in rows], ["가족 여행, 2박", "치과 예약"])
        self.assertEqual((rows[0]["date"], rows[0]["end_date"], rows[0]["time"]), (d(1), d(2), None))
        self.assertEqual((rows[1]["date"], rows[1]["time"], rows[1]["end_date"]), (d(3), "14:30", None))

    def test_weekly_recurring_with_exdate_and_override(self):
        start = TODAY - timedelta(days=TODAY.weekday())   # 이번 주 월요일
        s = f"{start:%Y%m%d}T090000"
        ex = f"{start + timedelta(weeks=1):%Y%m%d}T090000"
        moved = f"{start + timedelta(weeks=2):%Y%m%d}T090000"
        text = "\n".join([
            "BEGIN:VCALENDAR",
            "BEGIN:VEVENT", "UID:r@google.com", f"DTSTART;TZID=Asia/Seoul:{s}", f"DTEND;TZID=Asia/Seoul:{start:%Y%m%d}T100000",
            "RRULE:FREQ=WEEKLY;COUNT=4;BYDAY=MO", f"EXDATE;TZID=Asia/Seoul:{ex}", "SUMMARY:주간 회의", "END:VEVENT",
            "BEGIN:VEVENT", "UID:r@google.com", f"RECURRENCE-ID;TZID=Asia/Seoul:{moved}",
            f"DTSTART;TZID=Asia/Seoul:{start + timedelta(weeks=2, days=1):%Y%m%d}T150000",
            f"DTEND;TZID=Asia/Seoul:{start + timedelta(weeks=2, days=1):%Y%m%d}T160000", "SUMMARY:주간 회의(옮김)", "END:VEVENT",
            "END:VCALENDAR"])
        rows = gcal.parse_ics(text, TODAY)
        got = [(r["date"], r["time"], r["text"]) for r in rows]
        self.assertEqual(got, [
            (start.isoformat(), "09:00", "주간 회의"),
            ((start + timedelta(weeks=2, days=1)).isoformat(), "15:00", "주간 회의(옮김)"),
            ((start + timedelta(weeks=3)).isoformat(), "09:00", "주간 회의"),
        ])
        self.assertEqual(len({r["uid"] for r in rows}), 3)   # 반복 일정도 날짜마다 따로 (겹치지 않게)

    def test_monthly_nth_weekday_and_until(self):
        rows = gcal.parse_ics("\n".join([
            "BEGIN:VEVENT", "UID:m@google.com", "DTSTART;VALUE=DATE:20261103", "RRULE:FREQ=MONTHLY;BYDAY=1TU;UNTIL=20270131",
            "SUMMARY:첫째 화요일", "END:VEVENT"]), date(2026, 11, 10))
        self.assertEqual([r["date"] for r in rows], ["2026-11-03", "2026-12-01", "2027-01-05"])


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeGoogle(key="secret-key")
        self.app = App(self.env.db, self.env.config, self.env.collector, Bot(self.env.db, self.env.config, self.env.collector))
        self.g = self.app.gcal
        # 시험에서는 주소 검사(구글 주소만)를 거치지 않고 가짜 서버 주소를 바로 넣음
        self.env.config.values.update(gcal_push=True, gcal_push_url=self.fake.push_url, gcal_push_key="secret-key",
                                      gcal_import=True, gcal_ical_url=self.fake.ical_url)

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def add(self, **f):
        f = {"kind": "event", "category": "참고", **f}
        return self.env.db.execute(f"INSERT INTO events({', '.join(f)}) VALUES({', '.join('?' * len(f))})", tuple(f.values()))

    def add_item(self, source, title, events, registered=1):
        iid = self.env.db.execute("INSERT INTO items(source, title, body, received_at, created_at, registered) "
                                  "VALUES(?, ?, '', ?, ?, ?)", (source, title, TODAY.isoformat(), TODAY.isoformat(), registered))
        for e in events:
            self.add(item_id=iid, **e)
        return iid

    def titles(self):
        return sorted(e["title"] for e in self.fake.events.values())

    def test_create_update_delete_and_types(self):
        manual = self.add(date=d(1), time="13:20", text="학년 협의회", category="회의")
        self.add(date=d(2), text="시간표 교체: 3교시 2-1 과학 (원래 …)", category="수업·평가")   # 시간표 교체(기본 끔)
        self.add_item("school", "2026학년도 학사일정", [{"date": d(5), "end_date": d(6), "text": "중간고사", "category": "학사"}])
        self.add_item("cool", "공문 제출 안내", [{"date": d(3), "kind": "deadline", "text": "계획서 제출", "category": "제출·마감"},
                                           {"date": d(4), "text": "학부모 연수", "category": "연수"}])
        self.add_item("cool", "후보만", [{"date": d(3), "kind": "deadline", "text": "후보 마감"}], registered=0)
        self.add(date=(TODAY + timedelta(days=500)).isoformat(), text="너무 먼 일정")
        res = self.g.push_now()
        self.assertEqual(res, {"sent": 3, "errors": 0, "pending": 0})
        self.assertEqual(self.titles(), ["[마감] 공문 제출 안내", "중간고사", "학년 협의회"])
        ev = next(e for e in self.fake.events.values() if e["title"] == "학년 협의회")
        self.assertEqual((ev["allDay"], ev["start"][11:16], ev["end"][11:16]), (False, "13:20", "14:20"))
        self.assertIn("+09:00", ev["start"])
        school = next(e for e in self.fake.events.values() if e["title"] == "중간고사")
        self.assertEqual((school["allDay"], school["start"], school["end"]), (True, d(5), d(7)))   # 끝 날짜 다음 날(구글 규칙)
        self.assertIn(gcal.MARK, school["description"])
        self.assertIn("secret-key", self.fake.requests[0]["key"])

        # 바뀐 것이 없으면 보내지 않음
        n = len(self.fake.requests)
        self.assertEqual(self.g.plan(), ([], []))
        self.g.tick()
        self.assertEqual(len(self.fake.requests), n)

        # 고치기: 같은 구글 일정이 바뀜 (새로 만들지 않음)
        gid = self.env.db.one("SELECT gid FROM gcal_sync WHERE event_id = ?", (manual,))["gid"]
        self.env.db.execute("UPDATE events SET text = '학년 협의회(장소 변경)', time = NULL WHERE id = ?", (manual,))
        self.g.push_now()
        self.assertEqual(self.fake.events[gid]["title"], "학년 협의회(장소 변경)")
        self.assertTrue(self.fake.events[gid]["allDay"])
        self.assertEqual(len(self.fake.events), 3)

        # 지우기
        self.env.db.execute("DELETE FROM events WHERE id = ?", (manual,))
        self.g.push_now()
        self.assertNotIn(gid, self.fake.events)
        self.assertIn("other@google.com", self.fake.others)   # 다른 일정은 그대로

        # 종류 바꾸기: 학사일정을 끄면 구글에서도 지움, 시간표 교체·받은 자료를 켜면 올림
        self.env.config.values["gcal_push_types"] = ["deadline", "lesson", "inbox"]
        self.g.push_now()
        self.assertEqual(self.titles(), ["[마감] 공문 제출 안내", "공문 제출 안내", "시간표 교체: 3교시 2-1 과학 (원래 …)"])

    def test_mask_names_when_on(self):
        self.env.db.replace_students([{"number": 1, "name": "김민준"}])
        self.add(date=d(1), text="김민준 학부모 상담")
        self.g.push_now()
        self.assertEqual(self.titles(), ["김민준 학부모 상담"])   # 기본: 가리지 않음 (선생님 허용)
        self.env.config.values["gcal_mask"] = True
        self.g.push_now()
        self.assertEqual(self.titles(), ["○○○ 학부모 상담"])

    def test_failure_keeps_queue_and_retries(self):
        self.add(date=d(1), text="회의")
        self.fake.fail = "http"
        with self.assertRaises(gcal.GcalError):
            self.g.push_now()
        st = self.g.status()["push"]
        self.assertFalse(st["ok"])
        self.assertNotIn(self.fake.push_url, st["error"])   # 비밀 주소는 오류 글·기록에 없음
        self.assertFalse(any(self.fake.push_url in r["message"] or "secret-key" in r["message"]
                             for r in self.env.db.query("SELECT message FROM logs")))
        self.assertIsNotNone(self.g.retry_at["push"])
        self.assertEqual(self.g.pending(), 1)
        n = len(self.fake.requests)
        self.g.tick()   # 기다리는 동안은 보내지 않음
        self.assertEqual(len(self.fake.requests), n)
        self.fake.fail = None
        self.g.tick(now=datetime.now() + timedelta(minutes=3))
        self.assertEqual(self.titles(), ["회의"])
        self.assertTrue(self.g.status()["push"]["ok"])
        self.assertEqual(self.g.pending(), 0)

    def test_partial_failure_and_bad_key_and_html(self):
        self.add(date=d(1), text="좋은 일정")
        self.add(date=d(2), text="FAIL 일정")
        res = self.g.push_now()
        self.assertEqual((res["sent"], res["errors"], res["pending"]), (1, 1, 1))
        self.env.config.values["gcal_push_key"] = "wrong"
        with self.assertRaisesRegex(gcal.GcalError, "비밀 키"):
            self.g.ping()
        self.env.config.values["gcal_push_key"] = "secret-key"
        self.fake.fail = "html"
        with self.assertRaisesRegex(gcal.GcalError, "모든 사용자"):
            self.g.ping()
        self.fake.fail = None
        self.assertEqual(self.g.ping()["calendar"], "선생님")

    def test_import_skips_own_events_and_replaces(self):
        self.add(date=d(1), text="업무 데스크 일정")
        self.g.push_now()
        gid = next(iter(self.fake.events))
        self.fake.ics = "\r\n".join([
            "BEGIN:VCALENDAR",
            "BEGIN:VEVENT", f"UID:{gid}", f"DTSTART;VALUE=DATE:{compact(d(1))}", "SUMMARY:업무 데스크 일정", "END:VEVENT",
            "BEGIN:VEVENT", "UID:p@google.com", f"DTSTART;VALUE=DATE:{compact(d(2))}", "SUMMARY:개인 약속", "END:VEVENT",
            "END:VCALENDAR"])
        self.assertEqual(self.g.import_now(), {"count": 1})
        self.assertEqual(self.g.import_now(), {"count": 1})   # 다시 가져와도 겹치지 않음
        rows = self.g.events_between(TODAY, TODAY + timedelta(days=10))
        self.assertEqual([(r["date"], r["text"]) for r in rows], [(d(2), "개인 약속")])
        # 가져온 일정은 업무 데스크 일정(events)에 섞이지 않음 → 다시 올리지 않음
        self.assertEqual(self.env.db.one("SELECT COUNT(*) AS n FROM events")["n"], 1)
        self.env.config.values["gcal_import"] = False
        self.assertEqual(self.g.events_between(TODAY, TODAY + timedelta(days=10)), [])

    def test_import_failure_keeps_old_rows(self):
        self.fake.ics = "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:p\nDTSTART;VALUE=DATE:%s\nSUMMARY:개인\nEND:VEVENT\nEND:VCALENDAR" % compact(d(1))
        self.g.import_now()
        self.fake.fail = "http"
        with self.assertRaises(gcal.GcalError):
            self.g.import_now()
        self.assertEqual(len(self.g.events_between(TODAY, TODAY + timedelta(days=5))), 1)
        self.assertFalse(self.g.status()["import"]["ok"])
        self.fake.fail = None
        self.fake.ics = "<html>"
        with self.assertRaisesRegex(gcal.GcalError, "달력 자료가 아닙니다"):
            self.g.import_now()

    def test_off_sends_nothing(self):
        self.env.config.values.update(gcal_push=False, gcal_import=False)
        self.add(date=d(1), text="회의")
        self.g.tick()
        self.assertEqual(self.fake.requests, [])
        with self.assertRaises(gcal.GcalError):
            self.g.push_now()

    def test_script_text_has_key_and_guards(self):
        key = self.g.new_key()
        s = gcal.script_text(key)
        self.assertIn(f"const DESK_KEY = '{key}';", s)
        self.assertIn("ev.getTag(TAG)", s)   # 업무 데스크가 만든 일정만 고침·지움
        self.assertEqual(self.env.config["gcal_push_key"], key)
        self.assertNotEqual(self.g.new_key(), key)


class ApiTests(unittest.TestCase):
    def test_api(self):
        import http.client
        import json
        import threading
        from http.server import ThreadingHTTPServer
        from desk.server import make_handler
        env = Env()
        fake = FakeGoogle(key="")
        app = App(env.db, env.config, env.collector, Bot(env.db, env.config, env.collector))
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
        port = httpd.server_address[1]
        env.config.values["port"] = port
        httpd.RequestHandlerClass = make_handler(app)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        def req(method, path, body=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request(method, path, body=json.dumps(body) if body is not None else None,
                      headers={"Host": f"127.0.0.1:{port}", "X-Desk-Token": app.token, "Content-Type": "application/json"})
            r = c.getresponse()
            out = r.status, json.loads(r.read())
            c.close()
            return out

        try:
            st, res = req("GET", "/api/gcal")
            self.assertEqual(st, 200)
            self.assertFalse(res["push_on"] or res["import_on"])
            self.assertEqual(res["types"], gcal.DEFAULT_TYPES)
            st, res = req("POST", "/api/gcal", {"action": "script"})
            self.assertEqual(st, 200)
            self.assertIn(env.config["gcal_push_key"], res["script"])
            self.assertNotIn(env.config["gcal_push_key"], json.dumps(req("GET", "/api/gcal")[1]))   # 다시 보이지 않음
            self.assertNotIn(env.config["gcal_push_key"], json.dumps(req("GET", "/api/state")[1]))
            st, res = req("PUT", "/api/settings", {"gcal_push_url": "https://evil.example/macros/s/x/exec"})
            self.assertEqual(st, 400)
            self.assertEqual(req("POST", "/api/gcal", {"action": "check"})[0], 400)   # 주소 없음
            fake.key = env.config["gcal_push_key"]
            env.config.values.update(gcal_push_url=fake.push_url, gcal_push=True)
            st, res = req("POST", "/api/gcal", {"action": "check"})
            self.assertEqual((st, res["check"]["push"]["ok"], res["check"]["push"]["calendar"]), (200, True, "선생님"))
            st, ev = req("POST", "/api/events", {"date": d(1), "kind": "event", "category": "회의", "text": "협의회"})
            self.assertEqual(st, 200)
            st, res = req("POST", "/api/gcal", {"action": "push"})
            self.assertEqual((st, res["result"]["sent"], res["pending"]), (200, 1, 0))
            st, res = req("GET", f"/api/gcal/events?from={d(0)}&to={d(5)}")
            self.assertEqual((st, res), (200, []))
            st, res = req("POST", "/api/gcal", {"action": "clear_push"})
            self.assertFalse(res["push_set"])
            self.assertEqual(req("POST", "/api/gcal", {"action": "bogus"})[0], 400)
        finally:
            httpd.shutdown()
            httpd.server_close()
            fake.close()
            env.close()


if __name__ == "__main__":
    unittest.main()
