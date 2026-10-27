"""일정을 .ics(달력 파일)로 내보내기 → 구글 캘린더 '가져오기'에 그대로 넣을 수 있음."""

from datetime import date, datetime, timedelta, timezone


def _esc(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line):
    # 한 줄 75바이트 제한 (한글이 깨지지 않게 글자 단위로 자름)
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode("utf-8")) > 73:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n".join(out)


def to_ics(events, stamp=None):
    stamp = (stamp or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//teacher-desk//KO", "CALSCALE:GREGORIAN"]
    for e in events:
        d = date.fromisoformat(e["date"])
        title = ("[마감] " if e["kind"] == "deadline" else "") + (e.get("item_title") or e["text"] or "일정")
        lines += ["BEGIN:VEVENT", f"UID:desk-event-{e['id']}@teacher-desk", f"DTSTAMP:{stamp}"]
        if e.get("time") and not e.get("end_date"):
            h, m = map(int, e["time"].split(":"))
            start = datetime(d.year, d.month, d.day, h, m)
            lines.append(f"DTSTART;TZID=Asia/Seoul:{start:%Y%m%dT%H%M%S}")
            lines.append(f"DTEND;TZID=Asia/Seoul:{start + timedelta(hours=1):%Y%m%dT%H%M%S}")
        else:
            end = date.fromisoformat(e["end_date"]) if e.get("end_date") else d
            lines.append(f"DTSTART;VALUE=DATE:{d:%Y%m%d}")
            lines.append(f"DTEND;VALUE=DATE:{end + timedelta(days=1):%Y%m%d}")
        lines.append("SUMMARY:" + _esc(title[:120]))
        lines.append("DESCRIPTION:" + _esc(e["text"] or ""))
        lines.append("CATEGORIES:" + _esc(e.get("category") or ""))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"
