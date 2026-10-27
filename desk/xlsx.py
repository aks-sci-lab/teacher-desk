"""엑셀(.xlsx) 읽기: 표준 라이브러리만으로 시트의 칸 값과 병합 칸을 읽습니다 (읽기 전용, 서식은 무시)."""

import io
import re
import zipfile
import xml.etree.ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
MAX_CELLS = 200_000


class XlsxError(Exception):
    pass


def col_index(ref):
    """'B12' → (11, 1)  (행, 열, 0부터)."""
    m = re.match(r"([A-Z]+)(\d+)", ref)
    if not m:
        raise XlsxError(f"칸 주소가 잘못되었습니다: {ref}")
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)) - 1, col - 1


def _text(el):
    """<si>·<is> 안의 글자 (서식 조각 <r><t> 포함, 발음 표시 <rPh> 제외)."""
    if el is None:
        return ""
    out = []
    for node in el.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "rPh":
            break
        if tag == "t" and node.text:
            out.append(node.text)
    return "".join(out)


def read(data):
    """[{name, rows: [[값...]], merges: [(r1, c1, r2, c2)]}]  값은 str 또는 float."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise XlsxError("엑셀(.xlsx) 파일이 아닙니다. 예전 .xls 는 엑셀에서 .xlsx 로 다시 저장해 주세요.")
    names = set(z.namelist())
    shared = []
    if "xl/sharedStrings.xml" in names:
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        shared = [_text(si) for si in root.findall("m:si", NS)]
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {}
    if "xl/_rels/workbook.xml.rels" in names:
        for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).findall("rel:Relationship", NS):
            target = r.get("Target").lstrip("/")
            rels[r.get("Id")] = target if target.startswith("xl/") else "xl/" + target
    sheets, cells = [], 0
    for i, sh in enumerate(wb.findall("m:sheets/m:sheet", NS)):
        path = rels.get(sh.get(f"{{{NS['r']}}}id"), f"xl/worksheets/sheet{i + 1}.xml")
        if path not in names:
            continue
        root = ET.fromstring(z.read(path))
        grid = {}
        for c in root.iter(f"{{{NS['m']}}}c"):
            cells += 1
            if cells > MAX_CELLS:
                raise XlsxError("엑셀 파일이 너무 큽니다")
            r, col = col_index(c.get("r"))
            t = c.get("t")
            v = c.find("m:v", NS)
            if t == "s" and v is not None:
                val = shared[int(v.text)] if int(v.text) < len(shared) else ""
            elif t == "inlineStr":
                val = _text(c.find("m:is", NS))
            elif t in ("str", "e"):
                val = v.text if v is not None else ""
            elif t == "b":
                val = v.text if v is not None else ""
            else:
                try:
                    val = float(v.text) if v is not None and v.text else ""
                except ValueError:
                    val = v.text
            if val != "":
                grid[(r, col)] = val
        merges = []
        for m in root.iter(f"{{{NS['m']}}}mergeCell"):
            a, _, b = m.get("ref").partition(":")
            r1, c1 = col_index(a)
            r2, c2 = col_index(b or a)
            merges.append((r1, c1, r2, c2))
        nrows = max((r for r, _ in grid), default=-1) + 1
        ncols = max((c for _, c in grid), default=-1) + 1
        rows = [[grid.get((r, c), "") for c in range(ncols)] for r in range(nrows)]
        sheets.append({"name": sh.get("name"), "rows": rows, "merges": merges})
    return sheets
