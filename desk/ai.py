"""브리핑의 '지난 1주일 AI 요약': 이 PC 에서 도는 LM Studio(로컬 AI)로 쿨메신저 쪽지·업무포털 공문을 요약.

- LM Studio 의 '로컬 서버'(Developer → Start Server, 기본 http://127.0.0.1:1234/v1)를 씁니다.
  OpenAI 와 같은 모양의 주소(/models, /chat/completions)라 표준 라이브러리만으로 부릅니다.
- 쪽지·공문은 이 PC 밖으로 나가면 안 되므로 주소가 이 PC(127.0.0.1·localhost)일 때만 보냅니다.
- 학생 명단의 이름은 보내기 전에 '○○○' 로 가립니다.
"""

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from . import extract

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
DAYS = 7
MAX_ITEMS = 60
PER_ITEM_CHARS = 700      # 한 건에서 보내는 글자 수 (앞부분)
TOTAL_CHARS = 14000       # 한 번에 보내는 글자 수 (작은 모델의 기억 길이에 맞춤)
# 설정의 '보내는 양': 짧게 하면 LM Studio 가 읽는 시간이 크게 줄어듦 (8845HS 내장 그래픽 12B 모델 약 70토큰/초)
SIZES = {"normal": (700, 14000), "short": (400, 6000)}
TIMEOUT = 900             # LM Studio 답을 기다리는 최대 시간 (15분)
SOURCE_NAMES = {"cool": "쿨메신저", "edufine": "업무포털"}

SYSTEM = (
    "너는 한국 중·고등학교 담임 교사의 업무 비서다. 지난 1주일 동안 받은 쿨메신저 쪽지와 업무포털 공문을 읽고 "
    "교사가 1분 안에 파악할 수 있게 한국어로 요약한다. 자료에 없는 내용은 지어내지 않는다."
)
INSTRUCTION = """오늘은 {today}이다. 아래 자료를 다음 형식으로만 요약해 줘 (앞뒤 인사말 없이). 해당하는 것이 없는 칸은 '- 없음'.

## 꼭 할 일 (마감 순)
- 10/30(금) · 할 일 [n]
## 다가오는 행사·회의
- 11/04(화) 13:30 · 내용 [n]
## 알아 둘 내용
- 내용 [n]

규칙:
- 한 줄은 35자 안쪽으로 짧게, 명사형으로 끝내기 (예: '협의록 제출', '교과서 수량 확인')
- 날짜는 '10/28(수)' 모양으로 줄 맨 앞에, 날짜 다음에 ' · '. 날짜 없는 할 일은 '상시 · '
- 꼭 할 일은 최대 10개, 행사·회의는 최대 6개, 알아 둘 내용은 중요한 것만 최대 8개
- 같은 일은 한 줄로 합치고 자료 번호를 [3, 31] 처럼 함께 적기
- 이미 지난 마감은 빼기

자료:
"""
WEEKDAYS = "월화수목금토일"


class AIError(Exception):
    """화면에 그대로 보여 줄 한국어 메시지."""


def check_local(base):
    host = (urllib.parse.urlparse(base).hostname or "").lower()
    if host not in LOCAL_HOSTS:
        raise AIError("AI 주소는 이 PC(127.0.0.1)만 쓸 수 있습니다. 쪽지·공문이 다른 컴퓨터로 나가지 않게 하기 위해서입니다.")


def _call(base, path, payload=None, timeout=10):
    check_local(base)
    url = base.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    # 이 PC 로만 보내므로 프록시 설정을 거치지 않음
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error")
            msg = msg.get("message") if isinstance(msg, dict) else msg
        except Exception:
            msg = None
        raise AIError(f"LM Studio 오류 ({e.code}): {msg or e.reason}")
    except (urllib.error.URLError, OSError) as e:
        if "timed out" in str(e).lower():
            raise AIError("LM Studio 의 답이 너무 늦습니다. 더 작은 모델을 불러오거나 잠시 뒤 다시 해 보세요.")
        raise AIError("LM Studio 에 연결할 수 없습니다. LM Studio 를 켜고 Developer → 'Start Server' 를 눌러 주세요.")
    except ValueError:
        raise AIError("LM Studio 의 답을 읽을 수 없습니다")


def models(base):
    """불러온(또는 받아 둔) 대화용 모델 이름들."""
    data = _call(base, "/models").get("data") or []
    return [m["id"] for m in data if isinstance(m, dict) and m.get("id") and "embed" not in m["id"].lower()]


def check(base):
    names = models(base)
    if not names:
        raise AIError("LM Studio 에 불러온 모델이 없습니다. LM Studio 에서 모델을 하나 불러오세요.")
    return {"ok": True, "models": names}


def mask_names(text, names):
    for n in sorted({n for n in names if len(n) >= 2}, key=len, reverse=True):
        text = text.replace(n, "○" * len(n))
    return text


def week_items(db, today=None, days=DAYS):
    today = today or date.today()
    since = (today - timedelta(days=days - 1)).isoformat()
    return db.query(
        "SELECT id, source, title, body, received_at FROM items "
        "WHERE source IN ('cool', 'edufine') AND received_at >= ? AND status != 'archived' "
        "ORDER BY received_at DESC LIMIT ?", (since, MAX_ITEMS))


NEW_INTRO = "아래는 마지막 요약 뒤에 새로 들어온 자료뿐이다. 이것만 요약해 줘."
MAX_UPDATES = 10          # 하루 동안 덧붙인 '새로 들어온 것' 요약 (전체 다시 요약하면 비움)
MAX_NEW_ITEMS = 30


def build_prompt(items, student_names=(), size="normal", today=None, new=False):
    """자료 목록 → (보낼 글, 실제로 넣은 개수)."""
    per_item, limit = SIZES.get(size, SIZES["normal"])
    parts, used, total = [], 0, 0
    for n, it in enumerate(items, 1):
        body = re.sub(r"\s+", " ", it["body"]).strip()[:per_item]
        part = (f"[{n}] {SOURCE_NAMES.get(it['source'], it['source'])} · {it['received_at'][:16]} · "
                f"{it['title']}\n{body}\n")
        if total + len(part) > limit and parts:
            break
        parts.append(part)
        total += len(part)
        used += 1
    d = today or date.today()
    head = INSTRUCTION.replace("{today}", f"{d.isoformat()}({WEEKDAYS[d.weekday()]})")
    if new:
        head = head.replace("아래 자료를 다음 형식으로만 요약해 줘", NEW_INTRO + " 다음 형식으로만 쓰고")
    return mask_names(head + "\n".join(parts), student_names), used


def last_item_id(db):
    """지금까지 들어온 쿨메신저·업무포털 자료 중 가장 큰 번호 (요약 뒤 새 자료를 가리는 기준)."""
    row = db.one("SELECT MAX(id) AS n FROM items WHERE source IN ('cool', 'edufine')")
    return row["n"] or 0


def seen_id(db, result):
    """이 요약(과 덧붙인 요약)이 본 마지막 자료 번호. 0.5.8 전 요약은 만든 시각으로 짐작."""
    if not result:
        return None
    if "seen_id" in result:
        return result["seen_id"]
    row = db.one("SELECT MAX(id) AS n FROM items WHERE source IN ('cool', 'edufine') AND created_at <= ?",
                 (result.get("at", "") + ":59",))
    return row["n"] or 0


def new_items(db, result, limit=MAX_NEW_ITEMS):
    """요약 뒤에 새로 들어온 쿨메신저·업무포털 자료 (보관한 것 빼고)."""
    since = seen_id(db, result)
    if since is None:
        return []
    return db.query(
        "SELECT id, source, title, body, received_at FROM items "
        "WHERE source IN ('cool', 'edufine') AND id > ? AND status != 'archived' ORDER BY id DESC LIMIT ?",
        (since, limit))


def pick_model(config):
    model = config["ai_model"] or (models(config["ai_base"]) or [None])[0]
    if not model:
        raise AIError("LM Studio 에 불러온 모델이 없습니다. LM Studio 에서 모델을 하나 불러오세요.")
    return model


def weekly_summary(db, config, today=None, timeout=TIMEOUT):
    """지난 1주일 요약을 만들어 저장하고 돌려줌 {at, text, count, used, model, days, ids, seen_id}."""
    base = config["ai_base"]
    check_local(base)
    top = last_item_id(db)
    items = week_items(db, today)
    result = {"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "count": len(items), "used": 0,
              "model": "", "days": DAYS, "text": "", "seen_id": top, "updates": []}
    if not items:
        result["text"] = "지난 1주일 동안 받은 쿨메신저 쪽지·업무포털 공문이 없습니다."
        db.set_meta("ai_weekly", result)
        return result
    model = pick_model(config)
    prompt, used = build_prompt(items, [s["name"] for s in db.students()], config["ai_input_size"], today)
    text = ask(base, model, prompt, timeout)
    result.update(text=text, used=used, model=model, ids=[it["id"] for it in items[:used]])
    result["applied"] = apply_summary(db, text, result["ids"], today)
    db.set_meta("ai_weekly", result)
    db.log(f"AI 요약: 지난 {DAYS}일 자료 {used}건 ({model})")
    return result


def update_summary(db, config, today=None, timeout=TIMEOUT):
    """요약 뒤 새로 들어온 자료만 요약해 '🆕 새로 들어온 것'으로 덧붙임 (전체 요약이 없으면 전체)."""
    check_local(config["ai_base"])
    result = db.get_meta("ai_weekly")
    if not result or not result.get("text"):
        return weekly_summary(db, config, today, timeout)
    top = last_item_id(db)
    items = new_items(db, result)
    if not items:
        return result
    model = pick_model(config)
    prompt, used = build_prompt(items, [s["name"] for s in db.students()], config["ai_input_size"], today, new=True)
    text = ask(config["ai_base"], model, prompt, timeout)
    update = {"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "text": text, "model": model,
              "count": len(items), "used": used, "ids": [it["id"] for it in items[:used]]}
    update["applied"] = apply_summary(db, text, update["ids"], today)
    result["updates"] = ([update] + result.get("updates", []))[:MAX_UPDATES]
    result["seen_id"] = top
    db.set_meta("ai_weekly", result)
    db.log(f"AI 요약: 새로 들어온 자료 {used}건 덧붙임 ({model})")
    return result


# ---------------------------------------------------------------- 요약으로 분류하기
# 요약 줄 '- 10/30(금) · 할 일 [3, 7]' 을 읽어, 그 자료(메시지·공문)의 분류와 일정 후보를 고침.
# AI 에게 따로 묻지 않으므로 시간이 더 들지 않음. 달력에 넣은(체크한) 항목은 건드리지 않음.
P_HEAD = re.compile(r"^#{1,4}\s*(.+)")
P_ROW = re.compile(r"^[-*•·]\s*(.+)$")
P_REFS = re.compile(r"\[(\d+(?:\s*[,，]\s*\d+)*)\]")
P_MD = re.compile(r"(\d{1,2})\s*[/.월]\s*(\d{1,2})")


def parse_summary(text):
    """요약 글 → [(칸, 날짜 글, 내용, [자료 번호])]. 칸: todo / event / note."""
    rows, kind = [], None
    for raw in text.splitlines():
        line = raw.replace("**", "").strip()
        head = P_HEAD.match(line)
        if head:
            h = head.group(1)
            kind = "todo" if re.search(r"할\s*일|마감", h) else "event" if re.search(r"행사|회의|일정", h) else "note"
            continue
        m = P_ROW.match(line)
        if not m or not kind:
            continue
        body = m.group(1)
        refs = [int(n) for g in P_REFS.findall(body) for n in re.split(r"[,，]", g)]
        body = P_REFS.sub("", body).strip()
        when, _, what = body.partition(" · ")
        if not what:
            when, what = "", body
        rows.append((kind, when.strip(), what.strip(), refs))
    return rows


def row_date(when, today):
    m = P_MD.search(when)
    if not m:
        return None
    try:
        d = date(today.year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None
    if (d - today).days < -180:      # 12월에 받은 '1/5' 는 다음 해
        d = d.replace(year=d.year + 1)
    elif (d - today).days > 185:
        d = d.replace(year=d.year - 1)
    return d


def apply_summary(db, text, ids, today=None):
    """요약에 나온 날짜·칸으로 자료의 분류와 일정 후보를 맞춤 → {'added', 'changed', 'items'}."""
    today = today or date.today()
    added = changed = 0
    touched = set()
    for kind, when, what, refs in parse_summary(text):
        if kind == "note":
            continue
        d = row_date(when, today)
        ev_kind = "deadline" if kind == "todo" else "event"
        category = "제출·마감" if kind == "todo" else (extract.classify_line(what, False) or "행사")
        for n in refs:
            if not (1 <= n <= len(ids)):
                continue
            item = db.one("SELECT id, registered FROM items WHERE id = ?", (ids[n - 1],))
            if not item or item["registered"]:
                continue   # 달력에 넣은 항목은 선생님이 확인한 것이므로 그대로
            if item["id"] not in touched:
                db.execute("UPDATE items SET category = ? WHERE id = ?", (category, item["id"]))
                touched.add(item["id"])
            if not d:
                continue
            t = extract.find_time(when + " " + what)
            same = db.one("SELECT id, kind, time FROM events WHERE item_id = ? AND date = ? ORDER BY id LIMIT 1",
                          (item["id"], d.isoformat()))
            if same:
                # 마감은 일정으로 낮추지 않음 (같은 일이 '할 일'과 '행사'에 함께 나오기도 함)
                to_deadline = ev_kind == "deadline" and same["kind"] != "deadline"
                if to_deadline:
                    db.execute("UPDATE events SET kind = 'deadline', category = ?, time = COALESCE(time, ?) WHERE id = ?",
                               (category, t, same["id"]))
                    changed += 1
                elif t and not same["time"]:
                    db.execute("UPDATE events SET time = ? WHERE id = ?", (t, same["id"]))
                    changed += 1
            else:
                db.execute("INSERT INTO events(item_id, date, time, kind, category, text, source) "
                           "VALUES(?, ?, ?, ?, ?, ?, 'ai')", (item["id"], d.isoformat(), t, ev_kind, category, what[:160]))
                added += 1
    return {"added": added, "changed": changed, "items": len(touched)}


def ask(base, model, prompt, timeout=TIMEOUT):
    """LM Studio 에 요약을 부탁하고 글만 돌려줌."""
    if "qwen" in model.lower():
        prompt += "\n/no_think"   # Qwen3: 생각 단계 끄기
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "temperature": 0.2, "max_tokens": 2500, "stream": False,
    }
    try:
        # 요약에는 '생각' 단계가 필요 없어 끔 (이 칸을 아는 모델만 따름)
        res = _call(base, "/chat/completions", {**payload, "chat_template_kwargs": {"enable_thinking": False}},
                    timeout=timeout)
    except AIError as e:
        if not str(e).startswith("LM Studio 오류 (400"):
            raise
        res = _call(base, "/chat/completions", payload, timeout=timeout)  # 그 칸을 받지 않는 LM Studio
    try:
        msg = res["choices"][0]["message"]
        text = msg.get("content") or ""
        finish = res["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        raise AIError("LM Studio 의 답에 요약이 없습니다")
    # 생각 과정(<think>…</think>)을 내보내는 모델은 그 부분을 뺌
    text = re.sub(r"(?is)<think>.*?(</think>|$)", "", text).strip()
    if not text:
        raise AIError("AI 가 생각만 하다가 요약을 쓰지 못했습니다" + (" (답 길이 한도)" if finish == "length" else "")
                      + ". LM Studio 모델 설정에서 Reasoning Budget 을 줄이거나 끄세요.")
    return text


class Job:
    """요약은 몇 분 걸리므로 뒤에서 만들고, 화면은 몇 초마다 상태만 물어봄."""

    def __init__(self, db, config):
        self.db, self.config = db, config
        self.lock = threading.Lock()
        self.thread = None
        self.started_at = None
        self.error = None
        self.kind = None   # full: 전체 다시 요약 / new: 새로 들어온 것만

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self, kind="full"):
        with self.lock:
            if self.running:
                return False
            self.error = None
            self.kind = "new" if kind == "new" else "full"
            self.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.thread = threading.Thread(target=self._run, name="ai-summary", daemon=True)
            self.thread.start()
            return True

    def _run(self):
        try:
            (update_summary if self.kind == "new" else weekly_summary)(self.db, self.config)
        except AIError as e:
            self.error = str(e)
        except Exception as e:  # 뒤에서 도는 일이 조용히 죽지 않게
            self.error = f"요약 중 오류: {type(e).__name__}"
            self.db.log(f"AI 요약 오류: {type(e).__name__}: {e}", "error")

    def state(self):
        result = self.db.get_meta("ai_weekly")
        return {"enabled": bool(self.config["ai_enabled"]), "running": self.running,
                "kind": self.kind if self.running else None,
                "started_at": self.started_at if self.running else None, "error": self.error,
                "result": result, "timeout": TIMEOUT, "hidden": self.db.get_meta("ai_hidden", []),
                "new_count": len(new_items(self.db, result)) if result and result.get("text") else 0}


MAX_HIDDEN = 300


def set_hidden(db, key, hide=True):
    """요약에서 지운 줄 (다시 요약해도 같은 줄은 숨김). key 는 화면이 만든 '칸|날짜|내용'."""
    keys = [k for k in db.get_meta("ai_hidden", []) if k != key]
    if hide:
        keys.append(key)
    db.set_meta("ai_hidden", keys[-MAX_HIDDEN:])
    return keys[-MAX_HIDDEN:]
