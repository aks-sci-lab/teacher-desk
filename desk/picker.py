"""윈도우 '찾아보기' 창 (파일·폴더 고르기).

브라우저는 보안 때문에 고른 파일의 실제 경로를 알려 주지 않으므로, 업무 데스크(이 PC 의 프로그램)가
윈도우 기본 대화 상자를 띄워 경로를 받아 옵니다. 설치 없는 Python 에는 tkinter 가 없어 PowerShell 을 씁니다.
"""

import base64
import os
import subprocess

TIMEOUT = 600  # 선생님이 고르는 동안 기다리는 최대 시간(초)


class PickerError(Exception):
    pass


def _ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"


def build_script(kind, title, initial="", filter_text=""):
    """PowerShell 명령 (맨 앞 창으로 띄우고, 고른 경로를 UTF-8 로 출력)."""
    owner = ("Add-Type -AssemblyName System.Windows.Forms; "
             "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true; ShowInTaskbar = $false; "
             "Opacity = 0; StartPosition = 'CenterScreen'}; $owner.Show(); $owner.Activate(); ")
    if kind == "folder":
        dlg = ("$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
               f"$d.Description = {_ps_quote(title)}; $d.ShowNewFolderButton = $false; ")
        if initial:
            dlg += f"$d.SelectedPath = {_ps_quote(initial)}; "
        result = "$d.SelectedPath"
    else:
        dlg = (f"$d = New-Object System.Windows.Forms.OpenFileDialog; $d.Title = {_ps_quote(title)}; "
               "$d.CheckFileExists = $true; ")
        if filter_text:
            dlg += f"$d.Filter = {_ps_quote(filter_text)}; "
        if initial:
            dlg += f"$d.InitialDirectory = {_ps_quote(initial)}; "
        result = "$d.FileName"
    return (owner + dlg +
            "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
            f"if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {{ Write-Output {result} }}; "
            "$owner.Close()")


def pick(kind, title, initial="", filter_text=""):
    """고른 경로를 돌려줌. 취소하면 빈 문자열."""
    if os.name != "nt":
        raise PickerError("찾아보기 창은 윈도우에서만 열 수 있습니다. 경로를 직접 입력하세요.")
    script = build_script(kind, title, initial, filter_text)
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                           capture_output=True, timeout=TIMEOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PickerError(f"찾아보기 창을 열 수 없습니다 ({type(e).__name__}). 경로를 직접 입력하세요.")
    if r.returncode != 0:
        raise PickerError("찾아보기 창을 열 수 없습니다. 경로를 직접 입력하세요.")
    return r.stdout.decode("utf-8", "replace").strip()
