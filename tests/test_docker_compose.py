import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class DockerComposeTests(unittest.TestCase):
    def test_compose_exposes_runtime_toggles(self):
        compose_text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("CPA_ENABLE_REFRESH:", compose_text)
        self.assertIn("CPA_ENABLE_REFRESH: ${CPA_ENABLE_REFRESH:-true}", compose_text)
        self.assertIn("CPA_WORKER_THREADS:", compose_text)


    def test_compose_persists_runtime_and_exposes_quota_settings(self):
        compose_text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("./runtime:/app/runtime", compose_text)
        self.assertIn("CPA_QUOTA_REPORT_ENABLED:", compose_text)
        self.assertIn("CPA_QUOTA_STATE_FILE:", compose_text)
        self.assertIn("CPA_SERVER_NAME:", compose_text)
        self.assertIn("CPA_STATUS_BROADCAST_ENABLED:", compose_text)
        self.assertIn("CPA_STATUS_BROADCAST_HOURS_LOCAL:", compose_text)
        self.assertIn("CPA_STATUS_BROADCAST_TIMEZONE:", compose_text)
        self.assertNotIn("NEWAPI_HEALTHCHECK_ENABLED", compose_text)
        self.assertNotIn("AIGOCODE_BALANCE_ALERT_ENABLED", compose_text)


class DockerIgnoreTests(unittest.TestCase):
    def test_dockerignore_excludes_runtime_state(self):
        dockerignore_text = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")

        self.assertIn("runtime/", dockerignore_text.splitlines())


class ScheduledHealthcheckScriptTests(unittest.TestCase):
    def test_quota_test_modes_match_cli_choices(self):
        script_text = (REPO_ROOT / "scripts" / "scheduled_healthcheck.ps1").read_text(encoding="utf-8")

        for mode in ("summary", "broadcast", "alert", "recovery", "deleted", "disabled"):
            self.assertIn(f"'{mode}'", script_text)
