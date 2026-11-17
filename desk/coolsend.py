"""쿨메신저로 쪽지 보내기 — 쿨메신저 창을 대신 조작 (0.6.5, 선생님 요청 2026-11-17).

쿨메신저에는 다른 프로그램이 쪽지를 보내게 하는 공개 기능이 없고, 서버와는 쿨메신저만 쓰는 암호화 방식으로
주고받습니다(설치 폴더 점검 결과). 그래서 이 PC 에 켜진 쿨메신저 창을 사람 대신 누릅니다:

1. 메인 창 조직도(SysTreeView32 3013)에서 받는 사람을 찾아 두 번 클릭 → 새 쪽지 창(보내기 단추 3249)
2. 받는 사람 칩이 정확히 그 사람 1명인지 확인
3. 제목 칸(Edit 4362)에 글 넣기, 본문(크롬 방식 편집기)은 클립보드로 붙여넣기(원래 클립보드는 되돌림)
4. 받는 사람을 한 번 더 확인하고 [보내기] 단추를 진짜 마우스로 클릭 → 창이 닫히면 보낸 것

지키는 것:
- 선생님이 텔레그램에서 받는 사람·제목·내용을 보고 [보내기]를 누른 것만 보냄 (이 모듈은 확인 단추를 부르지 않음)
- 클릭하기 전에 그 자리가 정말 그 단추·그 사람인지, 맨 앞 창이 쿨메신저인지 확인. 어긋나면 멈춤
- 먼저 열려 있던 쪽지 창은 [닫기]로 닫고 시작 (선생님 결정, 그 창의 글은 보내지 않고 버려짐)
- 중간에 멈추면 이번에 쓰던 쪽지 창은 열어 둠(보내지 않음) — 선생님이 PC 에서 마저 보내거나 닫음
- 화면이 잠겨 있으면 시작하지 않음 (윈도우가 잠금 화면에서 클릭·입력을 막음)
- 한 번에 한 통만 (다른 보내기가 진행 중이면 기다리지 않고 거절)
- 기록(db.log)에는 받는 사람 이름·내용을 남기지 않음
"""

import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import coolmsg, winui

EXE = "CoolMessenger.exe"
ID_TREE, ID_SEARCH = 3013, 1708            # 메인 창: 조직도, 검색칸
ID_SEND, ID_CLOSE, ID_TITLE = 3249, 1440, 4362   # 쪽지 쓰기 창: 보내기, 닫기, 제목
ID_CHIP_DELETE = 8010                      # 받는 사람 칩마다 붙은 지우기 단추
ID_TO_SELF = 1661                          # [나에게 보내기] (나에게는 예약전송으로만 보내짐)
ID_MANAGER_OPEN = 3016                     # 메인 창: 메시지관리함 열기
ID_LIST, ID_INBOX, ID_SAVE_ALL = 3195, 4337, 3320   # 메시지관리함: 쪽지 목록, 받은메시지, 모든파일 저장
DOWNLOAD_KEY = r"Software\Jiransoft\CoolMsg50\Option\GetFile"   # DownPath: 쿨메신저가 받은 파일을 두는 폴더
SAVE_WAIT, SETTLE = 60.0, 2.0
ATTACH_LIMIT = 10
BODY_CLASS = "Chrome_RenderWidgetHostHWND"
OPEN_WAIT, CLOSE_WAIT = 6.0, 10.0
TITLE_LIMIT, BODY_LIMIT = 100, 4000
REPLY_PREFIX = "RE: "
# 조직도 글 → 이름: 학교 PC 는 '(홍길동)8661(교무기획2)'·'(홍길동)' 모양 (앞 괄호 안이 이름), '홍길동 1234' 모양도 받음
P_LABEL_NAME = re.compile(r"^\(?\s*([가-힣A-Za-z]+(?:\s[A-Za-z]+)*)")

_lock = threading.Lock()


class SendError(Exception):
    """보내지 못함. left_open 이면 쓰던 쪽지 창이 PC 에 열려 있음."""

    def __init__(self, message, left_open=False):
        super().__init__(message)
        self.left_open = left_open


def label_name(label):
    """조직도 항목 글에서 이름만 ('홍길동 1234/과학' → '홍길동')."""
    m = P_LABEL_NAME.match((label or "").strip())
    return m.group(1) if m else ""


def clean_name(name):
    """'홍길동(과학과)'·'홍길동 선생님' → '홍길동' (보낸 사람 칸·텔레그램에 쓴 이름)."""
    name = (name or "").strip()
    if name.startswith("("):   # '(홍길동)8661' 모양은 괄호 안이 이름
        name = name[1:]
    name = re.split(r"[()\[/<]", name, maxsplit=1)[0].strip()
    name = re.sub(r"\s*(선생님|샘|쌤|님|교사|부장|교감|교장)$", "", name)
    return label_name(name)


def reply_title(title):
    title = " ".join((title or "").split())
    if title.upper().startswith(("RE:", "RE :")):
        return title[:TITLE_LIMIT]
    return (REPLY_PREFIX + title)[:TITLE_LIMIT] if title else REPLY_PREFIX.strip()


class WinUI:
    """실제 윈도우 조작. 시험에서는 같은 메서드를 가진 가짜로 바꿔 끼움."""

    def require(self):
        winui.need_windows()
        if winui.screen_locked():
            raise SendError("PC 화면이 잠겨 있어 보낼 수 없습니다. 잠금을 풀면 다시 보내 주세요.")

    def main_window(self):
        for h in winui.top_windows(EXE, visible_only=False):   # 트레이로 숨긴 메인 창도
            tree = winui.item(h, ID_TREE)
            if tree and winui.class_name(tree) == "SysTreeView32":
                return h
        raise SendError("쿨메신저가 켜져 있지 않거나 로그인 전입니다. 쿨메신저를 켜고 로그인해 주세요.")

    def compose_windows(self):
        return [h for h in winui.top_windows(EXE) if winui.item(h, ID_SEND)]

    def people(self, main):
        """조직도의 사람 [(핸들, 글, 부서 글)] — 아래 항목이 없는 것을 사람으로 봄."""
        tree = winui.item(main, ID_TREE)
        items = winui.tree_items(tree)
        out, path = [], []
        for i, (h, label, depth) in enumerate(items):
            path[depth:] = [label]
            leaf = i + 1 >= len(items) or items[i + 1][2] <= depth
            if leaf and depth > 0 and label_name(label):
                out.append((h, label, path[depth - 1]))
        return out

    def open_compose(self, main, handle, label):
        """조직도 항목을 두 번 클릭해 새 쪽지 창을 엶. 그 자리가 그 사람인지 확인하고 누름."""
        tree = winui.item(main, ID_TREE)
        before = set(self.compose_windows())
        if not winui.bring_front(main):
            raise SendError("쿨메신저 창을 앞으로 가져오지 못했습니다.")
        pt = winui.tree_item_center(tree, handle)
        if not pt or not winui.tree_hit(tree, pt, handle) or not winui.window_at(pt, tree):
            raise SendError("조직도에서 받는 사람 자리를 확인하지 못해 멈췄습니다.")
        if winui.foreground() != main:
            raise SendError("다른 창이 앞에 있어 멈췄습니다.")
        winui.click_at(*pt, double=True)
        end = time.monotonic() + OPEN_WAIT
        while time.monotonic() < end:
            new = [h for h in self.compose_windows() if h not in before]
            if new:
                time.sleep(0.5)   # 칩·편집기가 다 그려질 때까지
                return new[0]
            time.sleep(0.2)
        raise SendError("쪽지 쓰기 창이 열리지 않았습니다.")

    def recipients(self, compose):
        """보이는 받는 사람·참조 칩의 글 (칩마다 지우기 단추 8010 이 붙어 있음)."""
        out = []
        for h in winui.children(compose):
            if winui.is_visible(h) and winui.item_direct(h, ID_CHIP_DELETE):
                out.append(winui.text(h))
        return out

    def set_title(self, compose, title):
        edit = winui.item(compose, ID_TITLE)
        return bool(edit) and winui.set_text(edit, title)

    def paste_body(self, compose, body):
        """본문 붙여넣기. 못 하면 까닭을 담은 SendError (다음에 어디서 멈췄는지 알 수 있게)."""
        target = next((h for h in winui.children(compose)
                       if winui.class_name(h) == BODY_CLASS and winui.is_visible(h)), None)
        if not target:
            raise SendError("쪽지 창의 본문 칸을 찾지 못해 보내지 않았습니다.", left_open=True)
        saved = winui.get_clipboard_text()
        try:
            if not winui.set_clipboard_text(body):
                raise SendError("클립보드에 내용을 넣지 못해 보내지 않았습니다.", left_open=True)
            if not winui.bring_front(compose):
                raise SendError("쪽지 창을 앞으로 가져오지 못해 보내지 않았습니다.", left_open=True)
            pt = winui.center(target)
            if not winui.window_at(pt, target):
                raise SendError("본문 칸 위를 다른 창이 가리고 있어 보내지 않았습니다.", left_open=True)
            winui.click_at(*pt)
            time.sleep(0.2)
            winui.hotkey(winui.VK_CONTROL, ord("V"))
            time.sleep(0.4)
            return True
        finally:
            winui.set_clipboard_text(saved)

    def close_compose(self, compose):
        """열려 있던 쪽지 창을 [닫기](1440)로 닫음 (선생님 결정 2026-11-17: 열려 있으면 닫고 보냄). 닫혔으면 True."""
        button = winui.item(compose, ID_CLOSE)
        if not button or not winui.bring_front(compose):
            return False
        pt = winui.center(button)
        if not winui.window_at(pt, button) or winui.foreground() != compose:
            return False
        winui.click_at(*pt)
        end = time.monotonic() + 3
        while time.monotonic() < end:
            if not winui.is_window(compose) or not winui.is_visible(compose):
                return True
            time.sleep(0.2)
        return False

    def press_send(self, compose):
        button = winui.item(compose, ID_SEND)
        if not button or not winui.bring_front(compose):
            return False
        pt = winui.center(button)
        if not winui.window_at(pt, button) or winui.foreground() != compose:
            return False
        winui.click_at(*pt)
        end = time.monotonic() + CLOSE_WAIT
        while time.monotonic() < end:
            if not winui.is_window(compose) or not winui.is_visible(compose):
                return True
            time.sleep(0.2)
        return False


class WinAttachUI(WinUI):
    """첨부 받기: 메시지관리함에서 그 쪽지를 골라 [모든파일 저장]."""

    def manager_window(self, main):
        def find():
            for h in winui.top_windows(EXE, visible_only=False):
                lv = winui.item(h, ID_LIST)
                if lv and winui.class_name(lv) == "SysListView32":
                    return h
            return None
        h = find()
        if h:
            return h
        if not winui.click_checked(winui.item(main, ID_MANAGER_OPEN), main):
            raise SendError("쿨메신저 메시지관리함을 열지 못했습니다.")
        end = time.monotonic() + OPEN_WAIT
        while time.monotonic() < end:
            time.sleep(0.3)
            h = find()
            if h:
                time.sleep(0.8)
                return h
        raise SendError("쿨메신저 메시지관리함이 열리지 않았습니다.")

    def show_inbox(self, manager):
        if not winui.bring_front(manager):
            raise SendError("메시지관리함을 앞으로 가져오지 못했습니다.")
        if not winui.click_checked(winui.item(manager, ID_INBOX), manager):
            raise SendError("메시지관리함의 [받은메시지]를 누르지 못했습니다.")
        time.sleep(1.0)

    def rows(self, manager):
        return winui.list_rows(winui.item(manager, ID_LIST), columns=4, limit=1000)

    def select_row(self, manager, i):
        lv = winui.item(manager, ID_LIST)
        if not winui.bring_front(manager):
            return False
        pt = winui.list_item_center(lv, i)
        if not pt or not winui.window_at(pt, lv) or winui.foreground() != manager:
            return False
        winui.click_at(*pt)
        time.sleep(1.0)
        return True

    def save_all(self, manager):
        return winui.click_checked(winui.item(manager, ID_SAVE_ALL), manager)

    def download_dir(self):
        path = winui.registry_value(DOWNLOAD_KEY, "DownPath")
        return Path(os.path.expandvars(path)) if path else None


def _squash(s):
    return re.sub(r"\s+", "", s or "")


def match_rows(rows, title, received_at, sender=""):
    """메시지관리함 목록 [[보낸사람, 제목(30자에서 잘림), '2026/11/17 13:45:00 (화)', 첨부]] 에서 그 쪽지 줄 번호들."""
    try:
        when = datetime.strptime(received_at[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return []
    want = _squash(title)
    out = []
    for i, row in enumerate(rows):
        if len(row) < 4:
            continue
        at = coolmsg.parse_when(row[2])
        if not at or abs(at - when) > timedelta(minutes=1):
            continue
        got = _squash(row[1])
        if got and not (want.startswith(got[:20]) or got.startswith(want[:20])):
            continue
        if sender and clean_name(sender) and clean_name(sender) not in row[0]:
            continue
        out.append(i)
    return out


def _files(folder):
    out = {}
    try:
        for p in Path(folder).iterdir():
            if p.is_file() and not p.name.startswith(("~$", ".")):
                st = p.stat()
                out[p] = (st.st_mtime_ns, st.st_size)
    except OSError:
        pass
    return out


def fetch_attachments(title, received_at, sender="", ui=None, log=None):
    """받은 쿨메신저 쪽지의 첨부를 쿨메신저가 받게 하고(모든파일 저장) 새로 생긴 파일 [Path] 를 돌려줌."""
    ui = ui or WinAttachUI()
    if not _lock.acquire(blocking=False):
        raise SendError("쿨메신저 창을 다른 일로 쓰는 중입니다. 끝나면 다시 보내 주세요.")
    try:
        ui.require()
        folder = ui.download_dir()
        if not folder or not Path(folder).is_dir():
            raise SendError("쿨메신저의 받은 파일 저장 폴더를 찾지 못했습니다 (쿨메신저 설정 → 파일 받기).")
        main = ui.main_window()
        manager = ui.manager_window(main)
        ui.show_inbox(manager)
        rows = ui.rows(manager)
        found = match_rows(rows, title, received_at, sender)
        if not found:
            raise SendError("메시지관리함 받은메시지에서 그 쪽지를 찾지 못했습니다 (오래되어 목록에 없거나 지워졌을 수 있음).")
        if len(found) > 1:
            raise SendError("메시지관리함에서 같은 쪽지로 보이는 것이 여럿이라 멈췄습니다. PC 에서 직접 받아 주세요.")
        if not (rows[found[0]][3] or "").strip():
            raise SendError("그 쪽지에는 첨부 파일이 없습니다.")
        # 저장 폴더에 같은 이름이 있어도 늘 새로 받음 (선생님 결정 2026-11-17: 쿨메신저가 '이름(1).pdf' 처럼 번호를 붙임)
        before = _files(folder)
        if not ui.select_row(manager, found[0]):
            raise SendError("메시지관리함에서 그 쪽지를 고르지 못했습니다.")
        # 고른 뒤 다시 읽어 같은 쪽지인지 확인 (그 사이 새 쪽지가 와서 줄이 밀렸을 수 있음)
        again = ui.rows(manager)
        if found[0] >= len(again) or again[found[0]] != rows[found[0]]:
            raise SendError("그 사이 목록이 바뀌어 멈췄습니다. 다시 보내 주세요.")
        if not ui.save_all(manager):
            raise SendError("[모든파일 저장]을 누르지 못했습니다.")
        new, end, last, stable = [], time.monotonic() + SAVE_WAIT, None, 0.0
        while time.monotonic() < end:   # 새 파일이 생기고 크기가 SETTLE 초 동안 그대로면 끝
            time.sleep(0.5)
            now = _files(folder)
            new = sorted(p for p, v in now.items() if before.get(p) != v)
            if new and now == last:
                stable += 0.5
                if stable >= SETTLE:
                    break
            else:
                stable = 0.0
            last = now
        if not new:
            raise SendError("파일이 저장되지 않았습니다 (서버 보관 기간이 지났거나 보낸 사람 PC 가 꺼져 있을 수 있음). PC 의 쿨메신저를 확인해 주세요.")
        if log:
            log(f"쿨메신저 첨부 {len(new)}개를 받았습니다 (텔레그램 요청)")
        return new[:ATTACH_LIMIT]
    except winui.Unsupported as e:
        raise SendError(str(e))
    finally:
        _lock.release()


def find_people(name, ui=None):
    """조직도에서 이름이 같은 사람들 [{'label', 'dept', 'name'}] (부서로 구분)."""
    ui = ui or WinUI()
    ui.require()
    want = clean_name(name)
    if not want:
        return []
    main = ui.main_window()
    return [{"label": label, "dept": dept, "name": want}
            for _, label, dept in ui.people(main) if label_name(label) == want]


def send(person, title, body, ui=None, log=None):
    """person({'label','dept','name'})에게 쪽지 한 통. 보냈으면 안내 글, 못 보냈으면 SendError."""
    ui = ui or WinUI()
    title = " ".join((title or "").split())[:TITLE_LIMIT]
    body = (body or "").strip()
    if not body:
        raise SendError("보낼 내용이 없습니다.")
    if len(body) > BODY_LIMIT:
        raise SendError(f"내용이 너무 깁니다 ({BODY_LIMIT:,}자까지).")
    if not _lock.acquire(blocking=False):
        raise SendError("다른 쪽지를 보내는 중입니다. 끝나면 다시 보내 주세요.")
    try:
        ui.require()
        main = ui.main_window()
        matches = [(h, label, dept) for h, label, dept in ui.people(main)
                   if label == person["label"] and dept == person["dept"]]
        if len(matches) != 1:
            raise SendError("조직도에서 받는 사람을 다시 찾지 못했습니다 (조직도가 바뀌었을 수 있음). 다시 보내 주세요.")
        for old in ui.compose_windows():   # 열려 있던 쪽지 창은 닫고 시작 (쓰던 글은 보내지 않고 버림)
            if not ui.close_compose(old):
                raise SendError("쿨메신저에 열려 있던 쪽지 창을 닫지 못했습니다. 그 창을 닫은 뒤 다시 보내 주세요.")
        if ui.compose_windows():
            raise SendError("쿨메신저에 열려 있던 쪽지 창을 닫지 못했습니다. 그 창을 닫은 뒤 다시 보내 주세요.")
        compose = ui.open_compose(main, matches[0][0], person["label"])

        def check_recipient():
            chips = ui.recipients(compose)
            if len(chips) != 1 or (chips[0] and person["name"] not in chips[0]):
                raise SendError("쪽지 창의 받는 사람이 예상과 달라 보내지 않았습니다. PC 의 쪽지 창을 확인해 주세요.",
                                left_open=True)

        check_recipient()
        if title and not ui.set_title(compose, title):
            raise SendError("제목을 넣지 못해 보내지 않았습니다. PC 의 쪽지 창을 확인해 주세요.", left_open=True)
        if not ui.paste_body(compose, body):
            raise SendError("내용을 넣지 못해 보내지 않았습니다. PC 의 쪽지 창을 확인해 주세요.", left_open=True)
        check_recipient()
        if not ui.press_send(compose):
            raise SendError("[보내기]를 누르지 못했거나 창이 닫히지 않았습니다. PC 의 쪽지 창을 확인해 주세요.",
                            left_open=True)
        if log:
            log("쿨메신저 쪽지를 보냈습니다 (텔레그램에서 확인함)")
        return "✅ 쿨메신저로 보냈습니다."
    except SendError as e:
        if log:
            log(f"쿨메신저 쪽지 보내기 멈춤: {e}", "warn")
        raise
    except winui.Unsupported as e:
        raise SendError(str(e))
    finally:
        _lock.release()
