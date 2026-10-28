import errno
import os
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from desk import collect
from desk.collect import Collector
from tests import test_server  # ServerTests 를 이름으로 가져오면 그 시험이 여기서 또 돔
from tests.test_collect import Env


class ReadFailTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.env.config.values["coolmessenger_folder"] = ""
        for i in range(5):
            (self.env.edu / f"공문{i}.hwp").write_bytes(b"x")
        (self.env.edu / "안내.txt").write_text("회의 11월 5일", encoding="utf-8")

    def tearDown(self):
        self.env.close()

    def run_with_blocked(self, reason="watch"):
        real = collect.read_document

        def fake(path):
            if path.suffix == ".hwp":   # 윈도우가 열기를 거절하는 파일 (Invalid argument)
                raise OSError(errno.EINVAL, "Invalid argument")
            return real(path)
        with patch.object(collect, "read_document", side_effect=fake) as m:
            stats = Collector(self.env.db, self.env.config).run(reason)
        return stats, m.call_count

    def test_grouped_message_and_no_endless_retry(self):
        stats, calls = self.run_with_blocked()
        self.assertEqual((stats["items"], calls), (1, 6))
        self.assertEqual(len(stats["errors"]), 1)                       # 5줄 대신 1줄
        self.assertIn("파일 5개를 읽지 못했습니다", stats["errors"][0])
        self.assertIn("문서보안", stats["errors"][0])
        stats, calls = self.run_with_blocked("watch")                     # 실시간 감시·예약은 다시 시도하지 않음
        self.assertEqual((calls, stats["errors"]), (0, []))
        stats, calls = self.run_with_blocked("manual")                    # '지금 수집'은 실패한 것만 다시
        self.assertEqual(calls, 5)
        (self.env.edu / "공문0.hwp").write_bytes(b"xy")                  # 파일이 바뀌면 다시
        stats, calls = self.run_with_blocked("watch")
        self.assertEqual(calls, 1)
        self.assertIn("공문0.hwp: 윈도우가 파일 열기를 막았습니다", stats["errors"][0])

    def test_reasons(self):
        p = self.env.edu / "공문0.hwp"
        with patch.object(collect.os, "stat", return_value=SimpleNamespace(st_file_attributes=0x400000)):
            self.assertIn("클라우드", collect.read_problem(p, OSError(errno.EINVAL, "Invalid argument")))
        self.assertIn("문서보안", collect.read_problem(p, OSError(errno.EACCES, "Permission denied")))
        self.assertIn("읽기 실패", collect.read_problem(p, OSError(errno.EIO, "I/O error")))
        if os.name != "nt":                                             # 긴 경로 처리는 윈도우에서만
            self.assertEqual(collect.long_path(p), p)

    def test_folder_check_counts_cloud_only(self):
        real_stat = Path.stat

        def fake_stat(self, *a, **k):
            st = real_stat(self, *a, **k)
            attrs = 0x400000 if self.name.startswith("공문") else 0   # 공문*.hwp 는 클라우드에만
            return SimpleNamespace(st_size=st.st_size, st_mtime=st.st_mtime, st_mode=st.st_mode, st_file_attributes=attrs)
        with patch.object(Path, "stat", fake_stat):
            r = collect.diagnose(self.env.edu)
        self.assertEqual((r["total"], r["supported"], r["cloud_only"]), (6, 6, 5))
        self.assertIn("Synology Drive", r["cloud_hint"])
        self.assertEqual(sum(f["kind"] == "클라우드에만 있음" for f in r["recent_files"]), 5)


class FolderCheckApiTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerTests.tearDownClass.__func__)
    req = test_server.ServerTests.req

    def test_checks_typed_path_before_saving(self):
        other = Path(self.env.tmp.name) / "새 폴더"
        other.mkdir()
        (other / "안내.hwp").write_bytes(b"x")
        st, r = self.req("GET", f"/api/diagnose?source=edufine&folder={quote(str(other))}")
        self.assertEqual((st, r["total"], r["saved"]), (200, 1, False))      # 아직 저장 안 한 경로
        st, r = self.req("GET", "/api/diagnose?source=edufine")
        self.assertEqual((st, r["saved"]), (200, True))                     # 저장된 설정
        self.env.config.values["coolmessenger_folder"] = ""
        self.assertEqual(self.req("GET", "/api/diagnose?source=cool")[0], 400)


if __name__ == "__main__":
    unittest.main()
