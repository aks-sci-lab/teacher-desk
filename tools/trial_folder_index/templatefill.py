"""시험용: 선생님의 기존 양식(.hwpx / .docx)을 틀로 두고 글자만 채우기 (표준 라이브러리만).

0.2 (선생님 지적 2026-11-16 "형식은 같은데 색과 모양이 달라"): 예전에는 본문 XML 을 통째로 읽었다가 다시 써서
XML 머리줄(standalone)·쓰지 않는 이름공간 선언·빈 태그 모양이 원본과 달라졌음. 이제는 원본 바이트를 그대로 두고
**채우는 칸의 문단 부분만** 잘라 바꾼다. 그래서 채운 칸 밖은 한 바이트도 바뀌지 않는다.

채우는 자리는 두 가지:
  1) 표의 이름 칸 옆 빈칸: '일 시' | (빈칸)  → 빈칸에 내용 (빈칸 첫 문단의 글자 모양·문단 모양을 그대로 씀)
  2) 자리표시 글자: {{작성자}} → 내용 (그 문단의 첫 글자 모양으로)
"""

import re
import zipfile
import xml.parsers.expat
from xml.sax.saxutils import escape

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


class Form:
    def __init__(self, path):
        self.path = str(path)
        self.ext = "." + self.path.rsplit(".", 1)[-1].lower()
        if self.ext not in KINDS:
            raise ValueError("hwpx 나 docx 양식만 됩니다")
        self.k = KINDS[self.ext]
        self.zip = zipfile.ZipFile(self.path)
        self.parts = {}   # 이름 → [원본 바이트, 뿌리, 바꿀 곳 목록]
        for info in self.zip.infolist():
            if self.k["parts"].search(info.filename):
                data = self.zip.read(info.filename)
                self.parts[info.filename] = [data, parse(data), []]

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

    def save(self, out):
        """바꿀 곳만 잘라 바꾸고, 나머지 파일은 원래 순서·압축 방식·바이트 그대로."""
        with zipfile.ZipFile(out, "w") as z:
            for info in self.zip.infolist():
                data = self.zip.read(info.filename)
                if info.filename in self.parts and self.parts[info.filename][2]:
                    buf, pos = bytearray(), 0
                    for s, e, new in sorted(self.parts[info.filename][2]):
                        buf += data[pos:s] + new
                        pos = e
                    data = bytes(buf + data[pos:])
                z.writestr(info, data, compress_type=info.compress_type)


FILL_INSTRUCTION = """[양식 채우기] 오늘은 {today}이다. 교사가 할 작업: "{task}"
학교 양식에 아래 칸이 있다. 참고 자료에 있는 사실만으로 각 칸에 들어갈 내용을 이 모양으로만 써 줘 (앞뒤 인사말 없이):
칸 이름: 내용
여러 줄이 필요한 칸은 다음 줄부터 '- ' 로 시작하는 줄을 이어 쓴다. 자료에 없으면 '(확인 필요)'.

칸: {fields}

참고 자료:
{materials}
"""


def parse_fill(text, fields):
    """AI 답 '칸: 내용' (+ '- ' 이어지는 줄) → {칸: 내용}. 양식에 없는 칸 이름은 버림."""
    keys = {label_key(f): f for f in fields}
    out, cur = {}, None
    for line in (text or "").splitlines():
        m = re.match(r"^\s*([^:：\-]{1,20})\s*[:：]\s*(.*)$", line)
        if m and label_key(m.group(1)) in keys:
            cur = keys[label_key(m.group(1))]
            out[cur] = m.group(2).strip()
        elif cur and line.strip().startswith("-"):
            out[cur] = (out[cur] + "\n" if out[cur] else "") + line.strip()
    return out
