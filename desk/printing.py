"""Windows 기본 프린터로 조퇴증·외출증을 조용히 출력합니다."""

import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path


class PrintError(RuntimeError):
    pass


_lock = threading.Lock()
_jobs = {}


def _is_windows():
    return os.name == "nt"


def _browser_path():
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    return next((path for path in candidates if path.is_file()), None)


def _reap(pid, process, profile):
    try:
        try:
            process.wait(timeout=300)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
        with _lock:
            if _jobs.get(pid) is process:
                del _jobs[pid]


def start_silent_pass(config, pass_id):
    """별도 브라우저 프로필에서 기본 프린터로 2장 출력하도록 시작합니다."""
    if not _is_windows():
        raise PrintError("자동 인쇄는 Windows에서만 사용할 수 있습니다.")
    browser = _browser_path()
    if browser is None:
        raise PrintError("자동 인쇄용 Microsoft Edge 또는 Google Chrome을 찾지 못했습니다.")

    with _lock:
        active = _jobs.get(int(pass_id))
        if active and active.poll() is None:
            return False

        profile = Path(tempfile.mkdtemp(prefix="teacher-desk-print-"))
        url = f"http://127.0.0.1:{config['port']}/?silent-print={int(pass_id)}"
        args = [
            str(browser),
            "--kiosk-printing",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile}",
            f"--app={url}",
        ]
        try:
            process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError as e:
            shutil.rmtree(profile, ignore_errors=True)
            raise PrintError(f"자동 인쇄창을 시작하지 못했습니다: {e}") from e
        _jobs[int(pass_id)] = process

    threading.Thread(target=_reap, args=(int(pass_id), process, profile), daemon=True).start()
    return True
