"""서식 채우기(desk/formfill.py) 시험: 가짜 서식만 씀 (실제 학교 서식·선생님 이름은 저장소에 넣지 않음)."""
import io
import struct
import unittest
import zipfile
import zlib

from desk import formfill
from desk.cfb import CompoundFile
from tests.hwp_fixture import build_cfb

PH, PT, PCS, PLS, CTRL, LIST, TABLE = 66, 67, 68, 69, 71, 72, 77
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 24


def rec(tag, level, payload):
    n = len(payload)
    if n < 0xFFF:
        return struct.pack("<I", tag | (level << 10) | (n << 20)) + payload
    return struct.pack("<II", tag | (level << 10) | (0xFFF << 20), n) + payload


def para(level, text, shape=3, table=False, last=False):
    """한글이 쓰는 모양대로: PARA_HEADER(높이 n) 밑에 TEXT·CHAR_SHAPE·LINE_SEG(높이 n+1)."""
    units = (struct.pack("<H", 11) + b" lbt" + b"\0" * 8 + struct.pack("<H", 11)) if table else b""
    units += text.encode("utf-16-le") + b"\r\0"
    head = bytearray(22)
    struct.pack_into("<I", head, 0, (len(units) // 2) | (0x80000000 if last else 0))
    struct.pack_into("<I", head, 4, 0x800 if table else 0)
    struct.pack_into("<HHH", head, 12, 1, 0, 1)
    out = rec(PH, level, bytes(head)) + rec(PT, level + 1, units)
    out += rec(PCS, level + 1, struct.pack("<II", 0, shape)) + rec(PLS, level + 1, b"\0" * 36)
    return out


def cell(level, col, row, text):
    head = bytearray(34)
    struct.pack_into("<I", head, 0, 1)
    struct.pack_into("<HH", head, 8, col, row)
    return rec(LIST, level, bytes(head)) + para(level, text, shape=5)


def section(rows, title="2025학년도 과학과 협의록", extra=()):
    """제목 + 표(이름 칸 | 값 칸) + 뒤 문단들."""
    out = para(0, title, shape=7)
    out += para(0, "", table=True)
    out += rec(CTRL, 1, b" lbt" + b"\0" * 40) + rec(TABLE, 2, b"\0" * 30)
    for r, (label, value) in enumerate(rows):
        out += cell(2, 0, r, label) + cell(2, 1, r, value)
    for i, t in enumerate(extra):
        out += para(0, t, last=i == len(extra) - 1)
    return out


def build_hwp(sec, compressed=True, preview=True):
    if compressed:
        c = zlib.compressobj(9, zlib.DEFLATED, -15)
        sec = c.compress(sec) + c.flush()
    header = b"HWP Document File".ljust(32, b"\0") + struct.pack("<II", 0x05000300, 1 if compressed else 0)
    streams = {"FileHeader": header.ljust(256, b"\0"), "DocInfo": b"\x11" * 300, "BodyText/Section0": sec,
               "PrvText": "옛 미리보기 글".encode("utf-16-le").ljust(200, b"\0")}
    if preview:
        streams["PrvImage"] = PNG
    return build_cfb(streams)


ROWS = [("일 시", "2025. 10. 15.(수) 15:00"), ("장 소", "과학실 1"), ("협의 내용", "작년 협의 내용")]


def body(data):
    cf = CompoundFile(data)
    return cf, formfill.read_records(zlib.decompress(cf.read("BodyText/Section0"), -15))


class HwpFormTests(unittest.TestCase):
    def test_fields_are_title_and_value_cells(self):
        f = formfill.Form("서식.hwp", build_hwp(section(ROWS, extra=["작성자 (      )"])))
        got = {x["label"]: x["text"] for x in f.fields()}
        self.assertEqual(got["제목"], "2025학년도 과학과 협의록")
        self.assertEqual(got["일시"], "2025. 10. 15.(수) 15:00")
        self.assertEqual(got["협의내용"], "작년 협의 내용")
        self.assertNotIn("", [x["label"] for x in f.fields()])
        self.assertEqual(len(f.fields()), 4)          # 이름 칸·표 문단은 자리가 아님
        self.assertEqual(f.preview_png(), PNG)

    def fill_and_check(self, data, values):
        f = formfill.Form("서식.hwp", data)
        ids = {x["label"]: x["id"] for x in f.fields()}
        done = f.fill({ids[k]: v for k, v in values.items()})
        self.assertEqual(len(done), len(values))
        out = f.to_bytes()
        cf, recs = body(out)
        _, before = body(data)
        texts = [formfill.para_text(d) for t, _, d in recs if t == PT]
        for v in values.values():
            self.assertIn(v, texts)
        # 이름 칸·표·DocInfo 는 그대로, 글자 모양 번호도 원래 칸 것
        self.assertIn("일 시", texts)
        self.assertEqual(cf.read("DocInfo"), b"\x11" * 300)
        self.assertEqual([r for r in recs if r[0] in (CTRL, TABLE, LIST)], [r for r in before if r[0] in (CTRL, TABLE, LIST)])
        self.assertEqual(len(recs), len(before))
        shapes = [struct.unpack("<II", d)[1] for t, _, d in recs if t == PCS]
        self.assertEqual(shapes, [struct.unpack("<II", d)[1] for t, _, d in before if t == PCS])
        self.assertEqual(cf.read("PrvImage"), PNG)
        self.assertEqual(len(cf.read("PrvText")), 200)
        return out

    def test_fill_small_section_mini_stream(self):
        data = build_hwp(section(ROWS))
        self.assertLess(len(CompoundFile(data).read("BodyText/Section0")), 4096)
        self.fill_and_check(data, {"일시": "2026. 10. 14.(수) 16:00", "제목": "2026학년도 과학과 협의록"})

    def test_fill_big_section(self):
        import random
        rnd = random.Random(1)
        filler = ["".join(chr(0xAC00 + rnd.randrange(11000)) for _ in range(60)) for _ in range(80)]
        data = build_hwp(section(ROWS, extra=filler))
        self.assertGreaterEqual(len(CompoundFile(data).read("BodyText/Section0")), 4096)
        out = self.fill_and_check(data, {"장소": "과학실 2", "협의내용": "수행평가 기준 확정"})
        self.assertIn("과학실 2", formfill.Form("서식.hwp", out).text())

    def test_mini_section_grows_past_4096(self):
        import random
        rnd = random.Random(2)
        long = "".join(chr(0xAC00 + rnd.randrange(11000)) for _ in range(3000))
        data = build_hwp(section(ROWS))
        out = self.fill_and_check(data, {"협의내용": long})
        self.assertGreaterEqual(len(CompoundFile(out).read("BodyText/Section0")), 4096)

    def test_table_paragraph_not_editable(self):
        f = formfill.HwpForm(build_hwp(section(ROWS)))
        tbl = next(s for s in f.slots if "tbl " in s.ctrls)
        with self.assertRaises(formfill.FormError):
            f.set_text(tbl.id, "x")

    def test_rejects_distribution_and_non_hwp(self):
        header = b"HWP Document File".ljust(32, b"\0") + struct.pack("<II", 0x05000300, 4)
        with self.assertRaises(formfill.FormError):
            formfill.Form("a.hwp", build_cfb({"FileHeader": header.ljust(256, b"\0")}))
        with self.assertRaises(formfill.FormError):
            formfill.Form("a.hwp", b"not a document")
        with self.assertRaises(formfill.FormError):
            formfill.Form("a.pdf", b"%PDF")


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def tc(text, bold=False):
    rpr = "<w:rPr><w:b/><w:color w:val=\"1F4E79\"/></w:rPr>" if bold else "<w:rPr><w:color w:val=\"C00000\"/></w:rPr>"
    t = f"<w:t>{text}</w:t>" if text else ""
    return f'<w:tc><w:tcPr><w:shd w:fill="DDEBF7"/></w:tcPr><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r>{rpr}{t}</w:r></w:p></w:tc>'


def docx_form():
    rows = "".join(f"<w:tr>{tc(a, True)}{tc('')}</w:tr>" for a in ("일 시", "장 소", "협의 내용"))
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n<w:document {W} xmlns:x="urn:x"><w:body>'
           f'<w:p><w:r><w:t>2026 협의록</w:t></w:r></w:p><w:tbl>{rows}</w:tbl>'
           '<w:p><w:r><w:rPr><w:i/></w:rPr><w:t>작성자: {{작성자}}</w:t></w:r></w:p></w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>", compress_type=zipfile.ZIP_STORED)
        z.writestr("word/document.xml", doc, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("word/styles.xml", "<w:styles " + W + "><!-- 색·글꼴 --></w:styles>", compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def hwpx_form():
    hp = 'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"'

    def c(text):
        run = f'<hp:run charPrIDRef="9"><hp:t>{text}</hp:t></hp:run>' if text else '<hp:run charPrIDRef="9"/>'
        return (f'<hp:tc><hp:subList><hp:p id="0" paraPrIDRef="4">{run}'
                '<hp:linesegarray><hp:lineseg textpos="0"/></hp:linesegarray></hp:p></hp:subList></hp:tc>')
    rows = "".join(f"<hp:tr>{c(a)}{c('')}</hp:tr>" for a in ("일시", "장소"))
    sec = f'<?xml version="1.0" encoding="UTF-8"?><hs:sec xmlns:hs="urn:hs" {hp}><hp:p><hp:run><hp:tbl>{rows}</hp:tbl></hp:run></hp:p></hs:sec>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/hwp+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("Contents/header.xml", "<head>글꼴·색</head>", compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("Contents/section0.xml", sec, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("Preview/PrvImage.png", PNG, compress_type=zipfile.ZIP_STORED)
    return buf.getvalue()


class ZipFormTests(unittest.TestCase):
    def test_docx_keeps_everything_but_filled_cells(self):
        data = docx_form()
        f = formfill.Form("서식.docx", data)
        fields = f.fields()
        self.assertEqual([x["label"] for x in fields], ["일 시", "장 소", "협의 내용", "작성자"])
        ids = {x["label"]: x["id"] for x in fields}
        done = f.fill({ids["일 시"]: "11월 3일", ids["협의 내용"]: "- 수행평가\n- 실험 안전", ids["작성자"]: "가짜교사"})
        self.assertEqual(sorted(done), sorted([ids["일 시"], ids["협의 내용"], ids["작성자"]]))
        out = f.to_bytes()
        zi, zo = zipfile.ZipFile(io.BytesIO(data)), zipfile.ZipFile(io.BytesIO(out))
        self.assertEqual([i.filename for i in zi.infolist()], [i.filename for i in zo.infolist()])
        self.assertEqual([i.compress_type for i in zi.infolist()], [i.compress_type for i in zo.infolist()])
        self.assertEqual(zi.read("word/styles.xml"), zo.read("word/styles.xml"))
        old, new = zi.read("word/document.xml"), zo.read("word/document.xml")
        self.assertTrue(new.startswith(old[:old.index(b"<w:tbl>")]))      # 선언·이름공간 그대로
        self.assertEqual(new.count(b'w:color w:val="C00000"'), 4)          # 빈칸 모양(빨강)이 새 문단에도
        self.assertIn("가짜교사".encode(), new)
        self.assertIn("실험 안전".encode(), new)
        self.assertIn(b"<w:i/>", new)

    def test_hwpx_drops_lineseg_only_in_filled_cell(self):
        data = hwpx_form()
        f = formfill.Form("서식.hwpx", data)
        self.assertEqual([x["label"] for x in f.fields()], ["일시", "장소"])
        f.fill({"F2": "과학실 2"})
        out = f.to_bytes()
        zo = zipfile.ZipFile(io.BytesIO(out))
        sec = zo.read("Contents/section0.xml")
        self.assertIn('<hp:run charPrIDRef="9"><hp:t>과학실 2</hp:t></hp:run>'.encode(), sec)
        self.assertEqual(sec.count(b"<hp:linesegarray>"), 3)
        self.assertEqual(zo.read("Contents/header.xml"), "<head>글꼴·색</head>".encode())
        self.assertEqual(f.preview_png(), PNG)
        self.assertIn("과학실 2", f.text())


class AnswerTests(unittest.TestCase):
    def test_parse_answer(self):
        ans = "다음과 같습니다\nP1: 2026 협의록\n**F2**: 첫 줄\n- 둘째\n- 셋째\nP9: 없는 자리\nP4:  \n"
        got = formfill.parse_answer(ans, {"P1", "F2", "P4"})
        self.assertEqual(got, {"P1": "2026 협의록", "F2": "첫 줄\n- 둘째\n- 셋째"})

    def test_slot_lines_shortens_old_text(self):
        line = formfill.slot_lines([{"id": "P3", "label": "협의내용", "text": "가" * 100}])
        self.assertIn("…(지금 100자)", line)
        self.assertLess(len(line), 70)


if __name__ == "__main__":
    unittest.main()
