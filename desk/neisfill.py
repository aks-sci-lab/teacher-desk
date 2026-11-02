"""나이스 입력 칸 채우기 (0.5.56, 시험 단계).

흐름: ① 확장 프로그램이 나이스 화면의 **입력 칸 구조**(칸 찾는 법·이름표·종류·선택지, **값은 없음**)를 보냄 → 양식으로 저장
      ② 선생님이 업무 데스크 '나이스 입력' 화면에서 칸마다 넣을 값을 정하고 [나이스에 채우기] → 채우기 요청
      ③ 나이스 탭이 열린 브라우저의 확장 프로그램이 요청을 받아(기다려 받기) 화면 위에 확인을 묻고 칸을 채움 → 결과를 알려 줌
- **저장·제출 단추는 절대 누르지 않음.** 채운 칸은 노랗게 표시하고, 저장은 선생님이 확인하고 직접.
- 모든 것은 이 PC 안 (data/desk.db 의 meta, 업무 데스크 ↔ 같은 PC 의 브라우저).
"""

import re
import secrets
import threading
from datetime import datetime

MAX_FORMS = 30
MAX_FIELDS = 300
MAX_JOBS = 20
MAX_WAIT = 25          # 확장 프로그램이 요청을 기다리는 최대 초
TYPES = {"text", "textarea", "select", "checkbox", "radio", "date", "number", "editable", "other"}


class FillError(ValueError):
    """화면에 그대로 보여 줄 한국어 메시지."""


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _text(v, limit):
    return " ".join(str(v or "").split())[:limit]


def clean_field(f):
    """확장 프로그램이 보낸 칸 하나 → 저장할 모양 (값은 받지 않음)."""
    if not isinstance(f, dict):
        return None
    key = str(f.get("key") or "").strip()[:300]
    if not key:
        return None
    t = f.get("type") if f.get("type") in TYPES else "other"
    opts = f.get("options") if isinstance(f.get("options"), list) else []
    return {"key": key, "label": _text(f.get("label"), 80) or key[:40], "type": t,
            "options": [_text(o, 80) for o in opts[:100] if _text(o, 80)]}


def clean_path(path):
    path = str(path or "")[:300]
    return path if path.startswith("/") else "/" + path


class NeisFill:
    def __init__(self, db):
        self.db = db
        self.cond = threading.Condition()

    # ---- 양식 (입력 칸 구조)
    def forms(self):
        return self.db.get_meta("neis_forms") or []

    def save_form(self, host, frame_path, title, fields):
        host = _text(host, 120).lower()
        if not re.fullmatch(r"([a-z0-9-]+\.)*neis\.go\.kr", host):
            raise FillError("나이스(neis.go.kr) 화면의 구조만 받습니다")
        clean = [c for c in (clean_field(f) for f in (fields or [])[:MAX_FIELDS]) if c]
        if not clean:
            raise FillError("이 화면에서 입력 칸을 찾지 못했습니다")
        frame_path = clean_path(frame_path)
        forms = self.forms()
        old = next((f for f in forms if f["host"] == host and f["frame_path"] == frame_path), None)
        form = {"id": old["id"] if old else secrets.token_hex(4), "host": host, "frame_path": frame_path,
                "title": _text(title, 80) or frame_path.rsplit("/", 1)[-1], "fields": clean, "captured_at": now_str()}
        forms = [form] + [f for f in forms if f is not old]
        self.db.set_meta("neis_forms", forms[:MAX_FORMS])
        return form

    def delete_form(self, form_id):
        self.db.set_meta("neis_forms", [f for f in self.forms() if f["id"] != form_id])

    # ---- 채우기 요청
    def jobs(self):
        return self.db.get_meta("neis_jobs") or []

    def _save_jobs(self, jobs):
        self.db.set_meta("neis_jobs", jobs[:MAX_JOBS])

    def request(self, form_id, values):
        """선생님이 정한 값으로 채우기 요청 (비워 둔 칸은 건드리지 않음)."""
        form = next((f for f in self.forms() if f["id"] == form_id), None)
        if not form:
            raise FillError("없는 양식입니다. 나이스 화면에서 입력 칸 구조를 다시 보내 주세요")
        if not isinstance(values, dict):
            raise FillError("값이 잘못되었습니다")
        entries = []
        for f in form["fields"]:
            v = values.get(f["key"])
            if v is None or (isinstance(v, str) and not v.strip()):
                continue
            if f["type"] in ("checkbox", "radio"):
                v = bool(v) if not isinstance(v, str) else v.strip() not in ("", "0", "false", "아니오")
            else:
                v = str(v)[:4000]
            entries.append({"key": f["key"], "label": f["label"], "type": f["type"], "value": v})
        if not entries:
            raise FillError("채울 값을 하나 이상 넣으세요")
        job = {"id": secrets.token_hex(4), "form_id": form_id, "title": form["title"], "host": form["host"],
               "frame_path": form["frame_path"], "entries": entries, "status": "pending",
               "created_at": now_str(), "result": None}
        with self.cond:
            # 아직 안 가져간 예전 요청은 새 요청으로 바꿈 (한 번에 하나만 기다리게)
            self._save_jobs([job] + [j for j in self.jobs() if j["status"] != "pending"])
            self.cond.notify_all()
        return job

    def cancel(self, job_id):
        with self.cond:
            self._save_jobs([j for j in self.jobs() if not (j["id"] == job_id and j["status"] == "pending")])

    def take(self, wait=0):
        """확장 프로그램이 가져갈 요청 (없으면 wait 초까지 기다림). 가져가면 'taken'."""
        wait = max(0, min(float(wait or 0), MAX_WAIT))
        with self.cond:
            job = self._next()
            if job is None and wait:
                self.cond.wait(wait)
                job = self._next()
            if job is None:
                return None
            jobs = self.jobs()
            for j in jobs:
                if j["id"] == job["id"]:
                    j["status"], j["taken_at"] = "taken", now_str()
            self._save_jobs(jobs)
            return {**job, "status": "taken"}

    def _next(self):
        return next((j for j in self.jobs() if j["status"] == "pending"), None)

    def report(self, job_id, result):
        """확장 프로그램이 알려 준 결과: 채운 칸 수, 못 찾은 칸 이름, 오류(선생님이 취소한 것 포함)."""
        if not isinstance(result, dict):
            raise FillError("결과가 잘못되었습니다")
        jobs = self.jobs()
        job = next((j for j in jobs if j["id"] == job_id), None)
        if not job:
            raise FillError("없는 요청입니다")
        missing = [_text(m, 80) for m in (result.get("missing") or [])[:MAX_FIELDS]]
        error = _text(result.get("error"), 200) or None
        try:
            filled = max(0, int(result.get("filled") or 0))
        except (TypeError, ValueError):
            filled = 0
        job["status"] = "failed" if error or not filled else "done"
        job["result"] = {"filled": filled, "missing": missing, "error": error, "at": now_str()}
        self._save_jobs(jobs)
        return job
