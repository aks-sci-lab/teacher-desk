import sqlite3
import tempfile
import unittest
from pathlib import Path

from desk.db import DB


class DBMigrationTests(unittest.TestCase):
    def test_existing_rows_survive_schema_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "desk.db"
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE students (id INTEGER PRIMARY KEY, number INTEGER NOT NULL, name TEXT NOT NULL);
                CREATE TABLE items (
                    id INTEGER PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL,
                    body TEXT NOT NULL, received_at TEXT NOT NULL, created_at TEXT NOT NULL,
                    content_hash TEXT, summary TEXT, category TEXT, status TEXT,
                    doc_numbers TEXT, file_path TEXT
                );
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY, item_id INTEGER, date TEXT NOT NULL,
                    end_date TEXT, time TEXT, kind TEXT NOT NULL, category TEXT,
                    text TEXT, done INTEGER
                );
                CREATE TABLE files_seen (path TEXT PRIMARY KEY, mtime REAL, size INTEGER, item_id INTEGER);
                CREATE TABLE todos (
                    id INTEGER PRIMARY KEY, text TEXT NOT NULL, date TEXT, time TEXT,
                    item_id INTEGER, event_id INTEGER, origin TEXT, hide_key TEXT,
                    done INTEGER, created_at TEXT NOT NULL, done_at TEXT
                );
                INSERT INTO students VALUES (1, 7, '가상 학생');
                INSERT INTO items VALUES
                    (1, 'paste', '직접 입력', '내용', '2026-11-02 09:00:00', '2026-11-02 09:00:00',
                     'hash-1', '[]', '참고', 'new', '[]', NULL),
                    (2, 'cool', '쪽지', '내용', '2026-11-02 10:00:00', '2026-11-02 10:00:00',
                     'hash-2', '[]', '참고', 'new', '[]', NULL);
                INSERT INTO events VALUES (1, 1, '2026-11-03', NULL, NULL, 'event', '행사', '회의', 0);
                INSERT INTO files_seen VALUES ('memo.txt', 1.0, 10, 1);
                INSERT INTO todos VALUES
                    (1, '나중 할 일', '2026-11-04', NULL, NULL, NULL, 'manual', NULL, 0,
                     '2026-11-02 09:00:00', NULL),
                    (2, '먼저 할 일', '2026-11-03', NULL, NULL, NULL, 'manual', NULL, 0,
                     '2026-11-02 09:00:00', NULL);
            """)
            conn.commit()
            conn.close()

            db = DB(path)
            try:
                self.assertEqual(db.one("SELECT name, parent_phone, parent_phone2 FROM students WHERE id = 1"),
                                 {"name": "가상 학생", "parent_phone": None, "parent_phone2": None})
                self.assertEqual(db.one("SELECT title, registered, images, ai_summary FROM items WHERE id = 1"),
                                 {"title": "직접 입력", "registered": 1, "images": None, "ai_summary": None})
                self.assertEqual(db.one("SELECT registered FROM items WHERE id = 2")["registered"], 0)
                self.assertEqual(db.one("SELECT text, source FROM events WHERE id = 1"),
                                 {"text": "회의", "source": None})
                self.assertIsNone(db.one("SELECT error FROM files_seen WHERE path = 'memo.txt'")["error"])
                self.assertEqual(db.query("SELECT id, sort FROM todos ORDER BY sort"), [
                    {"id": 2, "sort": 0.0},
                    {"id": 1, "sort": 1.0},
                ])
            finally:
                db.close()

    def test_failed_student_replacement_preserves_existing_roster(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "desk.db")
            try:
                db.replace_students([{"number": 7, "name": "가상 학생", "parent_phone": "010-0000-0000"}])
                with self.assertRaises(sqlite3.IntegrityError):
                    db.replace_students([
                        {"number": 8, "name": "다른 학생"},
                        {"number": None, "name": "잘못된 학생"},
                    ])

                db.replace_students([{"number": 7, "name": "가상 학생"}])
                self.assertEqual(db.students(), [{
                    "id": 1, "number": 7, "name": "가상 학생", "parent_phone": "010-0000-0000", "parent_phone2": None,
                }])
            finally:
                db.close()

    def test_log_keeps_only_the_latest_500_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "desk.db")
            try:
                for i in range(510):
                    db.log(f"entry {i}")
                rows = db.query("SELECT message FROM logs ORDER BY id")
                self.assertEqual(len(rows), 500)
                self.assertEqual(rows[0]["message"], "entry 10")
                self.assertEqual(rows[-1]["message"], "entry 509")
            finally:
                db.close()
