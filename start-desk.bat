@echo off
rem 이 파일은 tools/make_bats.py 가 만듭니다 (직접 고치지 말고 스크립트를 고치세요).
rem 한국어 윈도우에서 한글 안내가 깨지지 않도록 ANSI(CP949)로 저장되어 있습니다.
title 교사 업무 데스크
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
