"""화면 크기·브리핑 줄 배치 규칙이 빠지지 않았는지 (0.5.67). 실제 모양은 브라우저 캡처로 확인."""
import re
import unittest
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "web" / "css" / "desk.css").read_text(encoding="utf-8")


class UiCssTests(unittest.TestCase):
    def test_base_font_steps_by_width(self):
        self.assertIn("--base-fs: 15px;", CSS)
        self.assertRegex(CSS, r"@media \(min-width: 1700px\) \{ :root \{ --base-fs: 16px; \} \}")
        self.assertRegex(CSS, r"@media \(min-width: 2300px\) \{ :root \{ --base-fs: 18px; \} \}")
        self.assertIn("calc(var(--base-fs) * var(--font-scale))", CSS)   # 사용자 글자 크기 설정이 곱해짐

    def test_no_fixed_px_for_chrome_text(self):
        # 활동 막대·상태 표시줄 글자가 확대에 따라오도록 px 고정이 아님
        for sel in (".act {", "#statusbar {"):
            block = CSS[CSS.index(sel):].split("}", 1)[0]
            self.assertNotRegex(block, r"font-size:\s*\d+px")

    def test_brief_event_title_gets_full_row(self):
        self.assertRegex(CSS, r"\.cards \.ev \{[^}]*flex-wrap: wrap")
        self.assertRegex(CSS, r"\.cards \.ev > a[^{]*\{[^}]*flex: 1 1 100%")
        self.assertRegex(CSS, r"\.cards \.ev\.movable > \.to-todo \{ position: absolute")


if __name__ == "__main__":
    unittest.main()
