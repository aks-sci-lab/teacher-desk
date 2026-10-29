"""윈도우용 '설치 없이 쓰는' 업무 데스크 ZIP 만들기.

python.org 의 설치 없는 Python(embeddable)을 teacher-desk/python/ 에 넣고, PDF 읽기 도구(pypdf)를
teacher-desk/lib/ 에 넣어 묶습니다. 받는 사람은 압축을 풀고 start-desk.bat 만 누르면 됩니다.

  python tools/build_portable.py                       # python.org 에서 받아 dist/ 에 만듦
  python tools/build_portable.py --embed-zip 파일.zip   # 미리 받은 파일로 만듦 (시험용)

GitHub Actions(.github/workflows/desk-portable.yml)가 윈도우에서 이 스크립트를 돌리고, 결과물을 시험합니다.
"""

import argparse
import hashlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PYTHON_VERSION = "3.12.10"
EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
PYPDF = "pypdf==6.17.0"

DESK = Path(__file__).resolve().parents[1]   # 저장소 맨 위 = 업무 데스크
TOP = "TeacherDesk"  # ZIP 안 맨 위 폴더 (윈도우 압축 풀기에서 이름이 깨지지 않게 영문)
ZIP_NAME = "teacher-desk-windows.zip"

# 이 PC 의 설정·자료와 개발용 파일은 넣지 않음
SKIP_NAMES = {"config.json", "data", "__pycache__", "dist", "python", "lib", ".pytest_cache",
              "pass", ".git", ".github", "node_modules"}  # pass 는 ZIP 에서 teacher-desk 옆에 따로 둠

START_HERE = """교사 업무 데스크 (설치 없이 쓰는 윈도우용)
==========================================

1. 이 폴더(TeacherDesk)를 원하는 곳(예: 바탕화면)에 둡니다.
2. teacher-desk 폴더의 start-desk.bat 을 두 번 누르면 업무 데스크가 켜집니다.
   - Python 을 따로 설치하지 않아도 됩니다 (teacher-desk\\python 에 들어 있음).
   - 윈도우가 "PC 보호" 경고를 띄우면 [추가 정보] → [실행] 을 누르세요.
   - 명령 창 없이 백그라운드에서 실행됩니다. 브라우저를 닫아도 수집·텔레그램은 계속 동작합니다.
   - 완전히 끄려면 업무 데스크 설정에서 [프로그램 종료]를 누르세요.
3. 바탕화면에 바로 가기를 만들려면 teacher-desk 폴더의 make-desktop-shortcut.bat 을 두 번 누르세요.
4. 새 버전으로 바꾸려면 설정의 [프로그램 종료]를 누른 뒤 teacher-desk 폴더의 update-desk.bat 을 두 번 누르세요.
   (학생 명단·기록·설정은 그대로 남습니다)

자세한 사용법: teacher-desk\\README.md
Python {python} · PDF 읽기 {pypdf}
"""


def log(msg):
    print(msg, flush=True)


def copy_tree(src, dst):
    def ignore(folder, names):
        return [n for n in names if n in SKIP_NAMES or n.endswith((".pyc", ".tmp"))]
    shutil.copytree(src, dst, ignore=ignore)


def fetch_embed(dest):
    log(f"내려받기: {EMBED_URL}")
    with urllib.request.urlopen(EMBED_URL, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


def patch_pth(pydir):
    """embeddable Python 은 ._pth 파일에 적힌 곳에서만 모듈을 찾음 → 업무 데스크 폴더와 lib 를 추가."""
    pths = list(pydir.glob("python3*._pth"))
    if len(pths) != 1:
        raise SystemExit(f"._pth 파일을 찾을 수 없습니다: {pths}")
    pth = pths[0]
    lines = [l.strip() for l in pth.read_text(encoding="utf-8").splitlines()]
    paths = [l for l in lines if l and not l.startswith(("#", "import"))]
    rest = [l for l in lines if l not in paths]
    paths += [e for e in ("..", "..\\lib") if e not in paths]
    pth.write_text("\n".join(paths + rest) + "\n", encoding="utf-8")
    return pth


def build(out, embed_zip=None):
    out = Path(out)
    stage = out / TOP
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    log("업무 데스크·공유 페이지 복사")
    copy_tree(DESK, stage / "teacher-desk")
    copy_tree(DESK / "pass", stage / "pass")

    embed = Path(embed_zip) if embed_zip else fetch_embed(out / f"python-{PYTHON_VERSION}-embed-amd64.zip")
    digest = hashlib.sha256(embed.read_bytes()).hexdigest()
    log(f"Python embeddable SHA256: {digest}")
    pydir = stage / "teacher-desk" / "python"
    with zipfile.ZipFile(embed) as z:
        z.extractall(pydir)
    pth = patch_pth(pydir)
    log(f"{pth.name}: {pth.read_text(encoding='utf-8').split()}")

    log(f"PDF 읽기 도구 넣기: {PYPDF}")
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--no-deps", "--no-compile",
                    "--target", str(stage / "teacher-desk" / "lib"), PYPDF], check=True)

    (stage / "START-HERE.txt").write_text(
        START_HERE.format(python=PYTHON_VERSION, pypdf=PYPDF.split("==")[1]).replace("\n", "\r\n"),
        encoding="utf-8-sig", newline="")

    target = out / ZIP_NAME
    if target.exists():
        target.unlink()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(stage.rglob("*")):
            z.write(p, p.relative_to(out).as_posix())
    log(f"완성: {target} ({target.stat().st_size // 1024} KB)")
    return target


def main():
    for stream in (sys.stdout, sys.stderr):  # 윈도우에서 출력이 파일·파이프일 때도 한글이 깨지지 않게
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(DESK / "dist"))
    ap.add_argument("--embed-zip", help="미리 받은 python-*-embed-amd64.zip")
    args = ap.parse_args()
    build(args.out, args.embed_zip)


if __name__ == "__main__":
    main()
