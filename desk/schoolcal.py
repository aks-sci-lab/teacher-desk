"""학사일정 가져오기: 학교마다 다른 양식을 한 가지 일정 목록으로 바꿉니다.

양식을 정해 두지 않고, 들어온 자료의 모양을 보고 읽는 방법을 고릅니다.
  1) 달력표   : 머리줄에 '3월 4월 …', 옆 칸에 1~31일 (또는 그 반대). 세로로 합친 칸은 기간 일정
  2) 목록     : 날짜 열 + 행사 열 (엑셀 날짜, '2026.3.2', '3. 2.(월)', '3월 2일~6일' 모두)
  3) 글       : 한글·PDF·붙여넣기 등 줄마다 날짜가 있는 글
연도가 없는 날짜는 학년도로 정합니다: 3~12월 → 학년도, 1~2월 → 다음 해.
어느 방법이든 결과를 미리보기로 보여 주고, 선생님이 고친 뒤 달력에 넣습니다.
"""

import csv
import io
import re
import tempfile
from datetime import date, timedelta
from pathlib import Path

from . import extract, xlsx
from .docs import DocError, decode_text, read_document

CATEGORY = "학사"
WEEKDAY_ONLY = re.compile(r"^\(?[월화수목금토일]\)?(요일)?$")
MONTH_HEAD = re.compile(r"^\s*(\d{1,2})\s*월\s*$")
RELATIVE = re.compile(r"^(오늘|금일|내일|명일|모레|이번|금주|다음|차주|다다음|[월화수목금토일]요일)")
NOT_EVENT = re.compile(r"^(토요휴업일|일요일|토요일|주말|-|—|x|X|·)$")
CATEGORY_WORDS = [
    ("수업·평가", ("고사", "시험", "평가", "수행", "모의")),
    ("회의", ("회의", "위원회", "협의회")),
    ("연수", ("연수", "워크숍")),
    ("행사", ("체험", "축제", "대회", "수학여행", "운동회", "체육대회", "발표", "졸업", "입학", "수련", "캠프")),
]


class CalError(Exception):
    pass


def default_school_year(today=None):
    today = today or date.today()
    return today.year if today.month >= 3 else today.year - 1


def ay_date(school_year, month, day):
    """연도가 없는 날짜 → 학년도 기준 날짜 (없는 날이면 None)."""
    try:
        return date(school_year if month >= 3 else school_year + 1, month, day)
    except ValueError:
        return None


def category_for(title):
    for cat, words in CATEGORY_WORDS:
        if any(w in title for w in words):
            return cat
    return CATEGORY


def clean_title(s):
    s = re.sub(r"\s+", " ", str(s)).strip()
    s = re.sub(r"^[\s\-–~∼～:：·•▶■□○●※*,/]+|[\s\-–~∼～:：·•,/]+$", "", s)
    return s


def excel_date(v):
    """엑셀 날짜 숫자(1900 날짜 체계) → date."""
    if isinstance(v, float) and 20000 < v < 80000:
        return date(1899, 12, 30) + timedelta(days=int(v))
    return None


# ---------------------------------------------------------------- 글에서 찾기
def dates_in_text(text, school_year):
    """[(시작, 끝 위치, 시작 날짜, 끝 날짜)] — 연도가 적혀 있지 않은 날짜는 학년도 규칙으로 고침."""
    ref = date(school_year, 9, 1)
    out = []
    for s, e, d, end in extract.find_dates(text, ref):
        span = text[s:e]
        if RELATIVE.match(span):
            continue  # '내일', '다음 주 화요일', '수요일' 같은 말은 학사일정 날짜가 아님
        if not re.search(r"20\d{2}", span):
            d = ay_date(school_year, d.month, d.day) or d
            if end:
                end = ay_date(school_year, end.month, end.day) or end
                if end < d:
                    end = None
        out.append((s, e, d, end))
    return out


def from_text(text, school_year):
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        found = dates_in_text(line, school_year)
        if not found:
            continue
        # 한 줄에 날짜가 여럿이면 날짜마다 그 뒤의 글이 행사 이름
        # '상담 주간 ~ 3월 7일' 처럼 행사 이름 뒤에 끝 날짜가 오면 기간으로 봄
        i = 0
        while i < len(found):
            s, e, d, end = found[i]
            nxt = found[i + 1][0] if i + 1 < len(found) else len(line)
            between = line[e:nxt]
            if i + 1 < len(found) and not end and re.search(r"[~∼～\-–]\s*$", between):
                tail_end = found[i + 2][0] if i + 2 < len(found) else len(line)
                if not clean_title(line[found[i + 1][1]:tail_end]):
                    end = found[i + 1][2]
                    i += 1
            title = clean_title(between)
            if not title and i == 0:
                title = clean_title(line[:s])
            if title and not WEEKDAY_ONLY.match(title):
                events.append(_event(d, end, title))
            i += 1
    return events


# ---------------------------------------------------------------- 표에서 찾기
def _cell(v):
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    return str(v or "").strip()


def _day_value(v):
    s = _cell(v)
    m = re.match(r"^(\d{1,2})\s*(일)?\s*(\(?[월화수목금토일]\)?)?$", s)
    if m and 1 <= int(m.group(1)) <= 31:
        return int(m.group(1))
    return None


def from_grid(rows, merges, school_year):
    """달력표: 머리줄에 월, 한 열에 1~31일. 없으면 None."""
    for transpose in (False, True):
        grid = [list(r) for r in zip(*rows)] if transpose else rows
        mg = [(c1, r1, c2, r2) for r1, c1, r2, c2 in merges] if transpose else merges
        for hr, row in enumerate(grid[:15]):
            months = {c: int(MONTH_HEAD.match(_cell(v)).group(1)) for c, v in enumerate(row)
                      if MONTH_HEAD.match(_cell(v)) and 1 <= int(MONTH_HEAD.match(_cell(v)).group(1)) <= 12}
            if len(months) < 3:
                continue
            # 1~31 이 차례로 있는 '일' 열 찾기
            day_col = None
            for c in range(len(row)):
                seq = [_day_value(r[c]) if c < len(r) else None for r in grid[hr + 1:hr + 40]]
                if sum(1 for i, d in enumerate(seq) if d == i + 1) >= 25:
                    day_col = c
                    break
            if day_col is None:
                continue
            day_rows = {}
            for r in range(hr + 1, min(hr + 40, len(grid))):
                d = _day_value(grid[r][day_col]) if day_col < len(grid[r]) else None
                if d:
                    day_rows[r] = d
            month_cols = sorted(months)
            events = []
            for i, mc in enumerate(month_cols):
                limit = month_cols[i + 1] if i + 1 < len(month_cols) else mc + 3
                cols = [c for c in range(mc, min(limit, mc + 3)) if c != day_col]
                for r, day in day_rows.items():
                    for c in cols:
                        v = _cell(grid[r][c]) if c < len(grid[r]) else ""
                        if not v or WEEKDAY_ONLY.match(v) or NOT_EVENT.match(v) or _day_value(v) == day:
                            continue
                        start = ay_date(school_year, months[mc], day)
                        if not start:
                            continue
                        end = None
                        for (r1, c1, r2, c2) in mg:   # 세로로 합친 칸 = 기간
                            if r1 == r and c1 <= c <= c2 and r2 > r1:
                                last = max((day_rows.get(x) for x in range(r1, r2 + 1) if day_rows.get(x)), default=day)
                                end = ay_date(school_year, months[mc], last)
                        for line in re.split(r"[\n/]", v):
                            title = clean_title(line)
                            if title:
                                events.append(_event(start, end, title))
            return {"mode": "grid", "events": events}
    return None


def from_list(rows, school_year):
    """목록: 날짜 열 + 행사 열. 없으면 None."""
    if not rows:
        return None
    ncols = max(len(r) for r in rows)
    body = rows[:]
    best = None
    for c in range(ncols):
        vals = [r[c] if c < len(r) else "" for r in body]
        filled = [v for v in vals if _cell(v)]
        if len(filled) < 3:
            continue
        ok = sum(1 for v in filled if excel_date(v) or dates_in_text(_cell(v), school_year))
        if ok / len(filled) >= 0.6 and (best is None or ok > best[1]):
            best = (c, ok)
    if not best:
        return None
    dc = best[0]
    # 행사 열: 날짜 열이 아니고 글이 가장 긴 열
    def avg_len(c):
        vals = [_cell(r[c]) for r in body if c < len(r) and not excel_date(r[c])]
        vals = [v for v in vals if not dates_in_text(v, school_year) or len(v) > 12]
        return sum(len(v) for v in vals) / max(len(body), 1)
    others = [c for c in range(ncols) if c != dc]
    if not others:
        return None
    tc = max(others, key=avg_len)
    # 끝 날짜 열 (있으면): 날짜 열 바로 옆의 날짜 열
    ec = None
    for c in (dc + 1,):
        if c < ncols and c != tc:
            vals = [r[c] for r in body if c < len(r) and _cell(r[c])]
            if vals and sum(1 for v in vals if excel_date(v) or dates_in_text(_cell(v), school_year)) / len(vals) >= 0.6:
                ec = c
    events = []
    for r in body:
        dv = r[dc] if dc < len(r) else ""
        title = clean_title(_cell(r[tc]) if tc < len(r) else "")
        if not title:
            continue
        d = excel_date(dv)
        end = None
        if not d:
            found = dates_in_text(_cell(dv), school_year)
            if not found:
                continue
            d, end = found[0][2], found[0][3]
        if ec is not None and ec < len(r) and _cell(r[ec]):
            e2 = excel_date(r[ec]) or (dates_in_text(_cell(r[ec]), school_year) or [(0, 0, None, None)])[0][2]
            if e2 and e2 > d:
                end = e2
        events.append(_event(d, end, title))
    return {"mode": "list", "events": events}


def from_table(rows, merges, school_year):
    return (from_grid(rows, merges, school_year) or from_list(rows, school_year)
            or {"mode": "text", "events": from_text("\n".join(" ".join(_cell(v) for v in r) for r in rows), school_year)})


# ---------------------------------------------------------------- 들어온 자료 읽기
def _event(d, end, title):
    if end and end <= d:
        end = None
    return {"date": d.isoformat(), "end_date": end.isoformat() if end else None, "time": None,
            "kind": "event", "category": category_for(title), "text": title[:160]}


def _dedupe(events):
    seen, out = set(), []
    for e in sorted(events, key=lambda e: (e["date"], e["text"])):
        k = (e["date"], e["end_date"], e["text"])
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


def _in_year(events, school_year):
    """학년도(3월~다음 해 2월) 밖의 날짜는 빼고 개수를 알려 줌."""
    start, end = date(school_year, 3, 1), date(school_year + 1, 3, 1)
    keep = [e for e in events if start <= date.fromisoformat(e["date"]) < end]
    return keep, len(events) - len(keep)


def parse(school_year, filename=None, data=None, text=None):
    """{'mode', 'events', 'sheet', 'skipped'} — 미리보기용."""
    sheet = None
    if text is not None:
        rows = [r.split("\t") for r in text.splitlines()]
        if sum(1 for r in rows if len(r) >= 2) >= 3:   # 엑셀에서 복사한 표
            res = from_table(rows, [], school_year)
        else:
            res = {"mode": "text", "events": from_text(text, school_year)}
    else:
        ext = Path(filename or "").suffix.lower()
        if ext == ".xlsx":
            try:
                sheets = xlsx.read(data)
            except xlsx.XlsxError as e:
                raise CalError(str(e))
            best = None
            for sh in sheets:
                r = from_table(sh["rows"], sh["merges"], school_year)
                if best is None or len(r["events"]) > len(best[0]["events"]):
                    best = (r, sh["name"])
            if not best:
                raise CalError("엑셀 파일에 시트가 없습니다")
            res, sheet = best
        elif ext == ".xls":
            raise CalError("예전 엑셀(.xls)은 읽을 수 없습니다. 엑셀에서 '다른 이름으로 저장 → .xlsx' 로 바꿔 주세요.")
        elif ext == ".csv":
            rows = list(csv.reader(io.StringIO(decode_text(data))))
            res = from_table(rows, [], school_year)
        elif ext in (".hwp", ".hwpx", ".pdf", ".txt", ".html", ".htm"):
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / f"upload{ext}"
                p.write_bytes(data)
                try:
                    body = read_document(p)
                except DocError as e:
                    raise CalError(str(e))
            res = {"mode": "text", "events": from_text(body, school_year)}
        else:
            raise CalError(f"이 형식은 읽을 수 없습니다: {ext or '(확장자 없음)'} — xlsx, csv, hwp, hwpx, pdf, txt 를 쓰세요")
    events, skipped = _in_year(_dedupe(res["events"]), school_year)
    return {"mode": res["mode"], "events": events, "sheet": sheet, "skipped": skipped}


def save(db, school_year, events, origin, replace=True):
    """미리보기에서 고른 일정을 '학사일정' 항목 하나로 저장 (같은 학년도 이전 것은 바꿈)."""
    title = f"{school_year}학년도 학사일정"
    removed = 0
    if replace:
        for r in db.query("SELECT id FROM items WHERE source = 'school' AND title LIKE ?", (title + "%",)):
            db.execute("DELETE FROM items WHERE id = ?", (r["id"],))
            removed += 1
    lines = [f"{e['date']}{'~' + e['end_date'] if e.get('end_date') else ''} {e['text']}" for e in events]
    analysis = {"summary": lines[:4], "category": CATEGORY, "doc_numbers": [], "events": events}
    from .db import now_str
    item_id = db.add_item("school", f"{title} ({origin})"[:120], "\n".join(lines), now_str(), analysis,
                          file_path=origin)
    db.execute("UPDATE items SET status = 'done' WHERE id = ?", (item_id,))  # 수신함 '진행 중'에 쌓이지 않게
    return {"item_id": item_id, "count": len(events), "replaced": removed}
