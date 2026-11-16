"""기존 양식 지키며 채우기 시험.

    python tools/trial_folder_index/run_template_trial.py --out 결과폴더 [--form 내양식.hwpx] [--base http://127.0.0.1:1234/v1]

--form 이 없으면 가상 '교과협의회 회의록' 양식(hwpx·docx)을 만들어 쓴다. 한글이 저장한 양식처럼
'일 시' 를 두 글자 묶음으로 쪼개 두고, 내용 칸에 따로 글자 모양(굵게 12pt)을 걸어 둔다.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

from desk import ai, docs      # noqa: E402
import fake_lmstudio           # noqa: E402
import folderindex as fi       # noqa: E402
import make_samples            # noqa: E402
import templatefill as tf      # noqa: E402
import writers                 # noqa: E402

FORM_TEXT = """# 과학과 교과협의회 회의록
| 일시 | |
|---|---|
| 장소 | |
| 참석자 | |
| 안건 | |
| 협의 내용 | |
| 협의 결과 | |
작성자: {{작성자}}   확인: 교무부장 (서명)
"""
TASK = "2학기 과학과 협의회 회의록 작성"


def make_forms(out):
    hx, dx = out / "양식_협의회회의록.hwpx", out / "양식_협의회회의록.docx"
    writers.write_hwpx(hx, FORM_TEXT, title="양식")
    writers.write_docx(dx, FORM_TEXT)
    # 한글 양식처럼: '일시' 칸 글자를 두 묶음으로 쪼개고, 빈 내용 칸은 글자 모양 2(굵게 12pt)로
    _rewrite(hx, "Contents/section0.xml", lambda s: s.replace(
        '<hp:run charPrIDRef="3"><hp:t>일시</hp:t></hp:run>',
        '<hp:run charPrIDRef="3"><hp:t>일 </hp:t></hp:run><hp:run charPrIDRef="3"><hp:t>시</hp:t></hp:run>'
    ).replace('<hp:run charPrIDRef="0"></hp:run>', '<hp:run charPrIDRef="2"></hp:run>'))
    _rewrite(dx, "word/document.xml", lambda s: s.replace(
        '<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">일시</w:t></w:r>',
        '<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">일 </w:t></w:r>'
        '<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">시</w:t></w:r>'))
    return [hx, dx]


def _rewrite(path, name, fn):
    z = zipfile.ZipFile(path)
    items = [(i, z.read(i.filename)) for i in z.infolist()]
    z.close()
    with zipfile.ZipFile(path, "w") as o:
        for info, data in items:
            if info.filename == name:
                new = fn(data.decode("utf-8"))
                assert new != data.decode("utf-8"), "양식 만들기: 바꿀 곳을 못 찾음"
                data = new.encode("utf-8")
            o.writestr(info, data, compress_type=info.compress_type)


def compare(src, dst, form):
    """본문 밖 파일이 그대로인지, 표 칸 수·글자 모양 번호가 그대로인지."""
    a, b = zipfile.ZipFile(src), zipfile.ZipFile(dst)
    same, changed = [], []
    for info in a.infolist():
        (same if a.read(info.filename) == b.read(info.filename) else changed).append(info.filename)
    order_ok = [i.filename for i in a.infolist()] == [i.filename for i in b.infolist()]
    first = b.infolist()[0]
    f2 = tf.Form(dst)
    cells = lambda f: sum(len(r) for r in f.rows())   # noqa: E731
    styles = lambda z, n: sorted(re.findall(r'charPrIDRef="(\d+)"', z.read(n).decode()))  # noqa: E731
    out = {"unchanged_parts": same, "changed_parts": changed, "order_kept": order_ok,
           "cells_before": cells(form), "cells_after": cells(f2)}
    if src.suffix == ".hwpx":
        out["mimetype_first_stored"] = first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        out["char_styles_used_before"] = sorted(set(styles(a, "Contents/section0.xml")))
        out["char_styles_used_after"] = sorted(set(styles(b, "Contents/section0.xml")))
        out["text"] = docs.hwpx_text(dst.read_bytes())
    else:
        out["text"] = writers.docx_text(dst.read_bytes())
    return out


def render(path, outdir):
    soffice = shutil.which("soffice")
    if not soffice or path.suffix != ".docx":
        return None
    outdir.mkdir(exist_ok=True)
    subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(outdir), str(path)],
                   capture_output=True, timeout=120)
    pdf = outdir / (path.stem + ".pdf")
    try:
        import pypdfium2
        png = outdir / (path.stem + ".png")
        pypdfium2.PdfDocument(str(pdf))[0].render(scale=1.2).to_pil().save(png)
        return str(png)
    except Exception:
        return str(pdf) if pdf.exists() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="template-out")
    ap.add_argument("--form", action="append")
    ap.add_argument("--base")
    ap.add_argument("--model")
    a = ap.parse_args()
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    forms = [Path(f) for f in a.form] if a.form else make_forms(out)

    samples = make_samples.make(out / "samples")
    names = make_samples.STUDENTS
    fake = None
    base = a.base
    if not base:
        fake = fake_lmstudio.FakeLMStudio().__enter__()
        base = fake.base
    model = a.model or ai.models(base)[0]
    con = fi.open_index(out / "index.sqlite")
    fi.index_folder(con, samples, None, None, names, log=lambda *_: None)   # 본문 색인만 (AI 요약은 이 시험과 무관)
    paths = fi.search(con, TASK, "bm25", limit=3)
    materials = fi.build_materials(con, paths, names)

    R = {"task": TASK, "files": paths, "forms": []}
    for form_path in forms:
        form = tf.Form(form_path)
        fields = form.fields()
        prompt = tf.FILL_INSTRUCTION.format(today=f"{date(2026, 11, 16):%Y-%m-%d}", task=TASK,
                                            fields=", ".join(fields), materials=materials)
        answer = ai.mask_names(ai.ask(base, model, ai.mask_names(prompt, names), max_tokens=900), names)
        values = tf.parse_fill(answer, fields)
        values.setdefault("작성자", "(확인 필요)")
        done = form.fill(values)
        dst = out / f"채움_{form_path.stem}{form_path.suffix}"
        form.save(dst)
        cmp = compare(form_path, dst, tf.Form(form_path))
        R["forms"].append({"form": str(form_path), "fields": fields, "answer": answer, "filled": done,
                           "compare": cmp, "render_before": render(form_path, out / "render"),
                           "render_after": render(dst, out / "render"), "out": str(dst)})
    if fake:
        R["leaked_names"] = sorted({n for p in fake.prompts for n in names if n in p})
        fake.__exit__(None, None, None)
    (out / "template_results.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
    for f in R["forms"]:
        c = f["compare"]
        print(Path(f["form"]).name, "칸:", f["fields"], "채움:", f["filled"])
        print("  그대로:", c["unchanged_parts"], "바뀜:", c["changed_parts"], "순서", c["order_kept"],
              "표 칸", c["cells_before"], "→", c["cells_after"],
              {k: c[k] for k in c if k.startswith(("mimetype", "char_"))})
        print("  " + c["text"].replace("\n", " / ")[:400])
    print("이름 샘:", R.get("leaked_names"))


if __name__ == "__main__":
    main()
