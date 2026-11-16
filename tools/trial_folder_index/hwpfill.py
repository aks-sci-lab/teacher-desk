"""시험용: 한글 문서(.hwp, 5.x)의 양식을 그대로 두고 글자만 바꾸기 (표준 라이브러리만).

선생님 요청 2026-11-16 "이 서식에 맞춰서 해봐" (서식1 교과협의록.hwp, 작년 내용이 채워진 양식).

방법
- 본문(BodyText/Section*)을 레코드 단위로 읽어 '문단 자리' 목록을 만든다. 자리마다: 어느 표 칸인지(왼쪽 이름 칸),
  지금 글, 글자 모양 번호, 표·그림이 들어 있는지.
- 바꿀 문단은 그 문단의 PARA_HEADER·PARA_TEXT·PARA_CHAR_SHAPE·PARA_LINE_SEG 만 새로 쓴다.
  문단 모양·스타일·글자 모양 번호(굵게·색 등)는 원래 문단 것을 그대로 쓴다. 표·그림이 든 문단은 건드리지 않는다.
- DocInfo(글꼴·색·테두리 정의)와 다른 스트림은 바이트 그대로. 복합 문서(CFB)도 통째로 다시 만들지 않고,
  바뀐 본문만 파일 끝에 새 섹터로 붙이고 디렉터리 항목 하나와 FAT 만 고친다. 옛 본문 자리는 0으로 지운다.
- 미리보기 글(PrvText)은 새 글로 바꾼다. 미리보기 그림(PrvImage)은 만들 수 없어 그대로 둔다 (한글에서 저장하면 새로 만듦).
"""

import struct
import zlib

from desk.cfb import CompoundFile, ENDOFCHAIN, FREESECT

PARA_HEADER, PARA_TEXT, PARA_CHAR_SHAPE, PARA_LINE_SEG, PARA_RANGE_TAG = 66, 67, 68, 69, 70
CTRL_HEADER, LIST_HEADER, TABLE = 71, 72, 77
LAST_FLAG = 0x80000000
INLINE8 = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}


class HwpError(Exception):
    pass


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
    def __init__(self, path):
        self.path = str(path)
        self.data = open(self.path, "rb").read()
        self.cf = CompoundFile(self.data)
        head = self.cf.read("FileHeader")
        if head[:17] != b"HWP Document File":
            raise HwpError("한글(.hwp) 문서가 아닙니다")
        props, = struct.unpack_from("<I", head, 36)
        self.compressed = bool(props & 1)
        if props & 2:
            raise HwpError("암호가 걸린 문서는 채울 수 없습니다")
        if props & 4:
            raise HwpError("배포용 문서는 채울 수 없습니다")
        self.sections = {}
        names = sorted((p for p in self.cf.list_streams() if p.startswith("BodyText/Section")),
                       key=lambda p: int(p.rsplit("Section", 1)[1]))
        for name in names:
            raw = self.cf.read(name)
            if self.compressed:
                raw = zlib.decompress(raw, -15)
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
            raise HwpError(f"P{slot_id} 는 표·그림이 든 문단이라 바꾸지 않습니다")
        if any(ch in text for ch in "\t\r\n"):
            raise HwpError("문단 하나에는 한 줄만 넣습니다")
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

    def save(self, out):
        streams = {}
        for name in self.sections:
            raw = self.section_bytes(name)
            if any(s.section == name for s in (self.slots[k - 1] for k in self.changes)):
                if self.compressed:
                    c = zlib.compressobj(9, zlib.DEFLATED, -15)
                    payload = c.compress(raw) + c.flush()
                    if len(payload) < self.cf.cutoff:   # 작으면 미니 스트림에 들어가야 해서, 압축하지 않은 deflate 로
                        c = zlib.compressobj(0, zlib.DEFLATED, -15)
                        payload = c.compress(raw) + c.flush()
                    streams[name] = payload
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
        with open(out, "wb") as f:
            f.write(data.result())


# ---------------- 복합 문서 고치기 (바꾼 스트림만) ----------------
class CfbPatch:
    def __init__(self, data, cf):
        self.buf = bytearray(data)
        self.cf = cf
        self.ss = cf.ssize
        if (len(self.buf) - self.ss) % self.ss:
            self.buf += b"\0" * (self.ss - (len(self.buf) - self.ss) % self.ss)
        self.fat = list(cf.fat)
        self.num_fat, self.first_dir = struct.unpack_from("<II", self.buf, 0x2C)
        self.difat = list(struct.unpack_from("<109I", self.buf, 0x4C))
        if struct.unpack_from("<I", self.buf, 0x48)[0]:
            raise HwpError("아주 큰 문서(DIFAT 섹터)는 아직 지원하지 않습니다")

    def _off(self, sect):
        return (sect + 1) * self.ss

    def _chain(self, start, table):
        out, s = [], start
        while s not in (ENDOFCHAIN, FREESECT):
            out.append(s)
            s = table[s]
        return out

    def _dir_offset(self, idx):
        per = self.ss // 128
        sects = self._chain(self.first_dir, self.fat)
        return self._off(sects[idx // per]) + (idx % per) * 128

    def _mini_offset(self, msect):
        root = self.cf.entries[0]
        chain = self._chain(root["start"], self.fat)
        pos = msect * 64
        return self._off(chain[pos // self.ss]) + pos % self.ss

    def _wipe_old(self, e):
        if e["size"] < self.cf.cutoff:
            for m in self._chain(e["start"], self.cf.minifat):
                o = self._mini_offset(m)
                self.buf[o:o + 64] = b"\0" * 64
            self._free_minifat(e["start"])
        else:
            for s in self._chain(e["start"], self.fat):
                o = self._off(s)
                self.buf[o:o + self.ss] = b"\0" * self.ss
                self.fat[s] = FREESECT

    def _free_minifat(self, start):
        mf = list(self.cf.minifat)
        for m in self._chain(start, mf):
            mf[m] = FREESECT
        first_mf, = struct.unpack_from("<I", self.buf, 0x3C)
        raw = struct.pack(f"<{len(mf)}I", *mf)
        for k, s in enumerate(self._chain(first_mf, self.fat)):
            o = self._off(s)
            part = raw[k * self.ss:(k + 1) * self.ss]
            self.buf[o:o + len(part)] = part

    def replace_stream(self, path, payload):
        if len(payload) < self.cf.cutoff:
            raise HwpError("본문이 너무 짧아 이 시험 방식으로는 쓸 수 없습니다 (미니 스트림)")
        idx = self.cf.paths[path]
        e = self.cf.entries[idx]
        self._wipe_old(e)
        n = -(-len(payload) // self.ss)
        first = (len(self.buf) - self.ss) // self.ss
        self.buf += payload.ljust(n * self.ss, b"\0")
        need = first + n
        while len(self.fat) < need + 1:            # FAT 자리가 모자라면 FAT 섹터를 하나 더
            fs = (len(self.buf) - self.ss) // self.ss
            self.buf += b"\0" * self.ss
            self.fat.extend([FREESECT] * (self.ss // 4))
            slot = self.difat.index(FREESECT)
            self.difat[slot] = fs
            self.fat[fs] = 0xFFFFFFFD
            self.num_fat += 1
            need = max(need, fs + 1)
        for k in range(n):
            self.fat[first + k] = first + k + 1 if k < n - 1 else ENDOFCHAIN
        o = self._dir_offset(idx)
        struct.pack_into("<IQ", self.buf, o + 116, first, len(payload))

    def overwrite_same_size(self, path, payload):
        e = self.cf.entries[self.cf.paths[path]]
        assert len(payload) == e["size"]
        if e["size"] < self.cf.cutoff:
            chain = self._chain(e["start"], self.cf.minifat)
            for k, m in enumerate(chain):
                o = self._mini_offset(m)
                part = payload[k * 64:(k + 1) * 64]
                self.buf[o:o + len(part)] = part
        else:
            for k, s in enumerate(self._chain(e["start"], self.fat)):
                o = self._off(s)
                part = payload[k * self.ss:(k + 1) * self.ss]
                self.buf[o:o + len(part)] = part

    def result(self):
        struct.pack_into("<I", self.buf, 0x2C, self.num_fat)
        struct.pack_into("<109I", self.buf, 0x4C, *self.difat)
        raw = struct.pack(f"<{len(self.fat)}I", *self.fat)
        fat_sects = [s for s in self.difat if s != FREESECT][:self.num_fat]
        for k, s in enumerate(fat_sects):
            o = self._off(s)
            self.buf[o:o + self.ss] = raw[k * self.ss:(k + 1) * self.ss]
        return bytes(self.buf)
