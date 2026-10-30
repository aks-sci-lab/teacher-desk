"""폴더에서 새 자료를 모아 수신함에 넣습니다 (하루 3회 + '지금 수집').

- 에듀파인: 선생님이 저장한 공문 파일(.hwp .hwpx .pdf .txt)이 모이는 폴더
- 쿨메신저 쪽지: 설정한 이름의  %LOCALAPPDATA%\\CoolMessenger\\Memo\\<이름>.udb  (coolmsg.py)
- 쿨메신저 받은 파일(선택): 쿨메신저가 받은 파일을 저장하는 폴더의 문서·글 파일
"""

import hashlib
import json
import os
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from . import coolmsg, extract
from .db import now_str
from .docs import SUPPORTED, DocError, fix_surrogates, read_document

SOURCES = {"edufine": "edufine_folder", "cool": "coolmessenger_folder"}
SOURCE_NAMES = {"edufine": "에듀파인", "cool": "쿨메신저", "cool_sent": "쿨메신저(보냄)", "paste": "직접 입력", "school": "학사일정"}
MAX_FILES_PER_RUN = 500


def content_hash(text):
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def add_text_item(db, source, text, title=None, received_at=None, file_path=None, ref=None, images=None):
    """글 하나를 분석해 수신함에 저장. 같은 내용이 이미 있으면 None.
    source 가 'cool_sent'(내가 보낸 쪽지)이면 일정 후보·알림·AI 요약에 쓰지 않고 읽은 것으로 저장."""
    text = fix_surrogates(text).strip()
    title = fix_surrogates(title) if title else title
    if not text:
        return None
    h = content_hash(text)
    if db.one("SELECT id FROM items WHERE content_hash = ?", (h,)):
        return None
    received_at = received_at or now_str()
    ref = ref or datetime.strptime(received_at[:10], "%Y-%m-%d").date()
    analysis = extract.analyze(text, ref=ref, title=title)
    if source == "cool_sent":
        analysis = {**analysis, "events": []}   # 내가 쓴 글의 날짜는 일정이 아님
    item_id = db.add_item(source, analysis["title"], text, received_at, analysis,
                          content_hash=h, file_path=file_path, images=images)
    if source == "cool_sent":
        db.execute("UPDATE items SET status = 'read' WHERE id = ?", (item_id,))
    return {"id": item_id, "events": len(analysis["events"]),
            "deadlines": sum(1 for e in analysis["events"] if e["kind"] == "deadline")}


# 윈도우가 파일 열기 자체를 거절할 때의 까닭 (파일 특성 값: OneDrive 등 '클라우드에만 있는 파일')
CLOUD_ATTRS = 0x400000 | 0x40000 | 0x1000   # RECALL_ON_DATA_ACCESS · RECALL_ON_OPEN · OFFLINE
CLOUD_REASON = ("이름만 있고 내용은 이 PC 에 없는 파일입니다 (Synology Drive·OneDrive 같은 동기화 프로그램이 남긴 자리 표시). "
                "동기화 프로그램을 지웠다면 이 파일들은 껍데기라 읽을 수 없습니다 → NAS·클라우드 웹 화면에서 폴더를 내려받아 "
                "보통 폴더(예: 문서\\공문)에 두고 그 폴더를 지정하세요. 아직 쓰고 있다면 폴더를 마우스 오른쪽 → "
                "'Synology Drive → 로컬에 유지(고정)' 또는 OneDrive '항상 이 장치에 유지'를 누르세요")
BLOCKED_REASON = "윈도우가 파일 열기를 막았습니다. 문서보안(DRM)·백신 프로그램이 막았거나, 클라우드에만 있는 파일일 수 있습니다"
# 윈도우 클라우드 파일 오류 번호 (파이썬은 모르는 번호를 모두 'Invalid argument' 로 보여 줌)
CLOUD_PROVIDER_OFF = 362          # ERROR_CLOUD_FILE_PROVIDER_NOT_RUNNING
SYNC_REASON = ("동기화 프로그램(Synology Drive·OneDrive 등)의 파일인데 동기화 중이거나 멈춰 있어 열 수 없습니다. "
               "동기화 프로그램 상태를 확인하세요. 그 프로그램을 지웠다면 이름만 남은 껍데기일 수 있어, "
               "NAS·클라우드에서 파일을 다시 받아 다른 폴더에 두는 것이 확실합니다")
PROVIDER_OFF_REASON = ("동기화 프로그램(Synology Drive·OneDrive 등)이 꺼져 있거나 지워져서 파일을 열 수 없습니다. "
                       "동기화 프로그램을 지웠다면 이 파일들은 이름만 남은 껍데기일 수 있습니다 (내용은 NAS·클라우드에 있음). "
                       "동기화 프로그램을 다시 설치해 내려받거나, NAS 에서 파일을 다시 받아 다른 폴더에 두세요")


def read_problem(path, err):
    """OSError → 선생님이 알아들을 수 있는 까닭."""
    try:
        attrs = getattr(os.stat(path), "st_file_attributes", 0)
    except OSError:
        attrs = 0
    win = getattr(err, "winerror", None)
    code = f" [윈도우 오류 {win}]" if win else ""
    if win == CLOUD_PROVIDER_OFF:
        return PROVIDER_OFF_REASON + code
    if win and 358 <= win <= 399:   # ERROR_CLOUD_FILE_* (동기화 중·잠김·요청 취소 등)
        return SYNC_REASON + code
    if attrs & CLOUD_ATTRS:
        return CLOUD_REASON + code
    if err.errno in (1, 13, 22):  # EPERM · EACCES · EINVAL ('Invalid argument')
        return BLOCKED_REASON + code
    return f"읽기 실패 ({err.strerror or err}){code}"


def long_path(path):
    """윈도우는 260자가 넘는 경로를 그냥은 못 여는 경우가 있어 \\\\?\\ 를 붙임."""
    if os.name == "nt":
        full = str(Path(path).resolve())
        if len(full) >= 250 and not full.startswith("\\\\?\\"):
            return Path("\\\\?\\" + full)
    return Path(path)


def item_file(config, file_path):
    """항목의 원본 파일 (에듀파인·받은 파일 폴더 안에 아직 있는 문서만), 없으면 None. 화면·텔레그램 공용."""
    fp = file_path or ""
    if not fp or "#" in fp:
        return None
    roots = [config[k] for k in SOURCES.values() if config[k]]
    try:
        p = Path(fp).resolve()
        allowed = [os.path.normcase(str(Path(r).resolve())).rstrip("\\/") + os.sep for r in roots]
        name = os.path.normcase(str(p))
        if p.is_file() and any(name.startswith(a) for a in allowed):
            return p
    except OSError:
        pass
    return None


# 에듀파인에서 내려받은 파일 이름 앞의 '(천상고등학교-13551 (본문) 울산광역시교육청 중등교육과) '
P_EDU_PREFIX = re.compile(r"^\([^()]*\((?:본문|붙임\d*)\)[^)]*\)\s*")


def edufine_title(text, stem):
    """공문 제목: 본문의 '제목' 줄, 없으면 파일 이름 (앞의 문서번호·기관 묶음은 뗌)."""
    return extract.subject_line(text) or P_EDU_PREFIX.sub("", stem).strip() or stem


def retitle_edufine(db):
    """예전에 파일 이름을 그대로 제목으로 넣은 공문을 한 번 다시 이름 붙임 (손으로 고친 제목은 그대로)."""
    if db.get_meta("edufine_title") == 1:
        return 0
    n = 0
    for r in db.query("SELECT id, title, body, file_path FROM items WHERE source = 'edufine' AND file_path IS NOT NULL"):
        stem = Path(r["file_path"]).stem
        if r["title"] != stem[:len(r["title"])] or not r["title"]:
            continue
        new = edufine_title(r["body"] or "", stem)[:200]
        if new != r["title"]:
            db.execute("UPDATE items SET title = ? WHERE id = ?", (new, r["id"]))
            n += 1
    db.set_meta("edufine_title", 1)
    return n


def drop_admin_events(db):
    """예전에 시행·접수 날짜나 표어('오늘이 행복한 …')에서 잘못 뽑은 일정 후보를 한 번 지움.
    달력에 넣지 않은 항목의, 규칙으로 찾았고 손대지 않은(내용이 원문 그대로인) 일정만."""
    if db.get_meta("admin_events") == 1:
        return 0
    rows = db.query("SELECT e.id, e.date, e.text, i.body, i.received_at FROM events e JOIN items i ON i.id = e.item_id "
                    "WHERE i.registered = 0 AND e.source IS NULL")
    gone = []
    for r in rows:
        body = r["body"] or ""
        if not r["text"] or r["text"] not in body:
            continue  # 손으로 고친 일정
        try:
            ref = datetime.strptime(r["received_at"][:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        # 지금 규칙으로는 그 줄에서 일정이 하나도 안 나옴 (날짜만 고친 일정은 남김)
        if extract.event_context(body, r["date"], ref) is None and not extract.extract_events(r["text"], ref):
            gone.append(r["id"])
    for eid in gone:
        db.execute("DELETE FROM events WHERE id = ?", (eid,))
    db.set_meta("admin_events", 1)
    return len(gone)


def scan_folder(db, source, folder, stats, retry_failed=False):
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
            path = Path(root) / name
            try:  # 살펴보는 사이 지워졌거나 열 수 없는 파일은 건너뜀 (수집 전체가 멈추지 않게)
                files.append((path.stat(), path))
            except OSError:
                continue
    files.sort(key=lambda f: f[0].st_mtime)
    # 파일마다 묻지 않고 한 번에 읽어 둠
    seen_rows = {r["path"]: r for r in db.query("SELECT path, mtime, size, error FROM files_seen")}
    failed = {}
    for st, path in files[-MAX_FILES_PER_RUN:]:
        key = str(path)
        seen = seen_rows.get(key)
        if seen and seen["mtime"] == st.st_mtime and seen["size"] == st.st_size \
                and not (seen["error"] and retry_failed):
            continue
        item_id, error = None, None
        try:
            text = read_document(long_path(path))
            received = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            title = edufine_title(text, path.stem) if source == "edufine" else None
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
            # 같은 까닭으로 매번 실패하지 않게 기록해 둠 → 파일이 바뀌거나 '지금 수집'을 누르면 다시 시도
            error = read_problem(path, e)
            failed.setdefault(error, []).append(path.name)
        db.execute("INSERT INTO files_seen(path, mtime, size, item_id, error) VALUES(?,?,?,?,?) "
                   "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, size=excluded.size, "
                   "item_id=COALESCE(excluded.item_id, files_seen.item_id), error=excluded.error",
                   (key, st.st_mtime, st.st_size, item_id, error))
    # 읽지 못한 파일은 까닭별로 한 줄씩 (19개면 19줄 대신 1줄)
    for reason, names in failed.items():
        if len(names) <= 2:
            stats["errors"] += [f"{n}: {reason}" for n in names]
        else:
            stats["errors"].append(f"{SOURCE_NAMES[source]} 파일 {len(names)}개를 읽지 못했습니다 — {reason} "
                                   f"(예: {', '.join(names[:3])} …)")


def repair_comp_items(db, path, last):
    """0.4.0 까지 압축된 쪽지({COMP}...)가 풀리지 않은 채 들어간 항목을 지우고, 그 쪽지부터 다시 읽게 함."""
    bad = db.query("SELECT id, file_path FROM items WHERE source = 'cool' AND file_path LIKE ? "
                   "AND (body LIKE '{COMP}%' OR title LIKE '{COMP}%')", (f"{path}#%",))
    if not bad:
        return last
    rowids = [int(r["file_path"].rsplit("#", 1)[1]) for r in bad if r["file_path"].rsplit("#", 1)[1].isdigit()]
    for r in bad:
        db.execute("DELETE FROM items WHERE id = ?", (r["id"],))
    db.log(f"쿨메신저: 압축된 쪽지 {len(bad)}건을 풀어서 다시 읽습니다")
    if rowids and last is not None:
        return min(last, min(rowids) - 1)
    return last


def fill_missing_images(db, path):
    """0.5.2 전에 받은 쿨메신저 쪽지에도 사진 목록을 채움 (한 번, 사진 칸이 비어 있는 것만)."""
    rows = db.query("SELECT id, file_path FROM items WHERE source = 'cool' AND images IS NULL AND file_path LIKE ?",
                    (f"{path}#%",))
    ids = {}
    for r in rows:
        tail = r["file_path"].rsplit("#", 1)[1]
        if tail.isdigit():
            ids[int(tail)] = r["id"]
    if not ids:
        return
    found = coolmsg.images_for_rowids(path, ids)
    for rowid, item_id in ids.items():
        db.execute("UPDATE items SET images = ? WHERE id = ?",
                   (json.dumps(found.get(rowid, []), ensure_ascii=False), item_id))


def squash(s):
    return re.sub(r"\s+", "", s or "")


def cool_title(title, body):
    """쿨메신저 제목은 앞 몇 글자에서 잘려 있음 ('… 표창 관련 공') → 본문 첫 줄이 그 제목으로 시작하면 첫 줄로."""
    if not title:
        return None
    first = extract.make_title(body)
    if len(squash(first)) > len(squash(title)) and squash(first).startswith(squash(title)):
        return first
    return title


CLEAN_VERSION = 2   # 0.5.10: 글자 꾸밈 태그 사이 빈칸 없애기·관련 공문 날짜 빼기·잘린 제목


def cool_text(m):
    return m["body"] + (f"\n\n(보낸 사람: {m['sender']})" if m["sender"] else "")


EVENT_RULES = 2   # 일정 찾기 규칙이 바뀌면 올림 (0.5.21: 시각이 끼인 기간 하나로, 이름표 줄은 행사명으로 / 0.5.39: '…일자' 기준일은 일정 아님)


def rescan_events(db):
    """일정 찾기 규칙이 바뀌면 예전 일정 후보를 한 번 다시 찾음.
    달력에 넣지 않았고, 일정을 하나도 손대지 않은(끝냄·AI·직접 더함 없음, 날짜가 원문에 있는) 항목만."""
    if (db.get_meta("event_rules") or 0) >= EVENT_RULES:
        return 0
    items = db.query("SELECT id, title, body, received_at FROM items WHERE registered = 0 AND source != 'school'")
    events = {}
    for e in db.query("SELECT item_id, date, end_date, time, kind, category, text, done, source FROM events "
                      "WHERE item_id IS NOT NULL ORDER BY id"):
        events.setdefault(e["item_id"], []).append(e)
    changed = 0
    for it in items:
        old = events.get(it["id"])
        body = it["body"] or ""
        if not old or any(e["done"] or e["source"] for e in old):
            continue
        try:
            ref = datetime.strptime(it["received_at"][:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        found = {x.isoformat() for line in body.splitlines() for _, _, d, end in extract.find_dates(line, ref)
                 for x in (d, end) if x}
        if any(e["date"] not in found for e in old):
            continue  # 날짜를 손으로 고친 일정이 있음
        new = extract.analyze(body, ref=ref, title=it["title"])["events"]
        shape = lambda es: sorted((e["date"], e.get("end_date") or "", e.get("time") or "", e["kind"], e["text"]) for e in es)  # None 과 글자를 섞어 비교하면 오류
        if shape(new) != shape(old):
            db.replace_events(it["id"], new)
            changed += 1
    db.set_meta("event_rules", EVENT_RULES)
    if changed:
        db.log(f"일정 후보 {changed}건을 새 규칙으로 다시 찾았습니다 (달력에 넣지 않은 것만)")
    return changed


def reclean_cool_items(db, path):
    """글 정리 방식이 바뀌면 이미 받은 쿨메신저 쪽지를 한 번 다시 정리 (일정은 달력에 넣지 않은 것만 다시 찾음)."""
    key = f"cool_clean:{path}"
    if (db.get_meta(key) or 0) >= CLEAN_VERSION:
        return
    rows = db.query("SELECT id, title, body, registered, received_at, file_path FROM items "
                    "WHERE source = 'cool' AND file_path LIKE ?", (f"{path}#%",))
    ids = {}
    for r in rows:
        tail = r["file_path"].rsplit("#", 1)[1]
        if tail.isdigit():
            ids[int(tail)] = r["id"]
    memos = coolmsg.memos_for_rowids(path, ids) if ids else {}
    by_id = {r["id"]: r for r in rows}
    changed = 0
    for rowid, item_id in ids.items():
        m, it = memos.get(rowid), by_id.get(item_id)
        if not m or not it or not m["body"]:
            continue
        text = fix_surrogates(cool_text(m)).strip()
        title = cool_title(m["title"], m["body"]) or it["title"]
        if text == it["body"] and title == it["title"]:
            continue
        ref = datetime.strptime(it["received_at"][:10], "%Y-%m-%d").date()
        a = extract.analyze(text, ref=ref, title=title)
        db.execute("UPDATE items SET title = ?, body = ?, summary = ?, doc_numbers = ?, content_hash = ? WHERE id = ?",
                   (a["title"], text, json.dumps(a["summary"], ensure_ascii=False),
                    json.dumps(a["doc_numbers"], ensure_ascii=False), content_hash(text), item_id))
        if not it["registered"]:  # 아직 달력에 넣지 않은 일정 후보만 새로 찾은 것으로
            db.replace_events(item_id, a["events"])
        changed += 1
    db.set_meta(key, CLEAN_VERSION)
    if changed:
        db.log(f"쿨메신저: 받은 쪽지 {changed}건을 읽기 쉽게 다시 정리했습니다")


def cool_me(name, path):
    """'나'의 쿨메신저 이름: 설정의 이름, 없으면 .udb 파일 이름."""
    return (name or "").strip() or Path(path).stem


def scan_cool_memo(db, name, stats, path=""):
    """쿨메신저 쪽지 기록에서 지난번 이후 새 쪽지만 가져옴 (경로를 직접 지정했으면 그 파일)."""
    try:
        path = coolmsg.resolve(name, path)
        key = f"cool_last_rowid:{path}"
        last = repair_comp_items(db, path, db.get_meta(key))
        fill_missing_images(db, path)
        try:
            reclean_cool_items(db, path)
        except coolmsg.CoolError:
            raise
        except Exception as e:  # 다시 정리하다 실패해도 새 쪽지 받기는 계속 (다음 수집 때 다시 시도)
            stats["errors"].append(f"쿨메신저: 받은 쪽지 다시 정리 실패 ({type(e).__name__}: {e})")
        _, memos, top = coolmsg.read_new(path, last, me=cool_me(name, path))
    except coolmsg.CoolError as e:
        stats["errors"].append(f"쿨메신저: {e}")
        return
    for m in memos:
        text = cool_text(m)
        received = (m["when"] or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        sent = m.get("mine")
        res = add_text_item(db, "cool_sent" if sent else "cool", text, title=cool_title(m["title"], m["body"]), received_at=received,
                            file_path=f"{path}#{m['rowid']}", images=m.get("images") or [])
        if res and not sent:   # 내가 보낸 쪽지는 새 자료 수·알림에 세지 않음
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
            run_error = None
            try:
                if self.config["coolmessenger_name"] or self.config["coolmessenger_path"]:
                    stats["sources"].append("cool")
                    scan_cool_memo(self.db, self.config["coolmessenger_name"], stats,
                                   self.config["coolmessenger_path"])
                try:
                    retitle_edufine(self.db)
                    drop_admin_events(self.db)
                    rescan_events(self.db)
                except Exception as e:  # 예전 자료 정리가 실패해도 수집은 계속
                    stats["errors"].append(f"예전 자료 정리 실패: {e}")
                for source, key in SOURCES.items():
                    folder = self.config[key]
                    if folder:
                        stats["sources"].append(source)
                        scan_folder(self.db, source, folder, stats, retry_failed=reason in ("manual", "telegram"))
            except Exception as e:  # 예상 못 한 오류: 그때까지 모은 것은 남기고 기록 (수집 기록이 '진행 중'으로 남지 않게)
                run_error = f"{type(e).__name__}: {e}"
                stats["errors"].append(f"수집 중 오류: {run_error}")
            self.db.execute("UPDATE runs SET finished_at = ?, stats = ?, error = ? WHERE id = ?",
                            (now_str(), json.dumps(stats, ensure_ascii=False), run_error, run_id))
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
    supported = cloud = local_supported = 0
    for root, dirs, names in os.walk(folder):
        for name in names:
            p = Path(root) / name
            try:
                st = p.stat()
            except OSError:
                continue
            is_cloud = bool(getattr(st, "st_file_attributes", 0) & CLOUD_ATTRS)
            cloud += is_cloud
            supported += p.suffix.lower() in SUPPORTED
            local_supported += p.suffix.lower() in SUPPORTED and not is_cloud
            ext = p.suffix.lower() or "(없음)"
            e = by_ext.setdefault(ext, {"count": 0, "bytes": 0})
            e["count"] += 1
            e["bytes"] += st.st_size
            if len(files) < limit:
                files.append({"path": str(p.relative_to(folder)), "bytes": st.st_size,
                              "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                              "kind": "클라우드에만 있음" if is_cloud else sniff(p)})
            if not is_cloud and sniff(p) == "sqlite" and len(dbs) < 10:
                dbs.append(sqlite_shape(p, folder))
    files.sort(key=lambda f: f["modified"], reverse=True)
    total = sum(e["count"] for e in by_ext.values())
    return {"folder": str(folder), "total": total, "supported": supported, "cloud_only": cloud,
            "local_supported": local_supported, "cloud_hint": CLOUD_REASON if cloud else None,
            "extensions": by_ext, "recent_files": files[:60], "databases": dbs,
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
