import unittest
from unittest.mock import Mock

from src.notifier import FeishuNotifier
from src.settings import Settings


class NotifierMessageTests(unittest.TestCase):
    def notifier(self):
        notifier = FeishuNotifier(Settings(cpa_endpoint="https://example.com", cpa_token="secret"))
        notifier.send = Mock(return_value=True)
        return notifier

    def test_disabled_notification_lists_only_email(self):
        notifier = self.notifier()

        ok = notifier.notify_disabled_accounts([
            {"name": "token-a.json", "email": "a@example.com", "reason": "Week额度 100%"},
        ])

        self.assertTrue(ok)
        notifier.send.assert_called_once()
        _title, lines = notifier.send.call_args.args
        body = "\n".join(lines)
        self.assertIn("- a@example.com", body)
        self.assertNotIn("token-a.json", body)
        self.assertNotIn("Week额度 100%", body)

    def test_deleted_notification_keeps_reason_and_status(self):
        notifier = self.notifier()

        notifier.notify_deleted_accounts([
            {"name": "token-b.json", "email": "b@example.com", "reason": "invalid token", "status_code": 401},
        ])

        _title, lines = notifier.send.call_args.args
        body = "\n".join(lines)
        self.assertIn("invalid token", body)
        self.assertIn("状态码 401", body)

    def test_status_broadcast_uses_email_for_disabled_summary(self):
        notifier = self.notifier()

        notifier.notify_status_broadcast(
            {
                "total": 1,
                "alive": 1,
                "dead": 0,
                "disabled": 1,
                "enabled": 0,
                "refreshed": 0,
                "skipped": 0,
                "network_error": 0,
            },
            {"disabled": [{"name": "token-a.json", "email": "a@example.com"}]},
            None,
        )

        _title, lines = notifier.send.call_args.args
        body = "\n".join(lines)
        self.assertIn("禁用名单: a@example.com", body)
        self.assertNotIn("禁用名单: token-a.json", body)


if __name__ == "__main__":
    unittest.main()
