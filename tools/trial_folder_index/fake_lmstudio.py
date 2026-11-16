"""시험용 가짜 LM Studio (127.0.0.1). 진짜 AI 가 아니라 규칙으로 답을 만들어 흐름만 확인한다.

- /v1/models, /v1/chat/completions 만 흉내 냄 (앱의 desk.ai.ask 가 그대로 부를 수 있게).
- 답은 받은 글에서 줄을 골라 내는 '뽑아 쓰기' 이다. 같은 뜻의 다른 말(협의회→교과 모임)은 알지 못한다.
  그래서 이 서버로 잰 'AI 검색' 정확도는 진짜 AI 보다 낮게 나오는 쪽이며, 요약·초안의 글 품질은 잴 수 없다.
- 받은 요청을 모두 기록해(prompts) 학생 이름이 새지 않았는지 시험이 확인한다.
"""

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATE = re.compile(r"\d{1,2}\s*[./]\s*\d{1,2}|\d{4}\.\s*\d{1,2}\.|\d{1,2}:\d{2}|\d+\s*월")


def _lines(text):
    out = []
    for raw in text.splitlines():
        s = raw.strip().lstrip("-•◦*# ").strip()
        if len(s) >= 4 and not s.startswith(("[", "자료 제목", "조각", "메모", "요약:", "본문 앞부분")):
            out.append(s)
    return out


def _after(prompt, marker):
    i = prompt.rfind(marker)
    return prompt[i + len(marker):] if i >= 0 else prompt


def _words(text, n=10):
    stop = {"있음", "없음", "대상", "일시", "장소", "내용", "결과", "안내", "제출", "2026", "학년도", "학기"}
    cnt = {}
    for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", text):
        w = re.sub(r"(은|는|이|가|을|를|의|에|로|와|과|도|에서|으로|까지)$", "", w)
        if len(w) >= 2 and w not in stop and not w.isdigit():
            cnt[w] = cnt.get(w, 0) + 1
    return [w for w, _ in sorted(cnt.items(), key=lambda x: (-x[1], x[0]))[:n]]


def answer(prompt):
    if "[양식 채우기]" in prompt:
        fields = [f.strip() for f in re.search(r"칸: (.+)", prompt).group(1).split(",")]
        lines = _lines(_after(prompt, "참고 자료:"))
        out = []
        for f in fields:
            key = re.sub(r"\s", "", f)
            hit = [s for s in lines if re.sub(r"\s", "", s).startswith(key) and re.search(r"[:：]", s)]
            if hit:
                out.append(f"{f}: " + re.split(r"[:：]", hit[0], 1)[-1].strip())
            elif "안건" in key or "내용" in key:
                out.append(f"{f}:")
                out += [f"- {s}" for s in lines if s.startswith("안건")][:4] or ["- (확인 필요)"]
            elif "결과" in key:
                out.append(f"{f}:")
                out += [f"- {s}" for s in lines if "확정" in s or "결과" in s][:3] or ["- (확인 필요)"]
            else:
                out.append(f"{f}: (확인 필요)")
        return "\n".join(out)
    if "[색인 키워드]" in prompt:
        return ", ".join(_words(_after(prompt, "제목:"), 10))
    if "[검색어 늘리기]" in prompt:
        task = re.search(r'"(.+?)"', prompt)
        return ", ".join(re.split(r"\s+", task.group(1))) if task else ""
    if "[초안 작성]" in prompt:
        task = re.search(r'"(.+?)"', prompt).group(1)
        mats = re.split(r"\[자료 \d+\] ", _after(prompt, "참고 자료:"))[1:]
        out = [f"# {task.replace(' 작성', '')}", "## 개요"]
        dated, other = [], []
        for m in mats:
            title, _, rest = m.partition("\n")
            ls = _lines(rest)
            dated += [f"- {s} (자료: {title.strip()})" for s in ls if DATE.search(s)][:3]
            other += [f"- {s}" for s in ls if not DATE.search(s)][:3]
        out += dated[:6] or ["- (확인 필요)"]
        out += ["## 주요 내용"] + (other[:8] or ["- (확인 필요)"])
        out += ["## 일정 정리", "| 항목 | 내용 |", "|---|---|"]
        out += [f"| {i + 1} | {d[2:].split(' (자료')[0]} |" for i, d in enumerate(dated[:4])] or ["| - | (확인 필요) |"]
        out += ["## 다음 할 일", "- (확인 필요)"]
        return "\n".join(out)
    if "번째 조각" in prompt:
        ls = _lines(_after(prompt, "조각:"))
        pick = [s for s in ls if DATE.search(s) or "제출" in s or "폐강" in s][:6]
        return "\n".join(f"- {s}" for s in pick) or "- 없음"
    if "메모들이다" in prompt:
        seen, out = set(), []
        for s in _lines(_after(prompt, "메모:")):
            s = re.sub(r"^\(\d+/\d+\)\s*", "", s)
            if s not in seen:
                seen.add(s)
                out.append(f"- {s}")
        return "\n".join(out[:10])
    if "한 줄 요약" in prompt:
        body = _after(prompt, "자료:")
        ls = _lines(body)
        title = ls[0] if ls else "자료"
        dates = [s for s in ls if DATE.search(s)][:3]
        todo = [s for s in ls if "제출" in s or "신청" in s][:2]
        out = [f"한 줄 요약: {title[:40]}"]
        if todo:
            out.append("- 해야 할 일: " + "; ".join(todo))
        if dates:
            out.append("- 날짜·시간·장소: " + "; ".join(dates))
        return "\n".join(out)
    return "알 수 없는 요청"


class FakeLMStudio:
    """with FakeLMStudio(prompt_tps=0, gen_tps=0) as f: f.base ...  (tps 를 주면 그만큼 늦게 답함)"""

    def __init__(self, prompt_tps=0, gen_tps=0):
        self.prompts, self.stats = [], {"calls": 0, "prompt_chars": 0, "answer_chars": 0}
        self.prompt_tps, self.gen_tps = prompt_tps, gen_tps
        owner = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj):
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send({"data": [{"id": "fake-gemma-12b"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                msgs = body["messages"]
                prompt = "\n".join(m["content"] for m in msgs)
                text = answer(msgs[-1]["content"])
                owner.prompts.append(prompt)
                owner.stats["calls"] += 1
                owner.stats["prompt_chars"] += len(prompt)
                owner.stats["answer_chars"] += len(text)
                if owner.prompt_tps:
                    time.sleep(len(prompt) / owner.prompt_tps)
                if owner.gen_tps:
                    time.sleep(len(text) / owner.gen_tps)
                self._send({"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.server.server_port}/v1"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
