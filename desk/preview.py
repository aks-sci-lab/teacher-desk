"""이 PC의 한글·Excel로 문서를 PDF로 변환 (원본은 고치지 않음)."""

import base64
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

EXTENSIONS = (".hwp", ".hwpx", ".xlsx")
MAX_BYTES = 50 * 1024 * 1024
_lock = threading.Lock()


class PreviewError(Exception):
    """원문 창에 표시할 변환 실패 안내."""


_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$app = $null
$book = $null
try {
    if ([IO.Path]::GetExtension($env:DESK_PREVIEW_SOURCE) -eq '.xlsx') {
        $app = New-Object -ComObject Excel.Application
        $app.Visible = $false
        $app.DisplayAlerts = $false
        $app.AutomationSecurity = 3
        $app.EnableEvents = $false
        $book = $app.Workbooks.Open($env:DESK_PREVIEW_SOURCE, 0, $true)
        $book.ExportAsFixedFormat(0, $env:DESK_PREVIEW_TARGET)
    } else {
        $app = New-Object -ComObject HWPFrame.HwpObject
        if (-not $app.Open($env:DESK_PREVIEW_SOURCE, '', 'forceopen:true;versionwarning:false')) {
            throw 'open failed'
        }
        if (-not $app.SaveAs($env:DESK_PREVIEW_TARGET, 'PDF', '')) {
            throw 'save failed'
        }
    }
} catch {
    exit 1
} finally {
    if ($null -ne $book) {
        $book.Close($false)
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($book)
    }
    if ($null -ne $app) {
        $app.Quit()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($app)
    }
}
"""


def _convert(source: Path, target: Path) -> None:
    env = os.environ.copy()
    env.update(DESK_PREVIEW_SOURCE=str(source), DESK_PREVIEW_TARGET=str(target))
    command = base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")
    shell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    process = subprocess.Popen(
        [str(shell), "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", command],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        code = process.wait(timeout=90)
    except subprocess.TimeoutExpired:
        # 다른 한글·Excel 창을 종료하지 않고 이 변환 작업의 프로세스만 정리.
        try:
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        raise PreviewError("변환 시간이 90초를 넘었습니다. 한글의 파일 접근 허용 창이나 암호 입력 창을 확인하세요.")
    if code != 0:
        raise PreviewError("PDF 변환에 실패했습니다. 한글·Excel 설치 여부와 파일 접근 허용 창을 확인하세요. 원본 프로그램으로도 열 수 있습니다.")


def pdf_bytes(path: Path) -> bytes:
    path = Path(path)
    if sys.platform != "win32":
        raise PreviewError("한글·엑셀 원본 보기는 Windows의 한글·Excel 설치가 필요합니다.")
    if path.suffix.lower() not in EXTENSIONS:
        raise PreviewError("PDF 변환을 지원하지 않는 형식입니다.")
    if not _lock.acquire(blocking=False):
        raise PreviewError("다른 문서를 변환 중입니다. 잠시 뒤 다시 눌러 주세요.")
    try:
        if path.stat().st_size > MAX_BYTES:
            raise PreviewError("파일이 너무 큽니다 (50MB 초과).")
        # 작업용 복사본과 PDF는 요청이 끝나면 지움. 외부 서비스·영구 캐시를 쓰지 않음.
        with tempfile.TemporaryDirectory(prefix="teacher-desk-preview-") as tmp:
            source = Path(tmp) / ("source" + path.suffix.lower())
            target = Path(tmp) / "preview.pdf"
            shutil.copyfile(path, source)
            _convert(source, target)
            if not target.is_file() or target.stat().st_size > MAX_BYTES:
                raise PreviewError("PDF가 만들어지지 않았거나 50MB를 넘었습니다.")
            data = target.read_bytes()
            if not data.startswith(b"%PDF-"):
                raise PreviewError("올바른 PDF가 만들어지지 않았습니다.")
            return data
    except subprocess.TimeoutExpired as e:
        raise PreviewError("변환 작업을 종료하지 못했습니다. 작업 표시줄의 변환용 한글·Excel 창을 확인하세요.") from e
    except OSError as e:
        raise PreviewError("원문 변환용 파일을 읽거나 쓸 수 없습니다. 파일과 프로그램 설치 상태를 확인하세요.") from e
    finally:
        _lock.release()
