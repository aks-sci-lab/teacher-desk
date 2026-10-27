"""실시간 감시: 쿨메신저 쪽지 파일과 에듀파인·받은 파일 폴더가 바뀌면 바로 수집합니다.

윈도우 표준 기능만 쓰려고 몇 초마다 파일의 '바뀐 시각·크기'만 살펴봅니다 (내용은 읽지 않아 가볍습니다).
바뀐 것을 보면 한 번 더 기다려 저장이 끝난 것을 확인한 뒤 수집합니다 (내려받는 중인 파일을 읽지 않게).
9·12·15시 예약 수집은 혹시 놓친 것을 잡는 보조로 그대로 둡니다.
"""

import os
import threading
from pathlib import Path

from . import coolmsg
from .docs import SUPPORTED

MAX_FILES = 5000


def _stat(p):
    try:
        st = os.stat(p)
        return (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(p), None, None)


def _folder(folder):
    out = []
    try:
        for root, dirs, names in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for n in names:
                if not n.startswith(("~$", ".")) and Path(n).suffix.lower() in SUPPORTED:
                    out.append(_stat(Path(root) / n))
                    if len(out) >= MAX_FILES:
                        return out
    except OSError:
        pass
    return out


def signature(config):
    """감시하는 파일들의 (경로, 바뀐 시각, 크기) 모음. 같으면 바뀐 것이 없음."""
    sig = []
    if config["coolmessenger_name"] or config["coolmessenger_path"]:
        try:
            p = coolmsg.resolve(config["coolmessenger_name"], config["coolmessenger_path"])
            sig += [_stat(p), _stat(str(p) + "-wal")]  # 최근 쪽지는 -wal 에 먼저 쌓일 수 있음
        except coolmsg.CoolError:
            pass
    for key in ("edufine_folder", "coolmessenger_folder"):
        if config[key]:
            sig += sorted(_folder(config[key]))
    return tuple(sig)


class Watcher:
    def __init__(self, db, config, collector, interval=None):
        self.db, self.config, self.collector = db, config, collector
        self.interval = interval or max(5, int(config.get("watch_interval", 15) or 15))
        self.stop_event = threading.Event()
        self.thread = None
        self.last = None      # 마지막으로 수집한 때의 모습
        self.pending = None   # 바뀐 것을 처음 본 모습 (한 번 더 같으면 수집)
        self.runs = 0

    @property
    def enabled(self):
        return bool(self.config.get("watch_enabled", True))

    def tick(self):
        """한 번 살펴보기. 수집했으면 그 결과, 아니면 None."""
        if not self.enabled:
            self.last = self.pending = None
            return None
        sig = signature(self.config)
        if self.last is None:          # 켠 직후에는 지금 모습을 기준으로 (밀린 것은 예약 수집이 처리)
            self.last = sig
            return None
        if sig == self.last:
            self.pending = None
            return None
        if sig != self.pending:        # 바뀐 것을 처음 봄 → 저장이 끝나길 한 번 기다림
            self.pending = sig
            return None
        stats = self.collector.run("watch")
        if stats.get("busy"):
            return None                # 다른 수집 중 → 다음에 다시
        self.last = signature(self.config)
        self.pending = None
        self.runs += 1
        return stats

    def loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as e:  # 감시가 멈추지 않게 기록만
                self.db.log(f"실시간 감시 오류: {e}", "error")
            self.stop_event.wait(self.interval)

    def start(self):
        self.thread = threading.Thread(target=self.loop, name="watcher", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
