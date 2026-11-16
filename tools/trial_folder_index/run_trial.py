"""폴더 AI 색인 → 관련 파일 찾기 → 초안 문서(docx·hwpx) 흐름 시험.

    python tools/trial_folder_index/run_trial.py --out 결과폴더
    python tools/trial_folder_index/run_trial.py --out 결과폴더 --base http://127.0.0.1:1234/v1   (진짜 LM Studio)

--base 를 주지 않으면 가짜 LM Studio(fake_lmstudio.py)로 돈다. 결과는 결과폴더/report.md, results.json, drafts/.
"""

import argparse
import json
import resource
import shutil
import subprocess
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

from desk import ai, docs      # noqa: E402
import fake_lmstudio           # noqa: E402
import folderindex as fi       # noqa: E402
import make_samples            # noqa: E402
import writers                 # noqa: E402

TODAY = date(2026, 11, 16)
PROMPT_TPS = 70     # 선생님 PC 실측 (docs/DESIGN.md 0.5.3: 8845HS 780M, 프롬프트 처리 약 70토큰/초)
GEN_TPS = 10        # 답 쓰기 속도는 실측 없음 → 12B 모델 내장 그래픽 추정값
DRAFT_TASKS = [("2학기 과학과 협의회 회의록 작성", 3), ("현장체험학습 결과 보고서 작성", 2)]


def peak_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def stem(p):
    return Path(p).stem


def evaluate(con, method, base, model, names):
    rows, t0 = [], time.perf_counter()
    for task, want, kind in make_samples.QUERIES:
        got = [stem(p) for p in fi.search(con, task, method, limit=5, base=base, model=model, names=names)]
        first = next((i for i, g in enumerate(got) if g in want), None)
        rows.append({"task": task, "kind": kind, "want": want, "got": got,
                     "hit1": first == 0, "hit3": first is not None and first < 3,
                     "recall5": sum(1 for w in want if w in got) / len(want),
                     "rr": 1 / (first + 1) if first is not None else 0})
    ms = (time.perf_counter() - t0) * 1000 / len(rows)

    def avg(key, kind=None):
        sel = [r[key] for r in rows if kind is None or r["kind"] == kind]
        return sum(sel) / len(sel) if sel else 0
    return {"method": method, "rows": rows, "ms_per_query": ms,
            "hit1": avg("hit1"), "hit3": avg("hit3"), "recall5": avg("recall5"), "mrr": avg("rr"),
            "hit3_plain": avg("hit3", "plain"), "hit3_synonym": avg("hit3", "synonym")}


def check_docx(path, soffice):
    out = {"ok": True, "notes": []}
    try:
        text = writers.docx_text(path.read_bytes())
        out["roundtrip_chars"] = len(text)
    except Exception as e:
        out["ok"] = False
        out["notes"].append(f"다시 읽기 실패: {e}")
    try:
        import docx   # python-docx 가 있으면 한 번 더 확인 (앱에는 필요 없음)
        d = docx.Document(str(path))
        out["notes"].append(f"python-docx 로 열림: 문단 {len(d.paragraphs)}개, 표 {len(d.tables)}개")
    except ImportError:
        pass
    except Exception as e:
        out["ok"] = False
        out["notes"].append(f"python-docx 로 못 엶: {e}")
    out.update(_soffice_pdf(path, soffice))
    return out


def check_hwpx(path, soffice):
    out = {"ok": True, "notes": []}
    z = zipfile.ZipFile(path)
    first = z.infolist()[0]
    if first.filename != "mimetype" or first.compress_type != zipfile.ZIP_STORED:
        out["ok"] = False
        out["notes"].append("mimetype 이 맨 앞(압축 안 함)에 있지 않음")
    for n in z.namelist():
        if n.endswith((".xml", ".hpf")):
            try:
                ET.fromstring(z.read(n))
            except ET.ParseError as e:
                out["ok"] = False
                out["notes"].append(f"{n} XML 오류: {e}")
    try:
        text = docs.hwpx_text(path.read_bytes())
        out["roundtrip_chars"] = len(text)
        out["notes"].append("업무 데스크의 hwpx 읽기(desk/docs.py)로 다시 읽힘")
    except docs.DocError as e:
        out["ok"] = False
        out["notes"].append(f"다시 읽기 실패: {e}")
    out.update(_soffice_pdf(path, soffice))
    out["notes"].append("한컴오피스 한글에서 열리는지는 이 환경에서 확인 못 함 (한글 프로그램 없음)")
    return out


def _soffice_pdf(path, soffice):
    if not soffice:
        return {"pdf": None}
    outdir = path.parent / ("pdf-" + path.suffix.lstrip("."))
    outdir.mkdir(exist_ok=True)
    r = subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(outdir), str(path)],
                       capture_output=True, text=True, timeout=120)
    pdf = outdir / (path.stem + ".pdf")
    if pdf.exists():
        try:
            n = len(docs.pdf_text(pdf))
        except docs.DocError:
            n = 0
        return {"pdf": str(pdf), "pdf_chars": n}
    return {"pdf": None, "pdf_error": (r.stderr or r.stdout).strip()[-200:]}


def scale_test(src, out, times):
    """AI 없이 파일 읽기·색인·검색만 늘려 봄 (큰 폴더 첫 색인에서 AI 를 뺀 바닥 시간)."""
    big = out / "scale"
    if big.exists():
        shutil.rmtree(big)
    for i in range(times):
        shutil.copytree(src, big / f"{i:03d}", ignore=shutil.ignore_patterns("~$*"))
    db = out / "scale.sqlite"
    db.unlink(missing_ok=True)
    con = fi.open_index(db)
    rss0, t0 = peak_mb(), time.perf_counter()
    st = fi.index_folder(con, big, log=lambda *_: None)
    t_index = time.perf_counter() - t0
    t1 = time.perf_counter()
    st2 = fi.index_folder(con, big, log=lambda *_: None)
    t_again = time.perf_counter() - t1
    q = [fi.search(con, task, "bm25") for task, _, _ in make_samples.QUERIES]
    t2 = time.perf_counter()
    for task, _, _ in make_samples.QUERIES:
        fi.search(con, task, "bm25")
    t_search = (time.perf_counter() - t2) * 1000 / len(make_samples.QUERIES)
    con.close()
    res = {"files": st["files"], "errors": len(st["errors"]), "index_s": t_index, "reindex_s": t_again,
           "reindex_unchanged": st2["unchanged"], "search_ms": t_search, "db_mb": db.stat().st_size / 1e6,
           "peak_mb": peak_mb(), "peak_before_mb": rss0, "results": len(q)}
    shutil.rmtree(big)
    return res


def run(out, base=None, model=None, scale=50):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    samples = make_samples.make(out / "samples")
    names = make_samples.STUDENTS
    fake = None
    if not base:
        fake = fake_lmstudio.FakeLMStudio().__enter__()
        base = fake.base
    ai.check_local(base)
    model = model or ai.models(base)[0]
    R = {"ai": "가짜 LM Studio (규칙으로 뽑아 쓰기)" if fake else f"진짜 LM Studio {base} ({model})",
         "peak_start_mb": peak_mb()}

    # 1) 첫 색인 (AI 요약 + 키워드)
    db = out / "index.sqlite"
    db.unlink(missing_ok=True)
    con = fi.open_index(db)
    print("1) 첫 색인")
    t0 = time.perf_counter()
    st = fi.index_folder(con, samples, base, model, names)
    R["index"] = {**st, "seconds": time.perf_counter() - t0}
    if fake:
        R["index"]["fake_stats"] = dict(fake.stats)
    rows = [dict(r) for r in con.execute("SELECT path, ext, chars, read_ms, ai_ms, ai_calls, error, summary, keywords "
                                         "FROM files ORDER BY path")]
    R["files"] = rows

    # 2) 다시 색인 (바뀐 것 없음) / 파일 하나 고치고 다시
    t1 = time.perf_counter()
    st2 = fi.index_folder(con, samples, base, model, names, log=lambda *_: None)
    R["reindex"] = {"unchanged": st2["unchanged"], "new": st2["new"], "ai_calls": st2["ai_calls"],
                    "seconds": time.perf_counter() - t1}
    touched = samples / "과학과/2학기_과학과_협의회_회의록_2차.docx"
    writers.write_docx(touched, make_samples.DOCS["과학과/2학기_과학과_협의회_회의록_2차.docx"] + "- 추가 안건: 실험 기자재 점검\n")
    st3 = fi.index_folder(con, samples, base, model, names, log=lambda *_: None)
    R["reindex_one_changed"] = {"new": st3["new"], "ai_calls": st3["ai_calls"]}

    # 3) 검색 정확도: 지금 앱 방식(like) / 두 글자 조각 점수(bm25) / + AI 요약·키워드·검색어 늘리기
    print("2) 검색 비교")
    R["search"] = [evaluate(con, m, base, model, names) for m in ("like", "bm25", "bm25+ai")]

    # 4) 초안 문서
    print("3) 초안 문서")
    drafts = out / "drafts"
    if drafts.exists():
        shutil.rmtree(drafts)
    drafts.mkdir()
    R["drafts"] = []
    for task, k in DRAFT_TASKS:
        paths = fi.search(con, task, "bm25+ai", limit=k, base=base, model=model, names=names)
        t = time.perf_counter()
        text, prompt_chars = fi.make_draft(con, base, model, task, paths, names, today=TODAY)
        sec = time.perf_counter() - t
        name = fi.norm(task)[:20]
        (drafts / f"{name}.txt").write_text(text, encoding="utf-8")
        dx, hx = drafts / f"{name}.docx", drafts / f"{name}.hwpx"
        writers.write_docx(dx, text)
        writers.write_hwpx(hx, text, title=task)
        R["drafts"].append({"task": task, "files": paths, "prompt_chars": prompt_chars, "seconds": sec,
                            "text": text, "docx": {"path": str(dx), "bytes": dx.stat().st_size, **check_docx(dx, soffice)},
                            "hwpx": {"path": str(hx), "bytes": hx.stat().st_size, **check_hwpx(hx, soffice)}})

    # 5) 학생 이름이 AI 로 갔는지
    if fake:
        leaked = sorted({n for p in fake.prompts for n in names if n in p})
        R["masking"] = {"prompts": len(fake.prompts), "leaked_names": leaked,
                        "masked_seen": sum(1 for p in fake.prompts if "○○○" in p)}
        R["fake_total"] = dict(fake.stats)
        fake.__exit__(None, None, None)
    R["peak_after_ai_mb"] = peak_mb()
    con.close()
    R["index_db_kb"] = db.stat().st_size / 1024

    # 6) 큰 폴더 (AI 없이)
    if scale:
        print(f"4) 큰 폴더 시험 (예시 폴더 × {scale})")
        R["scale"] = scale_test(samples, out, scale)
    (out / "results.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return R


def estimate(R):
    """가짜 서버가 받은 글자 수로 선생님 PC 의 진짜 시간 짐작 (한국어는 글자당 약 1토큰)."""
    f = R.get("index", {}).get("fake_stats")
    if not f:
        return None
    sec = f["prompt_chars"] / PROMPT_TPS + f["answer_chars"] / GEN_TPS
    n = max(R["index"]["new"] - len(R["index"]["errors"]), 1)
    return {"calls": f["calls"], "prompt_chars": f["prompt_chars"], "answer_chars": f["answer_chars"],
            "minutes": sec / 60, "per_file_s": sec / n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="trial-out")
    ap.add_argument("--base")
    ap.add_argument("--model")
    ap.add_argument("--scale", type=int, default=50)
    a = ap.parse_args()
    R = run(a.out, a.base, a.model, a.scale)
    R["estimate"] = estimate(R)
    (Path(a.out) / "results.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str),
                                             encoding="utf-8")
    for s in R["search"]:
        print(f"{s['method']:8} 3위 안 {s['hit3']:.0%} (같은 낱말 {s['hit3_plain']:.0%}, 다른 말 {s['hit3_synonym']:.0%})"
              f" 1위 {s['hit1']:.0%} 재현율@5 {s['recall5']:.0%} MRR {s['mrr']:.2f} {s['ms_per_query']:.1f}ms")
    print(json.dumps({k: R[k] for k in ("reindex", "reindex_one_changed", "masking", "estimate", "scale",
                                         "peak_after_ai_mb", "index_db_kb") if k in R}, ensure_ascii=False, indent=1))
    for d in R["drafts"]:
        print(d["task"], d["files"], d["docx"]["ok"], d["docx"].get("pdf_chars"), d["hwpx"]["ok"], d["hwpx"].get("pdf_chars"),
              d["hwpx"].get("pdf_error"))


if __name__ == "__main__":
    main()
