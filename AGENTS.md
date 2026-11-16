# 작업 안내 (Claude Code)

## 프로젝트

**교사 업무 데스크**: 담임 교사가 학교 PC 에서 켜 두고 브라우저로 쓰는 업무 관리 앱 (조퇴·외출증, 결석계, 쿨메신저·에듀파인 수신함, 학사일정·달력·시간표, 브리핑, 텔레그램).
사용자(교사)는 코딩 초보다. 답·화면 문구·주석·PR 설명은 쉬운 한국어로, 용어는 풀어서 쓴다.
배포: 윈도우용 ZIP → Releases `desk-latest`, 학생 공유 페이지 → GitHub Pages.

| 경로 | 내용 |
|---|---|
| `desk.py`, `desk/` | 로컬 서버(Python 표준 라이브러리). API `server.py`, 설정 `config.py`, 저장 `db.py`, 수집 `collect.py`, 텔레그램 `telegram.py`, AI `ai.py` |
| `web/` | 화면 (순수 HTML/CSS/JS ES 모듈, `js/views/*.js`) |
| `pass/` | 학생 폰용 조퇴증·외출증 페이지 (공개, 내용은 주소 `#` 뒤에만) |
| `browser-extension/` | 에듀파인·나이스 화면 보내기 확장 프로그램. 나이스 입력 칸 채우기는 **저장·제출 단추를 절대 누르지 않는다** |
| `tools/` | `build_portable.py` 윈도우 ZIP, `make_bats.py` `.bat` 생성(`.bat` 직접 고치지 말 것) |
| `docs/` | `HANDOFF.md` 지금 상태, `DESIGN.md` 기능별 설계 결정(고치는 기능 항목만 읽기), `CHANGELOG.md` 버전별 기록 |

## 지켜야 할 규칙

- **학생 이름·조퇴 사유·공문·쪽지는 이 PC 의 `data/desk.db` 에만.** 클라우드·로그로 보내지 않는다. `config.json`·`data/` 는 커밋하지 않는다. 예외는 아래뿐이며 모두 선생님 본인 계정으로만:
  - 텔레그램: 연결된 선생님 채팅(chat_id 하나)으로만. 학생 이름은 가리지 않음(설정 `telegram_mask_names` 켜면 가림), 기록·오류 문구의 이름은 늘 가림. **조퇴·외출 사유는 보내지 않음.** 원문·원본 파일은 선생님이 달라고 할 때만. 학부모 연락처(확인 알림), 학생용 웹 링크(사유 제외), 쿨메신저 사진(기본 켬)·쪽지 원문(`telegram_cool_text`, 기본 끔), 통화·문자 기록 파일 받기(기본 끔), 문서 작성 결과 파일(달라고 할 때만, 학생 이름이 남아 있으면 안 보냄, `telegram_docwrite`).
  - 구글 캘린더로 일정 보내기(기본 끔, `desk/gcal.py`).
  - 구글 드라이브 암호화 동기화·백업(기본 끔, `drive.appdata` 만, 복구 키는 이 PC config.json 에만, `desk/cloudsync.py`).
  - 자세한 범위는 `docs/DESIGN.md` 의 해당 항목.
- **AI**: 업무자료를 쓰는 AI 는 **이 PC 의 LM Studio(127.0.0.1)로만**, 학생 이름은 가려서 보내고 답에서도 가린다. 공문·쪽지·학생 정보는 인터넷 AI 로 보내지 않는다. 일반 질문(공개 자료만, 매번 동의)만 외부 API 선택 가능. 명령 알아듣기·날짜 계산은 규칙 기반(`desk/assist.py`).
- 서버는 `127.0.0.1` 전용, 토큰·Host·Origin 검사.
- 빌드 도구 없음. Python 은 표준 라이브러리만(선택: PDF `pypdf`, 드라이브 동기화 `cryptography` — 없으면 그 기능만 꺼짐). 화면 외부 라이브러리는 CDN(jsdelivr)만.
- `.bat` 은 CP949·CRLF, `if ( … )` 안 echo 에 괄호 금지(`tests/test_bats.py`).
- ZIP 안 배치는 `TeacherDesk/teacher-desk` + `TeacherDesk/pass` 유지(설치된 `update-desk.bat` 이 기대함).
- 선생님 PC: 개발 폴더 `C:\Users\user\Desktop\업무 관리(개발용)`, 실제 앱 `C:\Users\user\Desktop\업무 관리`(Releases 에서 스스로 업데이트하므로 개발 폴더에서 복사해 덮어쓰지 않는다). 개발 폴더는 포트를 실제 앱과 다르게, **텔레그램 토큰은 넣지 않는다**(같은 봇 두 곳이면 Conflict). 실제 학생 자료 대신 가짜 자료로 시험한다.

## 작업 방법

- 시작할 때 `git fetch origin main`·`git status`. 모르는 미커밋 변경은 지우지 말고 출처 확인.
- 시험: `python -m unittest discover -s tests -t .` 와 `node --test tests/pass-codec.test.cjs tests/browser-ai.test.cjs tests/capture-extension.test.cjs` (CI 와 같음).
- 브랜치에서 고치고 PR. PR 설명: 요구사항, 바꾼 내용, 실행한 테스트, 확인하지 못한 것(실제 PC·나이스·텔레그램에서만 알 수 있는 것).
- **머지 = 배포**(Releases ZIP·공유 페이지가 바로 바뀜). **선생님이 승인한 PR 만 머지한다.**
- 버전은 `desk/__init__.py` 한 줄. PR 이 겹치면 큰 쪽 +1, `CHANGELOG`·`DESIGN` 은 양쪽 줄을 모두 살린다. 앞 PR 이 머지되면 열린 PR 에 `main` 을 합쳐 둔다.
- 큰 작업을 마치면 `docs/CHANGELOG.md` 맨 아래에 한 줄, 새 설계 결정은 `docs/DESIGN.md` 에 한 항목.
- 이 파일은 짧게 유지한다. 오래된 규칙은 옮기지 말고 지운다.
