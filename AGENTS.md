# 작업 안내 (AI 코딩 도구 공용)

이 파일은 GitHub Copilot, Claude Code 등 이 저장소에서 일하는 모든 AI가 먼저 읽는 공통 안내입니다.
중요한 결정은 여기에 적어 다음 작업자가 이어받을 수 있게 합니다.

## 프로젝트

**교사 업무 데스크**: 담임 교사가 학교 PC 에서 켜 두고 브라우저로 쓰는 업무 관리 앱.
조퇴증·외출증, 쿨메신저 쪽지·에듀파인 공문 수신함(날짜·마감 추출), 학사일정, 달력, 브리핑, 텔레그램 알림·명령.
사용자(교사)는 코딩 초보이므로, 설명은 쉬운 한국어로 하고 용어는 풀어서 쓴다.

- 2026-10-27 `aks-sci-lab/arduino_experiments`(과학 수업용 웹 실험실)의 `teacher-desk/`·`pass/` 에서 옮겨 온 **별개의 프로젝트**다. 예전 커밋 기록은 가져오지 않았다 (개인 이메일이 남지 않게).
- 배포: 윈도우용 ZIP → Releases `desk-latest`, 학생 공유 페이지 → GitHub Pages (https://aks-sci-lab.github.io/teacher-desk/)

## 구조

| 경로 | 내용 |
|---|---|
| `desk.py`, `desk/` | 로컬 서버(Python 표준 라이브러리). `server.py` API, `extract.py` 날짜·마감, `docs.py`·`cfb.py` hwp/hwpx/pdf, `collect.py` 수집, `coolmsg.py` 쿨메신저 .udb, `schoolcal.py`·`xlsx.py`·`neis.py` 학사일정, `scheduler.py` 9·12·15시, `watcher.py` 실시간 감시, `ai.py` LM Studio 1주일 요약, `updater.py` 켤 때 업데이트, `telegram.py`, `picker.py` 찾아보기 창 |
| `web/` | 화면 (순수 HTML/CSS/JS ES 모듈, VS Code 형태). `js/views/absence.js` 결석계 |
| `pass/` | 학생 폰용 조퇴증·외출증 페이지 (공개, 내용은 주소 `#` 뒤에만). `pass/timetable/` 은 시간표 교체 도우미 (사용자가 준 파일 그대로, 브라우저 안에서만) |
| `tests/` | Python 시험(`python -m unittest discover -s tests -t .`), `pass-codec.test.cjs`(`node --test`) |
| `tools/build_portable.py` | 윈도우용 ZIP (embeddable Python 3.12 + pypdf) |
| `tools/make_bats.py` | `make-desktop-shortcut.bat`·`update-desk.bat` 생성 (직접 고치지 말 것) |
| `docs/` | `DESIGN.md` 설계 결정, `CHANGELOG.md` 진행 기록 (필요할 때만 읽음) |
| `.github/workflows/` | `desk-portable.yml` 시험·윈도우 ZIP·Releases, `pages.yml` 공유 페이지 |

## 규칙

- 빌드 도구 없음. Python 은 표준 라이브러리만 (PDF 만 선택 `pypdf`). 화면 외부 라이브러리는 CDN(jsdelivr)만.
- 화면 문구와 주석은 한국어.
- **학생 이름·조퇴 사유·공문·쪽지는 이 PC 의 `data/desk.db` 에만.** 클라우드·로그로 보내지 않는다. `config.json`·`data/` 는 커밋하지 않는다.
- 텔레그램(사용자 결정 2026-10-28, 예외 2026-10-30): 학생 이름·조퇴 사유는 보내지 않음. **예외: 증 발급 뒤 '웹 링크 받기'를 눌렀을 때만 학생 이름이 든 학생용 링크(사유 제외)를 보냄 (사용자 선택 A, `desk/passlink.py`)**. (증 발급은 번호로, 답장의 이름은 '김○○', 사유는 단추로 고르고 답장에 쓰지 않음, 조퇴 기록·학생 명단은 검색에서 뺌). 수집 알림은 설정의 '알림에 담을 내용'대로. 브리핑·검색·질문의 답은 제목·요약까지, **원문·원본 파일은 선생님이 단추나 '원문 n'·'파일 n' 으로 달라고 할 때만** (원문 안의 이름은 그대로).
- 분석은 AI 없이 규칙 기반. 업무자료를 쓰는 AI(브리핑 1주일 요약·텔레그램 업무자료 질문)는 **이 PC 의 LM Studio(127.0.0.1)로만** (학생 이름은 가려서, AI 답에 이름이 나와도 가림). 공문·쪽지·학생 정보는 인터넷 AI 로 보내지 않는다. 0.5.29 일반 질문은 별도로 로컬·브라우저 내장·외부 API 선택(공개 자료만, 매번 동의). 자료 찾기·날짜 계산·'7번 조퇴' 같은 명령 알아듣기는 규칙(`desk/assist.py`).
- 서버는 `127.0.0.1` 전용, 토큰·Host·Origin 검사.
- `.bat` 은 CP949·CRLF. `if ( … )` 묶음 안 echo 에 괄호 금지 (`tests/test_bats.py` 가 검사).
- ZIP 안 배치는 `TeacherDesk/teacher-desk` + `TeacherDesk/pass` 로 유지한다 (설치된 `update-desk.bat` 이 이 배치를 기대함).

## 작업 폴더와 도구 전환 (사용자 결정 2026-10-30)

- **개발 폴더는 하나**: `C:\Users\user\Desktop\업무 관리(개발용)`. GitHub Copilot(VS Code)과 Claude Code 가 모두 여기서 개발한다. 실제로 쓰는 앱은 `C:\Users\user\Desktop\업무 관리`(윈도우용 ZIP 설치본)이며 **개발 폴더에서 복사해 덮어쓰지 않는다** — 실제 앱은 켤 때 Releases 에서 스스로 업데이트한다(`data`·`config.json` 은 그대로).
- 개발 폴더는 `python\python.exe` 가 없어 자동 업데이트 대상이 아니다. 개발 폴더의 `config.json`·`data` 는 따로 두고 **포트를 실제 앱과 다르게**, **텔레그램 토큰은 넣지 않는다**(같은 봇을 두 곳에서 켜면 둘 다 Conflict). 학생 이름 등 실제 자료는 개발 폴더에 가져오지 않고 가짜 자료로 시험한다.
- 도구를 바꿀 때(크레딧·한도): ① 끝낼 때 미완성이어도 **작업 브랜치에 커밋·push** ② 시작할 때 `git fetch origin main`(원격 이름까지 갱신되는지 확인)·`git status` — 모르는 미커밋 변경이 있으면 지우지 말고 출처 확인 ③ **한 번에 한 도구만** 같은 폴더에서 ④ 버전 번호(`desk/__init__.py`)는 겹치면 머지 직전에 큰 쪽 +1 로 정리.
- **배포는 도구와 무관하게 하나**: GitHub 에서 PR 을 `main` 에 머지(= 배포). 어느 도구로 머지하든 같고, 폴더에서 직접 배포하는 길은 없다. PC 의 폴더 변경은 push 하기 전에는 다른 도구·클라우드 세션에 보이지 않는다.

## 역할 분담 (사용자 지정, 2026-10-30 실제 방식에 맞춰 고침)

| 담당 | 하는 일 | 하지 않는 일 |
|---|---|---|
| **Claude Code / GitHub Copilot** | 크레딧에 따라 번갈아 구현·검토. 브랜치에서 수정 → 테스트 → PR. 상대 도구의 PR 은 코드뿐 아니라 설명·댓글까지 읽고 검토 | 사용자 승인 없는 `main` 머지 |
| **사용자** | 최종 승인 (머지 = 배포), 어느 도구로 이어서 할지 정함 | |

- 머지하면 Releases ZIP 과 공유 페이지가 바로 바뀐다. **머지 = 배포**.
- PR 설명에는 원래 요구사항, 바꾼 내용, 실행한 테스트, 확인하지 못한 것을 적는다. 검토자는 이것을 그대로 믿지 말고 직접 확인한다.
- 이어받은 작업의 PR 에 완료·남은 일·테스트·미검증을 적어 넘긴다(코드 동기화와 대화 인계는 별개).

## 더 알아야 할 때 (필요한 것만 읽기)

- `docs/DESIGN.md` — 기능별 **설계 결정**(왜 그렇게 만들었는지, 사용자가 정한 것). 그 기능을 고치기 전에 해당 항목만 읽는다 (쿨메신저·AI 요약·텔레그램·시간표·출결·업데이트·일정 후보 규칙 등).
- `docs/CHANGELOG.md` — **진행 기록**(버전별 한 줄, 확인 필요 사항). 이력이 필요할 때만. 큰 작업을 마치면 여기 맨 아래에 한 줄 더하고, 새 설계 결정은 `docs/DESIGN.md` 에 더한다.
- 사용법·설치·보안 설계는 `README.md`.
- 이 파일(`AGENTS.md`)은 짧게 유지한다 — 내용이 길어지면 위 두 문서로 옮긴다.
