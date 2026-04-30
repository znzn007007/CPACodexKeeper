import os
import pathlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.notifier import FeishuNotifier
from src.settings import SettingsError, load_settings


class SettingsTests(unittest.TestCase):
    def _make_env_file(self, content: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        env_path = Path(temp_dir.name) / ".env"
        env_path.write_text(content, encoding="utf-8")
        return env_path

    def test_load_settings_reads_required_values(self):
        with patch.dict(os.environ, {"CPA_ENDPOINT": "https://example.com", "CPA_TOKEN": "secret"}, clear=True):
            settings = load_settings()
        self.assertEqual(settings.cpa_endpoint, "https://example.com")
        self.assertEqual(settings.cpa_token, "secret")
        self.assertEqual(settings.interval_seconds, 1800)
        self.assertEqual(settings.worker_threads, 8)
        self.assertTrue(settings.enable_refresh)
        self.assertEqual(settings.notify_daily_summary_hours_utc, (0, 3, 6, 9, 12, 15))
        self.assertEqual(settings.server_name, "cpacodexkeeper")
        self.assertTrue(settings.status_broadcast_enabled)
        self.assertEqual(settings.status_broadcast_hours_local, (8, 12, 18, 23))
        self.assertEqual(settings.status_broadcast_timezone, "Asia/Hong_Kong")

    def test_load_settings_reads_from_project_env_file(self):
        env_file = self._make_env_file("CPA_ENDPOINT=https://env-file.example.com\nCPA_TOKEN=file-secret\nCPA_INTERVAL=120\nCPA_WORKER_THREADS=6\n")
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings(env_file=env_file)
        self.assertEqual(settings.cpa_endpoint, "https://env-file.example.com")
        self.assertEqual(settings.cpa_token, "file-secret")
        self.assertEqual(settings.interval_seconds, 120)
        self.assertEqual(settings.worker_threads, 6)

    def test_environment_variables_override_project_env_file(self):
        env_file = self._make_env_file("CPA_ENDPOINT=https://env-file.example.com\nCPA_TOKEN=file-secret\nCPA_WORKER_THREADS=4\n")
        with patch.dict(os.environ, {"CPA_ENDPOINT": "https://shell.example.com", "CPA_TOKEN": "shell-secret", "CPA_WORKER_THREADS": "12"}, clear=True):
            settings = load_settings(env_file=env_file)
        self.assertEqual(settings.cpa_endpoint, "https://shell.example.com")
        self.assertEqual(settings.cpa_token, "shell-secret")
        self.assertEqual(settings.worker_threads, 12)

    def test_load_settings_reads_feishu_notification_values(self):
        env_file = self._make_env_file(
            "\n".join([
                "CPA_ENDPOINT=https://example.com",
                "CPA_TOKEN=secret",
                "FEISHU_WEBHOOK_URL=https://open.feishu.cn/robot",
                "FEISHU_SECURITY_MODE=secret",
                "FEISHU_SECRET=abc",
                "FEISHU_NOTIFY_DAILY_SUMMARY_HOURS_UTC=0,6,12,18",
                "FEISHU_NOTIFY_FAILURE_THRESHOLD=4",
            ])
        )
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings(env_file=env_file)
        self.assertEqual(settings.feishu_webhook_url, "https://open.feishu.cn/robot")
        self.assertEqual(settings.feishu_security_mode, "secret")
        self.assertEqual(settings.feishu_secret, "abc")
        self.assertEqual(settings.notify_daily_summary_hours_utc, (0, 6, 12, 18))
        self.assertEqual(settings.notify_failure_threshold, 4)

    def test_load_settings_rejects_missing_endpoint(self):
        env_file = Path("does-not-exist.env")
        with patch.dict(os.environ, {"CPA_TOKEN": "secret"}, clear=True):
            with self.assertRaises(SettingsError):
                load_settings(env_file=env_file)

    def test_load_settings_rejects_bad_integer(self):
        env_file = Path("does-not-exist.env")
        with patch.dict(os.environ, {"CPA_ENDPOINT": "https://example.com", "CPA_TOKEN": "secret", "CPA_INTERVAL": "abc"}, clear=True):
            with self.assertRaises(SettingsError):
                load_settings(env_file=env_file)

    def test_load_settings_rejects_non_integer_worker_threads(self):
        env_file = Path("does-not-exist.env")
        with patch.dict(os.environ, {"CPA_ENDPOINT": "https://example.com", "CPA_TOKEN": "secret", "CPA_WORKER_THREADS": "abc"}, clear=True):
            with self.assertRaises(SettingsError):
                load_settings(env_file=env_file)

    def test_load_settings_rejects_zero_worker_threads(self):
        env_file = Path("does-not-exist.env")
        with patch.dict(os.environ, {"CPA_ENDPOINT": "https://example.com", "CPA_TOKEN": "secret", "CPA_WORKER_THREADS": "0"}, clear=True):
            with self.assertRaises(SettingsError):
                load_settings(env_file=env_file)

    def test_load_settings_rejects_secret_mode_without_secret(self):
        env_file = Path("does-not-exist.env")
        with patch.dict(
            os.environ,
            {
                "CPA_ENDPOINT": "https://example.com",
                "CPA_TOKEN": "secret",
                "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/robot",
                "FEISHU_SECURITY_MODE": "secret",
            },
            clear=True,
        ):
            with self.assertRaises(SettingsError):
                load_settings(env_file=env_file)


    def test_load_settings_reads_quota_defaults(self):
        env_file = Path("does-not-exist.env")
        with patch.dict(os.environ, {"CPA_ENDPOINT": "https://example.com", "CPA_TOKEN": "secret"}, clear=True):
            settings = load_settings(env_file=env_file)
        self.assertTrue(settings.quota_report_enabled)
        self.assertTrue(settings.quota_alert_enabled)
        self.assertTrue(settings.quota_summary_enabled)
        self.assertEqual(settings.quota_plus_effective_usable_lt, 10)
        self.assertEqual(settings.quota_plus_avg_remaining_5h_percent_lt, 30)
        self.assertEqual(settings.quota_plus_avg_remaining_7d_percent_lt, 30)
        self.assertEqual(settings.quota_summary_hour_local, 8)
        self.assertEqual(settings.quota_timezone, "Asia/Hong_Kong")
        self.assertEqual(settings.quota_state_file, "./runtime/quota_healthcheck_state.json")

    def test_load_settings_reads_quota_overrides(self):
        env_file = self._make_env_file("\n".join([
            "CPA_ENDPOINT=https://example.com",
            "CPA_TOKEN=secret",
            "CPA_QUOTA_REPORT_ENABLED=false",
            "CPA_QUOTA_ALERT_ENABLED=false",
            "CPA_QUOTA_SUMMARY_ENABLED=false",
            "CPA_QUOTA_PLUS_EFFECTIVE_USABLE_LT=5",
            "CPA_QUOTA_PLUS_AVG_REMAINING_5H_PERCENT_LT=20",
            "CPA_QUOTA_PLUS_AVG_REMAINING_7D_PERCENT_LT=25",
            "CPA_QUOTA_SUMMARY_HOUR_LOCAL=9",
            "CPA_QUOTA_TIMEZONE=UTC",
            "CPA_QUOTA_STATE_FILE=./runtime/test-quota.json",
        ]))
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings(env_file=env_file)
        self.assertFalse(settings.quota_report_enabled)
        self.assertFalse(settings.quota_alert_enabled)
        self.assertFalse(settings.quota_summary_enabled)
        self.assertEqual(settings.quota_plus_effective_usable_lt, 5)
        self.assertEqual(settings.quota_plus_avg_remaining_5h_percent_lt, 20)
        self.assertEqual(settings.quota_plus_avg_remaining_7d_percent_lt, 25)
        self.assertEqual(settings.quota_summary_hour_local, 9)
        self.assertEqual(settings.quota_timezone, "UTC")
        self.assertEqual(settings.quota_state_file, "./runtime/test-quota.json")

    def test_load_settings_rejects_bad_quota_percent(self):
        env_file = Path("does-not-exist.env")
        with patch.dict(os.environ, {
            "CPA_ENDPOINT": "https://example.com",
            "CPA_TOKEN": "secret",
            "CPA_QUOTA_PLUS_AVG_REMAINING_5H_PERCENT_LT": "101",
        }, clear=True):
            with self.assertRaises(SettingsError):
                load_settings(env_file=env_file)

    def test_load_settings_reads_status_broadcast_overrides(self):
        env_file = self._make_env_file("\n".join([
            "CPA_ENDPOINT=https://example.com",
            "CPA_TOKEN=secret",
            "CPA_SERVER_NAME=sub2api-prod",
            "CPA_STATUS_BROADCAST_ENABLED=true",
            "CPA_STATUS_BROADCAST_HOURS_LOCAL=23,8,12,8",
            "CPA_STATUS_BROADCAST_TIMEZONE=Asia/Shanghai",
        ]))
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings(env_file=env_file)
        self.assertEqual(settings.server_name, "sub2api-prod")
        self.assertEqual(settings.status_broadcast_hours_local, (8, 12, 23))
        self.assertEqual(settings.status_broadcast_timezone, "Asia/Shanghai")

    def test_load_settings_rejects_bad_status_broadcast_hour(self):
        env_file = self._make_env_file("\n".join([
            "CPA_ENDPOINT=https://example.com",
            "CPA_TOKEN=secret",
            "CPA_STATUS_BROADCAST_HOURS_LOCAL=8,24",
        ]))
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SettingsError):
                load_settings(env_file=env_file)

    def test_notifier_titles_include_server_name(self):
        env_file = self._make_env_file("\n".join([
            "CPA_ENDPOINT=https://example.com",
            "CPA_TOKEN=secret",
            "CPA_SERVER_NAME=sub2api-prod",
            "FEISHU_WEBHOOK_URL=https://open.feishu.cn/robot",
        ]))
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings(env_file=env_file)
        payload = FeishuNotifier(settings)._build_payload("CPA Codex 定时播报", ["ok"])
        self.assertIn("[sub2api-prod] CPA Codex 定时播报", payload["content"]["text"])

    def test_dry_run_notifier_titles_are_explicit(self):
        env_file = self._make_env_file("\n".join([
            "CPA_ENDPOINT=https://example.com",
            "CPA_TOKEN=secret",
            "CPA_SERVER_NAME=sub2api-prod",
            "FEISHU_WEBHOOK_URL=https://open.feishu.cn/robot",
        ]))
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings(env_file=env_file)
        payload = FeishuNotifier(settings, dry_run=True)._build_payload("CPA Codex 定时播报", ["ok"])
        self.assertIn("[sub2api-prod] [DRY-RUN] CPA Codex 定时播报", payload["content"]["text"])
