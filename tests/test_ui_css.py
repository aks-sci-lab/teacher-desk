"""화면 크기·브리핑 줄 배치 규칙이 빠지지 않았는지 (0.5.67). 실제 모양은 브라우저 캡처로 확인."""
import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
CSS = (WEB / "css" / "desk.css").read_text(encoding="utf-8")


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
        self.assertRegex(CSS, r"\.cards \.ev, \.brief-grid \.ev \{[^}]*flex-wrap: wrap")
        self.assertRegex(CSS, r"\.cards \.ev > a[^{]*\.brief-grid \.ev > span:last-child \{[^}]*flex: 1 1 100%")
        self.assertRegex(CSS, r"\.brief-grid \.ev\.movable > \.to-todo \{ position: absolute")

    def test_activity_bar_scrolls_instead_of_overflowing(self):   # 0.5.68
        self.assertRegex(CSS, r"#activity-top \{[^}]*overflow-y: auto")
        self.assertRegex(CSS, r"#activity-bottom \{ flex: none; \}")

    def test_modal_confirms_only_after_user_input(self):          # 0.5.68
        js = (WEB / "js" / "ui.js").read_text(encoding="utf-8")
        self.assertIn("e.isTrusted", js)                           # 프로그램이 바꾼 값은 입력으로 안 침
        self.assertIn("requestClose", js)
        self.assertNotIn("localStorage", js)                       # 입력 내용을 브라우저에 저장하지 않음
        self.assertNotIn("sessionStorage", js)

    def test_status_chip_warns_only_on_real_errors(self):          # 0.5.68
        js = (WEB / "js" / "views" / "today.js").read_text(encoding="utf-8")
        body = js[js.index("function statusChip"):js.index("function renderToday")]
        self.assertNotIn("!st?.telegram.linked ||", body)          # 안 쓰는 것은 경고 아님
        self.assertIn("tgError", body)
        self.assertIn("사용 안 함", body)

    def test_brief_uses_two_columns_and_compact_todo_rows(self):   # 0.5.70
        self.assertRegex(CSS, r"\.brief-grid:not\(\.one-col\) \{ grid-template-columns: minmax\(0, 3fr\) minmax\(0, 2fr\)")
        self.assertRegex(CSS, r"\.todo-list li\.todo \{ display: grid;")          # 체크칸·날짜·제목 줄 맞춤
        self.assertRegex(CSS, r"\.todo-list li\.todo > \.ai-txt \{[^}]*-webkit-line-clamp: 2")
        self.assertRegex(CSS, r"\.brief-right > \.new-card:not\(\.empty-card\) \{ flex: 1 1 0")  # 새 자료는 카드 안 스크롤
        js = (WEB / "js" / "views" / "today.js").read_text(encoding="utf-8")
        self.assertIn("div.brief-left", js)
        self.assertIn("div.brief-right", js)
        self.assertIn("function emptyStrip", js)                                       # 빈 카드는 작은 한 줄로


if __name__ == "__main__":
    unittest.main()
