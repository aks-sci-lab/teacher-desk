import importlib.util
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from desk import docs
from desk.cfb import CompoundFile, CFBError
from tests.hwp_fixture import build_cfb, build_hwp

LINES = ["2026학년도 학업성적관리위원회 개최 안내", "1. 일시: 2026. 11. 3.(화) 15:00", "2. 장소: 교무실"]


def write(tmp, name, data):
    p = Path(tmp) / name
    p.write_bytes(data)
    return p


class HwpTests(unittest.TestCase):
    def test_compressed_hwp(self):
        text = docs.hwp_text(build_hwp(LINES))
        self.assertEqual(text.split("\n"), LINES)  # 두 번째 문단 앞의 표 제어 문자(8글자)는 건너뜀

    def test_uncompressed_large_section_uses_regular_sectors(self):
        many = [f"문단 {i} 내용입니다" for i in range(400)]
        data = build_hwp(many, compressed=False)
        cf = CompoundFile(data)
        self.assertGreaterEqual(len(cf.read("BodyText/Section0")), 4096)
        self.assertEqual(docs.hwp_text(data).split("\n"), many)

    def test_distribution_document_reads_preview_only(self):
        text = docs.hwp_text(build_hwp([], distribution=True, preview="미리보기 글"))
        self.assertIn("미리보기 글", text)
        self.assertIn("배포용", text)

    def test_not_hwp(self):
        with self.assertRaises(docs.DocError):
            docs.hwp_text(b"not a compound file at all" * 30)
        with self.assertRaises(docs.DocError):
            docs.hwp_text(build_cfb({"Other": b"x" * 10}))

    def test_cfb_rejects_cycles(self):
        data = bytearray(build_hwp(LINES))
        # FAT 첫 칸(디렉터리 시작 섹터 다음)을 자기 자신으로 바꿔 무한 연결을 만듦
        import struct
        first_dir = struct.unpack_from("<I", data, 0x30)[0]
        struct.pack_into("<I", data, 512 + first_dir * 4, first_dir)
        with self.assertRaises((CFBError, docs.DocError)):
            CompoundFile(bytes(data))


class OtherFormatTests(unittest.TestCase):
    def test_received_xlsx_text_and_original_preserved(self):
        from tests.xlsx_fixture import build_xlsx
        data = build_xlsx([("안내", [["회의", "11월 3일"], ["장소", "교무실"]], [])])
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "받은 파일.xlsx", data)
            text = docs.read_document(path)
            self.assertIn("회의", text)
            self.assertIn("11월 3일", text)
            self.assertIn("교무실", text)
            self.assertEqual(path.read_bytes(), data)
            path.write_bytes(b"invalid")
            with self.assertRaises(docs.DocError):
                docs.read_document(path)

    def test_hwpx(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Contents/section0.xml",
                       '<s xmlns:hp="urn:hp"><hp:p><hp:run><hp:t>연수 안내</hp:t></hp:run></hp:p>'
                       '<hp:p><hp:run><hp:t>신청<hp:tab/>11월 2일까지</hp:t></hp:run></hp:p></s>')
        self.assertEqual(docs.tidy(docs.hwpx_text(buf.getvalue())), "연수 안내\n신청 11월 2일까지")

    def test_text_encodings_and_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(docs.read_document(write(tmp, "a.txt", "회의 안내".encode("cp949"))), "회의 안내")
            self.assertEqual(docs.read_document(write(tmp, "b.txt", "\ufeff회의".encode("utf-8"))), "회의")
            html = "<html><style>x{}</style><body><p>첫 줄</p>둘째&nbsp;줄<script>alert(1)</script></body></html>"
            self.assertEqual(docs.read_document(write(tmp, "c.html", html.encode())), "첫 줄\n둘째 줄")
            with self.assertRaises(docs.DocError):
                docs.read_document(write(tmp, "d.docx", b"PK"))

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "pypdf 없음")
    def test_pdf_errors_are_friendly(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(docs.DocError):
                docs.read_document(write(tmp, "bad.pdf", b"%PDF-1.4 broken"))


if __name__ == "__main__":
    unittest.main()
