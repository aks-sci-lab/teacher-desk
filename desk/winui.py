"""윈도우 창 다루기 (쿨메신저 쪽지 보내기용, 0.6.5).

파이썬 표준 라이브러리(ctypes)로 윈도우 기본 기능만 씁니다. 하는 일:
- 창 찾기(창 종류 이름·컨트롤 번호), 창 글 읽기·넣기
- 진짜 마우스 클릭·키 입력(SendInput) — 쿨메신저 단추는 직접 그리는 단추라 프로그램 신호(BM_CLICK)로는 안 눌림
- 클립보드에 글 넣기·되돌리기 (본문 칸이 크롬 방식이라 붙여넣기로만 들어감)
- 다른 프로그램(쿨메신저, 32비트)의 목록·조직도 글 읽기 (LVM_GETITEMTEXT·TVM_GETITEM)

윈도우가 아니면 Unsupported 를 냅니다. 화면이 잠겨 있으면 클릭·입력이 막히므로 먼저 확인합니다.
"""

import ctypes
import os
import struct
import time

IS_WINDOWS = os.name == "nt"


class Unsupported(Exception):
    pass


WM_SETTEXT, WM_GETTEXT, WM_GETTEXTLENGTH = 0x000C, 0x000D, 0x000E
GW_OWNER = 4
GWL_STYLE = -16
WS_VISIBLE, WS_DISABLED = 0x10000000, 0x08000000
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x8000, 0x4000
VK_CONTROL, VK_RETURN, VK_MENU = 0x11, 0x0D, 0x12
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
PROCESS_VM = 0x0008 | 0x0010 | 0x0020 | 0x0400   # VM_OPERATION | VM_READ | VM_WRITE | QUERY_INFORMATION
MEM_COMMIT_RESERVE, MEM_RELEASE, PAGE_READWRITE = 0x3000, 0x8000, 0x04
LVM_GETITEMCOUNT, LVM_GETITEMTEXTW = 0x1004, 0x1073
TVM_GETNEXTITEM, TVM_GETITEMW, TVM_ENSUREVISIBLE, TVM_GETITEMRECT, TVM_EXPAND = 0x110A, 0x113E, 0x1114, 0x1104, 0x1102
TVGN_ROOT, TVGN_NEXT, TVGN_CHILD = 0, 1, 4
TVIF_TEXT, TVE_EXPAND = 0x0001, 0x0002
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79

if IS_WINDOWS:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.GetDlgItem.restype = wintypes.HWND
    user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetParent.restype = wintypes.HWND
    user32.GetWindowLongW.restype = wintypes.LONG
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.VirtualAllocEx.restype = ctypes.c_void_p
    kernel32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
    kernel32.VirtualFreeEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
    kernel32.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                            ctypes.POINTER(ctypes.c_size_t)]
    kernel32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                           ctypes.POINTER(ctypes.c_size_t)]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]


def need_windows():
    if not IS_WINDOWS:
        raise Unsupported("윈도우에서만 할 수 있습니다.")


# ---- 창 찾기 ----
def class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def text(hwnd):
    """창 글 (다른 프로그램의 창도 WM_GETTEXT 로)."""
    n = user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 2)
    user32.SendMessageW(hwnd, WM_GETTEXT, n + 1, ctypes.addressof(buf))
    return buf.value


def set_text(hwnd, value):
    buf = ctypes.create_unicode_buffer(value)
    user32.SendMessageW(hwnd, WM_SETTEXT, 0, ctypes.addressof(buf))
    return text(hwnd) == value


def pid_of(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def exe_of(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(h)


def top_windows(exe_name, visible_only=True):
    """exe_name 프로그램의 (보이는) 최상위 창들."""
    need_windows()
    found, exes = [], {}

    def visit(hwnd, _):
        if not visible_only or user32.IsWindowVisible(hwnd):
            pid = pid_of(hwnd)
            if pid not in exes:
                exes[pid] = exe_of(pid).lower()
            if exes[pid] == exe_name.lower():
                found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(visit), 0)
    return found


def children(hwnd):
    out = []

    def visit(h, _):
        out.append(h)
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(visit), 0)
    return out


def item(hwnd, ctrl_id):
    """컨트롤 번호로 바로 아래 자식 찾기, 없으면 손자까지."""
    h = user32.GetDlgItem(hwnd, ctrl_id)
    if h:
        return h
    for c in children(hwnd):
        if user32.GetDlgCtrlID(c) == ctrl_id:
            return c
    return None


def item_id(hwnd):
    """창의 컨트롤 번호."""
    return user32.GetDlgCtrlID(hwnd)


def close_window(hwnd):
    """창에 닫기 신호(WM_CLOSE)를 보냄 — 윈도우 '열기' 창이면 [취소]와 같음."""
    user32.PostMessageW(hwnd, 0x0010, 0, 0)


def is_visible(hwnd):
    return bool(hwnd) and bool(user32.IsWindowVisible(hwnd))


def is_window(hwnd):
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


# ---- 화면 잠금 ----
def screen_locked():
    """화면이 잠겼으면(입력 데스크톱이 'Default' 가 아니면) True."""
    if not IS_WINDOWS:
        return False
    h = user32.OpenInputDesktop(0, False, 0x0001)   # DESKTOP_READOBJECTS
    if not h:
        return True
    try:
        buf = ctypes.create_unicode_buffer(256)
        need = wintypes.DWORD()
        user32.GetUserObjectInformationW(h, 2, buf, ctypes.sizeof(buf), ctypes.byref(need))   # UOI_NAME
        return buf.value.lower() != "default"
    finally:
        user32.CloseDesktop(h)


# ---- 앞으로 가져오기·입력 ----
def bring_front(hwnd, wait=0.3):
    if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
        user32.ShowWindow(hwnd, 9)   # SW_RESTORE (트레이로 숨긴 창도 다시 띄움)
    if user32.GetForegroundWindow() != hwnd:
        user32.keybd_event(VK_MENU, 0, 0, 0)    # Alt 를 한 번 눌러야 윈도우가 앞으로 가져오기를 허락함
        user32.keybd_event(VK_MENU, 0, 2, 0)
        user32.SetForegroundWindow(hwnd)
    time.sleep(wait)
    if user32.GetForegroundWindow() != hwnd:   # 안 되면 맨 앞 창의 입력에 잠깐 붙어서 다시
        me = kernel32.GetCurrentThreadId()
        other = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        if other and other != me and user32.AttachThreadInput(me, other, True):
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                user32.AttachThreadInput(me, other, False)
            time.sleep(wait)
    return user32.GetForegroundWindow() == hwnd


def _send(inputs):
    arr = (INPUT * len(inputs))(*inputs)
    return user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)) == len(inputs)


def _mouse(flags, x=0, y=0):
    return INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(x, y, 0, flags, 0, 0)))


def _key(vk=0, scan=0, flags=0):
    return INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=KEYBDINPUT(vk, scan, flags, 0, 0)))


def click_at(x, y, double=False):
    vx, vy = user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw, vh = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    ax = int((x - vx) * 65535 / max(vw - 1, 1))
    ay = int((y - vy) * 65535 / max(vh - 1, 1))
    absf = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    seq = [_mouse(MOUSEEVENTF_MOVE | absf, ax, ay), _mouse(MOUSEEVENTF_LEFTDOWN | absf, ax, ay),
           _mouse(MOUSEEVENTF_LEFTUP | absf, ax, ay)]
    ok = _send(seq)
    if double:
        time.sleep(0.05)
        ok = _send(seq[1:]) and ok
    return ok


def click(hwnd, double=False):
    left, top, right, bottom = rect(hwnd)
    return click_at((left + right) // 2, (top + bottom) // 2, double)


def hotkey(*vks):
    downs = [_key(vk) for vk in vks]
    ups = [_key(vk, flags=KEYEVENTF_KEYUP) for vk in reversed(vks)]
    return _send(downs + ups)


def type_text(value):
    seq = []
    for b in struct.unpack(f"<{len(value.encode('utf-16-le')) // 2}H", value.encode("utf-16-le")):
        seq += [_key(0, b, KEYEVENTF_UNICODE), _key(0, b, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
    return _send(seq) if seq else True


# ---- 클립보드 ----
def _open_clipboard():
    for _ in range(20):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.05)
    return False


def get_clipboard_text():
    """지금 클립보드의 글 (글이 아니면 None)."""
    if not _open_clipboard():
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p) if p else None
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(value):
    if not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        if value is None:
            return True
        data = value.encode("utf-16-le") + b"\x00\x00"
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(h)
        return bool(user32.SetClipboardData(CF_UNICODETEXT, h))
    finally:
        user32.CloseClipboard()


# ---- 다른 프로그램의 목록·조직도 읽기 (쿨메신저는 32비트) ----
class Remote:
    """다른 프로세스 안에 잠깐 메모리를 잡아 목록·트리 글을 받아 옴. with 로 쓰면 끝나고 돌려줌."""

    SIZE = 8192

    def __init__(self, hwnd):
        self.h = kernel32.OpenProcess(PROCESS_VM, False, pid_of(hwnd))
        if not self.h:
            raise Unsupported("쿨메신저 창을 읽을 권한이 없습니다 (관리자 권한으로 켜져 있으면 안 됨).")
        self.base = kernel32.VirtualAllocEx(self.h, None, self.SIZE, MEM_COMMIT_RESERVE, PAGE_READWRITE)
        if not self.base or self.base >= 0xFFFFFFFF:
            self.close()
            raise Unsupported("쿨메신저 창 글을 읽을 자리를 잡지 못했습니다.")

    def write(self, offset, data):
        n = ctypes.c_size_t()
        kernel32.WriteProcessMemory(self.h, self.base + offset, data, len(data), ctypes.byref(n))

    def read(self, offset, size):
        buf = ctypes.create_string_buffer(size)
        n = ctypes.c_size_t()
        kernel32.ReadProcessMemory(self.h, self.base + offset, buf, size, ctypes.byref(n))
        return buf.raw[:n.value]

    def close(self):
        if self.h:
            if getattr(self, "base", None):
                kernel32.VirtualFreeEx(self.h, self.base, 0, MEM_RELEASE)
            kernel32.CloseHandle(self.h)
            self.h = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def wtext(self, offset, chars):
        raw = self.read(offset, chars * 2)
        return raw.decode("utf-16-le", "replace").split("\x00", 1)[0]


TEXT_AT, TEXT_CHARS = 1024, 2048


def list_rows(hwnd, columns=4, limit=200):
    """ListView(32비트 프로세스) 앞쪽 limit 줄의 글 [[열1, 열2, …], …]."""
    count = user32.SendMessageW(hwnd, LVM_GETITEMCOUNT, 0, 0)
    rows = []
    with Remote(hwnd) as r:
        for i in range(min(count, limit)):
            row = []
            for col in range(columns):
                # LVITEMW (32비트): mask, iItem, iSubItem, state, stateMask, pszText, cchTextMax, ...
                lv = struct.pack("<IiiIIIi", 0x0001, i, col, 0, 0, r.base + TEXT_AT, TEXT_CHARS) + b"\x00" * 32
                r.write(0, lv)
                r.write(TEXT_AT, b"\x00\x00")
                user32.SendMessageW(hwnd, LVM_GETITEMTEXTW, i, r.base)
                row.append(r.wtext(TEXT_AT, TEXT_CHARS))
            rows.append(row)
    return rows


def tree_items(hwnd, expand=False, limit=5000):
    """TreeView(32비트 프로세스) 항목 [(핸들, 글, 깊이)] (펼쳐진 것만, expand 면 펼치며)."""
    out = []
    with Remote(hwnd) as r:
        def label(h):
            # TVITEMW (32비트): mask, hItem, state, stateMask, pszText, cchTextMax, ...
            tv = struct.pack("<IIIIIi", TVIF_TEXT, h, 0, 0, r.base + TEXT_AT, TEXT_CHARS) + b"\x00" * 24
            r.write(0, tv)
            r.write(TEXT_AT, b"\x00\x00")
            user32.SendMessageW(hwnd, TVM_GETITEMW, 0, r.base)
            return r.wtext(TEXT_AT, TEXT_CHARS)

        def walk(h, depth):
            while h and len(out) < limit:
                out.append((h, label(h), depth))
                if expand:
                    user32.SendMessageW(hwnd, TVM_EXPAND, TVE_EXPAND, h)
                child = user32.SendMessageW(hwnd, TVM_GETNEXTITEM, TVGN_CHILD, h)
                if child:
                    walk(child, depth + 1)
                h = user32.SendMessageW(hwnd, TVM_GETNEXTITEM, TVGN_NEXT, h)

        walk(user32.SendMessageW(hwnd, TVM_GETNEXTITEM, TVGN_ROOT, 0), 0)
    return out


def tree_item_center(hwnd, h):
    """트리 항목을 보이게 한 뒤 화면 좌표 가운데."""
    user32.SendMessageW(hwnd, TVM_ENSUREVISIBLE, 0, h)
    with Remote(hwnd) as r:
        r.write(0, struct.pack("<I", h) + b"\x00" * 12)   # RECT 첫 칸에 항목 핸들을 넣어 보냄
        if not user32.SendMessageW(hwnd, TVM_GETITEMRECT, 1, r.base):   # 1: 글자 부분만
            return None
        left, top, right, bottom = struct.unpack("<iiii", r.read(0, 16))
    pt = wintypes.POINT((left + right) // 2, (top + bottom) // 2)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


TVM_HITTEST = 0x1111


def foreground():
    return user32.GetForegroundWindow()


def center(hwnd):
    left, top, right, bottom = rect(hwnd)
    return (left + right) // 2, (top + bottom) // 2


def item_direct(hwnd, ctrl_id):
    """바로 아래 자식 중 컨트롤 번호가 ctrl_id 인 것."""
    return user32.GetDlgItem(hwnd, ctrl_id) or None


def window_at(pt, expect):
    """화면 점 pt 에 있는 창이 expect 이거나 그 안의 창이면 True (클릭이 엉뚱한 창에 가지 않게)."""
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    h = user32.WindowFromPoint(wintypes.POINT(*pt))
    while h:
        if h == expect:
            return True
        h = user32.GetParent(h)
    return False


def tree_hit(tree, pt, handle):
    """화면 점 pt 가 조직도에서 바로 그 항목 위인지 (TVM_HITTEST)."""
    p = wintypes.POINT(*pt)
    user32.ScreenToClient(tree, ctypes.byref(p))
    with Remote(tree) as r:
        # TVHITTESTINFO (32비트): POINT pt, UINT flags, HTREEITEM hItem
        r.write(0, struct.pack("<iiII", p.x, p.y, 0, 0))
        hit = user32.SendMessageW(tree, TVM_HITTEST, 0, r.base)
        flags, got = struct.unpack("<II", r.read(8, 8))
    return bool(hit) and got == handle and bool(flags & 0x0046)   # TVHT_ONITEM (아이콘·글·상태)


LVM_ENSUREVISIBLE, LVM_GETITEMRECT = 0x1013, 0x100E


def list_item_center(lv, i):
    """ListView 의 i 번째 줄을 보이게 한 뒤 글 부분 가운데 화면 좌표 (32비트 프로세스)."""
    user32.SendMessageW(lv, LVM_ENSUREVISIBLE, i, 0)
    with Remote(lv) as r:
        r.write(0, struct.pack("<iiii", 2, 0, 0, 0))   # left 칸에 LVIR_LABEL
        if not user32.SendMessageW(lv, LVM_GETITEMRECT, i, r.base):
            return None
        left, top, right, bottom = struct.unpack("<iiii", r.read(0, 16))
    pt = wintypes.POINT((left + right) // 2, (top + bottom) // 2)
    user32.ClientToScreen(lv, ctypes.byref(pt))
    return pt.x, pt.y


def click_checked(hwnd, top):
    """hwnd 가운데를 진짜 마우스로 클릭 — 그 자리가 hwnd 이고 맨 앞 창이 top 일 때만. 눌렀으면 True."""
    if not hwnd or not bring_front(top):
        return False
    pt = center(hwnd)
    if not window_at(pt, hwnd) or foreground() != top:
        return False
    return click_at(*pt)


def registry_value(path, name):
    """HKEY_CURRENT_USER 아래 값 (없으면 None)."""
    if not IS_WINDOWS:
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None
