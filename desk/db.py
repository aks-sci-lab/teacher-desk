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

CREATE TABLE IF NOT EXISTS absences (
    id INTEGER PRIMARY KEY,
    serial TEXT NOT NULL,
    student_number INTEGER,
    student_name TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    absence_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    guardian_name TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);

-- 0.5.95 출석부의 지각·조퇴·결과 (결석은 결석계가, 증 있는 조퇴·외출은 passes 가 원천)
CREATE TABLE IF NOT EXISTS attendance_marks (
    id INTEGER PRIMARY KEY,
    student_number INTEGER,
    student_name TEXT NOT NULL,
    date TEXT NOT NULL,
    kind TEXT NOT NULL,              -- late: 지각, early: 조퇴(증 없음), result: 결과
    mark_type TEXT NOT NULL,         -- 질병 / 출석인정 / 기타 / 미인정 / 미정
    periods TEXT,                    -- 결과 교시 (예: 3,4)
    note TEXT,
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
    file_path TEXT,
    registered INTEGER DEFAULT 0,    -- 1: 수신함에서 체크함 → 이 항목의 일정이 달력·브리핑에 들어감
    images TEXT                      -- 쿨메신저 쪽지의 사진 주소 JSON 배열 (NULL: 아직 확인 안 함)
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
    done INTEGER DEFAULT 0,
    source TEXT                      -- NULL: 규칙으로 찾음 / ai: 브리핑 AI 요약에서 찾음
);
CREATE INDEX IF NOT EXISTS events_date ON events(date);

CREATE TABLE IF NOT EXISTS files_seen (
    path TEXT PRIMARY KEY,
    mtime REAL,
    size INTEGER,
    item_id INTEGER,
    error TEXT                       -- 읽지 못한 까닭 (수동 수집 때 다시 시도)
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

-- 브리핑의 '꼭 할 일' 에 선생님이 직접 넣은 것 (0.5.18): 다른 칸에서 끌어 오거나 직접 씀
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    date TEXT,
    time TEXT,
    item_id INTEGER REFERENCES items(id) ON DELETE SET NULL,
    event_id INTEGER,
    origin TEXT,                     -- ai: AI 요약 줄 / event: 일정 / item: 받은 자료 / manual: 직접
    hide_key TEXT,                   -- AI 요약에서 옮겨 온 줄 (지우면 원래 칸으로 되돌림)
    done INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    done_at TEXT,
    sort REAL                        -- 선생님이 끌어서 정한 순서 (0.5.20)
);

CREATE TABLE IF NOT EXISTS todo_notes (
    id INTEGER PRIMARY KEY,
    todo_id INTEGER NOT NULL REFERENCES todos(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT                  -- 고친 적이 있으면 그 시각 (0.5.34)
);
CREATE INDEX IF NOT EXISTS todo_notes_todo ON todo_notes(todo_id);

-- 학급 경영 (0.5.80): 좌석·청소·1인 1역·당번·모둠의 지금 자료(JSON)와 예전 배치 기록. 학생은 번호로 연결
CREATE TABLE IF NOT EXISTS class_data (
    kind TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS class_history (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    label TEXT,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS class_history_kind ON class_history(kind, id);
-- 학급 투표 (0.5.81): 비밀 투표라 '누가 투표했나'(번호)와 '몇 표'(counts)를 따로 두고, 누가 무엇을 골랐는지는 남기지 않음
CREATE TABLE IF NOT EXISTS class_votes (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    mode TEXT NOT NULL,              -- secret: 교실 화면 비밀 투표 / manual: 손들기·종이 결과 입력
    options TEXT NOT NULL,           -- JSON 배열
    abstain INTEGER DEFAULT 0,       -- 1: 마지막 칸이 '기권'
    counts TEXT NOT NULL,            -- JSON 배열 (선택지 순서, 기권은 끝)
    status TEXT DEFAULT 'open',      -- open / closed
    created_at TEXT NOT NULL,
    closed_at TEXT,
    drawn TEXT,                      -- 동점 제비뽑기 결과 JSON
    parent_id INTEGER                -- 결선 투표면 원래 투표
);
-- 학급 제출물 확인·상담 기록 (0.5.82). 상담 기록은 특히 민감: 검색·브리핑·AI·텔레그램에 쓰지 않음
CREATE TABLE IF NOT EXISTS class_tasks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    due TEXT,
    note TEXT,
    closed INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS class_task_marks (
    task_id INTEGER NOT NULL,
    number INTEGER NOT NULL,
    status TEXT NOT NULL,            -- done: 제출 / exempt: 면제 (줄이 없으면 미제출)
    at TEXT NOT NULL,
    PRIMARY KEY (task_id, number)
);
CREATE TABLE IF NOT EXISTS class_counsel (
    id INTEGER PRIMARY KEY,
    number INTEGER NOT NULL,
    student_name TEXT NOT NULL,      -- 기록할 때의 이름 (명단이 바뀌어도 남게)
    date TEXT NOT NULL,
    time TEXT,
    method TEXT NOT NULL,            -- 대면 / 전화 / 문자 / 온라인 / 기타
    with_whom TEXT NOT NULL,         -- 학생 / 학부모 / 학생·학부모 / 기타
    topic TEXT NOT NULL,
    content TEXT,
    followup TEXT,
    followup_date TEXT,
    followup_done INTEGER DEFAULT 0,
    duration INTEGER,                -- 통화 길이(초), 통화 내역 불러오기용
    source TEXT,                     -- manual / import
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS class_counsel_number ON class_counsel(number, date);
-- 텔레그램으로 받은 통화·문자 파일에서 만든 상담 초안 (0.5.84): PC 에서 확인해야 저장, 14일 뒤 자동 삭제
CREATE TABLE IF NOT EXISTS class_call_drafts (
    id INTEGER PRIMARY KEY,
    drafts TEXT NOT NULL,            -- JSON 배열 (학부모 번호와 맞는 것만)
    stats TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS class_vote_voters (
    vote_id INTEGER NOT NULL,
    number INTEGER NOT NULL,
    PRIMARY KEY (vote_id, number)
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
        # 지우거나 고쳐 쓴 자료(예: 비밀 투표 중간 표 수)가 DB 파일의 빈 곳에 남지 않게 0으로 덮음 (0.5.81)
        self.conn.execute("PRAGMA secure_delete = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(items)")}
        if "registered" not in cols:
            # 0.4.2: 수신함에서 체크한 메시지만 일정 등록 (사용자 요청). 직접 입력·학사일정은 등록된 것으로
            self.conn.execute("ALTER TABLE items ADD COLUMN registered INTEGER DEFAULT 0")
            self.conn.execute("UPDATE items SET registered = 1 WHERE source IN ('paste', 'school')")
        if "error" not in {r[1] for r in self.conn.execute("PRAGMA table_info(files_seen)")}:
            self.conn.execute("ALTER TABLE files_seen ADD COLUMN error TEXT")  # 0.5.5
        if "source" not in {r[1] for r in self.conn.execute("PRAGMA table_info(events)")}:
            self.conn.execute("ALTER TABLE events ADD COLUMN source TEXT")  # 0.5.11
        if "images" not in cols:
            self.conn.execute("ALTER TABLE items ADD COLUMN images TEXT")  # 0.5.2: 쿨메신저 사진
        for col in ("ai_summary", "ai_summary_hash", "ai_summary_at", "ai_summary_model"):   # 0.5.35: 항목별 AI 요약
            if col not in cols:
                self.conn.execute(f"ALTER TABLE items ADD COLUMN {col} TEXT")
        if "parent_phone" not in {r[1] for r in self.conn.execute("PRAGMA table_info(students)")}:
            self.conn.execute("ALTER TABLE students ADD COLUMN parent_phone TEXT")  # 0.5.46: 학부모 확인용 연락처
        absence_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(absences)")}
        if "status" not in absence_cols:   # 0.5.95: 출석부에서 만든 초안(draft)과 작성 완료(done)
            self.conn.execute("ALTER TABLE absences ADD COLUMN status TEXT DEFAULT 'done'")
        if "printed_at" not in absence_cols:
            self.conn.execute("ALTER TABLE absences ADD COLUMN printed_at TEXT")
        if "parent_phone2" not in {r[1] for r in self.conn.execute("PRAGMA table_info(students)")}:
            self.conn.execute("ALTER TABLE students ADD COLUMN parent_phone2 TEXT")  # 0.5.89: 학부모 연락처 2
        if "import_key" not in {r[1] for r in self.conn.execute("PRAGMA table_info(class_counsel)")}:
            # 0.5.83: 통화·문자 내역에서 만든 상담 기록 (같은 통화를 두 번 저장하지 않게)
            self.conn.execute("ALTER TABLE class_counsel ADD COLUMN import_key TEXT")
        self.conn.execute("CREATE INDEX IF NOT EXISTS class_counsel_import ON class_counsel(import_key)")
        if "sort" not in {r[1] for r in self.conn.execute("PRAGMA table_info(todos)")}:
            # 0.5.20: 꼭 할 일 순서를 끌어서 바꿈. 예전 것은 그때 보이던 순서(날짜 순)대로 번호를 매김
            self.conn.execute("ALTER TABLE todos ADD COLUMN sort REAL")
            ids = [r[0] for r in self.conn.execute(
                "SELECT id FROM todos ORDER BY date IS NULL, date, COALESCE(time, '99'), id")]
            self.conn.executemany("UPDATE todos SET sort = ? WHERE id = ?", [(i, tid) for i, tid in enumerate(ids)])

    def refresh_schema(self):
        """자료를 통째로 바꾼 뒤(드라이브에서 받기, 0.5.96): 예전 버전 자료면 새 표·칸을 더함."""
        with self.lock:
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.executescript(SCHEMA)
            self._migrate()
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
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO logs(at, level, message) VALUES(?, ?, ?)",
                (now_str(), level, message),
            )
            self.conn.execute("DELETE FROM logs WHERE id <= (SELECT MAX(id) - 500 FROM logs)")

    # ---- 학생 명단 ----
    def students(self):
        return self.query("SELECT * FROM students ORDER BY number, name")

    def replace_students(self, rows):
        """명단을 통째로 바꿈. 연락처(1·2)를 주지 않은(None) 줄은 같은 번호·이름의 예전 연락처를 이어 받고,
        "" 이면 지움."""
        def pick(r, key, old_value):
            value = r.get(key)
            return old_value if value is None else (value or None)

        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                old = {(r[0], r[1]): (r[2], r[3]) for r in self.conn.execute(
                    "SELECT number, name, parent_phone, parent_phone2 FROM students")}
                self.conn.execute("DELETE FROM students")
                self.conn.executemany(
                    "INSERT INTO students(number, name, parent_phone, parent_phone2) VALUES(?, ?, ?, ?)",
                    [(r["number"], r["name"],
                      pick(r, "parent_phone", old.get((r["number"], r["name"]), (None, None))[0]),
                      pick(r, "parent_phone2", old.get((r["number"], r["name"]), (None, None))[1]))
                     for r in rows])
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    # ---- 수신 항목 ----
    def add_item(self, source, title, body, received_at, analysis, content_hash=None, file_path=None,
                 registered=None, images=None):
        """분석 결과(extract.analyze)와 함께 항목과 일정을 한 번에 저장.

        자동 수집한 메시지·공문의 일정은 '후보'로만 저장 (수신함에서 체크해야 달력에 들어감).
        """
        if registered is None:
            registered = source in ("paste", "school")
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                if content_hash and self.conn.execute(
                        "SELECT 1 FROM items WHERE content_hash = ? LIMIT 1", (content_hash,)).fetchone():
                    self.conn.rollback()
                    return None
                cur = self.conn.execute(
                    "INSERT INTO items(source, title, body, received_at, created_at, content_hash, summary, "
                    "category, doc_numbers, file_path, registered, images) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (source, title, body, received_at, now_str(), content_hash,
                     json.dumps(analysis["summary"], ensure_ascii=False), analysis["category"],
                     json.dumps(analysis["doc_numbers"], ensure_ascii=False), file_path, int(bool(registered)),
                     json.dumps(images, ensure_ascii=False) if images is not None else None))
                item_id = cur.lastrowid
                self.conn.executemany(
                    "INSERT INTO events(item_id, date, end_date, time, kind, category, text) VALUES(?,?,?,?,?,?,?)",
                    [(item_id, e["date"], e.get("end_date"), e.get("time"), e["kind"], e["category"], e["text"])
                     for e in analysis["events"]])
                self.conn.commit()
                return item_id
            except Exception:
                self.conn.rollback()
                raise

    def replace_events(self, item_id, events):
        """한 항목의 일정을 새로 찾은 것으로 바꿈."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.execute("DELETE FROM events WHERE item_id = ?", (item_id,))
                self.conn.executemany(
                    "INSERT INTO events(item_id, date, end_date, time, kind, category, text) VALUES(?,?,?,?,?,?,?)",
                    [(item_id, e["date"], e.get("end_date"), e.get("time"), e["kind"], e["category"], e["text"])
                     for e in events])
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

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
    if "images" in it:
        it["images"] = json.loads(it.get("images") or "[]")
    it["summary"] = json.loads(it.get("summary") or "[]")
    it["doc_numbers"] = json.loads(it.get("doc_numbers") or "[]")
    return it
