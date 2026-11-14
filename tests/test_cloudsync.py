"""여러 PC 이어 쓰기 (0.5.96): 가짜 드라이브로 동기화·잠금·충돌·암호 틀림·인터넷 끊김."""
import json
import urllib.parse
import time
import unittest

from desk import cloudcrypt, cloudsync, gdrive
from tests.fake_gdrive import FakeDrive
from tests.test_collect import Env

KEY = cloudcrypt.new_recovery_key()
HAS_CRYPTO = cloudcrypt.available()


def student_names(db):
    return [r["name"] for r in db.query("SELECT name FROM students ORDER BY number")]


@unittest.skipUnless(HAS_CRYPTO, "cryptography 가 없는 환경")
class CryptTests(unittest.TestCase):
    def test_round_trip_and_wrong_key(self):
        blob = cloudcrypt.encrypt(b"SQLite format 3\x00 secret", KEY)
        self.assertNotIn(b"secret", blob)
        self.assertEqual(cloudcrypt.decrypt(blob, KEY.lower().replace("-", " ")), b"SQLite format 3\x00 secret")   # 키는 대소문자·하이픈 무관
        with self.assertRaisesRegex(cloudcrypt.CryptError, "맞지 않거나"):
            cloudcrypt.decrypt(blob, cloudcrypt.new_recovery_key())
        tampered = blob[:5] + bytes([blob[5] ^ 1]) + blob[6:]   # 머리를 바꿔도 풀리지 않음
        with self.assertRaises(cloudcrypt.CryptError):
            cloudcrypt.decrypt(tampered, KEY)
        with self.assertRaises(cloudcrypt.CryptError):
            cloudcrypt.decrypt(b"not ours", KEY)
        with self.assertRaises(cloudcrypt.CryptError):
            cloudcrypt.check_passphrase("짧음")

    def test_recovery_key_shape(self):
        k = cloudcrypt.new_recovery_key()
        self.assertRegex(k, r"^([A-Z2-9]{5}-){4}[A-Z2-9]{5}$")
        self.assertNotRegex(k, "[01OIL]")


@unittest.skipUnless(HAS_CRYPTO, "cryptography 가 없는 환경")
class SyncTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDrive()
        self.pcs = []

    def tearDown(self):
        for env, _ in self.pcs:
            env.close()
        self.fake.close()
        cloudsync._paused.clear()   # 다른 시험에 읽기 전용이 남지 않게

    def pc(self, name, key=KEY):
        env = Env()
        env.config.values.update(self.fake.config_values(), gdrive_enabled=True, gdrive_token="RT",
                                 gdrive_passphrase=key, gdrive_pc_name=name)
        sync = cloudsync.Sync(env.db, env.config)
        self.pcs.append((env, sync))
        return env, sync

    def test_first_pc_uploads_encrypted_second_pc_receives(self):
        a, sa = self.pc("학교")
        a.db.replace_students([{"number": 1, "name": "가짜학생"}])
        self.assertEqual(sa.startup(), "ok")
        blob = self.fake.by_name(cloudsync.DATA)
        self.assertTrue(blob.startswith(b"TDSK"))
        self.assertNotIn("가짜학생".encode(), blob)              # 드라이브에는 암호화된 것만
        state = json.loads(self.fake.by_name(cloudsync.STATE))
        self.assertEqual((state["rev"], state["pc_name"]), (1, "학교"))
        self.assertNotIn("가짜학생", json.dumps(state, ensure_ascii=False))
        sa.shutdown()                                             # 끄면 잠금을 풂
        self.assertTrue(json.loads(self.fake.by_name(cloudsync.LOCK))["released"])

        b, sb = self.pc("집")                                     # 새로 설치한 PC → 묻지 않고 받음
        self.assertEqual(sb.startup(), "ok")
        self.assertEqual(student_names(b.db), ["가짜학생"])
        b.db.replace_students([{"number": 1, "name": "가짜학생"}, {"number": 2, "name": "둘째"}])
        sb.shutdown()
        self.assertEqual(json.loads(self.fake.by_name(cloudsync.STATE))["rev"], 2)

        a2 = cloudsync.Sync(a.db, a.config)                       # 학교 PC 를 다시 켬 → 집에서 바꾼 것을 받음
        self.assertEqual(a2.startup(), "ok")
        self.assertEqual(student_names(a.db), ["가짜학생", "둘째"])
        self.assertTrue(list((a.config.data_dir / "cloud-backups").glob("*before-download.db")))   # 바꾸기 전 것 보관

    def test_other_pc_in_use_is_read_only_until_take_over(self):
        a, sa = self.pc("학교")
        a.db.replace_students([{"number": 1, "name": "학교에서"}])
        sa.startup()
        b, sb = self.pc("집")
        self.assertEqual(sb.startup(), "readonly")
        self.assertTrue(cloudsync.paused())
        self.assertIn("학교 에서 쓰는 중", sb.status()["message"])
        self.assertEqual(student_names(b.db), ["학교에서"])       # 읽기 전용이라도 최신은 보여 줌
        self.assertEqual(sb.take_over(), "ok")                    # 강제로 가져오기
        self.assertFalse(cloudsync.paused())
        b.db.replace_students([{"number": 1, "name": "집에서"}])
        sb.sync_now()
        sa.tick(time.time() + cloudsync.HEARTBEAT + 1)            # 학교 PC 는 다음 확인 때 읽기 전용
        self.assertEqual(sa.mode, "readonly")
        self.assertEqual(student_names(a.db), ["집에서"])

    def test_idle_or_dead_holder_lock_is_taken(self):
        a, sa = self.pc("학교")
        sa.startup()
        lock = json.loads(self.fake.by_name(cloudsync.LOCK))
        lock["active_ts"] -= cloudsync.IDLE + 60                   # 켜 두고 30분 넘게 안 씀
        sa._refresh()
        sa._write_json(cloudsync.LOCK, lock)
        b, sb = self.pc("집")
        self.assertEqual(sb.startup(), "ok")

    def test_conflict_when_both_changed(self):
        a, sa = self.pc("학교")
        a.db.replace_students([{"number": 1, "name": "처음"}])
        sa.startup()
        sa.shutdown()
        b, sb = self.pc("집")
        sb.startup()
        b.db.replace_students([{"number": 1, "name": "집 변경"}])
        sb.shutdown()
        # 학교 PC 는 지난번에 올리지 못한 변경이 있음 (꺼질 때 인터넷 끊김)
        a.db.replace_students([{"number": 1, "name": "학교 변경"}])
        sa.state["dirty"] = True
        sa._save()
        a2 = cloudsync.Sync(a.db, a.config)
        self.assertEqual(a2.startup(), "conflict")
        self.assertTrue(cloudsync.paused())
        self.assertEqual(student_names(a.db), ["학교 변경"])     # 고르기 전에는 아무것도 안 바뀜
        a2.resolve("drive")
        self.assertEqual(student_names(a.db), ["집 변경"])
        self.assertTrue(list((a.config.data_dir / "cloud-backups").glob("*before-drive.db")))

    def test_conflict_keep_local_uploads_and_keeps_drive_copy(self):
        a, sa = self.pc("학교")
        a.db.replace_students([{"number": 1, "name": "학교"}])
        b, sb = self.pc("집")
        sb.startup()
        sb.shutdown()                                              # 집 PC 가 먼저 (비어 있는) 첫 판을 올림
        b.db.replace_students([{"number": 1, "name": "집"}])
        sb2 = cloudsync.Sync(b.db, b.config)
        sb2.startup()
        sb2.shutdown()
        self.assertEqual(sa.startup(), "conflict")                 # 처음 연결하는 PC 에 자료가 있음 → 묻기
        sa.resolve("local")
        self.assertEqual(sa.mode, "ok")
        self.assertTrue(list((a.config.data_dir / "cloud-backups").glob("*drive-before-local.enc")))
        sa.shutdown()
        sb3 = cloudsync.Sync(b.db, b.config)
        sb3.startup()
        self.assertEqual(student_names(b.db), ["학교"])

    def test_wrong_key_does_not_touch_local_data(self):
        a, sa = self.pc("학교")
        a.db.replace_students([{"number": 1, "name": "원래"}])
        sa.startup()
        sa.shutdown()
        b, sb = self.pc("집", key=cloudcrypt.new_recovery_key())
        b.db.replace_students([{"number": 1, "name": "집 자료"}])
        sb.state["base_rev"] = 0
        self.assertEqual(sb.startup(), "error")
        self.assertIn("맞지 않거나", sb.status()["message"])
        self.assertEqual(student_names(b.db), ["집 자료"])
        with self.assertRaises(cloudcrypt.CryptError):            # 설정에서 키를 넣을 때도 먼저 확인
            sb.set_key(cloudcrypt.new_recovery_key())
        sb.set_key(KEY)
        self.assertEqual(b.config["gdrive_passphrase"], cloudcrypt.normalize(KEY))

    def test_offline_keeps_working_and_retries(self):
        a, sa = self.pc("학교")
        a.config.values.update(gdrive_api_base="http://127.0.0.1:9", gdrive_upload_base="http://127.0.0.1:9")
        self.assertEqual(sa.startup(), "error")
        self.assertFalse(cloudsync.paused())                       # 인터넷이 없어도 이 PC 에서 계속 씀
        a.db.replace_students([{"number": 1, "name": "오프라인"}])
        sa.shutdown()
        self.assertTrue(sa.state["dirty"])                         # 다음에 올림
        a.config.values.update(self.fake.config_values())
        a2 = cloudsync.Sync(a.db, a.config)
        self.assertEqual(a2.startup(), "ok")
        self.assertEqual(json.loads(self.fake.by_name(cloudsync.STATE))["rev"], 1)

    def test_server_error_and_lost_login(self):
        a, sa = self.pc("학교")
        self.fake.fail = "500"
        self.assertEqual(sa.startup(), "error")
        self.fake.fail = "auth"
        a2 = cloudsync.Sync(a.db, a.config)
        a2.startup()
        self.assertIn("다시 로그인", a2.status()["message"])

    def test_periodic_upload_only_when_changed(self):
        a, sa = self.pc("학교")
        sa.startup()
        now = time.time()
        sa.tick(now + cloudsync.UPLOAD_EVERY + 1)
        self.assertEqual(json.loads(self.fake.by_name(cloudsync.STATE))["rev"], 1)
        a.db.replace_students([{"number": 1, "name": "새 학생"}])
        sa.tick(now + 2 * cloudsync.UPLOAD_EVERY + 2)
        self.assertEqual(json.loads(self.fake.by_name(cloudsync.STATE))["rev"], 2)

    def test_newer_app_version_on_drive_is_not_downloaded(self):
        a, sa = self.pc("학교")
        sa.startup()
        sa.shutdown()
        st = json.loads(self.fake.by_name(cloudsync.STATE))
        st.update(rev=5, app_version="9.9.9")
        sa._write_json(cloudsync.STATE, st)
        b, sb = self.pc("집")
        self.assertEqual(sb.startup(), "error")
        self.assertIn("업데이트", sb.status()["message"])

    def test_off_when_not_ready(self):
        a, sa = self.pc("학교")
        a.config.values["gdrive_enabled"] = False
        self.assertEqual(sa.startup(), "off")
        a.config.values.update(gdrive_enabled=True, gdrive_passphrase="")
        self.assertEqual(sa.startup(), "off")
        self.assertIn("복구 키", sa.status()["message"])
        self.assertEqual(self.fake.calls, [])                      # 준비 안 됐으면 드라이브에 아무것도 안 보냄


class LoginTests(unittest.TestCase):
    def test_login_url_and_token(self):
        fake = FakeDrive()
        env = Env()
        try:
            env.config.values.update(fake.config_values())
            login = gdrive.Login(env.config, 8770)
            url = login.url()
            self.assertIn("scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive.appdata", url)
            self.assertIn("code_challenge_method=S256", url)
            self.assertIn("redirect_uri=http%3A%2F%2F127.0.0.1%3A8770%2Foauth%2Fgoogle", url)
            fake.codes["C1"] = True
            self.assertEqual(login.finish("C1"), "RT")
            with self.assertRaises(gdrive.DriveError):
                login.finish("BAD")
        finally:
            env.close()
            fake.close()


if __name__ == "__main__":
    unittest.main()


from tests import test_server  # noqa: E402  (ServerTests 를 이름으로 가져오면 그 시험이 여기서 또 돔)


class CloudServerTests(test_server.ServerTests):
    def test_read_only_blocks_writes_but_not_cloud(self):
        cloudsync._paused.set()
        try:
            st, r = self.req("POST", "/api/todos", {"text": "읽기 전용에서 쓰기"})
            self.assertEqual(st, 423)
            self.assertIn("읽기 전용", r["error"])
            self.assertEqual(self.req("GET", "/api/state")[0], 200)           # 보기는 됨
            self.assertEqual(self.req("GET", "/api/cloud")[0], 200)
            self.assertEqual(self.req("POST", "/api/cloud", {"action": "newkey"})[0], 200)
            self.assertEqual(self.req("PUT", "/api/settings", {"gdrive_pc_name": "집"})[0], 200)
        finally:
            cloudsync._paused.clear()
        self.assertEqual(self.req("POST", "/api/todos", {"text": "다시 됨"})[0], 200)

    def test_google_login_return_and_no_secret_leak(self):
        fake = FakeDrive()
        try:
            self.env.config.values.update(fake.config_values())
            st, r = self.req("POST", "/api/cloud", {"action": "login"})
            self.assertEqual(st, 200)
            state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r["url"]).query))["state"]
            st, page = self.req("GET", "/oauth/google?state=wrong&code=C1", token=False)
            self.assertIn("오래되었거나".encode(), page)
            self.assertEqual(self.env.config["gdrive_token"], "")
            fake.codes["C1"] = True
            st, page = self.req("GET", f"/oauth/google?state={state}&code=C1", token=False)
            self.assertIn("로그인이 끝났습니다".encode(), page)
            self.assertEqual(self.env.config["gdrive_token"], "RT")
            st, page = self.req("GET", f"/oauth/google?state={state}&code=C1", token=False)   # 한 번만 씀
            self.assertIn("오래되었거나".encode(), page)
            if HAS_CRYPTO:
                key = cloudcrypt.new_recovery_key()
                self.assertEqual(self.req("POST", "/api/cloud", {"action": "key", "passphrase": key})[0], 200)
                self.assertEqual(self.req("POST", "/api/cloud", {"action": "key", "passphrase": "짧음"})[0], 400)
            text = json.dumps(self.req("GET", "/api/state")[1], ensure_ascii=False)
            self.assertNotIn('"RT"', text)
            self.assertNotIn("gdrive_token\"", text)
            self.assertIn('"gdrive_token_set": true', text)
            if HAS_CRYPTO:
                self.assertNotIn(cloudcrypt.normalize(key), text)
            st, r = self.req("POST", "/api/cloud", {"action": "logout"})
            self.assertEqual((st, self.env.config["gdrive_token"], r["mode"]), (200, "", "off"))
            self.assertTrue(fake.revoked)
        finally:
            self.env.config.values.update(gdrive_token="", gdrive_passphrase="", gdrive_enabled=False)
            fake.close()


for _name in [n for n in dir(test_server.ServerTests) if n.startswith("test_")]:
    if getattr(CloudServerTests, _name) is getattr(test_server.ServerTests, _name):
        setattr(CloudServerTests, _name, None)
