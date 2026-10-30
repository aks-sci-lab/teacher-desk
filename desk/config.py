"""설정 파일(config.json) 읽기·쓰기.

config.json 은 이 PC 에만 있는 파일입니다(텔레그램 토큰 같은 비밀값이 들어가므로 git 에 올리지 않음).
없으면 기본값으로 시작하고, 화면의 ⚙ 설정에서 저장하면 만들어집니다.
"""

import json
import re
import threading
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]

DEFAULTS = {
    "port": 8770,
    "data_dir": "data",
    # 학교·학급 정보 (조퇴증·외출증에 인쇄됨)
    "school_name": "",
    "grade": "",
    "class_no": "",
    "teacher_name": "",
    # 수집할 폴더
    "coolmessenger_name": "",      # 쿨메신저 쪽지 기록: %LOCALAPPDATA%\\CoolMessenger\\Memo\\<이름>.udb
    "coolmessenger_path": "",      # (선택) .udb 파일 경로를 직접 지정하면 이름보다 우선
    "coolmessenger_folder": "",    # (선택) 쿨메신저가 받은 파일을 저장하는 폴더
    "edufine_folder": "",
    # 실시간 감시 (쿨메신저 쪽지 파일·폴더가 바뀌면 바로 수집), 몇 초마다 살펴볼지
    "watch_enabled": True,
    "watch_interval": 20,
    # 켤 때 새 버전 확인·자동 업데이트 (윈도우용 ZIP 설치본만)
    "auto_update": True,
    # 하루 수집 시각
    "collect_times": ["09:00", "12:00", "15:00"],
    # 학생 공유용 공개 페이지 주소 (저장소의 pass/ 폴더가 배포된 곳)
    "pass_page_url": "https://aks-sci-lab.github.io/teacher-desk/",
    # 나이스 학사일정 (open.neis.go.kr 인증키, 고른 학교 {name, atpt, code})
    "neis_key": "",
    "neis_school": {},
    "neis_api_base": "https://open.neis.go.kr/hub",
    # 브리핑 '지난 1주일 AI 요약': 이 PC 의 LM Studio 로컬 서버 (모델 이름을 비우면 불러온 첫 모델)
    "ai_enabled": False,
    "ai_auto_item": True,         # AI 가 켜져 있으면 새 수신 항목을 뒤에서 자동 요약 (이 PC 의 LM Studio 로만)
    "ai_base": "http://127.0.0.1:1234/v1",
    "ai_model": "",
    "ai_input_size": "normal",     # normal: 14000자 / short: 6000자 (짧으면 빠름)
    "ai_provider": "local",       # 일반 질문 전용. 업무자료 요약·검색은 항상 로컬
    "ai_openai_model": "",
    "ai_anthropic_model": "",
    "ai_gemini_model": "",
    "ai_openai_key": "",
    "ai_anthropic_key": "",
    "ai_gemini_key": "",
    # 브라우저 단축키로 화면 보내기 (설정에서 켜면 만들어지는 연결 키, 비어 있으면 꺼짐)
    "capture_key": "",
    # 텔레그램
    "telegram_token": "",
    "telegram_detail": "titles",  # counts: 개수만 / titles: 제목까지 / summary: 요약까지
    "telegram_api_base": "https://api.telegram.org",
    # 화면 배치 (🧩 화면 배치 모드에서 저장)
    "layout": {},
}

# 화면에 그대로 돌려주면 안 되는 값
SECRET_KEYS = {"capture_key", "telegram_token", "neis_key", "ai_openai_key", "ai_anthropic_key", "ai_gemini_key"}
# 화면에서 바꿀 수 있는 값 (port, data_dir, api_base 는 파일에서만)
EDITABLE_KEYS = {
    "school_name", "grade", "class_no", "teacher_name",
    "coolmessenger_name", "coolmessenger_path", "coolmessenger_folder", "edufine_folder", "collect_times",
    "pass_page_url", "telegram_token", "telegram_detail", "layout", "neis_key", "neis_school",
    "watch_enabled", "auto_update", "ai_enabled", "ai_auto_item", "ai_base", "ai_model", "ai_input_size",
    "ai_provider", "ai_openai_model", "ai_anthropic_model", "ai_gemini_model",
    "ai_openai_key", "ai_anthropic_key", "ai_gemini_key",
}
BOOL_KEYS = {"watch_enabled", "auto_update", "ai_enabled", "ai_auto_item"}


# 예전 기본값을 그대로 쓰던 설정은 새 기본값으로 (저장소를 옮기면서 주소가 바뀜)
OLD_DEFAULTS = {"pass_page_url": ["https://aks-sci-lab.web.app/pass/"]}


class Config:
    def __init__(self, path=None):
        self.path = Path(path) if path else APP_DIR / "config.json"
        self._lock = threading.RLock()
        self.values = dict(DEFAULTS)
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                self.values.update(json.load(f))
            for k, olds in OLD_DEFAULTS.items():
                if self.values.get(k) in olds:
                    self.values[k] = DEFAULTS[k]

    def __getitem__(self, key):
        with self._lock:
            return self.values.get(key, DEFAULTS.get(key))

    def get(self, key, default=None):
        with self._lock:
            return self.values.get(key, default)

    @property
    def data_dir(self):
        d = Path(self["data_dir"])
        if not d.is_absolute():
            d = self.path.parent / d
        d.mkdir(parents=True, exist_ok=True)
        return d

    def public(self):
        """화면에 보낼 설정: 비밀값은 '설정됨' 여부만."""
        with self._lock:
            out = {k: v for k, v in self.values.items() if k not in SECRET_KEYS}
            for k in SECRET_KEYS:
                out[k + "_set"] = bool(self.values.get(k))
            return out

    def update(self, changes):
        """화면에서 온 변경을 검사해 저장. 잘못된 값이면 ValueError."""
        clean = {}
        for k, v in changes.items():
            if k not in EDITABLE_KEYS:
                continue
            if k in BOOL_KEYS:
                if not isinstance(v, bool):
                    raise ValueError(f"{k} 는 켬/끔이어야 합니다")
                clean[k] = v
            elif k == "collect_times":
                clean[k] = normalize_times(v)
            elif k == "telegram_detail":
                if v not in ("counts", "titles", "summary"):
                    raise ValueError("telegram_detail 값이 잘못되었습니다")
                clean[k] = v
            elif k == "neis_school":
                if not (isinstance(v, dict) and all(isinstance(v.get(x, ""), str) for x in ("name", "atpt", "code"))):
                    raise ValueError("학교 정보가 잘못되었습니다")
                clean[k] = {x: v.get(x, "")[:80] for x in ("name", "atpt", "code", "office")}
            elif k == "ai_input_size":
                if v not in ("normal", "short"):
                    raise ValueError("보내는 양 값이 잘못되었습니다")
                clean[k] = v
            elif k == "ai_base":
                if not isinstance(v, str):
                    raise ValueError("AI 주소는 글자여야 합니다")
                from .ai import AIError, check_local
                try:
                    check_local(v.strip())
                except AIError as e:
                    raise ValueError(str(e))
                clean[k] = v.strip()
            elif k == "ai_provider":
                if v not in ("local", "browser", "openai", "anthropic", "gemini"):
                    raise ValueError("AI 사용 방식이 잘못되었습니다")
                clean[k] = v
            elif k in ("ai_openai_model", "ai_anthropic_model", "ai_gemini_model"):
                if not isinstance(v, str) or (v.strip() and not re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", v.strip())):
                    raise ValueError("API 모델 이름은 영문·숫자·점·밑줄·하이픈·콜론 120자 이내로 입력하세요")
                clean[k] = v.strip()
            elif k in ("ai_openai_key", "ai_anthropic_key", "ai_gemini_key"):
                if not isinstance(v, str) or len(v.strip()) > 512 or any(not 33 <= ord(c) <= 126 for c in v.strip()):
                    raise ValueError("API 키 형식이 잘못되었습니다")
                clean[k] = v.strip()
            elif k == "layout":
                if not isinstance(v, dict):
                    raise ValueError("layout 은 객체여야 합니다")
                clean[k] = v
            else:
                if not isinstance(v, str):
                    raise ValueError(f"{k} 는 글자여야 합니다")
                clean[k] = v.strip()
        with self._lock:
            self.values.update(clean)
            self._write()

    def set_value(self, key, value):
        """화면에서 직접 고치지 않는 값(연결 키 등)을 서버가 저장."""
        with self._lock:
            self.values[key] = value
            self._write()

    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.values, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)


def normalize_times(value):
    """'9:00, 12:00' 또는 ['9:00'] → ['09:00', '12:00'] (정렬·중복 제거)."""
    if isinstance(value, str):
        value = [p for p in value.replace("，", ",").split(",")]
    if not isinstance(value, (list, tuple)):
        raise ValueError("수집 시각 형식이 잘못되었습니다 (예: 09:00, 12:00)")
    out = set()
    for t in value:
        t = str(t).strip()
        if not t:
            continue
        h, _, m = t.partition(":")
        try:
            h, m = int(h), int(m or 0)
        except ValueError:
            raise ValueError(f"시각이 잘못되었습니다: {t}")
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError(f"시각이 잘못되었습니다: {t}")
        out.add(f"{h:02d}:{m:02d}")
    return sorted(out)
