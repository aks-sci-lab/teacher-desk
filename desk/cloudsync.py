"""여러 PC 이어 쓰기: 선생님 본인 구글 드라이브에 암호화한 자료를 올리고 받기 (0.5.96, 기본 끔).

앱은 지금처럼 각 PC 에서 돌고, 드라이브는 '창고'입니다. 드라이브의 숨은 앱 전용 폴더(gdrive.py)에
- desk-data.enc  : data/desk.db 의 그때 모습을 통째로 암호화한 것 (cloudcrypt.py, 복구 키는 이 PC 에만)
- desk-state.json: 몇 번째 판인지(rev)·올린 시각·올린 PC 이름·앱 버전·암호 파일 해시 (학생 자료 없음)
- desk-lock.json : 지금 쓰는 PC (이름·마지막으로 살아 있던 시각·마지막으로 선생님이 쓴 시각)

흐름
- 켤 때(desk.py, 앱이 자료를 쓰기 전): 잠금을 보고 → 비었거나 내 것이거나 오래됐으면 가져오고 →
  드라이브 판이 내가 마지막으로 맞춘 판보다 새것이면 받아서 바꿈. 이 PC 에도 올리지 못한 변경이 있으면
  '충돌'로 두고 화면에서 고르게 함(바꾸기 전 것은 data/cloud-backups 에 남김).
- 쓰는 동안: 5분마다 잠금에 살아 있음 표시, 바뀐 것이 있으면 10분마다 올림. 끌 때 마지막으로 올리고 잠금을 풂.
- 다른 PC 가 쓰는 중이면 '읽기 전용': 화면의 저장·수집·텔레그램·수업 알림·구글 캘린더 올리기를 멈춤
  (paused()). [이 PC 에서 쓰기]를 누르면 잠금을 가져오고 드라이브 최신을 받음. 읽기 전용 동안 이 PC 에서
  바뀐 것(기록 등)은 버립니다.
- 켜 두고 자리를 뜬 PC: 선생님이 30분 넘게 쓰지 않았거나 20분 넘게 신호가 없으면 다른 PC 가 켤 때 잠금을
  가져가고, 원래 PC 는 다음 확인 때 읽기 전용이 됩니다.
- 인터넷이 안 되면 그냥 이 PC 에서 계속 쓰고, 나중에 다시 시도합니다.
"""

import hashlib
import json
import re
import os
import socket
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime, timedelta

from . import VERSION, cloudcrypt, gdrive

DATA, STATE, LOCK = "desk-data.enc", "desk-state.json", "desk-lock.json"
HEARTBEAT = 5 * 60        # 잠금에 살아 있음 표시
UPLOAD_EVERY = 10 * 60    # 바뀐 것이 있으면 올리는 간격
RETRY = 5 * 60            # 인터넷 오류 뒤 다시 시도
STALE = 20 * 60           # 이만큼 신호가 없으면 꺼진 PC 로 봄
IDLE = 30 * 60            # 이만큼 선생님이 안 썼으면 켜 두고 자리를 뜬 PC 로 봄
LOCAL_KEEP = 5            # 바꾸기 전 이 PC 자료 보관 개수
# 드라이브 백업 (0.5.97): 날짜마다 그날 마지막 자료 하나. 최근 14일은 날마다, 그보다 오래된 것은 달마다 하나(최근 12달)
BACKUP_RE = re.compile(r"^backup-(\d{4}-\d{2}-\d{2})(?:-([a-z]+))?\.enc$")
KEEP_DAYS, KEEP_MONTHS = 14, 12
TABLES = ("students", "passes", "absences", "items", "events", "todos", "class_counsel", "class_data")

_paused = threading.Event()   # 읽기 전용·충돌 중: 화면 저장·수집·텔레그램 등이 멈춤


def paused():
    """다른 PC 가 쓰는 중이거나 충돌을 고르기 전이면 True — 자료를 바꾸는 일을 하지 않음."""
    return _paused.is_set()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _ver(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


class Sync:
    def __init__(self, db, config, drive=None):
        self.db, self.config = db, config
        self.drive = drive or gdrive.Drive(config)
        self.path = config.data_dir / "cloud-sync.json"
        self.state = self._load()
        self.lock = threading.RLock()
        self.mode = "off"          # off · ok · readonly · conflict · error · starting
        self.message = ""
        self.holder = None         # 읽기 전용일 때 쓰는 PC {pc_name, active_at}
        self.remote = None         # 드라이브의 desk-state.json
        self.files = {}
        self.baseline = self._changes()
        self.last_beat = self.last_up = self.last_try = 0.0
        self.active_at = time.time()
        self.login = None          # 진행 중인 구글 로그인 (gdrive.Login)
        self.reload_rev = 0        # 자료를 통째로 바꾸면 올라감 → 화면이 새로 고침
        self.on_mode = None        # 모드가 바뀌면 부름 (desk.py 가 로그 등에 씀)
        self.stop_event = threading.Event()
        self.thread = None

    # ---- 이 PC 의 상태 파일 (data/cloud-sync.json, 학생 자료 없음) ----
    def _load(self):
        try:
            s = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            s = {}
        s.setdefault("pc_id", uuid.uuid4().hex)
        s.setdefault("base_rev", None)     # 마지막으로 드라이브와 맞춘 판
        s.setdefault("dirty", False)       # 지난번에 올리지 못한 이 PC 변경이 있음
        return s

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def pc_name(self):
        return (self.config["gdrive_pc_name"] or socket.gethostname() or "이 PC")[:40]

    # ---- 켜기·끄기 조건 ----
    def ready(self):
        """켜져 있고 로그인·복구 키·암호 도구가 다 있으면 None, 아니면 이유."""
        if not self.config["gdrive_enabled"]:
            return "꺼짐"
        if not cloudcrypt.available():
            return "암호화 도구(cryptography)가 없습니다 — 윈도우용 ZIP 설치본에서 쓰세요"
        if not gdrive.client_id(self.config):
            return "구글 로그인 정보(클라이언트 ID)가 없습니다"
        if not self.config["gdrive_token"]:
            return "구글 로그인이 필요합니다"
        if not self.config["gdrive_passphrase"]:
            return "복구 키(암호문)가 필요합니다"
        return None

    def _set_mode(self, mode, message=""):
        changed = mode != self.mode
        self.mode, self.message = mode, message
        if mode in ("readonly", "conflict", "starting"):
            _paused.set()
        else:
            _paused.clear()
        if changed and self.on_mode:
            try:
                self.on_mode(mode, message)
            except Exception:
                pass

    # ---- 바뀐 것 알기 ----
    def _changes(self):
        with self.db.lock:
            return self.db.conn.total_changes

    def dirty(self):
        """이 PC 에 드라이브에 없는 변경이 있는지 (읽기 전용 동안의 변경은 세지 않음)."""
        return bool(self.state.get("dirty")) or self._changes() != self.baseline

    def _clean(self, rev):
        self.state.update(base_rev=rev, dirty=False)
        self._save()
        self.baseline = self._changes()

    def touch(self):
        """선생님이 이 PC 화면을 쓰는 중 (server.py 가 X-Desk-Active 요청마다 부름)."""
        self.active_at = time.time()

    # ---- 드라이브 읽기 ----
    def _refresh(self):
        self.files = self.drive.files()
        self.remote = self._json(STATE)
        return self._json(LOCK)

    def _json(self, name):
        f = self.files.get(name)
        if not f:
            return None
        try:
            v = json.loads(self.drive.read(f["id"]).decode("utf-8"))
            return v if isinstance(v, dict) else None
        except (ValueError, UnicodeDecodeError):
            return None

    def _put(self, name, data):
        """있으면 내용을 바꾸고 없으면 새로 만듦 (만든 파일 번호를 기억)."""
        f = self.files.get(name)
        res = self.drive.write(name, data, f["id"] if f else None)
        if not f and isinstance(res, dict) and res.get("id"):
            self.files[name] = {"id": res["id"], "name": name}

    def _write_json(self, name, value):
        self._put(name, json.dumps(value, ensure_ascii=False).encode("utf-8"))

    # ---- 잠금 ----
    def _lock_free(self, lock, now=None):
        """비었거나·내 것이거나·꺼진 PC 이거나·켜 두고 자리를 뜬 PC 이면 True."""
        if not lock or lock.get("released"):
            return True
        if lock.get("pc_id") == self.state["pc_id"]:
            return True
        now = now or time.time()
        beat, active = lock.get("heartbeat_ts") or 0, lock.get("active_ts") or 0
        return now - beat > STALE or now - active > IDLE

    def _write_lock(self, released=False):
        now = time.time()
        self._write_json(LOCK, {"pc_id": self.state["pc_id"], "pc_name": self.pc_name(), "released": released,
                                "heartbeat_ts": now, "heartbeat": _now(), "active_ts": self.active_at,
                                "active_at": datetime.fromtimestamp(self.active_at).strftime("%Y-%m-%d %H:%M")})
        self.last_beat = now

    # ---- 자료 옮기기 ----
    def _snapshot(self):
        tmp = self.config.data_dir / "cloud-snapshot.tmp"
        if tmp.exists():
            tmp.unlink()
        with self.db.lock:
            out = sqlite3.connect(str(tmp))
            try:
                self.db.conn.backup(out)
            finally:
                out.close()
            changes = self.db.conn.total_changes
        try:
            return tmp.read_bytes(), changes
        finally:
            tmp.unlink()

    def _keep_local(self, label):
        """바꾸기 전 이 PC 자료를 data/cloud-backups 에 남김 (최근 몇 개만)."""
        folder = self.config.data_dir / "cloud-backups"
        folder.mkdir(exist_ok=True)
        data, _ = self._snapshot()
        (folder / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{label}.db").write_bytes(data)
        old = sorted(folder.glob("*.db"))
        for p in old[:-LOCAL_KEEP]:
            p.unlink()

    # ---- 드라이브 백업 (0.5.97) ----
    def _backup_previous(self):
        """올리기 전: 드라이브의 지금 자료가 다른 날 것이면 그날 백업으로 복사(그날의 마지막 모습)."""
        f, r = self.files.get(DATA), self.remote or {}
        day = (r.get("at") or "")[:10]
        if not f or not re.match(r"^\d{4}-\d{2}-\d{2}$", day) or day == date.today().isoformat():
            return
        name = f"backup-{day}.enc"
        if name not in self.files:
            self._copy(f["id"], name)
            self.prune_backups()

    def _copy(self, file_id, name):
        res = self.drive.copy(file_id, name)
        if isinstance(res, dict) and res.get("id"):
            self.files[name] = {"id": res["id"], "name": name}

    def backups(self):
        """[{name, date, kind, size}] 새것부터 (드라이브에서 새로 읽음)."""
        self.files = self.drive.files()
        return sorted(self.backups_cached(), key=lambda b: (b["date"], b["kind"]), reverse=True)

    def prune_backups(self, today=None):
        """최근 14일은 모두, 그보다 오래된 것은 달마다 가장 늦은 것 하나(최근 12달), 나머지는 지움."""
        today = today or date.today()
        keep_from = (today - timedelta(days=KEEP_DAYS - 1)).isoformat()
        oldest_month = (today.replace(day=1) - timedelta(days=31 * (KEEP_MONTHS - 1))).strftime("%Y-%m")
        months, removed = set(), []
        for b in sorted(self.backups_cached(), key=lambda b: (b["date"], b["kind"]), reverse=True):
            if b["date"] >= keep_from:
                continue
            month = b["date"][:7]
            if month >= oldest_month and month not in months:
                months.add(month)
                continue
            self.drive.delete(self.files[b["name"]]["id"])
            self.files.pop(b["name"], None)
            removed.append(b["name"])
        return removed

    def backups_cached(self):
        out = []
        for name, f in self.files.items():
            m = BACKUP_RE.match(name)
            if m:
                out.append({"name": name, "date": m.group(1), "kind": m.group(2) or "", "size": int(f.get("size") or 0)})
        return out

    def backup_now(self):
        """[지금 백업]: 드라이브의 지금 자료(올린 뒤)를 오늘 날짜 백업으로 (오늘 것은 바꿈)."""
        with self.lock:
            if self.mode != "ok":
                raise gdrive.DriveError("이 PC 에서 이어 쓰는 중일 때만 백업할 수 있습니다")
            self.sync_now()
            if self.mode != "ok":
                raise gdrive.DriveError(self.message or "드라이브에 올리지 못했습니다")
            name = f"backup-{date.today().isoformat()}.enc"
            old = self.files.pop(name, None)
            if old:
                self.drive.delete(old["id"])
            self._copy(self.files[DATA]["id"], name)
            self.prune_backups()
            return name

    def restore(self, name):
        """고른 백업으로 되살림: 이 PC 자료를 바꾸고(바꾸기 전 것은 이 PC 와 드라이브에 보관) 새 판으로 올림."""
        with self.lock:
            if self.mode != "ok":
                raise gdrive.DriveError("이 PC 에서 이어 쓰는 중일 때만 되살릴 수 있습니다 (읽기 전용이면 먼저 [이 PC 에서 쓰기])")
            if not BACKUP_RE.match(name or ""):
                raise gdrive.DriveError("백업 이름이 잘못되었습니다")
            lock = self._refresh()
            if not self._mine(lock):
                return self.connect()
            f = self.files.get(name)
            if not f:
                raise gdrive.DriveError("그 백업을 드라이브에서 찾지 못했습니다")
            data = cloudcrypt.decrypt(self.drive.read(f["id"]), self.config["gdrive_passphrase"])
            # 되살리기 전 지금 드라이브 자료도 남김 (되돌릴 수 있게)
            cur = self.files.get(DATA)
            if cur:
                keep = f"backup-{date.today().isoformat()}-restore.enc"
                if keep in self.files:
                    self.drive.delete(self.files.pop(keep)["id"])
                self._copy(cur["id"], keep)
            self._replace(data, "before-restore")
            self.upload()
            self._write_lock()
            return self.mode

    def upload(self):
        """이 PC 자료를 새 판으로 올림 (잠금이 내 것일 때만 부름)."""
        self._backup_previous()
        data, changes = self._snapshot()
        blob = cloudcrypt.encrypt(data, self.config["gdrive_passphrase"])
        rev = max(int((self.remote or {}).get("rev") or 0), int(self.state.get("base_rev") or 0)) + 1
        self._put(DATA, blob)
        self.remote = {"format": 1, "rev": rev, "at": _now(), "pc_id": self.state["pc_id"], "pc_name": self.pc_name(),
                       "app_version": VERSION, "size": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
        self._write_json(STATE, self.remote)
        self.state.update(base_rev=rev, dirty=False, last_up=_now())
        self._save()
        self.baseline = changes   # 스냅숏 뒤에 생긴 변경은 다음에 올림
        self.last_up = time.time()
        return rev

    def download(self, keep_label="before-download"):
        """드라이브 판으로 이 PC 자료를 통째로 바꿈 (바꾸기 전 것은 이 PC 에 남김)."""
        r = self.remote or {}
        if _ver(r.get("app_version")) > _ver(VERSION):
            raise gdrive.DriveError(f"드라이브 자료는 더 새 버전({r.get('app_version')})이 만든 것입니다. 업무 데스크를 먼저 업데이트하세요")
        f = self.files.get(DATA)
        if not f:
            raise gdrive.DriveError("드라이브에 자료 파일이 없습니다")
        blob = self.drive.read(f["id"])
        if r.get("sha256") and hashlib.sha256(blob).hexdigest() != r["sha256"]:
            raise gdrive.DriveError("드라이브 자료가 아직 다 올라가지 않았거나 손상되었습니다. 잠시 뒤 다시 시도하세요", offline=True)
        data = cloudcrypt.decrypt(blob, self.config["gdrive_passphrase"])
        self._replace(data, keep_label)
        self._clean(int(r.get("rev") or 0))
        self.state["last_down"] = _now()
        self._save()

    def _replace(self, data, keep_label):
        if not data.startswith(b"SQLite format 3\x00"):
            raise cloudcrypt.CryptError("풀어 낸 자료가 업무 데스크 자료가 아닙니다")
        tmp = self.config.data_dir / "cloud-incoming.tmp"
        tmp.write_bytes(data)
        try:
            src = sqlite3.connect(str(tmp))
            try:
                if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise cloudcrypt.CryptError("드라이브 자료가 손상되었습니다")
                if keep_label:
                    self._keep_local(keep_label)
                with self.db.lock:
                    src.backup(self.db.conn)
                    self.db.refresh_schema()   # 예전 버전 자료면 새 칸을 더함
            finally:
                src.close()
        finally:
            tmp.unlink()
        self.reload_rev += 1

    def local_empty(self):
        """이 PC 에 쓸 만한 자료가 없는지 (새로 설치한 PC → 묻지 않고 드라이브 것을 받음)."""
        for t in TABLES:
            try:
                if self.db.one(f"SELECT 1 AS x FROM {t} LIMIT 1"):
                    return False
            except sqlite3.Error:
                continue
        return True

    # ---- 켤 때·다시 시도할 때 ----
    def connect(self, force=False):
        """잠금을 보고 받기·올리기·충돌·읽기 전용을 정함. force: 다른 PC 가 쓰는 중이어도 가져옴."""
        with self.lock:
            why = self.ready()
            if why:
                self._set_mode("off", why)
                return self.mode
            self.last_try = time.time()
            try:
                lock = self._refresh()
                if not force and not self._lock_free(lock):
                    if self.mode != "readonly":   # 읽기 전용이 되기 전의 내 변경은 남겨 둠 (켜는 중 기록은 빼고)
                        self.state["dirty"] = bool(self.state.get("dirty")) if self.mode == "starting" else self.dirty()
                        self._save()
                    self.baseline = self._changes()
                    self.holder = {"pc_name": lock.get("pc_name") or "다른 PC", "active_at": lock.get("active_at") or "",
                                   "heartbeat": lock.get("heartbeat") or ""}
                    self._set_mode("readonly", f"{self.holder['pc_name']} 에서 쓰는 중 (마지막 사용 {self.holder['active_at']})")
                    self._view_latest()
                    return self.mode
                was_readonly = self.mode == "readonly"
                self._write_lock()
                self.holder = None
                rrev = int((self.remote or {}).get("rev") or 0)
                base = self.state.get("base_rev")
                own_dirty = bool(self.state.get("dirty")) if was_readonly or self.mode == "starting" else self.dirty()
                if not self.remote or DATA not in self.files:
                    self.upload()                                   # 드라이브가 비었음 → 이 PC 것이 첫 판
                elif base is None and not self.local_empty():
                    self._set_mode("conflict", "이 PC 에도 자료가 있고 드라이브에도 자료가 있습니다. 어느 것을 쓸지 고르세요")
                    return self.mode
                elif base is None or rrev > int(base):
                    if own_dirty:
                        self._set_mode("conflict", f"이 PC 에 올리지 못한 변경이 있는데, 드라이브에 더 새 판({(self.remote or {}).get('pc_name', '')} {(self.remote or {}).get('at', '')})이 있습니다. 어느 것을 쓸지 고르세요")
                        return self.mode
                    self.download()
                elif was_readonly:
                    self.download(keep_label=None)                  # 읽기 전용 동안 이 PC 에서 바뀐 것(기록 등)은 버림
                elif own_dirty:
                    self.upload()
                else:
                    self._clean(rrev)
                self._set_mode("ok", "")
            except gdrive.DriveError as e:
                if e.auth:
                    self._set_mode("error", str(e))
                else:
                    self._set_mode("error", f"{e} — 이 PC 에서 계속 쓰고 나중에 다시 시도합니다")
            except cloudcrypt.CryptError as e:
                self._set_mode("error", str(e))
            except Exception as e:   # 예상 못 한 오류에도 '확인 중'(쓰기 막힘)에 갇히지 않게 — 이 PC 에서 계속 씀
                self._set_mode("error", f"동기화 오류({type(e).__name__}) — 이 PC 에서 계속 쓰고 나중에 다시 시도합니다")
            return self.mode

    def _view_latest(self):
        """읽기 전용: 이 PC 에 내 변경이 없으면 드라이브 최신을 받아서 보여 줌."""
        if self.state.get("dirty") or not self.remote or DATA not in self.files:
            return
        if int(self.remote.get("rev") or 0) != int(self.state.get("base_rev") or -1):
            try:
                self.download(keep_label=None)
            except (gdrive.DriveError, cloudcrypt.CryptError) as e:
                self.message += f" · 최신 자료를 받지 못함: {e}"

    def _connect_safe(self):
        try:
            self.connect()
        finally:
            if self.mode == "starting":   # 어떤 경우에도 켜는 중에 갇히지 않게
                self._set_mode("error", "드라이브 확인을 마치지 못했습니다 — 이 PC 에서 계속 쓰고 나중에 다시 시도합니다")

    def startup(self, timeout=25):
        """desk.py 가 앱이 자료를 쓰기 전에 부름. 늦어지면 기다리지 않고 켬(뒤에서 이어 함)."""
        if self.ready():
            self._set_mode("off", self.ready())
            return self.mode
        self._set_mode("starting", "드라이브에서 최신 자료를 확인하는 중…")
        t = threading.Thread(target=self._connect_safe, name="cloud-start", daemon=True)
        t.start()
        t.join(timeout)
        return self.mode

    # ---- 화면에서 고르기 ----
    def resolve(self, choice):
        """충돌: drive(드라이브 것 쓰기, 이 PC 것은 보관) · local(이 PC 것을 올림, 드라이브 것은 이 PC 에 보관)."""
        with self.lock:
            if self.mode != "conflict":
                raise gdrive.DriveError("고를 충돌이 없습니다")
            self._refresh()
            self._write_lock()
            if choice == "drive":
                self.download(keep_label="before-drive")
            else:
                f = self.files.get(DATA)
                if f:   # 덮어쓰기 전 드라이브 것도 이 PC 에 (암호화된 그대로) 남김
                    folder = self.config.data_dir / "cloud-backups"
                    folder.mkdir(exist_ok=True)
                    (folder / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-drive-before-local.enc").write_bytes(self.drive.read(f["id"]))
                self.upload()
            self._set_mode("ok", "")
            return self.mode

    def take_over(self):
        """읽기 전용 → 이 PC 에서 쓰기 (다른 PC 는 다음 확인 때 읽기 전용이 됨)."""
        self.touch()
        return self.connect(force=True)

    def sync_now(self):
        with self.lock:
            if self.mode != "ok":
                return self.connect()
            try:
                lock = self._refresh()
                if not self._mine(lock):
                    return self.connect()
                self.upload()
                self._write_lock()
            except gdrive.DriveError as e:
                self._set_mode("error", f"{e} — 나중에 다시 시도합니다")
            return self.mode

    def _mine(self, lock):
        return bool(lock) and lock.get("pc_id") == self.state["pc_id"] and not lock.get("released")

    # ---- 뒤에서 ----
    def tick(self, now=None):
        now = now or time.time()
        with self.lock:
            if self.ready():
                if self.mode != "off":
                    self._set_mode("off", self.ready())
                return
            if self.mode in ("off", "error", "starting") and now - self.last_try >= (RETRY if self.mode == "error" else 0):
                self.connect()
                return
            if self.mode == "readonly" and now - self.last_try >= HEARTBEAT:
                self.connect()   # 잠금이 풀렸거나 자리를 뜬 PC 면 가져옴, 아니면 최신을 보여 줌
                return
            if self.mode != "ok":
                return
            try:
                if now - self.last_beat >= HEARTBEAT or (self.dirty() and now - self.last_up >= UPLOAD_EVERY):
                    lock = self._refresh()
                    if not self._mine(lock):   # 다른 PC 가 가져감 → 읽기 전용
                        self.state["dirty"] = self.dirty()
                        self._save()
                        self.connect()
                        return
                    if self.dirty() and now - self.last_up >= UPLOAD_EVERY:
                        self.upload()
                    self._write_lock()
            except gdrive.DriveError as e:
                self._set_mode("error", f"{e} — 이 PC 에서 계속 쓰고 나중에 다시 시도합니다")
            except cloudcrypt.CryptError as e:
                self._set_mode("error", str(e))

    def loop(self):
        while not self.stop_event.wait(60):
            try:
                self.tick()
            except Exception as e:   # 동기화 문제로 앱이 멈추지 않게
                self.message = f"동기화 오류: {e}"

    def start(self):
        self.thread = threading.Thread(target=self.loop, name="cloud-sync", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def shutdown(self):
        """끌 때 (desk.py, 마지막 기록을 쓴 뒤): 바뀐 것을 올리고 잠금을 풂. 못 올리면 다음에 올림."""
        self.stop()
        with self.lock:
            if self.mode != "ok":
                if self.mode in ("error", "starting"):
                    self.state["dirty"] = self.dirty()
                    self._save()
                return
            try:
                lock = self._refresh()
                if not self._mine(lock):
                    self.state["dirty"] = self.dirty()
                    self._save()
                    return
                if self.dirty():
                    self.upload()
                self._write_lock(released=True)
            except (gdrive.DriveError, cloudcrypt.CryptError):
                self.state["dirty"] = True
                self._save()

    # ---- 화면에 보일 상태 ----
    def status(self):
        r = self.remote or {}
        return {
            "enabled": bool(self.config["gdrive_enabled"]), "mode": self.mode, "message": self.message,
            "crypto": cloudcrypt.available(), "client": bool(gdrive.client_id(self.config)),
            "logged_in": bool(self.config["gdrive_token"]), "key_set": bool(self.config["gdrive_passphrase"]),
            "pc_name": self.pc_name(), "holder": self.holder, "last_up": self.state.get("last_up"),
            "last_down": self.state.get("last_down"), "remote_at": r.get("at"), "remote_pc": r.get("pc_name"),
            "dirty": self.mode == "ok" and self.dirty(), "reload": self.reload_rev,
        }

    # ---- 복구 키 ----
    def set_key(self, passphrase):
        """복구 키 저장. 드라이브에 이미 자료가 있으면 그 키로 풀리는지 먼저 확인."""
        p = cloudcrypt.check_passphrase(passphrase)
        if not cloudcrypt.available():
            raise cloudcrypt.CryptError("암호화 도구(cryptography)가 없습니다 — 윈도우용 ZIP 설치본에서 쓰세요")
        if self.config["gdrive_token"]:
            self._refresh()
            f = self.files.get(DATA)
            if f:
                cloudcrypt.decrypt(self.drive.read(f["id"]), p)   # 틀리면 CryptError
        self.config.set_value("gdrive_passphrase", p)
        return True

