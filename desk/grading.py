"""서술형 평가·수행평가 손글씨 채점 (0.5.80).

흐름: 평가 만들기(문항·모범 답안·채점 기준·만점) → 답안지 사진·스캔 올리기 → 이 PC 의 LM Studio 비전 모델이
손글씨를 문항별로 옮겨 적음(OCR) → 선생님이 옮겨 적은 글을 확인·고침 → (선택) AI 가 채점 기준으로 점수를 제안
→ 선생님이 점수를 정함 → CSV(엑셀)로 내보내기.

- 답안지 그림·학생 답·점수는 이 PC 의 data/ 에만 둡니다. AI 는 이 PC(127.0.0.1)의 LM Studio 로만 부릅니다
  (`ai.check_local`). 인터넷 AI 로는 보내지 않습니다.
- AI 가 읽은 글·제안 점수는 참고용입니다. 점수는 선생님이 정한 값만 내보냅니다.
- 점수 제안에 보내는 글에서는 학생 명단의 이름을 가립니다.
"""

import base64
import binascii
import csv
import io
import json
import re
import shutil
import threading
import time
from collections import deque

from . import ai
from .db import now_str

KINDS = {"essay": "서술형 평가", "performance": "수행평가"}
MAX_QUESTIONS = 30
MAX_PAGES = 6                 # 한 학생 답안지 최대 쪽 수 (한 번에 AI 에 보내는 그림 수)
MAX_IMAGE = 8 * 1024 * 1024   # 그림 한 장 최대 크기
MAX_SHEETS_PER_UPLOAD = 60
OCR_TOKENS = 3000
SCORE_TOKENS = 400

OCR_SYSTEM = ("너는 학생이 손으로 쓴 답안지를 보이는 그대로 옮겨 적는 도우미다. "
              "글을 고치거나 채점하거나 설명하지 않는다. 보이지 않는 내용은 지어내지 않는다.")
SCORE_SYSTEM = ("너는 한국 중·고등학교 서술형 평가 채점을 돕는 보조 채점자다. 주어진 채점 기준만으로 점수를 제안하고 "
                "이유를 짧게 쓴다. 최종 점수는 교사가 정한다. 학생 답은 손글씨를 옮겨 적은 것이라 오타가 있을 수 있다.")


class GradeError(Exception):
    """화면에 그대로 보여 줄 안내."""


# ---------------------------------------------------------------- 평가·문항
def _clean_text(v, name, limit):
    if v is None:
        return ""
    if not isinstance(v, str):
        raise GradeError(f"{name}은(는) 글자여야 합니다")
    v = v.strip()
    if len(v) > limit:
        raise GradeError(f"{name}이(가) 너무 깁니다 ({limit}자 이하)")
    return v


def clean_questions(raw):
    """화면에서 온 문항 목록 검사 → [{number, prompt, answer, rubric, max_score}]."""
    if not isinstance(raw, list) or not raw:
        raise GradeError("문항을 하나 이상 넣으세요")
    if len(raw) > MAX_QUESTIONS:
        raise GradeError(f"문항은 {MAX_QUESTIONS}개까지 넣을 수 있습니다")
    out, seen = [], set()
    for i, q in enumerate(raw, 1):
        if not isinstance(q, dict):
            raise GradeError("문항 형식이 잘못되었습니다")
        number = q.get("number", i)
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 99:
            raise GradeError("문항 번호는 1~99 사이의 정수여야 합니다")
        if number in seen:
            raise GradeError(f"{number}번 문항이 두 번 있습니다")
        seen.add(number)
        score = q.get("max_score", 0)
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1000:
            raise GradeError(f"{number}번 문항의 만점은 0~1000 사이의 숫자여야 합니다")
        out.append({"number": number, "max_score": float(score),
                    "prompt": _clean_text(q.get("prompt"), f"{number}번 문제", 2000),
                    "answer": _clean_text(q.get("answer"), f"{number}번 모범 답안", 4000),
                    "rubric": _clean_text(q.get("rubric"), f"{number}번 채점 기준", 4000)})
    return sorted(out, key=lambda q: q["number"])


def _clean_task(body):
    title = _clean_text(body.get("title"), "평가 이름", 100)
    if not title:
        raise GradeError("평가 이름을 넣으세요")
    kind = body.get("kind", "essay")
    if kind not in KINDS:
        raise GradeError("평가 종류가 잘못되었습니다")
    return title, kind, _clean_text(body.get("note"), "메모", 2000)


def create_task(db, body):
    title, kind, note = _clean_task(body)
    questions = clean_questions(body.get("questions"))
    with db.lock:
        try:
            cur = db.conn.execute("INSERT INTO grade_tasks (title, kind, note, created_at) VALUES (?, ?, ?, ?)",
                                  (title, kind, note, now_str()))
            tid = cur.lastrowid
            db.conn.executemany(
                "INSERT INTO grade_questions (task_id, number, prompt, answer, rubric, max_score) VALUES (?, ?, ?, ?, ?, ?)",
                [(tid, q["number"], q["prompt"], q["answer"], q["rubric"], q["max_score"]) for q in questions])
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise
    db.log(f"채점: 평가 '{title}' 를 만들었습니다 (문항 {len(questions)}개)")
    return task(db, tid)


def update_task(db, tid, body):
    """이름·종류·메모·문항 고치기. 문항은 번호로 맞춰 고치고(답·점수 유지), 없어진 번호는 지움."""
    get_task_row(db, tid)
    title, kind, note = _clean_task(body)
    questions = clean_questions(body.get("questions"))
    with db.lock:
        try:
            db.conn.execute("UPDATE grade_tasks SET title = ?, kind = ?, note = ? WHERE id = ?", (title, kind, note, tid))
            keep = [q["number"] for q in questions]
            db.conn.execute(f"DELETE FROM grade_questions WHERE task_id = ? AND number NOT IN ({','.join('?' * len(keep))})",
                            (tid, *keep))
            for q in questions:
                db.conn.execute(
                    "INSERT INTO grade_questions (task_id, number, prompt, answer, rubric, max_score) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(task_id, number) DO UPDATE SET prompt = excluded.prompt, answer = excluded.answer, "
                    "rubric = excluded.rubric, max_score = excluded.max_score",
                    (tid, q["number"], q["prompt"], q["answer"], q["rubric"], q["max_score"]))
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise
    return task(db, tid)


def get_task_row(db, tid):
    row = db.one("SELECT * FROM grade_tasks WHERE id = ?", (tid,))
    if not row:
        raise GradeError("없는 평가입니다")
    return row


def questions(db, tid):
    return db.query("SELECT * FROM grade_questions WHERE task_id = ? ORDER BY number", (tid,))


def list_tasks(db):
    return db.query(
        "SELECT t.*, (SELECT COUNT(*) FROM grade_questions q WHERE q.task_id = t.id) AS questions, "
        "(SELECT COUNT(*) FROM grade_sheets s WHERE s.task_id = t.id) AS sheets, "
        "(SELECT COUNT(*) FROM grade_sheets s WHERE s.task_id = t.id AND s.ocr_status = 'done') AS read "
        "FROM grade_tasks t ORDER BY t.id DESC")


def task(db, tid):
    """평가 하나: 문항·답안지(문항별 답·점수)."""
    out = get_task_row(db, tid)
    out["questions"] = questions(db, tid)
    sheets = db.query("SELECT id, task_id, student_number, student_name, images, ocr_status, ocr_error, ocr_model, "
                      "ocr_at, ocr_raw, created_at FROM grade_sheets WHERE task_id = ? "
                      "ORDER BY student_number IS NULL, student_number, id", (tid,))
    answers = db.query("SELECT a.* FROM grade_answers a JOIN grade_sheets s ON s.id = a.sheet_id WHERE s.task_id = ?", (tid,))
    by_sheet = {}
    for a in answers:
        by_sheet.setdefault(a["sheet_id"], {})[a["question_id"]] = a
    for s in sheets:
        s["pages"] = len(json.loads(s.pop("images") or "[]"))
        s["answers"] = by_sheet.get(s["id"], {})
        done = [a["score"] for a in s["answers"].values() if a["score"] is not None]
        s["total"] = sum(done) if done else None
        s["scored"] = len(done)
    out["sheets"] = sheets
    out["max_total"] = sum(q["max_score"] for q in out["questions"])
    return out


def delete_task(db, data_dir, tid):
    row = get_task_row(db, tid)
    db.execute("DELETE FROM grade_tasks WHERE id = ?", (tid,))
    shutil.rmtree(_task_dir(data_dir, tid), ignore_errors=True)
    db.log(f"채점: 평가 '{row['title']}' 와 답안지 그림을 지웠습니다")


# ---------------------------------------------------------------- 답안지 그림
def _task_dir(data_dir, tid):
    return data_dir / "grading" / str(int(tid))


def image_kind(data):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def _decode(b64):
    if not isinstance(b64, str):
        raise GradeError("파일 내용이 잘못되었습니다")
    if b64.startswith("data:"):
        b64 = b64.partition(",")[2]
    try:
        return base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise GradeError("파일 내용을 읽을 수 없습니다")


def pdf_pages(data):
    """스캔한 PDF → 쪽마다 가장 큰 JPEG 그림 (복사기·스캐너 PDF 는 보통 쪽마다 JPEG 한 장)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise GradeError("PDF 를 읽는 부품(pypdf)이 없습니다. 답안지를 JPG 로 저장해 올려 주세요.")
    try:
        reader = PdfReader(io.BytesIO(data))
        out = []
        for n, page in enumerate(reader.pages, 1):
            best = None
            res = page.get("/Resources") or {}
            xobjs = res.get_object().get("/XObject") if hasattr(res, "get_object") else None
            for name in (xobjs.get_object() if xobjs else {}):
                obj = xobjs.get_object()[name].get_object()
                if obj.get("/Subtype") != "/Image":
                    continue
                filt = obj.get("/Filter")
                filters = [str(x) for x in filt] if isinstance(filt, list) else [str(filt)] if filt else []
                if not filters or filters[-1] != "/DCTDecode":
                    continue
                raw = obj.get_data()
                if image_kind(raw) == "image/jpeg" and (best is None or len(raw) > len(best)):
                    best = raw
            if best is None:
                raise GradeError(f"PDF {n}쪽에서 답안지 그림을 꺼내지 못했습니다. 스캐너에서 JPG 로 저장하거나 사진으로 올려 주세요.")
            out.append(best)
        return out
    except GradeError:
        raise
    except Exception:
        raise GradeError("PDF 를 읽지 못했습니다. 스캐너에서 JPG 로 저장하거나 사진으로 올려 주세요.")


def file_pages(f):
    """올린 파일 하나 {name, data(base64)} → 그림(바이트) 목록."""
    if not isinstance(f, dict):
        raise GradeError("파일 형식이 잘못되었습니다")
    data = _decode(f.get("data"))
    if data[:5] == b"%PDF-":
        pages = pdf_pages(data)
    elif image_kind(data):
        pages = [data]
    else:
        raise GradeError(f"'{str(f.get('name') or '파일')[:60]}' 은(는) JPG·PNG 그림이나 PDF 가 아닙니다")
    for p in pages:
        if len(p) > MAX_IMAGE:
            raise GradeError("그림 한 장이 너무 큽니다 (8MB 이하)")
    return pages


def _roster_after(db, number):
    """번호 number 부터 차례로 학생 (명단이 없으면 번호만)."""
    rows = sorted(db.students(), key=lambda s: s["number"])
    names = {s["number"]: s["name"] for s in rows}
    nums = [s["number"] for s in rows if s["number"] >= number]
    while True:
        if nums:
            n = nums.pop(0)
            yield n, names[n]
            number = n + 1
        else:
            yield number, None
            number += 1


def add_sheets(db, data_dir, tid, body):
    """답안지 올리기: files 의 쪽을 pages_per_sheet 장씩 묶어 한 학생 답안지로. 학생은 start_number 부터 명단 순서."""
    get_task_row(db, tid)
    files = body.get("files")
    if not isinstance(files, list) or not files:
        raise GradeError("올릴 답안지 파일을 고르세요")
    per = body.get("pages_per_sheet", 1)
    if isinstance(per, bool) or not isinstance(per, int) or not 1 <= per <= MAX_PAGES:
        raise GradeError(f"한 학생 쪽 수는 1~{MAX_PAGES} 사이여야 합니다")
    start = body.get("start_number", 1)
    if isinstance(start, bool) or not isinstance(start, int) or not 0 <= start <= 999:
        raise GradeError("시작 번호가 잘못되었습니다")
    pages = [p for f in files for p in file_pages(f)]
    if len(pages) % per:
        raise GradeError(f"쪽 수({len(pages)}쪽)가 한 학생 {per}쪽으로 나누어떨어지지 않습니다")
    groups = [pages[i:i + per] for i in range(0, len(pages), per)]
    if len(groups) > MAX_SHEETS_PER_UPLOAD:
        raise GradeError(f"한 번에 {MAX_SHEETS_PER_UPLOAD}명까지 올릴 수 있습니다")
    folder = _task_dir(data_dir, tid)
    folder.mkdir(parents=True, exist_ok=True)
    students = _roster_after(db, start)
    created, next_number = [], start
    for group in groups:
        number, name = next(students)
        next_number = number + 1
        sid = db.execute("INSERT INTO grade_sheets (task_id, student_number, student_name, images, created_at) "
                         "VALUES (?, ?, ?, '[]', ?)", (tid, number, name, now_str()))
        names = []
        for i, data in enumerate(group, 1):
            ext = "png" if image_kind(data) == "image/png" else "jpg"
            fname = f"{sid}-{i}.{ext}"
            (folder / fname).write_bytes(data)
            names.append(fname)
        db.execute("UPDATE grade_sheets SET images = ? WHERE id = ?", (json.dumps(names), sid))
        created.append(sid)
    db.log(f"채점: 답안지 {len(created)}장을 올렸습니다")
    return {"created": created, "next_number": next_number}


def sheet_row(db, sid):
    row = db.one("SELECT * FROM grade_sheets WHERE id = ?", (sid,))
    if not row:
        raise GradeError("없는 답안지입니다")
    return row


def sheet_image(db, data_dir, sid, n):
    row = sheet_row(db, sid)
    names = json.loads(row["images"] or "[]")
    if not 1 <= n <= len(names):
        raise GradeError("없는 쪽입니다")
    # 파일 이름은 서버가 만든 것만 (sid-n.jpg)
    if not re.fullmatch(r"\d+-\d+\.(jpg|png)", names[n - 1]):
        raise GradeError("그림 파일 이름이 잘못되었습니다")
    path = _task_dir(data_dir, row["task_id"]) / names[n - 1]
    if not path.is_file():
        raise GradeError("그림 파일이 없습니다")
    data = path.read_bytes()
    return data, image_kind(data) or "application/octet-stream"


def update_sheet(db, sid, body):
    sheet_row(db, sid)
    number = body.get("student_number")
    if number is not None and (isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= 999):
        raise GradeError("번호가 잘못되었습니다")
    name = _clean_text(body.get("student_name"), "이름", 20) or None
    if number is not None and name is None:   # 번호만 바꾸면 명단의 이름으로
        name = next((s["name"] for s in db.students() if s["number"] == number), None)
    db.execute("UPDATE grade_sheets SET student_number = ?, student_name = ? WHERE id = ?", (number, name, sid))
    return {"ok": True}


def delete_sheet(db, data_dir, sid):
    row = sheet_row(db, sid)
    db.execute("DELETE FROM grade_sheets WHERE id = ?", (sid,))
    for name in json.loads(row["images"] or "[]"):
        if re.fullmatch(r"\d+-\d+\.(jpg|png)", name):
            (_task_dir(data_dir, row["task_id"]) / name).unlink(missing_ok=True)
    return {"ok": True}


# ---------------------------------------------------------------- 손글씨 읽기 (OCR)
def ocr_prompt(qs):
    lines = []
    for q in qs:
        hint = re.sub(r"\s+", " ", q["prompt"] or "")[:80]
        lines.append(f"- {q['number']}번" + (f": {hint}" if hint else ""))
    form = "\n".join(f"[{q['number']}] (학생이 쓴 답)" for q in qs)
    return ("이 답안지 그림에서 학생이 손으로 쓴 답을 문항별로 그대로 옮겨 적어 줘.\n\n"
            "규칙:\n"
            "- 맞춤법·띄어쓰기·문장을 고치지 말고 쓴 그대로 적기\n"
            "- 알아볼 수 없는 글자는 [?] 로 적기\n"
            "- 줄을 그어 지운 글자는 빼기\n"
            "- 인쇄된 문제 글·보기·이름 칸은 옮기지 말고 학생이 쓴 답만 적기\n"
            "- 그림·그래프·표로 답했으면 [그림: 간단한 설명] 으로 적기\n"
            "- 답이 없는 문항은 [빈칸]\n"
            "- 아래 형식으로만 답하기 (앞뒤 다른 말 없이)\n\n"
            f"{form}\n\n문항:\n" + "\n".join(lines))


P_MARK = re.compile(r"^\s*\[(\d{1,2})\]\s?", re.M)


def parse_ocr(text, numbers):
    """'[1] 답\\n[2] 답' → {1: '답', 2: '답'}. 표시가 없고 문항이 하나면 전체가 그 답."""
    text = (text or "").strip()
    text = re.sub(r"^```\w*\n?|```$", "", text).strip()
    marks = list(P_MARK.finditer(text))
    out = {}
    for i, m in enumerate(marks):
        n = int(m.group(1))
        if n not in numbers or n in out:
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        part = text[m.end():end].strip()
        out[n] = "" if part in ("[빈칸]", "(학생이 쓴 답)") else part
    if not marks and len(numbers) == 1:
        out[numbers[0]] = text
    return out


def ocr_model(config):
    return config["grade_ocr_model"] or ai.pick_model(config)


def data_url(data):
    return f"data:{image_kind(data) or 'image/jpeg'};base64," + base64.b64encode(data).decode("ascii")


def read_sheet(db, config, data_dir, sid, timeout=ai.TIMEOUT):
    """답안지 한 장을 비전 모델로 읽어 문항별 답에 넣음. 선생님이 고친 답은 덮어쓰지 않음."""
    row = sheet_row(db, sid)
    qs = questions(db, row["task_id"])
    if not qs:
        raise GradeError("문항이 없습니다")
    ai.check_local(config["ai_base"])
    model = ocr_model(config)
    images = [data_url(sheet_image(db, data_dir, sid, n)[0]) for n in range(1, len(json.loads(row["images"])) + 1)]
    text = ai.ask(config["ai_base"], model, ocr_prompt(qs), timeout, system=OCR_SYSTEM,
                  max_tokens=OCR_TOKENS, db=db, images=images)
    parsed = parse_ocr(text, [q["number"] for q in qs])
    now = now_str()
    with db.lock:
        try:
            for q in qs:
                got = parsed.get(q["number"])
                if got is None:
                    continue
                db.conn.execute(
                    "INSERT INTO grade_answers (sheet_id, question_id, ocr_text, text, updated_at) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(sheet_id, question_id) DO UPDATE SET ocr_text = excluded.ocr_text, "
                    "text = CASE WHEN grade_answers.edited THEN grade_answers.text ELSE excluded.text END, "
                    "updated_at = excluded.updated_at", (sid, q["id"], got, got, now))
            db.conn.execute("UPDATE grade_sheets SET ocr_status = 'done', ocr_error = NULL, ocr_model = ?, ocr_at = ?, "
                            "ocr_raw = ? WHERE id = ?", (model, now, text, sid))
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise
    return {"found": len(parsed), "questions": len(qs)}


# ---------------------------------------------------------------- 점수 제안
def fmt_score(v):
    return str(int(v)) if float(v).is_integer() else str(v)


def score_prompt(q, text, names=()):
    return ai.mask_names(
        f"{q['number']}번 문항 (만점 {fmt_score(q['max_score'])}점)\n"
        f"문제: {q['prompt'] or '(적지 않음)'}\n"
        f"모범 답안: {q['answer'] or '(적지 않음)'}\n"
        f"채점 기준:\n{q['rubric'] or '(적지 않음 — 모범 답안과 비교)'}\n\n"
        f"학생 답:\n{text}\n\n"
        "아래 형식으로만 답해 줘 (앞뒤 다른 말 없이):\n"
        f"점수: 0~{fmt_score(q['max_score'])} 사이 숫자\n"
        "이유: 채점 기준의 어느 항목을 맞히고 놓쳤는지 한두 문장", names)


P_SCORE = re.compile(r"점수\s*[:：]?\s*(\d+(?:\.\d+)?)")
P_REASON = re.compile(r"이유\s*[:：]\s*(.+)", re.S)


def parse_score(text, max_score):
    m = P_SCORE.search(text or "")
    if not m:
        raise GradeError("AI 답에서 점수를 찾지 못했습니다")
    score = min(max(float(m.group(1)), 0.0), float(max_score))
    r = P_REASON.search(text)
    reason = re.sub(r"\s+", " ", (r.group(1) if r else text[m.end():])).strip()[:500]
    return score, reason


def suggest_score(db, config, aid, timeout=ai.TIMEOUT):
    a = db.one("SELECT a.*, q.number, q.prompt, q.answer, q.rubric, q.max_score FROM grade_answers a "
               "JOIN grade_questions q ON q.id = a.question_id WHERE a.id = ?", (aid,))
    if not a:
        raise GradeError("없는 답입니다")
    text = (a["text"] or "").strip()
    if not text:
        score, reason, model = 0.0, "답이 비어 있음", None
    else:
        ai.check_local(config["ai_base"])
        model = ai.pick_model(config)
        names = [s["name"] for s in db.students()]
        reply = ai.ask(config["ai_base"], model, score_prompt(a, text, names), timeout,
                       system=SCORE_SYSTEM, max_tokens=SCORE_TOKENS, db=db)
        score, reason = parse_score(reply, a["max_score"])
        reason = ai.mask_names(reason, names)
    db.execute("UPDATE grade_answers SET ai_score = ?, ai_reason = ?, ai_model = ?, ai_status = 'done', ai_error = NULL, "
               "updated_at = ? WHERE id = ?", (score, reason, model, now_str(), aid))
    return {"ai_score": score, "ai_reason": reason}


# ---------------------------------------------------------------- 선생님이 고치기
def update_answer(db, body, aid=None, sheet_id=None, question_id=None):
    """답 글·점수·메모 저장. accept_ai: AI 제안 점수를 그대로 점수로."""
    if aid is None:   # 아직 답 줄이 없는 칸(AI 가 못 읽은 문항)은 새로 만듦
        s = sheet_row(db, sheet_id)
        q = db.one("SELECT * FROM grade_questions WHERE id = ? AND task_id = ?", (question_id, s["task_id"]))
        if not q:
            raise GradeError("없는 문항입니다")
        db.execute("INSERT OR IGNORE INTO grade_answers (sheet_id, question_id, updated_at) VALUES (?, ?, ?)",
                   (sheet_id, question_id, now_str()))
        aid = db.one("SELECT id FROM grade_answers WHERE sheet_id = ? AND question_id = ?", (sheet_id, question_id))["id"]
    a = db.one("SELECT a.*, q.max_score FROM grade_answers a JOIN grade_questions q ON q.id = a.question_id "
               "WHERE a.id = ?", (aid,))
    if not a:
        raise GradeError("없는 답입니다")
    sets, args = [], []
    if "text" in body:
        sets += ["text = ?", "edited = 1"]
        args.append(_clean_text(body["text"], "답", 8000))
    if body.get("accept_ai"):
        if a["ai_score"] is None:
            raise GradeError("AI 제안 점수가 없습니다")
        sets.append("score = ?")
        args.append(a["ai_score"])
    elif "score" in body:
        v = body["score"]
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= a["max_score"]):
            raise GradeError(f"점수는 0~{fmt_score(a['max_score'])} 사이여야 합니다")
        sets.append("score = ?")
        args.append(None if v is None else float(v))
    if "comment" in body:
        sets.append("comment = ?")
        args.append(_clean_text(body["comment"], "메모", 1000))
    if sets:
        db.execute(f"UPDATE grade_answers SET {', '.join(sets)}, updated_at = ? WHERE id = ?", (*args, now_str(), aid))
    return db.one("SELECT * FROM grade_answers WHERE id = ?", (aid,))


# ---------------------------------------------------------------- 내보내기
def export_csv(db, tid):
    t = task(db, tid)
    qs = t["questions"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["번호", "이름"] + [f"{q['number']}번({fmt_score(q['max_score'])})" for q in qs]
               + [f"합계({fmt_score(t['max_total'])})", "채점 안 한 문항"]
               + [f"{q['number']}번 메모" for q in qs])
    for s in t["sheets"]:
        by_q = {a["question_id"]: a for a in s["answers"].values()}
        scores = [by_q.get(q["id"], {}).get("score") for q in qs]
        missing = [str(q["number"]) for q, v in zip(qs, scores) if v is None]
        w.writerow([s["student_number"] if s["student_number"] is not None else "", s["student_name"] or ""]
                   + ["" if v is None else fmt_score(v) for v in scores]
                   + [fmt_score(s["total"]) if s["total"] is not None else "", ",".join(missing)]
                   + [(by_q.get(q["id"], {}).get("comment") or "") for q in qs])
    # 엑셀에서 한글이 깨지지 않게 BOM
    return buf.getvalue().encode("utf-8-sig"), t["title"]


# ---------------------------------------------------------------- 뒤에서 하나씩
class Job:
    """손글씨 읽기·점수 제안은 한 장에 수십 초씩 걸려 뒤에서 하나씩. 상태는 DB 칸(ocr_status·ai_status)에."""

    def __init__(self, db, config, busy=None):
        self.db, self.config, self.busy = db, config, busy or (lambda: False)
        self.queue = deque()
        self.lock = threading.Lock()
        self.thread = None
        # 꺼지기 전에 기다리던 일은 '아직' 으로 (다시 누르면 됨)
        db.execute("UPDATE grade_sheets SET ocr_status = '' WHERE ocr_status IN ('waiting', 'running')")
        db.execute("UPDATE grade_answers SET ai_status = '' WHERE ai_status IN ('waiting', 'running')")

    def add(self, kind, ids):
        table, col = ("grade_sheets", "ocr_status") if kind == "ocr" else ("grade_answers", "ai_status")
        added = 0
        with self.lock:
            queued = {(k, i) for k, i in self.queue}
            for i in ids:
                if (kind, i) in queued:
                    continue
                self.db.execute(f"UPDATE {table} SET {col} = 'waiting' WHERE id = ?", (i,))
                self.queue.append((kind, i))
                added += 1
            if added and not (self.thread and self.thread.is_alive()):
                self.thread = threading.Thread(target=self._worker, daemon=True, name="grading")
                self.thread.start()
        return added

    def pending(self):
        with self.lock:
            return len(self.queue)

    def _worker(self):
        while True:
            if self.busy():   # 브리핑 요약이 도는 중이면 LM Studio 를 기다림
                time.sleep(2)
                continue
            with self.lock:
                if not self.queue:
                    return
                kind, i = self.queue.popleft()
            self._run(kind, i)

    def _run(self, kind, i):
        table, col, err = (("grade_sheets", "ocr_status", "ocr_error") if kind == "ocr"
                           else ("grade_answers", "ai_status", "ai_error"))
        if not self.db.one(f"SELECT id FROM {table} WHERE id = ?", (i,)):
            return   # 그사이 지움
        self.db.execute(f"UPDATE {table} SET {col} = 'running' WHERE id = ?", (i,))
        try:
            if kind == "ocr":
                read_sheet(self.db, self.config, self.config.data_dir, i)
            else:
                suggest_score(self.db, self.config, i)
        except (ai.AIError, GradeError) as e:
            self.db.execute(f"UPDATE {table} SET {col} = 'error', {err} = ? WHERE id = ?", (str(e)[:300], i))
        except Exception as e:   # 예상 못 한 오류도 화면에 알림
            self.db.execute(f"UPDATE {table} SET {col} = 'error', {err} = ? WHERE id = ?", (type(e).__name__, i))
            self.db.log(f"채점 오류: {type(e).__name__}: {e}", "error")
