"""쿨메신저 첨부를 '화면 뒤에서' 받을 수 있는지 시험 (학교 PC 에서 손으로 실행, 2026-11-28).

지금 업무 데스크는 메시지관리함을 앞으로 띄우고 진짜 마우스로 쪽지 줄과 [모든파일 저장]을 누릅니다.
이 시험은 마우스·앞으로 가져오기 없이, 창에 '명령'만 보내서 받아지는지 봅니다.
되면 화면이 가려지지 않고, 잠금 중에도 받을 수 있을지 모릅니다.

    py tools\\coolsave_background_test.py         지금 바로 시험
    py tools\\coolsave_background_test.py 잠금    번호를 고른 뒤 30초 기다렸다가 시험 (그 사이 Win+L 로 잠그기)

준비: 쿨메신저 메시지관리함을 열고 [받은메시지]를 눌러 둔 뒤, 다른 창(메모장 등)을 그 위에 올려 두세요.
쿨메신저 프로그램은 바꾸지 않습니다. 쪽지를 보내지도 않습니다. 결과는 화면과 coolsave_background_result.txt 에 남깁니다
(쪽지 제목·보낸 사람은 파일에 남기지 않음).
"""

import ctypes
import struct
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desk import coolsend, winui  # noqa: E402

WM_COMMAND, BN_CLICKED = 0x0111, 0
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON = 0x0200, 0x0201, 0x0202, 0x0001
LVM_SETITEMSTATE, LVIS_SELECTED_FOCUSED = 0x102B, 0x0003
WAIT_EACH = 15.0
RESULT = Path("coolsave_background_result.txt")
_lines = []


def say(msg):
    print(msg)
    _lines.append(msg)


def find_manager():
    for h in winui.top_windows(coolsend.EXE, visible_only=False):
        lv = winui.item(h, coolsend.ID_LIST)
        if lv and winui.class_name(lv) == "SysListView32":
            return h, lv
    return None, None


def select_in_background(lv, i):
    """마우스 없이 목록 i 번째 줄만 고른 상태로 (LVM_SETITEMSTATE)."""
    with winui.Remote(lv) as r:
        # LVITEMW (32비트): mask, iItem, iSubItem, state, stateMask, ...
        r.write(0, struct.pack("<IiiII", 0, 0, 0, 0, LVIS_SELECTED_FOCUSED) + b"\x00" * 40)
        winui.user32.SendMessageW(lv, LVM_SETITEMSTATE, -1 & 0xFFFFFFFF, r.base)   # 모두 풀기
        r.write(0, struct.pack("<IiiII", 0, 0, 0, LVIS_SELECTED_FOCUSED, LVIS_SELECTED_FOCUSED) + b"\x00" * 40)
        ok = winui.user32.SendMessageW(lv, LVM_SETITEMSTATE, i, r.base)
    winui.user32.SendMessageW(lv, winui.LVM_ENSUREVISIBLE, i, 0)
    return bool(ok)


def post(hwnd, msg, wparam, lparam):
    PostMessageW = winui.user32.PostMessageW
    PostMessageW.argtypes = [winui.wintypes.HWND, winui.wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    return bool(PostMessageW(hwnd, msg, wparam, lparam))


def wait_new(folder, before):
    end, last, stable = time.monotonic() + WAIT_EACH, None, 0.0
    new = []
    while time.monotonic() < end:
        time.sleep(0.5)
        now = coolsend._files(folder)
        new = sorted(p for p, v in now.items() if before.get(p) != v)
        if new and now == last:
            stable += 0.5
            if stable >= 2.0:
                break
        else:
            stable = 0.0
        last = now
    return new


def main():
    winui.need_windows()
    wait_lock = len(sys.argv) > 1 and sys.argv[1] == "잠금"
    ui = coolsend.WinAttachUI()
    folder = ui.download_dir()
    if not folder or not folder.is_dir():
        print("쿨메신저 받은 파일 폴더를 찾지 못했습니다 (쿨메신저 설정 → 파일 받기).")
        return 1
    manager, lv = find_manager()
    if not manager:
        print("메시지관리함 창이 없습니다. 쿨메신저에서 메시지관리함을 열고 [받은메시지]를 눌러 두세요.")
        return 1
    rows = [(i, r) for i, r in enumerate(ui.rows(manager)) if (r[3] or "").strip()][:10]
    if not rows:
        print("목록 앞쪽에 첨부가 있는 쪽지가 없습니다. [받은메시지]를 눌러 두었는지 확인해 주세요.")
        return 1
    for n, (_, r) in enumerate(rows, 1):
        print(f"{n}. {r[2]}  {r[1]}  [{r[3]}]")
    answer = input(f"받아 볼 쪽지 번호 (1~{len(rows)}, 그만두려면 Enter): ").strip()
    if not answer.isdigit() or not 1 <= int(answer) <= len(rows):
        print("취소했습니다.")
        return 1
    idx, row = rows[int(answer) - 1]

    say(f"시험 시각 {datetime.now():%Y-%m-%d %H:%M:%S}, 잠금 시험={'예' if wait_lock else '아니오'}")
    if wait_lock:
        print("30초 뒤 시작합니다. 지금 Win+L 로 잠그고, 1분쯤 뒤에 풀어 결과를 보세요.")
        time.sleep(30)
    say(f"화면 잠김: {winui.screen_locked()}")
    say(f"맨 앞 창이 메시지관리함인가(시작 전): {winui.foreground() == manager}")

    # 1) 마우스 없이 쪽지 줄 고르기
    say(f"1) 뒤에서 줄 고르기: {'됨' if select_in_background(lv, idx) else '안 됨'}")
    time.sleep(1.5)
    title_box = winui.item(manager, coolsend.ID_TITLE)
    shown = winui.text(title_box) if title_box else ""
    want = coolsend._squash(row[1])[:15]
    same = bool(want) and want in coolsend._squash(shown)
    say(f"   오른쪽 보기 칸 제목이 고른 쪽지와 같은가: {same}")
    if not same:
        say("   → 줄은 골라졌지만 쪽지 내용이 바뀌지 않았으면 [모든파일 저장]이 다른 쪽지를 받을 수 있어 여기서 멈춥니다.")
        return finish(1)

    button = winui.item(manager, coolsend.ID_SAVE_ALL)
    if not button:
        say("   [모든파일 저장] 단추(3320)를 찾지 못했습니다.")
        return finish(1)
    left, top, right, bottom = winui.rect(button)
    inside = (((bottom - top) // 2) << 16) | ((right - left) // 2)

    tries = [
        ("2) 창에 '단추 눌림' 명령 보내기(WM_COMMAND)",
         lambda: post(manager, WM_COMMAND, (BN_CLICKED << 16) | coolsend.ID_SAVE_ALL, button)),
        ("3) 단추에 마우스 누름·뗌 신호만 보내기(화면 마우스는 안 움직임)",
         lambda: post(button, WM_MOUSEMOVE, 0, inside) and post(button, WM_LBUTTONDOWN, MK_LBUTTON, inside)
         and (time.sleep(0.1) or True) and post(button, WM_LBUTTONUP, 0, inside)),
    ]
    for label, act in tries:
        before = coolsend._files(folder)
        sent = act()
        new = wait_new(folder, before)
        say(f"{label}: 신호 {'보냄' if sent else '못 보냄'}, 새 파일 {len(new)}개")
        if new:
            say("   ✅ 이 방법으로 받아졌습니다. 파일: " + ", ".join(p.suffix or "(확장자 없음)" for p in new))
            break
    else:
        say("   ❌ 두 방법 모두 새 파일이 생기지 않았습니다. 지금처럼 마우스로 누르는 방법이 필요합니다.")
    say(f"맨 앞 창이 메시지관리함인가(끝난 뒤): {winui.foreground() == manager}")
    say("쿨메신저에 묻는 창(덮어쓸까요 등)이 떠 있으면 그것도 알려 주세요.")
    return finish(0)


def finish(code):
    try:
        RESULT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
        print(f"\n결과를 {RESULT.resolve()} 에 남겼습니다. 이 내용을 그대로 보내 주세요.")
    except OSError:
        pass
    return code


if __name__ == "__main__":
    sys.exit(main())
