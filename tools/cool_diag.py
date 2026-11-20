"""쿨메신저 첨부·사진 점검 (내용 없이 구조와 개수만 보여 줌).

학교 PC 에서:  py -I -X utf8 tools\\cool_diag.py   (다른 폴더에 두었으면 DESK_ROOT 에 앱 폴더)
- 쪽지 파일(.udb)의 표·열 이름과 줄 수
- 최근 쪽지 200개에서 사진이 어떤 모양으로 들어 있는지 (개수만)
- 첨부와 관련 있어 보이는 열의 값 '모양' (글자는 가, 영문은 a, 숫자는 0 으로 바꾸고 확장자만 남김)
- 한글·엑셀 PDF 변환이 되는지, 쿨메신저 받은 파일 폴더의 확장자별 개수
- 사용 기록(desk.log)에서 사진·첨부 관련 줄 (기록에는 원래 내용이 없음)
쪽지 내용·제목·이름·파일 이름은 보여 주지 않습니다.
"""

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(os.environ.get("DESK_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))

from desk import coolmsg  # noqa: E402

EXTS = ("hwpx", "hwp", "pdf", "xlsx", "xls", "docx", "doc", "pptx", "ppt", "zip", "txt", "jpg", "jpeg", "png", "gif", "bmp")
EXT_RE = re.compile(r"\.(" + "|".join(EXTS) + r")\b", re.I)
HINTS = ("file", "attach", "link", "host", "img", "image", "size", "count", "cnt", "flag", "type", "category", "url", "path")
MARKS = {"<img": r"<img\b", "cid:": r"cid:", "data:image": r"data:image", "file:": r"file:", "http 사진": r"<img[^>]+src=[\"']?https?:",
         "<object": r"<object\b", "background=": r"background\s*=", "RTF": r"^\{\\rtf", "\\pict": r"\\pict\b",
         "\\pngblip": r"\\pngblip", "\\jpegblip": r"\\jpegblip", "\\wmetafile": r"\\wmetafile", "\\emfblip": r"\\emfblip",
         "\\dibitmap": r"\\dibitmap", "\\wbitmap": r"\\wbitmap", "\\objdata": r"\\objdata", "HTML": r"<(html|body|div|p|span)\b"}


def shape(v):
    """값의 모양만: 글자 → 가/a/0, 알려진 확장자는 그대로, 200자까지."""
    if v is None:
        return "NULL"
    if isinstance(v, bytes):
        return f"<바이트 {len(v)}>"
    s = str(v)
    keep = {m.start(): m.group(0) for m in EXT_RE.finditer(s)}
    out, i = [], 0
    while i < len(s) and len(out) < 200:
        if i in keep:
            out.append(keep[i])
            i += len(keep[i])
            continue
        c = s[i]
        out.append("가" if "\uac00" <= c <= "\ud7a3" else "a" if c.isascii() and c.isalpha() else "0" if c.isdigit() else c)
        i += 1
    return "".join(out) + (f" …(모두 {len(s)}자)" if len(s) > 200 else "")


def main():
    cfg = {}
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("config.json 을 읽지 못함")
    print("== 설정 (켬/끔만)")
    for k in ("telegram_photos", "telegram_cool_text", "telegram_cool_summary", "telegram_cool_send", "ai_enabled"):
        print(f"  {k}: {cfg.get(k)}")
    print(f"  coolmessenger_folder 넣음: {bool(cfg.get('coolmessenger_folder'))}, coolmessenger_path 넣음: {bool(cfg.get('coolmessenger_path'))}")

    try:
        path = coolmsg.resolve(cfg.get("coolmessenger_name", ""), cfg.get("coolmessenger_path", ""))
        if not path.is_file():
            path = None
    except Exception as e:
        path = None
        print("쪽지 파일 찾기 실패:", type(e).__name__)
    if not path:
        cands = coolmsg.candidates()
        path = coolmsg.memo_path(cands[0]) if cands else None
    print("\n== 쪽지 파일:", "찾음" if path else "못 찾음")
    if path:
        with coolmsg.Snapshot(path) as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
            for t in tables:
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({coolmsg._q(t)})")]
                n = conn.execute(f"SELECT COUNT(*) FROM {coolmsg._q(t)}").fetchone()[0]
                print(f"  표 {t} ({n}줄): {', '.join(cols)}")
            info = coolmsg.detect(conn)
            print("\n== 찾은 쪽지 표·열:", {k: info.get(k) for k in ("table", "body", "title", "date", "sender")} if info else None)
            for t in tables:
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({coolmsg._q(t)})")]
                rows = conn.execute(f"SELECT * FROM {coolmsg._q(t)} ORDER BY rowid DESC LIMIT 50").fetchall()
                if not rows:
                    continue
                print(f"\n== 표 {t}: 열마다 최근 {len(rows)}줄 중 값이 있는 줄 수, 첨부·링크 관련 열은 값 모양 3개")
                for ci, c in enumerate(cols):
                    vals = [r[ci] for r in rows]
                    filled = [v for v in vals if v not in (None, "", 0, b"")]
                    types = Counter(type(v).__name__ for v in filled)
                    line = f"  {c}: {len(filled)}/{len(vals)} {dict(types)}"
                    if any(h in c.lower() for h in HINTS) or (info and t == info["table"] and c not in (info["body"], info["title"], info["sender"])):
                        sh = [shape(v) for v in filled[:3]]
                        line += "  모양: " + " | ".join(sh)
                    ext = Counter(m.lower() for v in filled if isinstance(v, str) for m in EXT_RE.findall(v))
                    if ext and not (info and t == info["table"] and c == info["body"]):
                        line += f"  확장자: {dict(ext)}"
                    print(line)
            if info:
                q = f"SELECT {coolmsg._q(info['body'])} FROM {coolmsg._q(info['table'])} ORDER BY rowid DESC LIMIT 200"
                marks, kinds, total, with_img, exts = Counter(), Counter(), 0, 0, Counter()
                for (v,) in conn.execute(q):
                    total += 1
                    s = coolmsg.raw_body(v) or ""
                    for name, pat in MARKS.items():
                        if re.search(pat, s, re.I | re.M):
                            marks[name] += 1
                    imgs = coolmsg.body_images(v)
                    with_img += bool(imgs)
                    for src in imgs:
                        kinds[coolmsg.image_kind(src)] += 1
                    exts.update(m.lower() for m in EXT_RE.findall(coolmsg.clean_body(v)))
                print(f"\n== 최근 쪽지 {total}개의 내용 모양 (쪽지 수): {dict(marks)}")
                print(f"  앱이 사진을 찾은 쪽지 {with_img}개, 사진 종류: {dict(kinds)}")
                print(f"  본문 글에 적힌 파일 확장자: {dict(exts)}")
                # 사진 표시가 있는데 앱이 못 찾은 쪽지: <img 주변 모양
                shown = 0
                for (v,) in conn.execute(q):
                    s = coolmsg.raw_body(v) or ""
                    if coolmsg.body_images(v):
                        continue
                    m = re.search(r"<img\b[^>]{0,200}|\\pict\b.{0,120}|<object\b[^>]{0,200}", s, re.I | re.S)
                    if m and shown < 5:
                        print("  못 찾은 사진 모양:", shape(m.group(0))[:240])
                        shown += 1
                # 앱이 찾은 사진 주소의 앞부분 모양 (주소 글자는 가림)
                shown = 0
                for (v,) in conn.execute(q):
                    for src in coolmsg.body_images(v):
                        if shown < 5 and not src.startswith("data:"):
                            print("  찾은 사진 주소 모양:", shape(src[:120]))
                            shown += 1

    base = Path(os.environ.get("LOCALAPPDATA", "")) / "CoolMessenger"
    print("\n== 쿨메신저 자료 폴더 (확장자별 파일 수, 2단계까지)")
    if base.is_dir():
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            c = Counter()
            for p in list(d.rglob("*"))[:20000]:
                if p.is_file():
                    c[p.suffix.lower() or "(없음)"] += 1
            print(f"  {d.name}: {dict(c.most_common(8))}")
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Jiransoft\CoolMsg50\Option\GetFile") as k:
            down = winreg.QueryValueEx(k, "DownPath")[0]
        dp = Path(down)
        c = Counter(p.suffix.lower() for p in dp.iterdir() if p.is_file()) if dp.is_dir() else None
        print("  받은 파일 폴더(DownPath):", "있음" if dp.is_dir() else "없음", dict(c or {}))
        print("  앱 설정 받은 파일 폴더와 같음:", Path(cfg.get("coolmessenger_folder") or "-").resolve() == dp.resolve())
    except (OSError, ImportError) as e:
        print("  DownPath 레지스트리 없음:", type(e).__name__)
    try:
        import winreg
        for prog in ("HWPFrame.HwpObject", "Excel.Application"):
            try:
                winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog).Close()
                print(f"  {prog}: 설치됨")
            except OSError:
                print(f"  {prog}: 없음")
    except ImportError:
        pass

    print("\n== 업무 데스크 수신함 (최근 14일 쿨메신저 쪽지 개수만)")
    try:
        import sqlite3
        db = sqlite3.connect(f"file:{ROOT / 'data' / 'desk.db'}?mode=ro", uri=True)
        for label, where in (("모두", ""), ("사진 목록 있음", "AND images IS NOT NULL AND images NOT IN ('', '[]')"),
                             ("원본 파일 있음(받은 파일 폴더)", "AND file_path IS NOT NULL AND file_path != ''")):
            n = db.execute("SELECT COUNT(*) FROM items WHERE source LIKE 'cool%' AND received_at >= date('now', '-14 day') "
                           + where).fetchone()[0]
            print(f"  {label}: {n}")
        print("  자료 종류별:", dict(db.execute("SELECT source, COUNT(*) FROM items WHERE received_at >= date('now', '-14 day') GROUP BY source").fetchall()))
        db.close()
    except Exception as e:
        print("  desk.db 읽기 실패:", type(e).__name__, e)

    print("\n== 사용 기록에서 사진·첨부·쿨메신저 관련 줄 (최근 40줄)")
    logs = sorted((ROOT / "data" / "logs").glob("desk.log*"), key=lambda p: p.stat().st_mtime)
    hits = []
    for p in logs:
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if re.search(r"사진|photo|image|첨부|attach|cool|쪽지|sendPhoto|sendDocument", line, re.I):
                    hits.append(line[:200])
        except OSError:
            pass
    for line in hits[-40:]:
        print(" ", line)


if __name__ == "__main__":
    main()
