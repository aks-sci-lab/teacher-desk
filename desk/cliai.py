"""일반 질문을 이 PC 에 설치·로그인한 명령줄(CLI) AI 로 묻기 (0.6.24).

선생님 요청 "AI 모델은 CLI 모델로 선택할 수 있도록": Claude Code·Gemini CLI·Codex CLI 를
API 키 없이(그 프로그램에 로그인한 구독으로) 쓸 수 있게 합니다.

- CLI 도 질문을 그 회사 서버로 보내므로 '외부 AI' 와 똑같이 다룹니다:
  일반 질문만, 매번 동의, 명단 이름·연락처·받은 자료 겹침 검사(providers.check_public).
  쪽지·공문 요약, AI 검색, 문서 작성 같은 업무자료 AI 는 이 선택과 관계없이 늘 LM Studio(이 PC)입니다.
- 질문은 명령줄 인자가 아니라 표준 입력으로만 넘기고, 명령줄에는 정해 둔 옵션과 검사한 모델 이름만 넣습니다.
- 빈 임시 폴더에서 실행해 CLI 가 업무 데스크 폴더(data/desk.db 등)를 읽지 못하게 합니다.
- 사용 기록에는 어떤 CLI 인지·걸린 시간만 남깁니다 (질문·답은 남기지 않음).
"""

import os
import re
import shutil
import subprocess
import tempfile

from . import ai, applog

TIMEOUT = 300
MAX_OUTPUT = 200_000
MODEL_NAME = re.compile(r"[A-Za-z0-9._:-]{1,120}")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# provider → (프로그램 이름, 화면 이름, 설치 안내)
TOOLS = {
    "claude_cli": ("claude", "Claude Code CLI", "https://docs.claude.com/ko/docs/claude-code/setup"),
    "gemini_cli": ("gemini", "Gemini CLI", "https://github.com/google-gemini/gemini-cli"),
    "codex_cli": ("codex", "Codex CLI", "https://github.com/openai/codex"),
}
# CLI 에는 '모델 목록' 명령이 없어 자주 쓰는 이름을 미리 넣어 둠 (목록에 없으면 직접 입력, 비우면 CLI 기본 모델)
PRESETS = {
    "claude_cli": ["sonnet", "opus", "haiku"],
    "gemini_cli": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"],
    "codex_cli": ["gpt-5", "gpt-5-codex", "gpt-5-mini"],
}


def _candidates(name):
    """PATH 에 없을 때(앱이 다른 환경으로 켜졌을 때) 윈도우에서 흔히 설치되는 자리."""
    home = os.path.expanduser("~")
    appdata = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
    local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    return [os.path.join(appdata, "npm", name + ".cmd"),
            os.path.join(home, ".local", "bin", name + ".exe"),
            os.path.join(home, ".local", "bin", name),
            os.path.join(local, "Programs", name, name + ".exe")]


def find(provider):
    """CLI 실행 파일 경로 (없으면 None)."""
    name = TOOLS[provider][0]
    path = shutil.which(name)
    if path:
        return path
    for p in _candidates(name):
        if os.path.isfile(p):
            return p
    return None


def need(provider):
    path = find(provider)
    if not path:
        name, label, url = TOOLS[provider]
        raise ai.AIError(f"이 PC 에서 {label}({name}) 를 찾지 못했습니다. 설치하고 명령 프롬프트에서 '{name}' 를 한 번 실행해 "
                         f"로그인한 뒤 다시 해 보세요. 설치 안내: {url}")
    return path


def command(provider, path, model):
    """정해 둔 옵션 + 검사한 모델 이름만. 질문은 표준 입력으로."""
    if model and not MODEL_NAME.fullmatch(model):
        raise ai.AIError("CLI 모델 이름은 영문·숫자·점·밑줄·하이픈·콜론 120자 이내로 입력하세요.")
    if provider == "claude_cli":
        cmd = [path, "-p", "--output-format", "text"]
        return cmd + (["--model", model] if model else [])
    if provider == "gemini_cli":
        return [path] + (["-m", model] if model else [])
    # Codex: exec 는 한 번 답하고 끝남, '-' 는 표준 입력에서 질문 읽기, 읽기 전용
    cmd = [path, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never"]
    return cmd + (["-m", model] if model else []) + ["-"]


def models(provider):
    need(provider)
    return list(PRESETS[provider])


def ask(provider, model, question, system, timeout=TIMEOUT):
    path = need(provider)
    cmd = command(provider, path, model)
    prompt = f"{system}\n\n질문:\n{question}\n"
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    label = TOOLS[provider][1]
    with applog.timed("ai", "CLI AI 질문", cli=TOOLS[provider][0], model=model or "기본"):
        with tempfile.TemporaryDirectory(prefix="desk-cli-") as cwd:
            try:
                r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout, cwd=cwd, env=env, creationflags=flags)
            except subprocess.TimeoutExpired:
                raise ai.AIError(f"{label} 의 답이 {timeout // 60}분 안에 오지 않아 멈췄습니다.") from None
            except OSError:
                raise ai.AIError(f"{label} 를 실행하지 못했습니다. 설치가 끝났는지 확인하세요.") from None
    out = ANSI.sub("", (r.stdout or "")[:MAX_OUTPUT]).strip()
    if r.returncode != 0 or not out:
        err = ANSI.sub("", (r.stderr or "") + "\n" + (r.stdout or "")).strip()
        hint = "로그인"
        low = err.lower()
        if "model" in low and ("not found" in low or "invalid" in low or "unknown" in low or "not supported" in low):
            hint = "모델 이름"
        elif "limit" in low or "quota" in low or "429" in low:
            hint = "이용 한도"
        first = next((line for line in err.splitlines() if line.strip()), "")[:200]
        raise ai.AIError(f"{label} 가 답하지 못했습니다 (종료 코드 {r.returncode}). {hint}을 확인하세요."
                         + (f"\nCLI 메시지: {first}" if first else ""))
    return out
