"""선생님이 업무 데스크 화면을 실제로 쓴 기록 (0.5.54) — 브리핑이 '마지막으로 닫은 이후'를 분석하는 기준.

0.5.77 (사용자 요청): 기준을 '마지막 로그아웃' = 선생님이 업무 데스크를 마지막으로 떠난 시각으로.
- 떠난 시각(left_at)은 ① 마지막 화면을 닫을 때(pagehide → /api/ui/bye) ② 프로그램을 다시 켤 때
  (꺼져 있던 동안은 쓰지 않았으므로 마지막으로 쓴 시각) 남김.
- 닫고 10분 안에 다시 쓰면(새로 고침·실수로 닫음·업데이트 뒤 다시 켜짐) 떠난 것으로 치지 않음 → 기간이 줄지 않음.
- 탭만 바꾸는 것은 닫은 것이 아님 (pagehide 는 창·탭을 닫거나 새로 고칠 때만).
- 떠난 기록이 없으면(창을 켜 둔 채 쉼) 예전처럼 3시간 넘게 안 쓰면 새 접속, 마지막으로 쓴 시각부터.

- 창을 켜 둔 것만으로는 접속이 아님: 화면이 '최근에 누르거나 타자를 쳤다'(X-Desk-Active)고 알려 줄 때만 씀.
- 3시간 넘게 쓰지 않다가 다시 쓰면 새 접속. 그때 바로 앞 접속의 마지막 시각(prev_end)을 남김.
  예) 금요일 16:20 에 마지막으로 쓰고 월요일 08:40 에 열면 → 금 16:20 이후 들어온 자료를 분석.
- 시각만 남기고 화면 내용은 남기지 않음 (data/desk.db 의 meta 'visit').
"""

from datetime import datetime, timedelta

VISIT_GAP = timedelta(hours=3)
RETURN_GAP = timedelta(minutes=10)   # 닫고 이만큼 안에 돌아오면 같은 접속 (새로 고침 등)
FIRST_DAYS = 7             # 처음 쓰는 날(기록 없음)에는 지난 7일
MAX_DAYS = 14              # 방학처럼 오래 쉬었어도 최근 14일까지만 (AI 가 한 번에 읽을 수 있는 양)
FMT = "%Y-%m-%d %H:%M"


def _parse(s):
    try:
        return datetime.strptime(s, FMT)
    except (TypeError, ValueError):
        return None


def current(db):
    return db.get_meta("visit") or {}


def record_active(db, now=None):
    """화면을 쓰는 중이라는 신호. 분이 바뀔 때만 저장. 새 접속이 시작되면 True."""
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    v = current(db)
    last = _parse(v.get("last_active"))
    left = _parse(v.get("left_at"))
    stamp = now.strftime(FMT)
    prev = None
    if last is None:
        prev = False                               # 처음 쓰는 날
    elif left is not None and now - left >= RETURN_GAP:
        prev = v.get("left_at")                    # 닫고 떠났다가 돌아옴 → 닫은 시각부터
    elif now - last >= VISIT_GAP:
        prev = v.get("last_active")                # 켜 둔 채 오래 쉼 → 마지막으로 쓴 시각부터
    if prev is not None:
        db.set_meta("visit", {"start": stamp, "prev_end": prev or None, "last_active": stamp})
        return True
    if v.get("last_active") != stamp or "left_at" in v:
        v = {**v, "last_active": stamp}
        v.pop("left_at", None)                     # 곧 돌아옴 (새로 고침 등) → 떠난 것이 아님
        db.set_meta("visit", v)
    return False


def record_leave(db, now=None):
    """마지막 화면을 닫았을 때. 쓰던 기록이 있을 때만 떠난 시각을 남김."""
    v = current(db)
    if not v.get("last_active"):
        return
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    db.set_meta("visit", {**v, "left_at": now.strftime(FMT)})


def app_started(db):
    """프로그램을 켤 때: 앞서 떠난 기록이 없으면 마지막으로 쓴 시각을 떠난 시각으로 (꺼져 있는 동안은 안 씀)."""
    v = current(db)
    if v.get("last_active") and not v.get("left_at"):
        db.set_meta("visit", {**v, "left_at": v["last_active"]})


def since(db, now=None):
    """브리핑이 분석할 시작 시각 'YYYY-MM-DD HH:MM' (마지막으로 닫은·떠난 시각, 처음이면 7일 전, 길어도 14일)."""
    now = now or datetime.now()
    prev = _parse(current(db).get("prev_end"))
    floor = now - timedelta(days=MAX_DAYS)
    start = prev if prev else now - timedelta(days=FIRST_DAYS)
    return max(start, floor).strftime(FMT)


def info(db, now=None):
    """화면에 보일 접속 정보: 이번 접속 시작, 마지막으로 닫은 시각(prev_end), 분석 시작 시각, 처음인지."""
    v = current(db)
    return {"start": v.get("start"), "prev_end": v.get("prev_end"), "since": since(db, now),
            "first": not v.get("prev_end")}
