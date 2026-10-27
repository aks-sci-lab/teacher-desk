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
| `desk.py`, `desk/` | 로컬 서버(Python 표준 라이브러리). `server.py` API, `extract.py` 날짜·마감, `docs.py`·`cfb.py` hwp/hwpx/pdf, `collect.py` 수집, `coolmsg.py` 쿨메신저 .udb, `schoolcal.py`·`xlsx.py`·`neis.py` 학사일정, `scheduler.py` 9·12·15시, `watcher.py` 실시간 감시, `updater.py` 켤 때 업데이트, `telegram.py`, `picker.py` 찾아보기 창 |
| `web/` | 화면 (순수 HTML/CSS/JS ES 모듈, VS Code 형태) |
| `pass/` | 학생 폰용 조퇴증·외출증 페이지 (공개, 내용은 주소 `#` 뒤에만) |
| `tests/` | Python 시험(`python -m unittest discover -s tests -t .`), `pass-codec.test.cjs`(`node --test`) |
| `tools/build_portable.py` | 윈도우용 ZIP (embeddable Python 3.12 + pypdf) |
| `tools/make_bats.py` | `make-desktop-shortcut.bat`·`update-desk.bat` 생성 (직접 고치지 말 것) |
| `.github/workflows/` | `desk-portable.yml` 시험·윈도우 ZIP·Releases, `pages.yml` 공유 페이지 |

## 규칙

- 빌드 도구 없음. Python 은 표준 라이브러리만 (PDF 만 선택 `pypdf`). 화면 외부 라이브러리는 CDN(jsdelivr)만.
- 화면 문구와 주석은 한국어.
- **학생 이름·조퇴 사유·공문·쪽지는 이 PC 의 `data/desk.db` 에만.** 클라우드·텔레그램·로그로 보내지 않는다 (텔레그램은 인원수·제목까지). `config.json`·`data/` 는 커밋하지 않는다.
- 분석은 AI 없이 규칙 기반. AI 요약은 학교 정보보안 지침 확인 후에만.
- 서버는 `127.0.0.1` 전용, 토큰·Host·Origin 검사.
- `.bat` 은 CP949·CRLF. `if ( … )` 묶음 안 echo 에 괄호 금지 (`tests/test_bats.py` 가 검사).
- ZIP 안 배치는 `TeacherDesk/teacher-desk` + `TeacherDesk/pass` 로 유지한다 (설치된 `update-desk.bat` 이 이 배치를 기대함).

## 역할 분담 (사용자 지정)

| 담당 | 하는 일 | 하지 않는 일 |
|---|---|---|
| **Claude Code: 구현** | 브랜치에서 수정 → 테스트 → PR. 검토에서 돌아온 문제를 같은 PR 에서 고침 | 사용자 승인 없는 `main` 머지 |
| **GitHub Copilot: 검토** | 요구사항·기존 기능 확인, 문제는 PR 댓글로 | 사용자 승인 없는 머지 |
| **사용자** | 최종 승인 (머지 = 배포) | |

- 머지하면 Releases ZIP 과 공유 페이지가 바로 바뀐다. **머지 = 배포**.
- PR 설명에는 원래 요구사항, 바꾼 내용, 실행한 테스트, 확인하지 못한 것을 적는다.

## 설계 결정

- 로컬 우선: 브라우저는 PC 파일을 못 읽으므로 Python 로컬 서버가 쿨메신저·에듀파인 폴더를 읽는다.
- 쿨메신저 쪽지: `%LOCALAPPDATA%\CoolMessenger\Memo\<이름>.udb` (사용자 확인). 이름만 넣거나 파일 경로를 직접 지정. `.udb` 구조는 미확인이라 SQLite 로 보고 표·열 자동 인식, 복사본을 읽기 전용으로.
- 학사일정: 양식 고정 없이 **읽기 → 미리보기에서 고치기 → 저장**. 엑셀 달력표/목록/글 자동 판별, 연도 없는 날짜는 학년도(3~12월=학년도, 1~2월=다음 해). 가장 범용적인 길은 나이스 Open API(인증키 필요).
- 텔레그램: 롱 폴링, 연결 코드를 보낸 계정 하나만.
- 화면: ① 활동 막대 ② 목록 패널 ③ 탭 ④ 작업 영역 ⑤ 하단 패널 ⑥ 상태 표시줄. 사용자가 🧩 화면 배치 → "배치 설명 복사"로 보내는 번호를 기준으로 고친다. 메뉴 '오늘' 은 '브리핑'(사용자 지정, 뷰 id 는 `today`).
- 배포 형태: Python 으로 개발, 선생님에게는 Python 이 들어 있는 ZIP. `update-desk.bat` 은 이 저장소 Releases 를 먼저, 없으면 예전 저장소를 받는다.
- 자동 업데이트(0.4.0, 사용자 요청): 켤 때 Releases 의 `latest.json`(워크플로가 ZIP 다음에 올림) 버전을 비교. 새 버전이면 PowerShell 업데이트 창을 띄우고 종료 코드 20 으로 끝냄 → 창이 python 종료를 기다려 `data`·`config.json` 빼고 덮어쓴 뒤 `start-desk.bat --no-update-check` 로 다시 켬. 윈도우용 ZIP(`python\python.exe` 있음)에서만, 인터넷이 안 되면 4초 뒤 그냥 켬.
- 실시간 감시(0.4.0, 사용자 요청): 윈도우 전용 API 없이 표준 라이브러리로 `watch_interval`(20초)마다 파일의 바뀐 시각·크기만 비교, 두 번 연속 같으면(저장 끝남) 수집. 폴더당 5000개까지. 파일 1만 개 폴더(읽는 형식 5000개)에서 한 번에 약 75 ms(리눅스 측정). 9·12·15시 예약 수집은 보조로 유지.

## 진행 기록

- 2026-10-26~27 (`arduino_experiments` 시절): 0.1.0 조퇴증·외출증·수신함·예약 수집·달력·텔레그램·화면 배치, 윈도우용 ZIP, 0.2.0 쿨메신저 이름 연동·업데이트 도구, 0.3.0 학사일정·검색 확대·브리핑·경로 직접 지정 — Claude Code
- 2026-10-27: 새 저장소로 분리 (0.3.1): 공유 페이지 GitHub Pages, 저장소 구조 정리 — Claude Code
  - 확인 필요: 실제 쿨메신저 `.udb` 구조, 나이스 실제 응답, 실제 학사일정 파일, 한국어 윈도우에서 찾아보기 창
- 2026-10-27: 0.4.0 켤 때 자동 업데이트(`latest.json` 비교 → 업데이트 창 → 다시 켬), 쿨메신저·에듀파인 폴더 실시간 감시 — Claude Code
  - 확인 필요: 0.3.x 설치본은 `update-desk.bat` 을 한 번 눌러야 0.4.0 이 됨, 학교 PC 에서 PowerShell 실행 제한 여부
