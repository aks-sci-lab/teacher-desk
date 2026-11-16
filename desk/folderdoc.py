"""문서 작성(0.6.1): 지정한 폴더의 파일로 색인을 만들고, 작업 내용으로 관련 파일을 찾아 문서를 만듦.

선생님 요청 2026-11-16 "지정된 폴더의 내용을 ai로 분석해서 색인을 만들고 작업 내용를 입력하면 자동으로 관련 파일 검색해서
문서 파일로 작성" → 시험(tools/trial_folder_index/) 뒤 결정: "색인 기능은 바로 실행하지 않고 누르면 실행되게 하고 나머진 진행해".

- 색인은 **선생님이 [색인 만들기]를 누를 때만** 돈다 (켤 때·예약·감시로 자동으로 돌지 않음). 바뀌지 않은 파일은 건너뜀.
  'AI 요약도 함께'를 고르면 이 PC 의 LM Studio 로 파일마다 요약(0.5.99 나눠 요약, 학생 이름 가림)을 더함.
- 찾기는 규칙: 띄어쓰기를 뺀 두 글자 조각 점수(BM25). 제목(파일 이름)·본문·AI 요약을 함께 봄.
  시험에서 지금 수신함 검색 방식(모든 낱말 포함) 42% → 83%, AI 요약을 더하면 92% (정답이 3위 안).
- 문서 만들기: 고른 파일에서 작업과 관계있는 줄만 골라(간소화, 900자 안쪽) AI 에 보냄.
  서식이 없으면 새 .docx(또는 .hwpx) 초안, 서식(.hwp·.hwpx·.docx)을 주면 그 서식을 그대로 두고 칸만 채움(formfill).
- 파일 내용·색인은 이 PC 의 data/desk.db 에만. AI 는 127.0.0.1 로만, 보내는 글과 받은 답 모두 학생 이름을 가림.
"""

import hashlib
import math
import re
import threading
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from . import ai, docs, docwrite, formfill

SUPPORTED = tuple(docs.SUPPORTED) + (".docx",)
SKIP_PREFIX = ("~$", ".")          # 오피스 임시 잠금 파일, 숨은 파일
MAX_FILES = 3000
SCHEMA = """
CREATE TABLE IF NOT EXISTS folder_files (
    path TEXT PRIMARY KEY,          -- 고른 폴더 안의 상대 경로 (/ 로)
    folder TEXT NOT NULL,           -- 색인할 때의 폴더 (폴더를 바꾸면 예전 것은 지움)
    size INTEGER, mtime REAL, hash TEXT,
    text TEXT, summary TEXT, error TEXT, chars INTEGER,
    indexed_at TEXT, summary_at TEXT
);
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
KEY_WORDS = ("제출", "결정", "확정", "선정", "마감", "까지", "일시", "장소", "참석", "대상", "기간", "방법")
DATEISH = re.compile(r"\d{1,4}\s*[./년]\s*\d{1,2}|\d{1,2}:\d{2}|\d+\s*(명|개|점|권|%)")
COMPACT_CHARS = 900       # 참고 자료 전체 (선생님 요청 "참고 문서 간소화시켜서 컨텍스트 양 줄여", 예전 방식 2,600자)
COMPACT_LINE = 90
FIELD_WEIGHTS = {"path": 3.0, "text": 1.0, "summary": 1.5}


class FolderDocError(Exception):
    """화면에 그대로 보여 줄 한국어 메시지."""


def ensure_schema(db):
    with db.lock:
        db.conn.executescript(SCHEMA)
        db.conn.commit()


# ---------------- 낱말 ----------------
def norm(text):
    """한글·영문·숫자만 남기고 띄어쓰기도 없앰 (pdf 가 '2 학기' 처럼 띄워 읽는 문제를 피함)."""
    return re.sub(r"[^0-9a-z가-힣]", "", (text or "").lower())


def bigrams(text):
    s = norm(text)
    return [s[i:i + 2] for i in range(len(s) - 1)] if len(s) > 1 else ([s] if s else [])


def query_terms(task):
    out = []
    for w in re.split(r"[\s,·/()]+", task or ""):
        w = w.strip()
        for p in PARTICLES:
            if w.endswith(p) and len(w) - len(p) >= 2:
                w = w[: -len(p)]
                break
        if len(norm(w)) >= 2 and w not in STOP_WORDS:
            out.append(w)
    return out


# ---------------- 폴더 읽기 ----------------
def check_folder(folder):
    if not folder:
        raise FolderDocError("먼저 색인할 폴더를 고르세요")
    p = Path(folder)
    if not p.is_dir():
        raise FolderDocError("폴더를 찾을 수 없습니다. 경로를 확인하세요")
    return p


def scan(folder):
    folder = Path(folder)
    found, skipped = [], 0
    for p in sorted(folder.rglob("*")):
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        if p.name.startswith(SKIP_PREFIX) or p.suffix.lower() not in SUPPORTED:
            skipped += 1
            continue
        found.append(p)
        if len(found) >= MAX_FILES:
            break
    return found, skipped


def read_text(p):
    if p.suffix.lower() == ".docx":
        if p.stat().st_size > docs.MAX_BYTES:
            raise docs.DocError("파일이 너무 큽니다 (50MB 초과)")
        try:
            return docs.tidy(docwrite.docx_text(p.read_bytes()))
        except Exception as e:  # zip·xml 오류 종류가 많음
            raise docs.DocError(f"워드 파일을 읽을 수 없습니다 ({type(e).__name__})")
    return docs.read_document(p)


def index_folder(db, folder, summarize=None, progress=None, stop=None):
    """폴더를 색인. summarize(rel, text) 가 있으면 AI 요약도 (요약이 이미 있고 파일이 그대로면 건너뜀)."""
    ensure_schema(db)
    folder = check_folder(folder)
    key = str(folder)
    db.execute("DELETE FROM folder_files WHERE folder != ?", (key,))   # 다른 폴더의 옛 색인
    files, skipped = scan(folder)
    st = {"files": len(files), "skipped": skipped, "new": 0, "unchanged": 0, "errors": 0, "summarized": 0,
          "removed": 0, "limit": len(files) >= MAX_FILES}
    seen = set()
    for n, p in enumerate(files):
        if stop and stop():
            st["stopped"] = True
            break
        rel = p.relative_to(folder).as_posix()
        seen.add(rel)
        if progress:
            progress(n, len(files), rel)
        try:
            stat = p.stat()
            old = db.one("SELECT size, mtime, hash, summary, error, text FROM folder_files WHERE path = ?", (rel,))
            same = old and old["size"] == stat.st_size and old["mtime"] == stat.st_mtime
            if not same:
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                same = old and old["hash"] == h
            else:
                h = old["hash"]
        except OSError:
            continue
        need_summary = summarize and not (old and old["summary"]) and not (old and old["error"])
        if same and not need_summary:
            if old and (old["size"], old["mtime"]) != (stat.st_size, stat.st_mtime):
                db.execute("UPDATE folder_files SET size = ?, mtime = ? WHERE path = ?", (stat.st_size, stat.st_mtime, rel))
            st["unchanged"] += 1
            continue
        if same:
            text, error, summary = old["text"], old["error"], old["summary"]
        else:
            text, error, summary = "", None, None
            try:
                text = read_text(p)
                if not text.strip():
                    error = "글자가 없음 (스캔한 그림 pdf 이면 글을 읽을 수 없음)"
            except docs.DocError as e:
                error = str(e)
            st["new"] += 1
        summary_at = None
        if summarize and text.strip() and not error:
            if stop and stop():
                st["stopped"] = True
            else:
                summary = summarize(rel, text)
                summary_at = datetime.now().strftime("%Y-%m-%d %H:%M")
                st["summarized"] += 1
        if error:
            st["errors"] += 1
        db.execute("INSERT INTO folder_files(path, folder, size, mtime, hash, text, summary, error, chars, indexed_at, "
                   "summary_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET folder = excluded.folder, "
                   "size = excluded.size, mtime = excluded.mtime, hash = excluded.hash, text = excluded.text, "
                   "summary = excluded.summary, error = excluded.error, chars = excluded.chars, "
                   "indexed_at = excluded.indexed_at, summary_at = COALESCE(excluded.summary_at, folder_files.summary_at)",
                   (rel, key, stat.st_size, stat.st_mtime, h, text, summary, error, len(text),
                    datetime.now().strftime("%Y-%m-%d %H:%M"), summary_at))
    if not st.get("stopped") and not st["limit"]:
        for row in db.query("SELECT path FROM folder_files"):
            if row["path"] not in seen:     # 폴더에서 지운 파일은 색인에서도 뺌
                db.execute("DELETE FROM folder_files WHERE path = ?", (row["path"],))
                st["removed"] += 1
    return st


def counts(db):
    ensure_schema(db)
    r = db.one("SELECT COUNT(*) AS n, SUM(summary IS NOT NULL AND summary != '') AS s, SUM(error IS NOT NULL) AS e, "
               "MAX(indexed_at) AS at FROM folder_files")
    return {"files": r["n"] or 0, "summarized": r["s"] or 0, "errors": r["e"] or 0, "last": r["at"]}


# ---------------- 찾기 ----------------
def search(db, task, limit=8):
    """작업 내용과 관계있는 파일 (점수 높은 순)."""
    ensure_schema(db)
    terms = query_terms(task)
    rows = db.query("SELECT path, text, summary FROM folder_files WHERE error IS NULL")
    if not rows or not terms:
        return []
    docs_bg = []
    for r in rows:
        d = {"path": Counter(bigrams(Path(r["path"]).stem.replace("_", " "))),
             "text": Counter(bigrams(r["text"])), "summary": Counter(bigrams(r["summary"]))}
        docs_bg.append((r, d))
    n = len(docs_bg)
    avg = {f: (sum(sum(d[f].values()) for _, d in docs_bg) / n) or 1 for f in FIELD_WEIGHTS}
    grams = Counter()
    for t in terms:
        for g in set(bigrams(t)):
            grams[g] += 1
    df = Counter()
    for _, d in docs_bg:
        present = set().union(*(d[f].keys() for f in FIELD_WEIGHTS))
        for g in grams:
            if g in present:
                df[g] += 1
    k1, b = 1.2, 0.75
    scored = []
    for r, d in docs_bg:
        s = 0.0
        for g, qw in grams.items():
            idf = math.log(1 + (n - df[g] + 0.5) / (df[g] + 0.5))
            tf = 0.0
            for f, w in FIELD_WEIGHTS.items():
                c = d[f].get(g, 0)
                if c:
                    ln = sum(d[f].values()) / avg[f]
                    tf += w * c * (k1 + 1) / (c + k1 * (1 - b + b * ln))
            s += qw * idf * tf
        if s > 0:
            scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    top = scored[0][0] if scored else 1
    return [{"path": r["path"], "score": round(s / top, 2), "summary": ai.summary_line(r["summary"]),
             "snippet": _snippet(r["text"], terms)} for s, r in scored[:limit]]


def _snippet(text, terms, size=80):
    flat = re.sub(r"\s+", " ", text or "")
    for t in terms:
        i = flat.find(t)
        if i >= 0:
            a = max(0, i - 20)
            return ("…" if a else "") + flat[a:a + size]
    return flat[:size]


# ---------------- 참고 자료 간소화 ----------------
def _line_score(line, qgrams):
    s = len(set(bigrams(line)) & qgrams) * 1.0
    if DATEISH.search(line):
        s += 1.5
    if any(k in line for k in KEY_WORDS):
        s += 1.0
    return s


def compact_materials(db, paths, task, names=(), budget=COMPACT_CHARS):
    """파일마다 작업과 관계있는 줄(작업 낱말이 겹치거나 날짜·숫자·'제출' 같은 말이 든 줄)만 골라 짧게.
    AI 요약이 있으면 요약 줄도 후보. 원래 순서는 지키고, 같은 줄은 한 번만. 학생 이름은 가림."""
    qgrams = set()
    for t in query_terms(task):
        qgrams |= set(bigrams(t))
    rows = [db.one("SELECT path, text, summary FROM folder_files WHERE path = ?", (p,)) for p in paths]
    rows = [r for r in rows if r]
    if not rows:
        return ""
    each = budget // len(rows)
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
        head = f"[자료 {i}] {Path(r['path']).stem[:30]}\n"
        room, keep = each - len(head), []
        for n, line in sorted(cands, key=lambda c: -_line_score(c[1], qgrams)):
            if _line_score(line, qgrams) <= 0 or room - len(line) - 1 < 0:
                continue
            keep.append((n, line))
            room -= len(line) + 1
        parts.append(head + "\n".join(line for _, line in sorted(keep)))
    return ai.mask_names("\n".join(parts), names)


# ---------------- 문서 만들기 ----------------
def safe_name(text, limit=40):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", text or "").strip()
    return re.sub(r"\s+", "_", s)[:limit] or "문서"


def make_document(db, config, task, paths, form=None, fmt="docx", out_dir=None, ask=None, today=None):
    """문서 하나를 만들어 out_dir 에 저장. form = (이름, 바이트) 이면 그 서식을 채움. ask(prompt, system, max_tokens)."""
    task = (task or "").strip()
    if not task:
        raise FolderDocError("할 작업을 적어 주세요")
    if not paths:
        raise FolderDocError("참고할 파일을 하나 이상 고르세요")
    names = [s["name"] for s in db.students()]
    materials = compact_materials(db, paths, task, names)
    today = today or date.today()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    res = {"task": task, "files": list(paths), "materials_chars": len(materials)}
    if form:
        fname, data = form
        try:
            f = formfill.Form(fname, data)
        except formfill.FormError as e:
            raise FolderDocError(str(e))
        fields = f.fields()
        if not fields:
            raise FolderDocError("서식에서 채울 칸을 찾지 못했습니다 (표의 이름 칸 옆 빈칸이나 {{자리표시}} 가 필요합니다)")
        prompt = formfill.FILL_INSTRUCTION.format(today=f"{today:%Y-%m-%d}", task=task,
                                                  slots=formfill.slot_lines(fields), materials=materials)
        answer = ai.mask_names(ask(ai.mask_names(prompt, names), formfill.FILL_SYSTEM, 900), names)
        values = formfill.parse_answer(answer, {x["id"] for x in fields})
        if not values:
            raise FolderDocError("AI 답에서 채울 내용을 찾지 못했습니다. 다시 해 보거나 작업 내용을 더 자세히 적어 주세요")
        try:
            filled = f.fill(values)
            data = f.to_bytes()
        except formfill.FormError as e:
            raise FolderDocError(str(e))
        name = f"{safe_name(Path(fname).stem)}_채움_{stamp}{f.ext}"
        (out_dir / name).write_bytes(data)
        labels = {x["id"]: x["label"] for x in fields}
        res.update({"file": name, "prompt_chars": len(prompt), "filled": [
            {"id": k, "label": labels.get(k, ""), "text": values[k]} for k in filled],
            "not_filled": [x["label"] for x in fields if x["id"] not in filled and not x["text"]]})
        return res
    prompt = DRAFT_INSTRUCTION.format(today=f"{today:%Y-%m-%d}", task=task, materials=materials)
    text = ai.mask_names(ask(ai.mask_names(prompt, names), DRAFT_SYSTEM, 1200), names).strip()
    # 참고한 파일 목록은 AI 가 아니라 프로그램이 붙임 (선생님이 원본을 확인할 수 있게)
    text += "\n## 참고한 파일\n" + "\n".join(f"- {p}" for p in paths)
    text += "\n※ 업무 데스크가 이 PC 의 AI 로 만든 초안입니다. 날짜·숫자·이름은 원본과 꼭 맞춰 보세요."
    ext = ".hwpx" if fmt == "hwpx" else ".docx"
    data = docwrite.hwpx_bytes(text, title=task) if ext == ".hwpx" else docwrite.docx_bytes(text)
    name = f"{safe_name(task)}_{stamp}{ext}"
    (out_dir / name).write_bytes(data)
    res.update({"file": name, "prompt_chars": len(prompt), "text": text})
    return res


def list_outputs(out_dir, limit=20):
    d = Path(out_dir)
    if not d.is_dir():
        return []
    files = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in (".docx", ".hwpx", ".hwp")]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "size": p.stat().st_size,
             "at": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")} for p in files[:limit]]


FORM_META = "folderdoc_form"   # 화면에서 올려 둔 서식 (이름·칸) — 파일은 documents/forms/current.<확장자>


def saved_form(db, out_dir):
    """화면 '문서 작성'에 올려 둔 서식 → (이름, 바이트) 또는 None."""
    meta = db.get_meta(FORM_META)
    if not meta:
        return None
    p = Path(out_dir) / "forms" / ("current" + Path(meta["name"]).suffix.lower())
    return (meta["name"], p.read_bytes()) if p.is_file() else None


def names_in_file(p, names):
    """만든 문서에 학생 이름(명단, 두 글자 이상)이 몇 명 들어 있는지. 읽지 못하면 None (그럴 땐 밖으로 보내지 않음).
    AI 답은 이미 가리지만, 서식에 원래 있던 글(작년 내용 등)·참고 파일 이름은 가리지 않으므로 보내기 전에 한 번 더 봄."""
    try:
        text = read_text(Path(p))
    except (docs.DocError, OSError):
        return None
    flat = re.sub(r"\s+", "", text or "")
    return sum(1 for n in {n for n in names if len(n) >= 2} if n in text or n in flat)


def output_path(out_dir, name):
    """받기: 결과 폴더 안의 파일 이름만 (경로를 벗어나지 않게)."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise FolderDocError("파일 이름이 잘못되었습니다")
    p = Path(out_dir) / name
    if not p.is_file() or p.suffix.lower() not in (".docx", ".hwpx", ".hwp"):
        raise FolderDocError("파일이 없습니다")
    return p


# ---------------- 뒤에서 하나씩 ----------------
class Job:
    """색인 만들기·문서 만들기를 뒤에서 한 번에 하나만 (선생님이 눌렀을 때만 시작)."""

    def __init__(self, db, config, out_dir):
        self.db, self.config, self.out_dir = db, config, Path(out_dir)
        self.lock = threading.Lock()
        self.kind = None          # 'index' | 'draft' | None
        self.progress = None
        self.result = None        # 마지막 결과 {kind, ...}
        self.error = None
        self.failed = None
        self.started = None
        self.stop_event = threading.Event()
        self.thread = None

    def status(self):
        with self.lock:
            return {"running": self.kind, "progress": self.progress, "result": self.result, "error": self.error,
                    "failed": self.failed, "started": self.started}

    def _ask_factory(self):
        if not self.config["ai_enabled"]:
            raise FolderDocError("⚙ 설정에서 AI 요약을 먼저 켜세요")
        base = self.config["ai_base"]
        ai.check_local(base)
        model = ai.pick_model(self.config)
        return lambda prompt, system, max_tokens: ai.ask(base, model, prompt, system=system,
                                                          max_tokens=max_tokens, db=self.db), base, model

    def _start(self, kind, fn, on_done=None):
        """on_done(결과 또는 None, 오류 글 또는 None): 끝나면 부름 (텔레그램에서 시작한 일의 답, 0.6.3)."""
        with self.lock:
            if self.kind:
                raise FolderDocError("다른 작업이 진행 중입니다. 끝난 뒤 다시 눌러 주세요")
            self.kind, self.error, self.failed, self.progress = kind, None, None, None
            self.started = datetime.now().strftime("%H:%M:%S")
            self.stop_event.clear()

        def run():
            result = error = None
            try:
                out = fn()
                result = {"kind": kind, **out}
                with self.lock:
                    self.result = result
            except (FolderDocError, ai.AIError) as e:
                error = str(e)
                with self.lock:
                    self.error, self.failed = error, kind
            except Exception as e:  # 뒤에서 도는 일이 조용히 죽지 않게
                error = f"{'색인' if kind == 'index' else '문서 만들기'} 중 오류: {type(e).__name__}"
                with self.lock:
                    self.failed = kind
                    self.error = error
                self.db.log(f"문서 작성 {kind} 오류: {type(e).__name__}: {e}", "error")
            finally:
                with self.lock:
                    self.kind = None
                    self.progress = None
            if on_done:
                try:
                    on_done(result, error)
                except Exception as e:   # 답을 보내지 못해도 결과 파일은 그대로
                    self.db.log(f"문서 작성 결과 알림 오류: {type(e).__name__}", "error")

        self.thread = threading.Thread(target=run, name=f"folderdoc-{kind}", daemon=True)
        self.thread.start()

    def busy(self):
        with self.lock:
            return self.kind

    def start_index(self, with_ai=False, on_done=None):
        folder = str(check_folder(self.config["folderdoc_folder"]))
        summarize = None
        if with_ai:
            ask, base, model = self._ask_factory()
            names = [s["name"] for s in self.db.students()]
            size = ai.chunk_size(self.config)

            def summarize(rel, text):
                it = {"source": "folder", "title": rel}
                body = re.sub(r"[ \t]+", " ", text).strip()
                try:
                    return ai.summarize_item(base, model, it, body, names, size, db=self.db, stop=self.stop_event.is_set)
                except ai.AIError as e:
                    if any(str(e).startswith(x) for x in ai.NOT_READY) or self.stop_event.is_set():
                        raise
                    return None   # 이 파일만 건너뜀

        def progress(done, total, rel):
            with self.lock:
                self.progress = {"done": done, "total": total, "file": rel}

        def fn():
            t0 = time.monotonic()
            st = index_folder(self.db, folder, summarize, progress, self.stop_event.is_set)
            st["seconds"] = round(time.monotonic() - t0, 1)
            self.db.log(f"문서 작성 폴더 색인: 파일 {st['files']}개 (새로 읽음 {st['new']}, AI 요약 {st['summarized']})")
            return st
        self._start("index", fn, on_done)

    def start_draft(self, task, paths, form=None, fmt="docx", on_done=None):
        ask, _, _ = self._ask_factory()

        def fn():
            with self.lock:
                self.progress = {"step": "AI 가 쓰는 중"}
            return make_document(self.db, self.config, task, paths, form, fmt, self.out_dir, ask)
        self._start("draft", fn, on_done)

    def stop(self):
        self.stop_event.set()
