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
INSTRUCTION = """아래 자료를 다음 형식으로 요약해 줘. 해당하는 것이 없는 칸은 '없음'.

## 꼭 할 일 (마감 순)
- 날짜 · 할 일 · 자료 번호 [n]
## 다가오는 행사·회의
- 날짜 · 내용 [n]
## 알아 둘 내용
- 한 줄씩 [n]

자료:
"""


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


def build_prompt(items, student_names=(), size="normal"):
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
    return mask_names(INSTRUCTION + "\n".join(parts), student_names), used


def weekly_summary(db, config, today=None, timeout=TIMEOUT):
    """지난 1주일 요약을 만들어 저장하고 돌려줌 {at, text, count, used, model, days}."""
    base = config["ai_base"]
    check_local(base)
    items = week_items(db, today)
    result = {"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "count": len(items), "used": 0,
              "model": "", "days": DAYS, "text": ""}
    if not items:
        result["text"] = "지난 1주일 동안 받은 쿨메신저 쪽지·업무포털 공문이 없습니다."
        db.set_meta("ai_weekly", result)
        return result
    model = config["ai_model"] or (models(base) or [None])[0]
    if not model:
        raise AIError("LM Studio 에 불러온 모델이 없습니다. LM Studio 에서 모델을 하나 불러오세요.")
    prompt, used = build_prompt(items, [s["name"] for s in db.students()], config["ai_input_size"])
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
    result.update(text=text, used=used, model=model,
                  ids=[it["id"] for it in items[:used]])
    db.set_meta("ai_weekly", result)
    db.log(f"AI 요약: 지난 {DAYS}일 자료 {used}건 ({model})")
    return result


class Job:
    """요약은 몇 분 걸리므로 뒤에서 만들고, 화면은 몇 초마다 상태만 물어봄."""

    def __init__(self, db, config):
        self.db, self.config = db, config
        self.lock = threading.Lock()
        self.thread = None
        self.started_at = None
        self.error = None

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self):
        with self.lock:
            if self.running:
                return False
            self.error = None
            self.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.thread = threading.Thread(target=self._run, name="ai-summary", daemon=True)
            self.thread.start()
            return True

    def _run(self):
        try:
            weekly_summary(self.db, self.config)
        except AIError as e:
            self.error = str(e)
        except Exception as e:  # 뒤에서 도는 일이 조용히 죽지 않게
            self.error = f"요약 중 오류: {type(e).__name__}"
            self.db.log(f"AI 요약 오류: {type(e).__name__}: {e}", "error")

    def state(self):
        return {"enabled": bool(self.config["ai_enabled"]), "running": self.running,
                "started_at": self.started_at if self.running else None, "error": self.error,
                "result": self.db.get_meta("ai_weekly"), "timeout": TIMEOUT}
