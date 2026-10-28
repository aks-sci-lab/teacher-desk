import unittest
from datetime import date

from desk import extract

REF = date(2026, 10, 26)  # 월요일


def dates(text, ref=REF):
    return [(d.isoformat(), e.isoformat() if e else None) for _, _, d, e in extract.find_dates(text, ref)]


class DateTests(unittest.TestCase):
    def test_full_dates(self):
        self.assertEqual(dates("2026. 11. 3.(화) 회의"), [("2026-11-03", None)])
        self.assertEqual(dates("2026-11-03"), [("2026-11-03", None)])
        self.assertEqual(dates("2026년 11월 3일"), [("2026-11-03", None)])

    def test_month_day_forms(self):
        self.assertEqual(dates("11월 3일(화요일)까지"), [("2026-11-03", None)])
        self.assertEqual(dates("11.3.(화) 제출"), [("2026-11-03", None)])
        self.assertEqual(dates("11/3 마감"), [("2026-11-03", None)])

    def test_year_rollover(self):
        # 12월에 받은 '1월 5일' 은 다음 해
        self.assertEqual(dates("1월 5일", date(2026, 12, 20)), [("2027-01-05", None)])
        # 1월에 받은 '12월 28일' 은 지난해
        self.assertEqual(dates("12월 28일", date(2027, 1, 3)), [("2026-12-28", None)])

    def test_relative(self):
        self.assertEqual(dates("내일 회의"), [("2026-10-27", None)])
        self.assertEqual(dates("모레"), [("2026-10-28", None)])
        self.assertEqual(dates("다음 주 수요일"), [("2026-11-04", None)])
        self.assertEqual(dates("이번 주 금요일"), [("2026-10-30", None)])
        self.assertEqual(dates("목요일에"), [("2026-10-29", None)])

    def test_ranges(self):
        self.assertEqual(dates("11월 9일 ~ 13일"), [("2026-11-09", "2026-11-13")])
        self.assertEqual(dates("11. 9.(월) ~ 11. 13.(금)"), [("2026-11-09", "2026-11-13")])
        self.assertEqual(dates("11월 28일 ~ 3일"), [("2026-11-28", "2026-12-03")])

    def test_not_dates(self):
        self.assertEqual(dates("1.5배 늘어남"), [])
        self.assertEqual(dates("3/4 분기 예산 13/20 항목"), [])
        self.assertEqual(dates("2월 30일"), [])

    def test_times(self):
        self.assertEqual(extract.find_time(" 15:00까지"), "15:00")
        self.assertEqual(extract.find_time(" 오후 3시 30분"), "15:30")
        self.assertEqual(extract.find_time(" 2시 반"), "14:30")  # 학교 일과 중 2시 = 오후
        self.assertEqual(extract.find_time(" 오전 9시"), "09:00")
        self.assertEqual(extract.find_time(" 8시 40분 등교"), "08:40")
        self.assertIsNone(extract.find_time(" 3시간 연수"))


class AnalyzeTests(unittest.TestCase):
    TEXT = (
        "[교무부] 2학기 학업성적관리위원회 개최 안내\n"
        "1. 일시: 2026. 11. 3.(화) 15:00\n"
        "2. 장소: 교무실\n"
        "3. 각 교과 협의록은 10월 30일(금)까지 제출 바랍니다.\n"
        "4. 관련: 학교혁신과-12345\n"
    )

    def test_analyze(self):
        a = extract.analyze(self.TEXT, REF)
        self.assertEqual(a["title"], "[교무부] 2학기 학업성적관리위원회 개최 안내")
        self.assertEqual(a["category"], "제출·마감")
        self.assertEqual(a["doc_numbers"], ["학교혁신과-12345"])
        evs = {(e["date"], e["time"], e["kind"], e["category"]) for e in a["events"]}
        self.assertIn(("2026-11-03", "15:00", "event", "회의"), evs)  # 제목의 '위원회' 로 회의
        self.assertIn(("2026-10-30", None, "deadline", "제출·마감"), evs)
        self.assertTrue(any("장소" in s for s in a["summary"]))

    def test_doc_numbers(self):
        f = extract.find_doc_numbers
        self.assertEqual(f("관련: 교육과정과-1234, 교무부 -56호"), ["교육과정과-1234", "교무부-56"])
        self.assertEqual(f("2026교무과-123"), ["교무과-123"])  # 앞의 숫자는 빼고
        self.assertEqual(f("교무과-12345678"), [])           # 8자리는 문서번호가 아님

    def test_doc_numbers_fast_on_long_runs(self):
        # 띄어쓰기 없는 긴 글에서 예전에는 수십 초 걸렸음
        import time
        start = time.monotonic()
        extract.analyze("11월" * 30000 + " 교무과-12", REF)
        self.assertLess(time.monotonic() - start, 3)

    def test_many_dates_on_one_line_fast(self):
        # 날짜가 아주 많은 한 줄 (줄바꿈 없는 표 등): 예전에는 날짜마다 줄 전체를 다시 훑어 수십 초
        import time
        start = time.monotonic()
        extract.analyze("11월 3일 제출 " * 20000, REF)
        self.assertLess(time.monotonic() - start, 3)

    def test_no_dates(self):
        a = extract.analyze("교직원 여러분 수고 많으셨습니다.", REF)
        self.assertEqual(a["events"], [])
        self.assertEqual(a["category"], "참고")

    def test_summary_skips_boilerplate(self):
        s = extract.summarize("수신 수신자 참조\n경유\n제목 연수 안내\n1. 기간: 11월 9일 ~ 13일\n2. 대상: 전 교원")
        self.assertFalse(any(x.startswith(("수신", "경유")) for x in s))
        self.assertEqual(len(s), 2)


if __name__ == "__main__":
    unittest.main()
