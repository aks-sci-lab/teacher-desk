"""내 시간표·수업 교체 후보 (사용자가 준 '시간표 교체 도우미'에서 담임 교사에게 쓸모 있는 것만 옮김).

- 학교 시간표 엑셀(.xlsx, 교사마다 몇 줄 · 요일 아래 교시 번호)을 읽어 교사별 수업을 만듦.
  배경색이 있는 칸·가로로 병합된 칸·고정 과목(자율·동아리·창체·봉사)은 '잠금' (바꿀 수 없음).
- 내 수업 하나를 고르고 날짜를 정하면, 그 주 안에서 바꿀 수 있는 자리를 찾음:
  맞교환(그 학급 수업을 가진 선생님과 서로 바꿈) · 2단계 교체(그 선생님의 다른 수업까지 한 번 더 바꿈).
  연속 수업이 기준(4교시) 이상으로 늘어나는 선생님이 있으면 경고, 학사일정상 수업이 없는 날·지난 날은 막음.
- 정한 교체는 날짜가 있는 기록으로 남겨 '오늘 내 수업'·텔레그램에 반영 (엑셀 파일은 고치지 않음).
"""

import re
from datetime import date, timedelta

from .xlsx import XlsxError, read

DAYNAMES = ["월", "화", "수", "목", "금", "토"]
LOCK_WORDS = ["자율", "동아리", "창체", "봉사"]
RUN_LIMIT = 4
P_CLS = re.compile(r"^\d{1,2}\s*-\s*\d{0,2}$")
# 이런 학사일정이 있는 날은 수업을 옮기지 않음 (원래 도구와 같은 낱말)
P_BLOCK = re.compile(r"공휴일|휴일|휴업|방학|어린이날|기념일|연휴|한글날|제헌절|성탄절|광복절|개천절|현충일|삼일절|선거|노동절|"
                     r"고사|수학능력|수능|수학여행|현장체험|방학식|졸업식|종업식|축제|체육|학력평가|모의평가|진단검사|창의체험|학술제|행사")
# 수업 자체가 없는 날 (학년이 안 적혀 있으면 전교). 나머지 P_BLOCK 낱말(행사·체육·고사…)은 일부 교시만 바뀔 수 있음
P_OFF = re.compile(r"공휴일|휴일|휴업|방학|어린이날|기념일|연휴|한글날|제헌절|성탄절|광복절|개천절|현충일|삼일절|선거|노동절|수학능력|수능")
P_GRADES = re.compile(r"(?:^|[\s(])([1-3](?:\s*[,.·]\s*[1-3])*)\s*학?년")


class TimetableError(Exception):
    pass


def _s(v):
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    return re.sub(r"\s+", " ", str(v or "")).strip()


def parse_sheet(sheet, lock_words=None):
    """시트 하나 → {days, slots, teachers:[{id, name, dept, lessons:{'요일-교시': 수업}}]}."""
    rows, merges, fills = sheet["rows"], sheet["merges"], sheet.get("fills") or {}
    lock_words = LOCK_WORDS if lock_words is None else lock_words
    nrows = len(rows)
    ncols = max((len(r) for r in rows), default=0)
    master = {}
    for r1, c1, r2, c2 in merges:
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                master[(r, c)] = (r1, c1)
    cell = lambda r, c: _s(rows[r][c]) if r < nrows and c < len(rows[r]) else ""
    mcell = lambda r, c: master.get((r, c), (r, c))

    # 1) 요일 머리행 (병합으로 비어 있는 칸은 앞 요일을 이어받음)
    day_row, day_of = -1, {}
    for r in range(min(30, nrows)):
        found, last, cnt = {}, None, 0
        for c in range(ncols):
            t = cell(r, c)
            d = next((i for i, x in enumerate(DAYNAMES) if t in (x, x + "요일")), -1)
            if d >= 0:
                if last != d:
                    cnt += 1
                last = d
            if last is not None and (d >= 0 or t == ""):
                found[c] = last
            elif t:
                last = None
        if cnt >= 3:
            day_row, day_of = r, found
            break
    if day_row < 0:
        raise TimetableError("요일(월·화·수·목·금)이 적힌 머리행을 찾지 못했습니다.")
    # 2) 교시 번호 행
    slots = []
    for r in range(day_row + 1, min(day_row + 5, nrows)):
        tmp = []
        for c in range(ncols):
            if c not in day_of:
                continue
            t = cell(r, c)
            if re.fullmatch(r"\d{1,2}", t) and 1 <= int(t) <= 12:
                tmp.append({"col": c, "d": day_of[c], "p": int(t)})
        if len(tmp) >= 8:
            slots, per_row = tmp, r
            break
    if not slots:
        raise TimetableError("요일 아래에서 교시 번호(1, 2, 3 …) 행을 찾지 못했습니다.")
    seen = set()
    slots = [s for s in slots if (s["d"], s["p"]) not in seen and not seen.add((s["d"], s["p"]))]
    for s in slots:
        s["key"] = f"{s['d']}-{s['p']}"
    first_col = min(s["col"] for s in slots)
    start = per_row + 1

    # 3) 교사 구분 열(병합 묶음이 가장 많은 열)·이름 열(글자가 든 칸이 가장 많은 열)
    is_num = lambda t: bool(re.fullmatch(r"[\d\s.\-]+", t))
    block_col, best, name_col, best_name = None, 0, None, 0
    for c in range(first_col):
        ms, named = set(), set()
        for r in range(start, nrows):
            m = mcell(r, c)
            t = cell(*m)
            if not t:
                continue
            ms.add(m)
            if not is_num(t) and re.search(r"[가-힣A-Za-z]", t):
                named.add(m)
        if len(ms) > best:
            best, block_col = len(ms), c
        if len(named) > best_name:
            best_name, name_col = len(named), c
    blocks, cur = [], None
    for r in range(start, nrows):
        key = None
        if block_col is not None:
            m = mcell(r, block_col)
            if cell(*m):
                key = m
        if key and (not cur or cur["key"] != key):
            cur = {"key": key, "rows": [r]}
            blocks.append(cur)
        elif cur:
            cur["rows"].append(r)
    if not blocks:
        blocks = [{"key": r, "rows": [x for x in (r, r + 1) if x < nrows]} for r in range(start, nrows, 2)]
    left = lambda r, c: cell(*mcell(r, c))
    for i, b in enumerate(blocks):
        name = next((left(r, name_col) for r in b["rows"] if name_col is not None and left(r, name_col)), "")
        name = name or (left(b["rows"][0], block_col) if block_col is not None else "") or f"교사 {i + 1}"
        dept = []
        for c in range(first_col):
            t = left(b["rows"][0], c)
            if c != name_col and t and t != name and not is_num(t) and t not in dept:
                dept.append(t)
        b["name"], b["dept"] = name, " ".join(dept)

    # 4) 수업 읽기
    teachers, lid = [], 0
    for b in blocks:
        lessons = {}
        for s in slots:
            texts, color, merged = [], None, False
            for r in b["rows"]:
                m = master.get((r, s["col"]))
                if m and m != (r, s["col"]):
                    if m[1] != s["col"]:          # 가로로 병합된 칸: 앞 칸의 수업이 이어짐
                        merged = True
                        if m[0] == r and cell(*m):
                            texts.append(cell(*m))
                            color = color or fills.get(m)
                    continue
                if m == (r, s["col"]) and master.get((r, s["col"] + 1)) == m:
                    merged = True
                t = cell(r, s["col"])
                if t:
                    texts.append(t)
                    color = color or fills.get((r, s["col"]))
            if not texts:
                continue
            cls, subj = "", []
            for t in texts:
                tt = t.replace(" ", "")
                if not cls and P_CLS.match(tt):
                    cls = tt
                else:
                    subj.append(t)
            subject = " ".join(subj)
            why = "배경색" if color else "병합 칸" if merged else "고정 과목" if any(k in subject for k in lock_words) else ""
            lessons[s["key"]] = {"id": lid, "cls": cls, "subj": subject, "locked": bool(why), "why": why, "color": color}
            lid += 1
        if lessons:
            teachers.append({"id": len(teachers), "name": b["name"], "dept": b["dept"], "lessons": lessons})
    if not teachers:
        raise TimetableError("교사별 수업을 읽지 못했습니다. 시트 구성을 확인해 주세요.")
    used = sorted({s["d"] for s in slots})
    slots.sort(key=lambda s: (s["d"], s["p"]))
    return {"days": DAYNAMES[:max(used) + 1], "slots": [{"key": s["key"], "d": s["d"], "p": s["p"]} for s in slots],
            "teachers": teachers}


def parse_file(data, sheet_name=None, lock_words=None):
    """엑셀 파일 → (시간표, 시트 이름들). 시트를 정하지 않으면 이름에 '시간표'가 든 시트, 없으면 읽히는 첫 시트."""
    try:
        sheets = read(data)
    except XlsxError as e:
        raise TimetableError(str(e))
    if not sheets:
        raise TimetableError("엑셀에 시트가 없습니다.")
    names = [s["name"] for s in sheets]
    if sheet_name:
        order = [s for s in sheets if s["name"] == sheet_name]
    else:
        order = sorted(sheets, key=lambda s: "시간표" not in (s["name"] or ""))
    err = None
    for sh in order:
        try:
            tt = parse_sheet(sh, lock_words)
            tt["sheet"] = sh["name"]
            return tt, names
        except TimetableError as e:
            err = e
    raise err or TimetableError("읽을 시트가 없습니다.")


# ---------------------------------------------------------------- 날짜·교체 기록
def monday(d):
    return d - timedelta(days=d.weekday())


def slot_date(key, mon):
    return mon + timedelta(days=int(key.split("-")[0]))


def week_state(tt, mon, swaps=()):
    """그 주의 교사별 수업 {tid: {key: 수업}} — 정해 둔 교체(날짜가 그 주인 것)를 반영."""
    cur = {t["id"]: dict(t["lessons"]) for t in tt["teachers"]}
    lessons = {l["id"]: l for t in tt["teachers"] for l in t["lessons"].values()}
    end = mon + timedelta(days=6)
    for sw in swaps:
        moves = [m for m in sw["moves"] if mon.isoformat() <= m["from_date"] <= end.isoformat()]
        for m in moves:
            if cur.get(m["tid"], {}).get(m["from"], {}).get("id") == m["lesson"]:
                cur[m["tid"]].pop(m["from"], None)
        for m in moves:
            if m["lesson"] in lessons and m["tid"] in cur:
                cur[m["tid"]][m["to"]] = {**lessons[m["lesson"]], "moved": True}
    return cur


def grade_of(cls):
    m = re.match(r"\s*(\d+)", cls or "")
    return int(m.group(1)) if m else None


def block_reason(day_events, d, grade, today):
    """이 날 이 학년 수업을 옮기면 안 되는 까닭 (지난 날·학사일정), 없으면 None."""
    if d < today:
        return "지난 날짜"
    for text in day_events.get(d.isoformat(), []):
        if not P_BLOCK.search(text):
            continue
        g = P_GRADES.search(text)
        grades = {int(x) for x in re.split(r"[,.·]", g.group(1))} if g else None
        if not grades or grade is None or grade in grades:
            return text
    return None


def day_note(day_events, d, grade):
    """그날 내 수업(grade 학년)에 대한 학사일정 판정 — 화면·텔레그램·수업 알림이 함께 씀.

    {'level': 'off', 'text'}   수업이 없는 날 (휴업·공휴일, 또는 그 학년이 적힌 행사)
    {'level': 'maybe', 'text'} 학년이 안 적힌 행사·체육·고사 등: 일부 교시가 바뀔 수 있어 확인 필요 (수업이 없다고 단정하지 않음)
    없으면 None (여러 개면 off 우선)."""
    maybe = None
    for text in day_events.get(d.isoformat(), []):
        if not P_BLOCK.search(text):
            continue
        g = P_GRADES.search(text)
        grades = {int(x) for x in re.split(r"[,.·]", g.group(1))} if g else None
        if grades and grade is not None and grade not in grades:
            continue   # 다른 학년 행사
        if P_OFF.search(text) or grades:
            return {"level": "off", "text": text}
        maybe = maybe or {"level": "maybe", "text": text}
    return maybe


def longest_run(day_lessons, periods):
    best = run = 0
    for p in range(1, periods + 1):
        run = run + 1 if day_lessons.get(p) else 0
        best = max(best, run)
    return best


def _day_map(state, d):
    return {int(k.split("-")[1]): l for k, l in state.items() if int(k.split("-")[0]) == d}


def find_candidates(tt, tid, key, when, swaps=(), day_events=None, today=None, two_step=True, limit=40):
    """내 수업(tid 선생님의 key 칸, 날짜 when)을 그 주 안에서 바꿀 후보."""
    today = today or date.today()
    day_events = day_events or {}
    mon = monday(when)
    cur = week_state(tt, mon, swaps)
    lesson = cur.get(tid, {}).get(key)
    if not lesson:
        raise TimetableError("그 시간에 수업이 없습니다.")
    if lesson["locked"]:
        raise TimetableError(f"잠긴 수업입니다 ({lesson['why']}). 바꿀 수 없습니다.")
    if not lesson["cls"]:
        raise TimetableError("학급이 적혀 있지 않은 수업이라 후보를 찾을 수 없습니다.")
    X = lesson["cls"]
    idx = {}
    for t, m in cur.items():
        for k, l in m.items():
            if l.get("cls"):
                idx.setdefault((l["cls"], k), []).append(t)
    slots = [s["key"] for s in tt["slots"]]
    periods = max(s["p"] for s in tt["slots"])
    d1 = int(key.split("-")[0])
    out = []
    for s2 in slots:
        if s2 == key:
            continue
        holders = idx.get((X, s2), [])
        if not holders or cur[tid].get(s2):
            continue   # 그 학급 수업이 없거나, 내가 그 시간에 다른 수업이 있음
        for B in holders:
            if B == tid:
                continue
            M = cur[B].get(s2)
            if not M or M["locked"]:
                continue
            N = cur[B].get(key)
            base = [{"tid": tid, "from": key, "to": s2, "l": lesson}, {"tid": B, "from": s2, "to": key, "l": M}]
            if not N:
                out.append({"kind": "swap", "moves": base})
                continue
            if not two_step or N["locked"] or not N["cls"]:
                continue
            for s3 in slots:
                if s3 == key or not (cur[B].get(s3) is None or s3 == s2):
                    continue
                for C in idx.get((N["cls"], s3), []):
                    if C == B:
                        continue
                    P = cur[C].get(s3)
                    if not P or P["locked"] or not (cur[C].get(key) is None or C == tid) or (C == tid and s3 == s2):
                        continue
                    out.append({"kind": "two", "moves": base + [{"tid": B, "from": key, "to": s3, "l": N},
                                                               {"tid": C, "from": s3, "to": key, "l": P}]})
                    if len(out) > 400:
                        break
    names = {t["id"]: t["name"] for t in tt["teachers"]}
    for c in out:
        after = {}
        for m in c["moves"]:
            after.setdefault(m["tid"], dict(cur[m["tid"]]))
        for m in c["moves"]:
            if after[m["tid"]].get(m["from"]) is m["l"]:
                after[m["tid"]].pop(m["from"], None)
        for m in c["moves"]:
            after[m["tid"]][m["to"]] = m["l"]
        boards, worse, max_run = [], False, 0
        for t in after:
            days = sorted({int(k.split("-")[0]) for m in c["moves"] if m["tid"] == t for k in (m["from"], m["to"])})
            for d in days:
                before_run = longest_run(_day_map(cur[t], d), periods)
                after_run = longest_run(_day_map(after[t], d), periods)
                self_ = t == tid   # 교체를 부탁하는 선생님은 연속 수업 경고에서 뺌
                if not self_:
                    max_run = max(max_run, after_run)
                    worse = worse or (after_run >= RUN_LIMIT and after_run > before_run)
                cells = []
                into = {m["to"] for m in c["moves"] if m["tid"] == t}
                for p in range(1, periods + 1):
                    k = f"{d}-{p}"
                    b, a = cur[t].get(k), after[t].get(k)
                    cells.append({"p": p, "before": (b or {}).get("cls") or ((b or {}).get("subj") or "")[:2],
                                  "after": (a or {}).get("cls") or ((a or {}).get("subj") or "")[:2],
                                  "in": k in into, "out": bool(b) and not a, "locked": bool(a and a.get("locked") and k not in into)})
                boards.append({"tid": t, "name": names[t], "day": tt["days"][d], "self": self_,
                               "run_before": before_run, "run_after": after_run, "cells": cells})
        block = None
        for m in c["moves"]:
            for k in (m["from"], m["to"]):
                block = block or block_reason(day_events, slot_date(k, mon), grade_of(m["l"]["cls"]), today)
        d2 = int(c["moves"][0]["to"].split("-")[0])
        c.update(worse=worse, max_run=max_run, block=block, boards=boards, teachers=len({m["tid"] for m in c["moves"]}),
                 same_day=d2 == d1,
                 dist=abs(d2 - d1) * 10 + abs(int(c["moves"][0]["to"].split("-")[1]) - int(key.split("-")[1])))
    out.sort(key=lambda c: (bool(c["block"]), c["worse"], c["kind"] != "swap", c["max_run"], not c["same_day"], c["dist"]))
    return [_public(tt, c, mon, names) for c in out[:limit]]


def slot_label(tt, key, mon=None):
    d, p = (int(x) for x in key.split("-"))
    head = ""
    if mon:
        dt = slot_date(key, mon)
        head = f"{dt.month}/{dt.day}"
    return f"{head}({tt['days'][d]}) {p}교시" if head else f"{tt['days'][d]}요일 {p}교시"


def _public(tt, c, mon, names):
    moves = [{"tid": m["tid"], "name": names[m["tid"]], "cls": m["l"]["cls"], "subj": m["l"]["subj"], "lesson": m["l"]["id"],
              "from": m["from"], "to": m["to"],
              "from_date": slot_date(m["from"], mon).isoformat(), "to_date": slot_date(m["to"], mon).isoformat(),
              "from_label": slot_label(tt, m["from"], mon), "to_label": slot_label(tt, m["to"], mon)} for m in c["moves"]]
    return {"kind": c["kind"], "moves": moves, "worse": c["worse"], "max_run": c["max_run"], "block": c["block"],
            "boards": c["boards"], "teachers": c["teachers"], "same_day": c["same_day"]}


def request_text(c, me):
    """쿨메신저로 보낼 교체 부탁 글."""
    first = c["moves"][0]
    others = [m for m in c["moves"] if m["tid"] != first["tid"]]
    lines = [f"{m['name']} 선생님, {me} 입니다." for m in others[:1]]
    lines.append(f"{first['from_label']} {first['cls']} {first['subj']} 수업을 {first['to_label']}(으)로 바꿀 수 있을까요?")
    lines.append("바뀌는 내용:")
    lines += [f"- {m['name']}: {m['cls']} {m['subj']} {m['from_label']} → {m['to_label']}" for m in c["moves"]]
    lines.append("가능하시면 알려 주세요. 감사합니다.")
    return "\n".join(lines)


def lessons_on(tt, tid, d, swaps=()):
    """그날 이 선생님의 수업 [{p, cls, subj, moved, locked}] (교체 반영)."""
    if d.weekday() >= len(tt["days"]):
        return []
    cur = week_state(tt, monday(d), swaps).get(tid, {})
    out = []
    for k, l in cur.items():
        dd, p = (int(x) for x in k.split("-"))
        if dd == d.weekday():
            out.append({"p": p, "cls": l["cls"], "subj": l["subj"], "moved": bool(l.get("moved")), "locked": l["locked"]})
    return sorted(out, key=lambda x: x["p"])


def find_teacher(tt, name):
    name = (name or "").replace(" ", "")
    if not name:
        return None
    for t in tt["teachers"]:
        if t["name"].replace(" ", "") == name:
            return t["id"]
    return None
