import unittest

from src.models import TokenQuota, UsageInfo
from src.openai_client import parse_usage_info
from src.quota_report import QuotaThresholds, aggregate, evaluate_alert, snapshot_from_usage, build_daily_summary_lines


class QuotaReportTests(unittest.TestCase):
    def test_aggregate_mixed_plus_free_rows(self):
        rows = [
            snapshot_from_usage(
                name="plus-ok",
                email="p@example.com",
                usage=UsageInfo(
                    plan_type="plus",
                    primary_window=TokenQuota(used_percent=20, reset_at=2000),
                    secondary_window=TokenQuota(used_percent=40, reset_at=3000),
                ),
            ),
            snapshot_from_usage(
                name="plus-5h-empty",
                email="p2@example.com",
                usage=UsageInfo(
                    plan_type="plus",
                    primary_window=TokenQuota(used_percent=100, reset_at=1000),
                    secondary_window=TokenQuota(used_percent=10, reset_at=4000),
                ),
            ),
            snapshot_from_usage(
                name="free-ok",
                email="f@example.com",
                usage=UsageInfo(
                    plan_type="free",
                    primary_window=TokenQuota(used_percent=50, reset_at=5000),
                    secondary_window=None,
                ),
            ),
        ]

        agg = aggregate(rows)

        self.assertEqual(agg["overall"]["total_auth_count"], 3)
        self.assertEqual(agg["overall"]["broad_usable_auth_count"], 3)
        self.assertEqual(agg["plus"]["total_auth_count"], 2)
        self.assertEqual(agg["plus"]["effective_usable_auth_count"], 1)
        self.assertEqual(agg["plus"]["exhausted_5h_but_7d_available_auth_count"], 1)
        self.assertEqual(agg["plus"]["avg_remaining_5h_percent"], 40.0)
        self.assertEqual(agg["plus"]["avg_remaining_7d_percent"], 75.0)
        self.assertEqual(agg["free"]["effective_usable_auth_count"], 1)
        self.assertIn("总 auth: 3", build_daily_summary_lines(agg))

    def test_alert_thresholds_are_strictly_less_than(self):
        agg = {
            "plus": {
                "effective_usable_auth_count": 10,
                "avg_remaining_5h_percent": 30,
                "avg_remaining_7d_percent": 30,
            }
        }
        self.assertEqual(evaluate_alert(agg, QuotaThresholds(10, 30, 30))["status"], "NORMAL")
        agg["plus"]["effective_usable_auth_count"] = 9
        result = evaluate_alert(agg, QuotaThresholds(10, 30, 30))
        self.assertEqual(result["status"], "ALERTING")
        self.assertIn("plus_effective_usable_auth_count=9 < 10", result["reasons"])

    def test_unknown_does_not_poison_plus_free_counts(self):
        agg = aggregate([{"name": "bad", "account_type": "unknown", "broad_usable": False, "effective_usable": False}])
        self.assertEqual(agg["unknown"]["total_auth_count"], 1)
        self.assertEqual(agg["plus"]["total_auth_count"], 0)
        self.assertEqual(agg["free"]["total_auth_count"], 0)

    def test_malformed_usage_payload_is_unknown_not_free_available(self):
        for payload in [
            {"json": {"unexpected": "shape"}},
            {"plan_type": "free"},
            {"plan_type": "free", "rate_limit": {}},
            {"plan_type": "free", "rate_limit": {"primary_window": None}},
            {"plan_type": "plus"},
        ]:
            with self.subTest(payload=payload):
                usage = parse_usage_info(payload)

                row = snapshot_from_usage(name="malformed", email=None, usage=usage)

                self.assertEqual(row["account_type"], "unknown")
                self.assertFalse(row["effective_usable"])


if __name__ == "__main__":
    unittest.main()
