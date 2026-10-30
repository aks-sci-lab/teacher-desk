"""명령 창 없이 실행하고, 이미 켜진 앱은 중복 실행하지 않습니다."""

import ctypes
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

from . import window
from .config import APP_DIR
from .updater import EXIT_UPDATING


class BackgroundError(RuntimeError):
    pass


def is_running(port):
    url = f"http://127.0.0.1:{port}/"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=1) as response:
            return (response.headers.get("Server", "").startswith("TeacherDesk")
                    and b'desk-token' in response.read(100000))
    except (urllib.error.URLError, OSError):
        return False


@contextmanager
def single_instance(port):
    if os.name != "nt":
        yield True
        return
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateMutexW(None, False, f"Local\\TeacherDesk-{port}")
    if not handle:
        raise BackgroundError("업무 데스크 실행 잠금을 만들지 못했습니다.")
    exists = ctypes.get_last_error() == 183
    try:
        yield not exists
    finally:
        kernel.CloseHandle(handle)


def launch(config, argv):
    if os.name != "nt":
        raise BackgroundError("창 없는 실행은 Windows에서만 지원합니다.")
    port = config["port"]
    open_browser = "--no-browser" not in argv
    if is_running(port):
        if open_browser:   # 열려 있는 창이 있으면 그 창을 앞으로, 없으면 전용 창을 새로
            window.show_ui(port, config.data_dir, config["app_window"])
        return 0
    command = [sys.executable, str(APP_DIR / "desk.py"),
               *(arg for arg in argv if arg != "--background")]
    try:
        process = subprocess.Popen(
            command, cwd=APP_DIR, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True)
    except OSError as e:
        raise BackgroundError("백그라운드 실행을 시작하지 못했습니다. Python 설치를 확인하세요.") from e
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if is_running(port):
            return 0
        code = process.poll()
        if code == EXIT_UPDATING:
            return 0
        if code is not None:
            raise BackgroundError("업무 데스크를 켜지 못했습니다. 같은 포트를 쓰는 프로그램이 있는지 확인하세요.")
        time.sleep(0.25)
    process.terminate()
    process.wait(timeout=5)
    raise BackgroundError("업무 데스크 시작 시간이 초과되었습니다. 다시 실행해 주세요.")
