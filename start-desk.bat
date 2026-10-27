@echo off
rem 이 파일은 한국어 윈도우에서 한글이 깨지지 않도록 ANSI(CP949)로 저장되어 있습니다.
title 교사 업무 데스크
cd /d "%~dp0"

rem Python 찾는 순서: 1) 이 폴더 안의 python\python.exe  2) PC 에 설치된 python  3) py 실행기
set "PY="
if exist "%~dp0python\python.exe" set "PY=%~dp0python\python.exe"
if not defined PY (
    python --version > nul 2> nul && set "PY=python"
)
if not defined PY (
    py -3 --version > nul 2> nul && set "PY=py"
)

if not defined PY (
    echo.
    echo  [안내] 이 PC 에 Python 이 설치되어 있지 않습니다.
    echo.
    echo  1. 잠시 뒤 Python 내려받기 페이지가 열립니다.
    echo  2. 설치 첫 화면 아래쪽 "Add python.exe to PATH" 에 꼭 체크하고 설치하세요.
    echo  3. 설치가 끝나면 이 파일을 다시 두 번 누르세요.
    echo.
    start "" "https://www.python.org/downloads/windows/"
    pause
    exit /b 1
)

echo  업무 데스크를 켭니다. 이 창을 닫으면 업무 데스크가 꺼집니다.
"%PY%" desk.py %*
rem 20 = 새 버전으로 바꾸는 중 (업데이트 창이 끝나면 업무 데스크를 다시 켬)
if "%errorlevel%"=="20" exit /b 0
if errorlevel 1 (
    echo.
    echo  [오류] 업무 데스크가 멈췄습니다. 이 창의 글을 사진으로 찍어 보내 주세요.
)
pause
