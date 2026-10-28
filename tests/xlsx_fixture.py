"""시험용 .xlsx 만들기 (글자는 공유 문자열, 숫자는 그대로, 병합 칸 지원)."""

import io
import zipfile
from xml.sax.saxutils import escape


def col_name(c):
    s = ""
    c += 1
    while c:
        c, r = divmod(c - 1, 26)
        s = chr(65 + r) + s
    return s


def build_xlsx(sheets):
    """sheets: [(이름, [[값...]], [ 'A1:A3', ... ]) 또는 (…, {'C3': 'FFF2A0B6'} 칸 배경색)]"""
    shared, index = [], {}
    sheet_xml = []
    colors = []
    for sh in sheets:
        for c in (sh[3] if len(sh) > 3 else {}).values():
            if c not in colors:
                colors.append(c)
    for sh in sheets:
        rows, merges = sh[1], sh[2]
        fills = sh[3] if len(sh) > 3 else {}
        out = ['<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
        for r, row in enumerate(rows):
            out.append(f'<row r="{r + 1}">')
            for c, v in enumerate(row):
                ref = f"{col_name(c)}{r + 1}"
                st = f' s="{colors.index(fills[ref]) + 1}"' if ref in fills else ""
                if v is None or v == "":
                    if st:
                        out.append(f'<c r="{ref}"{st}/>')
                    continue
                if isinstance(v, (int, float)):
                    out.append(f'<c r="{ref}"{st}><v>{v}</v></c>')
                else:
                    if v not in index:
                        index[v] = len(shared)
                        shared.append(v)
                    out.append(f'<c r="{ref}"{st} t="s"><v>{index[v]}</v></c>')
            out.append("</row>")
        out.append("</sheetData>")
        if merges:
            out.append(f'<mergeCells count="{len(merges)}">' + "".join(f'<mergeCell ref="{m}"/>' for m in merges) + "</mergeCells>")
        out.append("</worksheet>")
        sheet_xml.append("".join(out))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(sh[0])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, sh in enumerate(sheets))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(sheets)))
                   + "</Relationships>")
        z.writestr("xl/sharedStrings.xml",
                   '<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   + "".join(f"<si><t>{escape(s)}</t></si>" for s in shared) + "</sst>")
        if colors:
            z.writestr("xl/styles.xml",
                       '<?xml version="1.0"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       f'<fills count="{len(colors) + 2}"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
                       + "".join(f'<fill><patternFill patternType="solid"><fgColor rgb="{c}"/></patternFill></fill>' for c in colors)
                       + f'</fills><cellXfs count="{len(colors) + 1}"><xf fillId="0"/>'
                       + "".join(f'<xf fillId="{i + 2}" applyFill="1"/>' for i in range(len(colors)))
                       + "</cellXfs></styleSheet>")
        for i, x in enumerate(sheet_xml):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", x)
    return buf.getvalue()
