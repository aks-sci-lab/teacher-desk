"""나이스 교육정보 개방 포털(open.neis.go.kr)에서 학사일정 받기.

모든 학교의 학사일정이 나이스에 있으므로 양식 걱정 없이 쓸 수 있는 방법입니다.
- 학교 찾기: schoolInfo (학교 이름 → 시도교육청 코드 ATPT_OFCDC_SC_CODE, 학교 코드 SD_SCHUL_CODE)
- 학사일정: SchoolSchedule (AA_YMD 날짜, EVENT_NM 행사 이름)
인증키는 포털에서 무료로 받습니다. 인증키가 없으면 나이스가 시험용으로 몇 건만 돌려줍니다.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from .schoolcal import _event, _in_year

SKIP_EVENTS = {"토요휴업일"}


class NeisError(Exception):
    pass


MAX_PAGES = 5


def _get(base, endpoint, params, key):
    """모든 쪽(한 쪽 1000건)을 이어서 받음."""
    rows = []
    for page in range(1, MAX_PAGES + 1):
        part, total = _get_page(base, endpoint, params, key, page)
        rows += part
        if not part or total is None or len(rows) >= total:
            break
    return rows


def _get_page(base, endpoint, params, key, page):
    q = {"Type": "json", "pIndex": page, "pSize": 1000, **params}
    if key:
        q["KEY"] = key
    url = f"{base.rstrip('/')}/{endpoint}?{urllib.parse.urlencode(q)}"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            body = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as e:
        raise NeisError(f"나이스에 연결할 수 없습니다 ({getattr(e, 'reason', e)}). 인터넷 연결을 확인하세요.")
    except ValueError:
        raise NeisError("나이스 응답을 읽을 수 없습니다")
    if endpoint not in body:
        res = body.get("RESULT", {})
        if res.get("CODE") == "INFO-200":   # 해당하는 데이터가 없습니다
            return [], 0
        raise NeisError(f"나이스 오류: {res.get('MESSAGE') or res.get('CODE') or '알 수 없음'}")
    rows, total = [], None
    for part in body[endpoint]:
        rows += part.get("row", [])
        for h in part.get("head", []):
            if "list_total_count" in h:
                total = h["list_total_count"]
    return rows, total


def search_schools(name, key="", base="https://open.neis.go.kr/hub"):
    rows = _get(base, "schoolInfo", {"SCHUL_NM": name}, key)
    return [{"name": r.get("SCHUL_NM"), "atpt": r.get("ATPT_OFCDC_SC_CODE"), "code": r.get("SD_SCHUL_CODE"),
             "office": r.get("ATPT_OFCDC_SC_NM"), "kind": r.get("SCHUL_KND_SC_NM"), "address": r.get("ORG_RDNMA")}
            for r in rows]


def schedule(atpt, code, school_year, key="", base="https://open.neis.go.kr/hub"):
    """학년도(3월 1일~다음 해 2월 말) 학사일정 → 미리보기용 일정 목록. 이어진 날의 같은 행사는 기간으로 합침."""
    start = date(school_year, 3, 1)
    end = date(school_year + 1, 3, 1) - timedelta(days=1)
    rows = _get(base, "SchoolSchedule", {"ATPT_OFCDC_SC_CODE": atpt, "SD_SCHUL_CODE": code,
                                         "AA_FROM_YMD": start.strftime("%Y%m%d"),
                                         "AA_TO_YMD": end.strftime("%Y%m%d")}, key)
    # 그 학교 것만 (인증키가 없거나 잘못되면 나이스가 여러 학교의 일정을 섞어 보냄)
    mine = [r for r in rows if r.get("SD_SCHUL_CODE") == code and r.get("ATPT_OFCDC_SC_CODE", atpt) == atpt]
    if rows and not mine:
        raise NeisError("나이스가 다른 학교의 일정만 보냈습니다. ⚙ 설정의 나이스 인증키를 확인하세요.")
    days = []
    for r in mine:
        name = (r.get("EVENT_NM") or "").strip()
        ymd = r.get("AA_YMD") or ""
        if not name or name in SKIP_EVENTS or len(ymd) != 8:
            continue
        try:
            days.append((date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:])), name))
        except ValueError:
            continue
    days.sort()
    events, cur = [], None
    for d, name in days:
        # 주말을 사이에 두고 이어지는 같은 이름(예: 여름방학)은 한 기간으로
        if cur and cur[2] == name and (d - cur[1]).days <= 3:
            cur[1] = d
            continue
        if cur:
            events.append(_event(cur[0], cur[1], cur[2]))
        cur = [d, d, name]
    if cur:
        events.append(_event(cur[0], cur[1], cur[2]))
    events, _ = _in_year(events, school_year)
    return {"mode": "neis", "events": events, "sheet": None, "skipped": 0, "sample": not key}
