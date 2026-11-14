"""드라이브 백업·되살리기 (0.5.97): 날짜별 백업, 14일+월 1개 보관, 지금 백업, 되살리기."""
import json
import unittest
from datetime import date, timedelta

from desk import cloudcrypt, cloudsync, gdrive
from tests.fake_gdrive import FakeDrive
from tests.test_cloudsync import KEY, student_names
from tests.test_collect import Env


@unittest.skipUnless(cloudcrypt.available(), "cryptography 가 없는 환경")
class BackupTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDrive()
        self.envs = []

    def tearDown(self):
        for env in self.envs:
            env.close()
        self.fake.close()
        cloudsync._paused.clear()

    def pc(self, name, key=KEY):
        env = Env()
        self.envs.append(env)
        env.config.values.update(self.fake.config_values(), gdrive_enabled=True, gdrive_token="RT",
                                 gdrive_passphrase=key, gdrive_pc_name=name)
        return env, cloudsync.Sync(env.db, env.config)

    def backup_names(self):
        return sorted(f["name"] for f in self.fake.files.values() if f["name"].startswith("backup-"))

    def set_remote_day(self, sync, day):
        st = json.loads(self.fake.by_name(cloudsync.STATE))
        st["at"] = f"{day} 16:30"
        sync._write_json(cloudsync.STATE, st)
        sync.remote = st

    def test_first_upload_of_a_new_day_keeps_yesterday(self):
        a, sa = self.pc("학교")
        a.db.replace_students([{"number": 1, "name": "어제 자료"}])
        sa.startup()
        self.assertEqual(self.backup_names(), [])                     # 오늘 처음 올린 것은 아직 백업 아님
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.set_remote_day(sa, yesterday)
        old_blob = self.fake.by_name(cloudsync.DATA)
        a.db.replace_students([{"number": 1, "name": "오늘 자료"}])
        sa.sync_now()
        self.assertEqual(self.backup_names(), [f"backup-{yesterday}.enc"])
        self.assertEqual(self.fake.by_name(f"backup-{yesterday}.enc"), old_blob)   # 어제 마지막 모습 그대로(암호화된 채)
        sa.sync_now()                                                  # 같은 날 다시 올려도 더 안 만듦
        self.assertEqual(len(self.backup_names()), 1)

    def test_retention_14_days_and_one_per_month(self):
        a, sa = self.pc("학교")
        sa.startup()
        today = date(2026, 11, 14)
        days = [today - timedelta(days=i) for i in range(0, 60)] + [date(2025, 10, 3), date(2025, 9, 1)]
        for d in days:
            self.fake.files[f"id-{d}"] = {"name": f"backup-{d.isoformat()}.enc", "data": b"x"}
        sa.files = sa.drive.files()
        removed = sa.prune_backups(today)
        kept = set(self.backup_names())
        for i in range(14):                                            # 최근 14일은 모두
            self.assertIn(f"backup-{(today - timedelta(days=i)).isoformat()}.enc", kept)
        self.assertIn("backup-2026-10-31.enc", kept)                   # 10월은 가장 늦은 날 하나
        self.assertNotIn("backup-2026-10-30.enc", kept)
        self.assertIn("backup-2026-09-30.enc", kept)
        self.assertNotIn("backup-2025-10-03.enc", kept)                # 12달보다 오래된 것은 지움
        self.assertTrue(removed)
        self.assertEqual(len([k for k in kept if k.startswith("backup-2026-10")]), 1)

    def test_backup_now_and_restore_reach_other_pc(self):
        a, sa = self.pc("학교")
        a.db.replace_students([{"number": 1, "name": "되살릴 자료"}])
        sa.startup()
        name = sa.backup_now()
        self.assertEqual(name, f"backup-{date.today().isoformat()}.enc")
        a.db.replace_students([{"number": 1, "name": "실수로 바꾼 자료"}])
        sa.sync_now()
        sa.backup_now()                                                # 오늘 것은 바뀜 (하나만)
        self.assertEqual(self.backup_names(), [name])
        a.db.replace_students([{"number": 1, "name": "되살릴 자료"}])
        sa.sync_now()
        sa.backup_now()
        a.db.replace_students([{"number": 1, "name": "망가진 자료"}])
        sa.sync_now()
        before_rev = json.loads(self.fake.by_name(cloudsync.STATE))["rev"]
        listed = sa.backups()
        self.assertEqual([b["name"] for b in listed], [name])
        sa.restore(name)
        self.assertEqual(student_names(a.db), ["되살릴 자료"])
        self.assertGreater(json.loads(self.fake.by_name(cloudsync.STATE))["rev"], before_rev)   # 새 판으로 올림
        self.assertIn(f"backup-{date.today().isoformat()}-restore.enc", self.backup_names())    # 되살리기 직전 모습 보관
        self.assertTrue(list((a.config.data_dir / "cloud-backups").glob("*before-restore.db")))
        sa.shutdown()
        b, sb = self.pc("집")
        sb.startup()
        self.assertEqual(student_names(b.db), ["되살릴 자료"])

    def test_restore_needs_lock_and_valid_name_and_key(self):
        a, sa = self.pc("학교")
        sa.startup()
        sa.backup_now()
        b, sb = self.pc("집")
        self.assertEqual(sb.startup(), "readonly")
        with self.assertRaisesRegex(gdrive.DriveError, "이어 쓰는 중일 때만"):
            sb.restore(f"backup-{date.today().isoformat()}.enc")
        with self.assertRaises(gdrive.DriveError):
            sa.restore("../desk-lock.json")
        with self.assertRaises(gdrive.DriveError):
            sa.restore("backup-2000-01-01.enc")
        sa.config.values["gdrive_passphrase"] = cloudcrypt.new_recovery_key()   # 키가 다르면 풀지 못하고 자료는 그대로
        a.db.replace_students([{"number": 1, "name": "그대로"}])
        with self.assertRaises(cloudcrypt.CryptError):
            sa.restore(f"backup-{date.today().isoformat()}.enc")
        self.assertEqual(student_names(a.db), ["그대로"])



from tests import test_server  # noqa: E402


class BackupServerTests(test_server.ServerTests):
    @unittest.skipUnless(cloudcrypt.available(), "cryptography 가 없는 환경")
    def test_backup_actions(self):
        fake = FakeDrive()
        cfg = self.env.config
        try:
            cfg.values.update(fake.config_values(), gdrive_enabled=True, gdrive_token="RT", gdrive_passphrase=KEY)
            self.app.cloud.connect()
            self.assertEqual(self.app.cloud.mode, "ok")
            st, r = self.req("POST", "/api/cloud", {"action": "backup"})
            self.assertEqual(st, 200)
            st, r = self.req("POST", "/api/cloud", {"action": "backups"})
            self.assertEqual([b["date"] for b in r["backups"]], [date.today().isoformat()])
            self.assertNotIn(KEY, json.dumps(r))
            st, r = self.req("POST", "/api/cloud", {"action": "restore", "name": r["backups"][0]["name"]})
            self.assertEqual((st, r["mode"]), (200, "ok"))
            self.assertEqual(self.req("POST", "/api/cloud", {"action": "restore", "name": "nope"})[0], 400)
        finally:
            self.app.cloud.shutdown()
            cfg.values.update(gdrive_enabled=False, gdrive_token="", gdrive_passphrase="")
            self.app.cloud.mode = "off"
            cloudsync._paused.clear()
            fake.close()


for _name in [n for n in dir(test_server.ServerTests) if n.startswith("test_")]:
    if getattr(BackupServerTests, _name) is getattr(test_server.ServerTests, _name):
        setattr(BackupServerTests, _name, None)

if __name__ == "__main__":
    unittest.main()
