"""학교 실제 서식(.hwp)에 맞춰 채우기 + 참고 자료 간소화 시험.

    python tools/trial_folder_index/run_hwp_trial.py --form 서식.hwp --out 결과폴더 [--base http://127.0.0.1:1234/v1]

- 서식 파일의 문단 자리를 읽어, 채울 자리(표의 내용 칸, '(    )' 빈 괄호)만 골라 AI 에 보낸다.
- 참고 자료는 예전 방식(요약+본문 앞부분, 2,600자 안쪽)과 간소화 방식(작업과 관계있는 줄만, 900자 안쪽)을 비교한다.
- --base 가 없으면 AI 답 대신 미리 정해 둔 답(SCRIPTED)으로 채워 '양식이 그대로인지'만 확인한다.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

from desk import ai, docs          # noqa: E402
from desk.cfb import CompoundFile  # noqa: E402
import folderindex as fi           # noqa: E402
import hwpfill                     # noqa: E402
import make_samples                # noqa: E402
import writers                     # noqa: E402

TASK = "2026학년도 화학 교과서 선정 교과협의회 회의록 작성 (9월 22일 과학실 2, 참석 김과학 이화학)"
EXTRA_DOCS = {
    "공문/2026_교과용도서_선정_계획_안내.hwpx": """# 2027학년도 사용 교과용도서 선정 계획 안내
1. 관련: 교과용도서 선정 업무 처리 지침
2. 2027학년도에 사용할 검정 교과서 선정 일정을 아래와 같이 안내합니다.
## 추진 일정
- 교과협의회 개최 및 후보 도서 심사: 2026. 9. 22.(화)까지
- 학교운영위원회 심의: 2026. 10. 1.(목)
- 선정 결과 나이스 등록: 2026. 10. 8.(목)까지
## 유의 사항
- 교과협의회는 학교 '교과서 도서 선정기준 평가표'(10개 항목, 100점)로 심사
- 출판사·총판 직원의 교내 출입 및 홍보물 수령 금지 (불공정 행위 예방)
- 회의록은 대표교사가 작성하여 교무부에 제출
""",
    "과학과/2026_화학_교과서_후보목록.xlsx": [
        ["번호", "출판사", "저자 대표", "비고"],
        ["1", "가나출판", "가○○", "전시본 도착"],
        ["2", "다라교육", "다○○", "전시본 도착"],
        ["3", "마바서적", "마○○", "전시본 도착"],
        ["4", "사아에듀", "사○○", "디지털본만"],
        ["5", "자차미디어", "자○○", "전시본 도착"],
    ],
}
# 간소화해도 남아야 할 사실 (참고 자료에서)
FACTS = ["9. 22.", "10. 1.", "10. 8.", "평가표", "100점", "출입", "가나출판", "자차미디어", "대표교사"]

# 진짜 AI 가 없을 때 쓰는 정해 둔 답 (양식 보존 시험용. 내용 품질은 보지 않음)
SCRIPTED = {
    "안건": "(  화학  )교과서 선정",
    "일시": "2026년   9 월   22 일",
    "장소": "과학실 2",
    "참석자": "      김과학(인),  이화학(인)",
    "평가표제목": "(      화학      )교과서 도서 선정기준 평가표",
    "심사방법": "  화학 교사 2명이 후보 교과서 5종을 모두 심사하고, 평가표 점수를 합산하여 순위를 정하기로 결정함.",
    "작성자": "    김과학     (인)",
}
FILL_INSTRUCTION = """[서식 채우기] 교사가 할 작업: "{task}"
아래는 학교 서식의 채울 자리다. 각 자리의 지금 글은 작년 내용이며 길이·모양의 본보기다.
참고 자료와 작업 내용에 있는 사실만으로, 바꿀 자리만 이 모양으로 써 줘 (앞뒤 인사말 없이, 한 자리 한 줄):
P번호: 새 글
그대로 둘 자리는 쓰지 않는다. 자료에 없는 것은 '(확인 필요)'.

서식 자리:
{slots}

참고 자료:
{materials}
"""


def fillable(form):
    """채울 자리: 바깥 표의 내용 칸(이름 칸 제외) + 빈 괄호 '(    )' 가 있는 문단."""
    right = {(c["section"], c["table"], c["row"]): max(x["col"] for x in form.cells
             if (x["section"], x["table"], x["row"]) == (c["section"], c["table"], c["row"])) for c in form.cells}

    def is_label(c):   # 이름 칸: 맨 왼쪽이거나, 오른쪽에 칸이 더 있는 숫자 없는 짧은 글 (예: '장  소')
        t = c["text"].replace(" ", "")
        has_right = c["col"] < right[(c["section"], c["table"], c["row"])]
        return c["col"] == 0 or (has_right and 0 < len(t) <= 6 and not re.search(r"\d", t))
    out = []
    for s in form.slots:
        if not s.editable:
            continue
        if s.cell is None and s.text.strip() and s is next(x for x in form.slots if x.text.strip()):
            out.append(s)   # 맨 위 제목
            continue
        blank = re.search(r"\(\s{4,}\)", s.text)
        c = s.cell
        top = c is not None and c["level"] == min(x["level"] for x in form.cells)
        if blank or (top and not is_label(c)):
            out.append(s)
    return out


def slot_line(s, short=True):
    label = s.cell["label"] if s.cell else ""
    t = s.text if s.text else "(빈칸)"
    if short and len(t) > 40:
        t = t[:40] + f"…(지금 {len(s.text)}자)"
    return f"P{s.id} [{label}] {t}"


def scripted_answer(slots):
    """정해 둔 답을 자리 번호에 맞춤 (칸 이름·글 모양으로 찾아서)."""
    out = []
    for s in slots:
        lab = (s.cell or {}).get("label", "")
        if s.cell is None and "[물리]" in s.text:
            out.append(f"P{s.id}: " + s.text.replace("[물리]", "[화학]"))
        elif re.search(r"\(\s{4,}\)", s.text):
            out.append(f"P{s.id}: {SCRIPTED['평가표제목']}")
        elif lab in ("안건", "일시", "장소", "참석자", "작성자"):
            out.append(f"P{s.id}: {SCRIPTED[lab]}")
        elif s.text.startswith("  동일 과목"):
            out.append(f"P{s.id}: {SCRIPTED['심사방법']}")
    return "\n".join(out)


def parse_answer(text, allowed):
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"^\s*P(\d+)\s*[:：]\s?(.*)$", line)
        if m and int(m.group(1)) in allowed:
            out[int(m.group(1))] = m.group(2).rstrip()
    return out


def stream_diff(a_path, b_path):
    a, b = CompoundFile(open(a_path, "rb").read()), CompoundFile(open(b_path, "rb").read())
    same, changed = [], []
    for p in a.list_streams():
        (same if a.read(p) == b.read(p) else changed).append(p)
    return same, changed, a.list_streams() == b.list_streams()


def record_diff(a_path, b_path, changed_ids):
    """바꾼 문단 밖의 레코드가 모두 같은지 (문단 단위로 맞춰 봄)."""
    fa, fb = hwpfill.HwpForm(a_path), hwpfill.HwpForm(b_path)
    bad = []
    if len(fa.slots) != len(fb.slots):
        return [f"문단 수 {len(fa.slots)} → {len(fb.slots)}"]
    for sa, sb in zip(fa.slots, fb.slots):
        ra = fa.sections[sa.section][sa.start:sa.end]
        rb = fb.sections[sb.section][sb.start:sb.end]
        if sa.id in changed_ids:
            ha, hb = ra[0][2], rb[0][2]
            if ha[8:12] != hb[8:12]:
                bad.append(f"P{sa.id} 문단 모양·스타일이 바뀜")
            if sa.shapes and sb.shapes and sb.shapes[0][1] not in {x[1] for x in sa.shapes}:
                bad.append(f"P{sa.id} 글자 모양이 바뀜")
            continue
        if sa.ctrl and sa.id not in changed_ids:   # 표가 든 문단은 머리 레코드만 비교 (안쪽 문단은 따로 비교됨)
            ra, rb = ra[:1], rb[:1]
        if ra != rb:
            bad.append(f"P{sa.id} 바꾸지 않았는데 달라짐")
    return bad


def olefile_check(path):
    sys.path.insert(0, str(HERE.parents[1] / ".." / "pylib"))
    try:
        import olefile
    except ImportError:
        return "olefile 없음 (확인 생략)"
    ole = olefile.OleFileIO(str(path))
    n = len(ole.listdir())
    ole.close()
    return f"olefile 로 열림, 스트림 {n}개"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", required=True)
    ap.add_argument("--out", default="hwp-out")
    ap.add_argument("--base")
    ap.add_argument("--model")
    ap.add_argument("--pylib")
    a = ap.parse_args()
    if a.pylib:
        sys.path.insert(0, a.pylib)
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    form_src = out / "서식_원본.hwp"
    shutil.copy(a.form, form_src)
    form = hwpfill.HwpForm(form_src)
    R = {"task": TASK, "slots_all": len(form.slots)}

    # 1) 서식 '스크린샷': 한글이 저장해 둔 첫 쪽 미리보기 그림
    if form.cf.exists("PrvImage"):
        (out / "서식_미리보기.png").write_bytes(form.cf.read("PrvImage"))

    # 2) 참고 자료 찾기 (예시 폴더 + 교과서 선정 자료 2개)
    samples = make_samples.make(out / "samples")
    for rel, content in EXTRA_DOCS.items():
        p = samples / rel
        if p.suffix == ".hwpx":
            writers.write_hwpx(p, content, title=p.stem)
        else:
            p.write_bytes(make_samples._xlsx(content))
    con = fi.open_index(out / "index.sqlite")
    fi.index_folder(con, samples, None, None, make_samples.STUDENTS, log=lambda *_: None)
    paths = fi.search(con, TASK, "bm25", limit=3)
    R["files"] = paths

    # 3) 참고 자료: 예전 방식 vs 간소화
    slots = fillable(form)
    full_map = form.form_map()
    short_map = "\n".join(slot_line(s) for s in slots)
    mat_full = fi.build_materials(con, paths, make_samples.STUDENTS)
    mat_short = fi.compact_materials(con, paths, TASK, make_samples.STUDENTS)
    p_full = FILL_INSTRUCTION.format(task=TASK, slots=full_map, materials=mat_full)
    p_short = FILL_INSTRUCTION.format(task=TASK, slots=short_map, materials=mat_short)
    R["context"] = {
        "materials_full_chars": len(mat_full), "materials_short_chars": len(mat_short),
        "map_full_chars": len(full_map), "map_short_chars": len(short_map),
        "prompt_full_chars": len(p_full), "prompt_short_chars": len(p_short),
        "facts_full": [f for f in FACTS if f in mat_full], "facts_short": [f for f in FACTS if f in mat_short],
        "read_seconds_full": round(len(p_full) / 70), "read_seconds_short": round(len(p_short) / 70),
    }
    (out / "보낼글_예전.txt").write_text(p_full, encoding="utf-8")
    (out / "보낼글_간소화.txt").write_text(p_short, encoding="utf-8")

    # 4) AI 답 → 서식 채우기
    if a.base:
        model = a.model or ai.models(a.base)[0]
        answer = ai.mask_names(ai.ask(a.base, model, ai.mask_names(p_short, make_samples.STUDENTS), max_tokens=900),
                               make_samples.STUDENTS)
    else:
        answer = scripted_answer(slots)
    values = parse_answer(answer, {s.id for s in slots})
    for sid, text in values.items():
        form.set_text(sid, text)
    dst = out / "채움_서식.hwp"
    form.save(dst)
    same, changed, names_same = stream_diff(form_src, dst)
    R["fill"] = {"answer": answer, "changed_slots": sorted(values),
                 "streams_same": same, "streams_changed": changed, "stream_names_same": names_same,
                 "record_problems": record_diff(form_src, dst, set(values)),
                 "text_after": docs.hwp_text(dst.read_bytes()), "olefile": olefile_check(dst),
                 "old_text_left_in_file": [w for w in ("천중필", "안광수") if w.encode("utf-16-le") in dst.read_bytes()]}
    (out / "hwp_results.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in R.items() if k != "fill"}, ensure_ascii=False, indent=1))
    f = R["fill"]
    print("바뀐 자리", f["changed_slots"], "\n그대로", f["streams_same"], "\n바뀜", f["streams_changed"],
          "\n문제", f["record_problems"], "\n", f["olefile"], "\n옛 이름 남음", f["old_text_left_in_file"])
    print(f["text_after"][:600])


if __name__ == "__main__":
    main()
