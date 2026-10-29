import base64
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desk import preview

PDF = b"%PDF-1.4\n%%EOF\n"


class PreviewTests(unittest.TestCase):
    def test_copy_convert_and_cleanup_without_changing_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "안내 ' $문서.hwpx"
            path.write_bytes(b"original")
            work = []

            def convert(source, target):
                self.assertNotEqual(source, path)
                self.assertEqual(source.suffix, ".hwpx")
                self.assertEqual(source.read_bytes(), b"original")
                work.append(source.parent)
                source.write_bytes(b"changed copy")
                target.write_bytes(PDF)

            with patch("desk.preview.sys.platform", "win32"), patch("desk.preview._convert", side_effect=convert):
                self.assertEqual(preview.pdf_bytes(path), PDF)
            self.assertEqual(path.read_bytes(), b"original")
            self.assertFalse(work[0].exists())

    def test_invalid_missing_output_and_failure_release_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.hwp"
            path.write_bytes(b"original")
            with patch("desk.preview.sys.platform", "win32"):
                for content in (None, b"not pdf"):
                    def convert(source, target):
                        if content is not None:
                            target.write_bytes(content)
                    with patch("desk.preview._convert", side_effect=convert):
                        with self.assertRaises(preview.PreviewError):
                            preview.pdf_bytes(path)
                    self.assertFalse(preview._lock.locked())
                with patch("desk.preview._convert", side_effect=OSError("test")):
                    with self.assertRaisesRegex(preview.PreviewError, "읽거나 쓸"):
                        preview.pdf_bytes(path)
                self.assertFalse(preview._lock.locked())
                def oversized(source, target):
                    target.write_bytes(PDF)
                with patch("desk.preview._convert", side_effect=oversized), patch("desk.preview.MAX_BYTES", 8):
                    with self.assertRaisesRegex(preview.PreviewError, "50MB"):
                        preview.pdf_bytes(path)

    def test_platform_format_size_and_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.xlsx"
            path.write_bytes(b"original")
            with patch("desk.preview.sys.platform", "linux"):
                with self.assertRaisesRegex(preview.PreviewError, "Windows"):
                    preview.pdf_bytes(path)
            with patch("desk.preview.sys.platform", "win32"):
                with self.assertRaisesRegex(preview.PreviewError, "지원하지"):
                    preview.pdf_bytes(path.with_suffix(".exe"))
                with patch("desk.preview.MAX_BYTES", 1), patch("desk.preview._convert") as convert:
                    with self.assertRaisesRegex(preview.PreviewError, "50MB"):
                        preview.pdf_bytes(path)
                    convert.assert_not_called()
                preview._lock.acquire()
                try:
                    with self.assertRaisesRegex(preview.PreviewError, "변환 중"):
                        preview.pdf_bytes(path)
                finally:
                    preview._lock.release()

    def test_powershell_has_fixed_script_and_paths_only_in_environment(self):
        with patch("desk.preview.subprocess.Popen") as popen, \
                patch("desk.preview.subprocess.CREATE_NO_WINDOW", 0, create=True):
            popen.return_value.wait.return_value = 0
            preview._convert(Path("안내 '$.hwp"), Path("보기.pdf"))
            args, kwargs = popen.call_args
            self.assertNotIn("안내", " ".join(args[0]))
            self.assertEqual(kwargs["env"]["DESK_PREVIEW_SOURCE"], "안내 '$.hwp")
            self.assertEqual(base64.b64decode(args[0][-1]).decode("utf-16-le"), preview._SCRIPT)
            self.assertIn("$app.AutomationSecurity = 3", preview._SCRIPT)
            self.assertIn("$app.Workbooks.Open($env:DESK_PREVIEW_SOURCE, 0, $true)", preview._SCRIPT)
            self.assertIn("$app.Quit()", preview._SCRIPT)
            popen.return_value.wait.assert_called_once_with(timeout=90)

    def test_conversion_failure_and_timeout_are_explicit(self):
        with patch("desk.preview.subprocess.Popen") as popen, \
                patch("desk.preview.subprocess.CREATE_NO_WINDOW", 0, create=True), \
                patch("desk.preview.subprocess.run") as run:
            popen.return_value.wait.return_value = 1
            with self.assertRaisesRegex(preview.PreviewError, "설치"):
                preview._convert(Path("test.hwp"), Path("test.pdf"))
            popen.return_value.pid = 12345
            popen.return_value.wait.side_effect = [subprocess.TimeoutExpired("test", 90), 0]
            with self.assertRaisesRegex(preview.PreviewError, "90초"):
                preview._convert(Path("test.hwp"), Path("test.pdf"))
            self.assertEqual(run.call_args.args[0], ["taskkill.exe", "/PID", "12345", "/T", "/F"])
