import unittest
from datetime import date

from desk import ai, extract
from desk.collect import add_text_item
from tests.test_collect import Env

FUNERAL = """[부고] 교무부 ○○○ 선생님 부친상
1. 별세일: 2026. 10. 27.(화)
2. 발인일시: 2026. 10. 29.(목) 06:00
※ 직무연수 일정과 겹치시는 분은 참고 바랍니다"""


class RuleTests(unittest.TestCase):
    def test_family_events_and_clean_text(self):
        a = extract.analyze(FUNERAL, ref=date(2026, 10, 27))
        self.assertEqual(a["category"], "경조사")
        self.assertEqual({e["category"] for e in a["events"]}, {"경조사"})          # '연수'가 섞여 있어도
        self.assertEqual([e["text"] for e in a["events"]], ["별세일: 2026. 10. 27.(화)", "발인일시: 2026. 10. 29.(목) 06:00"])
        self.assertEqual(extract.event_text("♥ 일시: 11월 15일 오후 2시"), "일시: 11월 15일 오후 2시")
        self.assertEqual(extract.event_text("2026. 11. 4. 제출"), "2026. 11. 4. 제출")   # 날짜 숫자는 번호가 아님


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        db = self.env.db
        self.a = add_text_item(db, "cool", "체육대회 안내\n일시: 11월 20일", title="체육대회", received_at="2026-10-28 09:00:00")["id"]
        self.b = add_text_item(db, "edufine", "연수 신청 안내\n신청은 공문 참고", title="연수 신청", received_at="2026-10-28 09:00:00")["id"]
        self.c = add_text_item(db, "cool", "협의록 제출 11월 3일까지", title="협의록", received_at="2026-10-28 09:00:00")["id"]
        db.execute("UPDATE items SET registered = 1 WHERE id = ?", (self.c,))   # 선생님이 달력에 넣은 것

    def tearDown(self):
        self.env.close()

    def test_summary_rows_fix_categories_and_events(self):
        db = self.env.db
        text = ("## 꼭 할 일 (마감 순)\n- 11/07(금) · 연수 신청 [2]\n- 11/20(금) · 체육대회 참가 신청 [1]\n"
                "- 11/03(화) · 협의록 제출 [3]\n## 다가오는 행사·회의\n- 11/20(금) 13:30 · 체육대회 [1]\n"
                "## 알아 둘 내용\n- 교과서 주문 완료 [9]")
        r = ai.apply_summary(db, text, [self.a, self.b, self.c], today=date(2026, 10, 28))
        self.assertEqual(r, {"added": 1, "changed": 2, "items": 2})
        b = db.item(self.b)
        self.assertEqual(b["category"], "제출·마감")
        self.assertEqual([(e["date"], e["kind"], e["source"]) for e in b["events"]], [("2026-11-07", "deadline", "ai")])
        a = db.item(self.a)
        self.assertEqual(a["category"], "제출·마감")                          # 처음 나온 칸을 따름
        self.assertEqual([(e["date"], e["kind"], e["time"], e["source"]) for e in a["events"]],
                         [("2026-11-20", "deadline", "13:30", None)])           # 마감은 그대로, 시각만 더함
        c = db.item(self.c)
        self.assertEqual(c["category"], "제출·마감")
        self.assertEqual(len(c["events"]), 1)                                  # 달력에 넣은 것은 그대로
        self.assertIsNone(c["events"][0]["source"])

    def test_parse_and_year(self):
        rows = ai.parse_summary("## 꼭 할 일\n- **01/05(월)** · 결과 보고 [1, 2]\n- 상시 · 이수증 제출 [3]\n- 없음")
        self.assertEqual(rows, [("todo", "01/05(월)", "결과 보고", [1, 2]), ("todo", "상시", "이수증 제출", [3]),
                                ("todo", "", "없음", [])])
        self.assertEqual(ai.row_date("01/05(월)", date(2026, 12, 20)), date(2027, 1, 5))   # 12월에 받은 1월은 다음 해
        self.assertIsNone(ai.row_date("상시", date(2026, 12, 20)))


if __name__ == "__main__":
    unittest.main()
