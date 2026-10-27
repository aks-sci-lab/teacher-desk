"""켤 때 새 버전 확인과 자동 업데이트 (윈도우용 ZIP 으로 설치한 경우만).

1) Releases 의 latest.json 에서 최신 버전 번호를 읽어 지금 버전과 비교 (몇 초 안에 답이 없으면 그냥 켬)
2) 새 버전이 있으면 PowerShell 업데이트 창을 띄우고 업무 데스크는 끝냄 (돌고 있는 python.exe 는 바꿀 수 없어서)
3) 업데이트 창이 ZIP 을 받아 프로그램 파일만 바꾸고 (data 폴더·config.json 은 그대로) 업무 데스크를 다시 켬
"""

import json
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

LATEST_JSON = "https://github.com/aks-sci-lab/teacher-desk/releases/download/desk-latest/latest.json"
ZIP_URLS = [
    "https://github.com/aks-sci-lab/teacher-desk/releases/download/desk-latest/teacher-desk-windows.zip",
    "https://github.com/aks-sci-lab/arduino_experiments/releases/download/desk-latest/teacher-desk-windows.zip",
]
EXIT_UPDATING = 20  # start-desk.bat 은 이 값이면 '오류' 안내 없이 창을 닫음


def parse_version(v):
    """'0.3.10' → (0, 3, 10). 숫자가 아니면 (0,)."""
    nums = re.findall(r"\d+", str(v or ""))
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_portable(app_dir):
    """윈도우용 ZIP 설치본인지 (python 폴더가 함께 있음). 개발용 저장소 폴더는 업데이트하지 않음."""
    return (Path(app_dir) / "python" / "python.exe").is_file()


def check(current, url=None, timeout=4):
    """새 버전이 있으면 {'version', ...}, 없거나 확인할 수 없으면 None."""
    url = url or os.environ.get("DESK_UPDATE_JSON") or LATEST_JSON
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            info = json.loads(r.read().decode("utf-8"))
    except Exception:  # 인터넷이 안 되거나 주소가 없으면 조용히 넘어감 (켜는 것을 막지 않음)
        return None
    if not isinstance(info, dict) or "version" not in info:
        return None
    return info if parse_version(info["version"]) > parse_version(current) else None


def _q(s):
    return "'" + str(s).replace("'", "''") + "'"


def build_script(app_dir, pid, zip_urls, relaunch=True):
    """업데이트 창이 실행할 PowerShell (업무 데스크가 끝나길 기다렸다가 파일을 바꾸고 다시 켬)."""
    app = str(Path(app_dir))
    urls = ", ".join(_q(u) for u in zip_urls)
    restart = ("Start-Process -FilePath (Join-Path $dir 'start-desk.bat') -ArgumentList '--no-update-check' "
               "-WorkingDirectory $dir" if relaunch else "Write-Host ' (다시 켜기 생략)'")
    return f"""$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = '업무 데스크 업데이트'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$dir = {_q(app)}
$root = Split-Path $dir -Parent
$log = Join-Path $dir 'data\\update.log'
function Log($m) {{ Write-Host (' ' + $m); try {{ Add-Content -LiteralPath $log -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $m) -Encoding UTF8 }} catch {{}} }}
Write-Host ''
Log '업무 데스크를 새 버전으로 바꾸는 중입니다. 이 창은 저절로 닫힙니다.'
try {{ Wait-Process -Id {int(pid)} -Timeout 30 -ErrorAction SilentlyContinue }} catch {{}}
Start-Sleep -Seconds 1
$tmp = Join-Path $env:TEMP ('desk-update-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {{
  $got = $false
  foreach ($url in @({urls})) {{ try {{ Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile (Join-Path $tmp 'desk.zip'); $got = $true; break }} catch {{ Log ('받지 못함: ' + $url) }} }}
  if (-not $got) {{ throw '새 버전을 내려받지 못했습니다' }}
  Expand-Archive -LiteralPath (Join-Path $tmp 'desk.zip') -DestinationPath $tmp
  $new = Join-Path $tmp 'TeacherDesk'
  if (-not (Test-Path (Join-Path $new 'teacher-desk\\desk.py'))) {{ throw '받은 파일이 업무 데스크가 아닙니다' }}
  robocopy (Join-Path $new 'teacher-desk') $dir /E /XD data /XF config.json /NFL /NDL /NJH /NJS /NP | Out-Null
  if ($LASTEXITCODE -ge 8) {{ throw ('프로그램 파일을 바꾸지 못했습니다 (robocopy ' + $LASTEXITCODE + ')') }}
  robocopy (Join-Path $new 'pass') (Join-Path $root 'pass') /E /NFL /NDL /NJH /NJS /NP | Out-Null
  $start = Join-Path $new 'START-HERE.txt'; if (Test-Path $start) {{ Copy-Item $start $root -Force }}
  Log '업데이트를 마쳤습니다.'
}} catch {{
  Log ('업데이트 실패: ' + $_.Exception.Message + ' — 지금 버전으로 켭니다.')
  Start-Sleep -Seconds 3
}} finally {{
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}}
{restart}
"""


def start_update(app_dir, pid=None, zip_urls=None):
    """업데이트 창을 따로 띄움 (이 프로그램은 곧바로 끝나야 함)."""
    if os.environ.get("DESK_UPDATE_URL"):
        zip_urls = [os.environ["DESK_UPDATE_URL"]]
    script = build_script(app_dir, pid or os.getpid(), zip_urls or ZIP_URLS,
                          relaunch=not os.environ.get("DESK_NO_RELAUNCH"))
    fd, path = tempfile.mkstemp(prefix="desk-update-", suffix=".ps1")
    with os.fdopen(fd, "w", encoding="utf-8-sig") as f:  # BOM: 윈도우 PowerShell 5 가 한글을 바로 읽게
        f.write(script)
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
                     creationflags=flags, close_fds=True)
    return path
