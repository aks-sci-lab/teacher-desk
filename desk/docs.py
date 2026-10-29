"""저장한 문서에서 글자만 뽑기: .hwp .hwpx .pdf .xlsx .txt .html

- .hwp  : 표준 라이브러리만으로 읽음 (cfb.py). 배포용·암호 문서는 읽을 수 없음
- .hwpx : zip 안의 XML 을 읽음
- .pdf  : pypdf 가 설치되어 있어야 함 (pip install pypdf)
"""

import html
import re
import struct
import zipfile
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path

from .cfb import CompoundFile, CFBError
from . import xlsx

SUPPORTED = (".hwp", ".hwpx", ".pdf", ".xlsx", ".txt", ".html", ".htm")
MAX_BYTES = 50 * 1024 * 1024


class DocError(Exception):
    """읽을 수 없는 문서 (사용자에게 그대로 보여 줄 한국어 메시지)."""


def read_document(path):
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise DocError(f"지원하지 않는 형식입니다: {ext}")
    if path.stat().st_size > MAX_BYTES:
        raise DocError("파일이 너무 큽니다 (50MB 초과)")
    data = path.read_bytes()
    if ext == ".hwp":
        text = hwp_text(data)
    elif ext == ".hwpx":
        text = hwpx_text(data)
    elif ext == ".pdf":
        text = pdf_text(path)
    elif ext == ".xlsx":
        try:
            sheets = xlsx.read(data)
        except (xlsx.XlsxError, zipfile.BadZipFile, ET.ParseError, KeyError, ValueError) as e:
            raise DocError("엑셀 파일을 읽을 수 없습니다. Excel에서 .xlsx로 다시 저장해 주세요.") from e
        text = "\n\n".join(s["name"] + "\n" + "\n".join(
            "\t".join(str(v) if v is not None else "" for v in row) for row in s["rows"]
        ) for s in sheets)
    elif ext in (".html", ".htm"):
        text = html_text(decode_text(data))
    else:
        text = decode_text(data)
    return tidy(text)


def fix_surrogates(text):
    """이모지처럼 두 칸(서로게이트 짝)으로 적힌 글자를 하나로 합치고, 짝 없는 반쪽은 '�' 로.

    RTF 의 \\uN·한글 문서의 글자는 16비트씩 읽으므로 이모지가 반쪽 둘로 나뉨.
    반쪽이 남아 있으면 저장(UTF-8)할 때 'surrogates not allowed' 오류로 수집이 멈춤.
    """
    if not any("\ud800" <= c <= "\udfff" for c in text):
        return text
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def tidy(text):
    text = fix_surrogates(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"[ \t　]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)   # 줄 앞뒤 빈칸 (HTML 태그 자리)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def decode_text(data):
    for enc in ("utf-8-sig", "cp949", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


# 글자 꾸밈 태그: 글자 사이에 끼어 있어 지우기만 함 (공백으로 바꾸면 '계획 을' 처럼 낱말이 갈라짐)
INLINE_TAGS = r"span|font|b|i|u|s|em|strong|a|small|big|sub|sup|mark|o:p"


def html_text(s):
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", s)
    s = re.sub(rf"(?i)</?(?:{INLINE_TAGS})\b[^>]*>", "", s)
    s = re.sub(r"(?i)</t[dh]>", "\t", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return html.unescape(s)


# ---- 한글 5.0 (.hwp) ----
HWPTAG_PARA_TEXT = 0x10 + 51
# 8글자(16바이트)를 차지하는 제어 문자 (한글 문서 형식 5.0 명세)
_WIDE_CONTROLS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}


def hwp_text(data):
    try:
        cf = CompoundFile(data)
    except CFBError as e:
        raise DocError(f"한글 파일을 열 수 없습니다 ({e})")
    if not cf.exists("FileHeader"):
        raise DocError("한글 5.0 파일이 아닙니다 (FileHeader 없음)")
    header = cf.read("FileHeader")
    if not header.startswith(b"HWP Document File"):
        raise DocError("한글 5.0 파일이 아닙니다")
    props = struct.unpack_from("<I", header, 36)[0]
    compressed, encrypted, distribution = props & 1, props & 2, props & 4
    if encrypted:
        raise DocError("암호가 걸린 한글 문서입니다")

    sections = sorted((p for p in cf.list_streams() if p.startswith("BodyText/Section")),
                      key=lambda p: int(re.sub(r"\D", "", p) or 0))
    if distribution or not sections:
        # 배포용 문서는 본문이 암호화되어 있어 미리보기 글(앞부분)만 읽음
        if cf.exists("PrvText"):
            return cf.read("PrvText").decode("utf-16-le", "replace") + \
                "\n\n(배포용 문서라 앞부분 미리보기만 읽었습니다)"
        raise DocError("배포용 문서라 본문을 읽을 수 없습니다")

    parts = []
    for p in sections:
        raw = cf.read(p)
        if compressed:
            try:
                raw = zlib.decompress(raw, -15)
            except zlib.error:
                raise DocError(f"{p} 압축을 풀 수 없습니다")
        parts.append(hwp_section_text(raw))
    return "\n".join(parts)


def hwp_section_text(raw):
    """레코드(태그·크기 헤더 + 내용)를 따라가며 문단 글자(PARA_TEXT)만 모읍니다."""
    out, pos, n = [], 0, len(raw)
    while pos + 4 <= n:
        h = struct.unpack_from("<I", raw, pos)[0]
        pos += 4
        tag, size = h & 0x3FF, (h >> 20) & 0xFFF
        if size == 0xFFF:
            if pos + 4 > n:
                break
            size = struct.unpack_from("<I", raw, pos)[0]
            pos += 4
        body = raw[pos:pos + size]
        pos += size
        if tag == HWPTAG_PARA_TEXT:
            out.append(hwp_para_text(body))
    return "\n".join(out)


def hwp_para_text(body):
    chars, i, n = [], 0, len(body) // 2
    while i < n:
        c = struct.unpack_from("<H", body, i * 2)[0]
        if c < 32:
            if c in _WIDE_CONTROLS:
                if c == 9:
                    chars.append("\t")
                i += 8
                continue
            if c in (10, 13):
                chars.append("\n")
            elif c in (24, 30, 31):
                chars.append(" " if c != 24 else "-")
            i += 1
            continue
        chars.append(chr(c))
        i += 1
    return "".join(chars).rstrip("\n")


# ---- 한글 새 형식 (.hwpx) ----
def hwpx_text(data):
    import io
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise DocError("hwpx 파일을 열 수 없습니다")
    names = sorted((n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n, re.I)),
                   key=lambda n: int(re.sub(r"\D", "", n) or 0))
    if not names:
        raise DocError("hwpx 본문(Contents/section*.xml)이 없습니다")
    parts = []
    for name in names:
        info = z.getinfo(name)
        if info.file_size > MAX_BYTES:
            raise DocError("hwpx 본문이 너무 큽니다")
        buf = []
        for event, el in ET.iterparse(io.BytesIO(z.read(name)), events=("end",)):
            local = el.tag.rsplit("}", 1)[-1]
            if local == "t":
                # <hp:t>앞<hp:tab/>뒤</hp:t> 처럼 글자 사이에 탭·줄바꿈 요소가 끼어 있음
                buf.append(el.text or "")
                for child in el:
                    name = child.tag.rsplit("}", 1)[-1]
                    buf.append("\t" if name == "tab" else "\n" if name == "lineBreak" else "")
                    buf.append(child.tail or "")
            elif local == "p":
                buf.append("\n")
        parts.append("".join(buf))
    return "\n".join(parts)


# ---- PDF ----
def pdf_text(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise DocError("PDF 를 읽으려면 pypdf 를 설치하세요: 명령 프롬프트에서  pip install pypdf")
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise DocError("암호가 걸린 PDF 입니다")
        return "\n".join((page.extract_text() or "") for page in reader.pages[:200])
    except DocError:
        raise
    except Exception as e:  # pypdf 내부 오류는 종류가 많아 한 번에 처리
        raise DocError(f"PDF 를 읽을 수 없습니다 ({type(e).__name__})")
