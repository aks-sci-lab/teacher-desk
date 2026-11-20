"""브리핑의 '지난 1주일 AI 요약': 이 PC 에서 도는 LM Studio(로컬 AI)로 쿨메신저 쪽지·업무포털 공문을 요약.

- LM Studio 의 '로컬 서버'(Developer → Start Server, 기본 http://127.0.0.1:1234/v1)를 씁니다.
  OpenAI 와 같은 모양의 주소(/models, /chat/completions)라 표준 라이브러리만으로 부릅니다.
- 쪽지·공문은 이 PC 밖으로 나가면 안 되므로 주소가 이 PC(127.0.0.1·localhost)일 때만 보냅니다.
- 학생 명단의 이름은 보내기 전에 '○○○' 로 가립니다.
"""

import base64
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import hashlib
import urllib.request
from datetime import date, datetime, timedelta

from . import applog, coolmsg, extract, visits

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
DAYS = 7
MAX_ITEMS = 60
PER_ITEM_CHARS = 700      # 한 건에서 보내는 글자 수 (앞부분)
TOTAL_CHARS = 14000       # 한 번에 보내는 글자 수 (작은 모델의 기억 길이에 맞춤)
# 설정의 '보내는 양': 짧게 하면 LM Studio 가 읽는 시간이 크게 줄어듦 (8845HS 내장 그래픽 12B 모델 약 70토큰/초)
SIZES = {"normal": (700, 14000), "short": (400, 6000)}
TIMEOUT = 900             # LM Studio 답을 기다리는 최대 시간 (15분)
IDLE_TTL = 300            # 작업 중이 아닌 모델을 메모리에서 내리기까지의 시간
SOURCE_NAMES = {"cool": "쿨메신저", "edufine": "업무포털"}

SYSTEM = (
    "너는 한국 중·고등학교 담임 교사의 업무 비서다. 교사가 지난번 업무 데스크를 본 뒤(또는 지난 1주일 동안) 받은 쿨메신저 쪽지와 업무포털 공문을 읽고 "
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


def check(base, db=None):
    names = models(base)
    if not names:
        raise AIError("LM Studio 에 불러온 모델이 없습니다. LM Studio 에서 모델을 하나 불러오세요.")
    return {"ok": True, "models": names, "ttl_unsupported": ttl_unsupported(base, db)}


def mask_names(text, names):
    for n in sorted({n for n in names if len(n) >= 2}, key=len, reverse=True):
        text = text.replace(n, "○" * len(n))
    return text


def week_items(db, today=None, days=DAYS, since=None):
    """요약할 자료: since('YYYY-MM-DD HH:MM', 마지막으로 닫은 이후)가 있으면 그 뒤, 없으면 지난 days 일."""
    today = today or date.today()
    since = since or (today - timedelta(days=days - 1)).isoformat()
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


def weekly_summary(db, config, today=None, timeout=TIMEOUT, since=None):
    """요약을 만들어 저장하고 돌려줌 {at, text, count, used, model, days, ids, seen_id, range, since}.
    since 가 있으면 '마지막으로 닫은 이후'(range=visit), 없으면 '지난 1주일'(range=week)."""
    base = config["ai_base"]
    check_local(base)
    top = last_item_id(db)
    items = week_items(db, today, since=since)
    result = {"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "count": len(items), "used": 0,
              "model": "", "days": DAYS, "text": "", "seen_id": top, "updates": [],
              "range": "visit" if since else "week", "since": since}
    if not items:
        result["text"] = (f"{since} 이후 새로 받은 쿨메신저 쪽지·업무포털 공문이 없습니다." if since
                          else "지난 1주일 동안 받은 쿨메신저 쪽지·업무포털 공문이 없습니다.")
        db.set_meta("ai_weekly", result)
        return result
    model = pick_model(config)
    prompt, used = build_prompt(items, [s["name"] for s in db.students()], config["ai_input_size"], today)
    text = ask(base, model, prompt, timeout, db=db)
    result.update(text=text, used=used, model=model, ids=[it["id"] for it in items[:used]])
    result["applied"] = apply_summary(db, text, result["ids"], today)
    db.set_meta("ai_weekly", result)
    db.log(f"AI 요약: {since + ' 이후' if since else f'지난 {DAYS}일'} 자료 {used}건 ({model})")
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
    text = ask(config["ai_base"], model, prompt, timeout, db=db)
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


_ttl_refused = set()   # 이번 실행 중 '자동 해제(ttl)'를 거부한 서버 주소 (db 가 없는 호출용)
TTL_META = "ai_ttl_unsupported"


def ttl_unsupported(base, db=None):
    """이 서버 주소가 자동 해제(ttl)를 받지 않는다고 알려졌는지."""
    key = (base or "").rstrip("/")
    if key in _ttl_refused:
        return True
    return bool(db and db.get_meta(TTL_META) == key)


def _note_ttl_refused(base, db):
    key = (base or "").rstrip("/")
    _ttl_refused.add(key)
    if db is not None and db.get_meta(TTL_META) != key:
        db.set_meta(TTL_META, key)   # 같은 주소면 다음 요청부터 ttl 을 보내지 않음 (주소가 바뀌면 다시 시도)
        db.log("AI 서버가 자동 해제(ttl)를 지원하지 않아 모델이 메모리에 계속 남습니다 (" + key + ")", "warn")


def ask(base, model, prompt, timeout=TIMEOUT, system=SYSTEM, max_tokens=2500, db=None, images=()):
    """LM Studio 에 요약(또는 텔레그램 질문의 답)을 부탁하고 글만 돌려줌.
    images: 함께 보낼 사진 바이트(jpg·png) — 사진을 읽는 모델(예: Gemma 4)만 (0.6.11 텔레그램 참고자료 사진).
    0.6.15 사용 기록: 모델·글자 수·걸린 시간·부른 곳만 남김 (보낸 글·답은 남기지 않음)."""
    with applog.timed("ai", "LM Studio 요청", model=model, chars_in=len(prompt), images=len(images) or None,
                      timeout=timeout, **{"from": applog.caller({"ask", "_ask_lm", "<lambda>"})}) as extra:
        text = _ask_lm(base, model, prompt, timeout, system, max_tokens, db, images)
        extra["chars_out"] = len(text)
        return text


def _ask_lm(base, model, prompt, timeout, system, max_tokens, db, images):
    if "qwen" in model.lower():
        prompt += "\n/no_think"   # Qwen3: 생각 단계 끄기
    content = prompt
    if images:
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": f"data:{image_type(b)};base64,{base64.b64encode(b).decode()}"}}
            for b in images]
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "temperature": 0.2, "max_tokens": max_tokens, "stream": False,
    }
    # 요약에는 '생각' 단계가 필요 없어 끔 (이 칸을 아는 모델만 따름)
    # 0.6.7: Gemma 4 는 LM Studio 에서 enable_thinking 을 무시하고 reasoning_effort "none" 만 따름
    #   (학교 PC 실측: 생각 글 1500자·27초 → 0자·4초). 둘 다 보내고, 400 이면 둘 다 빼고 다시.
    think = {"chat_template_kwargs": {"enable_thinking": False}, "reasoning_effort": "none"}
    ttl = {"ttl": IDLE_TTL}
    # 400(모르는 칸)이면 칸을 하나씩 빼며 다시 시도: (생각끄기, ttl) 조합
    tries = [(True, True), (False, True), (True, False), (False, False)]
    if ttl_unsupported(base, db):
        tries = [(True, False), (False, False)]
    res = None
    for i, (use_think, use_ttl) in enumerate(tries):
        body = {**payload, **(think if use_think else {}), **(ttl if use_ttl else {})}
        try:
            res = _call(base, "/chat/completions", body, timeout=timeout)
        except AIError as e:
            if not str(e).startswith("LM Studio 오류 (400") or i == len(tries) - 1:
                raise   # 다른 이유의 400(모델 없음 등)은 그대로 보임
            continue
        if not use_ttl and (True, True) == tries[0] and i > 0:
            _note_ttl_refused(base, db)   # ttl 을 뺀 요청만 받아들였음 = ttl 거부
        break
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


IMAGE_SYSTEM = "너는 사진 속 글자를 정확히 옮겨 적는 도우미다. 사진에 없는 내용은 쓰지 않는다."
IMAGE_PROMPT = ("이 사진 속 글을 줄 바꿈을 살려 그대로 옮겨 적어 줘. 표는 '| 칸 | 칸 |' 모양으로. "
                "글이 거의 없으면 사진 내용을 세 줄 안으로 설명해 줘. 앞뒤 인사말 없이.")


def image_type(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def read_image(base, model, data, names=(), db=None, timeout=TIMEOUT):
    """사진 속 글을 이 PC 의 AI(LM Studio)로 옮겨 적음. 학생 이름은 답에서 가림. 사진을 못 읽는 모델이면 AIError."""
    check_local(base)
    try:
        text = ask(base, model, IMAGE_PROMPT, timeout=timeout, system=IMAGE_SYSTEM, max_tokens=1500, db=db, images=[data])
    except AIError as e:
        if str(e).startswith("LM Studio 오류 (4"):
            raise AIError("지금 불러온 AI 모델이 사진을 읽지 못합니다 (사진을 읽는 모델, 예: Gemma 4 가 필요합니다)")
        raise
    return mask_names(text, names)


class Job:
    """요약은 몇 분 걸리므로 뒤에서 만들고, 화면은 몇 초마다 상태만 물어봄."""

    def __init__(self, db, config):
        self.db, self.config = db, config
        self.lock = threading.Lock()
        self.thread = None
        self.started_at = None
        self.error = None
        self.kind = None   # full: 전체 다시 요약 / new: 새로 들어온 것만
        self.since = None  # 마지막으로 닫은 이후로 요약할 때 시작 시각

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self, kind="full", since=None):
        """kind: full(전체 다시) / new(새로 들어온 것만). since 가 있으면 '마지막으로 닫은 이후' 범위로 전체 요약."""
        with self.lock:
            if self.running:
                return False
            self.error = None
            self.kind = "new" if kind == "new" else "full"
            self.since = since
            self.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.thread = threading.Thread(target=self._run, name="ai-summary", daemon=True)
            self.thread.start()
            return True

    def _run(self):
        try:
            if self.kind == "new":
                update_summary(self.db, self.config)
            else:
                weekly_summary(self.db, self.config, since=self.since)
        except AIError as e:
            self.error = str(e)
        except Exception as e:  # 뒤에서 도는 일이 조용히 죽지 않게
            self.error = f"요약 중 오류: {type(e).__name__}"
            applog.exception("ai", "AI 요약", e)
            self.db.log(f"AI 요약 오류: {type(e).__name__}: {e}", "error")

    def state(self):
        result = self.db.get_meta("ai_weekly")
        return {"enabled": bool(self.config["ai_enabled"]), "running": self.running,
                "kind": self.kind if self.running else None,
                "started_at": self.started_at if self.running else None, "error": self.error,
                "result": result, "timeout": TIMEOUT, "hidden": self.db.get_meta("ai_hidden", []),
                "new_count": len(new_items(self.db, result)) if result and result.get("text") else 0,
                "visit": visits.info(self.db),
                "ttl_unsupported": ttl_unsupported(self.config["ai_base"], self.db)}


ITEM_SYSTEM = ("너는 한국 중·고등학교 담임 교사의 업무 비서다. 쪽지나 공문 한 건을 읽고 교사가 10초 안에 파악하게 "
               "한국어로 요약한다. 자료에 없는 내용은 지어내지 않고, 날짜·숫자는 원문 그대로 쓴다.")
ITEM_INSTRUCTION = """아래 자료 한 건을 이 형식으로만 요약해 줘 (앞뒤 인사말 없이, 없는 칸은 줄 자체를 빼기).

한 줄 요약: (40자 안쪽)
- 해야 할 일: 
- 날짜·시간·장소: 
- 대상·제출 방법: 

자료:
"""
ITEM_CHARS = 3500

# 0.6.8 쿨메신저 쪽지는 선생님이 정한 다섯 칸으로 요약 (선생님 요청 2026-11-17, 텔레그램에도 이 모양으로 보냄)
COOL_INSTRUCTION = """아래 쿨메신저 쪽지 한 건을 이 다섯 줄 형식으로만 요약해 줘 (앞뒤 인사말 없이, 다섯 줄 모두 쓰기).

보낸 사람: (쪽지에 적힌 이름·부서, 모르면 '모름')
업무 내용: (교사가 할 일이나 알아 둘 내용만 60자 안쪽. 쪽지 첫 줄·인사말·보낸 사람을 되풀이하지 말 것)
마감 기한: (날짜·시간을 원문 그대로, 없으면 '없음')
첨부파일: (쪽지에 적힌 파일 이름을 쉼표로, 없으면 '없음')
긴급 여부: (긴급 또는 보통. '긴급'·'급함'·'즉시'·'오늘까지'처럼 서두르라는 말이 있거나 마감이 오늘·내일이면 긴급)

자료:
"""
COOL_KEYS = (("sender", "보낸 사람"), ("task", "업무 내용"), ("due", "마감 기한"), ("files", "첨부파일"), ("urgent", "긴급 여부"))
P_COOL_FIELD = re.compile(r"^\s*[-*•·]?\s*\**\s*(보낸\s*사람|업무\s*내용|마감\s*기한|첨부\s*파일(?:\s*목록)?|긴급\s*여부)\s*\**\s*[:：]\s*(.*)$")
P_BODY_SENDER = re.compile(r"\(보낸 사람:\s*(.+?)\)\s*$")
P_FILE_NAME = re.compile(r"[^\s\\/:*?\"<>|,()\[\]]+\.(?:hwpx?|pdf|xlsx?|docx?|pptx?|zip|txt|csv|jpe?g|png)\b", re.I)
URGENT_WORDS = ("긴급", "급함", "급히", "즉시", "시급")
NONE_WORDS = ("없음", "없다", "모름", "해당 없음", "-", "")


def cool_fields(text, body=""):
    """쪽지 AI 요약 → {'sender','task','due','files','urgent'} (다섯 칸 중 둘 이상 읽혔을 때만, 아니면 None).

    - 보낸 사람은 쪽지 끝의 '(보낸 사람: …)' 가 있으면 그것을 씀 (AI 가 틀리게 옮기지 않게).
    - 첨부파일은 쪽지 글에 '(첨부 파일: …)' 줄(쿨메신저의 실제 첨부 목록)이 있으면 그것.
      없으면 AI 가 '없음' 이라 했어도 본문에 파일 이름(…hwp, …pdf 등)이 보이면 그 이름.
    - 긴급 여부는 '긴급' 또는 '보통' 두 가지로만.
    """
    found = {}
    names = dict((label.replace(" ", ""), key) for key, label in COOL_KEYS)
    for line in (text or "").splitlines():
        m = P_COOL_FIELD.match(line)
        if not m:
            continue
        label = re.sub(r"\s+", "", m.group(1)).replace("목록", "")
        key = names.get(label)
        if key and key not in found:
            found[key] = m.group(2).strip().strip("*").strip()
    if len(found) < 2:   # 0.6.14 칸 이름 없이 값만 다섯 줄로 답한 경우 (학교 PC Gemma 4, 2026-11-20): 순서대로 읽음
        values = [re.sub(r"^\s*(?:[-*•·]|\d+[.)])\s*", "", l).strip().strip("*").strip() for l in (text or "").splitlines()]
        values = [v for v in values if v]
        if len(values) != len(COOL_KEYS) or not re.search(r"긴급|보통|급", values[-1]):
            return None
        found = {key: v for (key, _), v in zip(COOL_KEYS, values)}
    out = {}
    for key, _ in COOL_KEYS:
        v = found.get(key, "")
        out[key] = "" if v.strip(" .'\"") in NONE_WORDS else v
    m = P_BODY_SENDER.search(body or "")
    if m:
        name = m.group(1).strip()
        same = re.fullmatch(r"(.+?)\s*\(\s*\1\s*\)", name)
        out["sender"] = (same.group(1) if same else name)[:30]
    real = coolmsg.files_in_text(body)
    if real:   # 쿨메신저가 실제로 붙인 첨부 (0.6.17): AI 가 '없음' 이라 해도, 다르게 옮겨 써도 이것으로
        out["files"] = ", ".join(real[:6]) + (f" 외 {len(real) - 6}개" if len(real) > 6 else "")
    if not out["files"]:
        files = []
        for f in P_FILE_NAME.findall(body or ""):
            if f not in files:
                files.append(f)
        out["files"] = ", ".join(files[:6])
    v = out["urgent"]
    out["urgent"] = "긴급" if any(w in v for w in URGENT_WORDS) and not re.search(r"아님|아니|않|보통", v) else "보통"
    return out

# 0.5.99 긴 자료(첨부 hwp·pdf 를 읽은 긴 공문 등)는 나눠서 요약한 뒤 합침 (예전에는 앞 3,500자만 읽었음).
# - 한 조각은 LM Studio 의 기억 길이(기본 4,096토큰)에 지시문·답과 함께 들어가는 크기.
#   한국어는 글자당 대략 1토큰이라 3,500자 + 지시문 + 답(500토큰)이 4,096토큰 안쪽.
# - 설정의 '보내는 양'을 '짧게'로 하면 조각을 2,000자로 줄여 PC 메모리(8845HS 24GB, 내장 그래픽이 같이 씀)를 덜 씀.
# - 조각 수는 최대 CHUNK_MAX 개 (한 조각 20~30초라 한 건이 몇 분 안에 끝나게). 그 뒤는 읽지 않았다고 요약 끝에 적음.
CHUNK_CHARS = {"normal": ITEM_CHARS, "short": 2000}
CHUNK_MAX = 8
CHUNK_NOTE_TOKENS = 400    # 조각 하나의 메모 답 길이
MERGE_ROUNDS = 3           # 메모가 한 번에 안 들어가면 메모끼리 다시 줄이는 최대 횟수
CHUNK_INSTRUCTION = """아래는 긴 자료 한 건을 {n}조각으로 나눈 것 중 {i}번째 조각이다. 이 조각에 있는 내용만
짧은 줄(- 로 시작)로 정리해 줘 (앞뒤 인사말 없이). 해야 할 일, 날짜·시간·장소, 대상·제출 방법, 꼭 알아 둘 내용만.
날짜·숫자는 원문 그대로 쓰고, 해당하는 내용이 없으면 '- 없음' 한 줄만.

자료 제목: {title}
조각:
"""
MERGE_INSTRUCTION = """아래는 긴 자료 한 건을 나눠 읽고 적은 메모들이다. 같은 내용은 합치고 날짜·숫자는 그대로 두어
짧은 줄(- 로 시작)로 다시 정리해 줘 (앞뒤 인사말 없이).

자료 제목: {title}
메모:
"""
FINAL_HEAD = "자료가 길어 {n}조각으로 나눠 읽은 메모를 대신 보낸다. 자료 제목: {title}\n"


def chunk_size(config):
    """설정의 '보내는 양'에 맞는 한 조각 글자 수."""
    return CHUNK_CHARS.get(config.get("ai_input_size"), ITEM_CHARS)


def split_chunks(text, size):
    """글을 size 글자 이하 조각으로: 빈 줄(문단) → 줄 → 문장 끝 → 띄어쓰기 순으로 끊을 곳을 찾음."""
    text = (text or "").strip()
    if size <= 0 or len(text) <= size:
        return [text] if text else []
    out = []
    while len(text) > size:
        window = text[:size + 1]
        cut = -1
        for pat in (r"\n\s*\n", r"\n", r"[.!?。](?=\s)", r"\s"):
            ends = [m.end() for m in re.finditer(pat, window) if m.end() >= size // 2]
            if ends:
                cut = ends[-1]
                break
        if cut <= 0:
            cut = size
        part = text[:cut].strip()
        if part:
            out.append(part)
        text = text[cut:].strip()
    if text:
        out.append(text)
    return out


def body_hash(body):
    return hashlib.sha256(" ".join((body or "").split()).encode("utf-8")).hexdigest()


def item_ai(db, it, job=None):
    """항목 화면에 줄 AI 요약 상태: 글(원문은 그대로 두고 따로)·만든 때·원문이 바뀌었는지·진행 중·대기 순번·실패."""
    fresh = bool(it.get("ai_summary")) and it.get("ai_summary_hash") == body_hash(it.get("body"))
    st = job.status(it["id"]) if job else {}
    return {"text": it.get("ai_summary") or "", "at": it.get("ai_summary_at"), "model": it.get("ai_summary_model"),
            "stale": bool(it.get("ai_summary")) and not fresh, "running": bool(st.get("running")),
            "queued": st.get("queued", 0), "error": st.get("error"), "part": st.get("part")}


def summary_line(text):
    """목록에 보일 한 줄: '한 줄 요약:'(쪽지는 '업무 내용:', 0.6.8) 뒤의 글, 없으면 첫 줄."""
    first = ""
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-*•· ").strip()
        if not line:
            continue
        m = re.match(r"\**\s*(?:한\s*줄\s*요약|업무\s*내용)\s*\**\s*[:：]\s*(.*)", line)
        if m:
            return m.group(1).strip()[:80]
        first = first or line[:80]
    return first


AUTO_SOURCES = ("cool", "edufine", "paste")   # 내가 보낸 쪽지·학사일정은 요약하지 않음
AUTO_DAYS = 7              # 자동 요약은 최근 이만큼 받은 항목만 (처음 켰을 때 옛 자료가 몰려 PC 가 오래 바쁘지 않게)
AUTO_BATCH = 30            # 한 번에 줄 세우는 최대 건수 (모자라면 다음 확인 때 이어서)
AUTO_MIN_CHARS = 40        # 이보다 짧은 글은 요약할 것이 없음
SCAN_SECONDS = 30          # 새 항목을 찾는 간격
FIRST_SCAN_SECONDS = 20    # 켠 직후 한 번
BUSY_WAIT = 3              # 브리핑 요약이 도는 중일 때 다시 확인하는 간격(초)
PAUSE_SECONDS = 300        # LM Studio 가 꺼져 있으면 이 시간 쉬었다가 다시 (오류를 쌓지 않음)
RETRY_FAILED_SECONDS = 3600
# 이 PC 의 LM Studio 자체가 준비되지 않은 오류 (항목 탓이 아님)
NOT_READY = ("LM Studio 에 연결할 수 없습니다", "LM Studio 의 답이 너무 늦습니다", "LM Studio 에 불러온 모델이 없습니다")


class ItemJob:
    """항목별 AI 요약을 뒤에서 한 건씩 만들어 저장하는 대기열 (0.5.44).

    - 눌렀거나 연 항목(start)은 맨 앞으로, 새로 들어온 항목(scan)은 자동으로 최신 순으로 줄을 섬.
    - 한 번에 한 건만 (브리핑 요약이 도는 중이면 그것이 끝날 때까지 기다림). 원문이 안 바뀌었으면 다시 만들지 않음.
    - LM Studio 가 꺼져 있으면 자동 요약은 조용히 쉬었다가 5분 뒤 다시 (눌러서 시킨 것만 오류를 보여 줌).
    - 0.5.98: 자동 요약은 gate() 가 True 일 때(내 수업 시간, aiclass.allowed)만. 아닐 때 생긴 것은 DB 에 그대로
      남아 있다가 다음 수업 교시에 찾아 요약. 수업이 끝나면 새 자동 요약은 시작하지 않음(만들던 1건은 마침).
      눌러서 시킨 것은 언제든 바로.
    """

    def __init__(self, db, config, busy=None, clock=time.monotonic, gate=None):
        self.db, self.config = db, config
        self.busy = busy or (lambda: False)
        self.gate = gate or (lambda: True)
        self.clock = clock
        self.lock = threading.Lock()
        self.queue = []            # 앞이 먼저
        self.forced = set()        # 눌러서 시킨 항목 (자동 요약이 쉬는 중에도 만듦)
        self.current = None
        self.thread = None
        self.worker_alive = False
        self.errors = {}
        self.parts = {}            # 나눠 요약하는 중인 항목의 [끝낸 조각, 모든 조각]
        self.failed = {}           # 자동 요약이 실패한 때 (한동안 다시 시키지 않음)
        self.paused_until = 0
        self.stop_event = threading.Event()
        self.auto_thread = None

    # ---- 상태
    def status(self, item_id):
        with self.lock:
            return {"running": self.current == item_id,
                    "queued": self.queue.index(item_id) + 1 if item_id in self.queue else 0,
                    "error": self.errors.get(item_id),
                    "part": self.parts.get(item_id) if self.current == item_id else None}

    def _set_part(self, item_id, done, total):
        """긴 자료를 나눠 요약하는 중: (끝낸 조각, 모든 조각) — 화면에 'n/m 조각' 으로 보임."""
        with self.lock:
            if total:
                self.parts[item_id] = [done, total]
            else:
                self.parts.pop(item_id, None)

    def summary(self):
        with self.lock:
            return {"running": self.current, "queued": len(self.queue),
                    "paused": self.clock() < self.paused_until}

    # ---- 시키기
    def start(self, item_id, force=False):
        """눌렀거나 연 항목을 맨 앞에 세움. True: 새로 줄을 섬, False: 이미 최신이거나 만드는 중."""
        it = self.db.one("SELECT id, title, body, source, ai_summary, ai_summary_hash FROM items WHERE id = ?", (item_id,))
        if not it:
            raise AIError("없는 항목입니다")
        if not force and it["ai_summary"] and it["ai_summary_hash"] == body_hash(it["body"]):
            return False
        with self.lock:
            if self.current == item_id:
                return False
            self.errors.pop(item_id, None)
            self.failed.pop(item_id, None)
            self.paused_until = 0
            if item_id in self.queue:
                self.queue.remove(item_id)
            self.queue.insert(0, item_id)
            self.forced.add(item_id)
            self._ensure_worker()
            return True

    def scan(self):
        """새로 들어온(또는 원문이 바뀐) 항목을 찾아 줄 세움. 새로 줄 선 건수."""
        enabled = bool(self.config["ai_enabled"] and self.config["ai_auto_item"])
        now = self.clock()
        if enabled and not self.gate():   # 수업 시간이 아님: 줄 세우지 않음 (다음 수업 교시에 다시 찾음)
            self._drop_auto()
            return 0
        if not enabled or now < self.paused_until:
            if not enabled:
                with self.lock:
                    self.queue = [i for i in self.queue if i in self.forced]
            return 0
        since = (date.today() - timedelta(days=AUTO_DAYS)).isoformat()
        marks = ",".join("?" * len(AUTO_SOURCES))
        rows = self.db.query(
            f"SELECT id, body, ai_summary, ai_summary_hash FROM items WHERE source IN ({marks}) "
            "AND status IN ('new', 'read') AND received_at >= ? ORDER BY received_at DESC, id DESC LIMIT 300",
            (*AUTO_SOURCES, since))
        with self.lock:
            auto = [i for i in self.queue if i not in self.forced]
            fresh = []
            for r in rows:
                if len(auto) + len(fresh) >= AUTO_BATCH:
                    break
                iid = r["id"]
                if iid in self.queue or iid == self.current or iid in fresh:
                    continue
                if iid in self.failed and now - self.failed[iid] < RETRY_FAILED_SECONDS:
                    continue
                if len((r["body"] or "").strip()) < AUTO_MIN_CHARS:
                    continue
                if r["ai_summary"] and r["ai_summary_hash"] == body_hash(r["body"]):
                    continue
                fresh.append(iid)
            if fresh:   # 새로 찾은 것을 눌러서 시킨 것 바로 뒤, 예전에 줄 선 자동 요약 앞에 (최신 순)
                head = [i for i in self.queue if i in self.forced]
                tail = [i for i in self.queue if i not in self.forced]
                self.queue = head + fresh + tail
                self._ensure_worker()
            return len(fresh)

    def start_auto(self):
        """뒤에서 새 항목을 주기적으로 찾아 요약 (켜 둔 동안)."""
        if self.auto_thread and self.auto_thread.is_alive():
            return
        self.auto_thread = threading.Thread(target=self._auto_loop, name="ai-item-auto", daemon=True)
        self.auto_thread.start()

    def stop(self):
        self.stop_event.set()

    def _auto_loop(self):
        wait = FIRST_SCAN_SECONDS
        while not self.stop_event.wait(wait):
            wait = SCAN_SECONDS
            try:
                self.scan()
            except Exception as e:   # 뒤에서 도는 일이 조용히 죽지 않게
                applog.exception("ai", "항목 AI 요약 찾기", e)
                self.db.log(f"항목 AI 요약 찾기 오류: {type(e).__name__}: {e}", "error")

    def pending(self):
        """자동 요약을 기다리는 항목 수 (수업 시간이 아니라 모아 둔 것 포함, 최대 300)."""
        if not (self.config["ai_enabled"] and self.config["ai_auto_item"]):
            return 0
        since = (date.today() - timedelta(days=AUTO_DAYS)).isoformat()
        marks = ",".join("?" * len(AUTO_SOURCES))
        rows = self.db.query(
            f"SELECT body, ai_summary, ai_summary_hash FROM items WHERE source IN ({marks}) "
            "AND status IN ('new', 'read') AND received_at >= ? LIMIT 300", (*AUTO_SOURCES, since))
        return sum(1 for r in rows if len((r["body"] or "").strip()) >= AUTO_MIN_CHARS
                   and not (r["ai_summary"] and r["ai_summary_hash"] == body_hash(r["body"])))

    def _drop_auto(self):
        with self.lock:
            self.queue = [i for i in self.queue if i in self.forced]

    # ---- 만들기
    def _ensure_worker(self):   # self.lock 을 잡고 부름
        if self.worker_alive:
            return
        self.worker_alive = True
        self.thread = threading.Thread(target=self._worker, name="ai-item", daemon=True)
        self.thread.start()

    def _worker(self):
        while True:
            with self.lock:
                if self.stop_event.is_set() or not self.queue:
                    self.current = None
                    self.worker_alive = False
                    return
                if self.queue[0] not in self.forced and not self.gate():
                    self.queue = [i for i in self.queue if i in self.forced]   # 수업이 끝남: 자동 요약은 다음 교시에
                    continue
                wait = self.busy()   # 브리핑 요약이 도는 중이면 같은 LM Studio 를 쓰므로 끝나길 기다림
                if not wait:
                    item_id = self.queue.pop(0)
                    self.current = item_id
                    forced = item_id in self.forced
                    self.forced.discard(item_id)
            if wait:
                self.stop_event.wait(BUSY_WAIT)
                continue
            self._run(item_id, forced)

    def _run(self, item_id, forced):
        it = self.db.one("SELECT id, title, body, source, ai_summary, ai_summary_hash FROM items WHERE id = ?", (item_id,))
        if not it or (not forced and it["ai_summary"] and it["ai_summary_hash"] == body_hash(it["body"])):
            return
        try:
            self._summarize(it)
        except AIError as e:
            msg = str(e)
            if not forced and msg.startswith(NOT_READY):   # LM Studio 가 꺼져 있음: 오류를 쌓지 않고 쉬었다가 다시
                with self.lock:
                    self.queue = [i for i in self.queue if i in self.forced]
                    self.paused_until = self.clock() + PAUSE_SECONDS
                return
            self.errors[item_id] = msg
            if not forced:
                self.failed[item_id] = self.clock()
        except Exception as e:
            self.errors[item_id] = f"요약 중 오류: {type(e).__name__}"
            self.failed[item_id] = self.clock()
            applog.exception("ai", "항목 AI 요약", e)
            self.db.log(f"항목 AI 요약 오류: {type(e).__name__}: {e}", "error")

    def _summarize(self, it):
        check_local(self.config["ai_base"])
        model = pick_model(self.config)
        names = [s["name"] for s in self.db.students()]
        body = re.sub(r"[ \t]+", " ", it["body"]).strip()
        text = summarize_item(self.config["ai_base"], model, it, body, names, chunk_size(self.config), db=self.db,
                              progress=lambda done, total: self._set_part(it["id"], done, total),
                              stop=self.stop_event.is_set)
        self._set_part(it["id"], None, None)
        self.db.execute("UPDATE items SET ai_summary = ?, ai_summary_hash = ?, ai_summary_at = ?, ai_summary_model = ? "
                        "WHERE id = ?", (text, body_hash(it["body"]), datetime.now().strftime("%Y-%m-%d %H:%M"), model, it["id"]))


def summarize_item(base, model, it, body, names, size=ITEM_CHARS, db=None, progress=None, stop=None,
                   timeout=TIMEOUT):
    """항목 한 건 요약. 짧으면 한 번에, 길면 조각마다 메모 → (메모가 길면 메모끼리 다시 줄임) → 마지막에 항목 형식으로.

    AI 에 보내는 글은 모두 학생 이름을 가리고(mask_names), 받은 메모도 다시 가림. 부르는 곳은 ask() 하나라
    127.0.0.1 로만 감(check_local 은 부르는 쪽에서).
    """
    source = SOURCE_NAMES.get(it["source"], it["source"])
    title = it["title"] or ""
    instruction = COOL_INSTRUCTION if it["source"] == "cool" else ITEM_INSTRUCTION
    chunks = split_chunks(body, size)
    if len(chunks) <= 1:
        prompt = mask_names(f"{instruction}[{source}] {title}\n{body}", names)
        return mask_names(ask(base, model, prompt, timeout, system=ITEM_SYSTEM, max_tokens=500, db=db), names)
    total = len(chunks)
    used = chunks[:CHUNK_MAX]
    left = sum(len(c) for c in chunks[CHUNK_MAX:])
    steps = len(used) + 1
    notes = []
    for i, part in enumerate(used, 1):
        if stop and stop():
            raise AIError("업무 데스크를 끄는 중이라 요약을 멈췄습니다")
        if progress:
            progress(i - 1, steps)
        head = CHUNK_INSTRUCTION.replace("{n}", str(total)).replace("{i}", str(i)).replace("{title}", title)
        note = ask(base, model, mask_names(head + part, names), timeout, system=ITEM_SYSTEM,
                   max_tokens=CHUNK_NOTE_TOKENS, db=db)
        note = mask_names(note, names).strip()
        if note and not re.fullmatch(r"[-\s]*없음[.\s]*", note):
            notes.append(f"({i}/{total}) {note}")
    if progress:
        progress(len(used), steps)
    if not notes:
        notes = ["- 없음"]
    joined = "\n".join(notes)
    room = max(size - len(instruction) - 200, size // 2)
    for _ in range(MERGE_ROUNDS):   # 메모를 합쳐도 한 번에 안 들어가면 몇 개씩 묶어 다시 줄임
        if len(joined) <= room:
            break
        if stop and stop():
            raise AIError("업무 데스크를 끄는 중이라 요약을 멈췄습니다")
        merged = []
        for group in split_chunks(joined, room):
            out = ask(base, model, mask_names(MERGE_INSTRUCTION.replace("{title}", title) + group, names), timeout,
                      system=ITEM_SYSTEM, max_tokens=CHUNK_NOTE_TOKENS, db=db)
            merged.append(mask_names(out, names).strip())
        joined = "\n".join(merged)
    joined = joined[:room]
    prompt = mask_names(instruction + f"[{source}] " + FINAL_HEAD.replace("{n}", str(total))
                        .replace("{title}", title) + joined, names)
    text = mask_names(ask(base, model, prompt, timeout, system=ITEM_SYSTEM, max_tokens=500, db=db), names).strip()
    text += f"\n(긴 자료라 {len(used)}조각으로 나눠 읽고 합친 요약"
    text += f", 뒷부분 약 {left:,}자는 읽지 못함 — 원문 확인)" if left else ")"
    return text


MAX_HIDDEN = 300


def set_hidden(db, key, hide=True):
    """요약에서 지운 줄 (다시 요약해도 같은 줄은 숨김). key 는 화면이 만든 '칸|날짜|내용'."""
    keys = [k for k in db.get_meta("ai_hidden", []) if k != key]
    if hide:
        keys.append(key)
    db.set_meta("ai_hidden", keys[-MAX_HIDDEN:])
    return keys[-MAX_HIDDEN:]
