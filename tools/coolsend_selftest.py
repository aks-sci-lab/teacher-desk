"""쿨메신저 쪽지 보내기 시험: 선생님 본인에게 시험 쪽지 한 통 (학교 PC 에서 손으로 실행).

    py tools\\coolsend_selftest.py            본인 이름(쿨메신저 쪽지 파일 이름)으로
    py tools\\coolsend_selftest.py 홍길동     다른 이름으로 (조직도에 보이는 이름)
    py tools\\coolsend_selftest.py 첨부       받은 쪽지 하나를 골라 첨부 받기 (모든파일 저장)

받는 사람 후보를 보여 주고, y 를 입력해야만 보냅니다. 쿨메신저에 열린 쪽지 쓰기 창이 없어야 합니다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desk import coolmsg, coolsend  # noqa: E402


def attach():
    """받은메시지 목록에서 첨부가 있는 쪽지를 골라 업무 데스크와 같은 방법으로 받아 봄."""
    ui = coolsend.WinAttachUI()
    try:
        ui.require()
        manager = ui.manager_window(ui.main_window())
        ui.show_inbox(manager)
        rows = [(i, r) for i, r in enumerate(ui.rows(manager)) if (r[3] or "").strip()][:10]
    except coolsend.SendError as e:
        print("멈춤:", e)
        return 1
    if not rows:
        print("받은메시지 앞쪽에 첨부가 있는 쪽지가 없습니다.")
        return 1
    for n, (_, r) in enumerate(rows, 1):
        print(f"{n}. {r[2]}  {r[1]}  [{r[3]}]")
    answer = input(f"받아 볼 쪽지 번호 (1~{len(rows)}, 그만두려면 Enter): ").strip()
    if not answer.isdigit() or not 1 <= int(answer) <= len(rows):
        print("취소했습니다.")
        return 1
    r = rows[int(answer) - 1][1]
    when = coolmsg.parse_when(r[2])
    try:
        files = coolsend.fetch_attachments(r[1], when.strftime("%Y-%m-%d %H:%M:%S"), coolsend.clean_name(r[0]), ui=ui)
    except coolsend.SendError as e:
        print("받지 못했습니다:", e)
        return 1
    print(f"✅ 새 파일 {len(files)}개:", ", ".join(p.name for p in files))
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "첨부":
        return attach()
    if len(sys.argv) > 1:
        name = sys.argv[1]
    else:
        files = sorted(Path(coolmsg.memo_dir()).glob("*.udb"))
        if not files:
            print("쿨메신저 쪽지 파일을 찾지 못했습니다. 이름을 함께 적어 주세요: py tools\\coolsend_selftest.py 홍길동")
            return 1
        name = files[0].stem
    try:
        people = coolsend.find_people(name)
    except coolsend.SendError as e:
        print("멈춤:", e)
        return 1
    if not people:
        print(f"조직도에서 '{name}' 을(를) 찾지 못했습니다.")
        return 1
    for i, p in enumerate(people, 1):
        print(f"{i}. {p['label']}  ({p['dept']})")
    pick = 0
    if len(people) > 1:
        answer = input(f"보낼 사람 번호 (1~{len(people)}): ").strip()
        if not answer.isdigit() or not 1 <= int(answer) <= len(people):
            print("취소했습니다.")
            return 1
        pick = int(answer) - 1
    if input("이 사람에게 시험 쪽지 한 통을 보낼까요? (y 입력): ").strip().lower() != "y":
        print("취소했습니다. 보낸 것은 없습니다.")
        return 1
    try:
        print(coolsend.send(people[pick], "[시험] 업무 데스크 쿨메신저 보내기 시험",
                            "업무 데스크 코드로 보낸 시험 쪽지입니다. 지워도 됩니다."))
    except coolsend.SendError as e:
        print("보내지 못했습니다:", e, "(쓰던 쪽지 창은 열어 두었습니다)" if e.left_open else "")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
