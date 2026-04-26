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

from .quota_report import build_daily_summary_lines
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
        text = self._format_title(title) + "\n" + "\n".join(lines)
        if self.settings.feishu_security_mode == "keyword" and self.settings.feishu_keyword:
            text = f"{self.settings.feishu_keyword} {text}"
        return text

    def _format_title(self, title: str) -> str:
        server_name = (self.settings.server_name or "cpacodexkeeper").strip()
        if self.dry_run and not title.startswith("[DRY-RUN]"):
            title = f"[DRY-RUN] {title}"
        prefix = f"[{server_name}] "
        if title.startswith(prefix):
            return title
        return f"{prefix}{title}"

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

    def _event_lines(
        self,
        events: list[dict[str, Any]],
        *,
        include_reason: bool = True,
        include_status: bool = False,
        limit: int = 20,
    ) -> list[str]:
        lines: list[str] = []
        for item in events[:limit]:
            parts = [str(item.get("name") or "unknown")]
            if item.get("email"):
                parts.append(str(item["email"]))
            if include_reason and item.get("reason"):
                parts.append(str(item["reason"])[:160])
            if include_status and item.get("status_code") is not None:
                parts.append(f"状态码 {item['status_code']}")
            if item.get("detail"):
                parts.append(str(item["detail"])[:120])
            lines.append("- " + " | ".join(parts))
        if len(events) > limit:
            lines.append(f"... 另有 {len(events) - limit} 个，详见容器日志")
        return lines

    def notify_deleted_accounts(self, events: list[dict[str, Any]], *, test: bool = False) -> bool:
        if not events:
            return False
        lines = [f"本轮删除: {len(events)} 个", "删除名单:"]
        lines.extend(self._event_lines(events, include_status=True))
        title = "[TEST] CPA Codex 删除通知" if test else "CPA Codex 删除通知"
        return self.send(title, lines)

    def notify_disabled_accounts(self, events: list[dict[str, Any]], *, test: bool = False) -> bool:
        if not events:
            return False
        lines = [f"本轮禁用: {len(events)} 个", "禁用名单:"]
        lines.extend(self._event_lines(events, include_reason=False))
        title = "[TEST] CPA Codex 禁用通知" if test else "CPA Codex 禁用通知"
        return self.send(title, lines)

    def notify_status_broadcast(
        self,
        stats: dict[str, int],
        events: dict[str, list[dict[str, Any]]],
        quota_agg: dict[str, Any] | None,
        *,
        test: bool = False,
    ) -> bool:
        def event_summary(key: str) -> str:
            values = events.get(key, [])
            if not values:
                return "-"
            names = [str(item.get("name") or "unknown") for item in values]
            display = ", ".join(names[:20])
            if len(names) > 20:
                display += f" ... 另有 {len(names) - 20} 个"
            return display

        lines = [
            "【账号巡检】",
            f"总计: {stats.get('total', 0)}",
            f"存活: {stats.get('alive', 0)}",
            f"删除: {stats.get('dead', 0)}",
            f"禁用: {stats.get('disabled', 0)}",
            f"启用: {stats.get('enabled', 0)}",
            f"刷新: {stats.get('refreshed', 0)}",
            f"跳过: {stats.get('skipped', 0)}",
            f"网络失败: {stats.get('network_error', 0)}",
            "",
            "【本轮名单摘要】",
            f"删除名单: {event_summary('deleted')}",
            f"禁用名单: {event_summary('disabled')}",
            f"启用名单: {event_summary('enabled')}",
            f"刷新名单: {event_summary('refreshed')}",
        ]
        lines.append("")
        if quota_agg:
            lines.extend(build_daily_summary_lines(quota_agg))
        else:
            lines.extend(["【额度概况】", "Quota 信息不可用或未启用"])
        title = "[TEST] CPA Codex 定时播报" if test else "CPA Codex 定时播报"
        return self.send(title, lines)

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
        if self.dry_run:
            return
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

