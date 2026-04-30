from __future__ import annotations

import json
from typing import Any, Protocol

from .quota_report import (
    QuotaThresholds,
    aggregate,
    build_alert_lines,
    build_daily_summary_lines,
    build_recovery_lines,
    evaluate_alert,
)
from .quota_state import QuotaHealthcheckState
from .settings import Settings


class Sender(Protocol):
    @property
    def enabled(self) -> bool: ...
    def send(self, title: str, lines: list[str], *, dedupe_key: str | None = None, cooldown_minutes: int | None = None) -> bool: ...


class QuotaHealthcheckJob:
    def __init__(self, settings: Settings, sender: Sender, *, logger=None) -> None:
        self.settings = settings
        self.sender = sender
        self.logger = logger
        self.state = QuotaHealthcheckState(settings.quota_state_file)

    def _log(self, level: str, message: str) -> None:
        if self.logger:
            self.logger(level, message)

    def _thresholds(self) -> QuotaThresholds:
        return QuotaThresholds(
            plus_effective_usable_lt=self.settings.quota_plus_effective_usable_lt,
            plus_avg_remaining_5h_percent_lt=self.settings.quota_plus_avg_remaining_5h_percent_lt,
            plus_avg_remaining_7d_percent_lt=self.settings.quota_plus_avg_remaining_7d_percent_lt,
        )

    def run(self, snapshots: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not self.settings.quota_report_enabled:
            self._log("INFO", "CPA quota report disabled")
            return None
        agg = aggregate(snapshots)
        evaluation = evaluate_alert(agg, self._thresholds())
        alert_evaluable = self._alert_evaluable(agg)
        self._log(
            "INFO",
            "CPA quota report generated: "
            f"total={agg['overall']['total_auth_count']} "
            f"usable={agg['overall']['broad_usable_auth_count']} "
            f"plus_effective={agg['plus']['effective_usable_auth_count']} "
            f"status={evaluation['status'] if alert_evaluable else 'SKIPPED'}",
        )

        if self.sender.enabled and alert_evaluable:
            self._maybe_send_alert_transition(agg, evaluation)
        if not self._dry_run:
            self.state.save()
        return agg

    @property
    def _dry_run(self) -> bool:
        return bool(getattr(self.sender, "dry_run", False))

    def _alert_evaluable(self, agg: dict[str, Any]) -> bool:
        """Only alert from a complete, positive Plus sample.

        Unknown rows are usually network errors or malformed usage payloads. In
        those rounds quota can still be shown in broadcasts, but alert state must
        not transition because the sample is incomplete.
        """
        if agg.get("unknown", {}).get("total_auth_count", 0) > 0:
            return False
        return agg.get("plus", {}).get("total_auth_count", 0) > 0

    def _maybe_send_alert_transition(self, agg: dict[str, Any], evaluation: dict[str, Any]) -> None:
        if not self.settings.quota_alert_enabled:
            return
        transition = self.state.plan_alert_transition(evaluation, send_recovery=self.settings.notify_send_recovery)
        action = transition["action"]
        if action == "alert":
            sent = self.sender.send("CPA Codex Plus 额度告警", build_alert_lines(agg, transition["reasons"]))
            if sent and not self._dry_run:
                self.state.commit_alert_transition(transition)
        elif action == "recovery":
            sent = self.sender.send("CPA Codex Plus 额度恢复", build_recovery_lines(agg))
            if sent and not self._dry_run:
                self.state.commit_alert_transition(transition)
        else:
            if not self._dry_run:
                self.state.commit_alert_transition(transition)



def fake_aggregate(status: str) -> dict[str, Any]:
    if status == "alert":
        plus_effective = 1
        avg5 = 5
        avg7 = 10
    else:
        plus_effective = 20
        avg5 = 80
        avg7 = 90
    return {
        "checked_at": "test",
        "overall": {
            "total_auth_count": 25,
            "broad_usable_auth_count": 22,
            "unavailable_auth_count": 3,
        },
        "plus": {
            "total_auth_count": 20,
            "broad_usable_auth_count": 18,
            "effective_usable_auth_count": plus_effective,
            "exhausted_5h_but_7d_available_auth_count": 2,
            "unavailable_7d_auth_count": 2,
            "total": 20,
            "avg_remaining_5h_percent": avg5,
            "avg_remaining_7d_percent": avg7,
            "earliest_reset_at_5h": "2026-04-25T08:00:00+00:00",
            "earliest_reset_at_7d": "2026-04-26T08:00:00+00:00",
        },
        "free": {
            "total_auth_count": 5,
            "effective_usable_auth_count": 4,
            "unavailable_7d_auth_count": 1,
            "total": 5,
            "avg_remaining_7d_percent": 70,
            "earliest_reset_at_7d": "2026-04-26T08:00:00+00:00",
        },
        "unknown": {"total_auth_count": 0},
        "auths": [],
    }


def run_test_notification(settings: Settings, sender: Sender, mode: str) -> bool:
    agg = fake_aggregate("alert" if mode == "alert" else "normal")
    if mode in {"summary", "broadcast"}:
        stats = {
            "total": 25,
            "alive": 22,
            "dead": 2,
            "disabled": 3,
            "enabled": 1,
            "refreshed": 4,
            "skipped": 0,
            "network_error": 1,
        }
        events = {
            "deleted": [{"name": "deleted-a", "reason": "Token 无效"}, {"name": "deleted-b", "reason": "已过期"}],
            "disabled": [{"name": "disabled-a", "reason": "5h额度 100%"}],
            "enabled": [{"name": "enabled-a"}],
            "refreshed": [{"name": "refreshed-a"}],
            "network_errors": [{"name": "network-a"}],
        }
        if hasattr(sender, "notify_status_broadcast"):
            return sender.notify_status_broadcast(stats, events, agg, test=True)
        lines = [
            "【账号巡检】",
            f"总计: {stats['total']}",
            f"删除: {stats['dead']}",
            f"禁用: {stats['disabled']}",
            f"启用: {stats['enabled']}",
            f"刷新: {stats['refreshed']}",
            "",
            "【本轮名单摘要】",
            "删除名单: deleted-a, deleted-b",
            "禁用名单: disabled-a",
            "启用名单: enabled-a",
            "刷新名单: refreshed-a",
            "",
            *build_daily_summary_lines(agg),
        ]
        return sender.send("[TEST] CPA Codex 定时播报", lines)
    if mode == "deleted":
        events = [
            {"name": "deleted-a", "email": "a@example.com", "reason": "Token 无效", "status_code": 401},
            {"name": "deleted-b", "email": "b@example.com", "reason": "Token 已过期且无 Refresh Token"},
        ]
        if hasattr(sender, "notify_deleted_accounts"):
            return sender.notify_deleted_accounts(events, test=True)
        return sender.send("[TEST] CPA Codex 删除通知", ["本轮删除: 2 个", "删除名单:", "- deleted-a", "- deleted-b"])
    if mode == "disabled":
        events = [
            {"name": "disabled-a", "email": "a@example.com", "reason": "5h额度 100%"},
            {"name": "disabled-b", "email": "b@example.com", "reason": "Week额度 100%"},
        ]
        if hasattr(sender, "notify_disabled_accounts"):
            return sender.notify_disabled_accounts(events, test=True)
        return sender.send("[TEST] CPA Codex 禁用通知", ["本轮禁用: 2 个", "禁用名单:", "- disabled-a", "- disabled-b"])
    if mode == "alert":
        evaluation = evaluate_alert(agg, QuotaThresholds(10, 30, 30))
        return sender.send("[TEST] CPA Codex Plus 额度告警", build_alert_lines(agg, evaluation["reasons"]))
    if mode == "recovery":
        return sender.send("[TEST] CPA Codex Plus 额度恢复", build_recovery_lines(agg))
    raise ValueError(f"unsupported quota test mode: {mode}")
