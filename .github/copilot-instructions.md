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
| UI·사용성 | 글자 크기·배치·가독성·정보 밀도·접근성·화면 간 일관성의 분석·검토 (구현은 Claude) | 분석·검토 대상: `web/css/desk.css`(자기 영역 묶음) · 기능 화면의 표현 부분 |
| 앱 기반·설정·배포·품질 | 화면 틀(탭·팝업·메뉴), 설정 화면, 전용 창, 업데이트, 윈도우 ZIP·CI, 성능·무결성 점검·경량화 | `desk/server.py`(공통) `config.py` `db.py` `window.py` `background.py` `updater.py` `desk.py` · `web/js/shell.js` `ui.js` `api.js` `store.js` `singleton.js` `update.js` `main.js` `views/settings.js` · `tools/` `.github/` |

- **UI·사용성의 구현 도구는 Claude**입니다. Copilot UI·사용성 채팅은 분석·검토를 맡고 구현을 병행하지 않습니다 (한 영역은 한 도구만).
- UI·사용성 담당은 화면의 모양(CSS·글자 크기·배치·접근성)을, 기능 담당은 해당 화면의 자료·업무 동작을 맡습니다. `web/js/shell.js`·`web/js/ui.js`의 탭·팝업·메뉴 구조는 **앱 기반·설정·배포·품질** 담당으로 둡니다. UI 쪽에서 이 두 파일을 고쳐야 하면 앱 기반 담당에 먼저 알리고, 변경 범위와 열린 PR을 맞춘 뒤 진행합니다. 다른 기능 화면을 고칠 때도 해당 담당과 먼저 조율합니다.
- 담당 영역은 **기본 책임 범위**입니다. 한 기능을 완성하는 데 꼭 필요한 작은 교차 영역 변경은 열린 PR과 파일 충돌을 확인한 뒤 함께 고칠 수 있습니다. 큰 교차 영역 작업은 총괄 채팅에 나눠 맡기도록 알립니다.
- 같이 쓰는 파일(`desk/server.py`, `web/css/desk.css`, `web/js/main.js`)은 다른 열린 PR과 충돌하지 않는지 먼저 확인하고, 필요한 부분만 작게 고칩니다. CSS 는 맨 아래 자기 영역 묶음에 덧붙입니다.
- 채팅마다 **PR 은 한 번에 하나**. PR 설명 첫 줄에 영역을 적습니다 (예: `영역: 출결·증 발급`). 버전·기록 충돌은 `AGENTS.md` 의 '충돌 줄이기'를 따릅니다.
- **개발 채팅은 머지하지 않습니다** (머지 = 배포). 머지는 '머지 전용' 대화가 선생님 승인(머지 대화에서 직접, 또는 Claude 총괄 대화에서 한 승인을 총괄이 PR 번호와 함께 전달한 것) 뒤에만 합니다.
- Claude Code 세션과 같은 영역 일을 동시에 하지 않도록, 시작할 때 `git fetch origin main`·`git status` 와 열린 PR 을 보고 같은 파일을 고치는 PR 이 있으면 사용자에게 먼저 알립니다.
- 이 안내는 채팅을 자동으로 만들거나 이름을 강제하지 않습니다. 사용자가 Copilot 에서 표의 영역 이름으로 채팅을 직접 만들고, 작업할 때 해당 채팅을 선택합니다.

## 요청 흐름 (사용자 결정 2026-11-03)

- 선생님은 요구사항을 Claude 의 **총괄 대화**에 말하고, 총괄이 영역별로 정리한 글을 이 채팅에 붙여 넣거나 GitHub 이슈로 Copilot 에 배정합니다. 그 글의 요구사항·완료 기준·머지 순서를 따릅니다.
- **한 영역은 한 번에 한 도구만** 맡습니다. 같은 영역의 Claude 세션이 열어 둔 PR·진행 중인 일이 있으면 시작하지 말고 선생님께 알립니다.
- PR 을 만들었거나 물을 것이 생기면 PR 설명(또는 이슈 댓글)에 정리해 두고, 선생님께 "총괄 대화에 전해 주세요"라고 알립니다.
- PR 은 초안(Draft)으로 두지 말고, 작업이 끝나면 Ready 로 바꿔 GitHub 체크(tests·windows-zip)가 돌게 합니다. 앞 PR 이 머지되면 열린 내 PR 에 `main` 을 합쳐 둡니다.
- 선생님은 총괄 대화에서 승인만 합니다 (`AGENTS.md` 「요청 흐름」). 결정이 필요한 것은 PR 설명 맨 위 '결정할 것' 에 짧게 적어 총괄이 모아 여쭙게 합니다.
