"""시험용 가상 학교 업무 폴더 만들기 (실제 학생·학교 정보 없음, 이름은 모두 지어낸 것).

    python tools/trial_folder_index/make_samples.py 폴더

hwp(표준 라이브러리 시험용 생성기), hwpx·docx(writers.py), xlsx(표준 라이브러리), pdf(LibreOffice 로 docx→pdf,
없으면 reportlab) 를 섞어 20개 남짓 만든다. 검색 정답(QUERIES)도 여기 둔다.
"""

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests.hwp_fixture import build_hwp   # noqa: E402
import writers                             # noqa: E402

# 가짜 학생 명단 (이름 가리기 시험용)
STUDENTS = ["김민준", "이서연", "박지호", "최수아", "정하준", "강예린", "윤도현", "임채원"]

DOCS = {
    "과학과/2학기_과학과_협의회_회의록_1차.hwpx": """# 2학기 과학과 교과협의회 회의록 (1차)
일시: 2026. 9. 3.(목) 15:30 ~ 16:30
장소: 과학실 2
참석: 과학과 교사 5명 (불참 없음)
## 안건 1. 2학기 수행평가 계획
- 1학년 통합과학: 탐구 보고서 30%, 실험 수행 20%
- 2학년 물리학: 실험 보고서 2회, 서술형 평가 1회
- 채점 기준표는 9월 18일까지 평가 담당에게 제출
## 안건 2. 실험실 안전 관리
- 약품장 잠금 상태 매주 금요일 점검
- 학생 안전교육은 첫 실험 수업 전에 실시
## 협의 결과
- 수행평가 세부 기준은 다음 협의회(10월)에서 최종 확정
""",
    "과학과/2학기_과학과_협의회_회의록_2차.docx": """# 2학기 과학과 교과협의회 회의록 (2차)
일시: 2026. 10. 15.(목) 15:30
장소: 과학실 2
## 안건 1. 수행평가 세부 기준 확정
- 탐구 보고서 채점 기준 4단계(상·중상·중·하)로 확정
- 실험 수행 평가는 관찰 체크리스트 사용
## 안건 2. 과학의 날 행사 결과 공유
- 참가 학생 112명, 우수 작품 6점
## 안건 3. 3학년 교육과정 편성 의견
- 2027학년도 3학년 선택과목에 '과학 탐구 실험' 개설 요청
""",
    "과학과/1학기_과학과_협의회_회의록.hwp": """1학기 과학과 교과협의회 회의록
표
일시: 2026. 3. 12.(목) 16:00, 장소: 과학실 1
안건: 연간 교과협의회 운영 계획, 실험 기자재 구입 목록
결과: 협의회는 매월 둘째 주 목요일에 열기로 함
기자재: 디지털 저울 10대, 전원 장치 8대 구입 요청""",
    "과학과/2026_과학과_협의회_운영계획.docx": """# 2026학년도 과학과 교과협의회 운영 계획
## 운영 목적
- 수업·평가 개선을 위한 교사 간 협력
## 운영 일정
| 월 | 주제 |
|---|---|
| 3월 | 연간 계획, 기자재 |
| 6월 | 1학기 평가 결과 분석 |
| 9월 | 2학기 수행평가 계획 |
| 10월 | 수행평가 기준 확정 |
| 12월 | 교육과정 편성 협의 |
## 기록
- 회의록은 회의 후 1주일 안에 교무 기획부에 제출
""",
    "평가/2학기_수행평가_세부기준_과학.xlsx": [
        ["과목", "영역", "배점", "상", "중", "하"],
        ["통합과학", "탐구 보고서", "30", "가설·변인·결론이 모두 타당", "두 가지 타당", "한 가지 이하"],
        ["통합과학", "실험 수행", "20", "안전 수칙 준수, 정확한 측정", "측정 오차 큼", "수행 미흡"],
        ["물리학", "실험 보고서", "20", "그래프와 해석 정확", "해석 일부 오류", "미제출"],
    ],
    "공문/2026_학업성적관리위원회_개최_안내.pdf": """# 2026학년도 2학기 학업성적관리위원회 개최 안내
1. 관련: 학업성적관리규정 제5조
2. 2학기 수행평가 계획 및 세부 기준 심의를 위하여 학업성적관리위원회를 아래와 같이 개최합니다.
## 개최 일시 및 장소
- 일시: 2026. 9. 24.(목) 16:00
- 장소: 교무실 회의실
## 제출 요청
- 교과별 수행평가 세부 기준표를 9. 18.(금)까지 평가 담당에게 제출
""",
    "학년/현장체험학습_계획서_2학년.hwpx": """# 2학년 현장체험학습 운영 계획
## 개요
- 일시: 2026. 10. 22.(목) 08:30 ~ 16:30
- 장소: 국립과천과학관
- 대상: 2학년 전체 210명, 인솔 교사 9명
## 세부 일정
| 시간 | 활동 |
|---|---|
| 08:30 | 학교 출발 |
| 10:00 | 상설 전시관 관람 |
| 13:00 | 천체 투영관 |
| 16:30 | 학교 도착 |
## 안전 대책
- 출발 전 안전교육, 버스별 인솔 교사 1명
""",
    "학년/현장체험학습_안전점검표.xlsx": [
        ["점검 항목", "결과", "비고"],
        ["버스 보험 가입 확인", "완료", "5대"],
        ["학생 비상연락망", "완료", ""],
        ["사전 안전교육", "완료", "10/20 실시"],
        ["응급 약품 준비", "완료", "보건실"],
    ],
    "학년/학부모_상담주간_운영_안내.pdf": """# 2학기 학부모 상담주간 운영 안내
1. 기간: 2026. 10. 26.(월) ~ 10. 30.(금)
2. 방법: 대면 상담 또는 전화 상담 (학부모 선택)
3. 신청: 10. 21.(수)까지 담임교사에게 회신서 제출
4. 협조 사항: 상담 결과는 상담 기록부에 정리하여 11. 6.(금)까지 학년부에 제출
""",
    "학년/상담주간_일정표_2-3.xlsx": [
        ["날짜", "시간", "학생", "방법"],
        ["10/26", "16:00", "김민준", "대면"],
        ["10/26", "16:30", "이서연", "전화"],
        ["10/27", "16:00", "박지호", "대면"],
        ["10/28", "16:00", "최수아", "전화"],
    ],
    "행사/과학의날_행사_계획.hwpx": """# 2026 과학의 날 행사 계획
## 일시·장소
- 2026. 10. 8.(목) 5~7교시, 강당 및 과학실
## 행사 내용
- 과학 탐구 발표 대회, 과학 상자 만들기, 과학 골든벨
## 시상
- 부문별 금상 1명, 은상 2명 (학교장 상)
## 협조
- 과학과 교사 전원 심사 참여
""",
    "행사/과학의날_시상_결과.xlsx": [
        ["부문", "상", "학년반", "이름"],
        ["탐구 발표", "금상", "2-3", "정하준"],
        ["탐구 발표", "은상", "1-5", "강예린"],
        ["과학 상자", "금상", "1-2", "윤도현"],
        ["골든벨", "금상", "2-1", "임채원"],
    ],
    "공문/학교폭력_예방교육_결과_보고.docx": """# 2학기 학교폭력 예방교육 실시 결과 보고
## 실시 개요
- 일시: 2026. 9. 10.(목) 6교시
- 대상: 전교생, 방법: 학급별 영상 교육 후 토의
## 결과
- 참여율 98%, 만족도 4.3점(5점 만점)
## 개선 사항
- 사이버 폭력 사례 중심으로 내용 보강 필요
""",
    "공문/정보보안_점검_안내.hwp": """2026년 하반기 개인정보 보호 및 정보보안 점검 안내
표
점검 기간: 2026. 11. 2. ~ 11. 13.
점검 내용: PC 비밀번호 설정, 개인정보 파일 암호화, 화면 보호기
제출: 자가 점검표를 11. 13.까지 정보부에 제출""",
    "공문/교원능력개발평가_실시_안내.hwpx": """# 2026학년도 교원능력개발평가 실시 안내
## 평가 기간
- 2026. 10. 12.(월) ~ 10. 30.(금)
## 참여 대상
- 교원, 학생(초4 이상), 학부모
## 유의 사항
- 학생 참여는 수업 시간을 활용하여 정보실에서 실시
""",
    "교육과정/2027_교육과정_편성_협의_결과.docx": """# 2027학년도 교육과정 편성 협의 결과
## 협의 일시
- 2026. 11. 5.(목) 15:00, 교육과정부
## 주요 결정
- 3학년 과학 선택과목: 물리학Ⅱ, 화학Ⅱ, 과학 탐구 실험
- 2학년 공통 과목 시수 조정 없음
## 남은 일
- 교과별 개설 희망 조사 11. 20.까지
""",
    "과학과/실험실_안전교육_계획.hwp": """2026학년도 과학 실험실 안전교육 계획
표
대상: 1~2학년 전체, 시기: 각 학기 첫 실험 수업 전
내용: 약품 취급, 화재 시 대피, 보안경 착용, 깨진 유리 처리
기록: 안전교육 실시 후 학생 서명부를 과학부에 보관""",
}

# 긴 문서 (나눠 요약 시험): 방과후 강좌 목록 110개, 약 1만 자
def _long_afterschool():
    subjects = ["수학 심화", "영어 독해", "과학 실험 탐구", "코딩 기초", "논술", "중국어 회화", "미술 창작",
                "밴드 합주", "수학 기초", "물리 문제 풀이", "화학 실험", "생명과학 탐구", "지구과학 관측"]
    lines = ["# 2026학년도 2학기 방과후학교 운영 계획", "## 운영 기간",
             "- 2026. 9. 7.(월) ~ 12. 18.(금), 주 2회, 방과후 1~2차시",
             "## 수강 신청", "- 신청 기간: 2026. 9. 1.(화) ~ 9. 4.(금), 학교 누리집에서 신청",
             "- 수강료는 스쿨뱅킹으로 9. 10.(목) 출금", "## 개설 강좌"]
    for i in range(110):
        s = subjects[i % len(subjects)]
        lines.append(f"- 강좌 {i + 1}: {s} {i // len(subjects) + 1}반, 대상 {1 + i % 3}학년, 정원 {15 + i % 10}명, "
                     f"요일 {'월수' if i % 2 else '화목'}, 장소 {'과학실' if '과학' in s or '실험' in s else '일반 교실'} "
                     f"{i % 7 + 1}, 강사 외부 강사 {i + 1}번, 교재비 {5000 + i % 4 * 2500}원")
        if i % 10 == 9:
            lines.append("※ 위 강좌는 수강 인원이 정원의 절반에 못 미치면 폐강될 수 있으며, 폐강 시 수강료는 전액 돌려줌.")
    lines += ["## 안전 관리", "- 귀가 지도: 마지막 차시 후 담당 교사가 출석 확인",
              "## 결과 보고", "- 강좌별 운영 결과(출석률·만족도)를 2026. 12. 24.(목)까지 방과후 담당에게 제출"]
    return "\n".join(lines)


DOCS["공문/2026_2학기_방과후학교_운영계획.pdf"] = _long_afterschool()

# 작업 내용 → 정답 파일 (앞쪽이 가장 중요). 'synonym' 은 낱말이 문서와 달라 규칙 검색으로는 어려운 것
QUERIES = [
    ("2학기 과학과 협의회 회의록 작성", ["2학기_과학과_협의회_회의록_1차", "2학기_과학과_협의회_회의록_2차",
                                    "2026_과학과_협의회_운영계획"], "plain"),
    ("현장체험학습 결과 보고서 작성", ["현장체험학습_계획서_2학년", "현장체험학습_안전점검표"], "plain"),
    ("학부모 상담주간 운영 결과 정리", ["학부모_상담주간_운영_안내", "상담주간_일정표_2-3"], "plain"),
    ("과학의 날 행사 결과 보고", ["과학의날_행사_계획", "과학의날_시상_결과"], "plain"),
    ("실험실 안전교육 실시 결과", ["실험실_안전교육_계획", "2학기_과학과_협의회_회의록_1차"], "plain"),
    ("수행평가 세부 기준 안내문", ["2학기_수행평가_세부기준_과학", "2학기_과학과_협의회_회의록_2차",
                              "2026_학업성적관리위원회_개최_안내"], "plain"),
    ("방과후 강좌 폐강 안내 가정통신문", ["2026_2학기_방과후학교_운영계획"], "plain"),
    ("3학년 선택과목 개설 협의", ["2027_교육과정_편성_협의_결과", "2학기_과학과_협의회_회의록_2차"], "plain"),
    ("교과 모임 기록 정리", ["2학기_과학과_협의회_회의록_1차", "2학기_과학과_협의회_회의록_2차",
                       "1학기_과학과_협의회_회의록"], "synonym"),
    ("학부모 면담 결과 정리", ["학부모_상담주간_운영_안내", "상담주간_일정표_2-3"], "synonym"),
    ("개인정보 보호 자가 점검표 제출", ["정보보안_점검_안내"], "plain"),
    ("견학 다녀온 뒤 보고", ["현장체험학습_계획서_2학년", "현장체험학습_안전점검표"], "synonym"),
]


def _xlsx(rows, sheet="Sheet1"):
    def col(i):
        s = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s
    data = "".join(f'<row r="{r + 1}">' + "".join(
        f'<c r="{col(c)}{r + 1}" t="inlineStr"><is><t>{escape(v)}</t></is></c>' for c, v in enumerate(row)) + "</row>"
        for r, row in enumerate(rows))
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{sheet}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{data}</sheetData></worksheet>')
    return buf.getvalue()


def _pdf(path, text):
    """docx 를 만든 뒤 LibreOffice 로 pdf 로 (학교 공문 pdf 와 비슷하게 글자가 든 pdf)."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / (path.stem + ".docx")
            writers.write_docx(src, text)
            subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
                           check=True, capture_output=True, timeout=120)
            shutil.move(str(Path(tmp) / (path.stem + ".pdf")), path)
        return
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfgen import canvas
    pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    c = canvas.Canvas(str(path))
    y = 800
    for line in text.splitlines():
        c.setFont("HYSMyeongJo-Medium", 10)
        c.drawString(40, y, line.lstrip("#- "))
        y -= 16
        if y < 40:
            c.showPage()
            y = 800
    c.save()


def make(out):
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    for rel, content in DOCS.items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        ext = p.suffix
        if ext == ".hwpx":
            writers.write_hwpx(p, content, title=p.stem)
        elif ext == ".docx":
            writers.write_docx(p, content)
        elif ext == ".hwp":
            p.write_bytes(build_hwp(content.splitlines()))
        elif ext == ".xlsx":
            p.write_bytes(_xlsx(content))
        elif ext == ".pdf":
            _pdf(p, content)
    # 읽을 수 없는 파일들 (실패 처리 시험)
    (out / "공문").mkdir(exist_ok=True)
    from reportlab.pdfgen import canvas   # 글자 없는(스캔한 그림 같은) pdf
    c = canvas.Canvas(str(out / "공문/스캔본_출장_공문.pdf"))
    c.rect(50, 500, 400, 250, fill=1)
    c.save()
    (out / "공문/깨진_파일.docx").write_bytes(b"PK\x03\x04 broken")
    (out / "학년/사진_현장체험.jpg").write_bytes(b"\xff\xd8\xff\xe0 not really a photo")
    (out / "~$임시_잠금파일.docx").write_bytes(b"lock")
    return out


if __name__ == "__main__":
    print(make(sys.argv[1] if len(sys.argv) > 1 else "samples"))
