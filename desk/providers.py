"""일반 질문용 AI 선택. 업무자료를 찾아 붙이지 않으며 외부 전송은 매번 확인합니다."""

import json
import re
import urllib.error
import urllib.request

from . import ai

PROVIDERS = {"local": "로컬", "browser": "브라우저 내장", "openai": "OpenAI",
             "anthropic": "Claude", "gemini": "Gemini"}
EXTERNAL = {"openai", "anthropic", "gemini"}
SYSTEM = "일반 지식 질문에 답하는 도우미입니다. 자료에 없는 학교 내부 사정은 추측하지 마세요. 한국어로 답하세요."
MAX_QUESTION = 4000
MAX_RESPONSE = 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ai.AIError("외부 AI가 다른 주소로 이동을 요청했습니다. API 키 보호를 위해 중단했습니다.")


def validate_question(question):
    if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION:
        raise ai.AIError(f"일반 질문은 1~{MAX_QUESTION}자로 입력하세요.")
    return question.strip()


def check_public(db, config, question):
    """명단의 이름·연락처·자료 복사를 막는 보조 검사. 모든 개인정보를 판별하지는 못합니다."""
    names = {s["name"] for s in db.students()}
    names.add(config["teacher_name"])
    if any(n and len(n) >= 2 and n in question for n in names):
        raise ai.AIError("명단에 있는 이름은 외부 AI로 보낼 수 없습니다. 개인정보를 빼거나 로컬 AI를 사용하세요.")
    if re.search(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|(?<!\d)0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}(?!\d)"
                 r"|(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)", question):
        raise ai.AIError("연락처·개인식별번호로 보이는 내용이 있습니다. 외부 AI로 보내지 않습니다.")
    if db.one("SELECT id FROM items WHERE source IN ('cool', 'edufine') AND "
              "((length(title) >= 8 AND instr(?, title) > 0) OR "
              "(length(?) >= 12 AND instr(body, ?) > 0)) LIMIT 1", (question, question, question)):
        raise ai.AIError("수신한 쪽지·공문과 겹치는 내용입니다. 업무자료는 로컬 AI로 질문하세요.")


def _request(url, headers, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=90) as response:
            data = response.read(MAX_RESPONSE + 1)
        if len(data) > MAX_RESPONSE:
            raise ai.AIError("외부 AI의 응답이 너무 큽니다.")
        return json.loads(data)
    except urllib.error.HTTPError as e:
        e.close()
        detail = {401: "API 키를 확인하세요.", 403: "이 모델을 사용할 권한을 확인하세요.",
                  404: "모델 이름을 확인하세요.", 429: "이용 한도·잔액을 확인하고 잠시 뒤 다시 시도하세요."}
        raise ai.AIError(f"외부 AI 요청 실패 ({e.code}). {detail.get(e.code, '서비스 상태와 모델 설정을 확인하세요.')}") from None
    except (urllib.error.URLError, OSError):
        raise ai.AIError("외부 AI에 연결하지 못했습니다. 인터넷 연결과 서비스 상태를 확인하세요.") from None
    except (ValueError, UnicodeError):
        raise ai.AIError("외부 AI의 응답을 읽을 수 없습니다.") from None


def ask(db, config, question, *, provider=None, confirmed=False):
    question = validate_question(question)
    provider = provider or config["ai_provider"]
    if provider not in PROVIDERS:
        raise ai.AIError("AI 사용 방식이 잘못되었습니다.")
    if provider == "browser":
        raise ai.AIError("브라우저 내장 AI는 앱의 'AI 질문' 화면에서만 사용할 수 있습니다. 텔레그램에서는 로컬 또는 외부 API를 선택하세요.")
    if provider == "local":
        return ai.ask(config["ai_base"], ai.pick_model(config), question, timeout=90,
                      system=SYSTEM, max_tokens=1200)
    if confirmed is not True:
        raise ai.AIError("공개 자료·일반 질문이며 외부 전송과 비용 발생에 동의하는지 먼저 확인하세요.")
    check_public(db, config, question)
    key, model = config[f"ai_{provider}_key"], config[f"ai_{provider}_model"]
    if not key or not model:
        raise ai.AIError("AI 질문 화면에서 해당 서비스의 API 키와 모델 이름을 저장하세요.")
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", model) or any(not 33 <= ord(c) <= 126 for c in key):
        raise ai.AIError("API 키 또는 모델 이름 형식이 잘못되었습니다. AI 질문 화면에서 다시 저장하세요.")
    messages = [{"role": "user", "content": question}]
    if provider == "openai":
        data = _request("https://api.openai.com/v1/responses", {"Authorization": f"Bearer {key}"},
                        {"model": model, "instructions": SYSTEM, "input": question,
                         "max_output_tokens": 1600, "store": False})
    elif provider == "anthropic":
        data = _request("https://api.anthropic.com/v1/messages",
                        {"x-api-key": key, "anthropic-version": "2023-06-01"},
                        {"model": model, "system": SYSTEM, "messages": messages, "max_tokens": 1600})
    else:
        data = _request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                        {"x-goog-api-key": key},
                        {"systemInstruction": {"parts": [{"text": SYSTEM}]},
                         "contents": [{"role": "user", "parts": [{"text": question}]}],
                         "generationConfig": {"maxOutputTokens": 1600}})
    try:
        if provider == "openai":
            if data.get("status") != "completed":
                raise ValueError
            parts = [part["text"] for output in data["output"] if output.get("type") == "message"
                     for part in output["content"] if part.get("type") == "output_text"]
        elif provider == "anthropic":
            if data.get("stop_reason") not in ("end_turn", "stop_sequence"):
                raise ValueError
            parts = [part["text"] for part in data["content"] if part.get("type") == "text"]
        else:
            if data["candidates"][0].get("finishReason") != "STOP":
                raise ValueError
            parts = [part["text"] for part in data["candidates"][0]["content"]["parts"]
                     if "text" in part and not part.get("thought")]
        if not parts or not all(isinstance(part, str) for part in parts):
            raise ValueError
        text = "\n".join(parts).strip()
        if not text:
            raise ValueError
    except (KeyError, IndexError, TypeError, AttributeError, ValueError):
        raise ai.AIError("외부 AI가 완성된 글 답변을 반환하지 않았습니다. 안전 차단·출력 한도·모델 설정을 확인하세요.") from None
    return text
