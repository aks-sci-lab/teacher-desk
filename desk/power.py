"""PC 가 저절로 절전(대기)에 들어가지 않게 (0.6.25, 선생님 결정 2026-11-27).

절전(대기)에 들어가면 PC 가 멈춰 텔레그램 명령도, 쿨메신저 쪽지 보내기도 못 합니다.
앱이 켜져 있는 동안 1분마다 윈도우에 "아직 일하는 중" 이라고 알려(SetThreadExecutionState, ES_SYSTEM_REQUIRED)
'일정 시간 안 쓰면 절전' 시계를 처음으로 되돌립니다.

하지 않는 것:
- 화면 끄기·화면 보호기·화면 잠금은 그대로 둡니다 (학교 보안 규칙). 잠긴 동안의 쪽지는 텔레그램이 대기해 둠.
- 선생님이 시작 메뉴·전원 단추로 직접 절전하는 것은 막지 않습니다.
- 설정 '앱이 켜져 있는 동안 절전 막기'(keep_awake)를 끄면 알리지 않습니다 (그러면 윈도우 전원 설정대로).
"""

import ctypes
import os
import threading

ES_SYSTEM_REQUIRED = 0x00000001
EVERY = 60   # 초 (윈도우의 가장 짧은 절전 시간 1분보다 짧지 않아도 됨: 알릴 때마다 시계가 처음부터)


def nudge():
    """한 번 알림. 윈도우가 아니거나 실패하면 False."""
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(ES_SYSTEM_REQUIRED))
    except (AttributeError, OSError):
        return False


class KeepAwake:
    def __init__(self, config, every=EVERY):
        self.config, self.every = config, every
        self.stop_event = threading.Event()
        self.thread = None

    def tick(self):
        return nudge() if self.config["keep_awake"] else False

    def start(self):
        if os.name != "nt":
            return
        self.thread = threading.Thread(target=self._loop, name="keep-awake", daemon=True)
        self.thread.start()

    def _loop(self):
        while True:
            self.tick()
            if self.stop_event.wait(self.every):
                return

    def stop(self):
        self.stop_event.set()
