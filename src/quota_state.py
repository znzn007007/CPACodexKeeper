from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_STATE = {
    "version": 1,
    "alert_state": {
        "status": "NORMAL",
        "last_transition_at": None,
        "last_alert_sent_at": None,
        "last_recovery_sent_at": None,
        "last_reasons": [],
    },
    "summary_state": {
        "last_summary_date": None,
        "last_summary_hour": None,
    },
    "broadcast_state": {
        "last_broadcast_date": None,
        "last_broadcast_hour": None,
    },
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _fresh_state() -> dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_STATE))


class QuotaHealthcheckState:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _fresh_state()
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return _fresh_state()
        state = _fresh_state()
        if isinstance(loaded, dict):
            for key, value in loaded.items():
                if key in state and isinstance(value, dict) and isinstance(state[key], dict):
                    state[key].update(value)
                else:
                    state[key] = value
            if "broadcast_state" not in loaded and isinstance(loaded.get("summary_state"), dict):
                summary_state = loaded["summary_state"]
                state["broadcast_state"].update({
                    "last_broadcast_date": summary_state.get("last_summary_date"),
                    "last_broadcast_hour": summary_state.get("last_summary_hour"),
                })
        return state

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def plan_alert_transition(self, evaluation: dict[str, Any], *, send_recovery: bool = True) -> dict[str, Any]:
        """Return the needed alert action without mutating persisted state."""
        alert_state = self.state.setdefault("alert_state", _fresh_state()["alert_state"])
        previous = alert_state.get("status", "NORMAL")
        current = evaluation.get("status", "NORMAL")
        reasons = list(evaluation.get("reasons") or [])
        action = "none"

        if current == "ALERTING" and previous != "ALERTING":
            action = "alert"
        elif current == "NORMAL" and previous == "ALERTING":
            if send_recovery:
                action = "recovery"
            else:
                current = "ALERTING"
                reasons = list(alert_state.get("last_reasons") or [])

        return {
            "action": action,
            "previous_status": previous,
            "current_status": current,
            "reasons": reasons,
        }

    def commit_alert_transition(self, transition: dict[str, Any]) -> None:
        """Persist an already observed/sent transition."""
        alert_state = self.state.setdefault("alert_state", _fresh_state()["alert_state"])
        action = transition.get("action", "none")
        current = transition.get("current_status", "NORMAL")
        reasons = list(transition.get("reasons") or [])
        now = _iso_now()

        update = {"status": current, "last_reasons": reasons}
        if action == "alert":
            update.update({
                "last_transition_at": now,
                "last_alert_sent_at": now,
                "last_recovery_sent_at": alert_state.get("last_recovery_sent_at"),
            })
        elif action == "recovery":
            update.update({
                "last_transition_at": now,
                "last_alert_sent_at": alert_state.get("last_alert_sent_at"),
                "last_recovery_sent_at": now,
                "last_reasons": [],
            })
        alert_state.update(update)

    def evaluate_alert_transition(self, evaluation: dict[str, Any], *, send_recovery: bool = True) -> dict[str, Any]:
        """Backward-compatible helper that plans and immediately commits."""
        transition = self.plan_alert_transition(evaluation, send_recovery=send_recovery)
        self.commit_alert_transition(transition)
        return transition

    def _local_summary_date(self, timezone_name: str, now: datetime | None = None) -> str:
        current = now or _utc_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        try:
            local_now = current.astimezone(ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError:
            local_now = current.astimezone(timezone.utc)
        return local_now.date().isoformat()

    def should_send_summary(self, *, enabled: bool, hour_local: int, timezone_name: str, now: datetime | None = None) -> bool:
        if not enabled:
            return False
        current = now or _utc_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        try:
            local_now = current.astimezone(ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError:
            local_now = current.astimezone(timezone.utc)
        if local_now.hour < hour_local:
            return False
        summary_state = self.state.setdefault("summary_state", _fresh_state()["summary_state"])
        today = local_now.date().isoformat()
        return summary_state.get("last_summary_date") != today

    def commit_summary(self, *, hour_local: int, timezone_name: str, now: datetime | None = None) -> None:
        summary_state = self.state.setdefault("summary_state", _fresh_state()["summary_state"])
        summary_state["last_summary_date"] = self._local_summary_date(timezone_name, now)
        summary_state["last_summary_hour"] = hour_local

    def _local_now(self, timezone_name: str, now: datetime | None = None) -> datetime:
        current = now or _utc_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        try:
            return current.astimezone(ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError:
            return current.astimezone(timezone.utc)

    def should_send_broadcast(
        self,
        *,
        enabled: bool,
        hours_local: tuple[int, ...],
        timezone_name: str,
        now: datetime | None = None,
    ) -> bool:
        if not enabled:
            return False
        local_now = self._local_now(timezone_name, now)
        if local_now.hour not in hours_local:
            return False
        broadcast_state = self.state.setdefault("broadcast_state", _fresh_state()["broadcast_state"])
        today = local_now.date().isoformat()
        return not (
            broadcast_state.get("last_broadcast_date") == today
            and broadcast_state.get("last_broadcast_hour") == local_now.hour
        )

    def commit_broadcast(self, *, timezone_name: str, now: datetime | None = None) -> None:
        local_now = self._local_now(timezone_name, now)
        broadcast_state = self.state.setdefault("broadcast_state", _fresh_state()["broadcast_state"])
        broadcast_state["last_broadcast_date"] = local_now.date().isoformat()
        broadcast_state["last_broadcast_hour"] = local_now.hour
