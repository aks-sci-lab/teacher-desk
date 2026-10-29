"""실행·바로 가기·업데이트 .bat 파일 만들기.

한글(바로 가기 이름 등)이 윈도우 언어와 상관없이 깨지지 않도록 PowerShell 명령을 UTF-16 base64
(-EncodedCommand)로 넣고, 화면 안내 문구는 한국어 윈도우용 CP949 로 저장합니다.
명령을 고치면 이 스크립트를 다시 실행하세요:  python tools/make_bats.py
"""

import base64
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
# 새 전용 저장소(aks-sci-lab/teacher-desk)를 먼저, 없으면 예전 저장소에서 받음 (저장소를 옮기는 동안 끊기지 않게)
RELEASE_ZIPS = [
    "https://github.com/aks-sci-lab/teacher-desk/releases/download/desk-latest/teacher-desk-windows.zip",
    "https://github.com/aks-sci-lab/arduino_experiments/releases/download/desk-latest/teacher-desk-windows.zip",
]

# WScript.Shell 은 윈도우 기본 문자표에 없는 글자(영어판 윈도우의 한글 등)로는 저장하지 못하므로
# 영문 이름으로 만든 뒤 한글 이름으로 바꿉니다.
SHORTCUT_PS = (
    "$ErrorActionPreference = 'Stop'; "
    "$d = [Environment]::GetFolderPath('Desktop'); "
    "$dir = $env:DESK_DIR; "
    "$tmp = Join-Path $d 'teacher-desk-shortcut.lnk'; "
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($tmp); "
    "$s.TargetPath = (Join-Path $dir 'start-desk.bat'); "
    "$s.WorkingDirectory = $dir; "
    "$s.WindowStyle = 7; "
    "$s.IconLocation = (Join-Path $env:SystemRoot 'System32\\imageres.dll') + ',109'; "
    "$s.Save(); "
    "Move-Item -LiteralPath $tmp -Destination (Join-Path $d '업무 데스크.lnk') -Force"
)

# 최신 ZIP 을 받아 프로그램 파일만 덮어씀. data 폴더·config.json(자료·설정)과 이 파일 자신은 건드리지 않음.
UPDATE_PS = (
    "$ErrorActionPreference = 'Stop'; "
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; "
    "$dir = $env:DESK_DIR.TrimEnd('\\'); "
    "$root = Split-Path $dir -Parent; "
    "$urls = if ($env:DESK_UPDATE_URL) { @($env:DESK_UPDATE_URL) } else { @(" + ", ".join(f"'{u}'" for u in RELEASE_ZIPS) + ") }; "
    "$tmp = Join-Path $env:TEMP ('desk-update-' + [guid]::NewGuid()); "
    "New-Item -ItemType Directory -Path $tmp | Out-Null; "
    "try { "
    "  Write-Host ' 최신 버전을 내려받는 중...'; "
    "  $got = $false; "
    "  foreach ($url in $urls) { try { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile (Join-Path $tmp 'desk.zip'); $got = $true; break } catch { Write-Host (' 받지 못함: ' + $url) } }; "
    "  if (-not $got) { throw '최신 버전을 내려받지 못했습니다' }; "
    "  Expand-Archive -LiteralPath (Join-Path $tmp 'desk.zip') -DestinationPath $tmp; "
    "  $new = Join-Path $tmp 'TeacherDesk'; "
    "  if (-not (Test-Path (Join-Path $new 'teacher-desk\\desk.py'))) { throw '받은 파일이 업무 데스크가 아닙니다' }; "
    "  Write-Host ' 프로그램 파일을 바꾸는 중... (자료와 설정은 그대로 둡니다)'; "
    "  robocopy (Join-Path $new 'teacher-desk') $dir /E /XD data /XF config.json update-desk.bat /NFL /NDL /NJH /NJS /NP | Out-Null; "
    "  if ($LASTEXITCODE -ge 8) { throw ('프로그램 파일을 바꾸지 못했습니다 (robocopy ' + $LASTEXITCODE + '). 업무 데스크 창을 닫았는지 확인하세요') }; "
    "  robocopy (Join-Path $new 'pass') (Join-Path $root 'pass') /E /NFL /NDL /NJH /NJS /NP | Out-Null; "
    "  if ($LASTEXITCODE -ge 8) { throw ('공유 페이지 파일을 바꾸지 못했습니다 (robocopy ' + $LASTEXITCODE + ')') }; "
    "  $start = Join-Path $new 'START-HERE.txt'; if (Test-Path $start) { Copy-Item $start $root -Force }; "
    "  $global:LASTEXITCODE = 0 "
    "} finally { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }"
)

# 주의: if ( ... ) 묶음 안의 echo 문구에 괄호를 쓰면 명령 창이 묶음의 끝으로 읽음 (tests/test_bats.py 가 검사)
HEADER = r'''@echo off
rem 이 파일은 tools/make_bats.py 가 만듭니다 (직접 고치지 말고 스크립트를 고치세요).
rem 한국어 윈도우에서 한글 안내가 깨지지 않도록 ANSI(CP949)로 저장되어 있습니다.
'''

SHORTCUT_BAT = HEADER + r'''title 바탕화면 바로 가기 만들기
cd /d "%~dp0"
set "DESK_DIR=%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}
if errorlevel 1 (
    echo.
    echo  [오류] 바로 가기를 만들지 못했습니다. start-desk.bat 을 오른쪽 클릭해 "바로 가기 만들기" 를 쓰세요.
    pause
    exit /b 1
)
echo.
echo  바탕화면에 "업무 데스크" 바로 가기를 만들었습니다.
pause
'''

START_BAT = HEADER + r'''title 교사 업무 데스크
cd /d "%~dp0"
set "PY="
if exist "%~dp0python\python.exe" set "PY=%~dp0python\python.exe"
if not defined PY (
    python --version > nul 2> nul && set "PY=python"
)
if not defined PY (
    py -3 --version > nul 2> nul && set "PY=py"
)
if not defined PY (
    echo  [오류] Python 을 찾지 못했습니다. Python 이 포함된 윈도우용 ZIP 을 다시 받아 주세요.
    pause
    exit /b 1
)
"%PY%" desk.py --background %*
if errorlevel 1 (
    echo  [오류] 업무 데스크가 켜지지 않았습니다. 위 안내를 확인하세요.
    pause
    exit /b 1
)
exit /b 0
'''

UPDATE_BAT = HEADER + r'''rem  update-desk.bat /y  : 묻지 않고 바로 업데이트 (자동 시험용)
title 업무 데스크 업데이트
cd /d "%~dp0"
set "DESK_DIR=%~dp0"

if /i not "%~1"=="/y" (
    echo.
    echo  업무 데스크를 최신 버전으로 바꿉니다.
    echo  - 학생 명단·발급 기록·수신함 자료와 설정은 그대로 남습니다.
    echo  - 먼저 업무 데스크 설정에서 프로그램 종료를 눌러 주세요.
    echo.
    pause
)

powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}
if errorlevel 1 (
    echo.
    echo  [오류] 업데이트하지 못했습니다. 인터넷 연결과 업무 데스크 프로그램 종료 여부를 확인하세요.
    echo  그래도 안 되면 이 창을 사진으로 찍어 보내 주세요.
    if /i not "%~1"=="/y" pause
    exit /b 1
)
echo.
echo  업데이트를 마쳤습니다. start-desk.bat 으로 다시 켜세요.
if /i not "%~1"=="/y" pause
'''


def write_bat(name, template, ps):
    encoded = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    out = DESK / name
    out.write_bytes(template.replace("{encoded}", encoded).replace("\n", "\r\n").encode("cp949"))
    print(f"만듦: {out}")


def main():
    write_bat("start-desk.bat", START_BAT, "")
    write_bat("make-desktop-shortcut.bat", SHORTCUT_BAT, SHORTCUT_PS)
    write_bat("update-desk.bat", UPDATE_BAT, UPDATE_PS)


if __name__ == "__main__":
    main()
