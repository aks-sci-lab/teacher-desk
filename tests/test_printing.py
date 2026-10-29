import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from desk import printing


class SilentPrintingTests(unittest.TestCase):
    def tearDown(self):
        printing._jobs.clear()

    def test_silent_print_requires_windows(self):
        with patch.object(printing, "_is_windows", return_value=False):
            with self.assertRaisesRegex(printing.PrintError, "Windows"):
                printing.start_silent_pass({"port": 8770}, 1)

    def test_starts_one_kiosk_job_per_pass(self):
        process = Mock()
        process.poll.return_value = None
        worker = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "profile"
            profile.mkdir()
            with (
                patch.object(printing, "_is_windows", return_value=True),
                patch.object(printing, "_browser_path", return_value=Path("edge.exe")),
                patch.object(printing.tempfile, "mkdtemp", return_value=str(profile)),
                patch.object(printing.subprocess, "Popen", return_value=process) as popen,
                patch.object(printing.threading, "Thread", return_value=worker) as thread,
            ):
                self.assertTrue(printing.start_silent_pass({"port": 8770}, 12))
                self.assertFalse(printing.start_silent_pass({"port": 8770}, 12))

            args = popen.call_args.args[0]
            self.assertIn("--kiosk-printing", args)
            self.assertIn(f"--user-data-dir={profile}", args)
            self.assertIn("--app=http://127.0.0.1:8770/?silent-print=12", args)
            popen.assert_called_once()
            thread.assert_called_once()
            worker.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
