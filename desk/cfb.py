"""MS 복합 문서(Compound File Binary) 읽기.

한글 .hwp(5.0) 파일은 이 형식 안에 FileHeader, BodyText/Section0 같은 '스트림'을 담고 있습니다.
추가 설치 없이 읽으려고 표준 라이브러리만으로 필요한 만큼 구현했습니다 (읽기 전용).
"""

import struct

SIGNATURE = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
NOSTREAM = 0xFFFFFFFF


class CFBError(ValueError):
    pass


class CompoundFile:
    def __init__(self, data):
        if data[:8] != SIGNATURE:
            raise CFBError("복합 문서 형식이 아닙니다")
        self.data = data
        (self.sector_shift, self.mini_shift) = struct.unpack_from("<HH", data, 0x1E)
        if self.sector_shift not in (9, 12) or self.mini_shift != 6:
            raise CFBError("지원하지 않는 섹터 크기")
        self.ssize = 1 << self.sector_shift
        num_fat, first_dir = struct.unpack_from("<II", data, 0x2C)
        self.cutoff, first_minifat, num_minifat, first_difat, num_difat = struct.unpack_from("<IIIII", data, 0x38)

        # FAT 섹터 목록(DIFAT): 헤더에 109개 + 이어지는 DIFAT 섹터
        difat = [s for s in struct.unpack_from("<109I", data, 0x4C)]
        sect, per = first_difat, self.ssize // 4 - 1
        for _ in range(num_difat):
            if sect in (ENDOFCHAIN, FREESECT):
                break
            vals = struct.unpack_from(f"<{per + 1}I", self._sector(sect))
            difat.extend(vals[:per])
            sect = vals[per]
        fat_sects = [s for s in difat if s not in (FREESECT, ENDOFCHAIN)][:num_fat]
        self.fat = []
        for s in fat_sects:
            self.fat.extend(struct.unpack(f"<{self.ssize // 4}I", self._sector(s)))

        dir_data = self._read_chain(first_dir, self.fat, self._sector)
        self.entries = []
        for off in range(0, len(dir_data) - 127, 128):
            e = dir_data[off:off + 128]
            name_len = struct.unpack_from("<H", e, 64)[0]
            name = e[:max(0, name_len - 2)].decode("utf-16-le", "replace")
            etype = e[66]
            left, right, child = struct.unpack_from("<III", e, 68)
            start, size = struct.unpack_from("<IQ", e, 116)
            if self.ssize == 512:
                size &= 0xFFFFFFFF
            self.entries.append({"name": name, "type": etype, "left": left, "right": right,
                                 "child": child, "start": start, "size": size})
        if not self.entries or self.entries[0]["type"] != 5:
            raise CFBError("루트 항목이 없습니다")

        root = self.entries[0]
        self.minifat = []
        if num_minifat and first_minifat not in (ENDOFCHAIN, FREESECT):
            mf = self._read_chain(first_minifat, self.fat, self._sector)
            self.minifat = list(struct.unpack(f"<{len(mf) // 4}I", mf))
        self.ministream = self._read_chain(root["start"], self.fat, self._sector)[:root["size"]] \
            if root["start"] not in (ENDOFCHAIN, FREESECT) else b""

        self.paths = {}
        self._walk(root["child"], "", set())

    def _sector(self, n):
        off = (n + 1) * self.ssize
        if off >= len(self.data):
            raise CFBError("파일이 잘렸습니다")
        return self.data[off:off + self.ssize].ljust(self.ssize, b"\0")

    def _mini_sector(self, n):
        off = n * 64
        if off >= len(self.ministream):
            raise CFBError("미니 스트림 범위를 벗어났습니다")
        return self.ministream[off:off + 64].ljust(64, b"\0")

    @staticmethod
    def _read_chain(start, table, reader):
        out, sect, seen = [], start, set()
        while sect not in (ENDOFCHAIN, FREESECT):
            if sect in seen or sect >= len(table):
                raise CFBError("섹터 연결이 잘못되었습니다")
            seen.add(sect)
            out.append(reader(sect))
            sect = table[sect]
        return b"".join(out)

    def _walk(self, idx, prefix, seen):
        # 형제는 left/right 트리, 하위 항목은 child 로 연결됨
        stack = [idx]
        while stack:
            i = stack.pop()
            if i == NOSTREAM or i >= len(self.entries) or i in seen:
                continue
            seen.add(i)
            e = self.entries[i]
            stack.extend([e["left"], e["right"]])
            path = prefix + e["name"]
            if e["type"] == 2:
                self.paths[path] = i
            elif e["type"] == 1:
                self._walk(e["child"], path + "/", seen)

    def list_streams(self):
        return sorted(self.paths)

    def exists(self, path):
        return path in self.paths

    def read(self, path):
        e = self.entries[self.paths[path]]
        if e["size"] < self.cutoff:
            data = self._read_chain(e["start"], self.minifat, self._mini_sector)
        else:
            data = self._read_chain(e["start"], self.fat, self._sector)
        return data[:e["size"]]
