"""학생에게 줄 조퇴증·외출증 링크 만들기 (pass/pass-codec.js 의 encode·shareUrl 과 같은 모양).

증의 내용은 주소의 # 뒤에만 들어가 서버에 남지 않습니다. 사유는 `share_reason` 이 켜진 증에만 담습니다
(텔레그램으로 발급한 증은 꺼져 있어 사유가 빠짐)."""

import base64
import json


def pack(p, school):
    d = {"v": 1, "k": "o" if p["kind"] == "out" else "e", "id": p["serial"], "nm": p["student_name"], "d": p["date"]}
    if p.get("student_number") is not None:
        d["no"] = p["student_number"]
    if p.get("time_out"):
        d["t"] = p["time_out"]
    if p["kind"] == "out" and p.get("time_return"):
        d["r"] = p["time_return"]
    if p.get("share_reason") and p.get("reason"):
        d["why"] = p["reason"]
    if school.get("school_name"):
        d["s"] = school["school_name"]
    cls = " ".join(x for x in (f"{school['grade']}학년" if school.get("grade") else "",
                               f"{school['class_no']}반" if school.get("class_no") else "") if x)
    if cls:
        d["c"] = cls
    if school.get("teacher_name"):
        d["tn"] = school["teacher_name"]
    return d


def share_url(base, p, school):
    """공유 페이지 주소가 비어 있으면 None."""
    base = (base or "").split("#")[0]
    if not base:
        return None
    raw = json.dumps(pack(p, school), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base + "#" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
