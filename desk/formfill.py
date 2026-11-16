"""문서 작성(0.6.1): 선생님 학교 서식(.hwp / .hwpx / .docx)을 그대로 두고 글자만 채우기 (표준 라이브러리만).

선생님 요청 2026-11-16 "기존 양식을 지키면서", "형식은 같은데 색과 모양이 달라" → 원본 바이트를 그대로 두고 바꿀 곳만 바꿈.
- hwpx·docx (ZipForm): 본문 XML 을 다시 쓰지 않고, 표의 '이름 칸 옆 빈칸'과 {{자리표시}} 의 문단만 잘라 바꿈.
  빈칸 첫 문단의 글자 모양·문단 모양을 그대로 씀. 나머지 파일(글꼴·색·스타일)은 바이트 그대로.
- hwp (HwpForm, 5.x): 본문 레코드 중 바꿀 문단의 PARA_HEADER·TEXT·CHAR_SHAPE·LINE_SEG 만 새로 씀.
  글자 모양 번호(굵게·색)·문단 모양은 원래 문단 것. 표·그림이 든 문단은 그대로. DocInfo 등 다른 스트림도 그대로.
  복합 문서는 바뀐 본문만 새 자리에 쓰고 옛 자리는 0 으로 지움. 작년 내용이 채워진 서식도 '문단 자리'로 바꿀 수 있음.
- 미리보기 그림: 한글이 저장한 hwp(PrvImage)·hwpx(Preview/PrvImage.png) 의 첫 쪽 그림을 화면에 보여 줌.
"""

import io
import re
import struct
import zipfile
import zlib
import xml.parsers.expat
from xml.sax.saxutils import escape

from .cfb import CompoundFile, CFBError, ENDOFCHAIN, FREESECT

SUPPORTED = (".hwp", ".hwpx", ".docx")
MAX_FORM_BYTES = 10 * 1024 * 1024


class FormError(Exception):
    """화면에 그대로 보여 줄 한국어 메시지."""



KINDS = {
    ".hwpx": {"parts": re.compile(r"Contents/section\d+\.xml$"), "run": "run", "seg": "linesegarray"},
    ".docx": {"parts": re.compile(r"word/(document|header\d*|footer\d*)\.xml$"), "run": "r", "seg": None},
}
PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
MAX_LABEL = 12   # 이름 칸으로 볼 최대 글자 수


def label_key(text):
    return re.sub(r"[\s:：·()（）\[\]]", "", text or "")


class Node:
    __slots__ = ("tag", "qname", "attrs", "children", "parent", "s", "se", "es", "e", "text")

    def __init__(self, qname, attrs, parent, s):
        self.qname, self.tag = qname, qname.rsplit(":", 1)[-1]
        self.attrs, self.parent, self.children, self.s = attrs, parent, [], s
        self.se = self.es = self.e = None
        self.text = []

    def iter(self, tag):
        if self.tag == tag:
            yield self
        for c in self.children:
            yield from c.iter(tag)


def _tag_end(data, i):
    """i 에서 시작하는 태그가 끝나는 '>' 다음 위치 (따옴표 안의 '>' 는 건너뜀)."""
    q = None
    while True:
        ch = data[i]
        if q:
            if ch == q:
                q = None
        elif ch in (34, 39):
            q = ch
        elif ch == 62:
            return i + 1
        i += 1


def parse(data):
    """expat 으로 읽으며 요소마다 원본 바이트 위치를 기억."""
    p = xml.parsers.expat.ParserCreate()   # 이름공간 처리 없이 접두어 그대로 (hp:t, w:t)
    root = Node("#doc", {}, None, 0)
    stack = [root]

    def start(name, attrs):
        s = p.CurrentByteIndex
        n = Node(name, attrs, stack[-1], s)
        n.se = _tag_end(data, s)
        stack[-1].children.append(n)
        stack.append(n)

    def end(name):
        n = stack.pop()
        i = p.CurrentByteIndex
        if data[n.se - 2] == 47:            # <a ... />
            n.es = n.e = n.se
        else:
            n.es, n.e = i, _tag_end(data, i)

    def chars(t):
        stack[-1].text.append(t)

    p.StartElementHandler, p.EndElementHandler, p.CharacterDataHandler = start, end, chars
    p.Parse(data, True)
    return root


class ZipForm:
    """hwpx·docx 양식: 표의 이름 칸 옆 빈칸과 {{자리표시}} 를 채움."""

    def __init__(self, data, ext):
        self.ext = ext
        self.k = KINDS[self.ext]
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise FormError("서식 파일을 열 수 없습니다")
        self.parts = {}   # 이름 → [원본 바이트, 뿌리, 바꿀 곳 목록]
        for info in self.zip.infolist():
            if self.k["parts"].search(info.filename):
                data = self.zip.read(info.filename)
                try:
                    self.parts[info.filename] = [data, parse(data), []]
                except xml.parsers.expat.ExpatError:
                    raise FormError("서식 파일의 본문을 읽을 수 없습니다")
        if not self.parts:
            raise FormError("서식 파일에 본문이 없습니다")

    # ---- 글자 읽기 ----
    @staticmethod
    def para_text(p):
        return "".join("".join(t.text) for t in p.iter("t"))

    def _cell_paras(self, tc):
        nested = {id(x) for t in tc.iter("tbl") for x in t.iter("p")}
        return [x for x in tc.iter("p") if id(x) not in nested]

    def cell_text(self, tc):
        return "\n".join(self.para_text(p) for p in self._cell_paras(tc)).strip()

    def rows(self, part=None):
        for name, (_, root, _) in self.parts.items():
            if part and name != part:
                continue
            for tbl in root.iter("tbl"):
                for tr in (c for c in tbl.children if c.tag == "tr"):
                    yield name, [c for c in tr.children if c.tag == "tc"]

    def fields(self):
        """채울 자리 목록: 이름 칸(옆 칸이 비어 있음)과 {{자리표시}}."""
        out, seen = [], set()
        for _, cells in self.rows():
            for a, b in zip(cells, cells[1:]):
                name = self.cell_text(a)
                if 0 < len(label_key(name)) <= MAX_LABEL and not self.cell_text(b) and label_key(name) not in seen:
                    seen.add(label_key(name))
                    out.append(name.strip())
        for _, root, _ in self.parts.values():
            for p in root.iter("p"):
                for m in PLACEHOLDER.finditer(self.para_text(p)):
                    if label_key(m.group(1)) not in seen:
                        seen.add(label_key(m.group(1)))
                        out.append(m.group(1))
        return out

    # ---- 글자 쓰기: 원본 문단 바이트를 틀로 새 문단 바이트를 만듦 ----
    def _para_bytes(self, data, p, text):
        """문단 p 의 원본 바이트에서 글자만 text 로 (첫 <t> 에 넣고 나머지 <t> 는 비움). 줄 배치 기억은 뺌."""
        chunk = bytearray()
        pos = p.s
        ts = list(p.iter("t"))
        segs = [n for n in p.iter(self.k["seg"])] if self.k["seg"] else []
        cuts = []   # (시작, 끝, 새 바이트)
        enc = escape(text).encode("utf-8")
        if ts:
            first = ts[0]
            open_tag = data[first.s:first.se]
            if self.ext == ".docx" and b"xml:space" not in open_tag:
                open_tag = open_tag[:-1] + b' xml:space="preserve">'
            if first.se == first.e:   # <t/> → <t>글</t>
                open_tag = open_tag[:-2].rstrip() + b">"
            close = b"</" + first.qname.encode() + b">"
            cuts.append((first.s, first.e, open_tag + enc + close))
            for t in ts[1:]:
                cuts.append((t.s, t.e, b""))
        else:   # 빈 문단: 첫 글자 묶음 안에 <t> 를 넣음, 묶음도 없으면 새로
            runs = [c for c in p.children if c.tag == self.k["run"]]
            pre = "hp" if self.ext == ".hwpx" else "w"
            t_bytes = f"<{pre}:t>".encode() + enc + f"</{pre}:t>".encode() if self.ext == ".hwpx" else \
                f'<w:t xml:space="preserve">'.encode() + enc + b"</w:t>"
            if runs:
                r = runs[0]
                if r.se == r.e:   # <hp:run .../> → 열고 닫는 모양으로
                    open_r = data[r.s:r.se][:-2].rstrip() + b">"
                    cuts.append((r.s, r.e, open_r + t_bytes + b"</" + r.qname.encode() + b">"))
                else:
                    cuts.append((r.es, r.es, t_bytes))
            else:
                at = p.es if p.es != p.e else None
                run = (b'<hp:run charPrIDRef="0">' + t_bytes + b"</hp:run>") if self.ext == ".hwpx" else \
                    (b"<w:r>" + t_bytes + b"</w:r>")
                if at is None:   # <w:p/> 같은 빈 태그
                    cuts.append((p.s, p.e, data[p.s:p.se][:-2].rstrip() + b">" + run + b"</" + p.qname.encode() + b">"))
                else:
                    cuts.append((at, at, run))
        for sg in segs:
            cuts.append((sg.s, sg.e, b""))
        for s, e, new in sorted(cuts):
            chunk += data[pos:s] + new
            pos = e
        chunk += data[pos:p.e]
        return bytes(chunk)

    def _fill_cell(self, part, tc, value):
        data, _, edits = self.parts[part]
        paras = self._cell_paras(tc)
        first = paras[0]
        lines = str(value).splitlines() or [""]
        new = b""
        for i, line in enumerate(lines):
            b = self._para_bytes(data, first, line)
            if i and self.ext == ".hwpx":   # 늘린 문단은 한글이 새 문단에 쓰는 번호로
                b = re.sub(rb'^(<hp:p\b[^>]*?\bid=")[^"]*"', rb'\g<1>2147483648"', b, count=1)
            new += b
        edits.append((first.s, paras[-1].e, new))

    def fill(self, values):
        """values: {칸 이름: 내용}. 채운 칸 이름 목록을 돌려줌."""
        want = {label_key(k): v for k, v in values.items() if v not in (None, "")}
        done = []
        for part, cells in self.rows():
            for a, b in zip(cells, cells[1:]):
                key = label_key(self.cell_text(a))
                if key in want and not self.cell_text(b):
                    self._fill_cell(part, b, want[key])
                    done.append(key)
        for part, (data, root, edits) in self.parts.items():
            for p in root.iter("p"):
                joined = self.para_text(p)
                if "{{" not in joined:
                    continue
                if any(p.s >= s and p.e <= e for s, e, _ in edits):   # 이미 표 칸으로 채운 문단
                    continue
                new = PLACEHOLDER.sub(lambda m: str(want.get(label_key(m.group(1)), m.group(0))), joined)
                if new != joined:
                    edits.append((p.s, p.e, self._para_bytes(data, p, new)))
                    done += [label_key(m.group(1)) for m in PLACEHOLDER.finditer(joined)
                             if label_key(m.group(1)) in want]
        return done

    def to_bytes(self):
        """바꿀 곳만 잘라 바꾸고, 나머지 파일은 원래 순서·압축 방식·바이트 그대로."""
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for info in self.zip.infolist():
                data = self.zip.read(info.filename)
                if info.filename in self.parts and self.parts[info.filename][2]:
                    buf, pos = bytearray(), 0
                    for s, e, new in sorted(self.parts[info.filename][2]):
                        buf += data[pos:s] + new
                        pos = e
                    data = bytes(buf + data[pos:])
                # ZipInfo 를 복사해서 씀 (writestr 가 자리 정보를 바꿔, 원본 서식을 다시 읽을 수 없게 되지 않도록)
                copy = zipfile.ZipInfo(info.filename, info.date_time)
                for k in ("compress_type", "comment", "extra", "create_system", "external_attr", "internal_attr"):
                    setattr(copy, k, getattr(info, k))
                z.writestr(copy, data, compress_type=info.compress_type)
        return out.getvalue()

PARA_HEADER, PARA_TEXT, PARA_CHAR_SHAPE, PARA_LINE_SEG, PARA_RANGE_TAG = 66, 67, 68, 69, 70
CTRL_HEADER, LIST_HEADER, TABLE = 71, 72, 77
LAST_FLAG = 0x80000000
INLINE8 = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}




# ---------------- 레코드 ----------------
def read_records(raw):
    out, i = [], 0
    while i + 4 <= len(raw):
        h, = struct.unpack_from("<I", raw, i)
        tag, level, size = h & 0x3FF, (h >> 10) & 0x3FF, h >> 20
        i += 4
        if size == 0xFFF:
            size, = struct.unpack_from("<I", raw, i)
            i += 4
        out.append([tag, level, raw[i:i + size]])
        i += size
    return out


def write_records(recs):
    buf = bytearray()
    for tag, level, data in recs:
        n = len(data)
        if n < 0xFFF:
            buf += struct.pack("<I", tag | (level << 10) | (n << 20))
        else:
            buf += struct.pack("<II", tag | (level << 10) | (0xFFF << 20), n)
        buf += data
    return bytes(buf)


def para_text(data):
    """PARA_TEXT → 글 (표·탭 같은 제어 문자는 뺌)."""
    out, i, n = [], 0, len(data) // 2
    while i < n:
        c, = struct.unpack_from("<H", data, i * 2)
        if c in INLINE8:
            i += 8
            continue
        if c >= 32:
            out.append(chr(c))
        i += 1
    return "".join(out)


def ctrl_prefix(data):
    """글 앞쪽에 모여 있는 제어 문자 길이. 제어 문자가 글 뒤나 사이에도 있으면 -1 (그 문단은 바꾸지 않음)."""
    i, n, prefix, seen_text = 0, len(data) // 2, 0, False
    while i < n:
        c, = struct.unpack_from("<H", data, i * 2)
        if c in INLINE8:
            if seen_text:
                return -1
            i += 8
            prefix = i
            continue
        if c != 13:
            seen_text = True
        i += 1
    return prefix


# ---------------- 문단 자리 ----------------
class Slot:
    """문단 하나. recs[start:end] 가 이 문단의 레코드 (PARA_HEADER 와 그 아래)."""

    def __init__(self, section, start, end, level, cell):
        self.section, self.start, self.end, self.level, self.cell = section, start, end, level, cell
        self.id = None
        self.text = ""
        self.ctrl = False
        self.shapes = []
        self.ctrls = []      # 이 문단에 든 제어 (secd·cold·tbl ·gso 등)
        self.prefix = 0      # 글 앞에 붙은 제어 문자(구역·단 정의 등) 길이 (글자 단위)

    @property
    def editable(self):
        # 표·그림이 든 문단은 그대로. 구역·단 정의만 앞에 붙은 제목 같은 문단은 글만 바꿀 수 있음
        return not self.ctrl or (self.prefix > 0 and bool(self.text.strip())
                                 and not any(c in ("tbl ", "gso ", "eqed") for c in self.ctrls))

    def describe(self):
        where = f"[{self.cell['label']}] " if self.cell and self.cell.get("label") is not None else ""
        kind = "표·그림 문단(그대로 둠)" if not self.editable else (repr(self.text) if self.text else "(빈 줄)")
        return f"P{self.id} {where}{kind}"


class HwpForm:
    """한글(.hwp 5.x) 양식: 문단 글자만 바꿈 (글자·문단 모양 번호, 표·그림, DocInfo 는 그대로)."""

    def __init__(self, data):
        self.data = data
        try:
            self.cf = CompoundFile(self.data)
            head = self.cf.read("FileHeader")
        except (CFBError, KeyError):
            raise FormError("한글 문서를 열 수 없습니다")
        if head[:17] != b"HWP Document File":
            raise FormError("한글(.hwp) 문서가 아닙니다")
        props, = struct.unpack_from("<I", head, 36)
        self.compressed = bool(props & 1)
        if props & 2:
            raise FormError("암호가 걸린 문서는 채울 수 없습니다")
        if props & 4:
            raise FormError("배포용 문서는 채울 수 없습니다")
        self.sections = {}
        names = sorted((p for p in self.cf.list_streams() if p.startswith("BodyText/Section")),
                       key=lambda p: int(p.rsplit("Section", 1)[1]))
        for name in names:
            raw = self.cf.read(name)
            try:
                if self.compressed:
                    raw = zlib.decompress(raw, -15)
            except zlib.error:
                raise FormError("한글 문서 본문을 풀 수 없습니다")
            self.sections[name] = read_records(raw)
        self.slots = []
        self.cells = []
        for name, recs in self.sections.items():
            self._index(name, recs)
        for i, s in enumerate(self.slots):
            s.id = i + 1
        self._label_cells()
        self.changes = {}

    def _index(self, name, recs):
        """문단과 표 칸을 찾음. 칸 = LIST_HEADER(앞에 TABLE 이 있는 것) 다음부터 같은 높이의 다음 LIST 전까지."""
        cell_stack = []   # (칸 높이, 칸 정보)
        i = 0
        while i < len(recs):
            tag, level, data = recs[i]
            while cell_stack and level < cell_stack[-1][0]:
                cell_stack.pop()
            if tag == LIST_HEADER and len(data) >= 12:
                while cell_stack and cell_stack[-1][0] >= level:
                    cell_stack.pop()
                col, row = struct.unpack_from("<HH", data, 8)
                cell = {"section": name, "list": i, "col": col, "row": row, "slots": [], "level": level,
                        "table": self._table_of(recs, i)}
                self.cells.append(cell)
                cell_stack.append((level, cell))
            elif tag == PARA_HEADER:
                j = i + 1
                while j < len(recs) and recs[j][1] > level:
                    j += 1
                cell = cell_stack[-1][1] if cell_stack and cell_stack[-1][0] == level else None
                s = Slot(name, i, j, level, cell)
                mask, = struct.unpack_from("<I", data, 4)
                s.ctrl = mask != 0
                for t, lv, d in recs[i + 1:j]:
                    if t == PARA_TEXT and lv == level + 1:
                        s.text = para_text(d)
                        s.prefix = ctrl_prefix(d)
                    if t == CTRL_HEADER and lv == level + 1:
                        s.ctrls.append(d[:4][::-1].decode("latin-1"))
                    if t == PARA_CHAR_SHAPE and lv == level + 1:
                        s.shapes = [struct.unpack_from("<II", d, k) for k in range(0, len(d), 8)]
                if cell:
                    cell["slots"].append(s)
                self.slots.append(s)
                # 표가 든 문단은 그 안을 계속 읽음 (안쪽 표의 칸·문단도 자리가 됨)
                i += 1
                continue
            i += 1

    @staticmethod
    def _table_of(recs, i):
        """이 칸이 속한 표 레코드 위치 (칸 앞쪽으로 가장 가까운, 한 단계 위 높이의 TABLE)."""
        level = recs[i][1]
        for k in range(i - 1, -1, -1):
            if recs[k][0] == TABLE and recs[k][1] == level:
                return k
        return None

    def _label_cells(self):
        """같은 표·같은 줄에서 바로 왼쪽 칸의 글을 이 칸의 이름으로."""
        by = {}
        for c in self.cells:
            by[(c["section"], c["table"], c["row"], c["col"])] = c
        for c in self.cells:
            c["text"] = "".join(s.text for s in c["slots"]).strip()
        for c in self.cells:
            left = None
            for col in range(c["col"] - 1, -1, -1):
                left = by.get((c["section"], c["table"], c["row"], col))
                if left:
                    break
            c["label"] = left["text"].replace(" ", "") if left else c["text"].replace(" ", "")

    def form_map(self):
        return "\n".join(s.describe() for s in self.slots)

    # ---------------- 바꾸기 ----------------
    def set_text(self, slot_id, text):
        s = self.slots[slot_id - 1]
        if not s.editable:
            raise FormError(f"P{slot_id} 는 표·그림이 든 문단이라 바꾸지 않습니다")
        if any(ch in text for ch in "\t\r\n"):
            raise FormError("문단 하나에는 한 줄만 넣습니다")
        self.changes[slot_id] = text

    def _new_para(self, recs, s, text):
        head = bytearray(recs[s.start][2])
        old_n, = struct.unpack_from("<I", head, 0)
        lvl = s.level + 1
        old_text = next((d for t, lv, d in recs[s.start + 1:s.end] if t == PARA_TEXT and lv == lvl), b"")
        pre = old_text[:s.prefix * 2] if s.ctrl else b""     # 구역 정의 같은 앞 제어 문자는 그대로
        struct.pack_into("<I", head, 0, (len(pre) // 2 + len(text) + 1) | (old_n & LAST_FLAG))
        if not s.ctrl:
            struct.pack_into("<I", head, 4, 0)        # 제어 문자 없음
        # 글자 모양: 원래 글의 첫 '보이는 글자' 자리의 모양 (앞 띄어쓰기만 다른 모양인 경우가 있음)
        lead = len(pre) // 2 + len(s.text) - len(s.text.lstrip())
        shape = s.shapes[0][1] if s.shapes else 0
        for pos, sid in s.shapes:
            if pos <= lead:
                shape = sid
        first_shape = s.shapes[0][1] if s.shapes else shape
        struct.pack_into("<H", head, 12, 1)           # 글자 모양 개수
        struct.pack_into("<H", head, 14, 0)           # 영역 태그 개수
        struct.pack_into("<H", head, 16, 1)           # 줄 배치 개수
        shapes = [(0, shape)] if not pre else [(0, first_shape), (len(pre) // 2, shape)]
        if pre and first_shape == shape:
            shapes = [(0, shape)]
        struct.pack_into("<H", head, 12, len(shapes))
        new_text = [PARA_TEXT, lvl, pre + text.encode("utf-16-le") + b"\r\x00"] if (text or pre) else None
        new_shape = [PARA_CHAR_SHAPE, lvl, b"".join(struct.pack("<II", p, i) for p, i in shapes)]
        seg = next((d for t, lv, d in recs[s.start + 1:s.end] if t == PARA_LINE_SEG and lv == lvl), None)
        new_seg = None
        if seg:
            seg = bytearray(seg[:36])
            struct.pack_into("<I", seg, 0, 0)          # 첫 줄은 0번 글자부터 (나머지 줄은 한글이 다시 계산)
            new_seg = [PARA_LINE_SEG, lvl, bytes(seg)]
        else:
            struct.pack_into("<H", head, 16, 0)
        # 원래 순서대로: 글·글자 모양·줄 배치는 새것으로, 영역 태그는 빼고, 제어(구역 정의 등)는 그대로
        out = [[PARA_HEADER, s.level, bytes(head)]]
        if new_text and not any(t == PARA_TEXT and lv == lvl for t, lv, _ in recs[s.start + 1:s.end]):
            out.append(new_text)
        for t, lv, d in recs[s.start + 1:s.end]:
            if lv == lvl and t == PARA_TEXT:
                if new_text:
                    out.append(new_text)
            elif lv == lvl and t == PARA_CHAR_SHAPE:
                out.append(new_shape)
            elif lv == lvl and t == PARA_LINE_SEG:
                out.append(new_seg)
            elif lv == lvl and t == PARA_RANGE_TAG:
                continue
            else:
                out.append([t, lv, d])
        return out

    def section_bytes(self, name):
        recs = self.sections[name]
        edits = sorted(((s.start, s.end, s) for sid, s in ((k, self.slots[k - 1]) for k in self.changes)
                        if s.section == name), reverse=True)
        recs = [list(r) for r in recs]
        for start, end, s in edits:
            recs[start:end] = self._new_para(recs, s, self.changes[s.id])
        return write_records(recs)

    def preview_text(self):
        lines = []
        for s in self.slots:
            t = self.changes.get(s.id, s.text)
            if t:
                lines.append(t)
        return "\r\n".join(lines)

    def to_bytes(self):
        streams = {}
        for name in self.sections:
            raw = self.section_bytes(name)
            if any(s.section == name for s in (self.slots[k - 1] for k in self.changes)):
                if self.compressed:
                    c = zlib.compressobj(9, zlib.DEFLATED, -15)
                    streams[name] = c.compress(raw) + c.flush()
                else:
                    streams[name] = raw
        data = CfbPatch(self.data, self.cf)
        for name, payload in streams.items():
            data.replace_stream(name, payload)
        if self.cf.exists("PrvText"):
            old = self.cf.read("PrvText")
            new = self.preview_text().encode("utf-16-le")[:len(old) - 2]
            new = new[:len(new) // 2 * 2]
            data.overwrite_same_size("PrvText", new.ljust(len(old), b"\0"))
        return data.result()


# ---------------- 복합 문서 고치기 (바꾼 스트림만) ----------------
FATSECT = 0xFFFFFFFD


class CfbPatch:
    """복합 문서(CFB)를 통째로 다시 만들지 않고, 바꾼 스트림만 새 자리에 쓰고 디렉터리 항목·FAT·미니 FAT 만 고침.
    옛 자리는 0 으로 지움 (지운 글이 파일 안에 남지 않게)."""

    def __init__(self, data, cf):
        self.buf = bytearray(data)
        self.cf = cf
        self.ss = cf.ssize
        if (len(self.buf) - self.ss) % self.ss:
            self.buf += b"\0" * (self.ss - (len(self.buf) - self.ss) % self.ss)
        self.fat = list(cf.fat)
        self.minifat = list(cf.minifat)
        self.num_fat, self.first_dir = struct.unpack_from("<II", self.buf, 0x2C)
        self.first_minifat, self.num_minifat = struct.unpack_from("<II", self.buf, 0x3C)
        self.difat = list(struct.unpack_from("<109I", self.buf, 0x4C))
        if struct.unpack_from("<I", self.buf, 0x48)[0]:
            raise FormError("아주 큰 문서(DIFAT 섹터)는 아직 채울 수 없습니다")
        root = cf.entries[0]
        self.root_start, self.root_size = root["start"], root["size"]

    def _off(self, sect):
        return (sect + 1) * self.ss

    @staticmethod
    def _chain(start, table):
        out, s, seen = [], start, set()
        while s not in (ENDOFCHAIN, FREESECT) and s < len(table) and s not in seen:
            seen.add(s)
            out.append(s)
            s = table[s]
        return out

    def _alloc(self):
        """파일 끝에 섹터 하나를 붙이고 번호를 돌려줌 (FAT 자리가 모자라면 FAT 섹터도 붙임)."""
        while True:
            n = (len(self.buf) - self.ss) // self.ss
            if n < len(self.fat):
                self.buf += b"\0" * self.ss
                self.fat[n] = ENDOFCHAIN
                return n
            if FREESECT not in self.difat:
                raise FormError("문서가 너무 커서 채울 수 없습니다")
            self.buf += b"\0" * self.ss          # 새 FAT 섹터
            self.fat.extend([FREESECT] * (self.ss // 4))
            self.difat[self.difat.index(FREESECT)] = n
            self.fat[n] = FATSECT
            self.num_fat += 1

    def _grow_chain(self, start, count):
        """start 에서 시작하는 사슬을 count 섹터가 되게 늘림. 새 시작 번호를 돌려줌."""
        chain = self._chain(start, self.fat)
        while len(chain) < count:
            s = self._alloc()
            if chain:
                self.fat[chain[-1]] = s
            chain.append(s)
        return chain[0] if chain else ENDOFCHAIN

    def _dir_offset(self, idx):
        per = self.ss // 128
        sects = self._chain(self.first_dir, self.fat)
        return self._off(sects[idx // per]) + (idx % per) * 128

    def _mini_offset(self, msect):
        chain = self._chain(self.root_start, self.fat)
        pos = msect * 64
        return self._off(chain[pos // self.ss]) + pos % self.ss

    def _wipe_old(self, e):
        if e["size"] < self.cf.cutoff:
            for m in self._chain(e["start"], self.minifat):
                o = self._mini_offset(m)
                self.buf[o:o + 64] = b"\0" * 64
                self.minifat[m] = FREESECT
        else:
            for s in self._chain(e["start"], self.fat):
                o = self._off(s)
                self.buf[o:o + self.ss] = b"\0" * self.ss
                self.fat[s] = FREESECT

    def replace_stream(self, path, payload):
        idx = self.cf.paths[path]
        e = self.cf.entries[idx]
        self._wipe_old(e)
        if len(payload) < self.cf.cutoff:
            start = self._put_mini(payload)
        else:
            n = -(-len(payload) // self.ss)
            sects = [self._alloc() for _ in range(n)]
            for a, b in zip(sects, sects[1:]):
                self.fat[a] = b
            for k, s in enumerate(sects):
                o = self._off(s)
                part = payload[k * self.ss:(k + 1) * self.ss]
                self.buf[o:o + len(part)] = part
            start = sects[0]
        struct.pack_into("<IQ", self.buf, self._dir_offset(idx) + 116, start, len(payload))

    def _put_mini(self, payload):
        """미니 스트림 끝에 붙임 (루트 항목의 미니 스트림과 미니 FAT 을 필요한 만큼 늘림)."""
        m = -(-len(payload) // 64)
        first = -(-self.root_size // 64)
        need = first + m
        self.root_start = self._grow_chain(self.root_start, -(-need * 64 // self.ss))
        self.root_size = need * 64
        if len(self.minifat) < need:
            self.minifat.extend([FREESECT] * (need - len(self.minifat)))
        for k in range(m):
            self.minifat[first + k] = first + k + 1 if k < m - 1 else ENDOFCHAIN
            o = self._mini_offset(first + k)
            part = payload[k * 64:(k + 1) * 64]
            self.buf[o:o + 64] = part.ljust(64, b"\0")
        return first

    def overwrite_same_size(self, path, payload):
        e = self.cf.entries[self.cf.paths[path]]
        assert len(payload) == e["size"]
        if e["size"] < self.cf.cutoff:
            for k, m in enumerate(self._chain(e["start"], self.minifat)):
                o = self._mini_offset(m)
                part = payload[k * 64:(k + 1) * 64]
                self.buf[o:o + len(part)] = part
        else:
            for k, s in enumerate(self._chain(e["start"], self.fat)):
                o = self._off(s)
                part = payload[k * self.ss:(k + 1) * self.ss]
                self.buf[o:o + len(part)] = part

    def result(self):
        # 미니 FAT 쓰기 (섹터가 모자라면 늘림)
        if self.minifat:
            raw = struct.pack(f"<{len(self.minifat)}I", *self.minifat)
            raw = raw.ljust(-(-len(raw) // self.ss) * self.ss, b"\xff")
            self.first_minifat = self._grow_chain(self.first_minifat if self.num_minifat else ENDOFCHAIN,
                                                  len(raw) // self.ss)
            chain = self._chain(self.first_minifat, self.fat)
            self.num_minifat = len(chain)
            for k, s in enumerate(chain):
                o = self._off(s)
                self.buf[o:o + self.ss] = raw[k * self.ss:(k + 1) * self.ss].ljust(self.ss, b"\xff")
        # 루트 항목 (미니 스트림 위치·크기)
        struct.pack_into("<IQ", self.buf, self._dir_offset(0) + 116, self.root_start, self.root_size)
        struct.pack_into("<II", self.buf, 0x3C, self.first_minifat, self.num_minifat)
        struct.pack_into("<I", self.buf, 0x2C, self.num_fat)
        struct.pack_into("<109I", self.buf, 0x4C, *self.difat)
        raw = struct.pack(f"<{len(self.fat)}I", *self.fat)
        fat_sects = [s for s in self.difat if s != FREESECT][:self.num_fat]
        for k, s in enumerate(fat_sects):
            o = self._off(s)
            self.buf[o:o + self.ss] = raw[k * self.ss:(k + 1) * self.ss]
        return bytes(self.buf)


# ---------------- 한 가지 모양으로 ----------------
def _is_label_cell(form, c, right):
    """hwp 표의 이름 칸: 맨 왼쪽이거나, 오른쪽에 칸이 더 있는 숫자 없는 짧은 글 (예: '장  소')."""
    t = c["text"].replace(" ", "")
    has_right = c["col"] < right[(c["section"], c["table"], c["row"])]
    return c["col"] == 0 or (has_right and 0 < len(t) <= 6 and not re.search(r"\d", t))


def hwp_fillable(form):
    """hwp 에서 채울 자리: 맨 위 제목, 바깥 표의 내용 칸 문단(이름 칸 제외), 빈 괄호 '(    )' 가 있는 문단."""
    if not form.cells:
        return [s for s in form.slots if s.editable]
    right = {}
    for c in form.cells:
        key = (c["section"], c["table"], c["row"])
        right[key] = max(right.get(key, 0), c["col"])
    top_level = min(x["level"] for x in form.cells)
    first_text = next((x for x in form.slots if x.text.strip()), None)
    out = []
    for s in form.slots:
        if not s.editable:
            continue
        if s.cell is None:
            if s is first_text:
                out.append(s)
            continue
        blank = re.search(r"\(\s{4,}\)", s.text)
        top = s.cell["level"] == top_level
        if blank or (top and not _is_label_cell(form, s.cell, right)):
            out.append(s)
    return out


class Form:
    """서식 하나: fields() 로 채울 자리 [{id, label, text}], fill({id: 글}) 로 채우고 to_bytes() 로 받음."""

    def __init__(self, name, data):
        ext = "." + (name or "").rsplit(".", 1)[-1].lower()
        if ext not in SUPPORTED:
            raise FormError("서식은 .hwp · .hwpx · .docx 만 됩니다")
        if len(data) > MAX_FORM_BYTES:
            raise FormError("서식 파일이 너무 큽니다 (10MB 초과)")
        self.ext = ext
        self.kind = ext[1:]
        if ext == ".hwp":
            self.f = HwpForm(data)
            self._slots = {f"P{s.id}": s for s in hwp_fillable(self.f)}
        else:
            self.f = ZipForm(data, ext)
            self._labels = {f"F{i}": lab for i, lab in enumerate(self.f.fields(), 1)}

    def fields(self):
        if self.ext == ".hwp":
            return [{"id": k, "label": (s.cell or {}).get("label") or ("제목" if s.cell is None else ""),
                     "text": s.text} for k, s in self._slots.items()]
        return [{"id": k, "label": lab, "text": ""} for k, lab in self._labels.items()]

    def fill(self, values):
        """values: {자리 id: 새 글}. 실제로 채운 id 목록."""
        done = []
        if self.ext == ".hwp":
            for k, v in values.items():
                s = self._slots.get(k)
                if s is None:
                    continue
                v = re.sub(r"[\t\r\n]+", " ", str(v)).rstrip()
                self.f.set_text(s.id, v)
                done.append(k)
            return done
        by_label = {self._labels[k]: v for k, v in values.items() if k in self._labels}
        filled = set(self.f.fill(by_label))
        return [k for k, lab in self._labels.items() if label_key(lab) in filled]

    def to_bytes(self):
        return self.f.to_bytes()

    def preview_png(self):
        """한글이 저장해 둔 첫 쪽 미리보기 그림 (PNG 일 때만)."""
        try:
            if self.ext == ".hwp":
                data = self.f.cf.read("PrvImage") if self.f.cf.exists("PrvImage") else b""
            elif self.ext == ".hwpx":
                data = self.f.zip.read("Preview/PrvImage.png")
            else:
                return None
        except (KeyError, CFBError):
            return None
        return data if data[:8] == b"\x89PNG\r\n\x1a\n" else None

    def text(self):
        """채운 뒤 글 (확인용)."""
        from . import docs
        data = self.to_bytes()
        try:
            if self.ext == ".hwp":
                return docs.hwp_text(data)
            if self.ext == ".hwpx":
                return docs.hwpx_text(data)
            from .docwrite import docx_text
            return docx_text(data)
        except Exception:   # 확인용이라 실패해도 채운 파일은 그대로
            return ""


FILL_SYSTEM = ("너는 한국 중·고등학교 교사의 문서 작성 비서다. 학교 서식의 빈칸을 참고 자료에 있는 사실만으로 채운다. "
               "자료에 없는 날짜·숫자·이름은 지어내지 말고 '(확인 필요)' 라고 쓴다.")
FILL_INSTRUCTION = """[서식 채우기] 오늘은 {today}이다. 교사가 할 작업: "{task}"
아래는 학교 서식의 채울 자리다. 지금 글이 있으면 작년 내용이며 길이·모양의 본보기다.
참고 자료와 작업 내용에 있는 사실만으로, 바꿀 자리만 이 모양으로 써 줘 (앞뒤 인사말 없이, 한 자리 한 줄):
자리번호: 새 글
F 로 시작하는 자리는 여러 줄이 필요하면 다음 줄부터 '- ' 로 이어 쓴다. 그대로 둘 자리는 쓰지 않는다.

서식 자리:
{slots}

참고 자료:
{materials}
"""


def slot_lines(fields, limit=40):
    """AI 에 보낼 자리 목록 (지금 글은 앞 40자만 — 보내는 양을 줄임)."""
    out = []
    for f in fields:
        t = f["text"] or "(빈칸)"
        if len(t) > limit:
            t = t[:limit] + f"…(지금 {len(f['text'])}자)"
        out.append(f"{f['id']} [{f['label']}] {t}")
    return "\n".join(out)


def parse_answer(text, ids):
    """AI 답 'P4: 글' / 'F2: 글' (+ '- ' 이어지는 줄) → {id: 글}."""
    out, cur = {}, None
    for line in (text or "").splitlines():
        m = re.match(r"^\s*\**\s*([PF]\d+)\s*\**\s*[:：]\s?(.*)$", line)
        if m and m.group(1) in ids:
            cur = m.group(1)
            out[cur] = m.group(2).rstrip()
        elif cur and cur.startswith("F") and line.strip().startswith("-"):
            out[cur] = (out[cur] + "\n" if out[cur] else "") + line.strip()
        else:
            cur = None
    return {k: v for k, v in out.items() if v.strip()}
