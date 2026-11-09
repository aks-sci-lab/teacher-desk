"""학급 경영: 좌석 배치도, 청소 구역·1인 1역(돌리기), 주번·일일 당번, 모둠 나누기 (0.5.80).

- 자료는 이 PC 의 data/desk.db 의 class_data(지금 것)·class_history(예전 배치) 표에만 둡니다.
  검색·AI·텔레그램·수집에는 쓰지 않습니다 (화면에서만).
- 학생은 학생 명단(students)의 **번호**로 연결합니다. 명단을 통째로 바꾸면 행 id 가 바뀌기 때문입니다.
  명단에서 빠진 번호는 저장할 때 지웁니다.
- 기록(로그)에는 학생 이름을 남기지 않습니다.
"""

import json
import math
import random
from datetime import date, timedelta

from . import absence, holidays
from .db import now_str

MAX_SIDE = 12          # 좌석 줄·칸 최대
HISTORY_KEEP = 30      # 좌석 기록 개수
GROUP_HISTORY_KEEP = 10
PERIOD_KEEP = 80       # 청소·1인 1역 지난 기간 수
ROT_KINDS = {"clean": "청소 구역", "role": "1인 1역"}
WD = "월화수목금토일"


class ClassError(ValueError):
    """화면에 그대로 보여 줄 잘못된 입력."""


def need(cond, message):
    if not cond:
        raise ClassError(message)


# ---------------------------------------------------------------- 저장
def get(db, kind, default=None):
    row = db.one("SELECT data FROM class_data WHERE kind = ?", (kind,))
    return json.loads(row["data"]) if row else default


def put(db, kind, data):
    db.execute("INSERT INTO class_data(kind, data, updated_at) VALUES(?, ?, ?) "
               "ON CONFLICT(kind) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
               (kind, json.dumps(data, ensure_ascii=False), now_str()))
    return data


def history_add(db, kind, label, data, keep):
    with db.lock, db.conn:
        db.conn.execute("INSERT INTO class_history(kind, label, data, created_at) VALUES(?, ?, ?, ?)",
                        (kind, label[:40], json.dumps(data, ensure_ascii=False), now_str()))
        db.conn.execute("DELETE FROM class_history WHERE kind = ? AND id NOT IN "
                        "(SELECT id FROM class_history WHERE kind = ? ORDER BY id DESC LIMIT ?)", (kind, kind, keep))


def history_list(db, kind):
    return db.query("SELECT id, label, created_at FROM class_history WHERE kind = ? ORDER BY id DESC", (kind,))


def history_get(db, kind, hid):
    row = db.one("SELECT * FROM class_history WHERE kind = ? AND id = ?", (kind, hid))
    need(row, "없는 기록입니다")
    return {**row, "data": json.loads(row["data"])}


def roster(db):
    return [{"number": r["number"], "name": r["name"]} for r in db.students()]


def numbers_of(db):
    return [r["number"] for r in roster(db)]


# ---------------------------------------------------------------- 입력 검사 도우미
def _int(v, name, lo, hi):
    need(isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi, f"{name}은(는) {lo}~{hi} 사이 숫자여야 합니다")
    return v


def _nums(v, valid, name="학생 번호", limit=100):
    """번호 목록 → 명단에 있는 것만, 겹치지 않게."""
    if v in (None, ""):
        return []
    need(isinstance(v, list) and len(v) <= limit, f"{name} 목록이 잘못되었습니다")
    out = []
    for n in v:
        need(isinstance(n, int) and not isinstance(n, bool), f"{name}이(가) 잘못되었습니다")
        if n in valid and n not in out:
            out.append(n)
    return out


def _pairs(v, valid):
    """[[1, 2], ...] 두 학생 묶음 (떼어 앉히기·떼어 놓기)."""
    if v in (None, ""):
        return []
    need(isinstance(v, list) and len(v) <= 60, "떼어 놓을 학생 목록이 잘못되었습니다")
    out = []
    for p in v:
        need(isinstance(p, list) and len(p) == 2 and all(isinstance(n, int) for n in p), "떼어 놓을 학생은 두 명씩입니다")
        a, b = p
        if a != b and a in valid and b in valid and sorted(p) not in out:
            out.append(sorted(p))
    return out


def _text(v, name, limit, required=True):
    need(isinstance(v, str), f"{name}은(는) 글자여야 합니다")
    v = v.strip()
    need(v or not required, f"{name}을(를) 입력하세요")
    need(len(v) <= limit, f"{name}이(가) 너무 깁니다 ({limit}자 이하)")
    return v


def _date(v, name="날짜"):
    try:
        return date.fromisoformat(v).isoformat()
    except (TypeError, ValueError):
        raise ClassError(f"{name} 형식이 잘못되었습니다 (예: 2026-11-09)")


# ---------------------------------------------------------------- 좌석 배치도
# 칸 번호는 '학생 쪽에서 칠판을 바라본' 방향 기준: 0 줄이 칠판에 가장 가까운 앞줄, 0 칸이 왼쪽.
# 교사 쪽 보기(칠판이 아래)는 화면에서 180도 돌려 그림.
def default_seats():
    return {"rows": 5, "cols": 6, "pair": True, "cells": [None] * 30, "off": [], "locked": [],
            "front": [], "front_rows": 2, "apart": [], "avoid_mates": True, "view": "student"}


def clean_seats(body, valid):
    s = default_seats()
    rows = _int(body.get("rows", s["rows"]), "줄 수", 1, MAX_SIDE)
    cols = _int(body.get("cols", s["cols"]), "칸 수", 1, MAX_SIDE)
    size = rows * cols
    cells = body.get("cells")
    if cells is None:
        cells = [None] * size
    need(isinstance(cells, list) and len(cells) == size, "자리 배치 자료가 잘못되었습니다 (줄×칸 수와 맞지 않음)")
    seen, out = set(), []
    for n in cells:
        need(n is None or (isinstance(n, int) and not isinstance(n, bool)), "자리 배치 자료가 잘못되었습니다")
        if n is not None and n in valid and n not in seen:
            seen.add(n)
            out.append(n)
        else:
            out.append(None)

    def idx_list(v, name):
        if v in (None, ""):
            return []
        need(isinstance(v, list) and len(v) <= size, f"{name} 자료가 잘못되었습니다")
        return sorted({i for i in v if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < size})

    off = idx_list(body.get("off"), "빈 칸")
    for i in off:
        out[i] = None   # 책상이 없는 칸에는 앉을 수 없음
    locked = [i for i in idx_list(body.get("locked"), "고정 자리") if i not in off and out[i] is not None]
    view = body.get("view", "student")
    need(view in ("student", "teacher"), "보기 방향이 잘못되었습니다")
    return {"rows": rows, "cols": cols, "pair": bool(body.get("pair", True)), "cells": out, "off": off, "locked": locked,
            "front": _nums(body.get("front"), valid), "front_rows": min(_int(body.get("front_rows", 2), "앞자리 줄 수", 1, MAX_SIDE), rows),
            "apart": _pairs(body.get("apart"), valid), "avoid_mates": bool(body.get("avoid_mates", True)), "view": view}


def mates_of(i, rows, cols, pair):
    """짝 자리: 분단(2칸씩 붙임)이면 같은 분단 옆 칸, 아니면 바로 왼쪽·오른쪽."""
    r, c = divmod(i, cols)
    if pair:
        m = c ^ 1
        return [r * cols + m] if m < cols else []
    return [r * cols + x for x in (c - 1, c + 1) if 0 <= x < cols]


def near_of(i, rows, cols):
    """붙어 앉은 자리: 앞뒤·옆·대각선."""
    r, c = divmod(i, cols)
    return [(r + dr) * cols + c + dc for dr in (-1, 0, 1) for dc in (-1, 0, 1)
            if (dr or dc) and 0 <= r + dr < rows and 0 <= c + dc < cols]


def mate_pairs(seats):
    """이 배치에서 짝이 된 두 학생 묶음들."""
    rows, cols, cells = seats["rows"], seats["cols"], seats["cells"]
    out = set()
    for i, n in enumerate(cells):
        if n is None:
            continue
        for j in mates_of(i, rows, cols, seats.get("pair", True)):
            if j < len(cells) and cells[j] is not None:
                out.add(tuple(sorted((n, cells[j]))))
    return out


def seat_problems(seats, prev_mates=()):
    """조건을 못 맞춘 곳 [(종류, 번호들)]. 종류: front·apart·mate."""
    rows, cols, cells = seats["rows"], seats["cols"], seats["cells"]
    where = {n: i for i, n in enumerate(cells) if n is not None}
    out = []
    for n in seats.get("front", []):
        if n in where and where[n] // cols >= seats.get("front_rows", 2):
            out.append(("front", [n]))
    for a, b in seats.get("apart", []):
        if a in where and b in where and where[b] in near_of(where[a], rows, cols):
            out.append(("apart", [a, b]))
    if seats.get("avoid_mates"):
        for p in mate_pairs(seats) & set(prev_mates):
            out.append(("mate", list(p)))
    return out


def shuffle_seats(seats, numbers, prev_mates=(), rng=None, tries=400):
    """무작위 배치. 🔒 고정 자리는 그대로, 앞자리 학생은 앞 n줄 안에 먼저 앉히고, 떼어 앉히기·짝 피하기를 맞춤.
    돌려줌: {cells, problems:[(종류, 번호들)], unplaced:[번호]}"""
    rng = rng or random.Random()
    rows, cols = seats["rows"], seats["cols"]
    off, locked = set(seats["off"]), set(seats["locked"])
    base = [seats["cells"][i] if i in locked else None for i in range(rows * cols)]
    placed = {n for n in base if n is not None}
    people = [n for n in numbers if n not in placed]
    free = [i for i in range(rows * cols) if i not in off and i not in locked]
    unplaced = []
    if len(people) > len(free):   # 자리가 모자라면 번호 뒤쪽 학생은 '자리 없음'으로
        unplaced = people[len(free):]
        people = people[:len(free)]
    front = [n for n in seats.get("front", []) if n in people]
    front_cells = [i for i in free if i // cols < seats.get("front_rows", 2)]
    prev = set(prev_mates) if seats.get("avoid_mates") else set()

    def cost(cells):
        trial = {**seats, "cells": cells}
        return sum({"front": 5, "apart": 3, "mate": 1}[k] for k, _ in seat_problems(trial, prev))

    best, best_cost = None, None
    for _ in range(tries):
        cells = list(base)
        fc = front_cells[:]
        rng.shuffle(fc)
        rest_people = [n for n in people if n not in front]
        for n in front:
            if fc:
                cells[fc.pop()] = n
            else:
                rest_people.append(n)
        empty = [i for i in free if cells[i] is None]
        rng.shuffle(empty)
        rng.shuffle(rest_people)
        for n, i in zip(rest_people, empty):
            cells[i] = n
        c = cost(cells)
        # 조금씩 자리를 바꿔 보며 더 나아지면 받아들임 (고정·책상 없는 칸은 건드리지 않음)
        for _ in range(60 if c else 0):
            a, b = rng.sample(free, 2) if len(free) >= 2 else (None, None)
            if a is None:
                break
            cells[a], cells[b] = cells[b], cells[a]
            nc = cost(cells)
            if nc <= c:
                c = nc
                if not c:
                    break
            else:
                cells[a], cells[b] = cells[b], cells[a]
        if best_cost is None or c < best_cost:
            best, best_cost = cells, c
        if not c:
            break
    result = {**seats, "cells": best or base}
    return {"cells": result["cells"], "problems": seat_problems(result, prev), "unplaced": unplaced}


def seats_state(db):
    valid = set(numbers_of(db))
    s = get(db, "seats") or default_seats()
    s = clean_seats(s, valid)   # 명단에서 빠진 번호는 지움
    return {"seats": s, "history": history_list(db, "seats")}


def _last_mates(db):
    """바로 전 기록(무작위 적용 직전 배치)의 짝들 + 지금 배치의 짝들."""
    out = set()
    cur = get(db, "seats")
    if cur:
        out |= mate_pairs({**default_seats(), **cur})
    row = db.one("SELECT data FROM class_history WHERE kind = 'seats' ORDER BY id DESC LIMIT 1")
    if row:
        out |= mate_pairs({**default_seats(), **json.loads(row["data"])})
    return out


# ---------------------------------------------------------------- 돌리기 (청소 구역·1인 1역)
def period_key(period, d):
    if period == "month":
        return d.strftime("%Y-%m")
    return (d - timedelta(days=d.weekday())).isoformat()


def period_label(period, key):
    if period == "month":
        y, m = key.split("-")
        return f"{y}년 {int(m)}월"
    mon = date.fromisoformat(key)
    fri = mon + timedelta(days=4)
    return f"{mon.month}/{mon.day}(월) ~ {fri.month}/{fri.day}(금)"


def default_rot(kind):
    if kind == "clean":
        items = [{"name": "교실 바닥", "count": 4}, {"name": "칠판·분필", "count": 2}, {"name": "복도", "count": 3},
                 {"name": "창틀·사물함", "count": 2}, {"name": "분리수거", "count": 2}]
        return {"items": items, "period": "week", "exclude": []}
    return {"items": [], "period": "month", "exclude": []}


def clean_rot(body, valid):
    items = body.get("items")
    need(isinstance(items, list) and len(items) <= 60, "역할 목록이 잘못되었습니다 (60개 이하)")
    out, names = [], set()
    for it in items:
        need(isinstance(it, dict), "역할 목록이 잘못되었습니다")
        name = _text(it.get("name"), "역할(구역) 이름", 30)
        need(name not in names, f"'{name}' 이(가) 두 번 있습니다")
        names.add(name)
        out.append({"name": name, "count": _int(it.get("count", 1), "인원", 1, 40)})
    period = body.get("period", "week")
    need(period in ("week", "month"), "돌리는 주기는 매주 또는 매월입니다")
    return {"items": out, "period": period, "exclude": _nums(body.get("exclude"), valid)}


def rotate(items, students, past, rng=None):
    """한 기간의 배정 {역할: [번호]}. past 는 지난 기간 배정들(오래된 것 → 최근).
    - 한 학생은 한 기간에 한 역할만.
    - 바로 전 기간과 같은 역할은 피하고, 그 역할을 덜 해 본 학생·전체로 덜 맡은 학생을 먼저.
    - 자리가 학생보다 많으면 남는 자리는 비워 두고, 적으면 지난번 쉰 학생이 먼저 맡음."""
    rng = rng or random.Random()
    counts = {s: {} for s in students}
    load = {s: 0 for s in students}
    last = {}
    for n, assign in enumerate(past):
        for item, nums in assign.items():
            for s in nums:
                if s in counts:
                    counts[s][item] = counts[s].get(item, 0) + 1
                    load[s] += 1
                    if n == len(past) - 1:
                        last[s] = item
    rested = {s for s in students if past and s not in last}   # 바로 전 기간에 아무것도 안 맡은 학생
    slots = [it["name"] for it in items for _ in range(it["count"])]
    rng.shuffle(slots)
    # 인원이 적은 역할부터 채워야 '같은 역할 피하기'를 맞추기 쉬움
    size = {it["name"]: it["count"] for it in items}
    slots.sort(key=lambda name: size[name])
    free = list(students)
    out = {it["name"]: [] for it in items}
    for name in slots:
        if not free:
            break
        pick = min(free, key=lambda s: (last.get(s) == name, s not in rested, counts[s].get(name, 0),
                                        load[s], rng.random()))
        out[name].append(pick)
        free.remove(pick)
    _fix_repeats(out, last, counts, free, rng)
    for name in out:
        out[name].sort()
    return out


def _fix_repeats(out, last, counts, free, rng):
    """차례로 고르다 보면 끝에 '지난번과 같은 역할'만 남을 수 있어, 그런 학생은 다른 역할·쉬는 학생과 맞바꿈."""
    for _ in range(200):
        bad = [(name, s) for name, nums in out.items() for s in nums if last.get(s) == name]
        if not bad:
            return
        name, s = bad[0]
        options = [(other, t) for other, nums in out.items() if other != name for t in nums
                   if last.get(t) != name and last.get(s) != other]
        options += [(None, t) for t in free if last.get(t) != name]
        if not options:
            return
        other, t = min(options, key=lambda o: (counts[o[1]].get(name, 0) + (counts[s].get(o[0], 0) if o[0] else 0),
                                               rng.random()))
        out[name][out[name].index(s)] = t
        if other is None:
            free[free.index(t)] = s
        else:
            out[other][out[other].index(t)] = s


def _rot_store(db, kind):
    return get(db, f"rot_{kind}") or {"periods": {}}


def _rot_people(db, cfg):
    ex = set(cfg.get("exclude", []))
    return [n for n in numbers_of(db) if n not in ex]


def rot_state(db, kind, today=None, rng=None):
    """지금 기간의 배정. 새 기간이 되었으면 자동으로 돌려 저장."""
    need(kind in ROT_KINDS, "없는 역할 종류입니다")
    today = today or date.today()
    cfg = get(db, f"cfg_{kind}") or default_rot(kind)
    store = _rot_store(db, kind)
    key = period_key(cfg["period"], today)
    people = _rot_people(db, cfg)
    if key not in store["periods"] and cfg["items"] and people:
        past = [store["periods"][k]["assign"] for k in sorted(store["periods"]) if k < key and _key_fits(cfg["period"], k)]
        store["periods"][key] = {"assign": rotate(cfg["items"], people, past, rng), "auto": True, "at": now_str()}
        _rot_save(db, kind, store)
    cur = store["periods"].get(key)
    keys = sorted(k for k in store["periods"] if k != key)
    valid = set(numbers_of(db))
    if cur:
        cur = {**cur, "assign": {k: [n for n in v if n in valid] for k, v in cur["assign"].items()}}
    return {"kind": kind, "name": ROT_KINDS[kind], "config": cfg, "key": key, "label": period_label(cfg["period"], key),
            "current": cur,
            "past": [{"key": k, "label": period_label(cfg["period"], k) if _key_fits(cfg["period"], k) else k,
                      "assign": store["periods"][k]["assign"]} for k in reversed(keys)][:8]}


def _key_fits(period, key):
    return len(key) == (7 if period == "month" else 10)


def _rot_save(db, kind, store):
    keys = sorted(store["periods"])
    for k in keys[:-PERIOD_KEEP]:
        del store["periods"][k]
    put(db, f"rot_{kind}", store)


def rot_reshuffle(db, kind, today=None, rng=None):
    need(kind in ROT_KINDS, "없는 역할 종류입니다")
    today = today or date.today()
    cfg = get(db, f"cfg_{kind}") or default_rot(kind)
    need(cfg["items"], "먼저 역할(구역) 목록을 넣으세요")
    people = _rot_people(db, cfg)
    need(people, "학생 명단이 비어 있습니다")
    store = _rot_store(db, kind)
    key = period_key(cfg["period"], today)
    past = [store["periods"][k]["assign"] for k in sorted(store["periods"]) if k < key and _key_fits(cfg["period"], k)]
    store["periods"][key] = {"assign": rotate(cfg["items"], people, past, rng), "auto": False, "at": now_str()}
    _rot_save(db, kind, store)
    return rot_state(db, kind, today)


def rot_assign(db, kind, body, today=None):
    """이번 기간 배정을 선생님이 직접 고침: {assign: {역할: [번호]}}."""
    need(kind in ROT_KINDS, "없는 역할 종류입니다")
    today = today or date.today()
    cfg = get(db, f"cfg_{kind}") or default_rot(kind)
    valid = set(numbers_of(db))
    assign = body.get("assign")
    need(isinstance(assign, dict) and len(assign) <= 60, "배정 자료가 잘못되었습니다")
    names = {it["name"] for it in cfg["items"]}
    out, used = {}, set()
    for item, nums in assign.items():
        need(item in names, f"'{item}' 은(는) 역할 목록에 없습니다")
        clean = [n for n in _nums(nums, valid) if n not in used]
        used |= set(clean)
        out[item] = sorted(clean)
    store = _rot_store(db, kind)
    key = period_key(cfg["period"], today)
    store["periods"][key] = {"assign": out, "auto": False, "at": now_str()}
    _rot_save(db, kind, store)
    return rot_state(db, kind, today)


# ---------------------------------------------------------------- 주번·일일 당번 (번호 순서대로)
def default_duty():
    today = date.today().isoformat()
    return {"day": {"on": True, "per": 2, "start": today, "first": None},
            "week": {"on": False, "per": 2, "start": today, "first": None}, "exclude": []}


def clean_duty(body, valid):
    out = {"exclude": _nums(body.get("exclude"), valid)}
    for k in ("day", "week"):
        v = body.get(k) or {}
        need(isinstance(v, dict), "당번 설정이 잘못되었습니다")
        first = v.get("first")
        need(first is None or isinstance(first, int), "시작 번호가 잘못되었습니다")
        out[k] = {"on": bool(v.get("on")), "per": _int(v.get("per", 2), "당번 인원", 1, 10),
                  "start": _date(v.get("start") or date.today().isoformat(), "시작 날짜"), "first": first}
    return out


def is_school_day(d, closed=()):
    return d.weekday() < 5 and not holidays.holiday_name(d) and d not in closed


def _school_days_between(db, start, end):
    """start 이상 end 미만의 수업일 수 (주말·법정공휴일·학사일정의 휴업일 뺌)."""
    if end < start:
        return -_school_days_between(db, end, start)
    closed = absence.closed_days(db, start, end)
    return sum(1 for n in range((end - start).days) if is_school_day(start + timedelta(days=n), closed))


def _duty_order(db, cfg):
    ex = set(cfg.get("exclude", []))
    return [n for n in numbers_of(db) if n not in ex]


def duty_on(db, cfg, mode, d):
    """그 날(일일)·그 주(주번)의 당번 번호들. 쉬는 날이면 []."""
    c = cfg[mode]
    order = _duty_order(db, cfg)
    if not c["on"] or not order:
        return []
    start = date.fromisoformat(c["start"])
    if mode == "day":
        if not is_school_day(d, absence.closed_days(db, d, d)):
            return []
        steps = _school_days_between(db, start, d)
    else:
        steps = ((d - timedelta(days=d.weekday())) - (start - timedelta(days=start.weekday()))).days // 7
    first = order.index(c["first"]) if c["first"] in order else 0
    per = min(c["per"], len(order))
    return [order[(first + steps * per + k) % len(order)] for k in range(per)]


def duty_state(db, today=None):
    today = today or date.today()
    cfg = get(db, "duty")
    if cfg is None and numbers_of(db):   # 처음 본 날을 시작 날짜로 저장 (안 그러면 매일 1번부터 다시 시작)
        cfg = put(db, "duty", default_duty())
    cfg = clean_duty(cfg or default_duty(), set(numbers_of(db)))
    mon = today - timedelta(days=today.weekday())
    days = [mon + timedelta(days=n) for n in range(12) if (mon + timedelta(days=n)).weekday() < 5]   # 이번 주·다음 주
    return {"config": cfg, "today": today.isoformat(),
            "day_today": duty_on(db, cfg, "day", today),
            "days": [{"date": d.isoformat(), "label": f"{d.month}/{d.day}({WD[d.weekday()]})",
                      "nums": duty_on(db, cfg, "day", d),
                      "off": holidays.holiday_name(d) or ("" if is_school_day(d, absence.closed_days(db, d, d)) else "휴업일")}
                     for d in days],
            "weeks": [{"start": (mon + timedelta(days=7 * n)).isoformat(),
                       "label": period_label("week", (mon + timedelta(days=7 * n)).isoformat()),
                       "nums": duty_on(db, cfg, "week", mon + timedelta(days=7 * n))} for n in range(4)]}


# ---------------------------------------------------------------- 모둠 나누기
def default_groups():
    return {"mode": "count", "value": 6, "apart": [], "avoid_last": True, "exclude": [], "groups": [], "at": None}


def clean_group_cfg(body, valid):
    mode = body.get("mode", "count")
    need(mode in ("count", "size"), "나누는 방법은 모둠 수 또는 모둠 인원입니다")
    return {"mode": mode, "value": _int(body.get("value", 6), "모둠 수" if mode == "count" else "모둠 인원", 1, 40),
            "apart": _pairs(body.get("apart"), valid), "avoid_last": bool(body.get("avoid_last", True)),
            "exclude": _nums(body.get("exclude"), valid)}


def clean_groups(v, valid):
    need(isinstance(v, list) and len(v) <= 40, "모둠 자료가 잘못되었습니다")
    seen, out = set(), []
    for g in v:
        need(isinstance(g, list), "모둠 자료가 잘못되었습니다")
        clean = [n for n in _nums(g, valid) if n not in seen]
        seen |= set(clean)
        out.append(clean)
    return out


def make_groups(people, mode, value, apart=(), past=(), rng=None, tries=300):
    """모둠 나누기. 떼어 놓을 두 학생은 다른 모둠에, 지난 모둠에서 같이 있던 학생끼리는 덜 겹치게.
    돌려줌: (모둠들, 못 맞춘 떼어 놓기 묶음)"""
    rng = rng or random.Random()
    people = list(people)
    if not people:
        return [], []
    n = value if mode == "count" else math.ceil(len(people) / value)
    n = max(1, min(n, len(people)))
    sizes = [len(people) // n + (1 if k < len(people) % n else 0) for k in range(n)]
    together = {}
    for groups in past:
        for g in groups:
            for i, a in enumerate(g):
                for b in g[i + 1:]:
                    key = tuple(sorted((a, b)))
                    together[key] = together.get(key, 0) + 1
    apart = [tuple(p) for p in apart]

    def split(order):
        out, k = [], 0
        for s in sizes:
            out.append(sorted(order[k:k + s]))
            k += s
        return out

    def cost(groups):
        c = 0
        for g in groups:
            gs = set(g)
            c += sum(100 for a, b in apart if a in gs and b in gs)
            c += sum(together.get(tuple(sorted((a, b))), 0) for i, a in enumerate(g) for b in g[i + 1:])
        return c

    best, best_cost = None, None
    for _ in range(tries):
        order = people[:]
        rng.shuffle(order)
        groups = split(order)
        c = cost(groups)
        for _ in range(80 if c else 0):   # 두 모둠 사이에서 한 명씩 바꿔 보기
            if n < 2:
                break
            ga, gb = rng.sample(range(n), 2)
            ia, ib = rng.randrange(len(groups[ga])), rng.randrange(len(groups[gb]))
            groups[ga][ia], groups[gb][ib] = groups[gb][ib], groups[ga][ia]
            nc = cost(groups)
            if nc <= c:
                c = nc
            else:
                groups[ga][ia], groups[gb][ib] = groups[gb][ib], groups[ga][ia]
        if best_cost is None or c < best_cost:
            best, best_cost = [sorted(g) for g in groups], c
        if not c:
            break
    bad = [list(p) for p in apart if any(p[0] in g and p[1] in g for g in best)]
    return best, bad


def groups_state(db):
    valid = set(numbers_of(db))
    g = {**default_groups(), **(get(db, "groups") or {})}
    cfg = clean_group_cfg(g, valid)
    return {**cfg, "groups": clean_groups(g.get("groups") or [], valid), "at": g.get("at"),
            "history": history_list(db, "groups")}


def _past_groups(db, n=3):
    rows = db.query("SELECT data FROM class_history WHERE kind = 'groups' ORDER BY id DESC LIMIT ?", (n,))
    return [json.loads(r["data"]).get("groups", []) for r in rows]


# ---------------------------------------------------------------- 브리핑 한 줄
def brief(db, today=None):
    """브리핑 카드용: 오늘 당번·이번 주 주번·이번 기간 청소. 설정한 것만 (화면에서만 씀)."""
    today = today or date.today()
    names = {r["number"]: r["name"] for r in roster(db)}
    if not names:
        return None
    who = lambda nums: [{"number": n, "name": names.get(n, "")} for n in nums]
    out = {}
    duty = get(db, "duty")
    if duty:
        duty = clean_duty(duty, set(names))
        if duty["day"]["on"]:
            out["day"] = who(duty_on(db, duty, "day", today))
        if duty["week"]["on"]:
            out["week"] = who(duty_on(db, duty, "week", today))
    if get(db, "cfg_clean") or get(db, "rot_clean"):
        st = rot_state(db, "clean", today)
        if st["current"]:
            out["clean"] = {"label": st["label"],
                            "areas": [{"name": k, "students": who(v)} for k, v in st["current"]["assign"].items()]}
    return out or None


# ---------------------------------------------------------------- API (server.py 가 등록)
def _api_seats_get(db, q, body):
    return {**seats_state(db), "students": roster(db)}


def _api_seats_put(db, q, body):
    valid = set(numbers_of(db))
    new = clean_seats(body.get("seats") or {}, valid)
    label = body.get("record")
    if label:   # 바꾸기 전 배치를 기록으로 남김 (무작위 적용·기록 불러오기 때)
        old = get(db, "seats")
        if old and any(n is not None for n in old.get("cells", [])):
            history_add(db, "seats", _text(label, "기록 이름", 40), old, HISTORY_KEEP)
    put(db, "seats", new)
    return _api_seats_get(db, q, body)


def _api_seats_record(db, q, body):
    cur = get(db, "seats")
    need(cur and any(n is not None for n in cur.get("cells", [])), "남길 배치가 없습니다")
    history_add(db, "seats", _text(body.get("label") or "직접 남긴 기록", "기록 이름", 40), cur, HISTORY_KEEP)
    return _api_seats_get(db, q, body)


def _api_seats_shuffle(db, q, body):
    """무작위 배치 미리 보기 (저장은 화면에서 [적용] 때)."""
    numbers = numbers_of(db)
    need(numbers, "학생 명단이 비어 있습니다. 출결 메뉴의 학생 명단을 먼저 넣으세요")
    seats = clean_seats(body.get("seats") or {}, set(numbers))
    return shuffle_seats(seats, numbers, _last_mates(db))


def _api_seats_history(db, q, body, hid):
    h = history_get(db, "seats", int(hid))
    h["data"] = clean_seats({**default_seats(), **h["data"]}, set(numbers_of(db)))
    return h


def _api_seats_history_delete(db, q, body, hid):
    history_get(db, "seats", int(hid))
    db.execute("DELETE FROM class_history WHERE kind = 'seats' AND id = ?", (int(hid),))
    return {"ok": True}


def _api_rot_get(db, q, body, kind):
    return {**rot_state(db, kind), "students": roster(db)}


def _api_rot_config(db, q, body, kind):
    need(kind in ROT_KINDS, "없는 역할 종류입니다")
    cfg = clean_rot(body, set(numbers_of(db)))
    old = get(db, f"cfg_{kind}") or default_rot(kind)
    put(db, f"cfg_{kind}", cfg)
    if old.get("period") != cfg["period"]:   # 주기를 바꾸면 이번 기간을 새로 돌림
        store = _rot_store(db, kind)
        store["periods"].pop(period_key(cfg["period"], date.today()), None)
        _rot_save(db, kind, store)
    db.log(f"학급 {ROT_KINDS[kind]} 목록 {len(cfg['items'])}개 저장")
    return _api_rot_get(db, q, body, kind)


def _api_rot_shuffle(db, q, body, kind):
    rot_reshuffle(db, kind)
    return _api_rot_get(db, q, body, kind)


def _api_rot_assign(db, q, body, kind):
    rot_assign(db, kind, body)
    return _api_rot_get(db, q, body, kind)


def _api_duty_get(db, q, body):
    return {**duty_state(db), "students": roster(db)}


def _api_duty_put(db, q, body):
    put(db, "duty", clean_duty(body, set(numbers_of(db))))
    return _api_duty_get(db, q, body)


def _api_groups_get(db, q, body):
    return {**groups_state(db), "students": roster(db)}


def _api_groups_make(db, q, body):
    """설정을 저장하고 새로 나눔. 지금 모둠은 기록으로 남김."""
    valid = set(numbers_of(db))
    need(valid, "학생 명단이 비어 있습니다. 출결 메뉴의 학생 명단을 먼저 넣으세요")
    cfg = clean_group_cfg(body, valid)
    old = get(db, "groups") or {}
    if old.get("groups"):
        history_add(db, "groups", "모둠", {"groups": old["groups"]}, GROUP_HISTORY_KEEP)
    people = [n for n in numbers_of(db) if n not in set(cfg["exclude"])]
    past = (([old["groups"]] if old.get("groups") else []) + _past_groups(db)) if cfg["avoid_last"] else []
    groups, bad = make_groups(people, cfg["mode"], cfg["value"], cfg["apart"], past)
    put(db, "groups", {**cfg, "groups": groups, "at": now_str()})
    return {**_api_groups_get(db, q, body), "problems": bad}


def _api_groups_put(db, q, body):
    """끌어서 옮긴 모둠 저장 (설정은 그대로)."""
    valid = set(numbers_of(db))
    g = {**default_groups(), **(get(db, "groups") or {})}
    if "groups" in body:
        g["groups"] = clean_groups(body["groups"], valid)
    if "config" in body:
        need(isinstance(body["config"], dict), "모둠 설정이 잘못되었습니다")
        g.update(clean_group_cfg(body["config"], valid))
    put(db, "groups", g)
    return _api_groups_get(db, q, body)


def _api_groups_history(db, q, body, hid):
    return history_get(db, "groups", int(hid))


ROUTES = [
    ("GET", "/api/class/seats", _api_seats_get),
    ("PUT", "/api/class/seats", _api_seats_put),
    ("POST", "/api/class/seats/record", _api_seats_record),
    ("POST", "/api/class/seats/shuffle", _api_seats_shuffle),
    ("GET", r"/api/class/seats/history/(\d+)", _api_seats_history),
    ("DELETE", r"/api/class/seats/history/(\d+)", _api_seats_history_delete),
    ("GET", r"/api/class/rot/(clean|role)", _api_rot_get),
    ("PUT", r"/api/class/rot/(clean|role)/config", _api_rot_config),
    ("POST", r"/api/class/rot/(clean|role)/shuffle", _api_rot_shuffle),
    ("PUT", r"/api/class/rot/(clean|role)/assign", _api_rot_assign),
    ("GET", "/api/class/duty", _api_duty_get),
    ("PUT", "/api/class/duty", _api_duty_put),
    ("GET", "/api/class/groups", _api_groups_get),
    ("PUT", "/api/class/groups", _api_groups_put),
    ("POST", "/api/class/groups/make", _api_groups_make),
    ("GET", r"/api/class/groups/history/(\d+)", _api_groups_history),
]
