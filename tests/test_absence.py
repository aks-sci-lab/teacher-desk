import unittest
from datetime import date

from desk import absence, schoolcal
from tests.test_collect import Env


class AbsenceTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def test_parse_name_number_type_and_date_ranges(self):
        for word, kind in absence.TYPE_WORDS.items():
            row = absence.parse_command(f"김가온 7번 {word} 10/29~11/3", date(2026, 10, 29))
            self.assertEqual((row["student_name"], row["student_number"], row["absence_type"]), ("김가온", 7, kind))
            self.assertEqual((row["start_date"], row["end_date"]), ("2026-10-29", "2026-11-03"))
        row = absence.parse_command("7 김가온 병결 2026년 12월 31일부터 1월 4일까지", date(2026, 10, 29))
        self.assertEqual((row["start_date"], row["end_date"]), ("2026-12-31", "2027-01-04"))
        row = absence.parse_command("김가온 7 병결 2026-10-29", date(2030, 1, 1))
        self.assertEqual(row["start_date"], row["end_date"])
        for text in ("김가온 7 감기 10/29", "김가온 7 병결 2/30", "김가온 병결 10/29",
                     "7 병결 10/29", "김가온 7 병결 2026-11-03~2026-10-29",
                     "김가온 7 병결 2026-01-01~2028-01-01"):
            with self.subTest(text=text), self.assertRaises(absence.AbsenceError):
                absence.parse_command(text, date(2026, 10, 29))

    def test_school_calendar_excludes_holidays_but_not_regular_events(self):
        db = self.env.db
        with self.assertRaises(absence.AbsenceError):
            absence.school_dates(db, date(2026, 10, 1), date(2026, 10, 9), require_calendar=True)
        schoolcal.save(db, 2026, [
            {"date": "2026-10-05", "end_date": None, "text": "대체공휴일", "kind": "event", "category": "학사"},
            {"date": "2026-10-06", "end_date": None, "text": "1학년 재량휴업일", "kind": "event", "category": "학사"},
            {"date": "2026-10-07", "end_date": None, "text": "체육대회", "kind": "event", "category": "학사"},
            {"date": "2026-10-08", "end_date": None, "text": "방학식", "kind": "event", "category": "학사"},
            {"date": "2026-10-09", "end_date": None, "text": "한글날", "kind": "event", "category": "학사"},
        ], "나이스")
        days = absence.school_dates(db, date(2026, 10, 1), date(2026, 10, 11), "1", True)
        self.assertEqual(days, ["2026-10-01", "2026-10-02", "2026-10-07", "2026-10-08"])
        self.assertIn("2026-10-06", absence.school_dates(db, date(2026, 10, 1), date(2026, 10, 11), "2", True))

