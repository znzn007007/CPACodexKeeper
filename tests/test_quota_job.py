import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.notifier import FeishuNotifier
from src.quota_job import QuotaHealthcheckJob, fake_aggregate, run_test_notification
from src.quota_report import error_snapshot
from src.settings import Settings


def plus_snapshot(name, *, remaining_5h, remaining_7d):
    return {
        "name": name,
        "email": f"{name}@example.com",
        "plan_type": "plus",
        "account_type": "plus",
        "broad_usable": remaining_7d > 0,
        "effective_usable": remaining_7d > 0 and remaining_5h > 0,
        "plus_effective_usable": remaining_7d > 0 and remaining_5h > 0,
        "free_effective_usable": False,
        "is_5h_empty": remaining_5h <= 0,
        "is_7d_empty": remaining_7d <= 0,
        "remaining_5h_percent": remaining_5h,
        "remaining_7d_percent": remaining_7d,
        "used_5h_percent": 100 - remaining_5h,
        "used_7d_percent": 100 - remaining_7d,
        "reset_5h_at": "2026-04-25T08:00:00+00:00",
        "reset_7d_at": "2026-04-26T08:00:00+00:00",
        "checked_at": "2026-04-25T00:00:00+00:00",
        "last_error": "",
    }


def alert_snapshots():
    return [plus_snapshot("plus-low", remaining_5h=5, remaining_7d=10)]


def normal_snapshots():
    return [plus_snapshot(f"plus-ok-{idx}", remaining_5h=80, remaining_7d=90) for idx in range(10)]


class FakeSender:
    enabled = True

    def __init__(self, *, ok=True):
        self.sent = []
        self.ok = ok

    def send(self, title, lines, *, dedupe_key=None, cooldown_minutes=None):
        self.sent.append((title, lines, dedupe_key, cooldown_minutes))
        return self.ok


class NotifyStateWritingSender(FakeSender):
    def __init__(self, notify_state_file):
        super().__init__()
        self.notify_state_file = Path(notify_state_file)

    def send(self, title, lines, *, dedupe_key=None, cooldown_minutes=None):
        if dedupe_key:
            self.notify_state_file.write_text('{"cooldowns":{"%s":"polluted"}}' % dedupe_key, encoding="utf-8")
        return super().send(title, lines, dedupe_key=dedupe_key, cooldown_minutes=cooldown_minutes)


class QuotaJobTests(unittest.TestCase):
    def settings(self, state_file):
        return Settings(cpa_endpoint="https://example.com", cpa_token="secret", quota_state_file=str(state_file))

    def test_job_uses_dedicated_quota_state_not_notify_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            notify_state = tmp_path / "notify_state.json"
            notify_state.write_text('{"last_daily_summary_date":"2026-01-01"}', encoding="utf-8")
            quota_state = tmp_path / "quota_healthcheck_state.json"
            settings = self.settings(quota_state)
            settings.notify_state_file = str(notify_state)
            sender = NotifyStateWritingSender(notify_state)
            job = QuotaHealthcheckJob(settings, sender)

            job.run(alert_snapshots())

            self.assertTrue(quota_state.exists())
            self.assertEqual(notify_state.read_text(encoding="utf-8"), '{"last_daily_summary_date":"2026-01-01"}')
            self.assertTrue(sender.sent)
            self.assertTrue(all(call[2] is None and call[3] is None for call in sender.sent))


    def test_failed_alert_send_does_not_advance_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            quota_state = Path(tmp) / "quota_healthcheck_state.json"
            sender = FakeSender(ok=False)
            job = QuotaHealthcheckJob(self.settings(quota_state), sender)

            job.run(alert_snapshots())

            restarted = QuotaHealthcheckJob(self.settings(quota_state), FakeSender())
            self.assertEqual(restarted.state.state["alert_state"]["status"], "NORMAL")

    def test_failed_summary_send_does_not_consume_day_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            quota_state = Path(tmp) / "quota_healthcheck_state.json"
            settings = self.settings(quota_state)
            settings.quota_summary_hour_local = 0
            settings.quota_alert_enabled = False
            sender = FakeSender(ok=False)
            job = QuotaHealthcheckJob(settings, sender)

            job.run(normal_snapshots())

            restarted = QuotaHealthcheckJob(settings, FakeSender())
            self.assertIsNone(restarted.state.state["summary_state"]["last_summary_date"])

    def test_job_no_longer_sends_standalone_quota_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            quota_state = Path(tmp) / "quota_healthcheck_state.json"
            settings = self.settings(quota_state)
            settings.quota_summary_hour_local = 0
            settings.quota_alert_enabled = False
            sender = FakeSender()

            agg = QuotaHealthcheckJob(settings, sender).run(normal_snapshots())

            self.assertIsNotNone(agg)
            self.assertEqual(sender.sent, [])

    def test_failed_recovery_send_keeps_alerting_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            quota_state = Path(tmp) / "quota_healthcheck_state.json"
            settings = self.settings(quota_state)
            settings.quota_summary_enabled = False

            QuotaHealthcheckJob(settings, FakeSender()).run(alert_snapshots())
            QuotaHealthcheckJob(settings, FakeSender(ok=False)).run(normal_snapshots())

            restarted = QuotaHealthcheckJob(settings, FakeSender())
            self.assertEqual(restarted.state.state["alert_state"]["status"], "ALERTING")


    def test_real_feishu_transport_does_not_create_notify_state_for_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            notify_state = tmp_path / "notify_state.json"
            quota_state = tmp_path / "quota_healthcheck_state.json"
            settings = self.settings(quota_state)
            settings.notify_state_file = str(notify_state)
            settings.feishu_webhook_url = "https://example.com/webhook"
            settings.quota_summary_enabled = False
            notifier = FeishuNotifier(settings)
            response = Mock()
            response.read.return_value = b"ok"
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=None)

            with patch("src.notifier.request.urlopen", return_value=response):
                QuotaHealthcheckJob(settings, notifier).run(alert_snapshots())

            self.assertTrue(quota_state.exists())
            self.assertFalse(notify_state.exists())

    def test_dry_run_does_not_persist_quota_or_notify_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            notify_state = tmp_path / "notify_state.json"
            quota_state = tmp_path / "quota_healthcheck_state.json"
            settings = self.settings(quota_state)
            settings.notify_state_file = str(notify_state)
            settings.feishu_webhook_url = "https://example.com/webhook"
            notifier = FeishuNotifier(settings, dry_run=True)

            QuotaHealthcheckJob(settings, notifier).run(alert_snapshots())
            notifier.send("deduped", ["line"], dedupe_key="dry-run")
            notifier.handle_failure_state({"network_error": 10})

            self.assertFalse(quota_state.exists())
            self.assertFalse(notify_state.exists())

    def test_unknown_quota_rows_skip_alert_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            quota_state = Path(tmp) / "quota_healthcheck_state.json"
            sender = FakeSender()
            job = QuotaHealthcheckJob(self.settings(quota_state), sender)

            agg = job.run([error_snapshot(name="network-failed", error="timeout")])

            self.assertIsNotNone(agg)
            self.assertEqual(sender.sent, [])
            restarted = QuotaHealthcheckJob(self.settings(quota_state), FakeSender())
            self.assertEqual(restarted.state.state["alert_state"]["status"], "NORMAL")

    def test_partial_quota_sample_with_unknown_rows_skips_alert_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            quota_state = Path(tmp) / "quota_healthcheck_state.json"
            sender = FakeSender()
            job = QuotaHealthcheckJob(self.settings(quota_state), sender)

            job.run(alert_snapshots() + [error_snapshot(name="network-failed", error="timeout")])

            self.assertEqual(sender.sent, [])
            restarted = QuotaHealthcheckJob(self.settings(quota_state), FakeSender())
            self.assertEqual(restarted.state.state["alert_state"]["status"], "NORMAL")

    def test_alerting_state_is_not_recovered_from_incomplete_quota_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            quota_state = Path(tmp) / "quota_healthcheck_state.json"
            settings = self.settings(quota_state)
            QuotaHealthcheckJob(settings, FakeSender()).run(alert_snapshots())

            sender = FakeSender()
            QuotaHealthcheckJob(settings, sender).run(normal_snapshots() + [error_snapshot(name="network-failed", error="timeout")])

            self.assertEqual(sender.sent, [])
            restarted = QuotaHealthcheckJob(settings, FakeSender())
            self.assertEqual(restarted.state.state["alert_state"]["status"], "ALERTING")

    def test_recovery_disabled_keeps_alerting_state_until_recovery_can_be_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            quota_state = Path(tmp) / "quota_healthcheck_state.json"
            settings = self.settings(quota_state)
            settings.quota_summary_enabled = False
            settings.notify_send_recovery = False

            QuotaHealthcheckJob(settings, FakeSender()).run(alert_snapshots())
            QuotaHealthcheckJob(settings, FakeSender()).run(normal_snapshots())

            restarted = QuotaHealthcheckJob(settings, FakeSender())
            self.assertEqual(restarted.state.state["alert_state"]["status"], "ALERTING")

    def test_controlled_test_notification_does_not_require_maintainer(self):
        sender = FakeSender()
        ok = run_test_notification(self.settings("./runtime/quota_healthcheck_state.test.json"), sender, "alert")
        self.assertTrue(ok)
        self.assertEqual(sender.sent[0][0], "[TEST] CPA Codex Plus 额度告警")

    def test_controlled_broadcast_deleted_disabled_notifications(self):
        for mode, expected_title in [
            ("broadcast", "[TEST] CPA Codex 定时播报"),
            ("deleted", "[TEST] CPA Codex 删除通知"),
            ("disabled", "[TEST] CPA Codex 禁用通知"),
        ]:
            with self.subTest(mode=mode):
                sender = FakeSender()
                ok = run_test_notification(self.settings("./runtime/quota_healthcheck_state.test.json"), sender, mode)
                self.assertTrue(ok)
                self.assertEqual(sender.sent[0][0], expected_title)


if __name__ == "__main__":
    unittest.main()
