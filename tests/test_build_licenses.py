"""윈도우용 ZIP 에 넣는 꾸러미 라이선스 확인 (0.5.96)."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_portable", ROOT / "tools" / "build_portable.py")
bp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bp)


def dist(lib, name, lic="MIT", license_file=True):
    d = lib / f"{name}-1.0.dist-info"
    d.mkdir()
    (d / "METADATA").write_text(f"Name: {name}\nLicense-Expression: {lic}\n", encoding="utf-8")
    if license_file:
        (d / "LICENSE").write_text("license", encoding="utf-8")


class LicenseTests(unittest.TestCase):
    def test_known_packages_pass_unknown_or_missing_stop(self):
        self.assertIn("cryptography==", bp.CRYPTOGRAPHY)    # 버전 고정
        with tempfile.TemporaryDirectory() as t:
            lib = Path(t)
            for n in ("pypdf", "cryptography", "cffi", "pycparser"):
                dist(lib, n)
            self.assertEqual(set(bp.check_licenses(lib)), {"pypdf", "cryptography", "cffi", "pycparser"})
            dist(lib, "evil_pkg")
            with self.assertRaises(SystemExit):
                bp.check_licenses(lib)
        with tempfile.TemporaryDirectory() as t:
            lib = Path(t)
            dist(lib, "pypdf")
            dist(lib, "cryptography", license_file=False)
            dist(lib, "cffi")
            with self.assertRaises(SystemExit):
                bp.check_licenses(lib)
        with tempfile.TemporaryDirectory() as t:
            lib = Path(t)
            dist(lib, "pypdf")
            with self.assertRaises(SystemExit):     # cryptography 가 빠지면 멈춤
                bp.check_licenses(lib)


if __name__ == "__main__":
    unittest.main()
