import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .cpa_client import CPAClient
from .logging_utils import ConsoleLogger, TokenLogger
from .models import MaintainerStats, format_window_label
from .notifier import FeishuNotifier
from .openai_client import OpenAIClient, parse_usage_info
from .quota_job import QuotaHealthcheckJob
from .quota_report import error_snapshot, snapshot_from_usage
from .settings import Settings
from .utils import format_seconds, get_expired_remaining, get_expired_remaining_with_status


class CPACodexKeeper:
    def __init__(self, settings: Settings, dry_run: bool = False):
        self.settings = settings
        self.dry_run = dry_run
        self.logger = ConsoleLogger()
        self.notifier = FeishuNotifier(settings=settings, dry_run=dry_run)
        self.cpa_client = CPAClient(
            settings.cpa_endpoint,
            settings.cpa_token,
            proxy=settings.proxy,
            timeout=settings.cpa_timeout_seconds,
            max_retries=settings.max_retries,
        )
        self.openai_client = OpenAIClient(
            proxy=settings.proxy,
            timeout=settings.usage_timeout_seconds,
            max_retries=settings.max_retries,
        )
        self.stats = MaintainerStats()
        self._stats_lock = threading.Lock()
        self._events_lock = threading.Lock()
        self.round_events = self._empty_round_events()
        self._quota_lock = threading.Lock()
        self.quota_snapshots = []
        self.quota_job = QuotaHealthcheckJob(settings=settings, sender=self.notifier, logger=self.log)

    def _empty_round_events(self):
        return {
            "deleted": [],
            "disabled": [],
            "enabled": [],
            "refreshed": [],
            "network_errors": [],
            "task_exceptions": [],
        }

    def reset_stats(self):
        with self._stats_lock:
            self.stats = MaintainerStats()
        with self._events_lock:
            self.round_events = self._empty_round_events()
        with self._quota_lock:
            self.quota_snapshots = []

    def blank_line(self):
        self.logger.blank_line()

    def _inc_stat(self, field_name, amount=1):
        with self._stats_lock:
            setattr(self.stats, field_name, getattr(self.stats, field_name) + amount)

    def _set_total(self, total):
        with self._stats_lock:
            self.stats.total = total

    def _stats_snapshot(self):
        with self._stats_lock:
            return self.stats.as_dict()

    def _record_event(self, event_name, payload):
        with self._events_lock:
            self.round_events[event_name].append(payload)

    def _events_snapshot(self):
        with self._events_lock:
            return {
                key: list(value)
                for key, value in self.round_events.items()
            }

    def _record_quota_snapshot(self, snapshot):
        with self._quota_lock:
            self.quota_snapshots.append(snapshot)

    def _quota_snapshot_list(self):
        with self._quota_lock:
            return list(self.quota_snapshots)

    def log(self, level, message, indent=0):
        self.logger.log(level, message, indent=indent)

    def log_token_header(self, idx, total, name):
        self.logger.token_header(idx, total, name)

    def filter_tokens(self, tokens):
        return [token for token in tokens if token.get("type") == "codex"]

    def get_token_list(self):
        return self.filter_tokens(self.cpa_client.list_auth_files())

    def get_token_detail(self, name):
        return self.cpa_client.get_auth_file(name)

    def delete_token(self, name, logger=None):
        if self.dry_run:
            (logger or self).log("DRY", f"将删除: {name}", indent=1)
            return True
        return self.cpa_client.delete_auth_file(name)

    def set_disabled_status(self, name, disabled=True, logger=None):
        if self.dry_run:
            (logger or self).log("DRY", f"将{'禁用' if disabled else '启用'}: {name}", indent=1)
            return True
        return self.cpa_client.set_disabled(name, disabled)

    def check_token_live(self, access_token, account_id=None):
        if not access_token:
            return None, "missing access_token"
        result = self.openai_client.check_usage(access_token, account_id)
        if result.status_code is None:
            return None, result.error or "request failed"
        return result.status_code, {
            "status_code": result.status_code,
            "body": result.body,
            "brief": result.brief or result.error or "",
            "json": result.json_data,
        }

    def parse_usage_info(self, resp_data):
        usage = parse_usage_info(resp_data)
        return {
            "plan_type": usage.plan_type,
            "primary_used_percent": usage.primary_used_percent,
            "primary_window_seconds": usage.primary_window.limit_window_seconds,
            "primary_reset_at": usage.primary_window.reset_at,
            "secondary_used_percent": usage.secondary_used_percent,
            "secondary_window_seconds": None if usage.secondary_window is None else usage.secondary_window.limit_window_seconds,
            "secondary_reset_at": None if usage.secondary_window is None else usage.secondary_window.reset_at,
            "used_percent": usage.primary_used_percent,
            "has_credits": usage.has_credits,
        }

    def try_refresh(self, token_data):
        rt = token_data.get("refresh_token")
        if not rt:
            return False, None, "缺少 Refresh Token"
        result = self.openai_client.refresh_token(rt)
        if result.status_code != 200 or not result.json_data:
            return False, None, f"刷新被拒({result.status_code})" if result.status_code else (result.error or "刷新失败")
        new_tokens = result.json_data
        expires_in = new_tokens.get("expires_in", 864000)
        new_data = dict(token_data)
        new_data.update({
            "access_token": new_tokens["access_token"],
            "refresh_token": new_tokens.get("refresh_token", rt),
            "id_token": new_tokens.get("id_token"),
            "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "expired": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + expires_in)),
        })
        return True, new_data, f"刷新成功，新有效期: {format_seconds(expires_in)}"

    def upload_updated_token(self, name, token_data, logger=None):
        if self.dry_run:
            (logger or self).log("DRY", f"将上传更新: {name}", indent=1)
            return True
        return self.cpa_client.upload_auth_file(name, token_data)

    def _skip_token(self, message, logger, *, network_error=False, token_name=None):
        logger.log("WARN" if network_error else "SKIP", message, indent=1)
        if network_error:
            self._inc_stat("network_error")
            if token_name:
                self._record_event("network_errors", {"name": token_name, "message": message})
            logger.blank_line()
            return "network_error"
        self._inc_stat("skipped")
        logger.blank_line()
        return "skipped"

    def _log_token_details(self, token_detail, logger):
        email = token_detail.get("email", "unknown")
        disabled = token_detail.get("disabled", False)
        expired_str, remaining_seconds, expiry_known = get_expired_remaining_with_status(token_detail)
        remaining_str = format_seconds(remaining_seconds) if expiry_known else "未知"

        logger.log("INFO", f"Email: {email}", indent=1)
        logger.log("INFO", f"状态: {'已禁用' if disabled else '正常'}", indent=1)
        logger.log("INFO", f"过期时间: {expired_str or '未知'}", indent=1)
        logger.log("INFO", f"剩余有效期: {remaining_str}", indent=1)
        return disabled, remaining_seconds, remaining_str, expiry_known

    def _has_refresh_token(self, token_detail):
        return bool((token_detail.get("refresh_token") or "").strip())

    def _delete_token_with_reason(self, name, reason, logger, *, token_detail=None, status_code=None, detail=None):
        logger.log("WARN", reason, indent=1)
        if self.delete_token(name, logger=logger):
            logger.log("DELETE", "已删除", indent=1)
            self._inc_stat("dead")
            email = (token_detail or {}).get("email")
            event = {"name": name, "reason": reason, "email": email}
            if status_code is not None:
                event["status_code"] = status_code
            if detail:
                event["detail"] = detail
            self._record_event("deleted", event)
            logger.blank_line()
            return "dead"
        return self._skip_token("删除失败", logger)

    def _handle_invalid_token(self, name, logger, *, token_detail=None, status_code=None, detail=None):
        return self._delete_token_with_reason(
            name,
            "Token 无效或 workspace 已停用，准备删除",
            logger,
            token_detail=token_detail,
            status_code=status_code,
            detail=detail,
        )

    def _apply_non_refreshable_expiry_policy(self, name, token_detail, remaining_seconds, expiry_known, logger):
        if self._has_refresh_token(token_detail) or not expiry_known or remaining_seconds > 0:
            return None
        return self._delete_token_with_reason(name, "Token 已过期且无 Refresh Token，准备删除", logger, token_detail=token_detail)

    def _handle_non_200_status(self, status, resp_data, logger):
        detail = resp_data.get("brief", "") if isinstance(resp_data, dict) else str(resp_data)
        msg = f"状态异常 ({status})"
        if detail:
            msg += f" | {detail}"
        return self._skip_token(msg, logger)

    def _log_usage_summary(self, body_info, logger):
        plan = body_info.get("plan_type", "unknown")
        primary_pct = body_info.get("primary_used_percent", 0)
        primary_seconds = body_info.get("primary_window_seconds")
        secondary_pct = body_info.get("secondary_used_percent")
        secondary_seconds = body_info.get("secondary_window_seconds")
        credits = body_info.get("has_credits", False)
        primary_label = format_window_label(primary_seconds, "primary_window")
        secondary_label = format_window_label(secondary_seconds, "secondary_window") if secondary_pct is not None else None

        quota_info = f"{primary_label}: {primary_pct}%"
        if secondary_pct is not None:
            quota_info += f" | {secondary_label}: {secondary_pct}%"
        quota_info += f" | Credits: {credits}"
        logger.log("OK", f"存活 | Plan: {plan} | {quota_info}", indent=1)
        return primary_pct, secondary_pct, primary_label, secondary_label

    def _apply_quota_policy(
        self,
        name,
        disabled,
        primary_pct,
        secondary_pct,
        logger,
        *,
        email=None,
        has_refresh_token=True,
        primary_label="primary_window",
        secondary_label="secondary_window",
    ):
        primary_reached = primary_pct >= self.settings.quota_threshold
        secondary_present = secondary_pct is not None
        secondary_reached = secondary_present and secondary_pct >= self.settings.quota_threshold
        effective_disabled = disabled

        if secondary_present:
            below_threshold = primary_pct < self.settings.quota_threshold and secondary_pct < self.settings.quota_threshold
            reached_parts = []
            if primary_reached:
                reached_parts.append(f"{primary_label}额度 {primary_pct}%")
            if secondary_reached:
                reached_parts.append(f"{secondary_label}额度 {secondary_pct}%")
            reached_summary = "、".join(reached_parts)
        else:
            below_threshold = primary_pct < self.settings.quota_threshold
            reached_summary = f"{primary_label}额度 {primary_pct}%"

        if disabled:
            if below_threshold:
                if secondary_present:
                    logger.log(
                        "WARN",
                        f"已禁用且 {primary_label}/{secondary_label} 额度均已低于 {self.settings.quota_threshold}%，准备启用",
                        indent=1,
                    )
                else:
                    logger.log(
                        "WARN",
                        f"已禁用但{primary_label}额度已降至 {primary_pct}% < {self.settings.quota_threshold}%，准备启用",
                        indent=1,
                    )
                if self.set_disabled_status(name, disabled=False, logger=logger):
                    logger.log("ENABLE", "已重新启用", indent=1)
                    self._inc_stat("enabled")
                    self._record_event("enabled", {"name": name, "email": email})
                    effective_disabled = False
                else:
                    logger.log("ERROR", "启用失败", indent=1)
                return None, effective_disabled
            if not has_refresh_token and (primary_reached or secondary_reached):
                return self._delete_token_with_reason(
                    name,
                    f"无 Refresh Token，且{reached_summary} >= {self.settings.quota_threshold}%，准备删除",
                    logger,
                    token_detail={"email": email},
                ), effective_disabled
            logger.log(
                "INFO",
                f"已禁用，{reached_summary} >= {self.settings.quota_threshold}%，保持禁用",
                indent=1,
            )
            return None, effective_disabled

        if primary_reached or secondary_reached:
            if not has_refresh_token:
                return self._delete_token_with_reason(
                    name,
                    f"无 Refresh Token，且{reached_summary} >= {self.settings.quota_threshold}%，准备删除",
                    logger,
                ), effective_disabled
            logger.log(
                "WARN",
                f"{reached_summary} >= {self.settings.quota_threshold}%，准备禁用",
                indent=1,
            )
            if self.set_disabled_status(name, disabled=True, logger=logger):
                logger.log("DISABLE", "已禁用", indent=1)
                self._inc_stat("disabled")
                self._record_event("disabled", {"name": name, "email": email, "reason": reached_summary})
                effective_disabled = True
            else:
                logger.log("ERROR", "禁用失败", indent=1)
            return None, effective_disabled

        return None, effective_disabled

    def _apply_refresh_policy(self, name, token_detail, remaining_seconds, remaining_str, logger, *, disabled):
        expiry_threshold_seconds = self.settings.expiry_threshold_days * 86400
        if remaining_seconds > 0 and remaining_seconds < expiry_threshold_seconds:
            if not self.settings.enable_refresh:
                logger.log(
                    "INFO",
                    f"剩余 {remaining_str} < {self.settings.expiry_threshold_days} 天，但刷新功能已关闭",
                    indent=1,
                )
                return
            if not disabled:
                logger.log(
                    "INFO",
                    f"剩余 {remaining_str} < {self.settings.expiry_threshold_days} 天，但当前为启用状态，交给 CPA 自动刷新",
                    indent=1,
                )
                return
            logger.log("WARN", f"剩余 {remaining_str} < {self.settings.expiry_threshold_days} 天，准备刷新", indent=1)
            success, new_data, msg = self.try_refresh(token_detail)
            if success:
                if self.upload_updated_token(name, new_data, logger=logger):
                    if disabled:
                        if self.set_disabled_status(name, disabled=True, logger=logger):
                            logger.log("DISABLE", "刷新后保持禁用", indent=1)
                        else:
                            logger.log("ERROR", "刷新后回写禁用失败", indent=1)
                    _, new_remaining = get_expired_remaining(new_data)
                    logger.log("REFRESH", f"{msg}，新剩余: {format_seconds(new_remaining)}", indent=1)
                    self._inc_stat("refreshed")
                    self._record_event("refreshed", {"name": name, "email": token_detail.get("email"), "message": msg})
                else:
                    logger.log("ERROR", "刷新成功但上传失败", indent=1)
            else:
                logger.log("ERROR", f"刷新失败: {msg}", indent=1)
        elif remaining_seconds > 0:
            logger.log("INFO", f"过期时间充足 ({remaining_str})", indent=1)

    def process_token(self, token_info, idx, total):
        name = token_info.get("name", "unknown")
        logger = TokenLogger(self.logger, idx, total, name)
        try:
            logger.log("INFO", "获取详情...", indent=1)
            token_detail = self.get_token_detail(name)
            if not token_detail:
                return self._skip_token("获取详情失败", logger)

            disabled, remaining_seconds, remaining_str, expiry_known = self._log_token_details(token_detail, logger)
            cleanup_result = self._apply_non_refreshable_expiry_policy(name, token_detail, remaining_seconds, expiry_known, logger)
            if cleanup_result:
                return cleanup_result
            access_token = token_detail.get("access_token")
            account_id = token_detail.get("account_id")
            if not access_token:
                return self._skip_token("缺少 access_token", logger)

            logger.log("INFO", "检测在线状态...", indent=1)
            status, resp_data = self.check_token_live(access_token, account_id)
            if status in (401, 402):
                detail = resp_data.get("brief", "") if isinstance(resp_data, dict) else str(resp_data)
                return self._handle_invalid_token(
                    name,
                    logger,
                    token_detail=token_detail,
                    status_code=status,
                    detail=detail,
                )
            if status is None:
                detail = resp_data.get("brief", "") if isinstance(resp_data, dict) else str(resp_data)
                msg = "网络检测失败"
                if detail:
                    msg += f" | {detail}"
                self._record_quota_snapshot(error_snapshot(name=name, email=token_detail.get("email"), error=msg))
                return self._skip_token(msg, logger, network_error=True, token_name=name)
            if status != 200:
                return self._handle_non_200_status(status, resp_data, logger)

            usage_model = parse_usage_info(resp_data)
            body_info = self.parse_usage_info(resp_data)
            self._record_quota_snapshot(snapshot_from_usage(name=name, email=token_detail.get("email"), usage=usage_model))
            primary_pct, secondary_pct, primary_label, secondary_label = self._log_usage_summary(body_info, logger)
            quota_result, refresh_disabled = self._apply_quota_policy(
                name,
                disabled,
                primary_pct,
                secondary_pct,
                logger,
                email=token_detail.get("email"),
                has_refresh_token=self._has_refresh_token(token_detail),
                primary_label=primary_label,
                secondary_label=secondary_label,
            )
            if quota_result:
                return quota_result
            self._apply_refresh_policy(
                name,
                token_detail,
                remaining_seconds,
                remaining_str,
                logger,
                disabled=refresh_disabled,
            )

            self._inc_stat("alive")
            logger.blank_line()
            return "alive"
        finally:
            logger.flush()

    def log_startup(self):
        self.logger.divider()
        self.log("INFO", "CPACodexKeeper 启动")
        self.log("INFO", f"API: {self.settings.cpa_endpoint}")
        self.log("INFO", f"Quota threshold: {self.settings.quota_threshold}% (disable when reached)")
        self.log("INFO", f"Expiry threshold: {self.settings.expiry_threshold_days} days (refresh disabled auth when below)")
        self.log("INFO", f"Refresh enabled: {self.settings.enable_refresh}")
        self.log("INFO", f"Feishu notify enabled: {self.notifier.enabled}")
        if self.dry_run:
            self.log("DRY", "演练模式 (不实际修改)")
        self.logger.divider()

    def _notify_post_run(self, stats):
        events = self._events_snapshot()
        if stats["network_error"] >= self.settings.notify_large_scale_usage_failure_threshold:
            self.notifier.notify_large_scale_usage_failure(stats, events)
        self.notifier.handle_failure_state(stats)
        quota_agg = self._run_quota_job()
        self.notifier.notify_deleted_accounts(events.get("deleted", []))
        self.notifier.notify_disabled_accounts(events.get("disabled", []))
        self._maybe_send_status_broadcast(stats, events, quota_agg)

    def _run_quota_job(self):
        try:
            return self.quota_job.run(self._quota_snapshot_list())
        except Exception as exc:
            self.log("ERROR", f"CPA quota report failed: {exc}")
            return None

    def _maybe_send_status_broadcast(self, stats, events, quota_agg):
        if not self.quota_job.state.should_send_broadcast(
            enabled=self.settings.status_broadcast_enabled,
            hours_local=self.settings.status_broadcast_hours_local,
            timezone_name=self.settings.status_broadcast_timezone,
        ):
            return
        sent = self.notifier.notify_status_broadcast(stats, events, quota_agg)
        if sent and not self.dry_run:
            self.quota_job.state.commit_broadcast(timezone_name=self.settings.status_broadcast_timezone)
            self.quota_job.state.save()

    def run(self):
        self.reset_stats()
        self.log_startup()
        tokens = self.get_token_list()
        if not tokens:
            result = self.cpa_client.last_list_auth_files_result
            if result and result.status_code not in (200, None):
                self.notifier.notify_cpa_api_exception(
                    f"/v0/management/auth-files 返回状态码 {result.status_code} | {result.brief or result.body[:200]}"
                )
            elif result and result.status_code is None:
                self.notifier.notify_cpa_api_exception(result.error or "获取 token 列表失败")
            self.log("WARN", "未获取到任何 codex Token")
            return

        self._set_total(len(tokens))
        random.shuffle(tokens)
        start_time = time.time()
        total = len(tokens)
        self.log("INFO", f"共计: {total} 个 codex Token")
        self.log("INFO", f"线程数: {self.settings.worker_threads}")
        self.blank_line()

        future_map = {}
        with ThreadPoolExecutor(max_workers=self.settings.worker_threads) as executor:
            for idx, token_info in enumerate(tokens, 1):
                future = executor.submit(self.process_token, token_info, idx, total)
                future_map[future] = token_info

            for future in as_completed(future_map):
                try:
                    future.result()
                except Exception as exc:
                    token_name = future_map[future].get("name", "unknown")
                    self.log("ERROR", f"Token 任务异常 ({token_name}): {exc}", indent=1)
                    self._record_event("task_exceptions", {"name": token_name, "message": str(exc)})
                    self._record_quota_snapshot(error_snapshot(name=token_name, error=str(exc)))
                    self.blank_line()

        elapsed = time.time() - start_time
        stats = self._stats_snapshot()
        self.logger.divider()
        self.log("INFO", "执行完成")
        self.log("INFO", f"耗时: {elapsed:.1f} 秒")
        self.log("INFO", "统计:")
        self.log("INFO", f"- 总计: {stats['total']}", indent=1)
        self.log("INFO", f"- 存活: {stats['alive']}", indent=1)
        self.log("INFO", f"- 死号(已删除): {stats['dead']}", indent=1)
        self.log("INFO", f"- 已禁用: {stats['disabled']}", indent=1)
        self.log("INFO", f"- 已启用: {stats['enabled']}", indent=1)
        self.log("INFO", f"- 已刷新: {stats['refreshed']}", indent=1)
        self.log("INFO", f"- 跳过: {stats['skipped']}", indent=1)
        self.log("INFO", f"- 网络失败: {stats['network_error']}", indent=1)
        self.logger.divider()
        self._notify_post_run(stats)

    def run_forever(self, interval_seconds=1800):
        round_no = 0
        self.log("INFO", f"守护模式启动，执行间隔: {interval_seconds} 秒")
        while True:
            round_no += 1
            self.log("INFO", f"开始第 {round_no} 轮巡检")
            try:
                self.run()
                self.log("INFO", f"第 {round_no} 轮巡检结束")
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                self.log("ERROR", f"第 {round_no} 轮巡检异常: {exc}")
                self.notifier.notify_round_exception(round_no, exc)
            self.log("INFO", f"等待 {interval_seconds} 秒后开始下一轮")
            time.sleep(interval_seconds)
