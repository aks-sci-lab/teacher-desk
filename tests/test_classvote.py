"""학급 투표: 1인 1표, 비밀(누가 무엇을 골랐는지 안 남김), 투표 중 결과 숨김, 직접 입력, 동점 결선·제비뽑기 (0.5.81)."""
import json
import random

from desk import classvote
from tests.test_classroom_api import ApiBase


class VoteTests(ApiBase):
    def make(self, **kw):
        body = {"title": "반장 선거", "options": ["가짜1", "가짜2", "가짜3"], "mode": "secret", **kw}
        st, v = self.req("POST", "/api/class/votes", body)
        self.assertEqual(st, 200, v)
        return v

    def test_one_vote_per_student_and_results_hidden_while_open(self):
        v = self.make()
        self.assertEqual(self.req("POST", f"/api/class/votes/{v['id']}/ballot", {"number": 1, "choice": 0})[0], 200)
        st, r = self.req("POST", f"/api/class/votes/{v['id']}/ballot", {"number": 1, "choice": 1})
        self.assertEqual(st, 400)
        self.assertIn("이미 투표", r["error"])
        self.assertEqual(self.req("POST", f"/api/class/votes/{v['id']}/ballot", {"number": 99, "choice": 1})[0], 400)
        self.assertEqual(self.req("POST", f"/api/class/votes/{v['id']}/ballot", {"number": 2, "choice": 7})[0], 400)
        st, got = self.req("GET", f"/api/class/votes/{v['id']}")
        self.assertEqual(got["voters"], [1])
        self.assertNotIn("counts", got)          # 투표 중에는 몇 표인지 화면에 안 감
        st, got = self.req("GET", f"/api/class/votes/{v['id']}?reveal=1")
        self.assertEqual(got["counts"], [1, 0, 0])

    def test_secret_no_link_between_voter_and_choice(self):
        v = self.make()
        for n, c in ((3, 2), (4, 0), (5, 2)):
            self.req("POST", f"/api/class/votes/{v['id']}/ballot", {"number": n, "choice": c})
        cols = {r[1] for r in self.env.db.conn.execute("PRAGMA table_info(class_vote_voters)")}
        self.assertEqual(cols, {"vote_id", "number"})   # 고른 것을 적는 칸이 없음
        st, r = self.req("POST", f"/api/class/votes/{v['id']}/close", {})
        self.assertEqual(r["result"]["winners"], ["가짜3"])
        self.assertEqual(r["result"]["total"], 3)
        self.assertEqual(self.req("POST", f"/api/class/votes/{v['id']}/ballot", {"number": 6, "choice": 0})[0], 400)
        self.assertEqual(self.req("POST", f"/api/class/votes/{v['id']}/reopen", {})[1]["status"], "open")
        self.assertEqual(self.req("POST", f"/api/class/votes/{v['id']}/ballot", {"number": 6, "choice": 0})[0], 200)

    def test_manual_tally_tie_runoff_and_draw(self):
        v = self.make(mode="manual", abstain=True)
        self.assertEqual(len(v["options"]), 3)
        self.assertEqual(self.req("POST", f"/api/class/votes/{v['id']}/tally", {"counts": [1, 2]})[0], 400)
        st, r = self.req("POST", f"/api/class/votes/{v['id']}/tally", {"counts": [5, 5, 1, 9]})   # 기권 9표는 1등이 아님
        self.assertTrue(r["result"]["tie"])
        self.assertEqual(r["result"]["winners"], ["가짜1", "가짜2"])
        st, ro = self.req("POST", f"/api/class/votes/{v['id']}/runoff", {})
        self.assertEqual(ro["options"], ["가짜1", "가짜2"])
        self.assertEqual(ro["parent_id"], v["id"])
        st, d = self.req("POST", f"/api/class/votes/{v['id']}/draw", {})
        self.assertIn(d["drawn"]["winner"], ["가짜1", "가짜2"])

    def test_edit_only_before_votes_and_reset(self):
        v = self.make()
        st, e = self.req("PATCH", f"/api/class/votes/{v['id']}", {"options": ["가", "나"]})
        self.assertEqual(e["options"], ["가", "나"])
        self.req("POST", f"/api/class/votes/{v['id']}/ballot", {"number": 1, "choice": 1})
        self.assertEqual(self.req("PATCH", f"/api/class/votes/{v['id']}", {"options": ["다", "라"]})[0], 400)
        st, r = self.req("POST", f"/api/class/votes/{v['id']}/reset", {})
        self.assertEqual(r["voters"], [])
        self.assertEqual(self.req("POST", f"/api/class/votes/{v['id']}/ballot", {"number": 1, "choice": 0})[0], 200)
        self.assertEqual(self.req("POST", "/api/class/votes", {"title": "x", "options": ["하나"]})[0], 400)
        self.assertEqual(self.req("DELETE", f"/api/class/votes/{v['id']}")[0], 200)
        self.assertEqual(self.req("GET", f"/api/class/votes/{v['id']}")[0], 400)

    def test_result_counts(self):
        v = {"options": ["a", "b"], "counts": [0, 0], "abstain": False}
        self.assertEqual(classvote.result(v)["winners"], [])
        v["counts"] = [3, 1]
        r = classvote.result(v)
        self.assertEqual((r["winners"], r["rows"][0]["pct"]), (["a"], 75))
