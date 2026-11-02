"""선생님이 업무 데스크 화면을 실제로 쓴 기록 (0.5.54) — 브리핑이 '지난 접속 이후'를 분석하는 기준.

- 창을 켜 둔 것만으로는 접속이 아님: 화면이 '최근에 누르거나 타자를 쳤다'(X-Desk-Active)고 알려 줄 때만 씀.
- 3시간 넘게 쓰지 않다가 다시 쓰면 새 접속. 그때 바로 앞 접속의 마지막 시각(prev_end)을 남김.
  예) 금요일 16:20 에 마지막으로 쓰고 월요일 08:40 에 열면 → 금 16:20 이후 들어온 자료를 분석.
- 시각만 남기고 화면 내용은 남기지 않음 (data/desk.db 의 meta 'visit').
"""

from datetime import datetime, timedelta

VISIT_GAP = timedelta(hours=3)
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
    stamp = now.strftime(FMT)
    if last is None or now - last >= VISIT_GAP:
        db.set_meta("visit", {"start": stamp, "prev_end": v.get("last_active"), "last_active": stamp})
        return True
    if v.get("last_active") != stamp:
        db.set_meta("visit", {**v, "last_active": stamp})
    return False


def since(db, now=None):
    """브리핑이 분석할 시작 시각 'YYYY-MM-DD HH:MM' (지난 접속의 마지막 시각, 처음이면 7일 전, 길어도 14일)."""
    now = now or datetime.now()
    prev = _parse(current(db).get("prev_end"))
    floor = now - timedelta(days=MAX_DAYS)
    start = prev if prev else now - timedelta(days=FIRST_DAYS)
    return max(start, floor).strftime(FMT)


def info(db, now=None):
    """화면에 보일 접속 정보: 이번 접속 시작, 지난 접속 끝, 분석 시작 시각, 처음인지."""
    v = current(db)
    return {"start": v.get("start"), "prev_end": v.get("prev_end"), "since": since(db, now),
            "first": not v.get("prev_end")}
