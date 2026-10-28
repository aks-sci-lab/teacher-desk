"""텔레그램 비서: 묻는 말에 맞는 자료를 이 PC 안에서 찾고, 이 PC 의 LM Studio 가 읽고 판단해 답함.

- 자료 찾기·날짜 계산은 규칙으로 (빠르고 정확), 읽고 답하기만 AI 로.
- AI 에는 이 PC(127.0.0.1)의 LM Studio 로만, 학생 명단의 이름은 가려서 보냄 (ai.check_local · ai.mask_names).
- '7번 조퇴'·'브리핑'·'3번 원문' 같은 말은 AI 없이 바로 명령으로.
"""

import re
from datetime import date, timedelta

from . import ai, queries

SYSTEM_QA = (
    "너는 한국 중·고등학교 담임 교사의 업무 비서다. 교사가 텔레그램으로 묻는 말에, 아래 주어진 쿨메신저 쪽지·업무포털 공문·"
    "달력 일정만 근거로 한국어로 짧게 답한다. 자료에 없으면 '받은 자료에서 찾지 못했습니다' 라고 말하고 지어내지 않는다."
)
QA_RULES = """오늘은 {today}이다.
규칙:
- 묻는 말에 바로 답하기 (첫 줄에 결론). 전체 10줄 안쪽, 한 줄 60자 안쪽
- 날짜는 '11/26(목)' 처럼 요일과 함께, 마감이면 'D-3' 처럼 남은 날도
- 근거 자료 번호를 줄 끝에 [2] 처럼 적기 (일정은 근거가 된 자료 번호)
- 해야 할 일이 있으면 '할 일:' 로 따로 적기
- 인사말·맺음말 없이

질문: {question}

{materials}"""

PER_ITEM = 900           # 자료 한 건에서 보내는 글자 수
MAX_ITEMS = 6
TOTAL = 6000             # 작은 모델도 빨리 읽게 (LM Studio 약 70토큰/초)
QA_TIMEOUT = 300

# 조사·어미를 떼고 남길 검색 낱말 (질문 말투는 버림)
P_WORD = re.compile(r"[가-힣A-Za-z0-9][가-힣A-Za-z0-9·\-]*")
JOSA = re.compile(r"(은|는|이|가|을|를|에|의|도|로|으로|과|와|만|까지|부터|에서|에게|이랑|랑|하고|이야|야|이요|요|인가|인지|이에요|예요|이나|나)$")
STOP = set("뭐 뭐야 뭐지 뭔가 뭘 무엇 무슨 언제 언제야 어디 어떻게 왜 얼마 몇 있어 있나 있니 있지 없어 알려줘 알려 줘 해줘 해 주세요 좀 보여줘 보여 찾아줘 찾아 말해줘 "
           "이번 다음 지난 주 주에 달 오늘 내일 모레 어제 이번주 다음주 지난주 이번달 해야 할 하는 했어 하나 관련 내용 정리 확인 "
           "그 저 것 거 게 나 내 우리 혹시 그리고 그럼 요즘 최근 일정 마감 메시지 쪽지 공문 자료".split())


def keywords(text):
    out = []
    for w in P_WORD.findall(text):
        w = JOSA.sub("", w) if len(w) > 2 else w
        if len(w) >= 2 and w not in STOP and not w.isdigit() and w not in out:
            out.append(w)
    return out[:6]


def period(text, today):
    """질문 속 기간 ('이번 주', '다음 주', '오늘', '내일', '이번 달'), 없으면 오늘부터 2주."""
    t = text.replace(" ", "")
    mon = today - timedelta(days=today.weekday())
    if "다음주" in t or "차주" in t:
        return mon + timedelta(days=7), mon + timedelta(days=13)
    if "이번주" in t or "금주" in t:
        return today, mon + timedelta(days=6)
    if "지난주" in t:
        return mon - timedelta(days=7), mon - timedelta(days=1)
    if "모레" in t:
        return today + timedelta(days=2), today + timedelta(days=2)
    if "내일" in t:
        return today + timedelta(days=1), today + timedelta(days=1)
    if "오늘" in t:
        return today, today
    if "이번달" in t:
        nxt = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        return today, nxt - timedelta(days=1)
    return today, today + timedelta(days=14)


# ---- 명령으로 바로 알아듣는 말 ----
P_PASS = re.compile(r"(?:(\d{1,3})\s*번\D{0,6}?(조퇴|외출)|(조퇴|외출)\S*\s*(\d{1,3})\s*번?)")
P_TIMES = re.compile(r"(\d{1,2})\s*(?::|시)\s*(\d{2})?\s*분?")
P_REF = re.compile(r"(?:(\d{1,3})\s*번?\s*(?:자료)?\s*(원문|파일)|(원문|파일)\s*(\d{1,3}))")


P_LESSON = re.compile(r"수업|시간표")
P_WEEKDAY = re.compile(r"([월화수목금])요일")


def lesson_days(text, today):
    """'내일 수업'·'금요일 시간표'·'다음 주 수업' → 날짜들 (주말은 뺌)."""
    m = P_WEEKDAY.search(text)
    if m:
        target = "월화수목금".index(m.group(1))
        mon = today - timedelta(days=today.weekday())
        if re.search(r"다음\s*주|차주", text) or (target < today.weekday() and not re.search(r"이번\s*주|금주", text)):
            mon += timedelta(days=7)
        return [mon + timedelta(days=target)]
    start, end = period(text, today)
    if (end - start).days > 6:   # 기간을 말하지 않았으면 오늘
        start = end = today
    return [start + timedelta(days=i) for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5]


def intent(text):
    """자연스러운 말 → (명령, 인자) 또는 None. 예) '7번 학생 조퇴 1시 30분' → ('early', ['7', '13:30'])."""
    m = P_PASS.search(text)
    if m:
        number = m.group(1) or m.group(4)
        kind = "early" if (m.group(2) or m.group(3)) == "조퇴" else "out"
        times = []
        for h, mi in P_TIMES.findall(text[m.end():] if m.group(1) else text[m.end():]):
            h = int(h)
            if h < 8 and "오전" not in text:
                h += 12   # 학교 시간: '1시 30분' 은 오후
            times.append(f"{h:02d}:{int(mi or 0):02d}")
        return kind, [number, *times[:2]]
    m = P_REF.search(text)
    if m and len(text) <= 20:
        n = m.group(1) or m.group(4)
        return ("text" if (m.group(2) or m.group(3)) == "원문" else "file"), [n]
    if re.search(r"브리핑|오늘\s*(할\s*일|업무|뭐)", text):
        return "brief", []
    if P_LESSON.search(text) and len(text) <= 25 and not re.search(r"교체|바꾸|바꿀|변경", text):
        return "lessons", [text]
    return None


# ---- 자료 모으기 ----
def gather(db, question, today=None):
    """질문에 맞는 자료(낱말이 많이 맞는 순)와 그 기간의 일정. AI 에 보낼 재료이자, AI 가 없을 때의 답."""
    today = today or date.today()
    words = keywords(question)
    scored = {}
    for w in words:
        where, args = queries._terms_where(["title", "body", "summary", "doc_numbers", "category"], [w])
        for r in db.query(f"SELECT id FROM items WHERE {where} AND status != 'archived' "
                          "ORDER BY received_at DESC LIMIT 40", args):
            scored[r["id"]] = scored.get(r["id"], 0) + 1
    ids = sorted(scored, key=lambda i: (-scored[i], -i))[:MAX_ITEMS]
    start, end = period(question, today)
    events = queries.events_between(db, start, end, include_done=False)
    if words:   # 낱말이 있으면 그 낱말이 든 일정·찾은 자료의 일정만
        events = [e for e in events if e["item_id"] in scored or any(w in (e["text"] or "") + (e["item_title"] or "") for w in words)]
    if not ids and not words:
        # '이번 주 뭐 있어?' 처럼 낱말이 없으면 그 기간 일정의 자료와 최근 자료
        ids = list(dict.fromkeys([e["item_id"] for e in events if e["item_id"]]))[:MAX_ITEMS]
        if len(ids) < 3:
            ids += [r["id"] for r in db.query("SELECT id FROM items WHERE source IN ('cool', 'edufine') AND status != 'archived' "
                                               "ORDER BY received_at DESC LIMIT ?", (MAX_ITEMS,)) if r["id"] not in ids]
            ids = ids[:MAX_ITEMS]
    lessons = {}
    if re.search(r"수업|시간표|교시|빈\s*시간|공강", question):
        for d in lesson_days(question, today) if (end - start).days <= 6 else [today + timedelta(days=i) for i in range(7)]:
            ls = queries.my_lessons(db, None, d)
            if ls is None:
                break
            if d.weekday() < 5:
                lessons[d.isoformat()] = ls
    items = []
    for i in ids:
        it = db.one("SELECT id, source, title, body, received_at, category FROM items WHERE id = ?", (i,))
        if it:
            items.append(it)
    return {"words": words, "items": items, "events": events[:20], "start": start, "end": end, "lessons": lessons}


def fmt_day(d):
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return f"{d.month}/{d.day}({'월화수목금토일'[d.weekday()]})"


def build_prompt(question, found, names, today):
    parts, total = [], 0
    names_src = {"cool": "쿨메신저", "edufine": "업무포털", "paste": "직접 입력", "school": "학사일정"}
    for n, it in enumerate(found["items"], 1):
        body = re.sub(r"\n{2,}", "\n", it["body"] or "")[:PER_ITEM]
        chunk = f"[{n}] {names_src.get(it['source'], it['source'])} · {it['received_at'][:10]} · {it['title']}\n{body}"
        if total + len(chunk) > TOTAL:
            break
        parts.append(chunk)
        total += len(chunk)
    ref = {it["id"]: n for n, it in enumerate(found["items"], 1)}
    ev_lines = []
    for e in found["events"]:
        when = fmt_day(e["date"]) + (f" {e['time']}" if e["time"] else "")
        src = f" [{ref[e['item_id']]}]" if e["item_id"] in ref else ""
        ev_lines.append(f"- {when} {'마감' if e['kind'] == 'deadline' else '일정'} · {(e['item_title'] or e['text'])[:60]}{src}")
    materials = (f"달력 일정 ({fmt_day(found['start'])}~{fmt_day(found['end'])}):\n" + ("\n".join(ev_lines) or "- 없음")
                 + "\n\n자료:\n" + ("\n\n".join(parts) or "(맞는 자료 없음)"))
    if found.get("lessons"):
        materials = "내 수업 (시간표):\n" + "\n".join(
            f"- {fmt_day(d)} " + (", ".join(f"{l['p']}교시 {l['cls']} {l['subj']}" for l in ls) or "수업 없음")
            for d, ls in found["lessons"].items()) + "\n\n" + materials
    prompt = QA_RULES.format(today=f"{today.isoformat()} ({'월화수목금토일'[today.weekday()]})", question=question,
                             materials=materials)
    return ai.mask_names(prompt, names), len(parts)


def answer(db, config, question, today=None):
    """질문 → (답 글, 근거 자료 id 목록). AI 를 켜지 않았거나 쓸 수 없으면 ai.AIError."""
    today = today or date.today()
    if not config["ai_enabled"]:
        raise ai.AIError("AI 요약이 꺼져 있습니다")
    found = gather(db, question, today)
    base = config["ai_base"]
    ai.check_local(base)
    model = ai.pick_model(config)
    prompt, used = build_prompt(question, found, [s["name"] for s in db.students()], today)
    text = ai.ask(base, model, prompt, timeout=QA_TIMEOUT, system=SYSTEM_QA, max_tokens=700)
    ids = [it["id"] for it in found["items"][:used]]
    # 답에 나온 자료 번호만 단추로 (나오지 않았으면 모두)
    cited = sorted({int(n) for g in re.findall(r"\[(\d+(?:\s*,\s*\d+)*)\]", text) for n in re.split(r"\s*,\s*", g)})
    cited_ids = [ids[n - 1] for n in cited if 0 < n <= len(ids)] or ids
    return ai.mask_names(text, [s["name"] for s in db.students()]), ids, cited_ids
