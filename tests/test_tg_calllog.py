"""텔레그램으로 통화·문자 기록 파일 받기 (0.5.84, 사용자 결정 2026-11-10 예외):
기본 끔, 연결된 선생님 채팅만, 메모리에서만 읽고 처리 뒤 메시지 삭제, 답장은 건수만, 초안은 PC 에서 확인해야 저장·14일 뒤 삭제."""
import unittest
from datetime import datetime, timedelta

from desk import calllog
from desk.telegram import Bot
from tests.fake_telegram import FakeTelegram
from tests.test_collect import Env
from tests.test_telegram_more import CHAT

NOW = datetime.now().replace(second=0, microsecond=0)
MS = lambda dt: str(int(dt.timestamp() * 1000))
CALLS = f"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<calls count="3">
  <call number="010-3333-0007" duration="95" date="{MS(NOW - timedelta(days=1))}" type="1" contact_name="가짜7 어머니" />
  <call number="010-9999-1234" duration="300" date="{MS(NOW - timedelta(days=1))}" type="2" contact_name="모르는 사람" />
  <call number="010-3333-0007" duration="40" date="{MS(NOW - timedelta(days=200))}" type="2" />
</calls>""".encode("utf-8")
SMS = f"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<smses count="1"><sms address="010-3333-0007" date="{MS(NOW - timedelta(days=2))}" type="1" body="가짜7 오늘 결석합니다 비밀내용" /></smses>""".encode("utf-8")


class TelegramCallLogTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fake = FakeTelegram()
        self.env.config.values.update(telegram_token="TEST:TOKEN", telegram_api_base=self.fake.base)
        self.bot = Bot(self.env.db, self.env.config, self.env.collector)
        self.bot.sync = True
        self.env.db.set_meta("telegram_chat_id", CHAT)
        self.env.db.replace_students([{"number": n, "name": f"가짜{n}", "parent_phone": f"010-3333-{n:04d}"} for n in range(1, 11)])

    def tearDown(self):
        self.fake.close()
        self.env.close()

    def send_file(self, data, chat=CHAT, chat_type="private", msg_id=77, fid="f1", size=None):
        self.fake.files[fid] = data
        doc = {"file_id": fid, "file_name": "가짜7_통화.xml", "file_size": size if size is not None else len(data)}
        return self.bot.handle_update({"update_id": 1, "message": {
            "message_id": msg_id, "chat": {"id": chat, "type": chat_type}, "document": doc}})

    def texts(self):
        return [m.get("text", "") for m in self.fake.sent]

    def test_off_by_default(self):
        self.assertFalse(self.env.config["telegram_calllog"])
        reply = self.send_file(CALLS)
        self.assertIn("받지 않았습니다", reply)
        self.assertNotIn("getFile", self.fake.calls)          # 내려받지도 않음
        self.assertEqual(calllog.pending_state(self.env.db)["drafts"], [])

    def test_other_chats_ignored(self):
        self.env.config.values["telegram_calllog"] = True
        self.assertIsNone(self.send_file(CALLS, chat=999))
        self.assertIsNone(self.send_file(CALLS, chat_type="group"))
        self.assertEqual((self.fake.sent, self.fake.calls), ([], []))

    def test_counts_only_delete_and_pending(self):
        self.env.config.values["telegram_calllog"] = True
        reply = self.send_file(CALLS)
        self.assertIn("초안 1건", reply)
        self.assertIn("버림 1건", reply)
        self.assertIn("오래된 것 1건", reply)
        self.send_file(SMS, msg_id=78, fid="f2")
        sent = " ".join(self.texts())
        for secret in ("가짜7", "010-3333", "01033330007", "9999", "모르는 사람", "비밀내용", "결석"):
            self.assertNotIn(secret, sent)                        # 답장에 이름·번호·내용 없음
        self.assertEqual([d["message_id"] for d in self.fake.deleted], [77, 78])   # 파일 메시지를 지움
        p = calllog.pending_state(self.env.db)
        self.assertEqual(sorted(d["method"] for d in p["drafts"]), ["문자", "전화"])
        self.assertNotIn("9999", str(p))                          # 맞지 않는 번호는 저장도 안 함
        self.assertEqual(self.env.db.one("SELECT COUNT(*) AS n FROM class_counsel")["n"], 0)   # 바로 저장하지 않음
        logs = str(self.env.db.query("SELECT message FROM logs"))
        self.assertNotIn("가짜7", logs)
        self.assertNotIn("010-3333", logs)
        # 같은 파일을 또 보내면 초안이 겹치지 않음
        self.assertIn("초안 0건", self.send_file(CALLS, msg_id=79, fid="f3"))
        # PC 에서 저장하면 대기 목록에서 빠짐
        calllog.save_drafts(self.env.db, {"items": [d for d in p["drafts"] if d["method"] == "전화"]})
        self.assertEqual([d["method"] for d in calllog.pending_state(self.env.db)["drafts"]], ["문자"])
        calllog.pending_drop(self.env.db)
        self.assertEqual(calllog.pending_state(self.env.db)["drafts"], [])

    def test_delete_failure_asks_teacher(self):
        self.env.config.values["telegram_calllog"] = True
        self.fake.fail_delete = True
        reply = self.send_file(CALLS)
        self.assertIn("직접 지워 주세요", reply)

    def test_bad_and_big_files(self):
        self.env.config.values["telegram_calllog"] = True
        bomb = '<?xml version="1.0"?><!DOCTYPE c [<!ENTITY a "xxxxxxxxxx">]><calls><call number="010-3333-0007" date="1"/></calls>'
        self.assertIn("읽지 못했습니다", self.send_file(bomb.encode("utf-16-le")))
        self.assertIn("너무 큽니다", self.send_file(b"<calls/>", size=calllog.MAX_TOTAL + 1, msg_id=80, fid="f9"))
        self.assertEqual(len(self.fake.deleted), 2)               # 못 읽은 파일도 지움

    def test_pending_expires(self):
        self.env.config.values["telegram_calllog"] = True
        self.send_file(CALLS)
        old = (datetime.now() - timedelta(days=calllog.PENDING_DAYS + 1)).strftime("%Y-%m-%d %H:%M:%S")
        self.env.db.execute("UPDATE class_call_drafts SET created_at = ?", (old,))
        self.assertEqual(calllog.pending_state(self.env.db)["drafts"], [])
        self.assertEqual(self.env.db.one("SELECT COUNT(*) AS n FROM class_call_drafts")["n"], 0)


if __name__ == "__main__":
    unittest.main()
