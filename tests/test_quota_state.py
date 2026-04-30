import tempfile
import unittest
import json
from datetime import datetime, timezone
from pathlib import Path

from src.quota_state import QuotaHealthcheckState


class QuotaStateTests(unittest.TestCase):
    def test_alert_recovery_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quota.json"
            state = QuotaHealthcheckState(path)
            self.assertEqual(state.evaluate_alert_transition({"status": "ALERTING", "reasons": ["low"]})["action"], "alert")
            state.save()

            restarted = QuotaHealthcheckState(path)
            self.assertEqual(restarted.evaluate_alert_transition({"status": "ALERTING", "reasons": ["low"]})["action"], "none")
            self.assertEqual(restarted.evaluate_alert_transition({"status": "NORMAL", "reasons": []})["action"], "recovery")
            restarted.save()

            restarted_again = QuotaHealthcheckState(path)
            self.assertEqual(restarted_again.evaluate_alert_transition({"status": "NORMAL", "reasons": []})["action"], "none")

    def test_summary_once_per_local_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = QuotaHealthcheckState(Path(tmp) / "nested" / "quota.json")
            before = datetime(2026, 4, 25, 23, 59, tzinfo=timezone.utc)  # 07:59 Hong Kong
            at_slot = datetime(2026, 4, 26, 0, 0, tzinfo=timezone.utc)  # 08:00 Hong Kong
            self.assertFalse(state.should_send_summary(enabled=True, hour_local=8, timezone_name="Asia/Hong_Kong", now=before))
            self.assertTrue(state.should_send_summary(enabled=True, hour_local=8, timezone_name="Asia/Hong_Kong", now=at_slot))
            self.assertTrue(state.should_send_summary(enabled=True, hour_local=8, timezone_name="Asia/Hong_Kong", now=at_slot))
            state.commit_summary(hour_local=8, timezone_name="Asia/Hong_Kong", now=at_slot)
            self.assertFalse(state.should_send_summary(enabled=True, hour_local=8, timezone_name="Asia/Hong_Kong", now=at_slot))
            state.save()
            self.assertTrue((Path(tmp) / "nested" / "quota.json").exists())

    def test_broadcast_once_per_local_hour_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = QuotaHealthcheckState(Path(tmp) / "quota.json")
            before = datetime(2026, 4, 25, 23, 59, tzinfo=timezone.utc)  # 07:59 Hong Kong
            slot_8 = datetime(2026, 4, 26, 0, 30, tzinfo=timezone.utc)  # 08:30 Hong Kong
            slot_12 = datetime(2026, 4, 26, 4, 0, tzinfo=timezone.utc)  # 12:00 Hong Kong

            self.assertFalse(state.should_send_broadcast(
                enabled=True,
                hours_local=(8, 12),
                timezone_name="Asia/Hong_Kong",
                now=before,
            ))
            self.assertTrue(state.should_send_broadcast(
                enabled=True,
                hours_local=(8, 12),
                timezone_name="Asia/Hong_Kong",
                now=slot_8,
            ))
            state.commit_broadcast(timezone_name="Asia/Hong_Kong", now=slot_8)
            self.assertFalse(state.should_send_broadcast(
                enabled=True,
                hours_local=(8, 12),
                timezone_name="Asia/Hong_Kong",
                now=slot_8,
            ))
            self.assertTrue(state.should_send_broadcast(
                enabled=True,
                hours_local=(8, 12),
                timezone_name="Asia/Hong_Kong",
                now=slot_12,
            ))

    def test_corrupted_state_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quota.json"
            path.write_text("not json", encoding="utf-8")
            state = QuotaHealthcheckState(path)
            self.assertEqual(state.state["alert_state"]["status"], "NORMAL")

    def test_old_summary_state_migrates_to_broadcast_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quota.json"
            path.write_text(json.dumps({
                "summary_state": {
                    "last_summary_date": "2026-04-26",
                    "last_summary_hour": 8,
                }
            }), encoding="utf-8")

            state = QuotaHealthcheckState(path)

            self.assertEqual(state.state["broadcast_state"]["last_broadcast_date"], "2026-04-26")
            self.assertEqual(state.state["broadcast_state"]["last_broadcast_hour"], 8)


if __name__ == "__main__":
    unittest.main()
