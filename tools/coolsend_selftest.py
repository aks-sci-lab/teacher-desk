"""쿨메신저 쪽지 보내기 시험: 선생님 본인에게 시험 쪽지 한 통 (학교 PC 에서 손으로 실행).

    py tools\\coolsend_selftest.py            본인 이름(쿨메신저 쪽지 파일 이름)으로
    py tools\\coolsend_selftest.py 홍길동     다른 이름으로 (조직도에 보이는 이름)
    py tools\\coolsend_selftest.py 첨부       받은 쪽지 하나를 골라 첨부 받기 (모든파일 저장)
    py tools\\coolsend_selftest.py 창         본인에게 쓰는 쪽지 창을 열어 단추 목록만 보여 줌 (보내지 않음)
    py tools\\coolsend_selftest.py 파일 C:\\경로\\시험.txt   그 파일을 붙여 본인에게 시험 쪽지 (0.6.9)

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


def controls(compose):
    """쪽지 창 안의 칸·단추 목록 (번호·종류·글·위치) — [파일 첨부] 단추 번호를 찾는 데 씀."""
    from desk import winui
    left, top, _, _ = winui.rect(compose)
    print(f"쪽지 창 {compose}: 칸·단추 목록 (번호 | 종류 | 글 | 창 안 위치)")
    for h in winui.children(compose):
        if not winui.is_visible(h):
            continue
        l, t, r, b = winui.rect(h)
        print(f"  {winui.item_id(h):>6} | {winui.class_name(h):<28} | {winui.text(h)[:30]:<30} | "
              f"{l - left},{t - top} {r - l}x{b - t}")
    button = coolsend.WinUI().attach_button(compose)
    print("[파일 첨부] 단추로 찾은 것:", f"{button} (번호 {winui.item_id(button)})" if button else "없음")


def pick_person(name):
    if not name:
        files = sorted(Path(coolmsg.memo_dir()).glob("*.udb"))
        if not files:
            print("쿨메신저 쪽지 파일을 찾지 못했습니다. 이름을 함께 적어 주세요: py tools\\coolsend_selftest.py 홍길동")
            return None
        name = files[0].stem
    try:
        people = coolsend.find_people(name)
    except coolsend.SendError as e:
        print("멈춤:", e)
        return None
    if not people:
        print(f"조직도에서 '{name}' 을(를) 찾지 못했습니다.")
        return None
    for i, p in enumerate(people, 1):
        print(f"{i}. {p['label']}  ({p['dept']})")
    if len(people) == 1:
        return people[0]
    answer = input(f"보낼 사람 번호 (1~{len(people)}): ").strip()
    if not answer.isdigit() or not 1 <= int(answer) <= len(people):
        print("취소했습니다.")
        return None
    return people[int(answer) - 1]


def probe(name):
    """쪽지 쓰기 창을 열고 칸·단추 목록만 출력 (보내지 않음, 창은 열어 둠)."""
    person = pick_person(name)
    if not person:
        return 1
    ui = coolsend.WinUI()
    try:
        ui.require()
        main = ui.main_window()
        if ui.compose_windows():
            print("열려 있는 쪽지 쓰기 창이 있습니다. 닫고 다시 실행해 주세요.")
            return 1
        handle = next(h for h, label, dept in ui.people(main) if label == person["label"] and dept == person["dept"])
        compose = ui.open_compose(main, handle, person["label"])
    except (coolsend.SendError, StopIteration) as e:
        print("멈춤:", e)
        return 1
    controls(compose)
    print("쪽지 창은 열어 두었습니다. 보내지 않았습니다. 확인 뒤 [닫기]로 닫아 주세요.")
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "첨부":
        return attach()
    if len(sys.argv) > 1 and sys.argv[1] == "창":
        return probe(sys.argv[2] if len(sys.argv) > 2 else "")
    files = []
    args = sys.argv[1:]
    if args and args[0] == "파일":
        if len(args) < 2 or not Path(args[1]).is_file():
            print("붙일 파일 위치를 적어 주세요: py tools\\coolsend_selftest.py 파일 C:\\경로\\시험.txt")
            return 1
        files, args = [str(Path(args[1]).resolve())], args[2:]
    person = pick_person(args[0] if args else "")
    if not person:
        return 1
    what = f"파일 {Path(files[0]).name} 을 붙인 " if files else ""
    if input(f"이 사람에게 {what}시험 쪽지 한 통을 보낼까요? (y 입력): ").strip().lower() != "y":
        print("취소했습니다. 보낸 것은 없습니다.")
        return 1
    try:
        print(coolsend.send(person, "[시험] 업무 데스크 쿨메신저 보내기 시험",
                            "업무 데스크 코드로 보낸 시험 쪽지입니다. 지워도 됩니다.", files=files))
    except coolsend.SendError as e:
        print("보내지 못했습니다:", e, "(쓰던 쪽지 창은 열어 두었습니다)" if e.left_open else "")
        if files and e.left_open:
            compose = next(iter(coolsend.WinUI().compose_windows()), None)
            if compose:
                controls(compose)   # 단추를 못 찾았을 때 고칠 수 있게 목록을 보여 줌
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
