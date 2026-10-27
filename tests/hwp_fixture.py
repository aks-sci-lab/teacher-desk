"""테스트용 복합 문서·한글 파일 만들기 (실제 학교 문서를 저장소에 넣지 않기 위해)."""

import struct
import zlib

SECT = 512
FREE, END, FATSECT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD


def _ceil(a, b):
    return -(-a // b)


def build_cfb(streams):
    """streams: {'FileHeader': bytes, 'BodyText/Section0': bytes} → 복합 문서 bytes (버전 3)."""
    # 디렉터리 항목: 루트, 저장소(폴더), 스트림
    entries = [{"name": "Root Entry", "type": 5, "children": []}]
    index = {"": 0}
    for path in streams:
        parts = path.split("/")
        for depth in range(len(parts)):
            key = "/".join(parts[:depth + 1])
            if key in index:
                continue
            parent = index["/".join(parts[:depth])]
            is_stream = depth == len(parts) - 1
            entries.append({"name": parts[depth], "type": 2 if is_stream else 1, "children": [],
                            "data": streams[path] if is_stream else None})
            index[key] = len(entries) - 1
            entries[parent]["children"].append(index[key])

    mini = [e for e in entries if e["type"] == 2 and len(e["data"]) < 4096]
    big = [e for e in entries if e["type"] == 2 and len(e["data"]) >= 4096]
    mini_counts = [_ceil(len(e["data"]), 64) or 1 for e in mini]
    n_mini = sum(mini_counts)
    dir_sects = _ceil(len(entries) * 128, SECT)
    minifat_sects = _ceil(n_mini * 4, SECT)
    ministream_sects = _ceil(n_mini * 64, SECT)
    big_counts = [_ceil(len(e["data"]), SECT) for e in big]
    others = dir_sects + minifat_sects + ministream_sects + sum(big_counts)
    fat_sects = 1
    while fat_sects * (SECT // 4) < others + fat_sects:
        fat_sects += 1
    total = fat_sects + others
    fat = [FREE] * (fat_sects * SECT // 4)

    def chain(start, count):
        for i in range(count):
            fat[start + i] = start + i + 1 if i < count - 1 else END
        return start if count else END

    for i in range(fat_sects):
        fat[i] = FATSECT
    pos = fat_sects
    first_dir = chain(pos, dir_sects); pos += dir_sects
    first_minifat = chain(pos, minifat_sects); pos += minifat_sects
    ministream_start = chain(pos, ministream_sects); pos += ministream_sects
    for e, c in zip(big, big_counts):
        e["start"] = chain(pos, c)
        pos += c

    minifat, ministream, mpos = [], b"", 0
    for e, c in zip(mini, mini_counts):
        e["start"] = mpos
        for i in range(c):
            minifat.append(mpos + i + 1 if i < c - 1 else END)
        ministream += e["data"].ljust(c * 64, b"\0")
        mpos += c
    entries[0]["start"] = ministream_start if n_mini else END
    entries[0]["data"] = ministream

    def link(parent):
        kids = parent["children"]
        parent["child"] = kids[0] if kids else FREE
        for a, b in zip(kids, kids[1:] + [None]):
            entries[a]["right"] = b if b is not None else FREE
            link(entries[a])

    link(entries[0])
    dir_bytes = b""
    for e in entries:
        name = (e["name"] + "\0").encode("utf-16-le")
        rec = name.ljust(64, b"\0") + struct.pack("<HBB", len(name), e["type"], 1)
        rec += struct.pack("<III", FREE, e.get("right", FREE), e.get("child", FREE))
        rec += b"\0" * 16 + b"\0" * 4 + b"\0" * 16
        size = len(e["data"]) if e.get("data") is not None else 0
        rec += struct.pack("<IQ", e.get("start", END) if e["type"] != 1 else 0, size)
        dir_bytes += rec
    dir_bytes = dir_bytes.ljust(dir_sects * SECT, b"\0")

    header = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" + b"\0" * 16
    header += struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + b"\0" * 6
    header += struct.pack("<IIII", 0, fat_sects, first_dir, 0)
    header += struct.pack("<IIIII", 4096, first_minifat if n_mini else END, minifat_sects, END, 0)
    difat = list(range(fat_sects)) + [FREE] * (109 - fat_sects)
    header += struct.pack("<109I", *difat)
    assert len(header) == SECT

    body = struct.pack(f"<{len(fat)}I", *fat)
    body += dir_bytes
    body += struct.pack(f"<{len(minifat)}I", *minifat).ljust(minifat_sects * SECT, b"\xff")
    body += ministream.ljust(ministream_sects * SECT, b"\0")
    for e, c in zip(big, big_counts):
        body += e["data"].ljust(c * SECT, b"\0")
    assert len(body) == total * SECT
    return header + body


def record(tag, payload):
    size = len(payload)
    if size < 0xFFF:
        return struct.pack("<I", tag | (size << 20)) + payload
    return struct.pack("<II", tag | (0xFFF << 20), size) + payload


def para(text, table_control=False):
    """문단 하나: PARA_HEADER + PARA_TEXT (표 제어 문자 8글자를 앞에 넣을 수 있음)."""
    units = b""
    if table_control:
        units += struct.pack("<H", 11) + b"\0" * 12 + struct.pack("<H", 11)
    units += text.encode("utf-16-le") + struct.pack("<H", 13)
    return record(66, b"\0" * 22) + record(67, units)


def build_hwp(paragraphs, compressed=True, distribution=False, preview=""):
    section = b"".join(para(p, table_control=(i == 1)) for i, p in enumerate(paragraphs))
    if compressed:
        c = zlib.compressobj(9, zlib.DEFLATED, -15)
        section = c.compress(section) + c.flush()
    props = (1 if compressed else 0) | (4 if distribution else 0)
    header = b"HWP Document File".ljust(32, b"\0") + struct.pack("<II", 0x05000300, props)
    streams = {"FileHeader": header.ljust(256, b"\0")}
    if preview:
        streams["PrvText"] = preview.encode("utf-16-le")
    if not distribution:
        streams["BodyText/Section0"] = section
    return build_cfb(streams)
