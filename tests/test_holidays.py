import unittest
from datetime import date, datetime
from unittest.mock import patch

from desk import holidays


YEARLY_HOLIDAYS = {
    2026: """
        2026-01-01|신정
        2026-02-16|설날
        2026-02-17|설날
        2026-02-18|설날
        2026-03-01|삼일절
        2026-03-02|대체공휴일(삼일절)
        2026-05-01|노동절
        2026-05-05|어린이날
        2026-05-24|부처님오신날
        2026-05-25|대체공휴일(부처님오신날)
        2026-06-03|지방선거일
        2026-06-06|현충일
        2026-07-17|제헌절
        2026-08-15|광복절
        2026-08-17|대체공휴일(광복절)
        2026-09-24|추석
        2026-09-25|추석
        2026-09-26|추석
        2026-10-03|개천절
        2026-10-05|대체공휴일(개천절)
        2026-10-09|한글날
        2026-12-25|성탄절
    """,
    2027: """
        2027-01-01|신정
        2027-02-06|설날
        2027-02-07|설날
        2027-02-08|설날
        2027-02-09|대체공휴일(설날)
        2027-03-01|삼일절
        2027-05-01|노동절
        2027-05-03|대체공휴일(노동절)
        2027-05-05|어린이날
        2027-05-13|부처님오신날
        2027-06-06|현충일
        2027-07-17|제헌절
        2027-07-19|대체공휴일(제헌절)
        2027-08-15|광복절
        2027-08-16|대체공휴일(광복절)
        2027-09-14|추석
        2027-09-15|추석
        2027-09-16|추석
        2027-10-03|개천절
        2027-10-04|대체공휴일(개천절)
        2027-10-09|한글날
        2027-10-11|대체공휴일(한글날)
        2027-12-25|성탄절
        2027-12-27|대체공휴일(성탄절)
    """,
    2028: """
        2028-01-01|신정
        2028-01-26|설날
        2028-01-27|설날
        2028-01-28|설날
        2028-03-01|삼일절
        2028-04-12|국회의원선거일
        2028-05-01|노동절
        2028-05-02|부처님오신날
        2028-05-05|어린이날
        2028-06-06|현충일
        2028-07-17|제헌절
        2028-08-15|광복절
        2028-10-02|추석
        2028-10-03|개천절·추석
        2028-10-04|추석
        2028-10-05|대체공휴일(개천절·추석)
        2028-10-09|한글날
        2028-12-25|성탄절
    """,
}


class HolidayTests(unittest.TestCase):
    def test_yearly_holiday_dates_and_names(self):
        for year, rows in YEARLY_HOLIDAYS.items():
            with self.subTest(year=year):
                expected = dict(line.split("|", 1) for line in rows.split())
                actual = holidays.holidays_between(date(year, 1, 1), date(year, 12, 31))
                self.assertEqual(actual, expected)

    def test_weekend(self):
        self.assertTrue(holidays.is_weekend(date(2026, 10, 3)))
        self.assertTrue(holidays.is_weekend(date(2026, 10, 4)))
        self.assertFalse(holidays.is_weekend(date(2026, 10, 2)))

    def test_name_and_inclusive_range(self):
        self.assertEqual(holidays.holiday_name(date(2026, 3, 1)), "삼일절")
        self.assertEqual(
            holidays.holidays_between(date(2026, 3, 1), date(2026, 3, 2)),
            {"2026-03-01": "삼일절", "2026-03-02": "대체공휴일(삼일절)"},
        )
        self.assertIsNone(holidays.holiday_name(date(2026, 3, 3)))

    def test_maximum_date_range(self):
        self.assertEqual(holidays.holidays_between(date.max, date.max), {})
        self.assertEqual(holidays.holidays_between(date(9999, 12, 30), date.max), {})

    def test_unverified_years_are_not_predicted(self):
        for year in (2025, 2029, 2030):
            with self.subTest(year=year):
                self.assertEqual(holidays.holidays_between(date(year, 1, 1), date(year, 12, 31)), {})

    def test_same_day_overlap_matches_kasi_2028_calendar(self):
        self.assertEqual(holidays.holiday_name(date(2028, 10, 5)), "대체공휴일(개천절·추석)")
        self.assertIsNone(holidays.holiday_name(date(2028, 10, 6)))

    def test_substitutes_for_different_dates_do_not_merge(self):
        with patch.object(holidays, "_FIXED", ((5, 1, "노동절"), (5, 2, "어린이날"))), \
                patch.object(holidays, "_LUNAR", {2027: ()}), \
                patch.object(holidays, "_ELECTIONS", {}):
            result = holidays._build_holidays()
        self.assertEqual(result[date(2027, 5, 3)], "대체공휴일(노동절)")
        self.assertEqual(result[date(2027, 5, 4)], "대체공휴일(어린이날)")

    def test_saturday_lunar_overlap_does_not_create_an_extra_substitute(self):
        with patch.object(holidays, "_FIXED", ((5, 1, "노동절"),)), \
                patch.object(holidays, "_LUNAR", {2027: ((5, 1, "추석"),)}), \
                patch.object(holidays, "_ELECTIONS", {}):
            result = holidays._build_holidays()
        self.assertEqual(result[date(2027, 5, 3)], "대체공휴일(노동절)")
        self.assertNotIn(date(2027, 5, 4), result)

    def test_invalid_input(self):
        for value in ("2026-03-01", datetime(2026, 3, 1)):
            with self.subTest(value=value), self.assertRaises(TypeError):
                holidays.holiday_name(value)
        with self.assertRaises(ValueError):
            holidays.holidays_between(date(2026, 3, 2), date(2026, 3, 1))


if __name__ == "__main__":
    unittest.main()
