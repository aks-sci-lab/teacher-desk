"""가짜 학급·가짜 휴대폰 백업 파일 만들기 (실제로 안 쓰는 010-0000-xxxx 번호, 실명 아님).
출력: OUT/roster.json, calls.xml, sms.xml, big_calls.xml, samsung_ko_utf8.csv, generic_en.csv, ko_cp949.csv, expect.json
"""
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import quoteattr

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
rng = random.Random(7)
FAM = "가나다라마바사아자차카타파하"
NOW = datetime(2026, 11, 10, 12, 0)   # 시험 기준 (KST)


def ms(dt):
    return str(int(dt.timestamp() * 1000))


def readable(dt):
    return dt.strftime("%b %-d, %Y %-I:%M:%S %p")


# ---- 학급 30명: 어머니·아버지 번호 (명단에는 연락처 한 칸만 있어서 '대표 번호' 하나를 넣음) ----
students, mom, dad = [], {}, {}
for n in range(1, 31):
    name = f"{FAM[n % len(FAM)]}가짜{n}"
    m = f"010-0000-{1000 + n:04d}"
    d = f"010-0000-{2000 + n:04d}"
    if n in (5, 6):            # 형제: 같은 어머니 번호
        m = "010-0000-1005"
    if n == 28:                # 아버지만
        m = None
    if n in (29, 30):          # 연락처 없음
        m, d = None, None
    if n in (7, 8, 9):         # 한 명만 (어머니)
        d = None
    mom[n], dad[n] = m, d
    # 명단에 적는 모양 섞기 (서버가 010-1234-5678 로 바꿔 저장)
    shown = {1: m.replace("-", "") if m else None, 2: m.replace("-", " ") if m else None,
             3: m.replace("-", ".") if m else None}.get(n % 4, m)
    rep = shown if m else (d if d else None)   # 어머니가 없으면 아버지 번호
    students.append({"number": n, "name": name, "parent_phone": rep})
(OUT / "roster.json").write_text(json.dumps(students, ensure_ascii=False), encoding="utf-8")

# 번호를 여러 모양으로 (앱마다 다름)
def variant(num, k):
    d = num.replace("-", "")
    return [num, d, "+82" + d[1:], "+82 " + d[1:3] + "-" + d[3:7] + "-" + d[7:], f"{d[:3]} {d[3:7]} {d[7:]}"][k % 5]


calls, smses, expect = [], [], {"call": [], "sms": [], "discard_numbers": [], "discard_texts": []}
t = NOW - timedelta(days=55)
for k in range(140):
    t += timedelta(hours=rng.randint(2, 9), minutes=rng.randint(0, 59))
    if t > NOW:
        break
    n = rng.randint(1, 30)
    who = rng.choice(["mom", "mom", "dad"])
    num = (mom if who == "mom" else dad)[n] or dad[n]
    kind = rng.random()
    if num is None:
        continue
    typ = rng.choice(["1", "2", "2", "1", "3", "5"])
    dur = 0 if typ in ("3", "5") else rng.choice([0, 12, 65, 125, 301, 1810])
    calls.append(dict(number=variant(num, k), duration=dur, date=t, type=typ, contact=f"{students[n-1]['name']} {'어머니' if who == 'mom' else '아버지'}"))
    expect["call"].append({"student": n, "who": who, "num": num, "date": t.isoformat(), "type": typ, "dur": dur})
# 학부모가 아닌 번호들
for k, (num, typ, dur) in enumerate([("010-0000-9001", "1", 40), ("+1 650-555-0100", "2", 600), ("1588-0000", "1", 30),
                                      ("02-0000-0000", "3", 0), ("", "1", 5), ("010-0000-9002", "5", 0)]):
    dt = NOW - timedelta(days=3, hours=k)
    calls.append(dict(number=num, duration=dur, date=dt, type=typ, contact="(Unknown)"))
    expect["discard_numbers"].append(num)
# 60일 넘은 학부모 통화
old = NOW - timedelta(days=75)
calls.append(dict(number=mom[1], duration=200, date=old, type="1", contact="오래된 통화"))
expect["old_call"] = {"student": 1, "date": old.isoformat()}

EMOJI = "감사합니다 😊"
for k, n in enumerate([1, 1, 1, 3, 5, 10, 12, 12, 28]):
    num = mom[n] or dad[n]
    base = NOW - timedelta(days=2 + k // 3, hours=k)
    body = [f"선생님 안녕하세요\n오늘 {students[n-1]['name']} 병원 들렀다 갑니다", "네 확인했습니다", EMOJI,
            "내일 체험학습 동의서 보낼게요 & 확인 부탁드려요", "알겠습니다!", "결석계는 금요일에 낼게요", "괜찮습니다", "<중요> 내일 늦어요", "아버지입니다"][k]
    smses.append(dict(address=variant(num, k), date=base, type="1" if k % 2 == 0 else "2", body=body, contact=students[n-1]["name"]))
    expect["sms"].append({"student": n, "date": base.isoformat(), "body": body})
for num, body in [("010-0000-9001", "[광고] 비밀특가 할인"), ("#1544", "[Web발신] 스팸문자 대출"), ("+1 650-555-0100", "Hello from abroad")]:
    smses.append(dict(address=num, date=NOW - timedelta(days=1), type="1", body=body, contact="(Unknown)"))
    expect["discard_texts"].append(body)


def ref_text(s):
    """SMS Backup & Restore 처럼: 줄바꿈은 &#10;, 이모지는 UTF-16 서로게이트 숫자 참조로."""
    out = []
    for ch in s:
        cp = ord(ch)
        if cp > 0xFFFF:
            cp -= 0x10000
            out.append(f"&#{0xD800 + (cp >> 10)};&#{0xDC00 + (cp & 0x3FF)};")
        elif ch == "\n":
            out.append("&#10;")
        elif ch in "&<>\"":
            out.append({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[ch])
        else:
            out.append(ch)
    return "".join(out)


def calls_xml(rows):
    lines = ["<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>",
             "<!--File Created By SMS Backup & Restore v10.20.003 on 10/11/2026 12:00:00-->",
             f'<calls count="{len(rows)}" backup_set="5e2c9a10-0000-4000-8000-000000000000" backup_date="{ms(NOW)}" type="full">']
    for r in rows:
        lines.append(f'  <call number={quoteattr(r["number"])} duration="{r["duration"]}" date="{ms(r["date"])}" type="{r["type"]}" '
                     f'presentation="1" subscription_id="89820000000000000000" post_dial_digits="" subscription_component_name="com.android.phone/com.android.services.telephony.TelephonyConnectionService" '
                     f'readable_date="{readable(r["date"])}" contact_name={quoteattr(r["contact"])} />')
    lines.append("</calls>")
    return "\n".join(lines).encode("utf-8")


(OUT / "calls.xml").write_bytes(calls_xml(calls))
big = []
for k in range(25000):   # 몇 MB 짜리 (거의 다 학부모 아닌 번호)
    big.append(dict(number=f"010-0000-{9100 + k % 800:04d}", duration=k % 300, date=NOW - timedelta(minutes=7 * k), type=str(1 + k % 3), contact="(Unknown)"))
big.append(dict(number=mom[2], duration=77, date=NOW - timedelta(hours=1), type="1", contact="큰 파일 속 학부모"))
(OUT / "big_calls.xml").write_bytes(calls_xml(big))

sms_lines = ["<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>",
             "<!--File Created By SMS Backup & Restore v10.20.003 on 10/11/2026 12:00:00-->",
             f'<smses count="{len(smses) + 2}" backup_set="5e2c9a10-0000-4000-8000-000000000001" backup_date="{ms(NOW)}" type="full">']
for r in smses:
    sms_lines.append(f'  <sms protocol="0" address={quoteattr(r["address"])} date="{ms(r["date"])}" type="{r["type"]}" subject="null" '
                     f'body="{ref_text(r["body"])}" toa="null" sc_toa="null" service_center="null" read="1" status="-1" locked="0" '
                     f'date_sent="{ms(r["date"])}" sub_id="1" readable_date="{readable(r["date"])}" contact_name={quoteattr(r["contact"])} />')
# MMS: 그룹 문자(선생님이 두 학부모에게 보냄)와 사진+글자
g = NOW - timedelta(days=4, hours=2)
sms_lines.append(f'  <mms date="{ms(g)}" ct_t="application/vnd.wap.multipart.related" msg_box="2" rr="null" sub="null" read_status="null" '
                 f'address="{mom[11].replace("-", "")}~{mom[12].replace("-", "")}" m_id="null" read="1" m_size="null" m_type="128" readable_date="{readable(g)}" contact_name="(Unknown)">'
                 f'<parts><part seq="0" ct="text/plain" name="null" chset="106" cd="null" fn="null" cid="&lt;text0&gt;" cl="text0.txt" ctt_s="null" ctt_t="null" text="{ref_text("학부모님께: 내일 현장체험학습 준비물 안내드립니다.")}" /></parts>'
                 f'<addrs><addr address="{mom[11].replace("-", "")}" type="151" charset="106" /><addr address="{mom[12].replace("-", "")}" type="151" charset="106" /></addrs></mms>')
p = NOW - timedelta(days=1, hours=5)
sms_lines.append(f'  <mms date="{ms(p)}" ct_t="application/vnd.wap.multipart.related" msg_box="1" address="+82{mom[14][1:].replace("-", "")}" m_type="132" readable_date="{readable(p)}" contact_name="(Unknown)">'
                 f'<parts><part seq="-1" ct="application/smil" text="&lt;smil&gt;&lt;/smil&gt;" /><part seq="0" ct="image/jpeg" name="IMG.jpg" data="AAAA" />'
                 f'<part seq="0" ct="text/plain" chset="106" text="사진 보내드려요 {ref_text("📷")}" /></parts>'
                 f'<addrs><addr address="+82{mom[14][1:].replace("-", "")}" type="137" charset="106" /><addr address="insert-address-token" type="151" charset="106" /></addrs></mms>')
sms_lines.append("</smses>")
(OUT / "sms.xml").write_bytes("\n".join(sms_lines).encode("utf-8"))
expect["mms"] = [{"students": [11, 12], "date": g.isoformat()}, {"students": [14], "date": p.isoformat()}]

# ---- CSV 모양 ----
d1 = NOW - timedelta(days=6, hours=3)   # 2026-11-04 09:00
csv_rows = [(17, "1", 95, d1), (18, "2", 0, d1 + timedelta(hours=1)), (19, "3", 0, d1 + timedelta(hours=2))]
# (1) 삼성식 한국어, 날짜·시간 칸이 따로, '2026. 11. 4. 오후 3:10' 모양
ko = ["이름,전화번호,날짜,시간,통화 시간,구분"]
for n, typ, dur, dt in csv_rows:
    h12 = dt.hour % 12 or 12
    ko.append(f"{students[n-1]['name']} 어머니,{mom[n]},{dt.year}. {dt.month}. {dt.day}.,{'오후' if dt.hour >= 12 else '오전'} {h12}:{dt.minute:02d},"
              f"{dur // 60:02d}:{dur % 60:02d},{ {'1': '수신', '2': '발신', '3': '부재중'}[typ] }")
ko.append("모르는 번호,010-0000-9003,2026. 11. 4.,오후 1:00,00:30,수신")
(OUT / "samsung_ko_utf8.csv").write_text("\n".join(ko), encoding="utf-8-sig")
# (2) 영어 앱: Name,Number,Date,Duration,Type  (MM/DD/YYYY hh:mm:ss, 초, INCOMING)
en = ["Name,Number,Date,Duration,Type"]
for n, typ, dur, dt in csv_rows:
    en.append(f"Parent {n},+82 {mom[n][1:]},{dt.strftime('%m/%d/%Y %H:%M:%S')},{dur},{ {'1': 'INCOMING', '2': 'OUTGOING', '3': 'MISSED'}[typ] }")
en.append("Spam,1588-0000,11/04/2026 13:00:00,10,INCOMING")
(OUT / "generic_en.csv").write_text("\n".join(en), encoding="utf-8")
# (3) CP949 한 칸 일시 '2026-11-04 오후 03:10:00', 통화시간 '1분 35초'
cp = ["번호,통화일시,통화시간,유형"]
for n, typ, dur, dt in csv_rows:
    h12 = dt.hour % 12 or 12
    cp.append(f"{mom[n].replace('-', '')},{dt:%Y-%m-%d} {'오후' if dt.hour >= 12 else '오전'} {h12:02d}:{dt.minute:02d}:00,"
              f"{dur // 60}분 {dur % 60}초,{ {'1': '받은 전화', '2': '건 전화', '3': '부재중 전화'}[typ] }")
(OUT / "ko_cp949.csv").write_bytes("\n".join(cp).encode("cp949"))
expect["csv"] = [{"student": n, "type": typ, "dur": dur, "date": dt.isoformat()} for n, typ, dur, dt in csv_rows]
expect["mom"], expect["dad"] = mom, dad
(OUT / "expect.json").write_text(json.dumps(expect, ensure_ascii=False, indent=1), encoding="utf-8")
for f in sorted(OUT.iterdir()):
    print(f.name, f.stat().st_size)
