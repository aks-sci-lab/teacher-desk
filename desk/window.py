"""업무 데스크 화면을 '전용 창' 하나로 열기 (0.5.43).

- 주소창 없는 전용 창(크롬·엣지의 --app)으로 열면, 다른 브라우저 창을 닫을 때 같이 닫히지 않습니다.
- 화면이 이미 열려 있으면 새로 열지 않고 그 창을 앞으로 가져옵니다 (윈도우). 못 찾으면 새로 엽니다.
- 열려 있는 화면은 서버가 압니다: 화면이 몇 초마다 보내는 신호(X-Desk-Client)로 확인하고, 창이 닫힐 때 알려 줍니다.
- 그래도 두 화면이 열리면 나중에 연 화면이 이기고, 먼저 열린 화면에는 안내가 덮입니다 (화면 쪽 web/js/singleton.js).
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

WINDOW_TITLE = "교사 업무 데스크"   # web/index.html 의 <title> 과 같아야 앞으로 가져올 수 있음
# 창을 가려 두면 브라우저가 신호를 1분에 한 번으로 줄이므로 넉넉히 (창이 닫힐 때는 바로 알려 줌)
ALIVE_SECONDS = 180
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


class UiTracker:
    """지금 열려 있는 업무 데스크 화면들과, 그중 '쓰는 화면'(가장 나중에 연 화면)."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.seen = {}
        self.active = None

    def touch(self, cid):
        with self.lock:
            self.seen[cid] = self.clock()

    def claim(self, cid):
        with self.lock:
            self.seen[cid] = self.clock()
            self.active = cid

    def bye(self, cid):
        with self.lock:
            self.seen.pop(cid, None)
            if self.active == cid:
                self.active = None

    def state(self):
        with self.lock:
            now = self.clock()
            for cid in [c for c, t in self.seen.items() if now - t > ALIVE_SECONDS]:
                del self.seen[cid]
            if self.active not in self.seen:
                self.active = None
            return {"open": bool(self.seen), "active": self.active, "count": len(self.seen)}


# ---------------------------------------------------------------- 전용 창
def _candidates():
    env = os.environ.get
    roots = [env("ProgramFiles(x86)"), env("ProgramFiles"), env("LOCALAPPDATA")]
    out = []
    for root in filter(None, roots):   # 엣지는 윈도우에 기본으로 있으므로 먼저
        out.append(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    for root in filter(None, roots):
        out.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    return out


def find_browser():
    """전용 창으로 열 수 있는 브라우저(엣지·크롬) 실행 파일. 없으면 None."""
    for p in _candidates():
        if p.is_file():
            return str(p)
    for name in ("msedge", "microsoft-edge", "google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def app_command(exe, url, profile):
    """전용 창 실행 명령. 전용 설정 폴더를 따로 써서 다른 브라우저 창과 완전히 따로 돈다."""
    return [exe, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "--window-size=1380,880"]


def open_ui(port, data_dir, app_window=True):
    """업무 데스크 화면을 새로 엶. 'window'(전용 창) 또는 'tab'(기본 브라우저 새 탭)을 돌려줌."""
    url = f"http://127.0.0.1:{port}/"
    exe = find_browser() if app_window else None
    if exe:
        profile = Path(data_dir) / "app-window"
        profile.mkdir(parents=True, exist_ok=True)
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
        try:
            subprocess.Popen(app_command(exe, url, profile), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
            return "window"
        except OSError:
            pass   # 못 열면 기본 브라우저로
    webbrowser.open(url)
    return "tab"


def focus_window(title=WINDOW_TITLE):
    """윈도우: 제목에 title 이 든 창(전용 창 또는 브라우저 탭)을 앞으로 가져옴. 찾아서 가져왔으면 True."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        text = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, text, n + 1)
        if title not in text.value:
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if "Console" in cls.value or "CASCADIA" in cls.value:   # start-desk.bat 이 열었던 검은 창은 제외
            return True
        found.append(hwnd)
        return True

    user32.EnumWindows(proc_type(visit), 0)
    if not found:
        return False
    hwnd = found[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)   # SW_RESTORE
    user32.keybd_event(0x12, 0, 0, 0)   # Alt 를 한 번 눌러야 윈도우가 앞으로 가져오기를 허락함
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)
    return True


def fetch_ui_state(port):
    """이미 켜진 업무 데스크에 지금 열린 화면이 있는지 물어봄 (화면을 열 때와 같은 방법으로 토큰을 받음). 실패하면 None."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{port}"
    try:
        with opener.open(base + "/", timeout=2) as r:
            m = re.search(rb'name="desk-token" content="([^"]+)"', r.read(100000))
        if not m:
            return None
        req = urllib.request.Request(base + "/api/ui", headers={"X-Desk-Token": m.group(1).decode()})
        with opener.open(req, timeout=2) as r:
            return json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def show_ui(port, data_dir, app_window=True, *, fetch=fetch_ui_state, focus=focus_window, opener=open_ui,
            is_windows=os.name == "nt"):
    """켜져 있는 업무 데스크의 화면을 보여 줌. 'focused'(열려 있는 창을 앞으로) · 'already-open'(개발용: 이미 열려 있음) ·
    'window'/'tab'(새로 열었음)."""
    state = fetch(port)
    if state and state.get("open"):
        if focus():
            return "focused"
        if not is_windows:
            return "already-open"
        # 열려 있다는 신호는 있는데 창을 못 찾으면(브라우저가 갑자기 꺼진 경우 등) 새로 엶
    return opener(port, data_dir, app_window)
