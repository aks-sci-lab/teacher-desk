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
    """가상 양식: 이름 칸은 하늘색 바탕, 제목은 남색 글자. 한글 양식처럼 '일시' 글자를 두 묶음으로 쪼개 둠."""
    hx, dx = out / "양식_협의회회의록.hwpx", out / "양식_협의회회의록.docx"
    writers.write_hwpx(hx, FORM_TEXT, title="양식")
    writers.write_docx(dx, FORM_TEXT)
    fill = ('<hh:borderFill id="3" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">'
            '<hh:slash type="NONE" Crooked="0" isCounter="0"/><hh:backSlash type="NONE" Crooked="0" isCounter="0"/>'
            + "".join(f'<hh:{x}Border type="SOLID" width="0.12 mm" color="#1F4E79"/>' for x in ("left", "right", "top", "bottom"))
            + '<hh:diagonal type="SOLID" width="0.1 mm" color="#000000"/><hc:fillBrush>'
            '<hc:winBrush faceColor="#DDEBF7" hatchColor="#999999" alpha="0"/></hc:fillBrush></hh:borderFill>')
    _rewrite(hx, "Contents/header.xml", lambda s: s.replace('<hh:borderFills itemCnt="2">', '<hh:borderFills itemCnt="3">')
             .replace("</hh:borderFills>", fill + "</hh:borderFills>")
             .replace('<hh:charPr id="1" height="1500" textColor="#000000"', '<hh:charPr id="1" height="1500" textColor="#1F4E79"'))

    def hsec(s):
        s = s.replace('<hp:run charPrIDRef="3"><hp:t>일시</hp:t></hp:run>',
                      '<hp:run charPrIDRef="3"><hp:t>일 </hp:t></hp:run><hp:run charPrIDRef="3"><hp:t>시</hp:t></hp:run>')
        # 첫째 칸(이름 칸)만 하늘색 바탕
        return re.sub(r'(<hp:tc [^>]*?borderFillIDRef=)"2"(>(?:(?!</hp:tc>).)*?<hp:cellAddr colAddr="0")',
                      r'\1"3"\2', s, flags=re.S)
    _rewrite(hx, "Contents/section0.xml", hsec)

    def dsec(s):
        s = s.replace('<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">일시</w:t></w:r>',
                      '<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">일 </w:t></w:r>'
                      '<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">시</w:t></w:r>')
        s = s.replace('<w:rPr><w:b/><w:sz w:val="32"/></w:rPr>', '<w:rPr><w:b/><w:color w:val="1F4E79"/><w:sz w:val="32"/></w:rPr>')
        return re.sub(r'<w:tr><w:tc><w:tcPr>(<w:tcW [^>]*/>)</w:tcPr>', r'<w:tr><w:tc><w:tcPr>\1<w:shd w:val="clear" w:color="auto" w:fill="DDEBF7"/></w:tcPr>', s)
    _rewrite(dx, "word/document.xml", dsec)
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
    cells = lambda f: sum(len(r) for _, r in f.rows())   # noqa: E731
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


def preview_image(path, outdir):
    """한글이 저장한 hwpx 에는 첫 쪽 미리보기 그림(Preview/PrvImage.png)이 들어 있음 → 양식 '스크린샷'으로 씀."""
    try:
        data = zipfile.ZipFile(path).read("Preview/PrvImage.png")
    except KeyError:
        return None
    outdir.mkdir(exist_ok=True)
    png = outdir / (path.stem + "_미리보기.png")
    png.write_bytes(data)
    return str(png)


def colors(img):
    """흰색·검정·회색이 아닌 색(바탕색·글자색)을 16단계로 묶어 넓이 비율로."""
    from collections import Counter
    c = Counter()
    px = list(img.convert("RGB").get_flattened_data()) if hasattr(img, "get_flattened_data") else img.convert("RGB").getdata()
    for r, g, b in px:
        if max(r, g, b) - min(r, g, b) > 24:
            c[(r // 16 * 16, g // 16 * 16, b // 16 * 16)] += 1
    total = len(px)
    return {"#%02X%02X%02X" % k: round(v / total * 100, 2) for k, v in c.most_common(4) if v / total > 0.0005}


def compare_images(before, after, out_png):
    """채우기 전후 그림 비교: 바뀐 곳에 빨간 상자, 나란히 놓은 그림 저장, 색 비교."""
    from PIL import Image, ImageChops, ImageDraw
    a, b = Image.open(before).convert("RGB"), Image.open(after).convert("RGB")
    w, h = max(a.width, b.width), max(a.height, b.height)
    A = Image.new("RGB", (w, h), "white"); A.paste(a)
    B = Image.new("RGB", (w, h), "white"); B.paste(b)
    side = Image.new("RGB", (w * 2 + 20, h), "white")
    side.paste(A, (0, 0)); side.paste(B, (w + 20, 0))
    ImageDraw.Draw(side).line((w + 10, 0, w + 10, h), fill=(200, 200, 200), width=2)
    side.save(out_png)
    # 위에서부터 처음 달라지는 줄 (그 위는 그림까지 똑같음 = 제목·머리말 그대로). 칸이 길어지면 아래는 밀림
    diff = ImageChops.difference(A, B).convert("L").point(lambda v: 255 if v > 40 else 0)
    box = diff.getbbox()
    ca, cb = colors(A), colors(B)
    return {"same_until_px": box[1] if box else h, "colors_before": ca, "colors_after": cb,
            "colors_same": set(ca) == set(cb), "side_by_side": str(out_png)}


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
        before, after = render(form_path, out / "render"), render(dst, out / "render")
        look = None
        if before and after and before.endswith(".png"):
            look = compare_images(before, after, out / f"비교_{form_path.stem}{form_path.suffix.replace('.', '_')}.png")
        R["forms"].append({"form": str(form_path), "fields": fields, "answer": answer, "filled": done,
                           "compare": cmp, "preview": preview_image(form_path, out / "render"),
                           "render_before": before, "render_after": after, "look": look, "out": str(dst)})
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
        print("  " + c["text"].replace("\n", " / ")[:300])
        if f["look"]:
            print("  그림 비교:", {k: v for k, v in f["look"].items() if k != "side_by_side"})
    print("이름 샘:", R.get("leaked_names"))


if __name__ == "__main__":
    main()
