"""윈도우 .bat 파일 검사 (리눅스에서도 돌아가는 정적 검사)."""

import re
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
BATS = sorted(DESK.glob("*.bat"))


class BatTests(unittest.TestCase):
    def test_found(self):
        self.assertEqual({p.name for p in BATS}, {"start-desk.bat", "make-desktop-shortcut.bat", "update-desk.bat"})

    def test_encoding_and_line_endings(self):
        for p in BATS:
            raw = p.read_bytes()
            raw.decode("cp949")  # 한국어 윈도우 명령 창용
            self.assertNotIn(b"\n", raw.replace(b"\r\n", b""), f"{p.name}: CRLF 가 아닌 줄바꿈")
            self.assertLess(max(len(l) for l in raw.split(b"\r\n")), 8000, f"{p.name}: 명령 창 한 줄 한도")

    def test_no_bare_parens_inside_blocks(self):
        """if ( ... ) 묶음 안의 echo 에 괄호가 있으면 명령 창이 묶음이 끝난 것으로 읽음."""
        for p in BATS:
            depth = 0
            for n, line in enumerate(p.read_bytes().decode("cp949").split("\r\n"), 1):
                s = line.strip()
                if depth and re.match(r"(?i)echo\b", s):
                    text = re.sub(r"\^[()]", "", s)
                    self.assertNotRegex(text, r"[()]", f"{p.name}:{n}: 묶음 안 echo 에 괄호 → ^( ^) 로 바꾸세요")
                if s.startswith(")"):
                    depth -= 1
                if s.endswith("("):
                    depth += 1
            self.assertEqual(depth, 0, f"{p.name}: 여는 괄호와 닫는 괄호 수가 다름")


if __name__ == "__main__":
    unittest.main()
