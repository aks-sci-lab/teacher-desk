# 폴더 AI 색인·초안 문서 시험 (앱에 넣기 전 시험용)

선생님 요청(2026-11-16): 지정한 폴더의 파일을 AI 로 읽어 색인을 만들고, 작업 내용을 입력하면 관련 파일을 찾아
문서 파일로 초안을 써 주는 기능. **앱(`desk/`, `web/`)에는 아직 넣지 않았고**, 이 폴더의 스크립트로 흐름만 시험한다.

| 파일 | 하는 일 |
|---|---|
| `make_samples.py` | 가상 학교 업무 폴더(hwp·hwpx·pdf·docx·xlsx 18개 + 깨진·스캔·임시 파일)와 검색 정답 12개 |
| `fake_lmstudio.py` | 127.0.0.1 가짜 LM Studio (규칙으로 뽑아 쓰기, 받은 요청 기록) |
| `folderindex.py` | 폴더 읽기 → 색인(sqlite) → 검색 3가지 → 초안 부탁. 앱의 `desk.docs`·`desk.ai`(0.5.99 나눠 요약, 이름 가리기)를 그대로 씀 |
| `writers.py` | 초안 글 → `.docx`·`.hwpx` (표준 라이브러리만), `.docx` 읽기 |
| `run_trial.py` | 전체 시험과 결과(`results.json`, `drafts/`) |

```
python tools/trial_folder_index/run_trial.py --out trial-out
python tools/trial_folder_index/run_trial.py --out trial-out --base http://127.0.0.1:1234/v1 --scale 0   # 진짜 LM Studio
```

- 예시 자료의 학생 이름은 모두 지어낸 것. 실제 자료로 돌리지 말 것(개발 폴더 규칙).
- 가짜 AI 로는 흐름·이름 가리기·시간 짐작만 확인된다. 요약·초안의 글 품질과 '같은 뜻 다른 말' 검색은 진짜 LM Studio 로 다시 재야 한다.
- pdf 예시는 LibreOffice 가 있으면 docx→pdf, 없으면 reportlab 으로 만든다(시험 도구에만 필요).
