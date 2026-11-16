"""시험용: 지정 폴더의 파일을 읽어 색인(본문 + AI 요약 + AI 키워드)을 만들고, 작업 내용으로 관련 파일을 찾고,
고른 파일로 초안 글을 AI 에 부탁하는 흐름. 앱(desk/)에는 아직 넣지 않음.

- 파일 읽기는 앱의 desk.docs.read_document 를 그대로 쓰고, .docx 만 이 시험에서 더함(writers.docx_text).
- AI 는 앱의 desk.ai.ask / summarize_item(0.5.99 나눠 요약) 을 그대로 부름 → 127.0.0.1 로만, 학생 이름은 가림.
- 색인은 sqlite 파일 하나 (앱에서는 data/desk.db 안의 표가 될 자리).
"""

import hashlib
import math
import re
import sqlite3
import time
from collections import Counter
from datetime import date
from pathlib import Path

from desk import ai, docs
import writers

SUPPORTED = tuple(docs.SUPPORTED) + (".docx",)
SKIP_PREFIX = ("~$", ".")          # 오피스 임시 잠금 파일, 숨은 파일

KEYWORD_INSTRUCTION = """[색인 키워드] 아래 업무 자료의 제목과 요약을 보고, 교사가 나중에 이 파일을 찾을 때 입력할 만한
검색어를 8~12개 쉼표로만 나열해 줘. 자료에 나온 낱말과, 같은 뜻의 다른 말(예: 협의회→교과 모임, 상담→면담)을 섞어서.
설명이나 번호 없이 한 줄로.

제목: {title}
요약:
{summary}
"""
EXPAND_INSTRUCTION = """[검색어 늘리기] 교사가 할 작업: "{task}"
이 작업에 참고할 학교 업무 파일을 찾으려 한다. 파일 제목·본문에 나올 만한 검색어를 6~10개 쉼표로만 나열해 줘.
작업에 쓴 낱말과 같은 뜻의 다른 말(예: 견학→현장체험학습)을 섞어서. 설명 없이 한 줄로.
"""
DRAFT_SYSTEM = ("너는 한국 중·고등학교 교사의 문서 작성 비서다. 주어진 참고 자료에 있는 사실만 써서 학교 문서 초안을 쓴다. "
                "자료에 없는 날짜·숫자·이름은 지어내지 말고 '(확인 필요)' 라고 쓴다.")
DRAFT_INSTRUCTION = """[초안 작성] 오늘은 {today}이다. 교사가 할 작업: "{task}"
아래 참고 자료만 바탕으로 문서 초안을 이 모양으로만 써 줘 (앞뒤 인사말 없이):
# 문서 제목
## 소제목
- 항목 (한 줄에 하나, 날짜·숫자는 자료 그대로)
| 칸 | 칸 |  (표가 어울릴 때만, 첫 줄은 머리줄)
소제목은 3~5개. 자료에 없는 내용은 '(확인 필요)'.

참고 자료:
{materials}
"""
STOP_WORDS = {"작성", "정리", "만들기", "준비", "하기", "관련", "내용", "자료", "문서", "초안", "뒤", "다녀온", "실시"}
PARTICLES = ("에서", "으로", "에게", "까지", "부터", "을", "를", "이", "가", "은", "는", "의", "에", "로", "와", "과", "도")


# ---------------- 낱말 → 두 글자 조각 ----------------
def norm(text):
    """한글·영문·숫자만 남기고 띄어쓰기도 없앰 (pdf 가 '2 학기' 처럼 띄워 읽는 문제를 피함)."""
    return re.sub(r"[^0-9a-z가-힣]", "", (text or "").lower())


def bigrams(text):
    s = norm(text)
    return [s[i:i + 2] for i in range(len(s) - 1)] if len(s) > 1 else ([s] if s else [])


def query_terms(task):
    out = []
    for w in re.split(r"[\s,·/]+", task or ""):
        w = w.strip()
        for p in PARTICLES:
            if w.endswith(p) and len(w) - len(p) >= 2:
                w = w[: -len(p)]
                break
        if len(norm(w)) >= 2 and w not in STOP_WORDS:
            out.append(w)
    return out


# ---------------- 색인 ----------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY, ext TEXT, size INTEGER, mtime REAL, hash TEXT,
    text TEXT, summary TEXT, keywords TEXT, error TEXT, chars INTEGER,
    read_ms REAL, ai_ms REAL, ai_calls INTEGER, indexed_at TEXT
);
"""


def open_index(path):
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def scan(folder):
    folder = Path(folder)
    found, skipped = [], []
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(folder).as_posix()
        if p.name.startswith(SKIP_PREFIX):
            skipped.append((rel, "임시·숨은 파일"))
        elif p.suffix.lower() not in SUPPORTED:
            skipped.append((rel, f"형식 제외({p.suffix.lower()})"))
        else:
            found.append(p)
    return found, skipped


def read_text(p):
    if p.suffix.lower() == ".docx":
        try:
            return docs.tidy(writers.docx_text(p.read_bytes()))
        except Exception as e:  # zip·xml 오류 종류가 많음
            raise docs.DocError(f"워드 파일을 읽을 수 없습니다 ({type(e).__name__})")
    return docs.read_document(p)


class Counted:
    """ai.ask 를 감싸 부른 횟수를 셈 (시험 보고용)."""

    def __init__(self):
        self.calls = 0
        self._orig = ai.ask

    def __enter__(self):
        def wrapped(*a, **k):
            self.calls += 1
            return self._orig(*a, **k)
        ai.ask = wrapped
        return self

    def __exit__(self, *exc):
        ai.ask = self._orig


def index_folder(con, folder, base=None, model=None, names=(), chunk=ai.ITEM_CHARS, log=print):
    """폴더 전체를 색인. 바뀌지 않은 파일(크기·수정 시각·내용 해시 같음)은 건너뜀. AI 가 없으면 본문만."""
    folder = Path(folder)
    files, skipped = scan(folder)
    stats = {"files": len(files), "skipped": skipped, "new": 0, "unchanged": 0, "errors": [], "ai_calls": 0,
             "removed": 0}
    seen = set()
    for p in files:
        rel = p.relative_to(folder).as_posix()
        seen.add(rel)
        st = p.stat()
        old = con.execute("SELECT size, mtime, hash, summary, error FROM files WHERE path=?", (rel,)).fetchone()
        done = old and (old["summary"] or not base or (old["error"] and not old["error"].startswith("AI ")))
        if done and old["size"] == st.st_size and old["mtime"] == st.st_mtime:
            stats["unchanged"] += 1
            continue
        data_hash = hashlib.sha256(p.read_bytes()).hexdigest()
        if done and old["hash"] == data_hash:
            con.execute("UPDATE files SET size=?, mtime=? WHERE path=?", (st.st_size, st.st_mtime, rel))
            stats["unchanged"] += 1
            continue
        t0 = time.perf_counter()
        text, error = "", None
        try:
            text = read_text(p)
            if not text.strip():
                error = "글자가 없음 (스캔한 그림 pdf 이면 글을 읽을 수 없음)"
        except docs.DocError as e:
            error = str(e)
        read_ms = (time.perf_counter() - t0) * 1000
        summary, keywords, ai_ms, calls = "", "", 0.0, 0
        if base and text.strip() and not error:
            t1 = time.perf_counter()
            with Counted() as c:
                try:
                    it = {"source": "folder", "title": rel}
                    summary = ai.summarize_item(base, model, it, text, names, size=chunk)
                    kw = ai.ask(base, model, ai.mask_names(KEYWORD_INSTRUCTION.format(title=rel, summary=summary),
                                                           names), max_tokens=120)
                    keywords = ai.mask_names(kw, names).strip()
                except ai.AIError as e:
                    error = f"AI 요약 실패: {e}"
            calls = c.calls
            ai_ms = (time.perf_counter() - t1) * 1000
        if error:
            stats["errors"].append((rel, error))
        stats["new"] += 1
        stats["ai_calls"] += calls
        con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                    (rel, p.suffix.lower(), st.st_size, st.st_mtime, data_hash, text, summary, keywords, error,
                     len(text), read_ms, ai_ms, calls))
        log(f"  {rel}: {len(text):,}자, 읽기 {read_ms:.0f}ms, AI {calls}회 {ai_ms / 1000:.1f}초" +
            (f" — {error}" if error else ""))
    for row in con.execute("SELECT path FROM files").fetchall():
        if row["path"] not in seen:     # 폴더에서 지운 파일은 색인에서도 뺌
            con.execute("DELETE FROM files WHERE path=?", (row["path"],))
            stats["removed"] += 1
    con.commit()
    return stats


# ---------------- 검색 ----------------
FIELD_WEIGHTS = {"path": 3.0, "text": 1.0, "summary": 1.5, "keywords": 2.0}


def _like_search(con, terms, limit):
    """지금 앱의 '단순 검색' 방식(queries.search): 모든 낱말이 제목·본문에 그대로 들어 있어야 함."""
    if not terms:
        return []
    where = " AND ".join("(path LIKE ? OR text LIKE ?)" for _ in terms)
    args = [a for t in terms for a in (f"%{t}%", f"%{t}%")]
    return [r["path"] for r in con.execute(f"SELECT path FROM files WHERE {where} ORDER BY mtime DESC LIMIT ?",
                                           (*args, limit))]


def _bm25(con, terms, fields, limit, extra_terms=()):
    rows = con.execute("SELECT path, text, summary, keywords FROM files WHERE error IS NULL").fetchall()
    if not rows:
        return []
    docs_bg = []
    for r in rows:
        d = {}
        for f in fields:
            d[f] = Counter(bigrams(r[f] if f != "path" else Path(r["path"]).stem.replace("_", " ")))
        docs_bg.append((r["path"], d))
    n = len(docs_bg)
    avg = {f: (sum(sum(d[f].values()) for _, d in docs_bg) / n) or 1 for f in fields}
    q = [(t, 1.0) for t in terms] + [(t, 0.6) for t in extra_terms]
    grams = Counter()
    for t, w in q:
        for g in set(bigrams(t)):
            grams[g] += w
    df = Counter()
    for _, d in docs_bg:
        present = set()
        for f in fields:
            present |= set(d[f])
        for g in grams:
            if g in present:
                df[g] += 1
    k1, b = 1.2, 0.75
    scored = []
    for path, d in docs_bg:
        s = 0.0
        for g, qw in grams.items():
            idf = math.log(1 + (n - df[g] + 0.5) / (df[g] + 0.5))
            tf = 0.0
            for f in fields:
                c = d[f].get(g, 0)
                if c:
                    ln = sum(d[f].values()) / avg[f]
                    tf += FIELD_WEIGHTS[f] * c * (k1 + 1) / (c + k1 * (1 - b + b * ln))
            s += qw * idf * tf
        if s > 0:
            scored.append((s, path))
    scored.sort(reverse=True)
    return [p for _, p in scored[:limit]]


def expand_query(base, model, task, names=()):
    out = ai.ask(base, model, ai.mask_names(EXPAND_INSTRUCTION.format(task=task), names), max_tokens=120)
    return [w.strip(" .-\"'") for w in re.split(r"[,\n]", out) if len(norm(w)) >= 2][:12]


def search(con, task, method="bm25", limit=5, base=None, model=None, names=()):
    terms = query_terms(task)
    if method == "like":
        return _like_search(con, terms, limit)
    if method == "bm25":
        return _bm25(con, terms, ("path", "text"), limit)
    if method == "bm25+ai":
        extra = expand_query(base, model, task, names) if base else []
        return _bm25(con, terms, ("path", "text", "summary", "keywords"), limit, extra)
    raise ValueError(method)


# ---------------- 초안 ----------------
MATERIAL_CHARS = 2600    # 참고 자료 전체 (4,096토큰 기억 길이 안에 지시문·답 1,000토큰과 함께 들어가게)


def build_materials(con, paths, names=(), budget=MATERIAL_CHARS):
    rows = [con.execute("SELECT path, text, summary FROM files WHERE path=?", (p,)).fetchone() for p in paths]
    rows = [r for r in rows if r]
    each = budget // max(len(rows), 1)
    parts = []
    for i, r in enumerate(rows, 1):
        head = f"[자료 {i}] {Path(r['path']).stem}\n"
        summ = (r["summary"] or "").strip()
        body = r["text"] or ""
        room = max(each - len(head) - len(summ), 200)
        parts.append(head + (f"요약: {summ}\n" if summ else "") + "본문 앞부분: " + body[:room])
    return ai.mask_names("\n\n".join(parts), names)


def make_draft(con, base, model, task, paths, names=(), today=None):
    today = today or date.today()
    materials = build_materials(con, paths, names)
    prompt = DRAFT_INSTRUCTION.format(today=f"{today:%Y-%m-%d}", task=task, materials=materials)
    text = ai.ask(base, model, ai.mask_names(prompt, names), system=DRAFT_SYSTEM, max_tokens=1200)
    text = ai.mask_names(text, names).strip()
    # 참고한 파일 목록은 AI 가 아니라 프로그램이 붙임 (선생님이 원본을 확인할 수 있게)
    text += "\n## 참고한 파일\n" + "\n".join(f"- {p}" for p in paths)
    text += "\n※ 업무 데스크가 이 PC 의 AI 로 만든 초안입니다. 날짜·숫자·이름은 원본과 꼭 맞춰 보세요."
    return text, len(prompt)


# ---------------- 참고 자료 간소화 (선생님 요청 2026-11-16 "참고 문서 간소화시켜서 컨텍스트 양 줄여") ----------------
KEY_WORDS = ("제출", "결정", "확정", "선정", "마감", "까지", "일시", "장소", "참석", "대상", "기간", "방법")
DATEISH = re.compile(r"\d{1,4}\s*[./년]\s*\d{1,2}|\d{1,2}:\d{2}|\d+\s*(명|개|점|권|%)")
COMPACT_CHARS = 900       # 참고 자료 전체 (예전 2,600자)
COMPACT_LINE = 90         # 한 줄 최대 글자


def _line_score(line, qgrams):
    g = set(bigrams(line))
    s = len(g & qgrams) * 1.0
    if DATEISH.search(line):
        s += 1.5
    if any(k in line for k in KEY_WORDS):
        s += 1.0
    return s


def compact_materials(con, paths, task, names=(), budget=COMPACT_CHARS, extra_terms=()):
    """파일마다 작업과 관계있는 줄(작업 낱말이 겹치거나 날짜·숫자·'제출' 같은 말이 든 줄)만 골라 짧게.
    AI 요약이 있으면 요약 줄도 후보에 넣음. 원래 순서는 지키고, 같은 줄은 한 번만."""
    qgrams = set()
    for t in list(query_terms(task)) + list(extra_terms):
        qgrams |= set(bigrams(t))
    rows = [con.execute("SELECT path, text, summary FROM files WHERE path=?", (p,)).fetchone() for p in paths]
    rows = [r for r in rows if r]
    each = budget // max(len(rows), 1)
    parts, seen = [], set()
    for i, r in enumerate(rows, 1):
        cands = []
        for n, raw in enumerate(((r["summary"] or "") + "\n" + (r["text"] or "")).splitlines()):
            line = re.sub(r"\s+", " ", raw.strip().lstrip("-•◦·* ")).strip()
            line = re.sub(r"^한\s*줄\s*요약\s*[:：]\s*", "", line)
            key = norm(line)
            if len(key) < 4 or key in seen or line.startswith("(긴 자료라"):
                continue
            seen.add(key)
            cands.append((n, line[:COMPACT_LINE]))
        scored = sorted(cands, key=lambda c: -_line_score(c[1], qgrams))
        head = f"[자료 {i}] {Path(r['path']).stem[:30]}\n"
        room, keep = each - len(head), []
        for n, line in scored:
            if _line_score(line, qgrams) <= 0 or room - len(line) - 1 < 0:
                continue
            keep.append((n, line))
            room -= len(line) + 1
        parts.append(head + "\n".join(l for _, l in sorted(keep)))
    return ai.mask_names("\n".join(parts), names)
