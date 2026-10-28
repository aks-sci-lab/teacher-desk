"""글에서 날짜·시각·마감을 찾아 일정으로 만들고, 분류·요약합니다.

AI 를 쓰지 않는 규칙 기반 분석이라 글이 이 PC 밖으로 나가지 않습니다.
학교 메시지·공문에 자주 나오는 표현을 기준으로 했습니다.
  날짜: 2026. 11. 3.  /  2026-11-03  /  11월 3일  /  11.3.(화)  /  11/3  /  오늘·내일·모레  /  다음 주 화요일
  기간: 11월 3일 ~ 7일  /  11. 3.(화) ~ 11. 7.(토)
  시각: 오후 3시  /  15:00  /  3시 30분  /  2시 반
"""

import re
from datetime import date, timedelta

WEEKDAYS = "월화수목금토일"

CATEGORIES = ["제출·마감", "회의", "연수", "행사", "수업·평가", "학사", "참고"]

DEADLINE_WORDS = ("마감", "기한", "제출", "회신", "까지", "보고", "신청", "등록")
CATEGORY_WORDS = [
    ("회의", ("회의", "협의회", "위원회", "협의", "상담", "면담")),
    ("연수", ("연수", "워크숍", "컨설팅", "설명회", "교육 실시", "직무교육")),
    ("행사", ("행사", "대회", "축제", "체험", "운동회", "졸업식", "입학식", "수학여행",
             "공연", "발표회", "캠프", "견학", "봉사")),
    ("수업·평가", ("수업", "평가", "시험", "고사", "수행", "공개수업", "성적")),
]
SUMMARY_WORDS = ("제출", "마감", "기한", "대상", "장소", "일시", "기간", "요청", "협조", "회신",
                 "붙임", "방법", "신청", "필수", "반드시")

# ---- 날짜 표현 ----
_DOW = r"(?:\s*\(\s*([월화수목금토일])(?:요일)?\s*\))?"
P_FULL = re.compile(r"(20\d{2})\s*(?:[.\-/]|년)\s*(\d{1,2})\s*(?:[.\-/]|월)\s*(\d{1,2})\s*(?:일|\.)?" + _DOW)
P_KMD = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일" + _DOW)
P_DOT = re.compile(r"(?<![\d.])(\d{1,2})\s*\.\s*(\d{1,2})\s*\.?\s*\(\s*([월화수목금토일])\s*\)")
# '3/4 분기', '1/2 이상' 같은 분수는 날짜가 아님
P_SLASH = re.compile(r"(?<![\d/])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])(?!\s*(?:분기|배|등분|쪽|페이지|이상|이하|정도|가량|명))" + _DOW)
P_REL_DAY = re.compile(r"(오늘|금일|내일|명일|모레)")
P_REL_WEEK = re.compile(r"(이번\s*주|금주|다음\s*주|차주|다다음\s*주)\s*([월화수목금토일])(?:요일)?")
P_WEEKDAY = re.compile(r"(?<![가-힣])([월화수목금토일])요일")

# 기간의 끝: "~ 7일", "~ 7.(토)", "~ 11. 7.(토)", "~ 11월 7일", "~ 2026. 11. 7."
P_RANGE_END = re.compile(
    r"\s*[~∼～\-–]\s*(?:"
    r"(20\d{2})\s*(?:[.\-/]|년)\s*(\d{1,2})\s*(?:[.\-/]|월)\s*(\d{1,2})\s*(?:일|\.)?|"
    r"(\d{1,2})\s*(?:월|\.)\s*(\d{1,2})\s*(?:일|\.)?|"
    r"(\d{1,2})\s*(?:일|\.)"
    r")" + _DOW)

P_TIME_KO = re.compile(r"(오전|오후|낮|저녁|밤|아침)?\s*(\d{1,2})\s*시(?!간|기|작|행|청|설|점|도)\s*(?:(\d{1,2})\s*분|(반))?")
P_TIME_COLON = re.compile(r"(?<![\d:])(오전|오후)?\s*(\d{1,2}):(\d{2})(?![\d:])")

# 낱말 첫머리에서만 시작 (띄어쓰기 없이 긴 글에서 자리마다 다시 찾느라 수십 초 걸리던 것 방지)
P_DOCNUM = re.compile(r"(?<![가-힣A-Za-z0-9])[A-Za-z0-9]*"
                      r"([가-힣][가-힣A-Za-z0-9]*(?:과|팀|부|실|청|원|센터|학교|위원회|지원단))\s*-\s*(\d{2,7})(?!\d)")


def _closest_year(month, day, ref):
    """연도가 없는 날짜는 기준일과 가장 가까운 해로 봅니다 (12월에 받은 '1월 5일'은 다음 해)."""
    best = None
    for y in (ref.year - 1, ref.year, ref.year + 1):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        if best is None or abs((d - ref).days) < abs((best - ref).days):
            best = d
    return best


def _safe_date(y, m, d):
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def _week_start(d):
    return d - timedelta(days=d.weekday())


def find_dates(text, ref):
    """글 속 날짜 표현을 모두 찾아 [(시작, 끝, date, end_date)] 로 돌려줍니다 (겹치는 것은 앞의 것 우선)."""
    found = []

    def add(m, d, end=None):
        if d is not None:
            found.append([m.start(), m.end(), d, end])

    for m in P_FULL.finditer(text):
        add(m, _safe_date(*m.group(1, 2, 3)))
    for m in P_KMD.finditer(text):
        add(m, _closest_year(int(m.group(1)), int(m.group(2)), ref))
    for m in P_DOT.finditer(text):
        add(m, _closest_year(int(m.group(1)), int(m.group(2)), ref))
    for m in P_SLASH.finditer(text):
        mo, dy = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= dy <= 31:
            add(m, _closest_year(mo, dy, ref))
    for m in P_REL_DAY.finditer(text):
        add(m, ref + timedelta(days={"오늘": 0, "금일": 0, "내일": 1, "명일": 1, "모레": 2}[m.group(1)]))
    for m in P_REL_WEEK.finditer(text):
        which = re.sub(r"\s", "", m.group(1))
        weeks = {"이번주": 0, "금주": 0, "다음주": 1, "차주": 1, "다다음주": 2}[which]
        add(m, _week_start(ref) + timedelta(weeks=weeks, days=WEEKDAYS.index(m.group(2))))
    for m in P_WEEKDAY.finditer(text):
        # '수요일' 만 있으면 오늘 이후 가장 가까운 그 요일
        add(m, ref + timedelta(days=(WEEKDAYS.index(m.group(1)) - ref.weekday()) % 7))

    # 겹침 제거: 먼저 시작하고 더 긴 것을 남김
    found.sort(key=lambda f: (f[0], -(f[1] - f[0])))
    picked = []
    for f in found:
        if picked and f[0] < picked[-1][1]:
            continue
        picked.append(f)

    # 기간 끝 붙이기
    for f in picked:
        m = P_RANGE_END.match(text, f[1])
        if not m:
            continue
        start = f[2]
        if m.group(1):
            end = _safe_date(*m.group(1, 2, 3))
        elif m.group(4):
            end = _safe_date(start.year, m.group(4), m.group(5))
            if end and end < start:
                end = _safe_date(start.year + 1, m.group(4), m.group(5))
        else:
            end = _safe_date(start.year, start.month, m.group(6))
            if end and end < start:  # 11.28 ~ 3일 → 다음 달
                nm = start.replace(day=1) + timedelta(days=32)
                end = _safe_date(nm.year, nm.month, m.group(6))
        if end and end > start and (end - start).days <= 366:
            f[3] = end
            f[1] = m.end()
            # 기간 끝이 다른 날짜 표현으로 따로 잡혔으면 지움
    cleaned = []
    for f in picked:
        if cleaned and f[0] < cleaned[-1][1]:
            continue
        cleaned.append(f)
    return [tuple(f) for f in cleaned]


def find_time(text):
    """글 앞부분에서 첫 시각을 찾아 'HH:MM' 으로. 없으면 None."""
    best = None
    for m in P_TIME_COLON.finditer(text):
        h, mi = int(m.group(2)), int(m.group(3))
        if m.group(1) == "오후" and h < 12:
            h += 12
        if h < 24 and mi < 60:
            best = (m.start(), f"{h:02d}:{mi:02d}")
            break
    for m in P_TIME_KO.finditer(text):
        if best and m.start() > best[0]:
            break
        h = int(m.group(2))
        mi = int(m.group(3)) if m.group(3) else (30 if m.group(4) else 0)
        ampm = m.group(1)
        if ampm in ("오후", "저녁", "밤") and h < 12:
            h += 12
        elif ampm == "낮" and h < 6:
            h += 12
        elif not ampm and 1 <= h <= 6:
            h += 12  # 학교 일과 중 '3시' 는 보통 오후 3시
        if h < 24 and mi < 60:
            best = (m.start(), f"{h:02d}:{mi:02d}")
            break
    return best[1] if best else None


def classify_line(line, is_deadline):
    if is_deadline:
        return "제출·마감"
    for cat, words in CATEGORY_WORDS:
        if any(w in line for w in words):
            return cat
    return None


def _lines(text):
    for raw in re.split(r"\r?\n", text):
        line = raw.strip()
        if line:
            yield line


# '가. 교육부 교육과정운영지원과-5058(2026. 10. 23.)' 같은 관련 공문 줄: 날짜는 공문 날짜라 일정이 아님
P_REF_BEFORE = re.compile(r"-\s*\d{2,7}\s*\(\s*$")
P_REF_LINE = re.compile(r"^\s*(?:\d+\s*[.)]\s*)?(?:관련|참고)\s*[:：]?\s*$|^\s*[가-하]\s*[.)]\s*.*-\s*\d{2,7}\s*\(\s*20\d{2}")


def is_reference_line(line):
    return bool(P_REF_LINE.search(line))


def extract_events(text, ref):
    """글에서 일정 목록을 뽑습니다."""
    events = []
    seen = set()
    for line in _lines(text):
        dates = find_dates(line, ref)
        if not dates:
            continue
        # 줄 전체에서 보는 단서는 한 번만 (날짜가 많은 긴 줄에서 날짜마다 줄 전체를 다시 훑지 않게)
        line_deadline = any(w in line for w in ("마감", "기한", "회신")) or (
            "까지" in line and any(w in line for w in ("제출", "신청", "등록", "보고")))
        line_category = classify_line(line, False)
        for i, (start, end, d, end_d) in enumerate(dates):
            if P_REF_BEFORE.search(line, max(0, start - 40), start):
                continue  # 문서번호(날짜): 관련 공문의 날짜 (바로 앞 몇 글자만 봄)
            nxt = dates[i + 1][0] if i + 1 < len(dates) else len(line)
            after = line[end:min(nxt, end + 25)]
            t = find_time(after)
            near = line[end:min(len(line), end + 20)]
            is_deadline = "까지" in near or line_deadline
            kind = "deadline" if is_deadline else "event"
            category = "제출·마감" if is_deadline else line_category
            key = (d.isoformat(), t, kind)
            if key in seen:
                continue
            seen.add(key)
            events.append({
                "date": d.isoformat(),
                "end_date": end_d.isoformat() if end_d else None,
                "time": t,
                "kind": kind,
                "category": category,
                "text": line[:160],
            })
    return events


def find_doc_numbers(text):
    out = []
    for m in P_DOCNUM.finditer(text):
        num = f"{m.group(1)}-{m.group(2)}"
        if num not in out:
            out.append(num)
    return out


def make_title(text, fallback=""):
    for line in _lines(text):
        line = re.sub(r"^[\s\-=*#>■□●○◎▶▷※]+", "", line).strip()
        if len(line) >= 2:
            return line[:80]
    return fallback or "(제목 없음)"


def summarize(text, title=None, limit=4):
    """중요해 보이는 줄(날짜·마감·대상·장소 등)을 원래 순서대로 몇 줄 뽑습니다."""
    scored = []
    for i, line in enumerate(_lines(text)):
        if title and line.strip() == title.strip():
            continue
        if re.match(r"^(수신|경유|참조|발신|시행|접수|우\s*\d|전화|전송|공개|비공개|/)", line):
            continue
        if is_reference_line(line):
            continue  # '1. 관련' 과 그 아래 관련 공문 줄
        s = 0
        if P_FULL.search(line) or P_KMD.search(line) or P_DOT.search(line) or P_REL_WEEK.search(line):
            s += 3
        s += sum(2 for w in SUMMARY_WORDS if w in line)
        if re.match(r"^\s*(\d+|[가-하])\s*[.)]", line):
            s += 1
        if len(line) < 6:
            s -= 2
        if s > 0:
            scored.append((s, i, line[:140]))
    top = sorted(scored, key=lambda x: (-x[0], x[1]))[:limit]
    return [line for _, _, line in sorted(top, key=lambda x: x[1])]


def item_category(text, events):
    """항목 전체 분류: 마감이 있으면 제출·마감, 아니면 일정·글 속 낱말로."""
    if any(e["kind"] == "deadline" for e in events):
        return "제출·마감"
    for e in events:
        if e["category"]:
            return e["category"]
    head = text[:400]
    for cat, words in CATEGORY_WORDS:
        if any(w in head for w in words):
            return cat
    return "참고"


def analyze(text, ref=None, title=None):
    """수신 항목 하나 분석: 제목·요약·일정·분류·문서번호."""
    ref = ref or date.today()
    title = title or make_title(text)
    events = extract_events(text, ref)
    category = item_category(text, events)
    # 줄에 단서가 없는 일정은 제목 → 항목 분류 순으로 따름 ('일시: 11. 3. 15:00' 같은 줄)
    fallback = classify_line(title, False) or (category if category != "제출·마감" else "참고")
    for e in events:
        e["category"] = e["category"] or fallback
    return {
        "title": title,
        "summary": summarize(text, title),
        "events": events,
        "category": category,
        "doc_numbers": find_doc_numbers(text),
    }
