"""Windows 기본 프린터로 조퇴증·외출증을 조용히 출력합니다."""

import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path


class PrintError(RuntimeError):
    pass


_lock = threading.Lock()
_jobs = {}

QUEUE_KEY, AT_KEY = "telegram_print_queue", "telegram_print_at"
QUEUE_TTL = 600   # 인쇄 요청이 유효한 시간(초). 실패한 요청이 다음 날 다시 나오지 않게


def queue(db, now=None):
    """인쇄 대기 중인 발급 번호. 시각이 없거나 10분이 지난 요청은 버립니다."""
    now = time.time() if now is None else now
    ids, at = db.get_meta(QUEUE_KEY, []), db.get_meta(AT_KEY, {})
    keep = [i for i in ids if now - at.get(str(i), 0) <= QUEUE_TTL]
    if keep != ids:
        db.set_meta(QUEUE_KEY, keep)
        db.set_meta(AT_KEY, {str(i): at[str(i)] for i in keep})
    return keep


def enqueue(db, pid, now=None):
    with db.lock:
        keep = [i for i in queue(db, now) if i != int(pid)] + [int(pid)]
        at = db.get_meta(AT_KEY, {})
        at[str(int(pid))] = time.time() if now is None else now
        db.set_meta(QUEUE_KEY, keep[-20:])
        db.set_meta(AT_KEY, {str(i): at[str(i)] for i in keep[-20:]})


def dequeue(db, pid):
    with db.lock:
        keep = [i for i in db.get_meta(QUEUE_KEY, []) if i != int(pid)]
        at = db.get_meta(AT_KEY, {})
        db.set_meta(QUEUE_KEY, keep)
        db.set_meta(AT_KEY, {str(i): at[str(i)] for i in keep if str(i) in at})


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
