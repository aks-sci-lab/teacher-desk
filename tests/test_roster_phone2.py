"""학생 명단 '학부모 연락처 2' (0.5.89, 가짜 자료).

- 붙여넣기: +82 → 010, 한 칸의 번호 둘을 1·2칸에 나눔, 못 읽으면 경고(조용히 버리지 않음)
- 저장: None = 예전 연락처 이어 받음, "" = 지움 (직접 고치기)
- 통화·문자 맞추기는 1·2칸 모두
"""
from desk import calllog
from desk.server import parse_roster
from tests.test_classroom_api import ApiBase


class ParseRosterPhoneTests(ApiBase):
    def test_paste_variants(self):
        rows = parse_roster(
            "번호\t이름\t학부모 연락처\t연락처 2\n"
            "1\t가짜하나\t+82 10-1111-0001\n"
            "2\t가짜둘\t010-1111-0002 / 010-2222-0002\n"
            "3\t가짜셋\t010-1111-0003, 01022220003\n"
            "4\t가짜넷\t010-1111-0004\t+82-10-2222-0004\n"
            "5 가짜다섯 010 1111 0005 010-2222-0005\n"
            "6\t가짜여섯\t010-12-34\n"
            "7\t가짜일곱\t010-1111-0007 010-2222-0007 010-3333-0007\n")
        by = {r["number"]: r for r in rows}
        self.assertEqual(len(rows), 7)                                  # 머리줄은 학생이 아님
        self.assertEqual((by[1]["parent_phone"], by[1]["parent_phone2"]), ("010-1111-0001", None))
        for n in (2, 3, 4, 5):
            self.assertEqual((by[n]["parent_phone"], by[n]["parent_phone2"]),
                             (f"010-1111-000{n}", f"010-2222-000{n}"), n)
            self.assertNotIn("warning", by[n])
        self.assertEqual(by[5]["name"], "가짜다섯")
        self.assertIsNone(by[6]["parent_phone"])
        self.assertIn("010-12-34", by[6]["warning"])                     # 못 읽은 것은 경고
        self.assertEqual((by[7]["parent_phone"], by[7]["parent_phone2"]), ("010-1111-0007", "010-2222-0007"))
        self.assertIn("3개", by[7]["warning"])

    def test_save_keep_and_clear(self):
        st, saved = self.req("PUT", "/api/students", {"rows": [
            {"number": 1, "name": "가짜하나", "parent_phone": "010-1111-0001", "parent_phone2": "+82 10-2222-0001"}]})
        self.assertEqual(st, 200, saved)
        self.assertEqual((saved[0]["parent_phone"], saved[0]["parent_phone2"]), ("010-1111-0001", "010-2222-0001"))
        # 붙여넣기처럼 연락처 없이(None) 다시 저장하면 예전 값을 이어 받음
        st, saved = self.req("PUT", "/api/students", {"rows": [{"number": 1, "name": "가짜하나",
                                                                "parent_phone": None, "parent_phone2": None}]})
        self.assertEqual((saved[0]["parent_phone"], saved[0]["parent_phone2"]), ("010-1111-0001", "010-2222-0001"))
        # 직접 고치기에서 비우면 지움
        st, saved = self.req("PUT", "/api/students", {"rows": [{"number": 1, "name": "가짜하나",
                                                                "parent_phone": "010-1111-0001", "parent_phone2": ""}]})
        self.assertEqual((saved[0]["parent_phone"], saved[0]["parent_phone2"]), ("010-1111-0001", None))
        st, err = self.req("PUT", "/api/students", {"rows": [{"number": 1, "name": "가짜하나", "parent_phone2": "abc"}]})
        self.assertEqual(st, 400)
        self.assertIn("연락처 2", err["error"])

    def test_calllog_matches_second_phone(self):
        self.env.db.replace_students([{"number": 1, "name": "가짜하나", "parent_phone": "010-1111-0001",
                                       "parent_phone2": "010-2222-0001"},
                                      {"number": 2, "name": "가짜둘", "parent_phone2": "010-2222-0002"}])
        m = calllog.parent_map(self.env.db)
        self.assertEqual(m["01011110001"], [1])
        self.assertEqual(m["01022220001"], [1])
        self.assertEqual(m["01022220002"], [2])                          # 2칸만 있어도 맞춤
