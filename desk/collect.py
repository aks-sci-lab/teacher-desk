"""폴더에서 새 자료를 모아 수신함에 넣습니다 (하루 3회 + '지금 수집').

- 에듀파인: 선생님이 저장한 공문 파일(.hwp .hwpx .pdf .txt)이 모이는 폴더
- 쿨메신저 쪽지: 설정한 이름의  %LOCALAPPDATA%\\CoolMessenger\\Memo\\<이름>.udb  (coolmsg.py)
- 쿨메신저 받은 파일(선택): 쿨메신저가 받은 파일을 저장하는 폴더의 문서·글 파일
"""

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from . import coolmsg, extract
from .db import now_str
from .docs import SUPPORTED, DocError, read_document

SOURCES = {"edufine": "edufine_folder", "cool": "coolmessenger_folder"}
SOURCE_NAMES = {"edufine": "에듀파인", "cool": "쿨메신저", "paste": "직접 입력", "school": "학사일정"}
MAX_FILES_PER_RUN = 500


def content_hash(text):
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def add_text_item(db, source, text, title=None, received_at=None, file_path=None, ref=None):
    """글 하나를 분석해 수신함에 저장. 같은 내용이 이미 있으면 None."""
    text = text.strip()
    if not text:
        return None
    h = content_hash(text)
    if db.one("SELECT id FROM items WHERE content_hash = ?", (h,)):
        return None
    received_at = received_at or now_str()
    ref = ref or datetime.strptime(received_at[:10], "%Y-%m-%d").date()
    analysis = extract.analyze(text, ref=ref, title=title)
    item_id = db.add_item(source, analysis["title"], text, received_at, analysis,
                          content_hash=h, file_path=file_path)
    return {"id": item_id, "events": len(analysis["events"]),
            "deadlines": sum(1 for e in analysis["events"] if e["kind"] == "deadline")}


def scan_folder(db, source, folder, stats):
    folder = Path(folder)
    if not folder.is_dir():
        stats["errors"].append(f"{SOURCE_NAMES[source]} 폴더를 찾을 수 없습니다: {folder}")
        return
    files = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in names:
            if name.startswith(("~$", ".")) or Path(name).suffix.lower() not in SUPPORTED:
                continue
            files.append(Path(root) / name)
    files.sort(key=lambda p: p.stat().st_mtime)
    for path in files[-MAX_FILES_PER_RUN:]:
        try:
            st = path.stat()
        except OSError:
            continue
        key = str(path)
        seen = db.one("SELECT mtime, size FROM files_seen WHERE path = ?", (key,))
        if seen and seen["mtime"] == st.st_mtime and seen["size"] == st.st_size:
            continue
        item_id = None
        try:
            text = read_document(path)
            received = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            # 공문은 저장할 때 파일 이름이 곧 제목인 경우가 많음
            title = path.stem if source == "edufine" else None
            res = add_text_item(db, source, text, title=title, received_at=received, file_path=key)
            if res:
                item_id = res["id"]
                stats["items"] += 1
                stats["events"] += res["events"]
                stats["deadlines"] += res["deadlines"]
                stats["new_ids"].append(item_id)
        except DocError as e:
            stats["errors"].append(f"{path.name}: {e}")
        except OSError as e:
            stats["errors"].append(f"{path.name}: 읽기 실패 ({e.strerror})")
            continue  # 다음 수집 때 다시 시도
        db.execute("INSERT INTO files_seen(path, mtime, size, item_id) VALUES(?,?,?,?) "
                   "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, size=excluded.size, "
                   "item_id=COALESCE(excluded.item_id, files_seen.item_id)",
                   (key, st.st_mtime, st.st_size, item_id))


def scan_cool_memo(db, name, stats, path=""):
    """쿨메신저 쪽지 기록에서 지난번 이후 새 쪽지만 가져옴 (경로를 직접 지정했으면 그 파일)."""
    try:
        path = coolmsg.resolve(name, path)
        key = f"cool_last_rowid:{path}"
        _, memos, top = coolmsg.read_new(path, db.get_meta(key))
    except coolmsg.CoolError as e:
        stats["errors"].append(f"쿨메신저: {e}")
        return
    for m in memos:
        text = m["body"]
        if m["sender"]:
            text += f"\n\n(보낸 사람: {m['sender']})"
        received = (m["when"] or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        res = add_text_item(db, "cool", text, title=m["title"] or None, received_at=received,
                            file_path=f"{path}#{m['rowid']}")
        if res:
            stats["items"] += 1
            stats["events"] += res["events"]
            stats["deadlines"] += res["deadlines"]
            stats["new_ids"].append(res["id"])
    db.set_meta(key, top)


class Collector:
    """수집 실행기. 동시에 두 번 돌지 않게 잠금."""

    def __init__(self, db, config, notify=None):
        self.db, self.config, self.notify = db, config, notify
        self.lock = threading.Lock()
        self.last_watch_errors = set()

    def run(self, reason="manual", slot_keys=()):
        if not self.lock.acquire(blocking=False):
            return {"busy": True}
        try:
            run_id = self.db.execute("INSERT INTO runs(started_at, reason, slot_keys) VALUES(?,?,?)",
                                     (now_str(), reason, json.dumps(list(slot_keys))))
            stats = {"items": 0, "events": 0, "deadlines": 0, "errors": [], "new_ids": [], "sources": []}
            if self.config["coolmessenger_name"] or self.config["coolmessenger_path"]:
                stats["sources"].append("cool")
                scan_cool_memo(self.db, self.config["coolmessenger_name"], stats, self.config["coolmessenger_path"])
            for source, key in SOURCES.items():
                folder = self.config[key]
                if folder:
                    stats["sources"].append(source)
                    scan_folder(self.db, source, folder, stats)
            self.db.execute("UPDATE runs SET finished_at = ?, stats = ? WHERE id = ?",
                            (now_str(), json.dumps(stats, ensure_ascii=False), run_id))
            label = {"schedule": "예약", "manual": "수동", "watch": "실시간", "telegram": "텔레그램"}.get(reason, reason)
            msg = f"수집({label}): 새 항목 {stats['items']}건, 일정 {stats['events']}건(마감 {stats['deadlines']}건)"
            if not stats["sources"]:
                msg += " — 수집할 폴더가 설정되지 않았습니다"
            # 실시간 감시는 자주 돌므로 새 자료가 있거나 처음 보는 문제일 때만 기록
            errors = stats["errors"][:20]
            if reason == "watch":
                errors = [e for e in errors if e not in self.last_watch_errors]
                self.last_watch_errors = set(stats["errors"])
            if reason != "watch" or stats["items"] or errors:
                self.db.log(msg)
            for err in errors:
                self.db.log(err, "warn")
            if self.notify:
                try:
                    self.notify(stats, reason)
                except Exception as e:  # 알림 실패가 수집 결과를 막지 않게
                    self.db.log(f"알림 실패: {e}", "warn")
            return stats
        finally:
            self.lock.release()


def diagnose(folder, limit=200):
    """폴더 구조만 살펴봅니다 (메시지 내용은 읽지 않음). 쿨메신저 전용 읽기 코드를 만들 때 씁니다."""
    folder = Path(folder)
    if not folder.is_dir():
        return {"error": f"폴더를 찾을 수 없습니다: {folder}"}
    by_ext, files, dbs = {}, [], []
    for root, dirs, names in os.walk(folder):
        for name in names:
            p = Path(root) / name
            try:
                st = p.stat()
            except OSError:
                continue
            ext = p.suffix.lower() or "(없음)"
            e = by_ext.setdefault(ext, {"count": 0, "bytes": 0})
            e["count"] += 1
            e["bytes"] += st.st_size
            if len(files) < limit:
                files.append({"path": str(p.relative_to(folder)), "bytes": st.st_size,
                              "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                              "kind": sniff(p)})
            if sniff(p) == "sqlite" and len(dbs) < 10:
                dbs.append(sqlite_shape(p, folder))
    files.sort(key=lambda f: f["modified"], reverse=True)
    return {"folder": str(folder), "extensions": by_ext, "recent_files": files[:60], "databases": dbs,
            "note": "파일 이름·크기·형식과 데이터베이스의 표·열 이름만 담았습니다. 메시지 내용은 없습니다."}


def sniff(path):
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return "읽기 실패"
    if head.startswith(b"SQLite format 3"):
        return "sqlite"
    if head.startswith(b"\xD0\xCF\x11\xE0"):
        return "복합문서(hwp/doc)"
    if head.startswith(b"PK"):
        return "zip(hwpx 등)"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.lstrip().startswith((b"<", b"\xef\xbb\xbf<")):
        return "xml/html"
    if head.startswith((b"{", b"[")):
        return "json"
    try:
        head.decode("utf-8")
        return "글자(utf-8)"
    except UnicodeDecodeError:
        return "알 수 없음"


def sqlite_shape(path, folder):
    """표 이름·열 이름·행 개수만 (읽기 전용으로 열기)."""
    out = {"path": str(path.relative_to(folder)), "tables": []}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1)
        try:
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                quoted = '"' + name.replace('"', '""') + '"'
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({quoted})")]
                count = conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
                out["tables"].append({"name": name, "columns": cols, "rows": count})
        finally:
            conn.close()
    except sqlite3.Error as e:
        out["error"] = f"열 수 없음 ({e}) — 암호화되었거나 쿨메신저가 사용 중일 수 있습니다"
    return out
