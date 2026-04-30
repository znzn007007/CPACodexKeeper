import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_INTERVAL_SECONDS = 1800
DEFAULT_QUOTA_THRESHOLD = 100
DEFAULT_EXPIRY_THRESHOLD_DAYS = 3
DEFAULT_USAGE_TIMEOUT_SECONDS = 15
DEFAULT_CPA_TIMEOUT_SECONDS = 30
DEFAULT_MAX_RETRIES = 2
DEFAULT_WORKER_THREADS = 8
DEFAULT_ENABLE_REFRESH = True
DEFAULT_NOTIFY_COOLDOWN_MINUTES = 60
DEFAULT_NOTIFY_SEND_RECOVERY = True
DEFAULT_NOTIFY_SEND_DAILY_SUMMARY = True
DEFAULT_NOTIFY_FAILURE_THRESHOLD = 3
DEFAULT_NOTIFY_LARGE_SCALE_USAGE_FAILURE_THRESHOLD = 5
DEFAULT_NOTIFY_DAILY_SUMMARY_HOURS_UTC = (0, 3, 6, 9, 12, 15)
DEFAULT_SERVER_NAME = "cpacodexkeeper"
DEFAULT_STATUS_BROADCAST_ENABLED = True
DEFAULT_STATUS_BROADCAST_HOURS_LOCAL = (8, 12, 18, 23)
DEFAULT_STATUS_BROADCAST_TIMEZONE = "Asia/Hong_Kong"
DEFAULT_CPA_QUOTA_REPORT_ENABLED = True
DEFAULT_CPA_QUOTA_ALERT_ENABLED = True
DEFAULT_CPA_QUOTA_SUMMARY_ENABLED = True
DEFAULT_CPA_QUOTA_PLUS_EFFECTIVE_USABLE_LT = 10
DEFAULT_CPA_QUOTA_PLUS_AVG_REMAINING_5H_PERCENT_LT = 30
DEFAULT_CPA_QUOTA_PLUS_AVG_REMAINING_7D_PERCENT_LT = 30
DEFAULT_CPA_QUOTA_SUMMARY_HOUR_LOCAL = 8
DEFAULT_CPA_QUOTA_TIMEZONE = "Asia/Hong_Kong"
PROJECT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class SettingsError(ValueError):
    pass


@dataclass(slots=True)
class Settings:
    cpa_endpoint: str
    cpa_token: str
    proxy: str | None = None
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    quota_threshold: int = DEFAULT_QUOTA_THRESHOLD
    expiry_threshold_days: int = DEFAULT_EXPIRY_THRESHOLD_DAYS
    usage_timeout_seconds: int = DEFAULT_USAGE_TIMEOUT_SECONDS
    cpa_timeout_seconds: int = DEFAULT_CPA_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    worker_threads: int = DEFAULT_WORKER_THREADS
    enable_refresh: bool = DEFAULT_ENABLE_REFRESH
    feishu_webhook_url: str | None = None
    feishu_security_mode: str = "none"
    feishu_keyword: str | None = None
    feishu_secret: str | None = None
    notify_cooldown_minutes: int = DEFAULT_NOTIFY_COOLDOWN_MINUTES
    notify_send_recovery: bool = DEFAULT_NOTIFY_SEND_RECOVERY
    notify_send_daily_summary: bool = DEFAULT_NOTIFY_SEND_DAILY_SUMMARY
    notify_daily_summary_hours_utc: tuple[int, ...] = DEFAULT_NOTIFY_DAILY_SUMMARY_HOURS_UTC
    notify_failure_threshold: int = DEFAULT_NOTIFY_FAILURE_THRESHOLD
    notify_large_scale_usage_failure_threshold: int = DEFAULT_NOTIFY_LARGE_SCALE_USAGE_FAILURE_THRESHOLD
    notify_state_file: str = "./runtime/notify_state.json"
    server_name: str = DEFAULT_SERVER_NAME
    status_broadcast_enabled: bool = DEFAULT_STATUS_BROADCAST_ENABLED
    status_broadcast_hours_local: tuple[int, ...] = DEFAULT_STATUS_BROADCAST_HOURS_LOCAL
    status_broadcast_timezone: str = DEFAULT_STATUS_BROADCAST_TIMEZONE
    quota_report_enabled: bool = DEFAULT_CPA_QUOTA_REPORT_ENABLED
    quota_alert_enabled: bool = DEFAULT_CPA_QUOTA_ALERT_ENABLED
    quota_summary_enabled: bool = DEFAULT_CPA_QUOTA_SUMMARY_ENABLED
    quota_plus_effective_usable_lt: int = DEFAULT_CPA_QUOTA_PLUS_EFFECTIVE_USABLE_LT
    quota_plus_avg_remaining_5h_percent_lt: int = DEFAULT_CPA_QUOTA_PLUS_AVG_REMAINING_5H_PERCENT_LT
    quota_plus_avg_remaining_7d_percent_lt: int = DEFAULT_CPA_QUOTA_PLUS_AVG_REMAINING_7D_PERCENT_LT
    quota_summary_hour_local: int = DEFAULT_CPA_QUOTA_SUMMARY_HOUR_LOCAL
    quota_timezone: str = DEFAULT_CPA_QUOTA_TIMEZONE
    quota_state_file: str = "./runtime/quota_healthcheck_state.json"


def _read_project_env_file(env_file: Path | None = None) -> dict[str, str]:
    target = env_file or PROJECT_ENV_FILE
    if not target.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in target.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _get_config_value(name: str, env_values: dict[str, str]) -> str | None:
    env_value = os.getenv(name)
    if env_value not in (None, ""):
        return env_value
    return env_values.get(name)


def _has_config_value(name: str, env_values: dict[str, str]) -> bool:
    env_value = os.getenv(name)
    if env_value not in (None, ""):
        return True
    return env_values.get(name) not in (None, "")


def _read_int(name: str, default: int, env_values: dict[str, str], *, minimum: int = 0, maximum: int | None = None) -> int:
    raw = _get_config_value(name, env_values)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc
    if value < minimum:
        raise SettingsError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise SettingsError(f"{name} must be <= {maximum}")
    return value


def _read_bool(name: str, default: bool, env_values: dict[str, str]) -> bool:
    raw = _get_config_value(name, env_values)
    if raw in (None, ""):
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{name} must be a boolean")


def _read_csv_ints(
    name: str,
    default: tuple[int, ...],
    env_values: dict[str, str],
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> tuple[int, ...]:
    raw = _get_config_value(name, env_values)
    if raw in (None, ""):
        return default
    values: list[int] = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            value = int(item)
        except ValueError as exc:
            raise SettingsError(f"{name} must contain integers separated by commas") from exc
        if value < minimum:
            raise SettingsError(f"{name} values must be >= {minimum}")
        if maximum is not None and value > maximum:
            raise SettingsError(f"{name} values must be <= {maximum}")
        values.append(value)
    if not values:
        raise SettingsError(f"{name} must contain at least one integer")
    return tuple(sorted(set(values)))


def _legacy_utc_hours_to_east8_local(hours_utc: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(sorted({(hour + 8) % 24 for hour in hours_utc}))


def load_settings(env_file: Path | None = None) -> Settings:
    env_values = _read_project_env_file(env_file)
    endpoint = (_get_config_value("CPA_ENDPOINT", env_values) or "").strip().rstrip("/")
    token = (_get_config_value("CPA_TOKEN", env_values) or "").strip()
    proxy = (_get_config_value("CPA_PROXY", env_values) or "").strip() or None

    if not endpoint:
        raise SettingsError("CPA_ENDPOINT is required")
    if not token:
        raise SettingsError("CPA_TOKEN is required")
    if not endpoint.startswith(("http://", "https://")):
        raise SettingsError("CPA_ENDPOINT must start with http:// or https://")

    feishu_webhook_url = (_get_config_value("FEISHU_WEBHOOK_URL", env_values) or "").strip() or None
    feishu_security_mode = (_get_config_value("FEISHU_SECURITY_MODE", env_values) or "none").strip().lower()
    feishu_keyword = (_get_config_value("FEISHU_KEYWORD", env_values) or "").strip() or None
    feishu_secret = (_get_config_value("FEISHU_SECRET", env_values) or "").strip() or None
    notify_state_file = (_get_config_value("FEISHU_NOTIFY_STATE_FILE", env_values) or "./runtime/notify_state.json").strip()
    quota_state_file = (_get_config_value("CPA_QUOTA_STATE_FILE", env_values) or "./runtime/quota_healthcheck_state.json").strip()
    quota_timezone = (_get_config_value("CPA_QUOTA_TIMEZONE", env_values) or DEFAULT_CPA_QUOTA_TIMEZONE).strip()
    server_name = (_get_config_value("CPA_SERVER_NAME", env_values) or DEFAULT_SERVER_NAME).strip()
    status_broadcast_timezone = (
        _get_config_value("CPA_STATUS_BROADCAST_TIMEZONE", env_values)
        or _get_config_value("CPA_QUOTA_TIMEZONE", env_values)
        or DEFAULT_STATUS_BROADCAST_TIMEZONE
    ).strip()
    if not server_name:
        raise SettingsError("CPA_SERVER_NAME must not be empty")
    if not status_broadcast_timezone:
        raise SettingsError("CPA_STATUS_BROADCAST_TIMEZONE must not be empty")
    if not quota_timezone:
        raise SettingsError("CPA_QUOTA_TIMEZONE must not be empty")

    if feishu_security_mode not in {"none", "keyword", "secret"}:
        raise SettingsError("FEISHU_SECURITY_MODE must be one of: none, keyword, secret")
    if feishu_webhook_url and feishu_security_mode == "keyword" and not feishu_keyword:
        raise SettingsError("FEISHU_KEYWORD is required when FEISHU_SECURITY_MODE=keyword")
    if feishu_webhook_url and feishu_security_mode == "secret" and not feishu_secret:
        raise SettingsError("FEISHU_SECRET is required when FEISHU_SECURITY_MODE=secret")

    legacy_summary_hours = _read_csv_ints(
        "FEISHU_NOTIFY_DAILY_SUMMARY_HOURS_UTC",
        DEFAULT_NOTIFY_DAILY_SUMMARY_HOURS_UTC,
        env_values,
        minimum=0,
        maximum=23,
    )
    if _has_config_value("CPA_STATUS_BROADCAST_HOURS_LOCAL", env_values):
        status_broadcast_hours = _read_csv_ints(
            "CPA_STATUS_BROADCAST_HOURS_LOCAL",
            DEFAULT_STATUS_BROADCAST_HOURS_LOCAL,
            env_values,
            minimum=0,
            maximum=23,
        )
    elif _has_config_value("FEISHU_NOTIFY_DAILY_SUMMARY_HOURS_UTC", env_values):
        status_broadcast_hours = _legacy_utc_hours_to_east8_local(legacy_summary_hours)
    else:
        status_broadcast_hours = DEFAULT_STATUS_BROADCAST_HOURS_LOCAL

    if _has_config_value("CPA_STATUS_BROADCAST_ENABLED", env_values):
        status_broadcast_enabled = _read_bool(
            "CPA_STATUS_BROADCAST_ENABLED",
            DEFAULT_STATUS_BROADCAST_ENABLED,
            env_values,
        )
    elif _has_config_value("FEISHU_NOTIFY_SEND_DAILY_SUMMARY", env_values):
        status_broadcast_enabled = _read_bool(
            "FEISHU_NOTIFY_SEND_DAILY_SUMMARY",
            DEFAULT_STATUS_BROADCAST_ENABLED,
            env_values,
        )
    else:
        status_broadcast_enabled = DEFAULT_STATUS_BROADCAST_ENABLED

    return Settings(
        cpa_endpoint=endpoint,
        cpa_token=token,
        proxy=proxy,
        interval_seconds=_read_int("CPA_INTERVAL", DEFAULT_INTERVAL_SECONDS, env_values, minimum=1),
        quota_threshold=_read_int("CPA_QUOTA_THRESHOLD", DEFAULT_QUOTA_THRESHOLD, env_values, minimum=0, maximum=100),
        expiry_threshold_days=_read_int("CPA_EXPIRY_THRESHOLD_DAYS", DEFAULT_EXPIRY_THRESHOLD_DAYS, env_values, minimum=0),
        usage_timeout_seconds=_read_int("CPA_USAGE_TIMEOUT", DEFAULT_USAGE_TIMEOUT_SECONDS, env_values, minimum=1),
        cpa_timeout_seconds=_read_int("CPA_HTTP_TIMEOUT", DEFAULT_CPA_TIMEOUT_SECONDS, env_values, minimum=1),
        max_retries=_read_int("CPA_MAX_RETRIES", DEFAULT_MAX_RETRIES, env_values, minimum=0, maximum=5),
        worker_threads=_read_int("CPA_WORKER_THREADS", DEFAULT_WORKER_THREADS, env_values, minimum=1),
        enable_refresh=_read_bool("CPA_ENABLE_REFRESH", DEFAULT_ENABLE_REFRESH, env_values),
        feishu_webhook_url=feishu_webhook_url,
        feishu_security_mode=feishu_security_mode,
        feishu_keyword=feishu_keyword,
        feishu_secret=feishu_secret,
        notify_cooldown_minutes=_read_int("FEISHU_NOTIFY_COOLDOWN_MINUTES", DEFAULT_NOTIFY_COOLDOWN_MINUTES, env_values, minimum=1),
        notify_send_recovery=_read_bool("FEISHU_NOTIFY_SEND_RECOVERY", DEFAULT_NOTIFY_SEND_RECOVERY, env_values),
        notify_send_daily_summary=_read_bool("FEISHU_NOTIFY_SEND_DAILY_SUMMARY", DEFAULT_NOTIFY_SEND_DAILY_SUMMARY, env_values),
        notify_daily_summary_hours_utc=legacy_summary_hours,
        notify_failure_threshold=_read_int(
            "FEISHU_NOTIFY_FAILURE_THRESHOLD",
            DEFAULT_NOTIFY_FAILURE_THRESHOLD,
            env_values,
            minimum=1,
        ),
        notify_large_scale_usage_failure_threshold=_read_int(
            "FEISHU_NOTIFY_LARGE_SCALE_USAGE_FAILURE_THRESHOLD",
            DEFAULT_NOTIFY_LARGE_SCALE_USAGE_FAILURE_THRESHOLD,
            env_values,
            minimum=1,
        ),
        notify_state_file=notify_state_file,
        server_name=server_name,
        status_broadcast_enabled=status_broadcast_enabled,
        status_broadcast_hours_local=status_broadcast_hours,
        status_broadcast_timezone=status_broadcast_timezone,
        quota_report_enabled=_read_bool("CPA_QUOTA_REPORT_ENABLED", DEFAULT_CPA_QUOTA_REPORT_ENABLED, env_values),
        quota_alert_enabled=_read_bool("CPA_QUOTA_ALERT_ENABLED", DEFAULT_CPA_QUOTA_ALERT_ENABLED, env_values),
        quota_summary_enabled=_read_bool("CPA_QUOTA_SUMMARY_ENABLED", DEFAULT_CPA_QUOTA_SUMMARY_ENABLED, env_values),
        quota_plus_effective_usable_lt=_read_int(
            "CPA_QUOTA_PLUS_EFFECTIVE_USABLE_LT",
            DEFAULT_CPA_QUOTA_PLUS_EFFECTIVE_USABLE_LT,
            env_values,
            minimum=0,
        ),
        quota_plus_avg_remaining_5h_percent_lt=_read_int(
            "CPA_QUOTA_PLUS_AVG_REMAINING_5H_PERCENT_LT",
            DEFAULT_CPA_QUOTA_PLUS_AVG_REMAINING_5H_PERCENT_LT,
            env_values,
            minimum=0,
            maximum=100,
        ),
        quota_plus_avg_remaining_7d_percent_lt=_read_int(
            "CPA_QUOTA_PLUS_AVG_REMAINING_7D_PERCENT_LT",
            DEFAULT_CPA_QUOTA_PLUS_AVG_REMAINING_7D_PERCENT_LT,
            env_values,
            minimum=0,
            maximum=100,
        ),
        quota_summary_hour_local=_read_int(
            "CPA_QUOTA_SUMMARY_HOUR_LOCAL",
            DEFAULT_CPA_QUOTA_SUMMARY_HOUR_LOCAL,
            env_values,
            minimum=0,
            maximum=23,
        ),
        quota_timezone=quota_timezone,
        quota_state_file=quota_state_file,
    )
