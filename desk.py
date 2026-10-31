"""교사 업무 데스크 실행 파일.

  python desk.py                 업무 데스크 켜기 (브라우저가 자동으로 열림)
  python desk.py --no-browser    브라우저를 열지 않고 켜기
  python desk.py --collect       지금 한 번 수집하고 끝내기
  python desk.py --diagnose cool 쿨메신저 폴더 구조 진단 (결과는 data/diagnose-cool.json)
  python desk.py --check         실행 환경 확인만 하고 끝내기 (윈도우용 ZIP 시험에 씀)
"""

import argparse
import importlib.util
import json
import os
import sys
from contextlib import nullcontext

from desk import VERSION, background, updater
from desk.collect import SOURCES, Collector, diagnose
from desk.config import APP_DIR, Config
from desk.db import DB
from desk.bell import LessonBell
from desk.scheduler import Scheduler
from desk.server import App, serve
from desk.telegram import Bot
from desk.watcher import Watcher


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # 윈도우 명령 창·파일 출력에서 한글 깨짐 방지
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="교사 업무 데스크")
    p.add_argument("--config", help="설정 파일 경로 (기본: config.json)")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--collect", action="store_true", help="한 번 수집하고 끝내기")
    p.add_argument("--diagnose", choices=sorted(SOURCES), help="폴더 구조 진단")
    p.add_argument("--check", action="store_true", help="실행 환경 확인만 하고 끝내기")
    p.add_argument("--no-update-check", action="store_true", help="켤 때 새 버전 확인하지 않기")
    p.add_argument("--background", action="store_true", help="명령 창 없이 백그라운드에서 켜기 (Windows)")
    args = p.parse_args(argv)

    if args.check:
        pdf = importlib.util.find_spec("pypdf") is not None
        print(f"업무 데스크 {VERSION} · Python {sys.version.split()[0]} ({sys.executable})")
        print(f"PDF 읽기(pypdf): {'준비됨' if pdf else '없음'}")
        return 0

    config = Config(args.config)
    if args.background and not (args.collect or args.diagnose):
        try:
            return background.launch(config, list(argv if argv is not None else sys.argv[1:]))
        except background.BackgroundError as e:
            print(f"[오류] {e}")
            return 1
    db = DB(config.data_dir / "desk.db")
    try:
        lock = nullcontext(True) if args.collect or args.diagnose else background.single_instance(config["port"])
        with lock as first:
            if not first:
                if not args.no_browser:   # 열려 있는 창이 있으면 그 창을 앞으로, 없으면 전용 창을 새로
                    from desk import window
                    window.show_ui(config["port"], config.data_dir, config["app_window"])
                print("업무 데스크가 이미 실행 중입니다.")
                return 0
            return run(args, config, db)
    finally:
        db.close()  # 업데이트 창이 프로그램 파일을 바꾸기 전에 자료 파일을 닫아 둠


def run(args, config, db):

    if args.diagnose:
        folder = config[SOURCES[args.diagnose]]
        if not folder:
            print("⚙ 설정에서 폴더를 먼저 지정하세요.")
            return 1
        result = diagnose(folder)
        out = config.data_dir / f"diagnose-{args.diagnose}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"진단 결과를 저장했습니다: {out}")
        print("이 파일에는 메시지 내용이 없고 파일 이름·형식만 있습니다. 개발 담당에게 전달하세요.")
        return 0

    # 켤 때 새 버전 확인 (윈도우용 ZIP 설치본만, 몇 초 안에 답이 없으면 그냥 켬)
    plain_start = not (args.collect or args.no_update_check)
    if plain_start and config["auto_update"] and (updater.is_portable(APP_DIR) or os.environ.get("DESK_UPDATE_JSON")):
        print("새 버전이 있는지 확인하는 중…")
        latest = updater.check(VERSION)
        if latest:
            print(f"새 버전 {latest['version']} 이 있습니다 (지금 {VERSION}). 업데이트 창이 열리고, 끝나면 다시 켜집니다.")
            db.log(f"자동 업데이트 시작: {VERSION} → {latest['version']}")
            updater.start_update(APP_DIR)
            return updater.EXIT_UPDATING
        print("최신 버전입니다.")

    bot = Bot(db, config)
    collector = Collector(db, config, notify=bot.notify_collect)
    bot.collector = collector

    if args.collect:
        stats = collector.run("manual")
        print(json.dumps({k: v for k, v in stats.items() if k != "new_ids"}, ensure_ascii=False, indent=2))
        return 0

    app = App(db, config, collector, bot)
    try:
        httpd = serve(app, open_browser=not args.no_browser)
    except OSError:
        print(f"{config['port']}번 포트를 쓸 수 없습니다. 업무 데스크가 이미 켜져 있는지 확인하세요.")
        return 1
    app.shutdown = httpd.shutdown
    app.update_checker = updater.Checker(VERSION, APP_DIR, on_new=bot.notify_update)
    app.update_checker.start()
    app.item_ai.start_auto()   # AI 가 켜져 있으면 새 수신 항목을 뒤에서 자동 요약
    scheduler = Scheduler(db, config, collector)
    scheduler.start()
    watcher = Watcher(db, config, collector)
    watcher.start()
    app.watcher = watcher
    bot.start()
    bell = LessonBell(db, config, bot)   # 수업 시작 알림 (시간표의 교시 시간)
    bell.start()
    db.log("업무 데스크 시작")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.item_ai.stop()
        scheduler.stop()
        watcher.stop()
        bot.stop()
        bell.stop()
        app.update_checker.stop()
        httpd.server_close()
        db.log("업무 데스크 종료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
