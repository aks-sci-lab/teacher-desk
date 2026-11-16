"""시험용: 선생님의 기존 양식(.hwpx / .docx)을 틀로 두고 글자만 채우기 (표준 라이브러리만).

양식의 글꼴·문단 모양·표·머리말(header.xml, styles.xml 등)은 바이트 그대로 두고, 본문 XML 안의 글자만 바꾼다.
채우는 자리는 두 가지:
  1) 표의 이름 칸 옆 빈칸: '일 시' | (빈칸)  → 빈칸에 내용
  2) 자리표시 글자: {{작성자}} → 내용
"""

import copy
import io
import re
import zipfile
import xml.etree.ElementTree as ET

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
KINDS = {
    ".hwpx": {"parts": re.compile(r"Contents/section\d+\.xml$"), "ns": HP,
              "tbl": "tbl", "tr": "tr", "tc": "tc", "p": "p", "run": "run", "t": "t"},
    ".docx": {"parts": re.compile(r"word/(document|header\d*|footer\d*)\.xml$"), "ns": W,
              "tbl": "tbl", "tr": "tr", "tc": "tc", "p": "p", "run": "r", "t": "t"},
}
PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
MAX_LABEL = 12   # 이름 칸으로 볼 최대 글자 수


def label_key(text):
    return re.sub(r"[\s:：·()（）\[\]]", "", text or "")


def _register(data):
    """원래 접두어(hp:, w: …)를 그대로 쓰게 등록 (ET 가 ns0: 로 바꾸지 않게)."""
    for _, (prefix, uri) in ET.iterparse(io.BytesIO(data), events=("start-ns",)):
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:
            pass


class Form:
    def __init__(self, path):
        self.path = str(path)
        self.ext = "." + self.path.rsplit(".", 1)[-1].lower()
        if self.ext not in KINDS:
            raise ValueError("hwpx 나 docx 양식만 됩니다")
        self.k = KINDS[self.ext]
        self.zip = zipfile.ZipFile(self.path)
        self.trees = {}
        for info in self.zip.infolist():
            if self.k["parts"].search(info.filename):
                data = self.zip.read(info.filename)
                _register(data)
                self.trees[info.filename] = ET.ElementTree(ET.fromstring(data))

    def q(self, name):
        return f"{{{self.k['ns']}}}{self.k[name]}"

    # ---- 글자 읽기 ----
    def _texts(self, el):
        return [t for t in el.iter(self.q("t"))]

    def cell_text(self, tc):
        return "\n".join("".join(t.text or "" for t in self._texts(p)) for p in self._cell_paras(tc)).strip()

    def _cell_paras(self, tc):
        # 표 안의 표는 따로 다루므로, 이 칸에 바로 속한 문단만 (hwpx 는 subList 아래)
        out = []
        for child in tc.iter(self.q("p")):
            out.append(child)
        nested = {id(p) for t in tc.iter(self.q("tbl")) for p in t.iter(self.q("p"))}
        return [p for p in out if id(p) not in nested]

    def rows(self):
        for tree in self.trees.values():
            for tbl in tree.getroot().iter(self.q("tbl")):
                for tr in tbl.findall(self.q("tr")):
                    yield tr.findall(self.q("tc"))

    def fields(self):
        """채울 자리 목록: 이름 칸(옆 칸이 비어 있음)과 {{자리표시}}."""
        out, seen = [], set()
        for cells in self.rows():
            for a, b in zip(cells, cells[1:]):
                name = self.cell_text(a)
                if 0 < len(label_key(name)) <= MAX_LABEL and not self.cell_text(b) and label_key(name) not in seen:
                    seen.add(label_key(name))
                    out.append(name.strip())
        for tree in self.trees.values():
            for p in tree.getroot().iter(self.q("p")):
                for m in PLACEHOLDER.finditer("".join(t.text or "" for t in self._texts(p))):
                    if label_key(m.group(1)) not in seen:
                        seen.add(label_key(m.group(1)))
                        out.append(m.group(1))
        return out

    # ---- 글자 쓰기 ----
    def _set_para(self, p, text):
        ts = self._texts(p)
        if not ts:   # 빈 문단: 첫 글자 묶음(run) 안에, 없으면 새로
            run = p.find(self.q("run"))
            if run is None:
                run = ET.SubElement(p, self.q("run"))
                if self.ext == ".hwpx":
                    run.set("charPrIDRef", "0")
            ts = [ET.SubElement(run, self.q("t"))]
        ts[0].text = text
        if self.ext == ".docx":
            ts[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        for t in ts[1:]:
            t.text = ""
            for c in list(t):   # 글자 사이의 탭 등
                t.remove(c)
        if self.ext == ".hwpx":   # 줄 배치 기억(linesegarray)은 한글이 다시 계산하도록 뺌
            for seg in p.findall(f"{{{HP}}}linesegarray"):
                p.remove(seg)

    def _set_cell(self, tc, value):
        lines = [l for l in str(value).splitlines()] or [""]
        paras = self._cell_paras(tc)
        first = paras[0]
        parent = _parent(tc, first)
        for p in paras[1:]:
            _parent(tc, p).remove(p)
        self._set_para(first, lines[0])
        at = list(parent).index(first)
        for i, line in enumerate(lines[1:], 1):
            np = copy.deepcopy(first)   # 첫 문단 모양(글꼴·문단 번호)을 그대로 복사
            if self.ext == ".hwpx":
                np.set("id", "2147483648")
            self._set_para(np, line)
            parent.insert(at + i, np)

    def fill(self, values):
        """values: {칸 이름: 내용}. 채운 칸 이름 목록을 돌려줌."""
        want = {label_key(k): v for k, v in values.items() if v not in (None, "")}
        done = []
        for cells in self.rows():
            for a, b in zip(cells, cells[1:]):
                key = label_key(self.cell_text(a))
                if key in want and not self.cell_text(b):
                    self._set_cell(b, want[key])
                    done.append(key)
        for tree in self.trees.values():
            for p in tree.getroot().iter(self.q("p")):
                joined = "".join(t.text or "" for t in self._texts(p))
                if "{{" not in joined:
                    continue
                new = PLACEHOLDER.sub(lambda m: str(want.get(label_key(m.group(1)), m.group(0))), joined)
                if new != joined:
                    self._set_para(p, new)
                    done += [label_key(m.group(1)) for m in PLACEHOLDER.finditer(joined)
                             if label_key(m.group(1)) in want]
        return done

    def save(self, out):
        """본문 XML 만 바꿔 쓰고, 나머지 파일은 원래 순서·압축 방식·바이트 그대로."""
        with zipfile.ZipFile(out, "w") as z:
            for info in self.zip.infolist():
                if info.filename in self.trees:
                    data = ET.tostring(self.trees[info.filename].getroot(), encoding="UTF-8", xml_declaration=True)
                else:
                    data = self.zip.read(info.filename)
                z.writestr(info, data, compress_type=info.compress_type)


def _parent(root, el):
    for p in root.iter():
        for c in p:
            if c is el:
                return p
    raise ValueError("부모를 찾을 수 없음")


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
