"""선생님 본인 구글 드라이브 연결 (0.5.96) — 구글 로그인(OAuth '데스크톱 앱') + 드라이브의 숨은 앱 전용 폴더.

- 권한 범위는 `drive.appdata` 하나: 업무 데스크가 만든 숨은 폴더(appDataFolder)만 읽고 쓰며,
  선생님 드라이브의 다른 파일은 볼 수도 바꿀 수도 없습니다. 선생님 드라이브 화면에는 보이지 않고
  '설정 → 앱 관리'에서 지울 수 있습니다.
- 로그인: 브라우저에서 구글 허용 → http://127.0.0.1:<포트>/oauth/google 로 돌아옴 (PKCE + 한 번 쓰는 state).
  받은 '다시 받기 토큰'(refresh token)은 config.json 의 비밀값으로만 둡니다 (화면·로그에 안 보냄).
- 표준 라이브러리(urllib)만 씁니다. 주소는 설정 파일의 `gdrive_*_base` 로 바꿀 수 있어 시험은 가짜 서버로 합니다.
"""

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

SCOPE = "https://www.googleapis.com/auth/drive.appdata"
TIMEOUT = 30
REDIRECT_PATH = "/oauth/google"
# 배포자(선생님)가 Google Cloud 에서 만든 '데스크톱 앱' OAuth 클라이언트. 구글 문서상 데스크톱 앱의 클라이언트 값은
# 비밀로 지킬 수 없는 값이라 저장소에 둘 수 있습니다(실제 보호는 PKCE·선생님 계정 로그인·좁은 범위).
# 비어 있으면 설정 → 드라이브 → 고급에서 넣습니다. README 「여러 PC 이어 쓰기」의 '배포자 준비' 참고.
DEFAULT_CLIENT_ID = ""
DEFAULT_CLIENT_SECRET = ""


def client_id(config):
    return config["gdrive_client_id"] or DEFAULT_CLIENT_ID


def client_secret(config):
    return config["gdrive_client_secret"] or DEFAULT_CLIENT_SECRET


class DriveError(Exception):
    """연결·권한·네트워크 문제. offline 이면 잠시 뒤 다시 시도할 만한 것."""

    def __init__(self, message, offline=False, auth=False):
        super().__init__(message)
        self.offline = offline
        self.auth = auth


def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class Login:
    """로그인 한 번: 주소 만들기 → 돌아온 code 를 토큰으로 바꾸기. 10분 지나면 버림."""

    TTL = 600

    def __init__(self, config, port):
        self.config = config
        self.redirect = f"http://127.0.0.1:{port}{REDIRECT_PATH}"
        self.state = secrets.token_urlsafe(24)
        self.verifier = secrets.token_urlsafe(64)
        self.created = time.monotonic()

    def url(self):
        challenge = _b64url(hashlib.sha256(self.verifier.encode()).digest())
        q = {"client_id": client_id(self.config), "redirect_uri": self.redirect, "response_type": "code",
             "scope": SCOPE, "code_challenge": challenge, "code_challenge_method": "S256", "state": self.state,
             "access_type": "offline", "prompt": "consent"}
        return self.config["gdrive_auth_url"] + "?" + urllib.parse.urlencode(q)

    def expired(self):
        return time.monotonic() - self.created > self.TTL

    def finish(self, code):
        """code → refresh token. 실패하면 DriveError."""
        data = _token_request(self.config, {
            "grant_type": "authorization_code", "code": code, "code_verifier": self.verifier,
            "redirect_uri": self.redirect,
        })
        if not data.get("refresh_token"):
            raise DriveError("구글이 다시 받기 토큰을 주지 않았습니다. 구글 계정 → 보안 → 타사 앱에서 업무 데스크 연결을 지운 뒤 다시 로그인하세요", auth=True)
        return data["refresh_token"]


def _token_request(config, fields):
    body = urllib.parse.urlencode({"client_id": client_id(config), "client_secret": client_secret(config),
                                   **fields}).encode()
    req = urllib.request.Request(config["gdrive_token_url"], data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8")).get("error")
        except Exception:
            err = None
        if err in ("invalid_grant", "unauthorized_client", "invalid_client"):
            raise DriveError("구글 로그인이 풀렸습니다. 설정 → 드라이브에서 다시 로그인하세요", auth=True)
        raise DriveError(f"구글 로그인 서버 오류 ({e.code})", offline=e.code >= 500)
    except (urllib.error.URLError, OSError, ValueError):
        raise DriveError("인터넷에 연결할 수 없습니다", offline=True)


class Drive:
    """앱 전용 폴더의 파일을 이름으로 다룸 (이름은 업무 데스크가 정한 것만)."""

    def __init__(self, config):
        self.config = config
        self._access = None
        self._expires = 0

    # ---- 토큰 ----
    def _token(self):
        if self._access and time.monotonic() < self._expires - 60:
            return self._access
        refresh = self.config["gdrive_token"]
        if not refresh:
            raise DriveError("구글 로그인이 필요합니다", auth=True)
        data = _token_request(self.config, {"grant_type": "refresh_token", "refresh_token": refresh})
        self._access = data.get("access_token")
        if not self._access:
            raise DriveError("구글 로그인 토큰을 받지 못했습니다", auth=True)
        self._expires = time.monotonic() + int(data.get("expires_in") or 3600)
        return self._access

    def _call(self, method, url, body=None, headers=None, raw=False, retry=True):
        h = {"Authorization": f"Bearer {self._token()}", **(headers or {})}
        req = urllib.request.Request(url, data=body, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT * 4) as r:
                data = r.read()
                if raw:
                    return data, r.headers
                return json.loads(data.decode("utf-8")) if data else {}
        except urllib.error.HTTPError as e:
            if e.code == 401 and retry:
                self._access = None   # 토큰이 끝났으면 한 번 새로 받아 다시
                return self._call(method, url, body, headers, raw, retry=False)
            if e.code in (401, 403):
                raise DriveError("드라이브 권한이 없습니다. 설정 → 드라이브에서 다시 로그인하세요", auth=True)
            if e.code == 404:
                raise DriveError("드라이브에서 파일을 찾지 못했습니다")
            raise DriveError(f"드라이브 서버 오류 ({e.code})", offline=e.code >= 500 or e.code == 429)
        except (urllib.error.URLError, OSError):
            raise DriveError("인터넷에 연결할 수 없습니다", offline=True)

    # ---- 파일 ----
    def files(self):
        """{이름: {id, size, modifiedTime}} — 앱 전용 폴더 안의 것만."""
        out, token = {}, None
        while True:
            q = {"spaces": "appDataFolder", "fields": "nextPageToken,files(id,name,size,modifiedTime)", "pageSize": "200"}
            if token:
                q["pageToken"] = token
            data = self._call("GET", self.config["gdrive_api_base"] + "/drive/v3/files?" + urllib.parse.urlencode(q))
            for f in data.get("files") or []:
                out[f["name"]] = f
            token = data.get("nextPageToken")
            if not token:
                return out

    def read(self, file_id):
        data, _ = self._call("GET", f"{self.config['gdrive_api_base']}/drive/v3/files/{urllib.parse.quote(file_id)}?alt=media", raw=True)
        return data

    def write(self, name, data, file_id=None):
        """새로 만들거나(file_id 없음) 내용을 바꿈. 크기 제한 없는 '이어 올리기(resumable)' 방식 한 번에."""
        base = self.config["gdrive_upload_base"] + "/upload/drive/v3/files"
        if file_id:
            url, method, meta = f"{base}/{urllib.parse.quote(file_id)}?uploadType=resumable", "PATCH", {}
        else:
            url, method, meta = f"{base}?uploadType=resumable", "POST", {"name": name, "parents": ["appDataFolder"]}
        _, headers = self._call(method, url, json.dumps(meta).encode(), raw=True, headers={
            "Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": "application/octet-stream",
            "X-Upload-Content-Length": str(len(data))})
        session = headers.get("Location")
        if not session:
            raise DriveError("드라이브가 올리기 주소를 주지 않았습니다")
        return self._call("PUT", session, bytes(data), headers={"Content-Type": "application/octet-stream"})

    def delete(self, file_id):
        self._call("DELETE", f"{self.config['gdrive_api_base']}/drive/v3/files/{urllib.parse.quote(file_id)}", raw=True)

    def revoke(self):
        """로그아웃: 구글에 토큰을 돌려줌 (실패해도 이 PC 에서는 지움)."""
        token = self.config["gdrive_token"]
        if not token:
            return
        try:
            req = urllib.request.Request(self.config["gdrive_revoke_url"], method="POST",
                                         data=urllib.parse.urlencode({"token": token}).encode(),
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            urllib.request.urlopen(req, timeout=10).close()
        except Exception:
            pass
