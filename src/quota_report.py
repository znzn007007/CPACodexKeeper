from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any

from .models import UsageInfo


@dataclass(slots=True)
class QuotaThresholds:
    plus_effective_usable_lt: int = 10
    plus_avg_remaining_5h_percent_lt: int = 30
    plus_avg_remaining_7d_percent_lt: int = 30


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp_to_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def display_iso(value: str | None) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def _remaining(used_percent: int | None) -> int | None:
    if used_percent is None:
        return None
    return max(0, 100 - int(used_percent))


def _detect_account_type(usage: UsageInfo) -> str:
    plan = (usage.plan_type or "").lower()
    if not usage.valid:
        return "unknown"
    if plan == "free":
        return "free"
    if plan == "plus":
        return "plus"
    if usage.primary_window and usage.secondary_window:
        return "plus"
    if usage.primary_window and not usage.secondary_window:
        return "free"
    return "unknown"


def snapshot_from_usage(*, name: str, email: str | None, usage: UsageInfo) -> dict[str, Any]:
    account_type = _detect_account_type(usage)
    primary = usage.primary_window
    secondary = usage.secondary_window

    if account_type == "free":
        free_window = primary if primary else secondary
        remaining_5h = None
        remaining_7d = _remaining(free_window.used_percent) if free_window else None
        used_5h = None
        used_7d = free_window.used_percent if free_window else None
        reset_5h_at = None
        reset_7d_at = timestamp_to_iso(free_window.reset_at) if free_window else None
        broad_usable = bool((remaining_7d or 0) > 0)
        effective_usable = broad_usable
        is_5h_empty = None
        is_7d_empty = True if remaining_7d is None else remaining_7d <= 0
    else:
        remaining_5h = _remaining(primary.used_percent) if primary else None
        remaining_7d = _remaining(secondary.used_percent) if secondary else None
        used_5h = primary.used_percent if primary else None
        used_7d = secondary.used_percent if secondary else None
        reset_5h_at = timestamp_to_iso(primary.reset_at) if primary else None
        reset_7d_at = timestamp_to_iso(secondary.reset_at) if secondary else None
        is_5h_empty = None if primary is None else (remaining_5h or 0) <= 0
        is_7d_empty = True if remaining_7d is None else remaining_7d <= 0
        broad_usable = bool((remaining_7d or 0) > 0)
        effective_usable = broad_usable and bool((remaining_5h or 0) > 0) if primary else broad_usable

    return {
        "name": name,
        "email": email,
        "plan_type": usage.plan_type,
        "account_type": account_type,
        "broad_usable": broad_usable,
        "effective_usable": effective_usable,
        "plus_effective_usable": account_type == "plus" and effective_usable,
        "free_effective_usable": account_type == "free" and effective_usable,
        "is_5h_empty": is_5h_empty,
        "is_7d_empty": is_7d_empty,
        "remaining_5h_percent": remaining_5h,
        "remaining_7d_percent": remaining_7d,
        "used_5h_percent": used_5h,
        "used_7d_percent": used_7d,
        "reset_5h_at": reset_5h_at,
        "reset_7d_at": reset_7d_at,
        "checked_at": utc_now_iso(),
        "last_error": "",
    }


def error_snapshot(*, name: str, email: str | None = None, error: str) -> dict[str, Any]:
    return {
        "name": name,
        "email": email,
        "account_type": "unknown",
        "broad_usable": False,
        "effective_usable": False,
        "remaining_5h_percent": None,
        "remaining_7d_percent": None,
        "reset_5h_at": None,
        "reset_7d_at": None,
        "checked_at": utc_now_iso(),
        "last_error": error,
    }


def aggregate_group(rows: list[dict[str, Any]], *, include_5h: bool) -> dict[str, Any]:
    rem7 = [float(x["remaining_7d_percent"]) for x in rows if x.get("remaining_7d_percent") is not None]
    resets7 = [x.get("reset_7d_at") for x in rows if x.get("reset_7d_at")]
    result: dict[str, Any] = {
        "total": len(rows),
        "avg_remaining_7d_percent": round(sum(rem7) / len(rem7), 2) if rem7 else 0,
        "earliest_reset_at_7d": min(resets7) if resets7 else None,
    }
    if include_5h:
        rem5 = [float(x["remaining_5h_percent"]) for x in rows if x.get("remaining_5h_percent") is not None]
        resets5 = [x.get("reset_5h_at") for x in rows if x.get("reset_5h_at")]
        result.update({
            "avg_remaining_5h_percent": round(sum(rem5) / len(rem5), 2) if rem5 else 0,
            "earliest_reset_at_5h": min(resets5) if resets5 else None,
        })
    return result


def aggregate(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    rows = list(snapshots)
    plus_rows = [x for x in rows if x.get("account_type") == "plus"]
    free_rows = [x for x in rows if x.get("account_type") == "free"]
    unknown_rows = [x for x in rows if x.get("account_type") not in {"plus", "free"}]

    broad_usable_rows = [x for x in rows if x.get("broad_usable")]
    unavailable_7d_rows = [x for x in rows if not x.get("broad_usable")]
    plus_broad_usable = [x for x in plus_rows if x.get("broad_usable")]
    plus_effective_usable = [x for x in plus_rows if x.get("effective_usable")]
    plus_5h_exhausted = [x for x in plus_rows if x.get("broad_usable") and not x.get("effective_usable")]
    plus_7d_exhausted = [x for x in plus_rows if not x.get("broad_usable")]
    free_effective_usable = [x for x in free_rows if x.get("effective_usable")]
    free_7d_exhausted = [x for x in free_rows if not x.get("broad_usable")]

    return {
        "checked_at": utc_now_iso(),
        "overall": {
            "total_auth_count": len(rows),
            "broad_usable_auth_count": len(broad_usable_rows),
            "unavailable_auth_count": len(unavailable_7d_rows),
        },
        "plus": {
            "total_auth_count": len(plus_rows),
            "broad_usable_auth_count": len(plus_broad_usable),
            "effective_usable_auth_count": len(plus_effective_usable),
            "exhausted_5h_but_7d_available_auth_count": len(plus_5h_exhausted),
            "unavailable_7d_auth_count": len(plus_7d_exhausted),
            **aggregate_group(plus_rows, include_5h=True),
        },
        "free": {
            "total_auth_count": len(free_rows),
            "effective_usable_auth_count": len(free_effective_usable),
            "unavailable_7d_auth_count": len(free_7d_exhausted),
            **aggregate_group(free_rows, include_5h=False),
        },
        "unknown": {"total_auth_count": len(unknown_rows)},
        "auths": rows,
    }


def evaluate_alert(agg: dict[str, Any], thresholds: QuotaThresholds) -> dict[str, Any]:
    plus = agg["plus"]
    reasons: list[str] = []
    if plus["effective_usable_auth_count"] < thresholds.plus_effective_usable_lt:
        reasons.append(f"plus_effective_usable_auth_count={plus['effective_usable_auth_count']} < {thresholds.plus_effective_usable_lt}")
    if plus["avg_remaining_5h_percent"] < thresholds.plus_avg_remaining_5h_percent_lt:
        reasons.append(f"plus_avg_remaining_5h_percent={plus['avg_remaining_5h_percent']} < {thresholds.plus_avg_remaining_5h_percent_lt}")
    if plus["avg_remaining_7d_percent"] < thresholds.plus_avg_remaining_7d_percent_lt:
        reasons.append(f"plus_avg_remaining_7d_percent={plus['avg_remaining_7d_percent']} < {thresholds.plus_avg_remaining_7d_percent_lt}")
    return {"status": "ALERTING" if reasons else "NORMAL", "reasons": reasons}


def build_daily_summary_lines(agg: dict[str, Any]) -> list[str]:
    overall = agg["overall"]
    plus = agg["plus"]
    free = agg["free"]
    lines = [
        "【总体】",
        f"总 auth: {overall['total_auth_count']}",
        f"总可用(7d>0): {overall['broad_usable_auth_count']}",
        f"总不可用(7d=0): {overall['unavailable_auth_count']}",
        "",
        "【Plus】",
        f"总 plus: {plus['total_auth_count']}",
        f"7d 可用: {plus['broad_usable_auth_count']}",
        f"当前可用(7d>0 且 5h>0): {plus['effective_usable_auth_count']}",
        f"5h exhausted(7d>0 且 5h=0): {plus['exhausted_5h_but_7d_available_auth_count']}",
        f"7d exhausted: {plus['unavailable_7d_auth_count']}",
        f"5h 平均剩余: {plus['avg_remaining_5h_percent']}%",
        f"7d 平均剩余: {plus['avg_remaining_7d_percent']}%",
        f"最早 5h 重置(UTC+8): {display_iso(plus['earliest_reset_at_5h'])}",
        f"最早 7d 重置(UTC+8): {display_iso(plus['earliest_reset_at_7d'])}",
        "",
        "【Free】",
        f"总 free: {free['total_auth_count']}",
        f"当前可用(7d>0): {free['effective_usable_auth_count']}",
        f"7d exhausted: {free['unavailable_7d_auth_count']}",
        f"7d 平均剩余: {free['avg_remaining_7d_percent']}%",
        f"最早 7d 重置(UTC+8): {display_iso(free['earliest_reset_at_7d'])}",
    ]
    if agg.get("unknown", {}).get("total_auth_count"):
        lines.extend(["", f"【Unknown】{agg['unknown']['total_auth_count']}"])
    return lines


def build_alert_lines(agg: dict[str, Any], reasons: list[str]) -> list[str]:
    plus = agg["plus"]
    lines = [
        "【Plus 当前状态】",
        f"总 plus: {plus['total_auth_count']}",
        f"当前可用(7d>0 且 5h>0): {plus['effective_usable_auth_count']}",
        f"5h exhausted(7d>0 且 5h=0): {plus['exhausted_5h_but_7d_available_auth_count']}",
        f"7d exhausted: {plus['unavailable_7d_auth_count']}",
        f"5h 平均剩余: {plus['avg_remaining_5h_percent']}%",
        f"7d 平均剩余: {plus['avg_remaining_7d_percent']}%",
        f"最早 5h 重置(UTC+8): {display_iso(plus['earliest_reset_at_5h'])}",
        f"最早 7d 重置(UTC+8): {display_iso(plus['earliest_reset_at_7d'])}",
    ]
    if reasons:
        lines.append("触发条件: " + "; ".join(reasons))
    return lines


def build_recovery_lines(agg: dict[str, Any]) -> list[str]:
    plus = agg["plus"]
    return [
        "【Plus 当前状态】",
        f"总 plus: {plus['total_auth_count']}",
        f"当前可用(7d>0 且 5h>0): {plus['effective_usable_auth_count']}",
        f"5h 平均剩余: {plus['avg_remaining_5h_percent']}%",
        f"7d 平均剩余: {plus['avg_remaining_7d_percent']}%",
        f"最早 5h 重置(UTC+8): {display_iso(plus['earliest_reset_at_5h'])}",
        f"最早 7d 重置(UTC+8): {display_iso(plus['earliest_reset_at_7d'])}",
    ]
