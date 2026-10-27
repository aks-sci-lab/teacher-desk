"""SQLite 저장소. 모든 자료는 이 PC 의 data/desk.db 한 파일에만 저장됩니다."""

import json
import sqlite3
import threading
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    number INTEGER NOT NULL,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS passes (
    id INTEGER PRIMARY KEY,
    serial TEXT NOT NULL,
    kind TEXT NOT NULL,              -- early: 조퇴, out: 외출
    student_number INTEGER,
    student_name TEXT NOT NULL,
    date TEXT NOT NULL,
    time_out TEXT,
    time_return TEXT,                -- 외출 복귀 예정
    reason TEXT,
    guardian_ok INTEGER DEFAULT 0,
    note TEXT,
    share_reason INTEGER DEFAULT 0,
    status TEXT DEFAULT 'issued',    -- issued / returned / cancelled
    returned_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,            -- cool / edufine / paste
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    received_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    content_hash TEXT,
    summary TEXT,                    -- JSON 배열
    category TEXT,
    status TEXT DEFAULT 'new',       -- new / read / done / archived
    doc_numbers TEXT,                -- JSON 배열
    file_path TEXT
);
CREATE INDEX IF NOT EXISTS items_hash ON items(content_hash);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    item_id INTEGER REFERENCES items(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    end_date TEXT,
    time TEXT,
    kind TEXT NOT NULL,              -- deadline / event
    category TEXT,
    text TEXT,
    done INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_date ON events(date);

CREATE TABLE IF NOT EXISTS files_seen (
    path TEXT PRIMARY KEY,
    mtime REAL,
    size INTEGER,
    item_id INTEGER
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    reason TEXT,
    slot_keys TEXT,                  -- JSON 배열 ('2026-10-26 09:00')
    stats TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class DB:
    """연결 하나를 잠금으로 보호해 여러 스레드(화면·수집·텔레그램)가 함께 씁니다."""

    def __init__(self, path):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        with self.lock:
            self.conn.close()

    # ---- 기본 ----
    def query(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    def get_meta(self, key, default=None):
        row = self.one("SELECT value FROM meta WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set_meta(self, key, value):
        self.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, json.dumps(value, ensure_ascii=False)))

    def log(self, message, level="info"):
        self.execute("INSERT INTO logs(at, level, message) VALUES(?, ?, ?)", (now_str(), level, message))
        # 기록이 끝없이 늘지 않게 최근 500개만 남김
        self.execute("DELETE FROM logs WHERE id <= (SELECT MAX(id) - 500 FROM logs)")

    # ---- 학생 명단 ----
    def students(self):
        return self.query("SELECT * FROM students ORDER BY number, name")

    def replace_students(self, rows):
        with self.lock:
            self.conn.execute("DELETE FROM students")
            self.conn.executemany("INSERT INTO students(number, name) VALUES(?, ?)",
                                  [(r["number"], r["name"]) for r in rows])
            self.conn.commit()

    # ---- 수신 항목 ----
    def add_item(self, source, title, body, received_at, analysis, content_hash=None, file_path=None):
        """분석 결과(extract.analyze)와 함께 항목과 일정을 한 번에 저장."""
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO items(source, title, body, received_at, created_at, content_hash, summary, "
                "category, doc_numbers, file_path) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (source, title, body, received_at, now_str(), content_hash,
                 json.dumps(analysis["summary"], ensure_ascii=False), analysis["category"],
                 json.dumps(analysis["doc_numbers"], ensure_ascii=False), file_path))
            item_id = cur.lastrowid
            self.conn.executemany(
                "INSERT INTO events(item_id, date, end_date, time, kind, category, text) VALUES(?,?,?,?,?,?,?)",
                [(item_id, e["date"], e.get("end_date"), e.get("time"), e["kind"], e["category"], e["text"])
                 for e in analysis["events"]])
            self.conn.commit()
            return item_id

    def item(self, item_id):
        it = self.one("SELECT * FROM items WHERE id = ?", (item_id,))
        if not it:
            return None
        decode_item(it)
        it["events"] = self.query("SELECT * FROM events WHERE item_id = ? ORDER BY date, time", (item_id,))
        it["related"] = self.related_items(it)
        return it

    def related_items(self, it):
        """같은 공문 문서번호가 나오는 다른 항목 (쿨메신저 안내 ↔ 에듀파인 공문)."""
        out = {}
        for num in it["doc_numbers"]:
            like = '%"' + num.replace("%", "").replace("_", "") + '"%'
            for r in self.query("SELECT id, source, title, received_at FROM items "
                                "WHERE id != ? AND doc_numbers LIKE ?", (it["id"], like)):
                out[r["id"]] = r
        return list(out.values())


def decode_item(it):
    it["summary"] = json.loads(it.get("summary") or "[]")
    it["doc_numbers"] = json.loads(it.get("doc_numbers") or "[]")
    return it
