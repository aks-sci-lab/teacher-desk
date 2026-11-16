"""0.5.100 구글 로그인 준비 도우미: 받은 클라이언트 JSON 넣기 · 연결 시험 · Drive API 꺼짐 안내 (가짜 드라이브)."""
import json
import unittest

from desk import gdrive
from tests import test_server
from tests.fake_gdrive import FakeDrive

CID = "123456789012-abcdefghij0123456789klmnopqrstuv.apps.googleusercontent.com"
FILE = json.dumps({"installed": {"client_id": CID, "project_id": "teacher-desk-1", "client_secret": "GOCSPX-abc_DEF-123",
                                 "redirect_uris": ["http://localhost"]}})


class ParseTests(unittest.TestCase):
    def test_desktop_file_and_typed_values(self):
        self.assertEqual(gdrive.parse_client(FILE), (CID, "GOCSPX-abc_DEF-123"))
        self.assertEqual(gdrive.parse_client("", f"  {CID} ", " GOCSPX-x1234567 "), (CID, "GOCSPX-x1234567"))

    def test_rejects_wrong_files(self):
        with self.assertRaisesRegex(ValueError, "데스크톱 앱"):
            gdrive.parse_client(json.dumps({"web": {"client_id": CID, "client_secret": "GOCSPX-abcdefgh"}}))
        for bad in ("not json", "[]", json.dumps({"type": "service_account"}),
                    json.dumps({"installed": {"client_id": "evil.example.com", "client_secret": "GOCSPX-abcdefgh"}}),
                    json.dumps({"installed": {"client_id": CID}})):
            with self.assertRaises(ValueError):
                gdrive.parse_client(bad)


class SetupServerTests(test_server.ServerTests):
    def test_client_file_then_login_and_check(self):
        fake = FakeDrive()
        try:
            vals = fake.config_values()
            vals.update(gdrive_client_id="", gdrive_client_secret="")
            self.env.config.values.update(vals)
            self.assertFalse(self.req("GET", "/api/cloud")[1]["client"])
            st, r = self.req("POST", "/api/cloud", {"action": "client", "json": json.dumps({"web": {}})})
            self.assertEqual(st, 400)
            self.assertIn("데스크톱 앱", r["error"])
            st, r = self.req("POST", "/api/cloud", {"action": "client", "json": FILE})
            self.assertEqual((st, r["client"]), (200, True))
            self.assertEqual(self.env.config["gdrive_client_id"], CID)
            text = json.dumps(self.req("GET", "/api/state")[1], ensure_ascii=False)
            self.assertNotIn("GOCSPX-abc_DEF-123", text)                    # 보안 비밀은 화면으로 돌려주지 않음
            st, r = self.req("POST", "/api/cloud", {"action": "login"})
            self.assertEqual(st, 200)
            self.assertIn("client_id=" + CID, r["url"])
            # 시험: 로그인 전이면 안내
            self.assertEqual(self.req("POST", "/api/cloud", {"action": "check"})[0], 400)
            self.env.config.values.update(gdrive_token="RT")
            st, r = self.req("POST", "/api/cloud", {"action": "check"})
            self.assertEqual((st, r["check"]["ok"]), (200, True))
            fake.fail = "disabled"
            self.app.cloud.drive._access = None
            st, r = self.req("POST", "/api/cloud", {"action": "check"})
            self.assertEqual(st, 400)
            self.assertIn("Drive API", r["error"])
            fake.fail = None
            # 다른 클라이언트로 바꾸면 예전 로그인은 풀림
            other = "999999999999-zzzzzzzzzz0123456789zzzzzzzzzzzz.apps.googleusercontent.com"
            st, r = self.req("POST", "/api/cloud", {"action": "client", "client_id": other, "client_secret": "GOCSPX-other123"})
            self.assertEqual((st, r["logged_in"], self.env.config["gdrive_token"]), (200, False, ""))
        finally:
            self.env.config.values.update(gdrive_token="", gdrive_enabled=False, gdrive_client_id="", gdrive_client_secret="")
            fake.close()


for _name in [n for n in dir(test_server.ServerTests) if n.startswith("test_")]:
    if getattr(SetupServerTests, _name) is getattr(test_server.ServerTests, _name):
        setattr(SetupServerTests, _name, None)

if __name__ == "__main__":
    unittest.main()
