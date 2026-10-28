import os
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from desk import coolmsg, docs, extract
from desk.collect import Collector, add_text_item, cool_title
from tests.test_collect import Env
from tests.test_coolmsg import comp, make_udb

NOTICE = """2026 년 학생평가 관련 공문 안내드립니다. 11월 2일(월)까지 추천부탁드립니다.
1. 관련
가. 교육부 교육과정운영지원과-5058(2026. 10. 23.)
나. 중등교육과-20870(2026. 10. 27.)
2. 표창 계획을 다음과 같이 안내하오니, 기한 내 대상자를 적극 추천하여 주시기 바랍니다.
- 제출 기한: 2026. 11. 4.(수) 17:00 까지"""


class ReadableTests(unittest.TestCase):
    def test_inline_tags_do_not_split_words(self):
        html = '<div><font face="굴림">계획<span style="color:red">을</span> 다음과 <b>같이</b></font></div><div>대상자<span>를</span></div>'
        self.assertEqual(coolmsg.clean_body(comp(html)), "계획을 다음과 같이\n대상자를")
        self.assertEqual(docs.html_text("<table><tr><td>이름</td><td>반</td></tr></table>").split(), ["이름", "반"])

    def test_reference_documents_are_not_events(self):
        a = extract.analyze(NOTICE, ref=date(2026, 10, 28))
        self.assertEqual([(e["date"], e["kind"]) for e in a["events"]], [("2026-11-02", "deadline"), ("2026-11-04", "deadline")])
        self.assertFalse(any("5058" in s or s.endswith("관련") for s in a["summary"]))
        self.assertIn("교육과정운영지원과-5058", a["doc_numbers"])

    def test_cut_title_uses_first_line(self):
        self.assertEqual(cool_title("2026년 학생평가 및 학생부 유공 장관 표창 관련 공",
                                    "2026년 학생평가 및 학생부 유공 장관 표창 관련 공문 안내\n본문"),
                         "2026년 학생평가 및 학생부 유공 장관 표창 관련 공문 안내")
        self.assertEqual(cool_title("회의 안내", "다른 첫 줄\n본문"), "회의 안내")
        self.assertIsNone(cool_title("", "본문"))

    def test_old_items_are_recleaned_once(self):
        env = Env()
        local = Path(env.tmp.name) / "local"
        memo = local / "CoolMessenger" / "Memo"
        memo.mkdir(parents=True)
        recent = (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        body = comp("<div>표창 계획<span>을</span> 안내합니다</div><div>1. 관련</div>"
                    "<div>가. 중등교육과-20870(2026. 10. 27.)</div><div>- 제출 기한: 11월 4일까지</div>")
        make_udb(memo / "홍길동.udb", "a", [("교무부", "표창 계획을", body, recent)])
        env.config.values.update(coolmessenger_name="홍길동", coolmessenger_folder="", edufine_folder="")
        try:
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                path = coolmsg.resolve("홍길동")
                # 0.5.8 까지 정리된 모양 (빈칸으로 갈라진 낱말, 관련 공문 날짜가 일정 후보)
                old = "표창 계획 을  안내합니다\n1. 관련\n가. 중등교육과-20870(2026. 10. 27.)\n- 제출 기한: 11월 4일까지\n\n(보낸 사람: 교무부)"
                item = add_text_item(env.db, "cool", old, title="표창 계획을", received_at=recent, file_path=f"{path}#1")["id"]
                env.db.execute("INSERT INTO events(item_id, date, kind, category, text) VALUES(?, '2026-10-27', 'event', '참고', ?)",
                               (item, "가. 중등교육과-20870(2026. 10. 27.)"))   # 예전엔 관련 공문 날짜도 일정 후보였음
                env.db.set_meta(f"cool_last_rowid:{path}", 1)
                Collector(env.db, env.config).run("manual")
                it = env.db.item(item)
                self.assertIn("표창 계획을 안내합니다", it["body"])
                self.assertEqual(it["title"], "표창 계획을 안내합니다")
                self.assertEqual([e["date"][5:] for e in it["events"]], ["11-04"])           # 관련 공문 날짜 빠짐
                Collector(env.db, env.config).run("manual")                                   # 두 번째는 다시 하지 않음
                self.assertEqual(sum("다시 정리" in r["message"] for r in env.db.query("SELECT message FROM logs")), 1)
        finally:
            env.close()

    def test_reclean_failure_does_not_stop_new_memos(self):
        env = Env()
        local = Path(env.tmp.name) / "local"
        memo = local / "CoolMessenger" / "Memo"
        memo.mkdir(parents=True)
        recent = (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        make_udb(memo / "홍길동.udb", "a", [("교무부", "회의", comp("<div>11월 3일 회의</div>"), recent)])
        (env.edu / "공문.txt").write_text("11월 5일 행사", encoding="utf-8")
        env.config.values.update(coolmessenger_name="홍길동", coolmessenger_folder="")
        try:
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}), \
                    patch("desk.collect.reclean_cool_items", side_effect=RuntimeError("뜻밖")):
                s = Collector(env.db, env.config).run("manual")
            self.assertEqual(s["items"], 2, s)          # 새 쪽지 + 에듀파인 공문은 그대로 받음
            self.assertTrue(any("다시 정리 실패" in e for e in s["errors"]), s)
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
