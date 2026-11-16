"""문서 작성(0.6.1): AI 초안 글을 .docx / .hwpx 파일로 만들기, .docx 글 읽기 (표준 라이브러리만).

초안 글 모양 (AI 가 이 모양으로 쓰게 지시함):
    # 제목
    ## 소제목
    - 항목
    | 칸 | 칸 |      (표: 첫 줄은 머리줄, |---| 줄은 건너뜀)
    그 밖의 줄은 보통 문단

hwpx 는 한컴 OWPML 의 최소 구조를 직접 만든 것 (2026-11-16 선생님 PC 한글에서 열림 확인).
"""

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape


def parse_blocks(text):
    """초안 글 → [(종류, 내용)] 종류: h1 h2 li p table."""
    blocks, table = [], None
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        s = line.strip()
        if s.startswith("|") and s.endswith("|") and len(s) > 1:
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            if table is None:
                table = []
                blocks.append(("table", table))
            table.append(cells)
            continue
        table = None
        if not s:
            continue
        if s.startswith("## "):
            blocks.append(("h2", s[3:].strip()))
        elif s.startswith("# "):
            blocks.append(("h1", s[2:].strip()))
        elif re.match(r"^[-*•]\s+", s):
            blocks.append(("li", re.sub(r"^[-*•]\s+", "", s)))
        else:
            blocks.append(("p", s))
    for kind, val in blocks:   # 표의 칸 수를 맞춤
        if kind == "table":
            n = max(len(r) for r in val)
            for r in val:
                r.extend([""] * (n - len(r)))
    return blocks


# ---------------- docx ----------------
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _w_run(text, bold=False, size=None):
    rpr = ""
    if bold or size:
        rpr = "<w:rPr>" + ("<w:b/>" if bold else "") + (f'<w:sz w:val="{size}"/>' if size else "") + "</w:rPr>"
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _w_para(text, style=None, bold=False, size=None, align=None):
    ppr = ""
    if style or align:
        ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + \
              (f'<w:jc w:val="{align}"/>' if align else "") + "</w:pPr>"
    return f"<w:p>{ppr}{_w_run(text, bold, size)}</w:p>"


def docx_bytes(text):
    body = []
    for kind, val in parse_blocks(text):
        if kind == "h1":
            body.append(_w_para(val, "Title", bold=True, size=32, align="center"))
        elif kind == "h2":
            body.append(_w_para(val, "Heading1", bold=True, size=26))
        elif kind == "li":
            body.append(_w_para("• " + val))
        elif kind == "p":
            body.append(_w_para(val))
        elif kind == "table":
            n = len(val[0])
            grid = "".join('<w:gridCol w:w="%d"/>' % (9000 // n) for _ in range(n))
            rows = []
            for ri, row in enumerate(val):
                cells = "".join(f'<w:tc><w:tcPr><w:tcW w:w="{9000 // n}" w:type="dxa"/></w:tcPr>'
                                f"{_w_para(c, bold=(ri == 0))}</w:tc>" for c in row)
                rows.append(f"<w:tr>{cells}</w:tr>")
            borders = "".join(f'<w:{b} w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
                              for b in ("top", "left", "bottom", "right", "insideH", "insideV"))
            body.append(f'<w:tbl><w:tblPr><w:tblW w:w="9000" w:type="dxa"/><w:tblBorders>{borders}</w:tblBorders>'
                        f"</w:tblPr><w:tblGrid>{grid}</w:tblGrid>{''.join(rows)}</w:tbl>")
            body.append("<w:p/>")
    sect = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1701" w:right="1440" w:bottom="1440" w:left="1440" w:header="851" w:footer="992" w:gutter="0"/>'
            "</w:sectPr>")
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document xmlns:w="{W}">'
                f"<w:body>{''.join(body)}{sect}</w:body></w:document>")
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:styles xmlns:w="{W}">'
              '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="맑은 고딕" w:eastAsia="맑은 고딕" w:hAnsi="맑은 고딕"/>'
              '<w:sz w:val="22"/><w:lang w:eastAsia="ko-KR"/></w:rPr></w:rPrDefault>'
              '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
              '<w:pPr><w:spacing w:before="240"/><w:outlineLvl w:val="0"/></w:pPr></w:style>'
              "</w:styles>")
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
          "</Types>")
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>")
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                "</Relationships>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
    return buf.getvalue()


def docx_text(data):
    """.docx 의 글 (문단은 줄바꿈, 표 칸은 탭)."""
    z = zipfile.ZipFile(io.BytesIO(data))
    root = ET.fromstring(z.read("word/document.xml"))
    out = []

    def walk(el):
        local = el.tag.rsplit("}", 1)[-1]
        if local == "tbl":
            for tr in el.iter(f"{{{W}}}tr"):
                cells = []
                for tc in tr.findall(f"{{{W}}}tc"):
                    cells.append(" ".join("".join(t.text or "" for t in p.iter(f"{{{W}}}t"))
                                          for p in tc.iter(f"{{{W}}}p")).strip())
                out.append("\t".join(cells))
            return
        if local == "p":
            buf = []
            for x in el.iter():
                n = x.tag.rsplit("}", 1)[-1]
                if n == "t":
                    buf.append(x.text or "")
                elif n == "tab":
                    buf.append("\t")
                elif n in ("br", "cr"):
                    buf.append("\n")
            out.append("".join(buf))
            return
        for c in el:
            walk(c)

    walk(root)
    return "\n".join(out)


# ---------------- hwpx ----------------
NS = {
    "ha": "http://www.hancom.co.kr/hwpml/2011/app",
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
    "hv": "http://www.hancom.co.kr/hwpml/2011/version",
    "opf": "http://www.idpf.org/2007/opf/",
    "dc": "http://purl.org/dc/elements/1.1/",
}
XMLNS = " ".join(f'xmlns:{k}="{v}"' for k, v in NS.items())

# charPr: 0 보통 10pt, 1 굵게 15pt(제목), 2 굵게 12pt(소제목), 3 굵게 10pt(표 머리)
CHAR_PRS = [(1000, False), (1500, True), (1200, True), (1000, True)]
# paraPr: 0 왼쪽, 1 가운데
PARA_PRS = ["JUSTIFY", "CENTER"]
LANGS = ("HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER")


def _header_xml():
    fonts = "".join(
        f'<hh:fontface lang="{lang}" fontCnt="1"><hh:font id="0" face="함초롬바탕" type="TTF" isEmbedded="0">'
        '<hh:typeInfo familyType="FCAT_GOTHIC" weight="6" proportion="4" contrast="0" strokeVariation="1" '
        'armStyle="1" letterform="1" midline="1" xHeight="1"/></hh:font></hh:fontface>' for lang in LANGS)
    border = ('<hh:borderFill id="{id}" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">'
              '<hh:slash type="NONE" Crooked="0" isCounter="0"/><hh:backSlash type="NONE" Crooked="0" isCounter="0"/>'
              '{lines}<hh:diagonal type="SOLID" width="0.1 mm" color="#000000"/></hh:borderFill>')
    none = "".join(f'<hh:{s}Border type="NONE" width="0.1 mm" color="#000000"/>' for s in ("left", "right", "top", "bottom"))
    solid = "".join(f'<hh:{s}Border type="SOLID" width="0.12 mm" color="#000000"/>' for s in ("left", "right", "top", "bottom"))
    borders = border.format(id=1, lines=none) + border.format(id=2, lines=solid)
    ref = '<hh:fontRef hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>'
    ratio = '<hh:ratio hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>'
    spacing = '<hh:spacing hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>'
    rel = '<hh:relSz hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>'
    off = '<hh:offset hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>'
    chars = "".join(
        f'<hh:charPr id="{i}" height="{h}" textColor="#000000" shadeColor="none" useFontSpace="0" useKerning="0" '
        f'symMark="NONE" borderFillIDRef="1">{ref}{ratio}{spacing}{rel}{off}{"<hh:bold/>" if b else ""}'
        '<hh:underline type="NONE" shape="SOLID" color="#000000"/><hh:strikeout shape="NONE" color="#000000"/>'
        '<hh:outline type="NONE"/><hh:shadow type="NONE" color="#B2B2B2" offsetX="10" offsetY="10"/></hh:charPr>'
        for i, (h, b) in enumerate(CHAR_PRS))
    paras = "".join(
        f'<hh:paraPr id="{i}" tabPrIDRef="0" condense="0" fontLineHeight="0" snapToGrid="1" '
        'suppressLineNumbers="0" checked="0">'
        f'<hh:align horizontal="{a}" vertical="BASELINE"/><hh:heading type="NONE" idRef="0" level="0"/>'
        '<hh:breakSetting breakLatinWord="KEEP_WORD" breakNonLatinWord="KEEP_WORD" widowOrphan="0" keepWithNext="0" '
        'keepLines="0" pageBreakBefore="0" lineWrap="BREAK"/><hh:autoSpacing eAsianEng="0" eAsianNum="0"/>'
        '<hh:margin><hc:intent value="0" unit="HWPUNIT"/><hc:left value="0" unit="HWPUNIT"/>'
        '<hc:right value="0" unit="HWPUNIT"/><hc:prev value="0" unit="HWPUNIT"/><hc:next value="400" unit="HWPUNIT"/>'
        '</hh:margin><hh:lineSpacing type="PERCENT" value="160" unit="HWPUNIT"/>'
        '<hh:border borderFillIDRef="1" offsetLeft="0" offsetRight="0" offsetTop="0" offsetBottom="0" connect="0" '
        'ignoreMargin="0"/></hh:paraPr>' for i, a in enumerate(PARA_PRS))
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><hh:head {XMLNS} version="1.4" secCnt="1">'
            '<hh:beginNum page="1" footnote="1" endnote="1" pic="1" tbl="1" equation="1"/>'
            f'<hh:refList><hh:fontfaces itemCnt="{len(LANGS)}">{fonts}</hh:fontfaces>'
            f'<hh:borderFills itemCnt="2">{borders}</hh:borderFills>'
            f'<hh:charProperties itemCnt="{len(CHAR_PRS)}">{chars}</hh:charProperties>'
            '<hh:tabProperties itemCnt="1"><hh:tabPr id="0" autoTabLeft="0" autoTabRight="0"/></hh:tabProperties>'
            f'<hh:paraProperties itemCnt="{len(PARA_PRS)}">{paras}</hh:paraProperties>'
            '<hh:styles itemCnt="1"><hh:style id="0" type="PARA" name="바탕글" engName="Normal" paraPrIDRef="0" '
            'charPrIDRef="0" nextStyleIDRef="0" langID="1042" lockForm="0"/></hh:styles>'
            '</hh:refList><hh:compatibleDocument targetProgram="HWP201X"><hh:layoutCompatibility/>'
            '</hh:compatibleDocument><hh:docOption><hh:linkinfo path="" pageInherit="0" footnoteInherit="0"/>'
            '</hh:docOption><hh:trackchageConfig flags="56"/></hh:head>')


SEC_PR = ('<hp:secPr id="" textDirection="HORIZONTAL" spaceColumns="1134" tabStop="8000" tabStopVal="4000" '
          'tabStopUnit="HWPUNIT" outlineShapeIDRef="0" memoShapeIDRef="0" textVerticalWidthHead="0" masterPageCnt="0">'
          '<hp:grid lineGrid="0" charGrid="0" wonggojiFormat="0"/>'
          '<hp:startNum pageStartsOn="BOTH" page="0" pic="0" tbl="0" equation="0"/>'
          '<hp:visibility hideFirstHeader="0" hideFirstFooter="0" hideFirstMasterPage="0" border="SHOW_ALL" '
          'fill="SHOW_ALL" hideFirstPageNum="0" hideFirstEmptyLine="0" showLineNumber="0"/>'
          '<hp:lineNumberShape restartType="0" countBy="0" distance="0" startNumber="0"/>'
          '<hp:pagePr landscape="WIDELY" width="59528" height="84186" gutterType="LEFT_ONLY">'
          '<hp:margin header="4252" footer="4252" gutter="0" left="8504" right="8504" top="5668" bottom="4252"/>'
          '</hp:pagePr>'
          '<hp:footNotePr><hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
          '<hp:noteLine length="-1" type="SOLID" width="0.12 mm" color="#000000"/>'
          '<hp:noteSpacing betweenNotes="283" belowLine="567" aboveLine="850"/>'
          '<hp:numbering type="CONTINUOUS" newNum="1"/><hp:placement place="EACH_COLUMN" beneathText="0"/>'
          '</hp:footNotePr>'
          '<hp:endNotePr><hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
          '<hp:noteLine length="14692344" type="SOLID" width="0.12 mm" color="#000000"/>'
          '<hp:noteSpacing betweenNotes="0" belowLine="567" aboveLine="850"/>'
          '<hp:numbering type="CONTINUOUS" newNum="1"/><hp:placement place="END_OF_DOCUMENT" beneathText="0"/>'
          '</hp:endNotePr>'
          '<hp:pageBorderFill type="BOTH" borderFillIDRef="1" textBorder="PAPER" headerInside="0" footerInside="0" '
          'fillArea="PAPER"><hp:offset left="1417" right="1417" top="1417" bottom="1417"/></hp:pageBorderFill>'
          '</hp:secPr>'
          '<hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" sameSz="1" sameGap="0"/></hp:ctrl>')

BODY_WIDTH = 59528 - 8504 * 2   # 쪽 너비 - 왼쪽·오른쪽 여백 (HWPUNIT)


class _Ids:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return self.n


def _hp_para(pid, text, char=0, para=0, first=False):
    run = f'<hp:run charPrIDRef="{char}">' + (SEC_PR if first else "")
    run += f"<hp:t>{escape(text)}</hp:t></hp:run>" if text else "</hp:run>"
    return (f'<hp:p id="{pid}" paraPrIDRef="{para}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'{run}</hp:p>')


def _hp_table(ids, rows):
    nrow, ncol = len(rows), len(rows[0])
    cw, ch = BODY_WIDTH // ncol, 1800
    trs = []
    for r, row in enumerate(rows):
        tcs = []
        for c, val in enumerate(row):
            inner = _hp_para(ids(), val, char=3 if r == 0 else 0)
            tcs.append(f'<hp:tc name="" header="{1 if r == 0 else 0}" hasMargin="0" protect="0" editable="0" '
                       f'dirty="0" borderFillIDRef="2"><hp:subList id="" textDirection="HORIZONTAL" '
                       'lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" '
                       f'textHeight="0" hasTextRef="0" hasNumRef="0">{inner}</hp:subList>'
                       f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
                       f'<hp:cellSz width="{cw}" height="{ch}"/>'
                       '<hp:cellMargin left="510" right="510" top="141" bottom="141"/></hp:tc>')
        trs.append("<hp:tr>" + "".join(tcs) + "</hp:tr>")
    tbl = (f'<hp:tbl id="{ids()}" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" '
           f'lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="1" rowCnt="{nrow}" colCnt="{ncol}" '
           'cellSpacing="0" borderFillIDRef="2" noAdjust="0">'
           f'<hp:sz width="{cw * ncol}" widthRelTo="ABSOLUTE" height="{ch * nrow}" heightRelTo="ABSOLUTE" protect="0"/>'
           '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" '
           'vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
           '<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
           '<hp:inMargin left="510" right="510" top="141" bottom="141"/>' + "".join(trs) + "</hp:tbl>")
    return (f'<hp:p id="{ids()}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0">{tbl}<hp:t/></hp:run></hp:p>')


def hwpx_bytes(text, title="초안"):
    ids = _Ids()
    paras = [_hp_para(0, "", first=True)]
    for kind, val in parse_blocks(text):
        if kind == "h1":
            paras.append(_hp_para(ids(), val, char=1, para=1))
        elif kind == "h2":
            paras.append(_hp_para(ids(), val, char=2))
        elif kind == "li":
            paras.append(_hp_para(ids(), "◦ " + val))
        elif kind == "p":
            paras.append(_hp_para(ids(), val))
        elif kind == "table":
            paras.append(_hp_table(ids, val))
    section = f'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><hs:sec {XMLNS}>{"".join(paras)}</hs:sec>'
    version = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
               f'<hv:HCFVersion xmlns:hv="{NS["hv"]}" tagetApplication="WORDPROCESSOR" major="5" minor="1" '
               'micro="0" buildNumber="1" os="1" xmlVersion="1.4" application="Hancom Office Hangul" '
               'appVersion="12, 0, 0, 0"/>')
    container = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
                 '<ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container" '
                 'xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf"><ocf:rootfiles>'
                 '<ocf:rootfile full-path="Contents/content.hpf" media-type="application/hwpml-package+xml"/>'
                 '</ocf:rootfiles></ocf:container>')
    manifest = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
                '<odf:manifest xmlns:odf="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"/>')
    hpf = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
           f'<opf:package {XMLNS} version="" unique-identifier="" id="">'
           f"<opf:metadata><opf:title>{escape(title)}</opf:title><opf:language>ko</opf:language></opf:metadata>"
           '<opf:manifest><opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>'
           '<opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>'
           '<opf:item id="settings" href="settings.xml" media-type="application/xml"/></opf:manifest>'
           '<opf:spine><opf:itemref idref="header" linear="yes"/><opf:itemref idref="section0" linear="yes"/>'
           "</opf:spine></opf:package>")
    settings = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
                f'<ha:HWPApplicationSetting xmlns:ha="{NS["ha"]}" xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0">'
                '<ha:CaretPosition listIDRef="0" paraIDRef="0" pos="0"/></ha:HWPApplicationSetting>')
    preview = "\n".join(v if isinstance(v, str) else "\n".join("\t".join(r) for r in v)
                        for _, v in parse_blocks(text))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/hwp+zip", compress_type=zipfile.ZIP_STORED)
        for name, data in (("version.xml", version), ("Contents/header.xml", _header_xml()),
                           ("Contents/section0.xml", section), ("settings.xml", settings),
                           ("Preview/PrvText.txt", preview[:1000]), ("META-INF/container.xml", container),
                           ("META-INF/manifest.xml", manifest), ("Contents/content.hpf", hpf)):
            z.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()
