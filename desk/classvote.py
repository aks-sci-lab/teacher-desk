"""학급 투표 (0.5.81): 교실 PC 화면(TV)에서 학생이 한 명씩 나와 비밀 투표, 또는 손들기·종이 결과 직접 입력.

비밀 투표를 지키려고 '누가 투표했는지'(class_vote_voters: 번호만)와 '무엇이 몇 표인지'(class_votes.counts)를
따로 저장하고, 한 표마다의 기록(누가 무엇을)은 남기지 않습니다. 인터넷·학생 폰 접속 없음, 이 PC 의 desk.db 에만.
"""

import json
import random

from .classroom import ClassError, need, numbers_of, roster, _text
from .db import now_str

MAX_OPTIONS = 30
MODES = ("secret", "manual")   # 교실 화면 비밀 투표 / 손들기·종이 결과 입력


def _row(db, vid):
    v = db.one("SELECT * FROM class_votes WHERE id = ?", (int(vid),))
    need(v, "없는 투표입니다")
    v["options"] = json.loads(v["options"])
    v["counts"] = json.loads(v["counts"])
    v["abstain"] = bool(v["abstain"])
    v["drawn"] = json.loads(v["drawn"]) if v.get("drawn") else None
    return v


def _voters(db, vid):
    return [r["number"] for r in db.query("SELECT number FROM class_vote_voters WHERE vote_id = ? ORDER BY number", (vid,))]


def clean_options(v):
    need(isinstance(v, list), "선택지 목록이 잘못되었습니다")
    out = []
    for o in v:
        o = _text(o, "선택지", 30)
        need(o not in out, f"'{o}' 이(가) 두 번 있습니다")
        out.append(o)
    need(2 <= len(out) <= MAX_OPTIONS, f"선택지는 2~{MAX_OPTIONS}개여야 합니다")
    return out


def result(v):
    """득표 결과: 1등(동점이면 여럿), 막대용 비율. 기권은 1등 계산에서 뺌."""
    counts = v["counts"]
    opts = v["options"]
    total = sum(counts)
    top = max(counts[:len(opts)]) if opts else 0
    winners = [o for o, c in zip(opts, counts) if c == top and top > 0]
    return {"total": total, "winners": winners, "tie": len(winners) > 1,
            "rows": [{"option": o, "count": c, "pct": round(100 * c / total) if total else 0}
                     for o, c in zip(opts + (["기권"] if v["abstain"] else []), counts)]}


def view(db, vid, reveal=False):
    """투표 중에는 몇 표인지 숨김 (결과는 끝낸 뒤나 선생님이 reveal 할 때만)."""
    v = _row(db, vid)
    voters = _voters(db, v["id"])
    out = {k: v[k] for k in ("id", "title", "mode", "options", "abstain", "status", "created_at", "closed_at", "drawn", "parent_id")}
    out["voters"] = voters
    out["eligible"] = len(numbers_of(db))
    if v["status"] == "closed" or reveal:
        out["counts"] = v["counts"]
        out["result"] = result(v)
    return out


def create(db, body):
    title = _text(body.get("title"), "투표 제목", 60)
    options = clean_options(body.get("options"))
    mode = body.get("mode", "secret")
    need(mode in MODES, "투표 방식이 잘못되었습니다")
    abstain = bool(body.get("abstain"))
    parent = body.get("parent_id")
    need(parent is None or isinstance(parent, int), "투표 자료가 잘못되었습니다")
    counts = [0] * (len(options) + (1 if abstain else 0))
    vid = db.execute("INSERT INTO class_votes(title, mode, options, abstain, counts, status, created_at, parent_id) "
                     "VALUES(?, ?, ?, ?, ?, 'open', ?, ?)",
                     (title, mode, json.dumps(options, ensure_ascii=False), int(abstain), json.dumps(counts), now_str(), parent))
    return view(db, vid)


def update(db, vid, body):
    """제목·선택지 고치기: 아직 아무도 투표하지 않았을 때만."""
    v = _row(db, vid)
    need(v["status"] == "open" and not _voters(db, v["id"]) and not any(v["counts"]), "투표가 시작된 뒤에는 선택지를 고칠 수 없습니다 (처음부터 다시 하기)")
    title = _text(body.get("title", v["title"]), "투표 제목", 60)
    options = clean_options(body.get("options", v["options"]))
    abstain = bool(body.get("abstain", v["abstain"]))
    mode = body.get("mode", v["mode"])
    need(mode in MODES, "투표 방식이 잘못되었습니다")
    db.execute("UPDATE class_votes SET title = ?, options = ?, abstain = ?, mode = ?, counts = ? WHERE id = ?",
               (title, json.dumps(options, ensure_ascii=False), int(abstain), mode,
                json.dumps([0] * (len(options) + int(abstain))), v["id"]))
    return view(db, v["id"])


def ballot(db, vid, body):
    """한 학생의 한 표. 번호는 명단에 있어야 하고 한 번만. 무엇을 골랐는지는 번호와 함께 남기지 않음."""
    number, choice = body.get("number"), body.get("choice")
    need(isinstance(number, int) and not isinstance(number, bool), "번호가 잘못되었습니다")
    need(number in set(numbers_of(db)), "학생 명단에 없는 번호입니다")
    with db.lock:
        v = _row(db, vid)
        need(v["status"] == "open" and v["mode"] == "secret", "지금 투표를 받는 중이 아닙니다")
        need(isinstance(choice, int) and not isinstance(choice, bool) and 0 <= choice < len(v["counts"]), "선택이 잘못되었습니다")
        if db.one("SELECT 1 AS x FROM class_vote_voters WHERE vote_id = ? AND number = ?", (v["id"], number)):
            raise ClassError(f"{number}번은 이미 투표했습니다")
        counts = v["counts"]
        counts[choice] += 1
        with db.conn:
            db.conn.execute("INSERT INTO class_vote_voters(vote_id, number) VALUES(?, ?)", (v["id"], number))
            db.conn.execute("UPDATE class_votes SET counts = ? WHERE id = ?", (json.dumps(counts), v["id"]))
    return {"ok": True, "voted": len(_voters(db, v["id"])), "eligible": len(numbers_of(db))}


def tally(db, vid, body):
    """손들기·종이 투표 결과를 직접 넣고 끝냄."""
    v = _row(db, vid)
    counts = body.get("counts")
    need(isinstance(counts, list) and len(counts) == len(v["counts"])
         and all(isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 1000 for c in counts), "표 수가 잘못되었습니다")
    db.execute("UPDATE class_votes SET counts = ?, status = 'closed', closed_at = ?, mode = 'manual' WHERE id = ?",
               (json.dumps(counts), now_str(), v["id"]))
    db.execute("DELETE FROM class_vote_voters WHERE vote_id = ?", (v["id"],))
    return view(db, v["id"])


def close(db, vid, body):
    v = _row(db, vid)
    db.execute("UPDATE class_votes SET status = 'closed', closed_at = ? WHERE id = ?", (now_str(), v["id"]))
    return view(db, v["id"])


def reopen(db, vid, body):
    """끝낸 투표를 다시 받기 (빠진 학생이 늦게 투표할 때). 결과는 그대로 이어서."""
    v = _row(db, vid)
    need(v["mode"] == "secret", "직접 입력한 투표는 다시 열 수 없습니다 (표 수를 다시 넣으세요)")
    db.execute("UPDATE class_votes SET status = 'open', closed_at = NULL, drawn = NULL WHERE id = ?", (v["id"],))
    return view(db, v["id"])


def reset(db, vid, body):
    """처음부터 다시: 모든 표와 투표한 학생 목록을 지움."""
    v = _row(db, vid)
    with db.lock, db.conn:
        db.conn.execute("DELETE FROM class_vote_voters WHERE vote_id = ?", (v["id"],))
        db.conn.execute("UPDATE class_votes SET counts = ?, status = 'open', closed_at = NULL, drawn = NULL WHERE id = ?",
                        (json.dumps([0] * len(v["counts"])), v["id"]))
    return view(db, v["id"])


def runoff(db, vid, body):
    """동점이면 동점인 선택지만으로 새 투표 (결선)."""
    v = _row(db, vid)
    r = result(v)
    need(v["status"] == "closed" and r["tie"], "동점일 때만 결선 투표를 만들 수 있습니다")
    return create(db, {"title": f"{v['title']} (결선)"[:60], "options": r["winners"], "mode": v["mode"],
                       "abstain": v["abstain"], "parent_id": v["id"]})


def draw(db, vid, body, rng=None):
    """동점이면 그중 하나를 제비뽑기로 (뽑은 결과를 남김)."""
    v = _row(db, vid)
    r = result(v)
    need(v["status"] == "closed" and r["tie"], "동점일 때만 제비뽑기를 할 수 있습니다")
    pick = (rng or random.SystemRandom()).choice(r["winners"])
    db.execute("UPDATE class_votes SET drawn = ? WHERE id = ?",
               (json.dumps({"winner": pick, "from": r["winners"], "at": now_str()}, ensure_ascii=False), v["id"]))
    return view(db, v["id"])


def delete(db, vid, body):
    v = _row(db, vid)
    with db.lock, db.conn:
        db.conn.execute("DELETE FROM class_vote_voters WHERE vote_id = ?", (v["id"],))
        db.conn.execute("DELETE FROM class_votes WHERE id = ?", (v["id"],))
    return {"ok": True}


def listing(db):
    rows = db.query("SELECT id, title, mode, status, created_at, closed_at FROM class_votes ORDER BY id DESC LIMIT 100")
    for r in rows:
        r["voted"] = db.one("SELECT COUNT(*) AS n FROM class_vote_voters WHERE vote_id = ?", (r["id"],))["n"]
    return rows


# ---------------------------------------------------------------- API (classroom.ROUTES 에 붙음)
ROUTES = [
    ("GET", "/api/class/votes", lambda db, q, b: {"votes": listing(db), "students": roster(db)}),
    ("POST", "/api/class/votes", lambda db, q, b: create(db, b)),
    ("GET", r"/api/class/votes/(\d+)", lambda db, q, b, vid: {**view(db, vid, q.get("reveal") == "1"), "students": roster(db)}),
    ("PATCH", r"/api/class/votes/(\d+)", lambda db, q, b, vid: update(db, vid, b)),
    ("DELETE", r"/api/class/votes/(\d+)", lambda db, q, b, vid: delete(db, vid, b)),
    ("POST", r"/api/class/votes/(\d+)/ballot", lambda db, q, b, vid: ballot(db, vid, b)),
    ("POST", r"/api/class/votes/(\d+)/tally", lambda db, q, b, vid: tally(db, vid, b)),
    ("POST", r"/api/class/votes/(\d+)/close", lambda db, q, b, vid: close(db, vid, b)),
    ("POST", r"/api/class/votes/(\d+)/reopen", lambda db, q, b, vid: reopen(db, vid, b)),
    ("POST", r"/api/class/votes/(\d+)/reset", lambda db, q, b, vid: reset(db, vid, b)),
    ("POST", r"/api/class/votes/(\d+)/runoff", lambda db, q, b, vid: runoff(db, vid, b)),
    ("POST", r"/api/class/votes/(\d+)/draw", lambda db, q, b, vid: draw(db, vid, b)),
]
