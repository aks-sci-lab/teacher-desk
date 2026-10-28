"""로컬 웹 서버: 화면(web/)과 자료(API)를 이 PC 안(127.0.0.1)에서만 제공합니다.

보안
- 127.0.0.1 에만 열려 있어 다른 컴퓨터·폰에서는 접속할 수 없습니다.
- 다른 웹사이트가 몰래 이 서버를 부르지 못하게, 화면에 심은 토큰과 Host·Origin 을 확인합니다.
"""

import json
import mimetypes
import os
import re
import secrets
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import url2pathname

import base64
import binascii

from . import VERSION, ai, coolmsg, extract, neis, picker, queries, schoolcal
from .collect import SOURCES, add_text_item, diagnose
from .config import APP_DIR
from .db import now_str
from .ics import to_ics
from .scheduler import next_slot

WEB_DIR = APP_DIR / "web"
# 저장소에서는 teacher-desk/pass, 윈도우용 ZIP 에서는 TeacherDesk/pass (teacher-desk 옆)
PASS_DIR = APP_DIR / "pass" if (APP_DIR / "pass").is_dir() else APP_DIR.parent / "pass"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
CATEGORIES = set(extract.CATEGORIES)
MAX_BODY = 20 * 1024 * 1024  # 학사일정 파일 올리기(base64) 때문에 넉넉히

CSP = ("default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; "
       # 쿨메신저 사진·스티커가 인터넷 주소일 수 있어 http(s) 그림은 허용 (스크립트·연결은 이 PC 만)
       "img-src 'self' data: blob: http: https:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def need(cond, message, status=400):
    if not cond:
        raise ApiError(status, message)


def check_date(v, name="날짜"):
    need(isinstance(v, str) and DATE_RE.match(v), f"{name} 형식이 잘못되었습니다 (예: 2026-11-03)")
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ApiError(400, f"{name}가 달력에 없는 날짜입니다")
    return v


def check_time(v, name="시각"):
    if v in (None, ""):
        return None
    need(isinstance(v, str) and TIME_RE.match(v), f"{name} 형식이 잘못되었습니다 (예: 13:20)")
    return v


def text_field(v, name, limit, required=False):
    if v is None:
        v = ""
    need(isinstance(v, str), f"{name}은(는) 글자여야 합니다")
    v = v.strip()
    need(v or not required, f"{name}을(를) 입력하세요")
    need(len(v) <= limit, f"{name}이(가) 너무 깁니다 ({limit}자 이하)")
    return v


def parse_roster(text):
    """엑셀에서 복사한 '번호  이름' 줄들 → [{number, name}]. 번호가 없으면 순서대로."""
    rows, auto = [], 0
    for line in text.splitlines():
        cells = [c.strip() for c in re.split(r"[\t,]+|\s{2,}", line.strip()) if c.strip()]
        if len(cells) == 1 and " " in cells[0]:
            cells = cells[0].split()
        if not cells:
            continue
        number = next((int(c) for c in cells if c.isdigit() and int(c) < 1000), None)
        name = next((c for c in cells if re.search(r"[가-힣A-Za-z]", c)
                     and c not in ("번호", "이름", "성명")), None)
        if not name:
            continue
        auto += 1
        rows.append({"number": number if number is not None else auto, "name": name[:20]})
    return rows


class App:
    """서버가 쓰는 부품 묶음."""

    def __init__(self, db, config, collector, bot):
        self.db, self.config, self.collector, self.bot = db, config, collector, bot
        self.token = secrets.token_urlsafe(24)
        self.watcher = None
        self.ai_job = ai.Job(db, config)   # AI 요약은 뒤에서 하나씩


def make_handler(app):
    port = app.config["port"]
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "TeacherDesk"

        def log_message(self, fmt, *args):  # 화면을 어지럽히지 않게 기본 접속 기록은 끔
            pass

        # ---- 공통 ----
        def guard(self):
            host = self.headers.get("Host", "")
            if host not in allowed_hosts:
                raise ApiError(403, "허용되지 않은 주소입니다")
            origin = self.headers.get("Origin")
            if origin and origin not in allowed_origins:
                raise ApiError(403, "다른 사이트의 요청은 받지 않습니다")

        def send_json(self, status, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_bytes(self, status, body, ctype, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def read_json(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ApiError(400, "Content-Length 가 잘못되었습니다")
            need(length >= 0, "Content-Length 가 잘못되었습니다")  # 음수면 read(-1) 이 연결이 끊길 때까지 기다림
            need(length <= MAX_BODY, "보낸 자료가 너무 큽니다", 413)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ApiError(400, "JSON 형식이 아닙니다")
            need(isinstance(data, dict), "JSON 객체여야 합니다")
            return data

        def dispatch(self, method):
            try:
                self.guard()
                url = urlparse(self.path)
                if url.path.startswith("/api/"):
                    if not secrets.compare_digest(self.headers.get("X-Desk-Token", ""), app.token):
                        raise ApiError(401, "화면을 새로 고침하세요 (서버가 다시 시작되었습니다)")
                    q = {k: v[-1] for k, v in parse_qs(url.query).items()}
                    body = self.read_json() if method in ("POST", "PUT", "PATCH") else {}
                    result = route(app, method, url.path, q, body)
                    if isinstance(result, tuple):  # (상태, 바이트, 형식, 머리글)
                        self.send_bytes(*result)
                    else:
                        self.send_json(200, result)
                elif method == "GET":
                    self.serve_static(url.path)
                else:
                    raise ApiError(405, "허용되지 않은 요청")
            except ApiError as e:
                self.send_json(e.status, {"error": e.message})
            except Exception as e:  # 예상 못 한 오류도 화면에 알림
                app.db.log(f"서버 오류: {type(e).__name__}: {e}", "error")
                self.send_json(500, {"error": f"서버 오류: {type(e).__name__}"})

        def serve_static(self, path):
            if path in ("/", "/index.html"):
                html = (WEB_DIR / "index.html").read_text(encoding="utf-8").replace("__DESK_TOKEN__", app.token)
                return self.send_bytes(200, html.encode("utf-8"), "text/html; charset=utf-8",
                                       {"Content-Security-Policy": CSP, "Cache-Control": "no-store",
                                        "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})
            root, rel = (PASS_DIR, path[len("/pass/"):]) if path.startswith("/pass/") else (WEB_DIR, path.lstrip("/"))
            if path == "/pass/" or rel == "":
                rel = "index.html"
            target = (root / rel).resolve()
            if root.resolve() not in target.parents or not target.is_file():
                raise ApiError(404, "없는 파일입니다")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            self.send_bytes(200, target.read_bytes(), ctype, {"Cache-Control": "no-cache"})

        def do_GET(self):
            self.dispatch("GET")

        def do_POST(self):
            self.dispatch("POST")

        def do_PUT(self):
            self.dispatch("PUT")

        def do_PATCH(self):
            self.dispatch("PATCH")

        def do_DELETE(self):
            self.dispatch("DELETE")

    return Handler


# ---------------------------------------------------------------- API
ROUTES = []


def api(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


def route(app, method, path, q, body):
    for m, rx, fn in ROUTES:
        match = rx.match(path)
        if match and m == method:
            return fn(app, q, body, *match.groups())
    raise ApiError(404, "없는 기능입니다")


def pypdf_ready():
    import importlib.util
    return importlib.util.find_spec("pypdf") is not None


@api("GET", "/api/state")
def get_state(app, q, body):
    db, cfg = app.db, app.config
    now = datetime.now()
    return {
        "version": VERSION,
        "settings": cfg.public(),
        "today": date.today().isoformat(),
        "next_collect": next_slot(now, cfg["collect_times"]),
        "last_run": db.one("SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"),
        "counts": {
            "new_items": db.one("SELECT COUNT(*) AS n FROM items WHERE status = 'new'")["n"],
            "students": db.one("SELECT COUNT(*) AS n FROM students")["n"],
        },
        "telegram": {
            "status": app.bot.status if app.bot else "꺼짐",
            "linked": bool(app.bot and app.bot.chat_id),
            "pair_code": app.bot.pair_code() if app.bot else None,
        },
        "pdf_ready": pypdf_ready(),
        "watch": {"enabled": bool(cfg["watch_enabled"]), "running": bool(app.watcher), "interval": cfg["watch_interval"]},
    }


@api("PUT", "/api/settings")
def put_settings(app, q, body):
    try:
        app.config.update(body)
    except ValueError as e:
        raise ApiError(400, str(e))
    return {"ok": True, "settings": app.config.public()}


@api("GET", "/api/logs")
def get_logs(app, q, body):
    after = int(q.get("after") or 0)
    return app.db.query("SELECT * FROM logs WHERE id > ? ORDER BY id DESC LIMIT 100", (after,))[::-1]


# ---- 학생 명단 ----
@api("GET", "/api/students")
def get_students(app, q, body):
    return app.db.students()


@api("POST", "/api/students/parse")
def parse_students(app, q, body):
    return parse_roster(text_field(body.get("text"), "명단", 20000))


@api("PUT", "/api/students")
def put_students(app, q, body):
    rows = body.get("rows")
    need(isinstance(rows, list) and len(rows) <= 100, "명단 형식이 잘못되었습니다 (100명 이하)")
    clean = []
    for r in rows:
        need(isinstance(r, dict) and isinstance(r.get("number"), int), "번호가 잘못되었습니다")
        clean.append({"number": r["number"], "name": text_field(r.get("name"), "이름", 20, True)})
    app.db.replace_students(clean)
    app.db.log(f"학생 명단 {len(clean)}명 저장")
    return app.db.students()


# ---- 조퇴증·외출증 ----
def pass_fields(body, partial=False):
    out = {}
    if "kind" in body or not partial:
        need(body.get("kind") in ("early", "out"), "조퇴/외출을 고르세요")
        out["kind"] = body["kind"]
    if "student_name" in body or not partial:
        out["student_name"] = text_field(body.get("student_name"), "학생 이름", 20, True)
    if "student_number" in body:
        n = body["student_number"]
        need(n is None or (isinstance(n, int) and 0 <= n < 1000), "번호가 잘못되었습니다")
        out["student_number"] = n
    if "date" in body or not partial:
        out["date"] = check_date(body.get("date"))
    for k, name in (("time_out", "나가는 시각"), ("time_return", "복귀 시각")):
        if k in body:
            out[k] = check_time(body[k], name)
    if "reason" in body:
        out["reason"] = text_field(body["reason"], "사유", 200)
    if "note" in body:
        out["note"] = text_field(body["note"], "메모", 200)
    for k in ("guardian_ok", "share_reason"):
        if k in body:
            out[k] = 1 if body[k] else 0
    return out


@api("GET", "/api/passes")
def list_passes(app, q, body):
    if q.get("date"):
        return app.db.query("SELECT * FROM passes WHERE date = ? ORDER BY time_out, id", (check_date(q["date"]),))
    return app.db.query("SELECT * FROM passes ORDER BY date DESC, id DESC LIMIT 200")


@api("POST", "/api/passes")
def create_pass(app, q, body):
    f = pass_fields(body)
    with app.db.lock:
        n = app.db.one("SELECT COUNT(*) AS n FROM passes WHERE date = ?", (f["date"],))["n"] + 1
        f["serial"] = f["date"].replace("-", "") + f"-{n:02d}"
        f["created_at"] = now_str()
        cols = ", ".join(f)
        pid = app.db.execute(f"INSERT INTO passes({cols}) VALUES({', '.join('?' * len(f))})", tuple(f.values()))
    app.db.log(f"{'조퇴증' if f['kind'] == 'early' else '외출증'} 발급 {f['serial']}")
    return app.db.one("SELECT * FROM passes WHERE id = ?", (pid,))


@api("PATCH", r"/api/passes/(\d+)")
def update_pass(app, q, body, pid):
    f = pass_fields(body, partial=True)
    if "status" in body:
        need(body["status"] in ("issued", "returned", "cancelled"), "상태가 잘못되었습니다")
        f["status"] = body["status"]
        f["returned_at"] = now_str() if body["status"] == "returned" else None
    need(app.db.one("SELECT id FROM passes WHERE id = ?", (pid,)), "없는 발급 기록입니다", 404)
    if f:
        app.db.execute(f"UPDATE passes SET {', '.join(k + ' = ?' for k in f)} WHERE id = ?", (*f.values(), pid))
    return app.db.one("SELECT * FROM passes WHERE id = ?", (pid,))


# ---- 수신함 ----
@api("POST", "/api/analyze")
def analyze_text(app, q, body):
    text = text_field(body.get("text"), "내용", 200000, True)
    return extract.analyze(text, ref=date.today())


@api("POST", "/api/items")
def create_item(app, q, body):
    text = text_field(body.get("text"), "내용", 200000, True)
    source = body.get("source") or "paste"
    need(source in ("paste", *SOURCES), "출처가 잘못되었습니다")
    title = text_field(body.get("title"), "제목", 200) or None
    res = add_text_item(app.db, source, text, title=title)
    need(res, "같은 내용이 이미 수신함에 있습니다", 409)
    app.db.log(f"직접 입력: 일정 {res['events']}건")
    return app.db.item(res["id"])


ITEM_LIST_SQL = """
SELECT i.id, i.source, i.title, i.received_at, i.category, i.status, i.registered,
       (SELECT MIN(e.date) FROM events e WHERE e.item_id = i.id AND e.kind = 'deadline' AND e.done = 0
          AND e.date >= ?) AS next_deadline,
       (SELECT COUNT(*) FROM events e WHERE e.item_id = i.id) AS event_count
FROM items i WHERE 1 = 1
"""


@api("GET", "/api/items")
def list_items(app, q, body):
    sql, args = ITEM_LIST_SQL, [date.today().isoformat()]
    if q.get("source"):
        sql += " AND i.source = ?"
        args.append(q["source"])
    status = q.get("status") or "active"
    if status == "active":
        sql += " AND i.status IN ('new', 'read')"
    elif status != "all":
        sql += " AND i.status = ?"
        args.append(status)
    sql += " ORDER BY i.received_at DESC, i.id DESC LIMIT 300"
    return app.db.query(sql, args)


@api("GET", r"/api/items/(\d+)")
def get_item(app, q, body, item_id):
    it = app.db.item(int(item_id))
    need(it, "없는 항목입니다", 404)
    if it["status"] == "new":
        app.db.execute("UPDATE items SET status = 'read' WHERE id = ?", (it["id"],))
        it["status"] = "read"
    # 인터넷 사진은 주소 그대로, 나머지(쪽지 안 그림·PC 파일)는 /images/n 으로 받게 (큰 그림을 목록에 싣지 않음)
    it["images"] = [{"n": n, "url": s if s.lower().startswith(("http://", "https://")) else None}
                    for n, s in enumerate(it.get("images") or [])]
    return it


def _image_roots(app):
    """PC 파일 사진을 읽어도 되는 폴더: 쿨메신저 폴더, 쪽지 파일(.udb)이 있는 폴더, 받은 파일 폴더."""
    roots = [coolmsg.memo_dir().parent]
    try:
        udb = coolmsg.resolve(app.config["coolmessenger_name"], app.config["coolmessenger_path"])
        roots += [udb.parent, udb.parent.parent]
    except coolmsg.CoolError:
        udb = None
    if app.config["coolmessenger_folder"]:
        roots.append(Path(app.config["coolmessenger_folder"]))
    return udb, roots


def _local_image(app, src):
    """사진 주소 → 읽어도 되는 PC 파일 (허용한 폴더 안의 그림 파일만), 없으면 None."""
    udb, roots = _image_roots(app)
    if src.lower().startswith("file:"):
        cands = [Path(url2pathname(urlparse(src).path))]
    elif os.path.isabs(src) or re.match(r"^[A-Za-z]:[\\/]", src):
        cands = [Path(src)]
    else:  # 이름만 있으면 쪽지 파일 옆에서 찾음
        rel = src.replace("\\", "/").lstrip("/")
        cands = [r / rel for r in roots] + ([udb.with_suffix("") / rel] if udb else [])
    allowed = [os.path.normcase(str(r.resolve())) for r in roots if str(r)]
    for c in cands:
        try:
            p = c.resolve()
        except OSError:
            continue
        name = os.path.normcase(str(p))
        if (p.suffix.lower() in coolmsg.IMAGE_EXTS and p.is_file()
                and any(name.startswith(a.rstrip("\\/") + os.sep) for a in allowed)):
            return p
    return None


@api("GET", r"/api/items/(\d+)/images/(\d+)")
def item_image(app, q, body, item_id, n):
    """쿨메신저 사진 하나 (쪽지 안에 든 그림, PC 파일). 인터넷 주소 사진은 화면이 직접 불러옴."""
    it = app.db.one("SELECT images FROM items WHERE id = ?", (item_id,))
    need(it, "없는 항목입니다", 404)
    srcs = json.loads(it["images"] or "[]")
    n = int(n)
    need(0 <= n < len(srcs), "없는 사진입니다", 404)
    src = srcs[n]
    head = {"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"}
    m = re.match(r"^data:(image/(?:png|jpeg|gif|bmp|webp));base64,(.+)$", src, re.S)
    if m:
        try:
            return 200, base64.b64decode(m.group(2)), m.group(1), head
        except (binascii.Error, ValueError):
            raise ApiError(404, "사진을 읽을 수 없습니다")
    p = _local_image(app, src)
    need(p, "사진 파일을 찾을 수 없습니다 (쿨메신저 폴더 밖이거나 지워짐)", 404)
    need(p.stat().st_size <= 30 * 1024 * 1024, "사진이 너무 큽니다", 413)
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return 200, p.read_bytes(), ctype, head


@api("PATCH", r"/api/items/(\d+)")
def update_item(app, q, body, item_id):
    need(app.db.one("SELECT id FROM items WHERE id = ?", (item_id,)), "없는 항목입니다", 404)
    f = {}
    if "status" in body:
        need(body["status"] in ("new", "read", "done", "archived"), "상태가 잘못되었습니다")
        f["status"] = body["status"]
    if "title" in body:
        f["title"] = text_field(body["title"], "제목", 200, True)
    if "category" in body:
        need(body["category"] in CATEGORIES, "분류가 잘못되었습니다")
        f["category"] = body["category"]
    if "registered" in body:
        need(isinstance(body["registered"], bool), "일정 등록 값이 잘못되었습니다")
        f["registered"] = int(body["registered"])
    if f:
        app.db.execute(f"UPDATE items SET {', '.join(k + ' = ?' for k in f)} WHERE id = ?", (*f.values(), item_id))
    return app.db.item(int(item_id))


@api("DELETE", r"/api/items/(\d+)")
def delete_item(app, q, body, item_id):
    app.db.execute("DELETE FROM items WHERE id = ?", (item_id,))
    # 같은 파일을 다시 수집하지 않도록 files_seen 은 남겨 둠
    return {"ok": True}


# ---- 일정 ----
def event_fields(body, partial=False):
    f = {}
    if "date" in body or not partial:
        f["date"] = check_date(body.get("date"))
    if "end_date" in body:
        f["end_date"] = check_date(body["end_date"], "끝 날짜") if body["end_date"] else None
    if "time" in body:
        f["time"] = check_time(body["time"])
    if "kind" in body or not partial:
        need(body.get("kind", "event") in ("deadline", "event"), "종류가 잘못되었습니다")
        f["kind"] = body.get("kind", "event")
    if "category" in body or not partial:
        cat = body.get("category") or "참고"
        need(cat in CATEGORIES, "분류가 잘못되었습니다")
        f["category"] = cat
    if "text" in body or not partial:
        f["text"] = text_field(body.get("text"), "내용", 300, True)
    if "done" in body:
        f["done"] = 1 if body["done"] else 0
    if f.get("end_date") and f.get("date"):
        need(f["end_date"] >= f["date"], "끝 날짜가 시작보다 빠릅니다")
    return f


@api("GET", "/api/events")
def list_events(app, q, body):
    start = date.fromisoformat(check_date(q.get("from"), "시작 날짜"))
    end = date.fromisoformat(check_date(q.get("to"), "끝 날짜"))
    need((end - start).days <= 400, "기간이 너무 깁니다")
    return queries.events_between(app.db, start, end)


@api("POST", "/api/events")
def create_event(app, q, body):
    f = event_fields(body)
    if body.get("item_id") is not None:
        need(app.db.one("SELECT id FROM items WHERE id = ?", (body["item_id"],)), "없는 항목입니다", 404)
        f["item_id"] = body["item_id"]
        # 일정을 직접 더하면 그 항목은 등록한 것으로 (더한 일정이 달력에 보이게)
        app.db.execute("UPDATE items SET registered = 1 WHERE id = ?", (body["item_id"],))
    eid = app.db.execute(f"INSERT INTO events({', '.join(f)}) VALUES({', '.join('?' * len(f))})", tuple(f.values()))
    return app.db.one("SELECT * FROM events WHERE id = ?", (eid,))


@api("PATCH", r"/api/events/(\d+)")
def update_event(app, q, body, eid):
    need(app.db.one("SELECT id FROM events WHERE id = ?", (eid,)), "없는 일정입니다", 404)
    f = event_fields(body, partial=True)
    if f:
        app.db.execute(f"UPDATE events SET {', '.join(k + ' = ?' for k in f)} WHERE id = ?", (*f.values(), eid))
    return app.db.one("SELECT * FROM events WHERE id = ?", (eid,))


@api("DELETE", r"/api/events/(\d+)")
def delete_event(app, q, body, eid):
    app.db.execute("DELETE FROM events WHERE id = ?", (eid,))
    return {"ok": True}


# ---- 학사일정 ----
def school_year_of(body):
    y = body.get("school_year")
    need(isinstance(y, int) and 2000 <= y <= 2100, "학년도가 잘못되었습니다")
    return y


@api("POST", "/api/schoolcal/preview")
def schoolcal_preview(app, q, body):
    """파일(base64) 또는 붙여넣은 글 → 미리보기 일정 (저장하지 않음)."""
    year = school_year_of(body)
    try:
        if body.get("text") is not None:
            return schoolcal.parse(year, text=text_field(body["text"], "내용", 500000, True))
        name = text_field(body.get("filename"), "파일 이름", 200, True)
        try:
            data = base64.b64decode(body.get("data") or "", validate=True)
        except (binascii.Error, ValueError):
            raise ApiError(400, "파일을 읽을 수 없습니다")
        need(data, "빈 파일입니다")
        return schoolcal.parse(year, filename=name, data=data)
    except schoolcal.CalError as e:
        raise ApiError(400, str(e))


@api("GET", "/api/neis/schools")
def neis_schools(app, q, body):
    name = text_field(q.get("q"), "학교 이름", 50, True)
    try:
        return neis.search_schools(name, app.config["neis_key"], app.config["neis_api_base"])
    except neis.NeisError as e:
        raise ApiError(502, str(e))


@api("POST", "/api/schoolcal/neis")
def schoolcal_neis(app, q, body):
    year = school_year_of(body)
    sch = app.config["neis_school"] or {}
    need(sch.get("atpt") and sch.get("code"), "먼저 학교를 찾아 고르세요")
    try:
        return neis.schedule(sch["atpt"], sch["code"], year, app.config["neis_key"], app.config["neis_api_base"])
    except neis.NeisError as e:
        raise ApiError(502, str(e))


@api("POST", "/api/schoolcal/save")
def schoolcal_save(app, q, body):
    year = school_year_of(body)
    evs = body.get("events")
    need(isinstance(evs, list) and 0 < len(evs) <= 2000, "넣을 일정을 고르세요")
    clean = []
    for e in evs:
        need(isinstance(e, dict), "일정 형식이 잘못되었습니다")
        f = event_fields({**e, "kind": "event"})
        f.setdefault("end_date", None)
        f.setdefault("time", None)
        clean.append(f)
    origin = text_field(body.get("origin"), "출처", 120) or "직접 입력"
    res = schoolcal.save(app.db, year, clean, origin, replace=bool(body.get("replace", True)))
    app.db.log(f"{year}학년도 학사일정 {res['count']}건 가져옴" + (" (이전 것 바꿈)" if res["replaced"] else ""))
    return res


@api("GET", "/api/calendar.ics")
def calendar_ics(app, q, body):
    today = date.today()
    evs = queries.events_between(app.db, today - timedelta(days=30), today + timedelta(days=365))
    return (200, to_ics(evs).encode("utf-8"), "text/calendar; charset=utf-8",
            {"Content-Disposition": 'attachment; filename="teacher-desk.ics"', "Cache-Control": "no-store"})


# ---- 대시보드·검색·수집 ----
@api("GET", "/api/today")
def get_today(app, q, body):
    return queries.today_summary(app.db)


@api("GET", "/api/ai/weekly")
def ai_weekly_get(app, q, body):
    return app.ai_job.state()


@api("POST", "/api/ai/weekly")
def ai_weekly_run(app, q, body):
    """요약 시작 (이미 하는 중이면 그대로). 결과는 GET 으로 확인."""
    need(app.config["ai_enabled"], "⚙ 설정에서 AI 요약을 먼저 켜세요")
    try:
        ai.check_local(app.config["ai_base"])
    except ai.AIError as e:
        raise ApiError(400, str(e))
    app.ai_job.start("new" if body.get("kind") == "new" else "full")
    return app.ai_job.state()


@api("POST", "/api/ai/hidden")
def ai_hidden(app, q, body):
    """요약 줄 지우기·되돌리기."""
    key = text_field(body.get("key"), "지울 줄", 400, True)
    return {"hidden": ai.set_hidden(app.db, key, body.get("hide", True) is not False)}


@api("POST", "/api/ai/check")
def ai_check(app, q, body):
    base = text_field(body.get("base"), "AI 주소", 200) or app.config["ai_base"]
    try:
        return ai.check(base)
    except ai.AIError as e:
        raise ApiError(400, str(e))


@api("GET", "/api/search")
def get_search(app, q, body):
    return queries.search(app.db, text_field(q.get("q"), "검색어", 100))


@api("POST", "/api/collect")
def post_collect(app, q, body):
    stats = app.collector.run("manual")
    need(not stats.get("busy"), "이미 수집 중입니다", 409)
    return stats


@api("GET", "/api/diagnose")
def get_diagnose(app, q, body):
    source = q.get("source")
    need(source in SOURCES, "진단할 폴더를 고르세요")
    # 화면 칸에 적은 경로를 바로 확인 (저장하기 전에도), 없으면 저장된 설정
    folder = (q.get("folder") or "").strip().strip('"') or app.config[SOURCES[source]]
    need(folder, "폴더 경로를 먼저 입력하세요")
    saved = app.config[SOURCES[source]]
    res = diagnose(folder)
    same = lambda a, b: os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))  # noqa: E731
    res["saved"] = bool(saved) and same(folder, saved)
    return res


@api("GET", "/api/cool")
def get_cool(app, q, body):
    """이 PC 에서 찾은 쿨메신저 이름들 (Memo 폴더의 *.udb)."""
    return {"dir": str(coolmsg.memo_dir()), "names": coolmsg.candidates(), "name": app.config["coolmessenger_name"],
            "path": app.config["coolmessenger_path"]}


@api("POST", "/api/cool/check")
def post_cool_check(app, q, body):
    name = text_field(body.get("name"), "쿨메신저 이름", 60)
    path = text_field(body.get("path"), "쿨메신저 파일 경로", 400)
    try:
        return coolmsg.check(coolmsg.resolve(name, path))
    except coolmsg.CoolError as e:
        raise ApiError(400, str(e))


PICK_PURPOSES = {
    # 용도: (종류, 창 제목, 파일 거르개, 처음 열 곳)
    "cool": ("file", "쿨메신저 쪽지 파일(.udb) 고르기", "쿨메신저 쪽지 (*.udb)|*.udb|모든 파일 (*.*)|*.*",
             lambda: str(coolmsg.memo_dir())),
    "coolfolder": ("folder", "쿨메신저 받은 파일 폴더 고르기", "", lambda: ""),
    "edufine": ("folder", "에듀파인 공문 저장 폴더 고르기", "", lambda: ""),
}


@api("POST", "/api/pick")
def post_pick(app, q, body):
    """윈도우 찾아보기 창을 띄워 고른 경로를 돌려줌 (취소하면 빈 값)."""
    need(body.get("purpose") in PICK_PURPOSES, "무엇을 고를지 알 수 없습니다")
    kind, title, filt, initial = PICK_PURPOSES[body["purpose"]]
    try:
        return {"path": picker.pick(kind, title, initial(), filt)}
    except picker.PickerError as e:
        raise ApiError(400, str(e))


@api("POST", "/api/telegram")
def post_telegram(app, q, body):
    need(app.bot, "텔레그램이 꺼져 있습니다")
    action = body.get("action")
    if action == "renew":
        app.bot.pair_code(renew=True)
    elif action == "unpair":
        app.bot.unpair()
        app.db.log("텔레그램 연결 해제")
    elif action == "test":
        need(app.bot.chat_id, "먼저 텔레그램을 연결하세요")
        try:
            app.bot.send("🔔 업무 데스크 시험 알림입니다.")
        except Exception as e:
            raise ApiError(502, f"보내기 실패: {e}")
    else:
        raise ApiError(400, "알 수 없는 동작")
    return {"ok": True, "pair_code": app.bot.pair_code(), "linked": bool(app.bot.chat_id)}


def serve(app, open_browser=True):
    port = app.config["port"]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{port}/"
    print(f"업무 데스크가 켜졌습니다: {url}")
    print("이 창을 닫으면 업무 데스크(예약 수집·텔레그램 포함)가 꺼집니다.")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    return httpd
