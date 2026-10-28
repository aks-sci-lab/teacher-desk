"""엑셀(.xlsx) 읽기: 표준 라이브러리만으로 시트의 칸 값·병합 칸·칸 배경색을 읽습니다 (읽기 전용, 나머지 서식은 무시)."""

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


# 테마 색 (기본 Office 테마) — 시간표의 '배경색 칸'을 알아보는 데만 씀
THEME = ["FFFFFF", "000000", "E7E6E6", "44546A", "4472C4", "ED7D31", "A5A5A5", "FFC000", "5B9BD5", "70AD47"]


def _tint(hex6, tint):
    if not tint:
        return "#" + hex6
    rgb = [int(hex6[i:i + 2], 16) for i in (0, 2, 4)]
    rgb = [round(v + (255 - v) * tint if tint > 0 else v * (1 + tint)) for v in rgb]
    return "#" + "".join(f"{max(0, min(255, v)):02X}" for v in rgb)


def _fill_colors(z, names):
    """칸 서식 번호(s) → 배경색 '#RRGGBB' (흰색·채우기 없음은 None)."""
    if "xl/styles.xml" not in names:
        return []
    root = ET.fromstring(z.read("xl/styles.xml"))
    fills = []
    for f in root.findall("m:fills/m:fill", NS):
        pf = f.find("m:patternFill", NS)
        color = None
        if pf is not None and pf.get("patternType") not in (None, "none", "gray125"):
            fg = pf.find("m:fgColor", NS)
            if fg is None:
                fg = pf.find("m:bgColor", NS)
            if fg is not None:
                if fg.get("rgb"):
                    hex6 = fg.get("rgb")[-6:].upper()
                    color = None if hex6 == "FFFFFF" else "#" + hex6
                elif fg.get("theme") is not None:
                    t = int(fg.get("theme"))
                    color = _tint(THEME[t] if t < len(THEME) else "CCCCCC", float(fg.get("tint") or 0))
                    color = None if color.upper() == "#FFFFFF" else color
                elif fg.get("indexed") is not None:
                    color = None if fg.get("indexed") in ("64", "9", "1") else "#C9C9C9"
        fills.append(color)
    out = []
    for xf in root.findall("m:cellXfs/m:xf", NS):
        fid = int(xf.get("fillId") or 0)
        out.append(fills[fid] if fid < len(fills) else None)
    return out


def read(data):
    """[{name, rows: [[값...]], merges: [(r1, c1, r2, c2)], fills: {(행, 열): '#RRGGBB'}}]  값은 str 또는 float."""
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
    styles = _fill_colors(z, names)
    sheets, cells = [], 0
    for i, sh in enumerate(wb.findall("m:sheets/m:sheet", NS)):
        path = rels.get(sh.get(f"{{{NS['r']}}}id"), f"xl/worksheets/sheet{i + 1}.xml")
        if path not in names:
            continue
        root = ET.fromstring(z.read(path))
        grid, fills = {}, {}
        for c in root.iter(f"{{{NS['m']}}}c"):
            cells += 1
            if cells > MAX_CELLS:
                raise XlsxError("엑셀 파일이 너무 큽니다")
            r, col = col_index(c.get("r"))
            st = c.get("s")
            if st and st.isdigit() and int(st) < len(styles) and styles[int(st)]:
                fills[(r, col)] = styles[int(st)]
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
        sheets.append({"name": sh.get("name"), "rows": rows, "merges": merges, "fills": fills})
    return sheets
