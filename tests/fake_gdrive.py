"""시험용 가짜 구글 로그인·드라이브 서버 (0.5.96): 앱 전용 폴더(appDataFolder)의 파일만 흉내 냄."""

import json
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeDrive:
    def __init__(self):
        self.files = {}          # id → {"name", "data"}
        self.sessions = {}       # 올리기 주소 → (file id 또는 None, 이름)
        self.codes = {}          # 로그인 code → code_verifier 확인용
        self.revoked = []
        self.fail = None         # None · "500" · "auth"
        self.calls = []
        self.lock = threading.Lock()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", ctype="application/json", headers=None):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def _authed(self):
                return self.headers.get("Authorization") == "Bearer AT"

            def handle_any(self, method):
                url = urllib.parse.urlparse(self.path)
                q = dict(urllib.parse.parse_qsl(url.query))
                body = self._body()
                fake.calls.append((method, url.path))
                if url.path == "/token":
                    form = dict(urllib.parse.parse_qsl(body.decode()))
                    if fake.fail == "auth":
                        return self._send(400, {"error": "invalid_grant"})
                    if form.get("grant_type") == "authorization_code":
                        if form.get("code") not in fake.codes:
                            return self._send(400, {"error": "invalid_grant"})
                        return self._send(200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})
                    if form.get("refresh_token") != "RT":
                        return self._send(400, {"error": "invalid_grant"})
                    return self._send(200, {"access_token": "AT", "expires_in": 3600})
                if url.path == "/revoke":
                    fake.revoked.append(body)
                    return self._send(200, {})
                if fake.fail == "500":
                    return self._send(503, {"error": "busy"})
                if not self._authed():
                    return self._send(401, {"error": "unauthorized"})
                with fake.lock:
                    if method == "GET" and url.path == "/drive/v3/files":
                        assert q.get("spaces") == "appDataFolder"
                        return self._send(200, {"files": [{"id": i, "name": f["name"], "size": str(len(f["data"]))}
                                                          for i, f in fake.files.items()]})
                    if method == "GET" and url.path.startswith("/drive/v3/files/"):
                        f = fake.files.get(url.path.rsplit("/", 1)[1])
                        if not f:
                            return self._send(404, {})
                        return self._send(200, f["data"], "application/octet-stream")
                    if method == "POST" and url.path.startswith("/drive/v3/files/") and url.path.endswith("/copy"):
                        src = fake.files.get(url.path.split("/")[-2])
                        if not src:
                            return self._send(404, {})
                        meta = json.loads(body or b"{}")
                        assert meta.get("parents") == ["appDataFolder"], meta
                        fid = uuid.uuid4().hex[:12]
                        fake.files[fid] = {"name": meta["name"], "data": src["data"]}
                        return self._send(200, {"id": fid, "name": meta["name"]})
                    if method == "DELETE" and url.path.startswith("/drive/v3/files/"):
                        fake.files.pop(url.path.rsplit("/", 1)[1], None)
                        return self._send(204)
                    if url.path.startswith("/upload/drive/v3/files") and q.get("uploadType") == "resumable":
                        sid = uuid.uuid4().hex
                        if method == "POST":
                            meta = json.loads(body or b"{}")
                            assert meta.get("parents") == ["appDataFolder"], meta
                            fake.sessions[sid] = (None, meta["name"])
                        else:
                            fid = url.path.rsplit("/", 1)[1]
                            if fid not in fake.files:
                                return self._send(404, {})
                            fake.sessions[sid] = (fid, fake.files[fid]["name"])
                        host = self.headers.get("Host")
                        return self._send(200, {}, headers={"Location": f"http://{host}/session/{sid}"})
                    if method == "PUT" and url.path.startswith("/session/"):
                        fid, name = fake.sessions.pop(url.path.rsplit("/", 1)[1])
                        fid = fid or uuid.uuid4().hex[:12]
                        fake.files[fid] = {"name": name, "data": body}
                        return self._send(200, {"id": fid, "name": name})
                return self._send(404, {})

            def do_GET(self):
                self.handle_any("GET")

            def do_POST(self):
                self.handle_any("POST")

            def do_PUT(self):
                self.handle_any("PUT")

            def do_PATCH(self):
                self.handle_any("PATCH")

            def do_DELETE(self):
                self.handle_any("DELETE")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def config_values(self):
        return {"gdrive_client_id": "test-client.apps.googleusercontent.com", "gdrive_client_secret": "test-secret",
                "gdrive_auth_url": self.base + "/auth", "gdrive_token_url": self.base + "/token",
                "gdrive_revoke_url": self.base + "/revoke", "gdrive_api_base": self.base, "gdrive_upload_base": self.base}

    def by_name(self, name):
        for f in self.files.values():
            if f["name"] == name:
                return f["data"]
        return None

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
