# GitHub Copilot 안내 (교사 업무 데스크)

작업 전에 저장소 맨 위의 `AGENTS.md` 와 `docs/HANDOFF.md` 를 먼저 읽고 그 규칙을 따르세요. 사용자는 코딩 초보 교사이므로 설명·PR·주석은 쉬운 한국어로 씁니다.

## 대화(채팅)는 Claude Code 세션과 똑같이 나눕니다 (사용자 결정 2026-11-03)

Claude Code 가 쓰는 세션과 **같은 이름·같은 영역**으로 Copilot 채팅을 나눠 씁니다. 사용자가 채팅 첫 말에 영역 이름을 적어 주면 그 영역 일만 합니다. 적지 않았으면 첫 요청을 보고 아래 표에서 영역을 골라 **"이 채팅은 ○○ 영역으로 하겠습니다"** 라고 먼저 확인합니다.

| 채팅 이름 | 맡는 일 | 주로 고치는 파일 |
|---|---|---|
| 총괄 | 요청을 받아 어느 영역인지 정해 주고, 영역 사이 조율, 작은 문서 일 | `AGENTS.md` `docs/HANDOFF.md` |
| 머지 전용 | PR 검토, 사용자가 **"머지해"** 라고 한 PR 만 머지, 배포 확인, 버전·충돌 정리 | (코드 안 고침) |
| 수신함·수집 | 쿨메신저·에듀파인 수집, 한글·PDF 읽기, 날짜·마감 찾기, 수신함·검색 화면 | `desk/collect.py` `coolmsg.py` `docs.py` `cfb.py` `preview.py` `extract.py` `watcher.py` `scheduler.py` · `web/js/views/inbox.js` `search.js` |
| AI | 브리핑 AI 요약, 수신 항목 자동 요약, 일반 질문 AI | `desk/ai.py` `localai.py` `providers.py` `visits.py` · `web/js/views/ai.js` `web/js/browser-ai.js` · `views/today.js` 의 AI 카드 |
| 출결·증 발급 | 조퇴·외출증, 결석계, 월별 통계, 인쇄, 학생 공유 페이지 | `desk/absence.py` `printing.py` `passlink.py` `queries.py`(출결) · `web/js/views/passes.js` `absence.js` `attendance.js` · `pass/` |
| 일정·시간표·주간 계획 | 달력·일정, 학사일정, 시간표·교체·선생님 찾기·수업 알림, 주간 계획, 브리핑 일정 카드 | `desk/schoolcal.py` `xlsx.py` `neis.py` `timetable.py` `bell.py` `weekplan.py` `ics.py` · `web/js/views/calendar.js` `schoolcal.js` `timetable.js` `week.js` · `views/today.js`(일정 카드) |
| 텔레그램 | 봇 명령·알림·대화형 입력 | `desk/telegram.py` `assist.py` |
| 브라우저 확장·외부 연동 | 에듀파인·나이스 화면 보내기, 나이스 입력 칸 채우기(**보류**: 사용자가 다시 하자고 하기 전에는 오류 수정만) | `browser-extension/` `desk/capture.py` `neisfill.py` · `web/js/views/neisfill.js` |
| 앱 기반·설정·배포·품질 | 화면 틀(탭·팝업·메뉴), 설정 화면, 전용 창, 업데이트, 윈도우 ZIP·CI, 성능·무결성 점검·경량화 | `desk/server.py`(공통) `config.py` `db.py` `window.py` `background.py` `updater.py` `desk.py` · `web/js/shell.js` `ui.js` `api.js` `store.js` `singleton.js` `update.js` `main.js` `views/settings.js` · `tools/` `.github/` |

- 한 채팅은 **자기 영역 파일만** 고칩니다. 다른 영역 일이 생기면 직접 하지 말고 사용자에게 "○○ 채팅(또는 Claude 의 ○○ 세션)에 맡기세요"라고 알립니다.
- 같이 쓰는 파일(`desk/server.py`, `web/css/desk.css`, `web/js/main.js`)은 자기 영역 부분만 작게 고칩니다. CSS 는 맨 아래 자기 영역 묶음에 덧붙입니다.
- 채팅마다 **PR 은 한 번에 하나**. PR 설명 첫 줄에 영역을 적습니다 (예: `영역: 출결·증 발급`). 버전·기록 충돌은 `AGENTS.md` 의 '충돌 줄이기'를 따릅니다.
- **개발 채팅은 머지하지 않습니다** (머지 = 배포). 머지는 '머지 전용' 채팅이 사용자의 "머지해" 뒤에만 합니다.
- Claude Code 세션과 같은 영역 일을 동시에 하지 않도록, 시작할 때 `git fetch origin main`·`git status` 와 열린 PR 을 보고 같은 파일을 고치는 PR 이 있으면 사용자에게 먼저 알립니다.
