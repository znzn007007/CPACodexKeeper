import base64
import hashlib
import hmac
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

from .settings import Settings


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FeishuNotifier:
    def __init__(self, settings: Settings, *, dry_run: bool = False) -> None:
        self.settings = settings
        self.dry_run = dry_run
        self.state_file = Path(settings.notify_state_file)
        self._lock = threading.Lock()
        self._state = self._load_state()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.feishu_webhook_url)

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {
                "cooldowns": {},
                "last_daily_summary_date": None,
                "last_daily_summary_slot": None,
                "consecutive_failure_rounds": 0,
                "last_failure_alert_at": None,
                "last_recovery_sent_at": None,
            }
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return {
                "cooldowns": {},
                "last_daily_summary_date": None,
                "last_daily_summary_slot": None,
                "consecutive_failure_rounds": 0,
                "last_failure_alert_at": None,
                "last_recovery_sent_at": None,
            }

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _format_text(self, title: str, lines: list[str]) -> str:
        text = title + "\n" + "\n".join(lines)
        if self.settings.feishu_security_mode == "keyword" and self.settings.feishu_keyword:
            text = f"{self.settings.feishu_keyword} {text}"
        return text

    def _build_payload(self, title: str, lines: list[str]) -> dict[str, Any]:
        text = self._format_text(title, lines)
        payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
        if self.settings.feishu_security_mode == "secret":
            timestamp = str(int(time.time()))
            secret = self.settings.feishu_secret or ""
            string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
            sign = base64.b64encode(hmac.new(string_to_sign, b"", digestmod=hashlib.sha256).digest()).decode("utf-8")
            payload["timestamp"] = timestamp
            payload["sign"] = sign
        return payload

    def _cooldown_active(self, dedupe_key: str, cooldown_minutes: int) -> bool:
        cooldowns = self._state.setdefault("cooldowns", {})
        raw = cooldowns.get(dedupe_key)
        if not raw:
            return False
        try:
            last_at = datetime.fromisoformat(raw)
        except Exception:
            return False
        elapsed = (_utc_now() - last_at).total_seconds()
        return elapsed < cooldown_minutes * 60

    def _mark_cooldown(self, dedupe_key: str) -> None:
        self._state.setdefault("cooldowns", {})[dedupe_key] = _utc_now().isoformat()

    def send(self, title: str, lines: list[str], *, dedupe_key: str | None = None, cooldown_minutes: int | None = None) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            effective_cooldown = cooldown_minutes or self.settings.notify_cooldown_minutes
            if dedupe_key and self._cooldown_active(dedupe_key, effective_cooldown):
                return False
            payload = self._build_payload(title, lines)
            if self.dry_run:
                if dedupe_key:
                    self._mark_cooldown(dedupe_key)
                    self._save_state()
                return True
        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = request.Request(
                self.settings.feishu_webhook_url or "",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=self.settings.cpa_timeout_seconds) as resp:
                _ = resp.read()
        except (error.HTTPError, error.URLError, TimeoutError, OSError):
            return False
        with self._lock:
            if dedupe_key:
                self._mark_cooldown(dedupe_key)
            self._save_state()
        return True

    def notify_delete(self, name: str, reason: str, *, email: str | None = None, status_code: int | None = None, detail: str | None = None) -> None:
        lines = [f"Token: {name}", f"原因: {reason}"]
        if email:
            lines.append(f"Email: {email}")
        if status_code is not None:
            lines.append(f"状态码: {status_code}")
        if detail:
            lines.append(f"详情: {detail[:300]}")
        self.send("CPACodexKeeper 删除通知", lines)

    def notify_refresh(self, name: str, message: str, *, email: str | None = None) -> None:
        lines = [f"Token: {name}", f"结果: {message}"]
        if email:
            lines.append(f"Email: {email}")
        self.send("CPACodexKeeper 刷新通知", lines)

    def notify_round_changes(self, stats: dict[str, int], events: dict[str, list[dict[str, Any]]]) -> None:
        changed = any(events.get(key) for key in ("deleted", "disabled", "enabled", "refreshed"))
        if not changed:
            return

        def names_for(key: str) -> str:
            names = [item["name"] for item in events.get(key, [])]
            return ", ".join(names[:20]) if names else "-"

        lines = [
            f"总计: {stats['total']}",
            f"删除: {stats['dead']}",
            f"禁用: {stats['disabled']}",
            f"启用: {stats['enabled']}",
            f"刷新: {stats['refreshed']}",
            f"删除名单: {names_for('deleted')}",
            f"禁用名单: {names_for('disabled')}",
            f"启用名单: {names_for('enabled')}",
            f"刷新名单: {names_for('refreshed')}",
        ]
        self.send("CPACodexKeeper 轮次变更通知", lines)

    def notify_large_scale_usage_failure(self, stats: dict[str, int], events: dict[str, list[dict[str, Any]]]) -> None:
        names = [item["name"] for item in events.get("network_errors", [])]
        lines = [
            f"本轮网络失败数: {stats['network_error']}",
            f"触发阈值: {self.settings.notify_large_scale_usage_failure_threshold}",
            f"涉及 Token: {', '.join(names[:20]) if names else '-'}",
        ]
        self.send(
            "CPACodexKeeper 大面积 usage 检测失败",
            lines,
            dedupe_key="large-scale-usage-failure",
        )

    def notify_cpa_api_exception(self, message: str) -> None:
        self.send(
            "CPACodexKeeper CPA API 异常",
            [message],
            dedupe_key="cpa-api-exception",
        )

    def notify_round_exception(self, round_no: int, exc: Exception) -> None:
        self.send(
            "CPACodexKeeper 巡检异常",
            [f"轮次: {round_no}", f"异常: {exc}"],
            dedupe_key="round-exception",
        )

    def notify_process_exit(self, exc: Exception) -> None:
        self.send(
            "CPACodexKeeper 进程异常退出",
            [f"异常: {exc}"],
            dedupe_key="process-exit",
        )

    def handle_failure_state(self, stats: dict[str, int]) -> None:
        send_recovery = False
        with self._lock:
            had_failures = stats["network_error"] > 0
            consecutive = int(self._state.get("consecutive_failure_rounds", 0))
            if had_failures:
                consecutive += 1
                self._state["consecutive_failure_rounds"] = consecutive
            else:
                if consecutive >= self.settings.notify_failure_threshold and self.settings.notify_send_recovery:
                    send_recovery = True
                self._state["consecutive_failure_rounds"] = 0
                self._save_state()
                consecutive = 0
        if send_recovery:
            self.send(
                "CPACodexKeeper 网络检测恢复",
                ["连续网络失败已恢复，本轮 network_error=0"],
                dedupe_key="failure-recovery",
                cooldown_minutes=1,
            )
        if not had_failures:
            return
        if consecutive >= self.settings.notify_failure_threshold:
            self.send(
                "CPACodexKeeper 连续多轮网络失败",
                [
                    f"当前连续失败轮次: {consecutive}",
                    f"本轮 network_error: {stats['network_error']}",
                ],
                dedupe_key="consecutive-network-failure",
            )

    def maybe_send_daily_summary(self, stats: dict[str, int]) -> None:
        if not self.settings.notify_send_daily_summary or not self.enabled:
            return
        now = _utc_now()
        hour = now.hour
        if hour not in self.settings.notify_daily_summary_hours_utc:
            return
        with self._lock:
            last_date = self._state.get("last_daily_summary_date")
            last_slot = self._state.get("last_daily_summary_slot")
            today = now.date().isoformat()
            if last_date == today and last_slot == hour:
                return
            self._state["last_daily_summary_date"] = today
            self._state["last_daily_summary_slot"] = hour
            self._save_state()
        lines = [
            f"总计: {stats['total']}",
            f"存活: {stats['alive']}",
            f"删除: {stats['dead']}",
            f"禁用: {stats['disabled']}",
            f"启用: {stats['enabled']}",
            f"刷新: {stats['refreshed']}",
            f"跳过: {stats['skipped']}",
            f"网络失败: {stats['network_error']}",
        ]
        self.send("CPACodexKeeper 日报", lines)
