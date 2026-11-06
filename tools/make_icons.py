"""앱 아이콘 만들기 (표준 라이브러리만): web/icons/desk.ico (16·32·48·256) 와 favicon-32.png.

모양은 web/icons/favicon.svg 와 같습니다 (학교 건물). 고치면 두 곳을 같이 맞추고
`python tools/make_icons.py` 를 다시 실행하세요.
"""
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "web" / "icons"
BLUE, WHITE = (37, 99, 235), (255, 255, 255)


def in_tri(x, y, a, b, c):
    def s(p, q, r):
        return (p[0] - r[0]) * (q[1] - r[1]) - (q[0] - r[0]) * (p[1] - r[1])
    p = (x, y)
    d = (s(p, a, b), s(p, b, c), s(p, c, a))
    return not (any(v < 0 for v in d) and any(v > 0 for v in d))


def rect(x, y, x0, y0, w, h):
    return x0 <= x < x0 + w and y0 <= y < y0 + h


def shade(x, y):
    """64x64 도면의 한 점 색: None=투명."""
    r = 14
    cx, cy = min(max(x, r), 64 - r), min(max(y, r), 64 - r)
    if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
        return None
    if (x - 32) ** 2 + (y - 20) ** 2 <= 3.5 ** 2:
        return BLUE
    if rect(x, y, 28, 38, 8, 12) or rect(x, y, 18, 32, 6, 6) or rect(x, y, 40, 32, 6, 6):
        return BLUE
    if in_tri(x, y, (32, 9), (9, 26), (55, 26)) or rect(x, y, 14, 28, 36, 22) or rect(x, y, 9, 50, 46, 5):
        return WHITE
    return BLUE


def render(size, ss=4):
    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    c = shade((px + (sx + .5) / ss) * 64 / size, (py + (sy + .5) / ss) * 64 / size)
                    if c:
                        acc[0] += c[0]; acc[1] += c[1]; acc[2] += c[2]; acc[3] += 1
            n = ss * ss
            a = acc[3]
            row += bytes([acc[i] // a for i in range(3)] + [255 * a // n]) if a else b"\0\0\0\0"
        rows.append(bytes(row))
    return rows


def png(size):
    raw = b"".join(b"\0" + r for r in render(size))
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    sizes = [16, 32, 48, 256]
    pngs = [png(s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    off = 6 + 16 * len(sizes)
    ents = b""
    for s, p in zip(sizes, pngs):
        ents += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(p), off)
        off += len(p)
    (OUT / "desk.ico").write_bytes(head + ents + b"".join(pngs))
    (OUT / "favicon-32.png").write_bytes(pngs[1])
    print("만듦:", OUT)


if __name__ == "__main__":
    main()
