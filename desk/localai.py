"""로컬 AI 설치 도우미: 사양은 이 PC에서만 확인하고 배치파일은 요청할 때 생성."""

import base64
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

from . import ai

MODELS = {
    "qwen3-4b": {
        "name": "Qwen3 4B · Q4_K_M", "repo": "lmstudio-community/Qwen3-4B-GGUF",
        "file": "Qwen3-4B-Q4_K_M.gguf", "ram": 12, "free": 6, "disk": 7,
        "description": "가벼운 한국어 요약용. 작은 모델이라 중요한 날짜·내용은 원문과 확인하세요.",
    },
    "qwen3-8b": {
        "name": "Qwen3 8B · Q4_K_M", "repo": "lmstudio-community/Qwen3-8B-GGUF",
        "file": "Qwen3-8B-Q4_K_M.gguf", "ram": 24, "free": 12, "disk": 12,
        "description": "요약·질문 품질 우선. 내장 그래픽이나 CPU만 쓰면 오래 걸릴 수 있습니다.",
    },
}

_HARDWARE = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$gpu = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })
@{cpu=$cpu.Name; cores=[int]$cpu.NumberOfLogicalProcessors;
  ram_gb=[math]::Round($os.TotalVisibleMemorySize / 1MB, 1);
  free_gb=[math]::Round($os.FreePhysicalMemory / 1MB, 1); gpus=$gpu} | ConvertTo-Json -Compress
"""


class SetupError(Exception):
    """설정 화면에 보여 줄 안내."""


def _powershell(script: str) -> list[str]:
    exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return [str(exe), "-NoProfile", "-NonInteractive", "-EncodedCommand",
            base64.b64encode(script.encode("utf-16-le")).decode("ascii")]


def hardware(data_dir: Path) -> dict:
    if platform.system() != "Windows":
        raise SetupError("자동 사양 확인과 설치 배치파일은 Windows에서 사용할 수 있습니다.")
    try:
        result = subprocess.run(_powershell(_HARDWARE), capture_output=True, timeout=20,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise SetupError("PC 사양을 확인하지 못했습니다. 학교에서 Windows 관리 기능을 제한하는지 확인해 주세요.")
        data = json.loads(result.stdout.decode("utf-8-sig"))
        if (not isinstance(data, dict) or not isinstance(data.get("ram_gb"), (int, float))
                or not isinstance(data.get("free_gb"), (int, float))
                or data["ram_gb"] <= 0 or data["free_gb"] < 0):
            raise SetupError("PC 메모리 정보를 읽지 못했습니다.")
        data["disk_gb"] = round(shutil.disk_usage(data_dir).free / 1024**3, 1)
        data["lms_installed"] = bool(shutil.which("lms") or (Path.home() / ".lmstudio" / "bin" / "lms.exe").is_file())
        data["recommendation"] = recommend(data)
        return data
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        raise SetupError("PC 사양 확인에 실패했습니다. 잠시 뒤 다시 확인해 주세요.") from e


def recommend(spec: dict) -> dict:
    choices = []
    for key, model in MODELS.items():
        reasons = []
        if spec["ram_gb"] < model["ram"]:
            reasons.append(f"전체 메모리 {model['ram']}GB 이상 권장")
        if spec["free_gb"] < model["free"]:
            reasons.append(f"현재 사용 가능한 메모리 {model['free']}GB 이상 권장")
        if spec["disk_gb"] < model["disk"]:
            reasons.append(f"남은 디스크 {model['disk']}GB 이상 권장")
        choices.append({"id": key, **model, "eligible": not reasons, "reasons": reasons})
    available = [m["id"] for m in choices if m["eligible"]]
    return {"selected": available[-1] if available else None, "models": choices,
            "note": "메모리·현재 여유 공간에 따른 보수적인 추천이며 속도·품질을 보장하지 않습니다. "
                    "GPU 이름은 참고용이며 내장 그래픽의 공유 메모리를 전용 VRAM으로 계산하지 않습니다. "
                    "디스크는 업무 데스크가 있는 드라이브 기준입니다. 모델 저장 위치가 다르면 그 드라이브의 여유 공간도 확인하세요."}


def validate_port(port) -> int:
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise SetupError("AI 포트는 1024~65535 사이의 정수여야 합니다.")
    return port


def setup_batch(model_id: str, port: int) -> bytes:
    model = MODELS.get(model_id)
    if not model:
        raise SetupError("목록에 있는 모델을 골라 주세요.")
    validate_port(port)
    # 외부 입력을 명령으로 넣지 않음: 모델은 고정 목록, 포트는 검증한 정수.
    script = r"""
$ErrorActionPreference = 'Stop'
function Run-Lms {
    & $script:lms @args
    if ($LASTEXITCODE -ne 0) { throw 'LM Studio 명령이 실패했습니다. 위 오류를 확인하고 LM Studio를 최신 버전으로 업데이트하세요.' }
}
function Assert-LocalPort {
    $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -eq __PORT__ })
    if ($listeners | Where-Object { $_.LocalAddress -notin @('127.0.0.1','::1') }) {
        throw '이 포트가 외부에 열려 있습니다. LM Studio의 네트워크 공개를 끄거나 도우미에서 다른 포트를 고르세요.'
    }
}
try {
    $cmd = Get-Command lms -ErrorAction SilentlyContinue
    $script:lms = if ($cmd) { $cmd.Source } else { Join-Path $env:USERPROFILE '.lmstudio\bin\lms.exe' }
    if (-not (Test-Path -LiteralPath $script:lms)) {
        Write-Host 'LM Studio를 먼저 설치해야 합니다. 설치와 모델 다운로드에만 인터넷을 씁니다.'
        if ((Read-Host '공식 LM Studio 설치를 시작할까요? Y/N') -ne 'Y') { throw '설치를 취소했습니다.' }
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Start-Process 'https://lmstudio.ai/download'
            throw '공식 페이지에서 설치한 뒤 LM Studio를 한 번 열고 이 파일을 다시 실행하세요.'
        }
        & winget install --id ElementLabs.LMStudio --exact --source winget --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) { throw 'LM Studio 설치에 실패했습니다. 공식 페이지 https://lmstudio.ai/download 를 확인하세요.' }
        throw '설치했습니다. LM Studio를 한 번 열어 초기 설정을 마친 뒤 이 파일을 다시 실행하세요.'
    }
    Write-Host '공문이나 학생 자료는 보내지 않습니다. 모델을 내려받고 이 PC에서만 서버를 엽니다.'
    if ((Read-Host '모델 다운로드와 실행을 시작할까요? Y/N') -ne 'Y') { throw '실행을 취소했습니다.' }
    Assert-LocalPort
    Run-Lms daemon up
    $server = Run-Lms server status --json | ConvertFrom-Json
    if ($server.running -and $server.port -ne __PORT__) {
        throw '기존 AI 서버가 다른 포트에서 실행 중입니다. 도우미에 그 포트를 입력하세요. 기존 서버는 바꾸지 않았습니다.'
    }
    $loaded = Run-Lms ps --json | ConvertFrom-Json
    if (-not ($loaded | Where-Object { $_.identifier -eq '__MODEL__' })) {
        Run-Lms get 'https://huggingface.co/__REPO__/blob/main/__FILE__'
        Run-Lms load '__REPO__/__FILE__' --identifier '__MODEL__' --context-length 12288
    }
    if (-not $server.running) { Run-Lms server start --bind 127.0.0.1 --port __PORT__ }
    Assert-LocalPort
    $r = Invoke-RestMethod -Uri 'http://127.0.0.1:__PORT__/v1/models' -TimeoutSec 15
    if (-not ($r.data | Where-Object { $_.id -eq '__MODEL__' })) { throw '선택한 모델을 서버에서 찾지 못했습니다.' }
    Write-Host 'AI 실행을 확인했습니다. 업무 데스크 설정에서 [연결 확인 후 적용]을 눌러 마무리하세요.'
    Write-Host '재부팅 뒤 AI를 다시 켤 때도 이 파일을 실행할 수 있습니다. 기존 모델은 재사용합니다.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Read-Host 'Enter를 누르면 닫습니다'
    exit 1
}
Read-Host 'Enter를 누르면 닫습니다'
"""
    script = script.replace("__PORT__", str(port)).replace("__REPO__", model["repo"]).replace(
        "__FILE__", model["file"]).replace("__MODEL__", "teacher-desk-" + model_id)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    line = "powershell.exe -NoProfile -EncodedCommand " + encoded
    if len(line) > 8000:
        raise SetupError("설치 배치파일이 너무 깁니다.")
    return ("@echo off\r\nsetlocal\r\n" + line + "\r\nexit /b %errorlevel%\r\n").encode("cp949")


def apply(config, model_id: str, port: int) -> dict:
    if model_id not in MODELS:
        raise SetupError("목록에 있는 모델을 골라 주세요.")
    validate_port(port)
    if port == config["port"]:
        raise SetupError("AI 포트와 업무 데스크 포트는 달라야 합니다.")
    base = f"http://127.0.0.1:{port}/v1"
    identifier = "teacher-desk-" + model_id
    if identifier not in ai.models(base):
        raise SetupError("설치 배치파일을 먼저 실행해 선택한 모델을 불러오세요. 기존 AI 설정은 바꾸지 않았습니다.")
    ai.ask(base, identifier, "연결 시험입니다. '준비됐습니다'라고 한 문장으로 답하세요.",
           timeout=60, system="한국어로 짧게 답하세요.", max_tokens=64)
    config.update({"ai_enabled": True, "ai_base": base, "ai_model": identifier, "ai_input_size": "short"})
    return {"ok": True, "settings": config.public()}
