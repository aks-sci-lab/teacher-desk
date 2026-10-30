"""브라우저 단축키로 보낸 화면(에듀파인 등)을 수신함에 넣기.

브라우저 확장 프로그램(browser-extension/)이 단축키를 누른 순간의 화면 글자를 이 PC 의 업무 데스크로만 보냅니다.
- 설정에서 '단축키 연결'을 켜야 받습니다 (연결 키가 있어야 하고, 꺼 두면 모두 거절).
- 받은 글은 '에듀파인' 출처로 수신함에 들어가 날짜·마감을 찾습니다 (공문 파일을 읽을 때와 같은 규칙).
"""

import re
import secrets
from urllib.parse import urlparse

from . import extract
from .collect import add_text_item

MAX_TEXT = 200000
EXT_ORIGIN = re.compile(r"^(chrome-extension|moz-extension)://[A-Za-z0-9._-]+$")


def is_extension_origin(origin):
    return bool(origin and EXT_ORIGIN.match(origin))


def new_key():
    return secrets.token_urlsafe(24)


def clean_text(text):
    """화면 글자 정리: 줄 끝 공백·빈 줄 묶음·바로 이어서 되풀이되는 줄을 줄임."""
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    out, blank, last = [], 0, None
    for line in text.split("\n"):
        line = line.rstrip()
        if not line.strip():
            blank += 1
            continue
        if line.strip() == last:
            continue
        if blank and out:
            out.append("")
        out.append(line)
        blank, last = 0, line.strip()
    return "\n".join(out).strip()


def short_where(url):
    """기록용 위치: 주소의 호스트만 (경로·검색어에는 개인 정보가 있을 수 있어 남기지 않음)."""
    try:
        return urlparse(url).hostname or "알 수 없는 곳"
    except ValueError:
        return "알 수 없는 곳"


def receive(app, body):
    """확장 프로그램이 보낸 {text, title, url} 를 수신함에 저장. 같은 내용이면 duplicate."""
    text = clean_text(str(body.get("text") or ""))[:MAX_TEXT]
    if len(text) < 10:
        return {"ok": False, "error": "보낼 글이 거의 없습니다 (화면이 다 열린 뒤 다시 눌러 보세요)"}
    ext_title = " ".join(str(body.get("title") or "").split())[:200]
    title = extract.subject_line(text) or ext_title or None
    res = add_text_item(app.db, "edufine", text, title=title)
    where = short_where(str(body.get("url") or ""))
    if not res:
        return {"ok": True, "duplicate": True}
    app.db.log(f"단축키로 받음({where}): 일정 {res['events']}건, 마감 {res['deadlines']}건")
    return {"ok": True, "duplicate": False, "id": res["id"], "events": res["events"], "deadlines": res["deadlines"]}
