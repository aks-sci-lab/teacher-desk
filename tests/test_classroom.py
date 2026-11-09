"""학급 경영: 좌석 무작위 배치 조건, 청소·1인 1역 돌리기, 당번 순서, 모둠 나누기, API (0.5.80)."""
import random
import unittest
from datetime import date

from desk import classroom as cr
from tests.test_classroom_api import ApiBase


def seats(**kw):
    s = cr.default_seats()
    s.update(kw)
    s["cells"] = kw.get("cells", [None] * (s["rows"] * s["cols"]))
    return s


class SeatTests(unittest.TestCase):
    def test_neighbors_and_mates(self):
        self.assertEqual(cr.mates_of(0, 2, 4, True), [1])
        self.assertEqual(cr.mates_of(3, 2, 4, True), [2])
        self.assertEqual(cr.mates_of(5, 2, 4, False), [4, 6])
        self.assertEqual(sorted(cr.near_of(0, 2, 2)), [1, 2, 3])

    def test_shuffle_places_everyone_and_keeps_locked_and_off(self):
        nums = list(range(1, 25))
        s = seats(rows=5, cols=6, off=[5, 29])
        s["cells"][12] = 7
        s["locked"] = [12]
        r = cr.shuffle_seats(s, nums, rng=random.Random(1))
        cells = r["cells"]
        self.assertEqual(cells[12], 7)
        self.assertIsNone(cells[5])
        self.assertIsNone(cells[29])
        self.assertEqual(sorted(n for n in cells if n), nums)
        self.assertEqual(r["unplaced"], [])

    def test_front_apart_and_mates_conditions(self):
        nums = list(range(1, 29))
        prev = {(1, 2), (3, 4), (5, 6), (7, 8)}
        for seed in range(5):
            s = seats(rows=5, cols=6, front=[10, 11, 12], front_rows=1, apart=[[1, 2], [3, 9]], avoid_mates=True)
            r = cr.shuffle_seats(s, nums, prev_mates=prev, rng=random.Random(seed))
            self.assertEqual(r["problems"], [], seed)
            where = {n: i for i, n in enumerate(r["cells"]) if n}
            for n in (10, 11, 12):
                self.assertLess(where[n] // 6, 1)
            self.assertNotIn(where[2], cr.near_of(where[1], 5, 6))
            self.assertFalse(cr.mate_pairs({**s, "cells": r["cells"]}) & prev)

    def test_too_few_seats_reports_unplaced(self):
        r = cr.shuffle_seats(seats(rows=2, cols=2), [1, 2, 3, 4, 5, 6], rng=random.Random(0))
        self.assertEqual(r["unplaced"], [5, 6])

    def test_impossible_front_is_reported(self):
        s = seats(rows=2, cols=2, front=[1, 2, 3], front_rows=1)
        r = cr.shuffle_seats(s, [1, 2, 3, 4], rng=random.Random(0))
        self.assertEqual([k for k, _ in r["problems"]], ["front"])

    def test_clean_seats_drops_unknown_and_duplicates(self):
        s = cr.clean_seats({"rows": 1, "cols": 4, "cells": [1, 1, 99, None], "off": [3], "locked": [0, 2]}, {1, 2})
        self.assertEqual(s["cells"], [1, None, None, None])
        self.assertEqual(s["locked"], [0])
        with self.assertRaises(cr.ClassError):
            cr.clean_seats({"rows": 2, "cols": 2, "cells": [None]}, {1})


class RotateTests(unittest.TestCase):
    ITEMS = [{"name": "바닥", "count": 4}, {"name": "칠판", "count": 2}, {"name": "복도", "count": 3}]

    def test_one_role_each_and_avoids_same_role_in_a_row(self):
        students = list(range(1, 10))
        past = []
        for n in range(6):
            a = cr.rotate(self.ITEMS, students, past, random.Random(n))
            got = [s for v in a.values() for s in v]
            self.assertEqual(sorted(got), students)
            self.assertEqual({k: len(v) for k, v in a.items()}, {"바닥": 4, "칠판": 2, "복도": 3})
            if past:
                last = {s: k for k, v in past[-1].items() for s in v}
                for k, v in a.items():
                    for s in v:
                        self.assertNotEqual(last[s], k, (n, s))
            past.append(a)

    def test_even_spread_over_time(self):
        students = list(range(1, 10))
        past = []
        for n in range(9):
            past.append(cr.rotate(self.ITEMS, students, past, random.Random(n)))
        count = {s: sum(s in p["칠판"] for p in past) for s in students}
        self.assertLessEqual(max(count.values()) - min(count.values()), 1)

    def test_fewer_slots_rested_students_go_first(self):
        items = [{"name": "분리수거", "count": 2}]
        first = cr.rotate(items, [1, 2, 3, 4], [], random.Random(0))
        second = cr.rotate(items, [1, 2, 3, 4], [first], random.Random(0))
        self.assertEqual(set(first["분리수거"]) & set(second["분리수거"]), set())

    def test_period_keys(self):
        self.assertEqual(cr.period_key("week", date(2026, 11, 12)), "2026-11-09")
        self.assertEqual(cr.period_key("month", date(2026, 11, 12)), "2026-11")
        self.assertEqual(cr.period_label("week", "2026-11-09"), "11/9(월) ~ 11/13(금)")


class GroupTests(unittest.TestCase):
    def test_sizes_even_and_apart(self):
        people = list(range(1, 27))
        groups, bad = cr.make_groups(people, "count", 6, apart=[[1, 2], [3, 4]], rng=random.Random(2))
        self.assertEqual(sorted(len(g) for g in groups), [4, 4, 4, 4, 5, 5])
        self.assertEqual(sorted(n for g in groups for n in g), people)
        self.assertEqual(bad, [])
        for g in groups:
            self.assertFalse({1, 2} <= set(g) or {3, 4} <= set(g))

    def test_size_mode_and_less_overlap(self):
        people = list(range(1, 13))
        first, _ = cr.make_groups(people, "size", 4, rng=random.Random(0))
        self.assertEqual(len(first), 3)
        second, _ = cr.make_groups(people, "size", 4, past=[first], rng=random.Random(0))
        same = sum(1 for g in second for i, a in enumerate(g) for b in g[i + 1:]
                   if any(a in f and b in f for f in first))
        self.assertLessEqual(same, 3)


class DutyAndApiTests(ApiBase):
    def test_duty_order_skips_weekend_and_excluded(self):
        db = self.env.db
        cfg = cr.clean_duty({"day": {"on": True, "per": 2, "start": "2026-11-09", "first": 1},
                             "week": {"on": True, "per": 1, "start": "2026-11-02"}, "exclude": [3]}, set(range(1, 13)))
        self.assertEqual(cr.duty_on(db, cfg, "day", date(2026, 11, 9)), [1, 2])
        self.assertEqual(cr.duty_on(db, cfg, "day", date(2026, 11, 10)), [4, 5])
        self.assertEqual(cr.duty_on(db, cfg, "day", date(2026, 11, 11)), [6, 7])        # 명단 12명, 3번 빠짐
        self.assertEqual(cr.duty_on(db, cfg, "day", date(2026, 11, 14)), [])        # 토요일
        self.assertEqual(cr.duty_on(db, cfg, "day", date(2026, 11, 16)), [12, 1])   # 수업일 5일 뒤, 끝에서 처음으로
        self.assertEqual(cr.duty_on(db, cfg, "week", date(2026, 11, 12)), [2])

    def test_seats_api_record_and_history(self):
        st, r = self.req("POST", "/api/class/seats/shuffle", {"seats": {"rows": 3, "cols": 4}})
        self.assertEqual(st, 200)
        cells = r["cells"]
        st, r = self.req("PUT", "/api/class/seats", {"seats": {"rows": 3, "cols": 4, "cells": cells}})
        self.assertEqual(st, 200)
        self.assertEqual(r["seats"]["cells"], cells)
        st, r = self.req("PUT", "/api/class/seats", {"seats": {"rows": 3, "cols": 4, "cells": [None] * 12},
                                                     "record": "무작위 배치 전"})
        self.assertEqual(len(r["history"]), 1)
        st, h = self.req("GET", f"/api/class/seats/history/{r['history'][0]['id']}")
        self.assertEqual(h["data"]["cells"], cells)
        self.assertEqual(self.req("PUT", "/api/class/seats", {"seats": {"rows": 30}})[0], 400)

    def test_rotation_api_auto_assigns_and_manual_edit(self):
        st, r = self.req("PUT", "/api/class/rot/role/config",
                         {"items": [{"name": "우유", "count": 2}, {"name": "칠판", "count": 1}], "period": "month"})
        self.assertEqual(st, 200)
        self.assertEqual(sum(len(v) for v in r["current"]["assign"].values()), 3)
        st, r = self.req("PUT", "/api/class/rot/role/assign", {"assign": {"우유": [1, 2], "칠판": [2]}})
        self.assertEqual(r["current"]["assign"], {"우유": [1, 2], "칠판": []})
        self.assertEqual(self.req("PUT", "/api/class/rot/role/assign", {"assign": {"없는역할": [1]}})[0], 400)

    def test_groups_api_and_brief(self):
        st, r = self.req("POST", "/api/class/groups/make", {"mode": "count", "value": 2, "apart": [[1, 2]]})
        self.assertEqual(st, 200)
        self.assertEqual(len(r["groups"]), 2)
        st, r = self.req("POST", "/api/class/groups/make", {"mode": "count", "value": 3})
        self.assertEqual(len(r["history"]), 1)
        self.req("PUT", "/api/class/duty", {"day": {"on": True, "per": 1, "start": "2026-11-09"}})
        st, today = self.req("GET", "/api/today")
        self.assertEqual(st, 200)
        self.assertIn("classroom", today)

    def test_class_data_not_in_search(self):
        self.req("PUT", "/api/class/rot/clean/config", {"items": [{"name": "비밀구역", "count": 1}], "period": "week"})
        st, r = self.req("GET", "/api/search?q=%EB%B9%84%EB%B0%80%EA%B5%AC%EC%97%AD")
        self.assertEqual(st, 200)
        self.assertNotIn("비밀구역", str(r))


if __name__ == "__main__":
    unittest.main()
